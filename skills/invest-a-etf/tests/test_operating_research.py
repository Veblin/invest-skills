"""经营研究真实 report→seal→HTML→QC 合同；所有原料在 tmp，禁止外呼。"""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

from etf_generation import seal_etf_payload, verify_etf_payload
from etf_html import render_etf_html
from etf_research import (attach_research, load_research_input, research_findings,
                          validate_research)


@pytest.fixture
def prepared(tmp_path):
    content = "公司财报正文：需求增长，但现金回款仍待观察。"
    (tmp_path / "original.txt").write_text(content)
    sha = hashlib.sha256(content.encode()).hexdigest()
    r = {
        "schema_version": 1, "symbol": "588000", "as_of": "2026-10-12",
        "holdings_as_of": "2026-06-30", "question": "需求是否转化为经营改善？",
        "budget": {"max_names": 2, "target_cov_pct": 30},
        "selection": [{"code": "688008", "reason": "接口环节与需求兑现"},
                      {"code": "688012", "reason": "设备环节竞争解释"}],
        "sources": [{"id": "S1", "url": "https://example.com/company.pdf",
                     "title": "公司原始披露", "source_type": "original", "sha256": sha,
                     "text_sha256": sha, "path": "original.txt", "text_path": "original.txt",
                     "locator": "物理第1页经营章节", "excerpt": content,
                     "published_date": "2026-08-28", "read_at": "2026-10-12T10:00:00+08:00"}],
        "facts": [{"id": "F1", "kind": "company", "code": "688008", "statement": "公司披露需求增长。",
                   "source_ids": ["S1"], "period_start": "2026-01-01", "period_end": "2026-06-30"},
                  {"id": "F2", "kind": "event", "statement": "后续行业变化待跟踪。",
                   "source_ids": ["S1"], "period_start": "2026-07-01", "period_end": "2026-07-01"},
                  {"id": "F3", "kind": "company", "code": "688012", "statement": "设备环节尚待验证。",
                   "source_ids": ["S1"], "period_start": "2026-01-01", "period_end": "2026-06-30"}],
        "insights": [{"id": "I1", "title": "需求与兑现", "fact_ids": ["F1", "F2"],
                      "codes": ["688008", "688012"], "scope": "selected_holdings",
                      "interpretation": "收入变化支持需求解释，未证明持续性。",
                      "transmission": "后续行业变化是待检验条件，不能解释此前业绩。",
                      "etf_link": "两环节可能产生不同影响。",
                      "conditional_conclusion": "若后续回款同步改善，解释才增强。",
                      "alternative": "也可能是备货前置。", "falsifier": "后续收入与回款背离。",
                      "boundary": "所选样本未代表全组合；当前估值假设未知。"}],
    }
    p = tmp_path / "research.json"
    p.write_text(json.dumps(r, ensure_ascii=False))
    return r, p


def payload():
    return {"symbol": "588000", "generated_at": "2026-10-12T11:00:00+08:00", "profile": {},
            "quote": {}, "kline": {}, "share_history": {}, "history": None,
            "events": {}, "playbook": None,
            "holdings": {"status": "ok", "report_date": "2026-06-30",
                         "rows": [{"code": "688008", "name": "接口", "pct": 8.13},
                                  {"code": "688012", "name": "设备", "pct": 8},
                                  {"code": "688256", "name": "未选", "pct": 9.3}],
                         "top10_sum_pct": 25.43, "holding_scope": "前十大，非全组合"}}


def attach(prepared):
    _, path = prepared
    p = payload()
    calls = []
    def fetch(code):
        calls.append(code)
        return {"status": "ok", "source": "offline-marker", "attempted_sources": ["stub"],
                "rows": [{"end_date": "20260630", "ann_date": "20260801", "revenue": 10},
                         {"end_date": "2026-06-30", "ann_date": "20260828", "revenue": 20},
                         {"end_date": "20260630", "ann_date": "20261020", "revenue": 999},
                         {"end_date": "20260331", "ann_date": None, "revenue": 888}]}
    r = attach_research(p, load_research_input(path, "588000"), financial_getter=fetch)
    return p, r, calls


