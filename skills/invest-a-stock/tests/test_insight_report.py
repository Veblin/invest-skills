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
