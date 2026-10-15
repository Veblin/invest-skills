"""R1-A 回归：report_qc sourcing 层（F2 派生表述来源 / F4 §N 引用存在性）。

T6-2/T6-3（v0.3.0 数据可信轮）：warning 语义（人工复核），不破 D1=A（lint 只做 error 级 F3）。
"""

import json
import sys
from pathlib import Path

# 只加 skills/lib（R1 审查 F12：再加 skills/ 会把 skills/lib 暴露为顶层包 lib，
# 污染 pytest 同批其他用例的 lib.* 解析——路径污染型顺序依赖）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from report_qc import _check_sourcing, qc_file, write_qc_record  # noqa: E402
import snapshot_core  # noqa: E402


def test_f2_derived_claim_with_source_passes():
    text = "估值较行业高 1.5 倍，源于份额增长 [来源: Python calc: a/b]"
    layer = _check_sourcing(text)
    assert layer.status == "pass" and layer.findings_count == 0


def test_f2_derived_claim_without_source_warns():
    text = "竞争格局方面，龙头市占率约 3 倍于次席（同行平均口径）。"
    layer = _check_sourcing(text)
    assert layer.status == "warn"
    ids = [d["id"] for d in layer.details]
    assert "f2-derived-claim-no-source" in ids
    assert layer.details[0]["line"] == 1


def test_f2_source_in_previous_lines_counts():
    text = "[来源: 引擎字段 x]\n加工：差距约 2.3 个百分点。"
    layer = _check_sourcing(text)
    assert layer.findings_count == 0     # 前 3 行窗口内有来源


def test_f4_section_ref_diff():
    text = "# 报告\n\n见 §5 与 §3.2 的对照。\n\n## 3 情景\n\n## 5 结论\n"
    layer = _check_sourcing(text)
    ids = [(d["id"], d["message"]) for d in layer.details]
    assert len(ids) == 1 and ids[0][0] == "f4-section-ref-missing" and "§3.2" in ids[0][1]


def test_f4_no_refs_no_findings():
    assert _check_sourcing(" ## 1 甲\n\n## 2 乙\n").findings_count == 0


def test_qc_file_overall_warn_not_fail(tmp_path):
    """sourcing 命中 → QCResult WARN（不 FAIL）；挂载对 etf 类型生效。"""
    report = tmp_path / "516160-测试ETF" / "2026-09-10-10-00-00.md"
    report.parent.mkdir(parents=True)
    report.write_text(
        "# 测试ETF\n\n估算折溢价约 1.2 个百分点。\n\n见 §9 不存在节。\n",
        encoding="utf-8",
    )
    r = qc_file(report)
    assert r.report_type == "etf"
    sourcing = [l for l in r.layers if l.layer == "sourcing"]
    assert sourcing and sourcing[0].status == "warn"
    assert r.overall in ("WARN", "FAIL")  # FAIL 仅可能来自 lint 层既有规则；sourcing 自身不产 error


def test_qc_file_unknown_type_still_gets_sourcing(tmp_path):
    """F13 修正：unknown 类型（新技能产出）同样挂 sourcing——否则「必跑」形同虚设。"""
    report = tmp_path / "随手笔记.md"
    report.write_text("利润是去年的 2 倍。", encoding="utf-8")
    r = qc_file(report)
    sourcing = [l for l in r.layers if l.layer == "sourcing"]
    assert sourcing and sourcing[0].status == "warn"


def test_f4_external_spec_citations_ignored():
    """F4 豁免：指向外部规范（conventions.md §N）的引用不报「本文缺节」。"""
    text = ("# 报告\n\n遵循共享规范 report-conventions.md §2.3 的口径。\n\n"
            "另见 §9 不存在节。\n\n## 1 概况\n")
    layer = _check_sourcing(text)
    msgs = [d["message"] for d in layer.details]
    assert len(msgs) == 1 and "§9" in msgs[0]           # 仅本文 §9 命中
    assert all("§2.3" not in m for m in msgs)


def test_cli_verbose_prints_details(tmp_path, capsys):
    """附带修复：CLI -v 此前解析后从未传递（detail 行永不输出）。"""
    from report_qc import main

    report = tmp_path / "516160-x" / "2026-09-10-10-00-00.md"
    report.parent.mkdir(parents=True)
    report.write_text("差距约 2.3 个百分点。\n", encoding="utf-8")
    rc = main([str(report), "-v"])
    out = capsys.readouterr().out
    assert "f2-derived-claim-no-source" in out      # verbose 生效：detail 可见
    assert rc in (0, 1, 2)


