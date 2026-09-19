"""Contract tests for reader-first insight reports."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from fixtures.collections import collection_v2_minimal


def _model(profile=None):
    from lib.insight_model import build_report_model
    return build_report_model(collection_v2_minimal(), "600176", profile)


def test_facts_are_sourced_and_derived_facts_explain_formula():
    model = _model()
    assert model["report_contract_version"] == "1.0"
    assert all(fact["source_ids"] and fact["as_of"] for fact in model["facts"])
    derived = next(fact for fact in model["facts"] if fact["id"] == "financials.ocf_to_np.latest")
    assert derived["formula"] == "n_cashflow_act/net_profit"


def test_findings_reference_facts_and_carry_association_boundary():
    model = _model()
    fact_ids = {fact["id"] for fact in model["facts"]}
    assert model["findings"]
    for finding in model["findings"]:
        assert set(finding["fact_ids"]).issubset(fact_ids)
        assert finding["association_status"] == "descriptive"
        assert "买入" not in finding["claim"]


def test_profile_only_changes_ranking_not_evidence_set():
    base = _model()
    valuation = _model({"focuses": ["valuation"]})
    assert {f["id"] for f in base["findings"]} == {f["id"] for f in valuation["findings"]}
    assert valuation["findings"][0]["id"] == "valuation-position"


def test_insufficient_data_is_explicit_not_a_completed_conclusion():
    from lib.insight_model import build_report_model
    collection = collection_v2_minimal()
    collection["dimensions"] = collection["dimensions"][:1]
    collection["summary"] = {"total": 1, "available": 1, "sources_responded": 1}
    model = build_report_model(collection, "600176")
    assert model["completion"] == "insufficient"
    assert not model["findings"]
    assert model["core_tension"]["status"] == "insufficient"


def test_markdown_and_html_consume_same_finding_ids():
    from lib.render_insight import render_insight_html, render_insight_markdown
    model = _model()
    md = render_insight_markdown(model)
    html = render_insight_html(model)
    for finding in model["findings"]:
        assert finding["claim"] in md
        assert f'evidence-{finding["id"]}' in html
    assert "不构成任何投资建议" in md
    assert 'id="dimension"' in html
    assert "字段来源单元格可悬停查看公式" in html


def test_sidecars_publish_facts_findings_and_manifest(tmp_path):
    from lib.insight_model import write_sidecars
    report = tmp_path / "sample.insight.md"
    written = write_sidecars(report, _model())
    manifest = json.loads(written["manifest"].read_text(encoding="utf-8"))
    assert manifest["facts_manifest"] == "sample.insight.facts.json"
    assert manifest["insight"] == "sample.insight.insight.json"
    assert json.loads(written["facts"].read_text(encoding="utf-8"))["facts"]


def test_shared_qc_accepts_a_complete_insight_and_rejects_missing_sidecars(tmp_path):
    from lib.render_insight import render_insight_html, render_insight_markdown
    from lib.insight_model import write_sidecars
    shared_lib = Path(__file__).resolve().parents[2] / "lib"
    sys.path.insert(0, str(shared_lib))
    try:
        from report_qc import qc_file
    finally:
        sys.path.remove(str(shared_lib))

    report = tmp_path / "600176-测试股份" / "sample.insight.md"
    report.parent.mkdir()
    model = _model()
    report.write_text(render_insight_markdown(model), encoding="utf-8")
    html = report.with_suffix(".html")
    html.write_text(render_insight_html(model), encoding="utf-8")
    write_sidecars(report, model, html_path=html)
    result = qc_file(report, fail_on="error")
    assert next(layer for layer in result.layers if layer.layer == "insight-contract").status == "pass"
    report.with_suffix(".facts.json").unlink()
    failed = qc_file(report, fail_on="error")
    assert next(layer for layer in failed.layers if layer.layer == "insight-contract").status == "fail"


def test_qc_rejects_html_that_is_not_from_same_findings(tmp_path):
    from lib.render_insight import render_insight_markdown
    from lib.insight_model import write_sidecars
    shared_lib = Path(__file__).resolve().parents[2] / "lib"
    sys.path.insert(0, str(shared_lib))
    try:
        from report_qc import qc_file
    finally:
        sys.path.remove(str(shared_lib))
    model = _model()
    report = tmp_path / "600176-测试股份" / "sample.insight.md"
    report.parent.mkdir()
    report.write_text(render_insight_markdown(model), encoding="utf-8")
    html = report.with_suffix(".html")
    html.write_text("<html>stale</html>", encoding="utf-8")
    write_sidecars(report, model, html_path=html)
    result = qc_file(report, fail_on="error")
    layer = next(layer for layer in result.layers if layer.layer == "insight-contract")
    assert layer.status == "fail"
    assert any(item["id"] == "insight-html-pair-mismatch" for item in layer.details)


# ══════════════════════════════════════════════════════════════════
# 分析合成（analysis.json 注入）：独立分区，不污染确定性引擎块
#
# 背景：项目最有价值的分析合成只被 full 消费且渲染在文末，读者拿到的
# 「阅读面」反而没有它。此处把 analysis 接进 insight，但**不改 completion**
# ——让 AI 散文把「证据不足」抬成「分析完成」会同时违反诚实性与兼容性。
# ══════════════════════════════════════════════════════════════════

_ANALYSIS_SECTION = {
    "module": "financials",
    "title": "单位盈利下滑是扩产代价还是竞争格局",
    "facts_md": "综合毛利率 25.02% → 23.93%。",
    "analysis_md": "**结论：** 判别变量是三季报境内毛利率。",
    "evidence_tag": "B",
    "position": "financials",
}


def _analysis(**overrides):
    return [{**_ANALYSIS_SECTION, **overrides}]


def test_synthesis_absent_without_analysis_and_contract_still_passes():
    from lib.insight_model import validate_insight

    model = _model()
    assert model["synthesis"] == {"status": "absent", "source": None,
                                  "section_count": 0, "sections": []}
    assert validate_insight(model) == []


def test_injection_does_not_change_deterministic_blocks():
    """注入分析合成不得改变任何引擎块——completion 与 synthesis 是两条独立状态轴。"""
    from lib.insight_model import build_report_model

    coll = collection_v2_minimal()
    base = build_report_model(coll, "600176")
    injected = build_report_model(coll, "600176", analysis=_analysis())
    for key in ("facts", "findings", "core_tension", "analysis_chains",
                "discoveries", "completion", "gaps"):
        assert json.dumps(base[key], sort_keys=True, ensure_ascii=False) == \
            json.dumps(injected[key], sort_keys=True, ensure_ascii=False), key


def test_invalid_payload_raises_insight_schema_error():
    import pytest
    from lib.insight_model import InsightSchemaError, build_report_model

    coll = collection_v2_minimal()
    with pytest.raises(InsightSchemaError):
        build_report_model(coll, "600176", analysis=[{"module": "x"}])
    with pytest.raises(InsightSchemaError):
        # 围栏代码不属 md 子集 → analysis_schema 拒绝，不得静默降级成 absent
        build_report_model(coll, "600176", analysis=_analysis(analysis_md="```\ncode\n```"))


def test_validate_synthesis_rejects_inconsistent_blocks():
    from lib.insight_model import _validate_synthesis

    assert _validate_synthesis(None) == []
    assert _validate_synthesis({"status": "wat", "sections": []}) != []
    assert _validate_synthesis({"status": "absent", "sections": [{}]}) != []
    assert _validate_synthesis({"status": "injected", "sections": [],
                                "section_count": 0}) != []
    assert _validate_synthesis({"status": "injected", "section_count": 2,
                                "sections": [_ANALYSIS_SECTION]}) != []
    assert _validate_synthesis({"status": "injected", "section_count": 1,
                                "sections": [{**_ANALYSIS_SECTION, "title": ""}]}) != []


def test_md_and_html_render_same_synthesis_sections_in_order():
    from lib.insight_model import build_report_model
    from lib.render_insight import render_insight_html, render_insight_markdown

    model = build_report_model(collection_v2_minimal(), "600176",
                               analysis=_analysis())
    md = render_insight_markdown(model)
    html = render_insight_html(model)
    for text in (md, html):
        assert _ANALYSIS_SECTION["title"] in text
        assert "未经引擎来源校验" in text   # provenance 横幅
    # 价值内容不得落到文末，且两格式同序：核心矛盾 → 分析合成 → 本次新增发现
    assert (md.index("## 核心矛盾") < md.index("## 分析合成（Claude 撰写）")
            < md.index("## 本次新增发现"))
    assert (html.index("核心矛盾") < html.index('id="synthesis"')
            < html.index("本次新增发现"))


def test_synthesis_status_is_shown_as_an_independent_axis():
    from lib.insight_model import build_report_model
    from lib.render_insight import render_insight_markdown

    absent = render_insight_markdown(_model())
    assert "分析合成：** 未注入（仅引擎结论）" in absent
    injected = render_insight_markdown(
        build_report_model(collection_v2_minimal(), "600176", analysis=_analysis()))
    assert "分析合成：** 已注入（1 段）" in injected

    def completion_field(text):
        line = next(l for l in text.splitlines() if "产物状态" in l)
        return line.split("｜")[0]

    # 同一份 collection：注入前后 completion 显示必须一致（两条状态轴独立，
    # AI 散文不得把「证据不足」抬成「分析完成」）
    assert completion_field(absent) == completion_field(injected)


def test_synthesis_titles_are_escaped_in_html():
    from lib.insight_model import build_report_model
    from lib.render_insight import render_insight_html

    model = build_report_model(collection_v2_minimal(), "600176",
                               analysis=_analysis(title="<b>x</b>"))
    html = render_insight_html(model)
    assert "&lt;b&gt;x&lt;/b&gt;" in html
    assert "<b>x</b>" not in html


def test_sidecars_bind_analysis_path_in_manifest(tmp_path):
    from lib.insight_model import write_sidecars

    report = tmp_path / "sample.insight.md"
    sidecar = tmp_path / "sample.insight.analysis.json"
    sidecar.write_text("[]", encoding="utf-8")
    written = write_sidecars(report, _model(), analysis_path=sidecar)
    manifest = json.loads(written["manifest"].read_text(encoding="utf-8"))
    assert manifest["analysis_sidecar"] == "sample.insight.analysis.json"
    assert json.loads(written["insight"].read_text(encoding="utf-8"))["synthesis"] == {
        "status": "absent", "source": None, "section_count": 0, "sections": []}
    assert written["analysis"] == sidecar


def test_qc_flags_injected_synthesis_without_same_generation_sidecar(tmp_path):
    from lib.insight_model import build_report_model, write_sidecars
    from lib.render_insight import render_insight_html, render_insight_markdown
    shared_lib = Path(__file__).resolve().parents[2] / "lib"
    sys.path.insert(0, str(shared_lib))
    try:
        from report_qc import qc_file
    finally:
        sys.path.remove(str(shared_lib))

    report = tmp_path / "600176-测试股份" / "sample.insight.md"
    report.parent.mkdir()
    model = build_report_model(collection_v2_minimal(), "600176", analysis=_analysis())
    report.write_text(render_insight_markdown(model), encoding="utf-8")
    html = report.with_suffix(".html")
    html.write_text(render_insight_html(model), encoding="utf-8")
    sidecar = report.with_suffix(".analysis.json")
    sidecar.write_text(json.dumps(_analysis(), ensure_ascii=False), encoding="utf-8")
    write_sidecars(report, model, html_path=html, analysis_path=sidecar)

    def contract_layer(path):
        return next(layer for layer in qc_file(path, fail_on="error").layers
                    if layer.layer == "insight-contract")

    assert contract_layer(report).status == "pass"

    sidecar.unlink()
    layer = contract_layer(report)
    assert layer.status == "fail"
    assert any(item["id"] == "insight-analysis-sidecar-missing" for item in layer.details)


def test_qc_ignores_legacy_insight_without_synthesis_key(tmp_path):
    """旧产物（.insight.json 无 synthesis 键）不得因新规则被误判。"""
    from lib.insight_model import write_sidecars
    from lib.render_insight import render_insight_markdown
    shared_lib = Path(__file__).resolve().parents[2] / "lib"
    sys.path.insert(0, str(shared_lib))
    try:
        from report_qc import qc_file
    finally:
        sys.path.remove(str(shared_lib))

    report = tmp_path / "600176-测试股份" / "legacy.insight.md"
    report.parent.mkdir()
    model = _model()
    report.write_text(render_insight_markdown(model), encoding="utf-8")
    written = write_sidecars(report, model)
    payload = json.loads(written["insight"].read_text(encoding="utf-8"))
    payload.pop("synthesis", None)
    written["insight"].write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    layer = next(layer for layer in qc_file(report, fail_on="error").layers
                 if layer.layer == "insight-contract")
    assert not any(str(item["id"]).startswith("insight-synthesis")
                   or "analysis-sidecar" in str(item["id"]) for item in layer.details)