def test_full_readonly_consumption_and_cutoff(prepared):
    p, r, calls = attach(prepared)
    assert calls == ["688008", "688012"]
    assert r["coverage"]["weight_pct"] == 16.13
    assert [x["values"]["revenue"] for x in r["financial_observations"]] == [20, 20]
    assert r["financials"]["688008"]["source"] == "offline-marker"
    assert p["data_status"]["dimensions"]["holdings_evidence"]["status"] == "available"
    assert p["holdings_evidence"]["budget"] == r["selection_budget"]
    assert "披露耗尽" not in (p["holdings_evidence"]["shortfall_note"] or "") or "不能误称" in p["holdings_evidence"]["shortfall_note"]
    assert len(r["financials"]["688008"]["rows"]) == 4  # 原始冲突/未知未删除
    assert "path" not in r["sources"][0]
    md = r["narrative_md"] + "\n\n" + r["appendix_md"]
    assert research_findings(p, md) == []
    seal_etf_payload(p)
    assert verify_etf_payload(p) == []
    html = render_etf_html(p, md_text=md)
    assert 'data-operating-research="1"' in html and "竞争解释" in html
    assert "16.13%" in html and "全组合" in html
    assert "持仓报告期末 2026-06-30" in md and "持仓报告期末 2026-06-30" in html
    assert "2026-06-30 披露" not in md and "2026-06-30 披露" not in html
    p["research"]["facts"][0]["statement"] = "篡改"
    assert verify_etf_payload(p)


@pytest.mark.parametrize("mutation", [
    lambda r: r["sources"][0].pop("excerpt"),
    lambda r: r["sources"][0].pop("read_at"),
    lambda r: r["sources"][0].pop("text_sha256"),
    lambda r: r["sources"][0].update(read_at="2026-08-01T00:00:00+08:00"),
    lambda r: r["sources"][0].update(published_date="2026-10-13"),
    lambda r: r["facts"][0].update(period_end="2026-12-31"),
    lambda r: r["facts"][0].update(source_ids=["UNKNOWN"]),
    lambda r: r["insights"][0].update(scope="whole_etf"),
    lambda r: r["insights"][0].update(fact_ids=["UNKNOWN"]),
    lambda r: r["insights"][0].pop("alternative"),
    lambda r: r["insights"][0].update(historical_links=[{"cause": "F2", "effect": "F1"}]),
    lambda r: r["insights"][0].update(comparisons=[{"left": "F1", "right": "F2"}]),
    lambda r: r.update(budget={"max_names": 1, "target_cov_pct": 30}),
    lambda r: r.update(budget={"max_names": 2, "target_cov_pct": float("nan")}),
    lambda r: r.update(sources=[None]),
    lambda r: r.update(selection=[None]),
    lambda r: r.update(insights=None),
])
def test_counterexamples_rejected(prepared, mutation):
    r, _ = prepared
    mutation(r)
    assert validate_research(r)


@pytest.mark.parametrize("mutation", [
    lambda r: r["sources"][0].update(path="absent.pdf"),
    lambda r: r["sources"][0].update(sha256="0" * 64),
    lambda r: r["sources"][0].update(text_sha256="0" * 64),
    lambda r: r["sources"][0].update(excerpt="不存在的摘要"),
    lambda r: r.update(symbol="515050"),
])
def test_metadata_is_not_read_evidence(prepared, mutation):
    r, path = prepared
    mutation(r)
    path.write_text(json.dumps(r))
    with pytest.raises(ValueError):
        load_research_input(path, "588000")


@pytest.mark.parametrize("mutation", [
    lambda p: p["holdings"].update(report_date="2026-03-31"),
    lambda p: p["holdings"]["rows"][0].update(pct=None),
    lambda p: p["holdings"]["rows"][0].update(pct=float("inf")),
    lambda p: p["holdings"]["rows"][0].update(code="123456"),
])
def test_holdings_contract_before_financial_request(prepared, mutation):
    _, path = prepared
    p = payload()
    mutation(p)
    with pytest.raises(ValueError):
        attach_research(p, load_research_input(path, "588000"),
                        financial_getter=lambda c: pytest.fail("财务腿不得发起"))