# T6-5 端到端正例（固化 2026-09-10 人工构造样例的全层 PASS 行为，
# 防 sourcing/结构/lint 任一层在未来误伤合规输入）
#
# v0.3.0 A3：新增 readability / conclusion-evidence 两层后，本样例须继续满足
# 全层 PASS——正例就该连 §3.4 的软指标（长句占比 ≤30%）也满足。句子已按
# 中文句末符断为短句（原样例整段无句末符 → 单句 100% 长句占比 → WARN，
# 属该指标的正确触发而非误伤；R-A1 是软建议，WARN 仍可交付）。
_POSITIVE_SAMPLE = """# 样例ETF 研究报告（正例样例）

> 本报告为研究工具输出，不构成投资建议。

## 1 概况

[事实] 最新净值 1.0000 [来源: 引擎字段 nav]。跟踪指数 PE 12.34x [来源: 引擎字段 index_pe]。

[分析] 折溢价约 1.2 个百分点，处于常态区间 [来源: Python calc: (price-nav)/nav]。

[证据强度: ✅ 强 🌐 多源 🕐 近 30 日 ✓✓ 跨源一致]
"""


def test_positive_sample_sourcing_layer_pass_payload_warn_explicit(tmp_path):
    """sourcing 正例只校验**目标层**通过；缺同代 payload 的 WARN 显式断言。

    v0.3.2 B06 起 ETF 类型挂 etf-generation 层：无 payload 属「未执行 ≠ PASS」
    的 warning（历史样例库大量无 payload，不能一概强转 PASS，也不能删门禁）；
    整体 PASS 的真实 generation 正例见下一条。
    """
    report = tmp_path / "510300-样例ETF" / "2026-09-10-10-00-00.md"
    report.parent.mkdir(parents=True)
    report.write_text(_POSITIVE_SAMPLE, encoding="utf-8")
    r = qc_file(report, fail_on="error")
    layers = {l.layer: l for l in r.layers}
    assert layers["sourcing"].status == "pass", layers["sourcing"].details
    # 无 payload → etf-generation 层必须显式 WARN（缺 payload 未执行 ≠ PASS）
    gen = layers["etf-generation"]
    assert gen.status == "warn", gen.details
    assert any(d["id"] == "etf-payload-missing" and d["severity"] == "warning"
               for d in gen.details)
    assert r.overall == "WARN"


def _sealed_payload(symbol: str) -> dict:
    payload = {
        "skill": "invest-a-etf",
        "symbol": symbol,
        "generated_at": "2026-09-10T10:00:00+08:00",
        "profile": {
            "tracking": {"status": "limited", "note": "缺总收益基准"},
            "identity": {"axes": {"asset_type": "stock", "channel": "domestic",
                                  "strategy": "broad_market"},
                         "verification": {"status": "verified_original"}},
        },
        "holdings": {},
        "data_status": {"dimensions": {}},
    }
    snapshot_core.seal(payload, version_field="payload_version", version=1,
                       extra={"payload_version": 1, "symbol": symbol,
                              "generated_at": payload["generated_at"],
                              "frozen_at": payload["generated_at"]})
    return payload


def test_full_generation_with_payload_overall_pass(tmp_path):
    """完整同代产物（md+payload+html+QC 记录）真实 generation → 整体 PASS。"""
    d = tmp_path / "510300-样例ETF"
    d.mkdir(parents=True)
    ts = "2026-09-10-10-00-00"
    md = d / f"{ts}.md"
    md.write_text(_POSITIVE_SAMPLE, encoding="utf-8")
    payload = _sealed_payload("510300")
    (d / f"{ts}.payload.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (d / f"{ts}.html").write_text(
        "<html><head>"
        f'<meta name="invest-generation" content="{payload["_meta"]["payload_hash"]}">'
        "</head><body>净值 1.0000</body></html>", encoding="utf-8")
    r = qc_file(md, fail_on="error")
    assert r.overall == "WARN"          # 记录未写：etf-qc-record-missing（未执行）
    write_qc_record(md, r)              # 记录本代 md/html/payload 实际哈希
    r2 = qc_file(md, fail_on="error")
    assert r2.overall == "PASS", [(l.layer, l.status, l.details) for l in r2.layers]
