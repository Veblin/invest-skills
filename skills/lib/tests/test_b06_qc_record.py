"""B06：QC 记录侧车（产物 sha256 绑定/分层签认）+ ETF 同代断言层。

覆盖负责人定点补充（2026-10-08）：
- 改 MD 一行后旧 manual PASS 失效（记录 stale）；
- 保留 meta 哈希改 HTML 数字不再自称 paired（HTML sha256 不符 → stale）；
- 未执行 ≠ PASS（无记录/签认未执行单列）；
- --verify-data ≠ network_used（ETF 离线域核验）；
- 可判定反例：无基准 TE 数字、隔夜 NAV 错价、缺权重=0、错月拼当期、身份未核称已核。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_SKILLS_LIB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SKILLS_LIB))  # 无条件插 0：防其他 skill 目录先行入 path 遮蔽同名模块

# ETF 领域用例（test_etf_domain_metadata_is_checked_by_qc）直接 import etf_generation，
# 该模块在技能自身 lib 目录。**追加到末尾**（不放行首：不抢已存在同名模块的命中）并
# 不依赖 ETF tests 的 conftest 先入 path——shared 套件单独运行（`pytest skills/lib/tests`）
# 也必须可跑（Codex 复检：旧实现靠跨目录 import 顺序，独立运行时 ModuleNotFoundError）。
_ETF_SCRIPTS_LIB = Path(__file__).resolve().parents[2] / "invest-a-etf" / "scripts" / "lib"
if str(_ETF_SCRIPTS_LIB) not in sys.path:
    sys.path.append(str(_ETF_SCRIPTS_LIB))

import snapshot_core  # noqa: E402
from report_qc import (  # noqa: E402
    QC_SIGNOFF_LAYERS,
    etf_generation_findings,
    generation_artifacts,
    qc_file,
    qc_record_path,
    verify_qc_record,
    write_qc_record,
)

MD_BODY = """# 华泰柏瑞沪深300ETF（510300）ETF 研究备忘录

> ⚠️ 本备忘录由 AI 辅助生成，不构成投资建议。

## 1. 产品快照

[事实] 身份：资产=股票 / 通道=境内 / 策略=宽基，核验=原文核验。 [来源: identity.axes]
[分析] 跟踪质量受限：跟踪误差不可算（缺总收益基准）。 [证据强度: ⚠️·📡·🕐·✓✗]

## 2. 持仓透视

[事实] 前十大合计 42.3%（披露范围，缺口不按 0）。 [来源: holdings.top10_sum_pct]
[分析] 缺权重不按 0、不重归一化。 [证据强度: ⚠️·📡·🕐·✓✗]