def test_qc_catches_missing_text_and_false_coverage(prepared):
    p, r, _ = attach(prepared)
    md = r["narrative_md"] + "\n\n" + r["appendix_md"]
    assert research_findings(p, md.replace("16.13%", "100.00%"))
    assert research_findings(p, r["narrative_md"])
    bad = copy.deepcopy(p)
    bad["research"]["coverage"]["weight_pct"] = 100
    assert research_findings(bad, md)
    assert research_findings({"research": []}, md)
    assert research_findings({}, "旧报告") == []


def test_financial_failure_remains_visible(prepared):
    _, path = prepared
    def fetch(c):
        raise TimeoutError("opaque upstream")
    r = attach_research(payload(), load_research_input(path, "588000"), financial_getter=fetch)
    assert r["financial_observations"] == []
    assert r["financials"]["688008"]["status"] == "missing"
    assert "TimeoutError" in r["financials"]["688008"]["note"]


def test_standard_financial_bridge_envelope(prepared):
    _, path = prepared
    r = attach_research(payload(), load_research_input(path, "588000"), financial_getter=lambda c: {
        "status": "available", "data": [{"end_date": "20260630", "ann_date": "20260828", "revenue": 123}],
        "_meta": {"source": "standard-envelope-marker", "query_params": "offline"}})
    assert len(r["financial_observations"]) == 2
    assert all(x["source"] == "standard-envelope-marker" for x in r["financial_observations"])
    assert "123.00" in r["appendix_md"]


def test_source_date_uses_shanghai_day(prepared):
    r, _ = prepared
    r["sources"][0].update(published_date="2026-08-29", read_at="2026-08-28T17:00:00+00:00")
    assert validate_research(r) == []


def test_actual_qc_consumer_and_html_missing_scope(prepared, tmp_path):
    import report_qc
    p, r, _ = attach(prepared)
    seal_etf_payload(p)
    d = tmp_path / "588000-经营测试"
    d.mkdir()
    md = d / "2026-10-12-11-00-00.md"
    body = r["narrative_md"] + "\n\n" + r["appendix_md"]
    md.write_text(body)
    md.with_suffix(".payload.json").write_text(json.dumps(p))
    html = render_etf_html(p, md_text=body, generation={"payload_hash": p["_meta"]["payload_hash"]})
    md.with_suffix(".html").write_text(html)
    findings = report_qc.etf_generation_findings(md)
    assert not [f for f in findings if f["id"].startswith("etf-operating-research")]
    md.write_text(body.replace("16.13%", "100.00%"))
    assert any(f["id"] == "etf-operating-research" for f in report_qc.etf_generation_findings(md))
    md.write_text(body)
    md.with_suffix(".html").write_text(html.replace('data-operating-research="1"', ""))
    assert any(f["id"] == "etf-operating-research-html" for f in report_qc.etf_generation_findings(md))


@pytest.mark.parametrize("record_refresh", [False, True])
@pytest.mark.parametrize("old,new", [
    ("所选证券覆盖 16.13%", "所选证券覆盖 100%"),
    ("研究问题：需求是否转化为经营改善？", "研究问题：经营改善已获确认。"),
    ("持仓期末 2026-06-30", "持仓期末 2026-08-28"),
    ("非当前完整持仓，不推为全组合盈利或预期收益。", "可代表全组合盈利。"),
    ("收入变化支持需求解释，未证明持续性。", "需求持续改善已获确认。"),
    ("所选样本未代表全组合；当前估值假设未知。", "所选样本代表全组合。"),
    ("也可能是备货前置。", "不存在竞争解释。"),
    ("公司披露需求增长。", "公司披露需求下降。"),
])
def test_qc_rejects_html_content_tampering(prepared, tmp_path, record_refresh, old, new):
    import report_qc
    p, r, _ = attach(prepared)
    seal_etf_payload(p)
    md = tmp_path / "2026-10-12-11-00-00.md"
    body = r["narrative_md"] + "\n\n" + r["appendix_md"]
    md.write_text(body)
    md.with_suffix(".payload.json").write_text(json.dumps(p))
    html = render_etf_html(p, md_text=body, generation={"payload_hash": p["_meta"]["payload_hash"]})
    assert old in html
    md.with_suffix(".html").write_text(html)
    assert not [f for f in report_qc.etf_generation_findings(md, record_refresh=record_refresh)
                if f["id"].startswith("etf-operating-research")]
    md.with_suffix(".html").write_text(html.replace(old, new))
    findings = report_qc.etf_generation_findings(md, record_refresh=record_refresh)
    assert any(f["id"] == "etf-operating-research-html" and f["severity"] == "error"
               for f in findings)


