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
             identity: dict | None = None) -> dict:
    payload = {
        "skill": "invest-a-etf",
        "symbol": symbol,
        "generated_at": "2026-10-08T10:00:00+08:00",
        "profile": {
            "tracking": tracking or {"status": "limited", "note": "缺总收益基准"},
            "identity": identity or {
                "axes": {"asset_type": "stock", "channel": "domestic",
                         "strategy": "broad_market"},
                "verification": {"status": "verified_original"},
            },
        },
        "holdings": holdings or {},
        "data_status": {"dimensions": {}},
    }
    if index_exposure is not None:
        payload["index_exposure"] = index_exposure
    snapshot_core.seal(
        payload, version_field="payload_version", version=1,
        extra={"payload_version": 1, "symbol": symbol,
               "generated_at": payload["generated_at"],
               "frozen_at": payload["generated_at"]})
    return payload


def _write_generation(tmp_path: Path, *, md_text: str = MD_BODY,
                      payload: dict | None = None, html_extra: str = "",
                      ts: str = "2026-10-08-10-00-00") -> Path:
    d = tmp_path / "510300-沪深300ETF"
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
