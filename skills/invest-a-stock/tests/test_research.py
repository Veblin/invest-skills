"""机构研报维度：采集汇总、渲染与 LAW 6 合规。"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from conftest import FORBIDDEN_SIGNAL_WORDS


def _analysis_body_without_legal_disclaimers(text: str) -> str:
    kept: list[str] = []
    for line in text.splitlines():
        if line.startswith(">") and any(
            p in line for p in ("不构成", "免责声明", "风险提示", "非交易信号")
        ):
            continue
        kept.append(line)
    return "\n".join(kept)


class TestSummarizeResearch:
    def test_report_rc_full_summary(self):
        from lib.collector import _summarize_research

        rc = [
            {"report_date": "20260101", "org_name": "A证券", "rating": "买入",
             "max_price": 50.0, "min_price": 45.0, "eps": 2.5, "quarter": "2026Q1", "np": 100000},
            {"report_date": "20251201", "org_name": "B证券", "rating": "增持",
             "max_price": 48.0, "min_price": 42.0, "eps": 2.3, "quarter": "2026Q1", "np": 95000},
        ]
        summary = _summarize_research(rc, None, None)
        assert summary["status"] == "ok"
        assert summary["target_price_range"]["min"] == 42.0
        assert summary["target_price_range"]["max"] == 50.0
        assert summary["target_price_range"]["avg_upper"] == 49.0
        assert "买入" not in summary["summary_text"]
        assert "目标价" not in summary["summary_text"]

    def test_report_rc_only_max_prices(self):
        from lib.collector import _summarize_research

        rc = [{"report_date": "20260101", "rating": "买入", "max_price": 55.0}]
        summary = _summarize_research(rc, None, None)
        assert summary["target_price_range"] == {"min": 55.0, "max": 55.0, "avg_upper": 55.0}

    def test_forecast_uses_profit_fields(self):
        from lib.collector import _summarize_research

        fc = [{
            "end_date": "20261231", "type": "预增",
            "p_change_min": 10.0, "p_change_max": 20.0,
            "profit_min": 50000, "profit_max": 60000,
        }]
        summary = _summarize_research(None, fc, None)
        assert summary["status"] == "ok_guidance_only"
        g = summary["company_guidance"]
        assert g["profit_min_100m"] == 5.0
        assert g["profit_max_100m"] == 6.0

    def test_forecast_fallback_derived_profit(self):
        from lib.collector import _summarize_research

        fc = [{
            "end_date": "20261231", "type": "预增",
            "p_change_min": 10.0, "p_change_max": 20.0,
            "last_parent_net": 100000,
        }]
        summary = _summarize_research(None, fc, None)
        g = summary["company_guidance"]
        assert g["profit_min_100m"] == 11.0
        assert g["profit_max_100m"] == 12.0

    def test_akshare_limited(self):
        from lib.collector import _summarize_research

        ak = [{"title": "研报1"}, {"title": "研报2"}]
        summary = _summarize_research(None, None, ak)
        assert summary["status"] == "ok_limited"
        assert "2 条" in summary["summary_text"]

    def test_no_data(self):
        from lib.collector import _summarize_research

        summary = _summarize_research(None, None, None)
        assert summary["status"] == "no_data"


class TestAggregateSellsidePriceRange:
    def test_nan_ignored(self):
        from lib.collector import _aggregate_sellside_price_range

        result = _aggregate_sellside_price_range([(float("nan"), 40.0), (50.0, None)])
        assert result is not None
        assert result["min"] == 40.0
        assert result["max"] == 50.0


class TestCollectResearchShortCircuit:
    def test_skips_lower_tiers_when_report_rc_ok(self):
        from lib.collector import collect_research

        with patch("lib.collector._q_tushare_report_rc", return_value=[{"rating": "买入"}]):
            with patch("lib.collector._q_tushare_forecast") as mock_fc:
                with patch("lib.collector._q_akshare_research") as mock_ak:
                    dim = collect_research("600176")
                    mock_fc.assert_not_called()
                    mock_ak.assert_not_called()
        sources = [s["source"] for s in dim["_meta"]["all_sources"]]
        assert sources == ["tushare.report_rc"]

    def test_forecast_only_when_report_rc_fails(self):
        from lib.collector import collect_research

        with patch("lib.collector._q_tushare_report_rc", return_value=None):
            with patch("lib.collector._q_tushare_forecast", return_value=[{"type": "预增"}]):
                with patch("lib.collector._q_akshare_research") as mock_ak:
                    dim = collect_research("600176")
                    mock_ak.assert_not_called()
        sources = [s["source"] for s in dim["_meta"]["all_sources"]]
        assert sources == ["tushare.report_rc", "tushare.forecast"]


def _collection_with_research(summary: dict) -> dict:
    return {
        "symbol": "600176",
        "fetched_at": "2026-06-17T00:00:00Z",
        "dimensions": [
            {
                "dimension": "research",
                "display": "机构研报",
                "data": [],
                "status": "partial",
                "research_summary": summary,
                "_meta": {"source": "tushare.report_rc"},
            },
        ],
    }


class TestSectionResearchSummary:
    def test_ok_without_ratings_still_shows_eps(self):
        from lib.render import _section_research_summary

        summary = {
            "status": "ok",
            "latest_ratings": [],
            "target_price_range": {"min": 40.0, "max": 50.0, "avg_upper": 48.0},
            "eps_forecasts": [{"quarter": "2026Q1", "avg_eps": 2.5, "n_analysts": 3}],
        }
        c = _collection_with_research(summary)
        dims = {d["dimension"]: d for d in c["dimensions"]}
        text = _section_research_summary(c, "600176", dims)
        assert "卖方预期价位" in text
        assert "EPS预测" in text
        assert "机构覆盖" not in text

    def test_forbidden_words_with_research_data(self):
        from lib.render import _section_research_summary

        summary = {
            "status": "ok",
            "latest_ratings": [
                {"org": "A证券", "rating": "买入", "report_date": "20260101"},
                {"org": "B证券", "rating": "中性", "report_date": "20251201"},
            ],
            "target_price_range": {"min": 40.0, "max": 50.0, "avg_upper": 48.0},
            "eps_forecasts": [],
        }
        c = _collection_with_research(summary)
        dims = {d["dimension"]: d for d in c["dimensions"]}
        section = _section_research_summary(c, "600176", dims)
        body = _analysis_body_without_legal_disclaimers(section)
        for word in FORBIDDEN_SIGNAL_WORDS:
            assert word not in body, f"research section must not contain: {word}"
        assert "偏多" in body
        assert "卖方预期价位" in body

    def test_v3_report_with_research_stays_compliant(self):
        from fixtures.collections import collection_v2_minimal
        from lib.render import render_report_v3

        c = collection_v2_minimal()
        c["market_structure"] = {"availability": {}}
        c["dimensions"].append({
            "dimension": "research",
            "display": "机构研报",
            "data": [],
            "status": "partial",
            "research_summary": {
                "status": "ok",
                "latest_ratings": [{"org": "A", "rating": "买入", "report_date": "20260101"}],
                "target_price_range": {"min": 10.0, "max": 12.0, "avg_upper": 11.5},
                "eps_forecasts": [],
            },
            "_meta": {"source": "tushare.report_rc"},
        })
        text = render_report_v3(c, "600176")
        body = _analysis_body_without_legal_disclaimers(text)
        for word in FORBIDDEN_SIGNAL_WORDS:
            assert word not in body, f"v3 with research must not contain: {word}"

    def test_no_data_returns_empty(self):
        from lib.render import _section_research_summary

        c = _collection_with_research({"status": "no_data"})
        dims = {d["dimension"]: d for d in c["dimensions"]}
        assert _section_research_summary(c, "600176", dims) == ""


class TestHtmlResearch:
    def test_html_includes_research_when_present(self):
        from lib.render import _html_research

        md = "## 机构观点\n\n- **卖方预期价位:** 10 – 12 元"
        html = _html_research(md)
        assert "机构观点与盈利预测" in html
        assert "卖方预期价位" in html

    def test_html_empty_when_no_md(self):
        from lib.render import _html_research

        assert _html_research("") == ""


class TestDefaultDims:
    def test_default_dims_exclude_research(self):
        from lib.collector import _DEFAULT_DIMS

        assert "research" not in _DEFAULT_DIMS

    def test_default_dims_include_holder_changes(self):
        from lib.collector import _DEFAULT_DIMS

        assert "holder_changes" in _DEFAULT_DIMS

    def test_invest_parser_default_dims(self):
        from invest import build_parser

        parser = build_parser()
        collect_args = parser.parse_args(["collect", "600176"])
        assert "research" not in collect_args.dims
        assert "holder_changes" in collect_args.dims

    def test_synthesize_parser_default_dims(self):
        from invest import build_parser

        parser = build_parser()
        args = parser.parse_args(["synthesize", "600176"])
        assert "holder_changes" in args.dims