@pytest.mark.parametrize("mode", ["comment", "duplicate", "hidden", "template", "textarea", "iframe",
                                  "hidden_ancestor", "styled_ancestor"])
def test_html_research_copy_cannot_mask_wrong_visible_content(prepared, mode):
    from etf_research import research_html_findings
    p, r, _ = attach(prepared)
    body = r["narrative_md"] + "\n\n" + r["appendix_md"]
    html = render_etf_html(p, md_text=body)
    section = '<section id="research">' + html.split('<section id="research">', 1)[1].split("</section>", 1)[0] + "</section>"
    bad_section = section.replace("所选证券覆盖 16.13%", "所选证券覆盖 100%")
    if mode == "comment":
        replacement = "<!--" + section + "-->" + bad_section
    elif mode == "duplicate":
        replacement = section + bad_section
    elif mode == "hidden":
        replacement = section.replace('data-operating-research="1"', 'data-operating-research="1" hidden')
    else:
        visible_false = bad_section.replace('id="research"', 'id="research-visible"')
        tag = {"template": "template", "textarea": "textarea", "iframe": "iframe",
               "hidden_ancestor": "div hidden",
               "styled_ancestor": 'div style="display:none"'}[mode]
        replacement = f"<{tag}>" + section + f"</{tag.split()[0]}>" + visible_false
    assert research_html_findings(p, html.replace(section, replacement), body)


def test_cli_forwards_research_and_rejects_before_collection(prepared, monkeypatch, capsys, tmp_path):
    scripts = Path(__file__).resolve().parents[1] / "scripts"
    spec = importlib.util.spec_from_file_location("operating_cli", scripts / "etf.py")
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    _, path = prepared
    import etf_research
    hits = []
    monkeypatch.setattr(cli, "_collect_report_payload", lambda *a, **kw: payload())
    def fake_attach(p, r):
        hits.append(r["symbol"])
        return attach_research(p, r, financial_getter=lambda c: {"status": "missing", "rows": []})
    monkeypatch.setattr(etf_research, "attach_research", fake_attach)
    report_dir = tmp_path / "588000-经营研究ETF"
    report_dir.mkdir()
    out = report_dir / "2026-10-12-11-00-00.payload.json"
    assert cli.main(["report", "588000", "--json", "--research", str(path),
                     "--payload-out", str(out)]) == 0
    frozen = json.loads(out.read_text())
    assert hits == ["588000"] and verify_etf_payload(frozen) == []
    # 实际 CLI 产物：report --research → seal → html --payload → QC --record。
    import report_qc
    md = out.with_suffix("").with_suffix(".md")
    body = ("# ETF 经营研究（588000）\n\n> 不构成投资建议。\n\n"
            + frozen["research"]["narrative_md"] + "\n\n"
            + frozen["research"]["appendix_md"] + "\n\n> 不构成投资建议。\n")
    md.write_text(body)
    assert cli.main(["html", "588000", "--md", str(md), "--payload", str(out), "--no-open"]) == 0
    html_path = md.with_suffix(".html")
    html = html_path.read_text()
    assert "持仓报告期末 2026-06-30" in body and "持仓报告期末 2026-06-30" in html
    assert not [f for f in report_qc.etf_generation_findings(md, record_refresh=True)
                if f["id"].startswith("etf-operating-research")]
    html_path.write_text(html.replace("所选证券覆盖 16.13%", "所选证券覆盖 100%"))
    assert report_qc.main([str(md), "--record", "--fail-on", "error", "--json"]) != 0
    record = json.loads(md.with_suffix(".qc.json").read_text())
    assert record["machine_qc"]["overall"] == "FAIL"
    research_layer = next(x for x in record["machine_qc"]["layers"] if x["layer"] == "etf-generation")
    assert research_layer["status"] == "fail"
    assert cli.main(["report", "588000", "--json", "--research", str(path)]) == 2
    hits.clear()
    assert cli.main(["report", "588000", "--json"]) == 0
    assert hits == []
    capsys.readouterr()