> 不构成投资建议。
"""


def _payload(symbol: str = "510300", *, tracking: dict | None = None,
             holdings: dict | None = None, index_exposure: dict | None = None,
             identity: dict | None = None, kline: dict | None = None,
             peers: dict | None = None, share_history: dict | None = None,
             index_pe: float | None = None) -> dict:
    profile = {
        "tracking": tracking or {"status": "limited", "note": "缺总收益基准"},
        "identity": identity or {
            "axes": {"asset_type": "stock", "channel": "domestic",
                     "strategy": "broad_market"},
            "verification": {"status": "verified_original"},
        },
    }
    if index_pe is not None:
        profile["index_pe"] = index_pe
    payload = {
        "skill": "invest-a-etf",
        "symbol": symbol,
        "generated_at": "2026-10-08T10:00:00+08:00",
        "profile": profile,
        "holdings": holdings or {},
        "data_status": {"dimensions": {}},
    }
    if index_exposure is not None:
        payload["index_exposure"] = index_exposure
    if kline is not None:
        payload["kline"] = kline
    if peers is not None:
        payload["peers"] = peers
    if share_history is not None:
        payload["share_history"] = share_history
    snapshot_core.seal(
        payload, version_field="payload_version", version=1,
        extra={"payload_version": 1, "symbol": symbol,
               "generated_at": payload["generated_at"],
               "frozen_at": payload["generated_at"]})
    return payload


def _write_generation(tmp_path: Path, *, md_text: str = MD_BODY,
                      payload: dict | None = None, html_extra: str = "",
                      ts: str = "2026-10-08-10-00-00",
                      dirname: str = "510300-沪深300ETF") -> Path:
    d = tmp_path / dirname
    d.mkdir(parents=True, exist_ok=True)
    md = d / f"{ts}.md"
    md.write_text(md_text, encoding="utf-8")
    if payload is not None:
        (d / f"{ts}.payload.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        (d / f"{ts}.html").write_text(
            "<html><head>"
            f'<meta name="invest-generation" content="{payload["_meta"]["payload_hash"]}">'
            f"</head><body>净值 1.2345{html_extra}</body></html>",
            encoding="utf-8")
    return md


class TestQCReecordBinding:
    @pytest.mark.parametrize("contents", ["{broken", "[]", "null", '{"ts":"other"}'])
    def test_existing_damaged_record_is_invalid_and_qc_error(self, tmp_path, contents):
        p = _write_generation(tmp_path, payload=_payload())
        qc_record_path(p).write_text(contents, encoding="utf-8")
        record = verify_qc_record(p)
        assert record["status"] == "invalid"
        assert record["issues"]
        findings = etf_generation_findings(p)
        assert any(f["id"] == "etf-qc-record-stale" and f["severity"] == "error"
                   for f in findings)
        assert not any(f["id"] == "etf-qc-record-missing" for f in findings)

    def test_record_roundtrip_valid(self, tmp_path: Path):
        pl = _payload()
        p = _write_generation(tmp_path, payload=pl)
        result = qc_file(p, fail_on="error")
        path, rec = write_qc_record(p, result, signoffs={"manual_numbers": {"status": "pass"}})
        assert path == qc_record_path(p) and path.is_file()
        assert rec["artifacts"]["md"]["sha256"]
        # 真校验（替换恒真旁路）：记录内 payload_hash 必须等于**落盘 payload
        # 文件**的实际封存哈希，且为 64 位十六进制。
        written = json.loads(
            (p.parent / f"{p.stem}.payload.json").read_text(encoding="utf-8"))
        assert rec["payload_hash"] == written["_meta"]["payload_hash"]
        assert len(rec["payload_hash"]) == 64
        v = verify_qc_record(p)
        assert v["status"] == "valid"
        assert v["signoffs"]["manual_numbers"]["status"] == "pass"
        assert v["signoffs"]["manual_logic"]["status"] == "not_executed"  # 默认未执行

    def test_tampered_payload_breaks_record_binding(self, tmp_path: Path):
        """真反例：改 payload 内封存实况 → 记录 stale + 同代层 error（双重绑定）。"""
        p = _write_generation(tmp_path, payload=_payload())
        write_qc_record(p, qc_file(p, fail_on="error"))
        payload_path = p.parent / f"{p.stem}.payload.json"
        tampered = json.loads(payload_path.read_text(encoding="utf-8"))
        tampered["profile"]["tracking"] = {"status": "available", "td_pct": 0.1}
        payload_path.write_text(json.dumps(tampered, ensure_ascii=False), encoding="utf-8")
        v = verify_qc_record(p)
        assert v["status"] == "stale"
        assert any(c.startswith("payload") for c in v["changed"])
        findings = etf_generation_findings(p)
        assert any(f["id"] == "etf-payload-invalid" and f["severity"] == "error"
                   for f in findings)

    def test_edit_md_invalidates_old_record(self, tmp_path: Path):
        p = _write_generation(tmp_path, payload=_payload())
        write_qc_record(p, qc_file(p, fail_on="error"),
                        signoffs={"manual_numbers": {"status": "pass"}})
        p.write_text(MD_BODY.replace("42.3%", "43.1%"), encoding="utf-8")
        v = verify_qc_record(p)
        assert v["status"] == "stale"
        assert any("md" in c for c in v["changed"])
        # 机器层与服务层同判定：报告 QC 层出现 error 级「记录与产物不一致」
        findings = etf_generation_findings(p)
        assert any(f["id"] == "etf-qc-record-stale" and f["severity"] == "error"
                   for f in findings)

    def test_edit_html_keeping_meta_hash_is_stale(self, tmp_path: Path):
        """保留 meta 哈希改 HTML 数字 → 不得再自称 paired/valid。"""
        p = _write_generation(tmp_path, payload=_payload())
        write_qc_record(p, qc_file(p, fail_on="error"))
        html = p.parent / f"{p.stem}.html"
        html.write_text(html.read_text(encoding="utf-8").replace("1.2345", "9.9999"),
                        encoding="utf-8")
        v = verify_qc_record(p)
        assert v["status"] == "stale"
        assert any("html" in c for c in v["changed"])

    def test_edit_decision_detected(self, tmp_path: Path):
        p = _write_generation(tmp_path, payload=_payload())
        dec = p.parent / f"{p.stem}.decision.json"
        dec.write_text(json.dumps({"symbol": "510300", "report_ts": p.stem}),
                       encoding="utf-8")
        write_qc_record(p, qc_file(p, fail_on="error"))
        dec.write_text(json.dumps({"symbol": "510300", "report_ts": p.stem,
                                   "scenarios": [{"name": "x"}]}), encoding="utf-8")
        assert verify_qc_record(p)["status"] == "stale"

    def test_no_record_is_none_not_pass(self, tmp_path: Path):
        p = _write_generation(tmp_path)
        v = verify_qc_record(p)
        assert v["status"] == "none"
        findings = etf_generation_findings(p)
        assert any(f["id"] == "etf-qc-record-missing" for f in findings)
        assert any(f["id"] == "etf-payload-missing" and f["severity"] == "warning"
                   for f in findings)

    def test_record_new_artifact_after_record_is_stale(self, tmp_path: Path):
        p = _write_generation(tmp_path)
        write_qc_record(p, qc_file(p, fail_on="error"))
        (p.parent / f"{p.stem}.html").write_text("<html></html>", encoding="utf-8")
        v = verify_qc_record(p)
        assert v["status"] == "stale"
        assert any("html" in c and "新增" in c for c in v["changed"])

    def test_generation_artifacts_hashes_all_present(self, tmp_path: Path):
        p = _write_generation(tmp_path, payload=_payload())
        arts = generation_artifacts(p)
        assert arts["md"]["sha256"] and arts["payload"]["sha256"] and arts["html"]["sha256"]
        assert arts["decision"] is None

    def test_signoff_layers_vocab_locked(self):
        assert set(QC_SIGNOFF_LAYERS) == {
            "data_review", "original_materials",
            "manual_numbers", "manual_compliance", "manual_logic"}


class TestSignoffValidation:
    def test_unknown_layer_rejected(self, tmp_path: Path):
        p = _write_generation(tmp_path)
        import pytest
        from report_qc import _normalize_signoffs
        with pytest.raises(ValueError):
            _normalize_signoffs({"manual_magic": {"status": "pass"}})

    def test_unknown_status_rejected(self):
        import pytest
        from report_qc import _normalize_signoffs
        with pytest.raises(ValueError):
            _normalize_signoffs({"manual_numbers": {"status": "probably"}})


class TestETFDomainFindings:
    """可判定反例：应失败的断言模式必须被机器拦截。"""

    def test_te_claim_without_comparable_benchmark_fails(self, tmp_path: Path):
        md = MD_BODY.replace("跟踪误差不可算（缺总收益基准）", "跟踪误差 0.36%（年化）")
        p = _write_generation(tmp_path, md_text=md, payload=_payload())
        findings = etf_generation_findings(p)
        assert any(f["id"] == "etf-tracking-claim" and f["severity"] == "error"
                   for f in findings)

    def test_te_claim_in_table_row_also_fails(self, tmp_path: Path):
        """断言写在表格行里同样要拦（fail-open 回归：正文口径漏检表格）。"""
        md = MD_BODY + "\n| 指标 | 值 |\n|---|---|\n| 确认性 TD / TE | 跟踪误差 0.52%（年化） |\n"
        p = _write_generation(tmp_path, md_text=md, payload=_payload())
        findings = etf_generation_findings(p)
        assert any(f["id"] == "etf-tracking-claim" and f["severity"] == "error"
                   for f in findings)

    def test_te_claim_compliant_wording_passes(self, tmp_path: Path):
        p = _write_generation(tmp_path, payload=_payload())
        findings = etf_generation_findings(p)
        assert not any(f["id"] == "etf-tracking-claim" for f in findings)

    def test_mispricing_claim_fails(self, tmp_path: Path):
        md = MD_BODY + "\n[分析] 当前折价 1.2%，说明该 ETF 被低估，存在错价机会。\n"
        p = _write_generation(tmp_path, md_text=md, payload=_payload())
        findings = etf_generation_findings(p)
        assert any(f["id"] == "etf-mispricing-claim" and f["severity"] == "error"
                   for f in findings)

    def test_mispricing_negated_wording_passes(self, tmp_path: Path):
        # 带否定/免责语境的同句不算错价断言
        md = MD_BODY + "\n[分析] 折价不自动意味着低估，净值时点未知，不得确认错价。\n"
        p = _write_generation(tmp_path, md_text=md, payload=_payload())
        findings = etf_generation_findings(p)
        assert not any(f["id"] == "etf-mispricing-claim" for f in findings)

    def test_zero_weight_claim_fails_when_gaps_present(self, tmp_path: Path):
        md = MD_BODY + "\n[事实] 缺失权重按 0 计入后，前十大合计 100%。\n"
        p = _write_generation(
            tmp_path, md_text=md,
            payload=_payload(holdings={"coverage": {"gaps": ["XX 权重缺失"]}}))
        findings = etf_generation_findings(p)
        assert any(f["id"] == "etf-holdings-zero-weight" and f["severity"] == "error"
                   for f in findings)

    def test_zero_weight_compliant_wording_passes(self, tmp_path: Path):
        p = _write_generation(
            tmp_path, payload=_payload(holdings={"coverage": {"gaps": ["XX 权重缺失"]}}))
        findings = etf_generation_findings(p)
        assert not any(f["id"] == "etf-holdings-zero-weight" for f in findings)

    def test_stale_month_composition_claim_fails(self, tmp_path: Path):
        md = MD_BODY + "\n[分析] 以上成分与权重已拼成当期完整组合。\n"
        p = _write_generation(
            tmp_path, md_text=md,
            payload=_payload(index_exposure={
                "status": "ok",
                "period_alignment": {"same_period": False, "joinable": False,
                                     "cons_as_of": "2026-09-30",
                                     "weight_as_of": "2026-08-31"}}))
        findings = etf_generation_findings(p)
        assert any(f["id"] == "etf-stale-composition-claim" and f["severity"] == "error"
                   for f in findings)

    def test_identity_claim_when_unverified_fails(self, tmp_path: Path):
        md = MD_BODY.replace("核验=原文核验", "身份已核，原文核验通过")
        p = _write_generation(
            tmp_path, md_text=md,
            payload=_payload(identity={
                "axes": {"asset_type": "bond", "channel": "domestic", "strategy": "unknown"},
                "verification": {"status": "mapped_unverified"}}))
        findings = etf_generation_findings(p)
        assert any(f["id"] == "etf-identity-claim" and f["severity"] == "error"
                   for f in findings)

    def test_html_generation_mismatch_fails(self, tmp_path: Path):
        p = _write_generation(tmp_path, payload=_payload())
        html = p.parent / f"{p.stem}.html"
        html.write_text("<html><head>"
                        '<meta name="invest-generation" content="deadbeefdeadbeef">'
                        "</head></html>", encoding="utf-8")
        findings = etf_generation_findings(p)
        assert any(f["id"] == "etf-html-generation-mismatch" and f["severity"] == "error"
                   for f in findings)

    def test_payload_symbol_mismatch_fails(self, tmp_path: Path):
        p = _write_generation(tmp_path, payload=_payload(symbol="510300"))
        payload_path = p.parent / f"{p.stem}.payload.json"
        pl = json.loads(payload_path.read_text(encoding="utf-8"))
        snap = dict(pl)
        snap["symbol"] = "515050"
        payload_path.write_text(json.dumps(snap, ensure_ascii=False), encoding="utf-8")
        findings = etf_generation_findings(p)
        # 篡改后封存哈希先失效（同一防线），至少一条 error
        assert any(f["severity"] == "error" for f in findings)


_ID_MD_HEAD = "# 华泰柏瑞沪深300ETF（510300）ETF 研究备忘录\n\n## 1 概况\n\n"


class TestBF04ClaimScope:
    """B-F04 反例（负责人-整体反例.py 复现）：否定/缺口豁免须**同分句同命题**——
    句末其他维度的缺口/待核措辞不得保护前一分句的确认性断言，也不得整行豁免。"""

    def test_tracking_claim_with_trailing_gap_still_fails(self, tmp_path: Path):
        md = MD_BODY + "\n[分析] 跟踪误差为 0.20%，其他维度有缺口。\n"
        p = _write_generation(tmp_path, md_text=md, payload=_payload())
        findings = etf_generation_findings(p)
        assert any(f["id"] == "etf-tracking-claim" and f["severity"] == "error"
                   for f in findings)

    def test_identity_claim_with_trailing_pending_still_fails(self, tmp_path: Path):
        md = _ID_MD_HEAD + "[事实] 身份已核验，宏观背景待核。 [来源: identity.axes]\n"
        p = _write_generation(
            tmp_path, md_text=md,
            payload=_payload(identity={
                "axes": {"asset_type": "bond", "channel": "domestic", "strategy": "unknown"},
                "verification": {"status": "mapped_unverified"}}))
        findings = etf_generation_findings(p)
        assert any(f["id"] == "etf-identity-claim" and f["severity"] == "error"
                   for f in findings)

    def test_mispricing_claim_with_trailing_gap_still_fails(self, tmp_path: Path):
        md = MD_BODY + "\n[分析] 当前折价 1.2% 显示该 ETF 被低估，其他维度有缺口。\n"
        p = _write_generation(tmp_path, md_text=md, payload=_payload())
        findings = etf_generation_findings(p)
        assert any(f["id"] == "etf-mispricing-claim" and f["severity"] == "error"
                   for f in findings)

    def test_zero_weight_claim_with_trailing_gap_still_fails(self, tmp_path: Path):
        md = MD_BODY + "\n[事实] 缺失权重按 0 计入后合计 100%，其他维度有缺口。\n"
        p = _write_generation(
            tmp_path, md_text=md,
            payload=_payload(holdings={"coverage": {"gaps": ["XX 权重缺失"]}}))
        findings = etf_generation_findings(p)
        assert any(f["id"] == "etf-holdings-zero-weight" and f["severity"] == "error"
                   for f in findings)

    def test_stale_mix_claim_with_trailing_gap_still_fails(self, tmp_path: Path):
        md = MD_BODY + "\n[分析] 以上成分与权重已拼成当期完整组合，其他维度有缺口。\n"
        p = _write_generation(
            tmp_path, md_text=md,
            payload=_payload(index_exposure={
                "status": "ok",
                "period_alignment": {"same_period": False, "joinable": False,
                                     "cons_as_of": "2026-09-30",
                                     "weight_as_of": "2026-08-31"}}))
        findings = etf_generation_findings(p)
        assert any(f["id"] == "etf-stale-composition-claim" and f["severity"] == "error"
                   for f in findings)

    def test_same_clause_negation_still_exempt(self, tmp_path: Path):
        """真实否定措辞（同分句）仍通过：不得因收紧作用域误伤合规写法。"""
        md = (MD_BODY
              + "\n[分析] 该数值为研究数值（非确认指标）：跟踪误差 0.52% 不得引用为确认值。\n"
              + "\n[分析] 折价不自动意味着低估，不得确认错价。\n")
        p = _write_generation(
            tmp_path, md_text=md,
            payload=_payload(holdings={"coverage": {"gaps": ["XX 权重缺失"]}}))
        findings = etf_generation_findings(p)
        assert not any(f["id"] == "etf-tracking-claim" for f in findings)
        assert not any(f["id"] == "etf-mispricing-claim" for f in findings)

    def test_identity_true_pending_wording_passes(self, tmp_path: Path):
        md = _ID_MD_HEAD + "[事实] 身份待核（未做核验）：来源为静态映射。\n"
        p = _write_generation(
            tmp_path, md_text=md,
            payload=_payload(identity={
                "axes": {"asset_type": "bond", "channel": "domestic", "strategy": "unknown"},
                "verification": {"status": "mapped_unverified"}}))
        findings = etf_generation_findings(p)
        assert not any(f["id"] == "etf-identity-claim" for f in findings)


class TestR2ClaimScopeBoundaries:
    """R2 重点负例（负责人-整体反例.py 同族）：括号/转折/表格格不是豁免通道——
    「其他维度」的缺口措辞不得保护另一命题的确认性断言；真实「不可得/不得写成」
    仍须通过（不得因收紧作用域误伤合规写法）。"""

    def _unverified_identity(self) -> dict:
        return _payload(identity={
            "axes": {"asset_type": "bond", "channel": "domestic", "strategy": "unknown"},
            "verification": {"status": "mapped_unverified"}})

    def test_paren_gap_does_not_exempt_tracking_claim(self, tmp_path: Path):
        md = MD_BODY + "\n[分析] 跟踪误差为 0.20%（其他维度有缺口）。\n"
        p = _write_generation(tmp_path, md_text=md, payload=_payload())
        findings = etf_generation_findings(p)
        assert any(f["id"] == "etf-tracking-claim" and f["severity"] == "error"
                   for f in findings), "括注缺口不得豁免括号前的确认性断言"

    def test_contrastive_but_does_not_exempt_identity_claim(self, tmp_path: Path):
        md = _ID_MD_HEAD + "[事实] 身份已核验但宏观背景待核。 [来源: identity.axes]\n"
        p = _write_generation(tmp_path, md_text=md, payload=self._unverified_identity())
        findings = etf_generation_findings(p)
        assert any(f["id"] == "etf-identity-claim" and f["severity"] == "error"
                   for f in findings), "「但」后句的待核不得豁免前句身份断言"

    def test_table_cell_gap_does_not_exempt_tracking_claim(self, tmp_path: Path):
        md = (MD_BODY
              + "\n| 项 | 值 | 说明 |\n|---|---|---|\n"
              + "| 确认性 TD / TE | 跟踪误差 0.52%（年化） | 其他维度有缺口 |\n")
        p = _write_generation(tmp_path, md_text=md, payload=_payload())
        findings = etf_generation_findings(p)
        assert any(f["id"] == "etf-tracking-claim" and f["severity"] == "error"
                   for f in findings), "邻格缺口不得豁免本格确认性断言"

    def test_contrastive_however_does_not_exempt_tracking_claim(self, tmp_path: Path):
        md = MD_BODY + "\n[分析] 跟踪误差为 0.20%，然而其他维度有缺口。\n"
        p = _write_generation(tmp_path, md_text=md, payload=_payload())
        findings = etf_generation_findings(p)
        assert any(f["id"] == "etf-tracking-claim" and f["severity"] == "error"
                   for f in findings)

    def test_true_unavailable_and_pending_still_pass(self, tmp_path: Path):
        """真正「不可得/不得写成」通过：跟踪误差不可得、身份待核、不得写成跟踪误差。"""
        md = (MD_BODY
              + "\n[分析] 跟踪误差不可得（基准腿缺总收益口径）。\n"
              + "\n[分析] 价格基准偏离 0.36% 不得写成「跟踪误差」。\n"
              + "\n[分析] 折价不自动意味着低估。\n")
        p = _write_generation(
            tmp_path, md_text=md,
            payload=_payload(holdings={"coverage": {"gaps": ["XX 权重缺失"]}}))
        findings = etf_generation_findings(p)
        assert not any(f["id"] == "etf-tracking-claim" for f in findings)
        assert not any(f["id"] == "etf-mispricing-claim" for f in findings)
        md_id = _ID_MD_HEAD + "[事实] 身份待核：静态映射，未做原文核验。\n"
        p2 = _write_generation(tmp_path / "id", md_text=md_id,
                               payload=self._unverified_identity())
        assert not any(f["id"] == "etf-identity-claim"
                       for f in etf_generation_findings(p2))


class TestR2RecordEntrypoint:
    """R2 记录入口合同（负责人-QC记录入口.py 复现）：
    `--json --record` 必须真实写记录且 stdout 纯 JSON；陈旧产物一次重跑完成
    重核验且旧人工 PASS 不继承；机器 FAIL 照实记录、内容/封存错误不被刷新绕过。"""

    def _findings(self, parsed: list[dict]) -> list[dict]:
        return [d for layer in parsed[0]["layers"] for d in layer.get("details") or []]

    def test_json_record_actually_writes_and_stdout_is_json(self, tmp_path, capsys):
        from report_qc import main

        p = _write_generation(tmp_path, payload=_payload())
        rc = main([str(p), "--fail-on", "error", "--json", "--record"])
        out = capsys.readouterr().out
        parsed = json.loads(out)           # stdout 必须是纯 JSON（日志走 stderr）
        assert isinstance(parsed, list) and parsed and rc in (0, 1, 2)
        rec_path = qc_record_path(p)
        assert rec_path.is_file(), "--json --record 必须真实写记录（旧实现静默忽略）"
        rec = json.loads(rec_path.read_text(encoding="utf-8"))
        assert rec["machine_qc"]["overall"] == parsed[0]["overall"]
        assert verify_qc_record(p)["status"] == "valid"

    def test_stale_rerun_single_pass_and_no_inherited_signoff(self, tmp_path, capsys):
        from report_qc import main

        p = _write_generation(tmp_path, payload=_payload())
        write_qc_record(p, qc_file(p, fail_on="error"),
                        signoffs={"manual_numbers": {"status": "pass"}})
        text = p.read_text(encoding="utf-8").replace("42.3%", "42.4%")
        p.write_text(text, encoding="utf-8")
        assert verify_qc_record(p)["status"] == "stale"
        main([str(p), "--fail-on", "error", "--json", "--record"])   # 只跑一次
        capsys.readouterr()
        rec = json.loads(qc_record_path(p).read_text(encoding="utf-8"))
        assert verify_qc_record(p)["status"] == "valid", "一次重跑即应完成重核验"
        assert rec["signoffs"]["manual_numbers"]["status"] == "not_executed", \
            "旧人工 PASS 不得继承"
        assert rec["supersedes"]["status"] == "stale"

    def test_content_error_recorded_as_fail_not_bypassed(self, tmp_path, capsys):
        from report_qc import main

        md = MD_BODY.replace("跟踪误差不可算（缺总收益基准）", "跟踪误差 0.36%（年化）")
        p = _write_generation(tmp_path, md_text=md, payload=_payload())
        rc = main([str(p), "--fail-on", "error", "--json", "--record"])
        parsed = json.loads(capsys.readouterr().out)
        assert rc == 2 and parsed[0]["overall"] == "FAIL"
        assert any(d["id"] == "etf-tracking-claim" for d in self._findings(parsed))
        rec = json.loads(qc_record_path(p).read_text(encoding="utf-8"))
        assert rec["machine_qc"]["overall"] == "FAIL", "机器 FAIL 必须照实记录"

    def test_record_refresh_keeps_payload_seal_error(self, tmp_path, capsys):
        from report_qc import main

        p = _write_generation(tmp_path, payload=_payload())
        payload_path = p.parent / f"{p.stem}.payload.json"
        pl = json.loads(payload_path.read_text(encoding="utf-8"))
        pl["profile"]["tracking"] = {"status": "available", "td_pct": 0.1}
        payload_path.write_text(json.dumps(pl, ensure_ascii=False), encoding="utf-8")
        rc = main([str(p), "--fail-on", "error", "--json", "--record"])
        parsed = json.loads(capsys.readouterr().out)
        assert rc == 2 and parsed[0]["overall"] == "FAIL"
        assert any(d["id"] == "etf-payload-invalid" for d in self._findings(parsed))


class TestNetworkUsedSemantics:
    def test_verify_data_flag_alone_does_not_mean_network(self, tmp_path: Path):
        """--verify-data ≠ network_used：ETF 域核验离线，未联网应记 False。"""
        p = _write_generation(tmp_path, payload=_payload())
        r = qc_file(p, fail_on="error", verify_data=True)
        assert r.network_used is False
        audit = next(l for l in r.layers if l.layer == "audit")
        assert audit.status == "skip"
        assert any("network_used=false" in d.get("message", "")
                   for d in audit.details)

    def test_recorded_machine_result_and_signed_state(self, tmp_path: Path):
        p = _write_generation(tmp_path, payload=_payload())
        r = qc_file(p, fail_on="error")
        path, rec = write_qc_record(p, r)
        assert rec["machine_qc"]["overall"] == r.overall
        assert rec["machine_qc"]["network_used"] is False
        assert json.loads(path.read_text(encoding="utf-8"))["signoffs"]


class TestR3SameObjectNegation:
    """R3 五族同命题否证：否证必须贴附**本族对象**——其他维度（宏观背景/盘口/
    规模）的缺口或待核不构成豁免；真实否定仍须通过（R3 负责人断言反例 + 五族
    正反例）。阈限说明：这是有限规则，不冒充自然语言万能核验。"""

    def _unverified_identity(self) -> dict:
        return _payload(identity={
            "axes": {"asset_type": "stock", "channel": "domestic",
                     "strategy": "broad_market"},
            "verification": {"status": "mapped_unverified"}})

    # ── tracking（跟踪误差/差异数字） ──
    @pytest.mark.parametrize("text", [
        "跟踪误差为 0.20%。",
        "跟踪误差为 0.20%，其他维度有缺口。",
        "跟踪误差为 0.20%（其他维度有缺口）。",
        "跟踪误差为 0.20% 而盘口有缺口。",       # R3 负责人反例（旧实现放过）
    ])
    def test_tracking_claim_blocked_with_other_dimension_gap(self, tmp_path, text):
        md = MD_BODY + f"\n[分析] {text}\n"
        p = _write_generation(tmp_path, md_text=md, payload=_payload())
        findings = etf_generation_findings(p)
        assert any(f["id"] == "etf-tracking-claim" and f["severity"] == "error"
                   for f in findings), text

    def test_tracking_true_negations_still_pass(self, tmp_path):
        md = (MD_BODY
              + "\n[分析] 跟踪误差不可得（基准腿缺总收益口径）。\n"
              + "\n[分析] 确认性 TD/TE 不可算（两腿收益类型不同）。\n"
              + "\n[分析] 价格基准偏离 0.36% 不构成跟踪差异/跟踪误差。\n")
        p = _write_generation(tmp_path, md_text=md, payload=_payload())
        assert not any(f["id"] == "etf-tracking-claim"
                       for f in etf_generation_findings(p))

    # ── identity（身份已核/原文核验） ──
    @pytest.mark.parametrize("text", [
        "身份已核验，宏观背景待核。",
        "身份已核验但宏观背景待核。",
        "身份已核验且宏观背景待核。",             # R3 负责人反例（旧实现放过）
        "身份已核验（宏观背景待核）。",
    ])
    def test_identity_claim_blocked_with_other_topic_pending(self, tmp_path, text):
        md = _ID_MD_HEAD + f"[事实] {text} [来源: identity.axes]\n"
        p = _write_generation(tmp_path, md_text=md, payload=self._unverified_identity())
        findings = etf_generation_findings(p)
        assert any(f["id"] == "etf-identity-claim" and f["severity"] == "error"
                   for f in findings), text

    @pytest.mark.parametrize("text", [
        "身份待核：静态映射。",
        "身份未核验。",
        "未做原文核验（静态映射）。",
        "原文核验未执行。",
    ])
    def test_identity_true_negations_still_pass(self, tmp_path, text):
        md = _ID_MD_HEAD + f"[事实] {text} [来源: identity.axes]\n"
        p = _write_generation(tmp_path, md_text=md, payload=self._unverified_identity())
        assert not any(f["id"] == "etf-identity-claim"
                       for f in etf_generation_findings(p)), text

    # ── stale-mix（错月拼当期组合） ──
    def test_stale_mix_negation_attached_passes(self, tmp_path):
        md = (MD_BODY
              + "\n[分析] 成分 9 月与权重 8 月两份文件日期不同（错月），"
                "不可拼成「当期完整组合」。\n")
        p = _write_generation(tmp_path, md_text=md, payload=_payload(
            index_exposure={"status": "ok",
                            "period_alignment": {"joinable": False,
                                                 "cons_as_of": "2026-09-30",
                                                 "weight_as_of": "2026-08-31"}}))
        assert not any(f["id"] == "etf-stale-composition-claim"
                       for f in etf_generation_findings(p))

    def test_stale_mix_affirmation_flagged(self, tmp_path):
        md = MD_BODY + "\n[分析] 成分与权重已拼成当期完整组合。\n"
        p = _write_generation(tmp_path, md_text=md, payload=_payload(
            index_exposure={"status": "ok",
                            "period_alignment": {"joinable": False,
                                                 "cons_as_of": "2026-09-30",
                                                 "weight_as_of": "2026-08-31"}}))
        findings = etf_generation_findings(p)
        assert any(f["id"] == "etf-stale-composition-claim"
                   and f["severity"] == "error" for f in findings)

    # ── zero-weight（缺权重按 0） ──
    def test_zero_weight_negation_attached_passes(self, tmp_path):
        md = MD_BODY + "\n[分析] 缺权重不得按 0 计、不重归一化。\n"
        p = _write_generation(tmp_path, md_text=md, payload=_payload(
            holdings={"coverage": {"gaps": ["XX 权重缺失"]}}))
        assert not any(f["id"] == "etf-holdings-zero-weight"
                       for f in etf_generation_findings(p))

    def test_zero_weight_affirmation_flagged(self, tmp_path):
        md = MD_BODY + "\n[分析] 缺权重按 0 计，前十大合计 100%。\n"
        p = _write_generation(tmp_path, md_text=md, payload=_payload(
            holdings={"coverage": {"gaps": ["XX 权重缺失"]}}))
        findings = etf_generation_findings(p)
        assert any(f["id"] == "etf-holdings-zero-weight"
                   and f["severity"] == "error" for f in findings)

    # ── mispricing（折溢价→错价/高估/低估） ──
    def test_mispricing_negation_attached_passes(self, tmp_path):
        md = (MD_BODY
              + "\n[分析] 折价不意味着低估（净值时点未知）。\n"
              + "\n[分析] 折价率的变动不构成错价判断。\n")
        p = _write_generation(tmp_path, md_text=md, payload=_payload())
        assert not any(f["id"] == "etf-mispricing-claim"
                       for f in etf_generation_findings(p))

    def test_mispricing_affirmation_flagged(self, tmp_path):
        md = MD_BODY + "\n[分析] 折价意味着该 ETF 被低估。\n"
        p = _write_generation(tmp_path, md_text=md, payload=_payload())
        findings = etf_generation_findings(p)
        assert any(f["id"] == "etf-mispricing-claim" and f["severity"] == "error"
                   for f in findings)

@pytest.mark.parametrize("damage", [
    "schema_missing", "schema_unsupported", "artifact_key_missing", "artifact_type",
    "artifact_hash", "machine_missing", "machine_type", "machine_overall",
    "signoff_missing", "signoff_type", "signoff_status",
])
def test_qc_record_schema_damage_fails_closed(tmp_path, damage):
    p = _write_generation(tmp_path, payload=_payload())
    _, rec = write_qc_record(p, qc_file(p, fail_on="error"))
    if damage == "schema_missing":
        rec.pop("schema_version")
    elif damage == "schema_unsupported":
        rec["schema_version"] = 2
    elif damage == "artifact_key_missing":
        rec["artifacts"].pop("html")
    elif damage == "artifact_type":
        rec["artifacts"]["md"] = "broken"
    elif damage == "artifact_hash":
        rec["artifacts"]["md"]["sha256"] = "not-a-sha256"
    elif damage == "machine_missing":
        rec.pop("machine_qc")
    elif damage == "machine_type":
        rec["machine_qc"] = ["PASS"]
    elif damage == "machine_overall":
        rec["machine_qc"]["overall"] = "UNREVIEWED"
    elif damage == "signoff_missing":
        rec["signoffs"].pop("manual_logic")
    elif damage == "signoff_type":
        rec["signoffs"]["manual_logic"] = "pass"
    elif damage == "signoff_status":
        rec["signoffs"]["manual_logic"]["status"] = "approved"
    qc_record_path(p).write_text(json.dumps(rec), encoding="utf-8")
    checked = verify_qc_record(p)
    assert checked["status"] == "invalid"
    assert checked["issues"]
    findings = etf_generation_findings(p)
    assert any(f["id"] == "etf-qc-record-stale" and f["severity"] == "error"
               for f in findings)
    _, refreshed = write_qc_record(p, qc_file(p, fail_on="error", record_refresh=True))
    assert refreshed["supersedes"]["status"] == "invalid"
    assert verify_qc_record(p)["status"] == "valid"
    assert all(v["status"] == "not_executed" for v in refreshed["signoffs"].values())


@pytest.mark.parametrize("claim", [
    "TD = 0.20%", "TE 0.30%（年化）", "td 为 0.20%",
    "| TD / TE（确认性） | 0.20% / 0.30% | 两腿同口径 |",
])
def test_abbreviated_tracking_claim_without_comparable_benchmark(tmp_path, claim):
    p = _write_generation(tmp_path, payload=_payload(), md_text=MD_BODY + "\n" + claim)
    assert any(f["id"] == "etf-tracking-claim" for f in etf_generation_findings(p))


@pytest.mark.parametrize("claim", [
    "TD 0.20% 不构成确认性跟踪差异", "TE 0.30%（非确认指标）",
    "| 价格基准偏离（非确认 TD/TE） | 0.20% / 0.30% | 研究数值 |",
])
def test_abbreviated_research_deviation_is_not_confirmatory(tmp_path, claim):
    p = _write_generation(tmp_path, payload=_payload(), md_text=MD_BODY + "\n" + claim)
    assert not any(f["id"] == "etf-tracking-claim" for f in etf_generation_findings(p))

@pytest.mark.parametrize("family,claim,overrides,expected", [
    ("tracking", "跟踪误差 0.20% 不构成确认指标；TE 0.30%（年化）。", {}, "etf-tracking-claim"),
    ("identity", "身份已核（未经身份核验）；身份已核验。", {"identity": {"verification": {"status": "mapped_unverified"}}}, "etf-identity-claim"),
    ("mispricing", "折溢价不意味低估；溢价代表高估。", {}, "etf-mispricing-claim"),
    ("zero_weight", "缺权重不得按 0；缺权重按 0。", {"holdings": {"rows": [{"pct": None}]}}, "etf-holdings-zero-weight"),
    ("stale_mix", "不能拼成当前完整组合；当前完整组合已完整取得。", {"index_exposure": {"status": "ok", "period_alignment": {"joinable": False}}}, "etf-stale-composition-claim"),
])
def test_negated_first_claim_cannot_hide_later_assertion(tmp_path, family, claim, overrides, expected):
    p = _write_generation(tmp_path, payload=_payload(**overrides), md_text=claim)
    assert any(f["id"] == expected for f in etf_generation_findings(p))


@pytest.mark.parametrize("claim", ["td 0.20%（非确认指标）", "te 0.30% 不可确认"])
def test_lowercase_tracking_research_negation_is_protected(tmp_path, claim):
    p = _write_generation(tmp_path, payload=_payload(), md_text=claim)
    assert not any(f["id"] == "etf-tracking-claim" for f in etf_generation_findings(p))


@pytest.mark.parametrize("damage", ["symbol", "generated_at", "frozen_at"])
def test_etf_domain_metadata_is_checked_by_qc(tmp_path, damage):
    from etf_generation import load_etf_payload
    from report_qc import _load_etf_payload_for_qc
    payload = _payload()
    if damage == "symbol":
        payload["_meta"]["symbol"] = "159919"
    elif damage == "generated_at":
        payload.pop("generated_at")
    else:
        payload["_meta"].pop("frozen_at")
    snapshot_core.seal(payload)
    p = _write_generation(tmp_path, payload=payload)
    for loaded, errors in (load_etf_payload(p.with_suffix(".payload.json")),
                           _load_etf_payload_for_qc(p)):
        assert loaded is None and errors
    assert any(f["id"] == "etf-payload-invalid" for f in etf_generation_findings(p))

@pytest.mark.parametrize('claim', [
    '价格基准偏离研究数值 TD 0.20% / TE 年化 0.30%（仅作研究）。',
    '研究偏离为 TD 0.20%、TE 年化 0.30%。',
    '| 价格基准偏离（研究数值，非确认） | TD 0.20% / TE 0.30% | research_only=True, not_confirmatory=True |',
    '| 价格基准偏离（研究数值，非确认 TD/TE） | td 0.20%／te 0.30% | research_only=true |',
])
def test_tracking_research_table_and_prose_are_protected(tmp_path, claim):
    p = _write_generation(tmp_path, payload=_payload(), md_text=claim)
    assert not any(f['id'] == 'etf-tracking-claim' for f in etf_generation_findings(p))


def test_tracking_research_label_does_not_hide_later_confirmation(tmp_path):
    p = _write_generation(tmp_path, payload=_payload(),
                          md_text='研究偏离 TD 0.20% / TE 0.30%；跟踪误差 0.40%。')
    assert any(f['id'] == 'etf-tracking-claim' for f in etf_generation_findings(p))


# ── 2026-10-10 A8：同代数值一致性 / 独立复算 / 期间 / 极值排名 / 估值排除 ──


def _kline(*, bandwidth: float | None = None, upper=1.5238, mid=1.471,
           lower=1.4181, derived_extra: dict | None = None) -> dict:
    derived: dict = dict(derived_extra or {})
    if bandwidth is not None:
        derived["boll_bandwidth_pct"] = bandwidth
    return {"boll_upper": upper, "boll_mid": mid, "boll_lower": lower,
            "derived": derived}


_PEER_ROWS = [
    {"symbol": "510300", "flow_20d_e": -0.44, "flow_5d_e": 0.0,
     "share_change_pct": -0.53, "share_change_span": 19,
     "flow_window": {"start": "2026-09-04", "end": "2026-10-09", "segments": 19,
                     "confirmable": True},
     "trend": "→ 资金面平稳", "row_count": 20},
    {"symbol": "510310", "flow_20d_e": -0.81, "flow_5d_e": -0.03,
     "share_change_pct": -4.93, "share_change_span": 19,
     "flow_window": {"start": "2026-09-04", "end": "2026-10-09", "segments": 19,
                     "confirmable": True},
     "trend": "→ 资金面平稳", "row_count": 20},
]


def _peers(rows: list[dict] | None = None, *, window_days: int = 20,
           rank: int = 1, total: int = 2) -> dict:
    return {
        "symbol": "510300", "available": True, "peers": ["510310"],
        "flow": {"window_days": window_days, "rows": rows if rows is not None else _PEER_ROWS},
        "rs": {"rank_20d": {"rank": rank, "total": total,
                            "returns": {"510300": 1.0, "510310": 0.5}}},
    }


class TestETFBollRecompute:
    """A8 反例①：BOLL 带宽独立复算（(upper−lower)/mid），不以底稿自述值为真值。"""

    def test_old_formula_payload_and_report_intercepted(self, tmp_path: Path):
        """原反例：旧公式 7.45（除以下轨）——payload 与正文引用都被拦。"""
        md = MD_BODY + "\n| BOLL 带宽 | 7.45% | kline.derived.boll_bandwidth_pct |\n"
        p = _write_generation(tmp_path, md_text=md,
                              payload=_payload(kline=_kline(bandwidth=7.45)))
        findings = etf_generation_findings(p)
        assert any(f["id"] == "etf-boll-formula-mismatch" and f["severity"] == "error"
                   for f in findings)
        assert any(f["id"] == "etf-boll-report-mismatch" and f["severity"] == "error"
                   for f in findings)

    def test_fixed_formula_is_legal_positive_control(self, tmp_path: Path):
        md = (MD_BODY
              + "\n| BOLL 带宽 | 7.19% | kline.derived.boll_bandwidth_pct |\n"
              + "| BOLL（净值） | 上轨 1.5238 / 中轨 1.471 / 下轨 1.4181 | kline.boll_* |\n")
        p = _write_generation(tmp_path, md_text=md,
                              payload=_payload(kline=_kline(bandwidth=7.19)))
        assert not any(f["id"].startswith("etf-boll")
                       for f in etf_generation_findings(p))

    def test_band_values_must_match_payload(self, tmp_path: Path):
        md = MD_BODY + "\n| BOLL（净值） | 上轨 1.9999 / 中轨 1.471 / 下轨 1.4181 | kline.boll_* |\n"
        p = _write_generation(tmp_path, md_text=md,
                              payload=_payload(kline=_kline(bandwidth=None)))
        findings = etf_generation_findings(p)
        assert any(f["id"] == "etf-boll-band-mismatch" and f["severity"] == "error"
                   for f in findings)

    def test_zero_mid_without_number_is_legal_stop(self, tmp_path: Path):
        """R3 正控：零中轨且无处给数 = 合法停算（引擎新口径不输出带宽键）。"""
        p = _write_generation(
            tmp_path, payload=_payload(kline=_kline(bandwidth=None, mid=0)))
        assert not any(f["id"].startswith("etf-boll")
                       for f in etf_generation_findings(p))

    def test_zero_mid_with_number_still_flagged(self, tmp_path: Path):
        p = _write_generation(
            tmp_path, payload=_payload(kline=_kline(bandwidth=7.19, mid=0)))
        findings = etf_generation_findings(p)
        assert any(f["id"] == "etf-boll-zero-mid" and f["severity"] == "error"
                   for f in findings)

    def test_nan_bandwidth_is_not_silently_passed(self, tmp_path: Path):
        """NaN 不能让 abs(nan−x)>tol 恒 False 静默通过。"""
        p = _write_generation(
            tmp_path, payload=_payload(kline=_kline(bandwidth=float("nan"))))
        findings = etf_generation_findings(p)
        assert any(f["id"] == "etf-boll-uncomputable" and f["severity"] == "error"
                   for f in findings)

    def test_negative_mid_is_uncomputable(self, tmp_path: Path):
        p = _write_generation(
            tmp_path, payload=_payload(kline=_kline(bandwidth=7.19, mid=-1.471)))
        findings = etf_generation_findings(p)
        assert any(f["id"] == "etf-boll-zero-mid" and f["severity"] == "error"
                   for f in findings)

    def test_missing_raw_values_warn_not_pass(self, tmp_path: Path):
        md = MD_BODY + "\n| BOLL 带宽 | 7.19% | kline.derived.boll_bandwidth_pct |\n"
        p = _write_generation(
            tmp_path, md_text=md,
            payload=_payload(kline={"derived": {"boll_bandwidth_pct": 7.19}}))
        findings = etf_generation_findings(p)
        assert any(f["id"] == "etf-boll-uncomputable" and f["severity"] == "warning"
                   for f in findings)

    def test_no_boll_data_at_all_is_skip(self, tmp_path: Path):
        p = _write_generation(tmp_path, payload=_payload())
        assert not any(f["id"].startswith("etf-boll")
                       for f in etf_generation_findings(p))


class TestETFDerivedPayloadConsistency:
    """A8：同代数字与底稿一致——正文派生字段值必须等于同代 payload。"""

    def test_mismatch_flagged(self, tmp_path: Path):
        md = MD_BODY + "\n| NAV vs MA20 偏离 | -1.00% | kline.derived.nav_vs_ma20_pct |\n"
        p = _write_generation(
            tmp_path, md_text=md,
            payload=_payload(kline={"derived": {"nav_vs_ma20_pct": -2.78}}))
        findings = etf_generation_findings(p)
        assert any(f["id"] == "etf-derived-mismatch" and f["severity"] == "error"
                   for f in findings)

    def test_match_passes(self, tmp_path: Path):
        md = MD_BODY + "\n| NAV vs MA20 偏离 | -2.78% | kline.derived.nav_vs_ma20_pct |\n"
        p = _write_generation(
            tmp_path, md_text=md,
            payload=_payload(kline={"derived": {"nav_vs_ma20_pct": -2.78}}))
        assert not any(f["id"] == "etf-derived-mismatch"
                       for f in etf_generation_findings(p))

    def test_cited_but_absent_in_payload_flagged(self, tmp_path: Path):
        md = MD_BODY + "\n| 距年内低点 | 6.12% | dist_to_ytd_low_pct |\n"
        p = _write_generation(tmp_path, md_text=md, payload=_payload())
        findings = etf_generation_findings(p)
        assert any(f["id"] == "etf-derived-not-in-payload" and f["severity"] == "error"
                   for f in findings)

    def test_stats_field_match_passes(self, tmp_path: Path):
        md = MD_BODY + "\n| 距年内低点 | 6.12% | dist_to_ytd_low_pct |\n"
        p = _write_generation(
            tmp_path, md_text=md,
            payload=_payload(),
            )
        # 无 history.stats 时引用即缺口；补上后应通过
        payload_path = p.parent / f"{p.stem}.payload.json"
        pl = json.loads(payload_path.read_text(encoding="utf-8"))
        pl["history"] = {"stats": {"dist_to_ytd_low_pct": 6.12}}
        snapshot_core.seal(pl)
        payload_path.write_text(json.dumps(pl, ensure_ascii=False), encoding="utf-8")
        assert not any(f["id"] == "etf-derived-not-in-payload"
                       for f in etf_generation_findings(p))


class TestETFPeerPeriodAndRank:
    """A8 反例②③：期间一致（同类产品窗口/份额窗口）与极值排名。"""

    _TABLE_HEAD = "| 代码 | 名称 | 20日资金流(亿) | 近5日(亿) | 份额变化% | 趋势 |\n|---|---|---:|---:|---:|---|\n"

    @pytest.mark.parametrize("header,value,error", [
        ("份额变化（万份）", "10000", False),
        ("份额变化（份）", "100000000", False),
        ("份额变化", "10000", False),
        ("份额变化%", "10", False),
        ("份额变化（百分比）", "10000", True),
        ("份额变化％", "10000", True),
    ])
    def test_share_change_unit_is_respected(self, tmp_path, header, value, error):
        rows = [dict(_PEER_ROWS[0], share_change_pct=10)]
        md = MD_BODY + f"\n| 代码 | {header} |\n|---|---:|\n| 510300 | {value} |\n"
        path = _write_generation(tmp_path, md_text=md,
                                 payload=_payload(peers=_peers(rows)))
        assert any(f["id"] == "etf-peer-row-mismatch"
                   for f in etf_generation_findings(path)) is error

    @pytest.mark.parametrize("claim,error", [
        ("510310 的20日收益排名 1/2。", False),
        ("主标的年度收益排名 1/2。", False),
        ("主标的120日收益排名 1/2。", False),
        ("主标的20日波动为3%且年内收益排名 1/2。", False),
        ("主标的20日收益为3%而规模排名 1/2。", False),
        ("20日收益排名 1/2。", False),
        ("510300 的20日收益排名 1/2。", True),
        ("本标的20日收益排名 2/2。主标的20日收益排名 1/2。", True),
        ("510310 的20日收益排名 1/2；本标的20日收益排名 2/2。", False),
    ])
    def test_rank_claim_subject_and_window(self, tmp_path, claim, error):
        peers = _peers(rank=2, total=2)
        peers["rs"]["rank_20d"]["returns"] = {"510300": 0.5, "510310": 1.0}
        path = _write_generation(tmp_path, md_text=MD_BODY + "\n[分析] " + claim,
                                 payload=_payload(peers=peers))
        assert any(f["id"] == "etf-rank-mismatch"
                   for f in etf_generation_findings(path)) is error

    @pytest.mark.parametrize("case", ["window_false", "row_false", "missing_value",
                                      "missing_member", "group_false", "mixed_unconfirmed",
                                      "subject_missing", "one_value"])
    def test_extreme_requires_every_member_confirmed(self, tmp_path, case):
        rows = [dict(r, confirmable=True,
                     flow_window=dict(r["flow_window"], confirmable=True)) for r in _PEER_ROWS]
        peers = _peers(rows)
        if case == "window_false":
            rows[1]["flow_window"]["confirmable"] = False
        elif case == "row_false":
            rows[1]["confirmable"] = False
        elif case == "group_false":
            peers["flow"]["common_window"] = {"confirmable": False}
        elif case == "mixed_unconfirmed":
            rows[1]["share_change_pct"] = 1.0
            rows[1]["flow_window"]["confirmable"] = False
        elif case == "subject_missing":
            rows[0]["share_change_pct"] = None
        elif case == "one_value":
            rows[1]["share_change_pct"] = None
        else:
            peers["peers"].append("510500")
            if case == "missing_value":
                rows.append({"symbol": "510500", "share_change_pct": None,
                             "flow_window": None, "confirmable": False})
        md = MD_BODY + "\n[分析] 本标的份额降幅为组内最小。\n"
        p = _write_generation(tmp_path, md_text=md, payload=_payload(peers=peers))
        findings = etf_generation_findings(p)
        assert any(f["id"] == "etf-extreme-window-unverified" and f["severity"] == "error"
                   for f in findings)
        assert not any(f["id"] == "etf-extreme-claim-mismatch" for f in findings)

    def test_extreme_confirmation_missing_is_unverified(self, tmp_path):
        rows = [dict(r, flow_window=dict(r["flow_window"])) for r in _PEER_ROWS]
        for row in rows:
            row["flow_window"].pop("confirmable", None)
            row.pop("confirmable", None)
        p = _write_generation(tmp_path,
                              md_text=MD_BODY + "\n[分析] 本标的份额降幅为组内最小。\n",
                              payload=_payload(peers=_peers(rows)))
        assert any(f["id"] == "etf-extreme-window-unverified" and f["severity"] == "warning"
                   for f in etf_generation_findings(p))

    def test_peer_row_values_must_match_payload(self, tmp_path: Path):
        md = MD_BODY + "\n" + self._TABLE_HEAD + (
            "| 510300 | 沪深300ETF | -0.44 | 0.00 | -0.53 | → 资金面平稳 |\n"
            "| 510310 | 沪深300ETF华泰 | **-9.99** | -0.03 | -4.93 | → 资金面平稳 |\n")
        p = _write_generation(tmp_path, md_text=md, payload=_payload(peers=_peers()))
        findings = etf_generation_findings(p)
        assert any(f["id"] == "etf-peer-row-mismatch" and f["severity"] == "error"
                   for f in findings)

    def test_peer_row_matching_table_passes(self, tmp_path: Path):
        md = MD_BODY + "\n" + self._TABLE_HEAD + (
            "| 510300 | 沪深300ETF | -0.44 | 0.00 | -0.53 | → 资金面平稳 |\n"
            "| 510310 | 沪深300ETF华泰 | -0.81 | -0.03 | -4.93 | → 资金面平稳 |\n")
        p = _write_generation(tmp_path, md_text=md, payload=_payload(peers=_peers()))
        assert not any(f["id"].startswith("etf-peer") for f in etf_generation_findings(p))

    def test_peer_5day_column_mismatch_caught(self, tmp_path: Path):
        """近 5 日列（模板「近5日(亿)」无「资金流」字样）同样必须对拍。"""
        md = MD_BODY + "\n" + self._TABLE_HEAD + (
            "| 510300 | 沪深300ETF | -0.44 | **9.99** | -0.53 | → 资金面平稳 |\n")
        p = _write_generation(tmp_path, md_text=md, payload=_payload(peers=_peers()))
        findings = etf_generation_findings(p)
        assert any(f["id"] == "etf-peer-row-mismatch" and "flow_5d_e" in f["message"]
                   for f in findings)

    def test_unavailable_row_must_not_be_filled(self, tmp_path: Path):
        rows = [dict(_PEER_ROWS[0]), {"symbol": "510310", "flow_20d_e": None,
                                      "flow_5d_e": None, "share_change_pct": None,
                                      "share_change_span": None, "row_count": None,
                                      "note": "份额历史不可用"}]
        md = MD_BODY + "\n" + self._TABLE_HEAD + (
            "| 510310 | 沪深300ETF华泰 | -0.81 | — | — | — |\n")
        p = _write_generation(tmp_path, md_text=md,
                              payload=_payload(peers=_peers(rows)))
        findings = etf_generation_findings(p)
        assert any(f["id"] == "etf-peer-row-fabricated" and f["severity"] == "error"
                   for f in findings)

    def test_window_wording_must_match_payload(self, tmp_path: Path):
        head = ("| 代码 | 名称 | 60日资金流(亿) | 份额变化% |\n|---|---|---:|---:|\n")
        md = MD_BODY + "\n" + head + "| 510300 | 沪深300ETF | -0.44 | -0.53 |\n"
        p = _write_generation(tmp_path, md_text=md,
                              payload=_payload(peers=_peers(window_days=20)))
        findings = etf_generation_findings(p)
        assert any(f["id"] == "etf-peer-window-mismatch" and f["severity"] == "error"
                   for f in findings)

    def test_missing_span_warns(self, tmp_path: Path):
        rows = [dict(r) for r in _PEER_ROWS]
        for r in rows:
            r.pop("share_change_span")
        p = _write_generation(tmp_path, payload=_payload(peers=_peers(rows)))
        findings = etf_generation_findings(p)
        assert any(f["id"] == "etf-peer-span-missing" and f["severity"] == "warning"
                   for f in findings)

    def test_share_window_and_rowcount_match_payload(self, tmp_path: Path):
        sh = {"date_range": "20260904 ~ 20261009", "summary": {"row_count": 20}}
        ok = MD_BODY + "\n| 窗口 | 2026-09-04 ~ 2026-10-09（20 个数据行） | share_history.date_range |\n"
        p = _write_generation(tmp_path, md_text=ok,
                              payload=_payload(share_history=sh))
        assert not any(f["id"].startswith("etf-share-")
                       for f in etf_generation_findings(p))
        bad = ok.replace("2026-09-04 ~ 2026-10-09", "2026-08-01 ~ 2026-10-09")
        p2 = _write_generation(tmp_path / "bad", md_text=bad,
                               payload=_payload(share_history=sh))
        assert any(f["id"] == "etf-share-window-mismatch"
                   for f in etf_generation_findings(p2))
        bad2 = ok.replace("20 个数据行", "19 个数据行")
        p3 = _write_generation(tmp_path / "bad2", md_text=bad2,
                               payload=_payload(share_history=sh))
        assert any(f["id"] == "etf-share-rowcount-mismatch"
                   for f in etf_generation_findings(p3))

    def test_rank_claim_mismatch_flagged(self, tmp_path: Path):
        md = MD_BODY + "\n[分析] 主标的 20 日收益排名 2/2。\n"
        p = _write_generation(tmp_path, md_text=md,
                              payload=_payload(peers=_peers(rank=1, total=2)))
        findings = etf_generation_findings(p)
        assert any(f["id"] == "etf-rank-mismatch" and f["severity"] == "error"
                   for f in findings)

    def test_rank_claim_match_passes(self, tmp_path: Path):
        md = MD_BODY + "\n[分析] 主标的 20 日收益排名 1/2。\n"
        p = _write_generation(tmp_path, md_text=md,
                              payload=_payload(peers=_peers(rank=1, total=2)))
        assert not any(f["id"] == "etf-rank-mismatch"
                       for f in etf_generation_findings(p))

    def test_group_extreme_claim_recomputed(self, tmp_path: Path):
        # 主标的 -2.31%，另一行 -0.53%：同号组「降幅最小」= 最接近零 = -0.53（另一行）
        rows = [dict(_PEER_ROWS[0], share_change_pct=-2.31),
                dict(_PEER_ROWS[1], share_change_pct=-0.53)]
        md = MD_BODY + "\n[分析] 本标的份额降幅为组内最小（-2.31%）。\n"
        p = _write_generation(tmp_path, md_text=md,
                              payload=_payload(peers=_peers(rows)))
        findings = etf_generation_findings(p)
        assert any(f["id"] == "etf-extreme-claim-mismatch" and f["severity"] == "error"
                   for f in findings), "组内最小应为 -0.53（另一行），-2.31 非极值"

    def test_group_extreme_true_claim_passes(self, tmp_path: Path):
        md = MD_BODY + "\n[分析] 本标的份额降幅为组内最小（-0.53%）。\n"
        p = _write_generation(tmp_path, md_text=md,
                              payload=_payload(peers=_peers()))
        assert not any(f["id"] == "etf-extreme-claim-mismatch"
                       for f in etf_generation_findings(p))

    def test_zero_member_participates_in_extreme(self, tmp_path: Path):
        """R1 反例：零份额变化是合法极值——存在 0.00 成员时本标的不是「降幅最小」。"""
        rows = [dict(_PEER_ROWS[0], share_change_pct=-0.53),
                dict(_PEER_ROWS[1], share_change_pct=0.0)]
        md = MD_BODY + "\n[分析] 本标的份额降幅为组内最小（-0.53%）。\n"
        p = _write_generation(tmp_path, md_text=md,
                              payload=_payload(peers=_peers(rows)))
        findings = etf_generation_findings(p)
        assert any(f["id"] == "etf-extreme-claim-mismatch" and f["severity"] == "error"
                   for f in findings), "零不得被过滤：0.00 才是组内最小降幅"

    def test_zero_subject_is_judgeable(self, tmp_path: Path):
        rows = [dict(_PEER_ROWS[0], share_change_pct=0.0),
                dict(_PEER_ROWS[1], share_change_pct=-4.93)]
        md = MD_BODY + "\n[分析] 本标的份额降幅为组内最小（0.00%）。\n"
        p = _write_generation(tmp_path, md_text=md,
                              payload=_payload(peers=_peers(rows)))
        assert not any(f["id"] == "etf-extreme-claim-mismatch"
                       for f in etf_generation_findings(p))

    def test_mixed_sign_group_not_mechanically_judged(self, tmp_path: Path):
        rows = [dict(_PEER_ROWS[0], share_change_pct=-0.53),
                dict(_PEER_ROWS[1], share_change_pct=+0.40)]
        md = MD_BODY + "\n[分析] 本标的份额降幅为组内最小。\n"
        p = _write_generation(tmp_path, md_text=md,
                              payload=_payload(peers=_peers(rows)))
        assert not any(f["id"] == "etf-extreme-claim-mismatch"
                       for f in etf_generation_findings(p)), \
            "真混合符号下极值语义不唯一：跳过而非误报（边界如实声明）"

    def test_generic_share_wording_uses_literal_semantics(self, tmp_path: Path):
        """「份额变化…最小」不倒置（只有「降幅」倒置）：最小 = 数值最小。"""
        rows = [dict(_PEER_ROWS[0], share_change_pct=-0.53),
                dict(_PEER_ROWS[1], share_change_pct=-4.93)]
        md = MD_BODY + "\n[分析] 本标的份额变化组内最小。\n"
        p = _write_generation(tmp_path, md_text=md,
                              payload=_payload(peers=_peers(rows)))
        findings = etf_generation_findings(p)
        assert any(f["id"] == "etf-extreme-claim-mismatch" for f in findings), \
            "字面读法：组内最小=-4.93（另一行），-0.53 不该判过"

    def test_no_common_window_refuses_mechanical_extreme_judgement(self, tmp_path: Path):
        """R6 反例：各产品各自同窗 ≠ 产品间共同窗口——已知起止不同 + 无条件极值断言 → error。"""
        rows = [dict(_PEER_ROWS[0], share_change_pct=-0.53),
                dict(_PEER_ROWS[1], share_change_pct=-4.93,
                     flow_window={"start": "2026-09-10", "end": "2026-10-09",
                                  "segments": 17})]
        md = MD_BODY + "\n[分析] 本标的份额降幅为组内最小（-0.53%）。\n"
        p = _write_generation(tmp_path, md_text=md,
                              payload=_payload(peers=_peers(rows)))
        findings = etf_generation_findings(p)
        assert any(f["id"] == "etf-extreme-window-unverified" and f["severity"] == "error"
                   for f in findings)
        assert not any(f["id"] == "etf-extreme-claim-mismatch" for f in findings), \
            "不可比产品不得按 values 无条件重排名"

    def test_elsewhere_disclosure_does_not_exempt_claim(self, tmp_path: Path):
        """R6 复查反例：别处（表头）出现「实际覆盖」不得豁免另一处的无条件极值断言。"""
        rows = [dict(_PEER_ROWS[0], share_change_pct=-0.53),
                dict(_PEER_ROWS[1], share_change_pct=-4.93,
                     flow_window={"start": "2026-09-10", "end": "2026-10-09",
                                  "segments": 17})]
        md = (MD_BODY
              + "\n| 产品 | 实际覆盖 |\n|---|---|\n| A | 19 |\n"
              + "\n[分析] 本标的份额降幅为组内最小（-0.53%）。\n")
        p = _write_generation(tmp_path, md_text=md,
                              payload=_payload(peers=_peers(rows)))
        findings = etf_generation_findings(p)
        assert any(f["id"] == "etf-extreme-window-unverified" and f["severity"] == "error"
                   for f in findings), "全篇词语（表头）不得触发豁免"

    def test_common_window_missing_refuses_and_warns(self, tmp_path: Path):
        rows = [dict(r) for r in _PEER_ROWS]
        for r in rows:
            r.pop("flow_window")
        md = MD_BODY + "\n[分析] 本标的份额降幅为组内最小（-0.53%）。\n"
        p = _write_generation(tmp_path, md_text=md,
                              payload=_payload(peers=_peers(rows)))
        findings = etf_generation_findings(p)
        assert any(f["id"] == "etf-extreme-window-unverified"
                   and f["severity"] == "warning" for f in findings)

    def test_same_clause_limitation_exempts_window_unverified(self, tmp_path: Path):
        """合法正控（Codex R6）：同句明确「窗口不同不判断组内最小」→ 豁免。"""
        rows = [dict(_PEER_ROWS[0], share_change_pct=-0.53),
                dict(_PEER_ROWS[1], share_change_pct=-4.93,
                     flow_window={"start": "2026-09-10", "end": "2026-10-09",
                                  "segments": 17})]
        md = (MD_BODY
              + "\n[分析] 各产品窗口起止不一致，本标的份额降幅是否组内最小不作判断"
                "（不可比）。\n")
        p = _write_generation(tmp_path, md_text=md,
                              payload=_payload(peers=_peers(rows)))
        assert not any(f["id"] == "etf-extreme-window-unverified"
                       for f in etf_generation_findings(p))

    def test_negation_first_limitation_exempts(self, tmp_path: Path):
        """Codex 正控（root-qc-probes-final unmatched_no_rank_valid，误伤修复）：
        否定在前、对象在后（「不判断…组内最小」）同样必须豁免。"""
        rows = [dict(_PEER_ROWS[0], share_change_pct=-0.53),
                dict(_PEER_ROWS[1], share_change_pct=-4.93,
                     flow_window={"start": "2026-09-10", "end": "2026-10-09",
                                  "segments": 17})]
        for text in ("窗口不同，不判断本标的份额降幅组内最小。",
                     "窗口不同，本标的份额降幅不作排名结论。"):
            p = _write_generation(tmp_path / text[-8:-4], md_text=MD_BODY + f"\n[分析] {text}\n",
                                  payload=_payload(peers=_peers(rows)))
            assert not any(f["id"] == "etf-extreme-window-unverified"
                           for f in etf_generation_findings(p)), text

    def test_negation_in_another_clause_does_not_exempt(self, tmp_path: Path):
        """反例：另一分句的否定不得豁免本分句的无条件极值断言（分句作用域）。"""
        rows = [dict(_PEER_ROWS[0], share_change_pct=-0.53),
                dict(_PEER_ROWS[1], share_change_pct=-4.93,
                     flow_window={"start": "2026-09-10", "end": "2026-10-09",
                                  "segments": 17})]
        md = (MD_BODY
              + "\n[分析] 不能判断共同窗口；本标的份额降幅组内最小。\n")
        p = _write_generation(tmp_path, md_text=md,
                              payload=_payload(peers=_peers(rows)))
        findings = etf_generation_findings(p)
        assert any(f["id"] == "etf-extreme-window-unverified" and f["severity"] == "error"
                   for f in findings)


class TestETFPeerWindowFields:
    """R2 反例：长窗口（120 日）合法表不得被误判——按 window_days+flow_total_e 消费。"""

    _LONG_ROWS = [
        {"symbol": "510300", "flow_total_e": -1.2, "flow_20d_e": None,
         "flow_5d_e": 0.0, "share_change_pct": -0.53, "share_change_span": 118,
         "window_days": 120, "trend": "→ 资金面平稳", "row_count": 120,
         "flow_window": {"start": "2026-04-10", "end": "2026-10-09", "segments": 118},
         "note": "flow_20d_e 仅 20 日窗口别名"},
        {"symbol": "510310", "flow_total_e": -2.4, "flow_20d_e": None,
         "flow_5d_e": -0.03, "share_change_pct": -4.93, "share_change_span": 118,
         "window_days": 120, "trend": "→ 资金面平稳", "row_count": 120,
         "flow_window": {"start": "2026-04-10", "end": "2026-10-09", "segments": 118},
         "note": "flow_20d_e 仅 20 日窗口别名"},
    ]
    _HEAD = "| 代码 | 名称 | 120日资金流(亿) | 份额变化% |\n|---|---|---:|---:|\n"

    @pytest.mark.parametrize("days", [15, 25, 35, 55, 105, 115])
    @pytest.mark.parametrize("space", ["", " "])
    def test_complete_period_selects_window_total(self, tmp_path, days, space):
        rows = [dict(self._LONG_ROWS[0], window_days=days,
                     flow_total_e=10.0, flow_5d_e=2.0)]
        head = (f"| 代码 | {days}{space}日资金流(亿) | 近5日(亿) |\n"
                "|---|---:|---:|\n")
        for name, total, recent, expected in [
            ("valid", "10.0", "2.0", None),
            ("wrong_total", "2.0", "2.0", "flow_total_e"),
            ("wrong_recent", "10.0", "10.0", "flow_5d_e"),
        ]:
            md = MD_BODY + "\n" + head + f"| 510300 | {total} | {recent} |\n"
            p = _write_generation(tmp_path / name, md_text=md,
                                  payload=_payload(peers=_peers(rows, window_days=days)))
            findings = [f for f in etf_generation_findings(p)
                        if f["id"].startswith("etf-peer")]
            if expected is None:
                assert findings == []
            else:
                assert any(f["id"] == "etf-peer-row-mismatch"
                           and expected in f["message"] for f in findings)

    def test_long_window_table_passes(self, tmp_path: Path):
        md = MD_BODY + "\n" + self._HEAD + (
            "| 510300 | 沪深300ETF | -1.20 | -0.53 |\n")
        p = _write_generation(
            tmp_path, md_text=md,
            payload=_payload(peers=_peers(self._LONG_ROWS, window_days=120)))
        assert not any(f["id"].startswith("etf-peer")
                       for f in etf_generation_findings(p)), \
            "120 日合法表不得因别名 flow_20d_e=None 被误判 fabricated"

    def test_15day_header_against_20day_payload_is_rejected(self, tmp_path):
        md = (MD_BODY + "\n| 代码 | 15日资金流(亿) |\n|---|---:|\n"
              "| 510300 | 2.0 |\n")
        rows = [dict(self._LONG_ROWS[0], window_days=20,
                     flow_total_e=10.0, flow_5d_e=2.0)]
        p = _write_generation(tmp_path, md_text=md,
                              payload=_payload(peers=_peers(rows, window_days=20)))
        findings = etf_generation_findings(p)
        assert any(f["id"] == "etf-peer-window-mismatch" for f in findings)

    def test_long_window_value_mismatch_still_caught(self, tmp_path: Path):
        md = MD_BODY + "\n" + self._HEAD + (
            "| 510300 | 沪深300ETF | -9.99 | -0.53 |\n")
        p = _write_generation(
            tmp_path, md_text=md,
            payload=_payload(peers=_peers(self._LONG_ROWS, window_days=120)))
        assert any(f["id"] == "etf-peer-row-mismatch"
                   for f in etf_generation_findings(p))

    def test_long_window_unavailable_row_still_caught(self, tmp_path: Path):
        rows = [dict(self._LONG_ROWS[0], flow_total_e=None)]
        md = MD_BODY + "\n" + self._HEAD + (
            "| 510300 | 沪深300ETF | -1.20 | -0.53 |\n")
        p = _write_generation(
            tmp_path, md_text=md,
            payload=_payload(peers=_peers(rows, window_days=120)))
        assert any(f["id"] == "etf-peer-row-fabricated"
                   for f in etf_generation_findings(p))


class TestETFValuationExcludedClaim:
    """A8 反例④：已排除估值摘要却在结论区称低位（自相矛盾的可机械反例）。"""

    _EXCLUDED = "公开检索摘要口径不一，不构成本报告定量结论。\n"

    def test_excluded_summary_but_conclusion_claims_low_fails(self, tmp_path: Path):
        md = (MD_BODY
              + "\n## 结论\n\n估值处于低位，港股配置价值明显。\n"
              + "\n## 附注\n\n" + self._EXCLUDED)
        p = _write_generation(tmp_path, md_text=md, payload=_payload())
        findings = etf_generation_findings(p)
        assert any(f["id"] == "etf-valuation-excluded-claim"
                   and f["severity"] == "error" for f in findings)

    def test_sourced_low_claim_is_not_mechanically_blocked(self, tmp_path: Path):
        """带来源的低位断言可能是本次已读一手材料 → 机器不裁，交人工复检。"""
        md = (MD_BODY
              + "\n## 结论\n\n估值处于低位（[来源: 恒生指数公司官网 2026-10-08]）。\n"
              + "\n## 附注\n\n" + self._EXCLUDED)
        p = _write_generation(tmp_path, md_text=md, payload=_payload())
        assert not any(f["id"] == "etf-valuation-excluded-claim"
                       for f in etf_generation_findings(p))

    def test_same_clause_exclusion_negation_exempts(self, tmp_path: Path):
        md = (MD_BODY
              + "\n## 结论\n\n估值摘要口径不一，低位判断不进入结论。\n"
              + "\n## 附注\n\n" + self._EXCLUDED)
        p = _write_generation(tmp_path, md_text=md, payload=_payload())
        assert not any(f["id"] == "etf-valuation-excluded-claim"
                       for f in etf_generation_findings(p))

    def test_payload_with_index_pe_not_blocked(self, tmp_path: Path):
        md = (MD_BODY
              + "\n## 结论\n\n估值处于低位。\n"
              + "\n## 附注\n\n" + self._EXCLUDED)
        p = _write_generation(tmp_path, md_text=md, payload=_payload(index_pe=13.5))
        assert not any(f["id"] == "etf-valuation-excluded-claim"
                       for f in etf_generation_findings(p))

    def test_no_exclusion_anchor_not_blocked(self, tmp_path: Path):
        md = MD_BODY + "\n## 结论\n\n估值处于低位。\n"
        p = _write_generation(tmp_path, md_text=md, payload=_payload())
        assert not any(f["id"] == "etf-valuation-excluded-claim"
                       for f in etf_generation_findings(p))

    def test_non_conclusion_section_claim_not_blocked(self, tmp_path: Path):
        md = (MD_BODY
              + "\n## 9. 因子与主题逻辑\n\n[分析] 部分机构口径称估值处于低位。\n"
              + "\n## 附注\n\n" + self._EXCLUDED)
        p = _write_generation(tmp_path, md_text=md, payload=_payload())
        assert not any(f["id"] == "etf-valuation-excluded-claim"
                       for f in etf_generation_findings(p))


# ── 2026-10-11 R1/R2/R3 复查回归（逐声明组内极值 / 份额口径锚定 / 部分合计限定） ──

_MD_513660 = MD_BODY.replace("华泰柏瑞沪深300ETF（510300）", "华夏中证500ETF（513660）")
_R1_FLOW_START, _R1_FLOW_END = "20260901", "20260930"


def _r1_row(symbol: str, share: float, *, confirmable: bool = True,
            flow: float = 1.0) -> dict:
    return {
        "symbol": symbol, "flow_total_e": flow, "flow_20d_e": flow,
        "flow_5d_e": 0.0, "share_change_pct": share, "share_change_span": 20,
        "window_days": 20, "confirmable": confirmable,
        "flow_window": {"start": _R1_FLOW_START, "end": _R1_FLOW_END,
                        "segments": 20, "confirmable": confirmable},
        "trend": "→ 资金面平稳", "row_count": 21,
    }


def _r1_peers(rows: list[dict], *, common_confirmable: bool = True) -> dict:
    """R1 复查场景 payload：main=513660、peers=[513600]、rs returns 10/5。"""
    return {
        "symbol": "513660", "available": True, "peers": ["513600"],
        "flow": {"window_days": 20, "rows": rows,
                 "common_window": {"start": _R1_FLOW_START, "end": _R1_FLOW_END,
                                   "confirmable": common_confirmable,
                                   "all_products_same_window": True,
                                   "products_confirmable": 2}},
        "rs": {"rank_20d": {"rank": 1, "total": 2,
                            "returns": {"513660": 10.0, "513600": 5.0}}},
    }


def _write_r1(tmp_path: Path, md_text: str, rows: list[dict], *,
              common_confirmable: bool = True, name: str = "gen") -> Path:
    return _write_generation(
        tmp_path / name, md_text=_MD_513660 + "\n" + md_text,
        payload=_payload(symbol="513660",
                         peers=_r1_peers(rows, common_confirmable=common_confirmable)),
        dirname="513660-中证500ETF")


class TestR1ExtremeMultiClaim:
    """R1（2026-10-11 复查）：组内极值逐声明核验（finditer，非 search+break）。

    首句正确或被否证不得遮蔽后续错误声明；逗号/分句各自独立识别主体/指标/
    限定；错误与不可比无条件断言均拦；未知/歧义主体留人工（不猜主标的）。
    """

    def test_peer_only_min_claim_is_error(self, tmp_path: Path):
        p = _write_r1(tmp_path, "[分析] 513600 份额变化组内最小。\n",
                      [_r1_row("513660", 1.0), _r1_row("513600", 2.0)])
        findings = etf_generation_findings(p)
        assert any(f["id"] == "etf-extreme-claim-mismatch"
                   and f["severity"] == "error" and "513600" in f["message"]
                   for f in findings)

    def test_first_correct_claim_does_not_hide_later_error(self, tmp_path: Path):
        md = ("[分析] 本标的份额变化组内最小。\n"
              "513600 份额变化组内最小。\n")
        p = _write_r1(tmp_path, md, [_r1_row("513660", 1.0), _r1_row("513600", 2.0)])
        findings = etf_generation_findings(p)
        assert any(f["id"] == "etf-extreme-claim-mismatch" and "513600" in f["message"]
                   for f in findings), "首句正确不得遮蔽后续错误声明"

    def test_comma_separated_claims_judged_independently(self, tmp_path: Path):
        md = "[分析] 本标的份额变化组内最小，513600 份额变化组内最小。\n"
        p = _write_r1(tmp_path, md, [_r1_row("513660", 1.0), _r1_row("513600", 2.0)])
        assert any(f["id"] == "etf-extreme-claim-mismatch" and "513600" in f["message"]
                   for f in etf_generation_findings(p)), "逗号分句同样独立核验"

    def test_negation_exempts_only_its_own_clause(self, tmp_path: Path):
        rows = [_r1_row("513660", 1.0), _r1_row("513600", 2.0, confirmable=False)]
        md = ("[分析] 本报告不判断本标的份额变化组内最小。\n"
              "本标的份额变化组内最大。\n")
        p = _write_r1(tmp_path, md, rows, common_confirmable=False)
        findings = etf_generation_findings(p)
        assert any(f["id"] == "etf-extreme-window-unverified" and f["severity"] == "error"
                   for f in findings), "前条否证不得豁免后条无条件断言"

    @pytest.mark.parametrize("claim", [
        "513660 与 513600 份额变化组内最小。",
        "该产品份额变化组内最小。",
    ])
    def test_ambiguous_subject_left_to_manual(self, tmp_path: Path, claim):
        p = _write_r1(tmp_path, f"[分析] {claim}\n",
                      [_r1_row("513660", 1.0), _r1_row("513600", 2.0)])
        findings = etf_generation_findings(p)
        assert not any(f["id"] == "etf-extreme-claim-mismatch" for f in findings), \
            "主体多候选/未知：留人工，不猜主标的"


class TestR2ExtremeCaliberNotCrossed:
    """R2（2026-10-11 复查）：份额极值分支只锚定本分句明确份额指标。

    「净值涨幅组内最高」等非份额口径不得拿份额变化值裁决（原误报复现）；
    跨分句指标不串用；真正的份额涨幅/降幅规则保留。
    """

    def test_nav_return_claim_not_judged_by_share_values(self, tmp_path: Path):
        """原反例：513660 净值涨幅组内最高（rs returns 10 最高），
        份额 1/2 会被误读为组内最小 → 不得报 etf-extreme-claim-mismatch。"""
        p = _write_r1(tmp_path, "[分析] 513660 20日净值涨幅组内最高。\n",
                      [_r1_row("513660", 1.0), _r1_row("513600", 2.0)])
        assert not any(f["id"] == "etf-extreme-claim-mismatch"
                       for f in etf_generation_findings(p))

    @pytest.mark.parametrize("md,subject", [
        ("[分析] 513660 净值涨幅组内最高，513600 份额变化组内最小。\n", "513600"),
        ("[分析] 513600 份额变化组内最小，513660 净值涨幅组内最高。\n", "513600"),
    ])
    def test_cross_clause_calibers_not_mixed(self, tmp_path: Path, md, subject):
        p = _write_r1(tmp_path, md, [_r1_row("513660", 1.0), _r1_row("513600", 2.0)])
        findings = [f for f in etf_generation_findings(p)
                    if f["id"] == "etf-extreme-claim-mismatch"]
        assert len(findings) == 1 and subject in findings[0]["message"], \
            "只裁决明确份额分句；净值分句跳过留人工、不串用份额值"

    def test_share_gain_extreme_still_judged(self, tmp_path: Path):
        """保留真份额涨幅规则：份额 1/2 时「本标的份额涨幅组内最高」为错。"""
        p = _write_r1(tmp_path, "[分析] 本标的份额涨幅组内最高。\n",
                      [_r1_row("513660", 1.0), _r1_row("513600", 2.0)])
        assert any(f["id"] == "etf-extreme-claim-mismatch" and "份额变化" in f["message"]
                   for f in etf_generation_findings(p))


class TestETFPeerClaimBoundaries:
    """QC 正反例：分句否证、绝对份额和非资金流列不能串用。"""

    @pytest.mark.parametrize("boundary", ["，", ",", "、", "；", ";", "！", "?", "|"])
    @pytest.mark.parametrize("confirmed", [True, False])
    def test_preceding_negation_cannot_hide_extreme(self, tmp_path, boundary, confirmed):
        rows = [_r1_row("513660", 1.0),
                _r1_row("513600", 2.0, confirmable=confirmed)]
        p = _write_r1(tmp_path,
                      f"不作份额排名{boundary}本标的份额变化组内最大。", rows,
                      common_confirmable=confirmed)
        expected = ("etf-extreme-claim-mismatch" if confirmed
                    else "etf-extreme-window-unverified")
        assert any(f["id"] == expected and f["severity"] == "error"
                   for f in etf_generation_findings(p))

    @pytest.mark.parametrize("claim", [
        "本标的份额总量组内最大。",
        "本标的总份额组内最大。",
        "本标的份额数量组内最大。",
        "本标的份额组内最大。",
        "本标的份额变化（万份）组内最大。",
        "本标的份额变化（份）组内最大。",
        "本标的份额变化为10000份且组内最大。",
        "本标的份额变化为1亿份且组内最大。",
    ])
    def test_absolute_shares_not_compared_with_growth_pct(self, tmp_path, claim):
        # 最大绝对份额可同时具有较小的增长百分比；当前 schema 未提供绝对份额。
        p = _write_r1(tmp_path, claim,
                      [_r1_row("513660", 1.0), _r1_row("513600", 2.0)])
        assert not any(f["id"].startswith("etf-extreme-")
                       for f in etf_generation_findings(p))

    @pytest.mark.parametrize("claim,wrong", [
        ("本标的份额变化组内最大。", True),
        ("本标的份额涨幅组内最高。", True),
        ("本标的份额变化百分比组内最大。", True),
        ("本标的份额变化组内最小。", False),
        ("不判断本标的份额变化组内最大。", False),
    ])
    def test_percentage_claims_and_same_clause_negation(self, tmp_path, claim, wrong):
        p = _write_r1(tmp_path, claim,
                      [_r1_row("513660", 1.0), _r1_row("513600", 2.0)])
        errors = [f for f in etf_generation_findings(p)
                  if f["id"] == "etf-extreme-claim-mismatch"]
        assert bool(errors) is wrong

    @pytest.mark.parametrize("header,flow_context,mismatch", [
        ("近5日成交额(亿)", False, False),
        ("近5日平均规模(亿)", False, False),
        ("近5日成交额(亿)", True, False),
        ("近5日平均规模(亿)", True, False),
        ("近5日(亿)", False, False),
        ("近5日(亿)", True, True),
        ("5日（亿元）", True, True),
        ("近5日资金流(亿)", False, True),
    ])
    def test_five_day_columns_need_flow_semantics(self, tmp_path, header,
                                                 flow_context, mismatch):
        extra_head = " | 20日资金流(亿)" if flow_context else ""
        extra_rule = " | ---:" if flow_context else ""
        extra_value = " | 1.0" if flow_context else ""
        table = (f"| 代码 | {header}{extra_head} |\n"
                 f"| --- | ---:{extra_rule} |\n"
                 f"| 513660 | 10.0{extra_value} |\n")
        p = _write_r1(tmp_path, table,
                      [_r1_row("513660", 1.0), _r1_row("513600", 2.0)])
        errors = [f for f in etf_generation_findings(p)
                  if f["id"] in {"etf-peer-row-mismatch", "etf-peer-row-fabricated"}]
        assert bool(errors) is mismatch


class TestR3PartialFlowLabel:
    """R3（2026-10-11 复查）：不可确认行全窗流量数字的限定词判定。

    本行/本单元格直接限定（部分区间合计/窗口不可确认/可确认段小计）通过；
    其他指标「部分」、明确否定、跨行文字、「已确认」措辞不豁免；已确认行正常。
    """

    _HEAD = "| 代码 | 20日资金流(亿) | 份额变化% | 备注 |\n|---|---:|---:|---|\n"

    @staticmethod
    def _rows() -> list[dict]:
        rows = [dict(r) for r in _PEER_ROWS]
        rows[1] = dict(rows[1], flow_20d_e=0.0, flow_5d_e=0.0, share_change_pct=2.0,
                       flow_window={"start": "2026-09-04", "end": "2026-10-09",
                                    "segments": 19, "confirmable": False},
                       confirmable=False)
        return rows

    @staticmethod
    def _write(tmp_path: Path, table_body: str, *, rows: list[dict] | None = None
               ) -> Path:
        md = MD_BODY + "\n" + TestR3PartialFlowLabel._HEAD + table_body
        return _write_generation(tmp_path, md_text=md,
                                 payload=_payload(peers=_peers(rows or
                                                               TestR3PartialFlowLabel._rows())))

    def _unlabeled(self, p: Path) -> list[dict]:
        return [f for f in etf_generation_findings(p)
                if f["id"] == "etf-peer-partial-flow-unlabeled"]

    def test_unconfirmed_row_without_label_is_error(self, tmp_path: Path):
        errs = self._unlabeled(self._write(tmp_path, "| 510310 | 0 | 2 | — |\n"))
        assert len(errs) == 1 and errs[0]["severity"] == "error"

    def test_other_metric_partial_does_not_exempt(self, tmp_path: Path):
        """「成交额部分样本」是其他指标的措辞，不豁免资金流合计。"""
        errs = self._unlabeled(self._write(
            tmp_path, "| 510310 | 0 | 2 | 成交额部分样本 |\n"))
        assert len(errs) == 1

    @pytest.mark.parametrize("table_body", [
        "| 510310 | 0（不是部分合计） | 2 | — |\n",
        "| 510310 | 0 | 2 | 并非不可确认 |\n",
        "| 510310 | 0 | 2 | 无不可确认段 |\n",
    ])
    def test_negated_label_does_not_exempt(self, tmp_path: Path, table_body):
        assert len(self._unlabeled(self._write(tmp_path, table_body))) == 1, \
            "明确否定（不是部分合计/并非不可确认/无不可确认段）不构成限定"

    @pytest.mark.parametrize("table_body", [
        "| 510310 | 0（已确认） | 2 | — |\n",
        "| 510310 | 0 | 2 | 全窗数据已确认 |\n",
    ])
    def test_confirmed_wording_does_not_exempt(self, tmp_path: Path, table_body):
        assert len(self._unlabeled(self._write(tmp_path, table_body))) == 1, \
            "「已确认」措辞不是部分/不可确认限定（payload 明确 False）"

    @pytest.mark.parametrize("table_body", [
        "| 510310 | 0（部分区间合计） | 2 | — |\n",
        "| 510310 | 0 | 2 | 窗口不可确认 |\n",
        "| 510310 | 0（可确认段小计） | 2 | — |\n",
        "| 510310 | 0 | 2 | 全窗流量为可确认段小计 |\n",
        "| 510310 | 0 | 2 | 流量按部分区间合计 |\n",
    ])
    def test_direct_flow_or_window_qualifiers_pass(self, tmp_path: Path, table_body):
        assert self._unlabeled(self._write(tmp_path, table_body)) == []

    def test_cross_row_label_does_not_exempt_this_row(self, tmp_path: Path):
        table = ("| 510300 | -0.44 | -0.53 | 窗口不可确认 |\n"
                 "| 510310 | 0 | 2 | — |\n")
        errs = self._unlabeled(self._write(tmp_path, table))
        assert len(errs) == 1 and "510310" in errs[0]["message"], \
            "别行（跨行）的限定不豁免本行"

    def test_confirmed_rows_untouched(self, tmp_path: Path):
        table = ("| 510300 | -0.44 | -0.53 | — |\n"
                 "| 510310 | -0.81 | -4.93 | — |\n")
        confirmed = [dict(r) for r in _PEER_ROWS]
        assert self._unlabeled(self._write(tmp_path, table, rows=confirmed)) == []


class TestR6IdentityBoundaryTerms:
    """R6（2026-10-11 复查）：身份核验检查只认**完成态**声明。

    159796 现场缺口表行「| 身份和现行费率更新材料 | 原文核验边界 | …」仅描述
    核验边界/待办，不含完成声明，却被旧 regex 的裸「原文核验」误报 FAIL。
    真声明（本标的身份已核/原文核验通过/已读原文）在 unknown 仍拦；任务式
    措辞不检（未知留人工）；别处否定不豁免另一分句的肯定声明。
    """

    _UNVERIFIED = {"verification": {"status": "unknown"},
                   "labels": {"asset_type": "未知", "channel": "未知",
                              "strategy": "未知"}}

    def test_gap_table_boundary_row_is_not_a_claim(self, tmp_path: Path):
        """159796 原行：边界/影响/补证路径仅描述待核范围 → 不得报身份已核。"""
        md = (MD_BODY
              + "\n| 缺口/未核项 | 类型 | 对结论的影响 | 补证路径 |\n|---|---|---|---|\n"
              + "| 身份和现行费率更新材料 | 原文核验边界 | 历史登记不等于全部现行"
                "信息已核 | 管理人合同、招募更新和公告全量核验，另封存 |\n")
        p = _write_generation(tmp_path, md_text=md,
                              payload=_payload(identity=self._UNVERIFIED))
        assert not any(f["id"] == "etf-identity-claim"
                       for f in etf_generation_findings(p))

    @pytest.mark.parametrize("claim", [
        "本标的身份已核。",
        "原文核验通过。",
        "已完成原文核验。",
        "已读原文。",
    ])
    def test_true_completion_claims_still_error(self, tmp_path: Path, claim):
        p = _write_generation(tmp_path / claim[-4:], md_text=_ID_MD_HEAD + f"[事实] {claim}\n",
                              payload=_payload(identity=self._UNVERIFIED))
        assert any(f["id"] == "etf-identity-claim" and f["severity"] == "error"
                   for f in etf_generation_findings(p)), claim

    @pytest.mark.parametrize("text", [
        "原文核验边界：管理人合同与公告全量核验后另封存。",
        "原文核验材料已归档（未读）。",   # 名称式 + 括号内否定
        "补证路径：完成原文核验后重新封存。",  # 任务式（未知留人工）
    ])
    def test_task_or_noun_wording_is_not_a_claim(self, tmp_path: Path, text):
        p = _write_generation(tmp_path / text[-6:-2], md_text=_ID_MD_HEAD + f"[事实] {text}\n",
                              payload=_payload(identity=self._UNVERIFIED))
        assert not any(f["id"] == "etf-identity-claim"
                       for f in etf_generation_findings(p)), text

    def test_negation_elsewhere_does_not_exempt_completion_claim(self, tmp_path: Path):
        md = _ID_MD_HEAD + "[事实] 未做原文核验；原文核验通过。\n"
        p = _write_generation(tmp_path, md_text=md,
                              payload=_payload(identity=self._UNVERIFIED))
        assert any(f["id"] == "etf-identity-claim" and f["severity"] == "error"
                   for f in etf_generation_findings(p)), \
            "另一分句的否定不得豁免本分句的完成声明"
