"""Regression tests for v0.1.5 code-review fixes."""
from __future__ import annotations

import json
from pathlib import Path

import pytest


class TestExtractScalarList:
    def test_kline_list_uses_last_close(self):
        from lib.schema import _extract_scalar

        data = [{"close": 10.0}, {"close": 12.5}]
        assert _extract_scalar(data) == 12.5

    def test_dict_skips_zero_close_uses_price(self):
        from lib.schema import _extract_scalar

        assert _extract_scalar({"close": 0.0, "price": 10.0}) == 10.0


class TestRelativeDiff:
    def test_mixed_sign_not_false_convergence(self):
        from lib.fusion import weighted_rrf_for_dimension

        fp = weighted_rrf_for_dimension("t", {"a": -15.0, "b": -5.0})
        assert fp is not None
        assert fp.consensus == "weak"
        assert fp.max_diff_pct > 1.0


class TestAutoCrossValidateZero:
    def test_near_zero_avg_returns_none(self):
        from lib.schema import SourceResult, _auto_cross_validate

        s1 = SourceResult("a", 1e-20, "valuation")
        s2 = SourceResult("b", -1e-20, "valuation")
        assert _auto_cross_validate("valuation", [s1, s2]) is None

    def test_zero_change_pct_included(self):
        from lib.schema import SourceResult, _auto_cross_validate

        s1 = SourceResult("a", {"change_pct": 0.0}, "quote")
        s2 = SourceResult("b", {"change_pct": 0.5}, "quote")
        cv = _auto_cross_validate("quote", [s1, s2])
        assert cv is not None


class TestFusionZeroScalar:
    def test_zero_change_pct_not_skipped(self):
        from lib.fusion import fuse_from_source_results
        from lib.schema import DimensionResult, SourceResult

        dim = DimensionResult("quote", [
            SourceResult("a", {"change_pct": 0.0}, "quote"),
            SourceResult("b", {"change_pct": 0.1}, "quote"),
        ])
        fused = fuse_from_source_results({"quote": dim})
        assert "quote" in fused
        assert len(fused["quote"].source_values) == 2


class TestNormalizeCollection:
    def test_credibility_scores_alias(self):
        import invest

        out = invest._normalize_collection_for_render(
            {"credibility_scores": {"quote": 80.0}},
        )
        assert out["credibility"] == {"quote": 80.0}
        assert out["credibility_scores"] == {"quote": 80.0}


class TestRenderAttachExtras:
    def test_offline_skips_market_structure_fetch(self):
        from unittest.mock import patch
        from lib import collector
        from lib.render import render

        c = {
            "symbol": "600176",
            "summary": {"available": 1, "total": 1},
            "dimensions": [
                {"dimension": "basic_info", "data": {"name": "测试"},
                 "status": "available", "_meta": {}},
            ],
            "market_structure": {"availability": {}},
            "industry_peers": {"peers": [], "sufficient": True},
        }
        with patch.object(collector, "attach_market_structure") as ms_mock, \
             patch.object(collector, "attach_phase2_extras") as p2_mock:
            render(c, "600176", "compact", attach_extras=False)
        ms_mock.assert_not_called()
        p2_mock.assert_not_called()


class TestResumeCompatibility:
    def test_with_macro_mismatch_rejects_cache(self, isolated_store):
        import argparse
        import invest

        isolated_store.save_pipeline_step("600176", "collect", {
            "dims": ["quote"], "with_macro": False, "deep": False,
        })
        cached = {
            "symbol": "600176",
            "dimensions": [{"dimension": "quote", "data": {}}],
            "macro_context": {},
        }
        args = argparse.Namespace(symbol="600176", with_macro=True, deep=False)
        assert invest._resume_cache_compatible(args, ["quote"], cached) is False

    def test_matching_flags_accepts_cache(self, isolated_store):
        import argparse
        import invest

        isolated_store.save_pipeline_step("600176", "collect", {
            "dims": ["quote"], "with_macro": True, "deep": False,
        })
        cached = {
            "symbol": "600176",
            "dimensions": [{"dimension": "quote", "data": {}}],
            "macro_context": {"indicators": {"pmi": {"value": 50.0}}},
        }
        args = argparse.Namespace(symbol="600176", with_macro=True, deep=False)
        assert invest._resume_cache_compatible(args, ["quote"], cached) is True


class TestFusionSerialization:
    def test_fused_to_dict_json_roundtrip(self):
        from lib.fusion import fusion_results_to_dict, weighted_rrf_for_dimension

        fp = weighted_rrf_for_dimension("quote", {"tushare": 10.0, "akshare": 10.5})
        blob = fusion_results_to_dict({"quote": fp})
        text = json.dumps(blob)
        loaded = json.loads(text)
        assert loaded["quote"]["fused_value"] == fp.fused_value


class TestArchiverList:
    def test_list_archives_parses_symbol(self, tmp_path):
        from lib.archiver import list_archives

        f = tmp_path / "20260622-120000-600176.json"
        f.write_text("{}", encoding="utf-8")
        rows = list_archives(symbol="600176", raw_dir=str(tmp_path))
        assert len(rows) == 1
        assert rows[0]["symbol"] == "600176"
        assert rows[0]["timestamp"] == "20260622-120000"


class TestChainKeywordOrder:
    def test_new_energy_before_auto(self):
        from lib.chain import collect_chain_context

        ctx = collect_chain_context("000000", industry="新能源汽车")
        assert "锂" in ctx["upstream"]


class TestMacroLabel:
    def test_cpi_shown_with_lpr(self):
        from lib.macro import macro_signal_label

        label = macro_signal_label({
            "indicators": {
                "cpi": {"value": 3.5},
                "lpr": {"value": 3.1, "signal": "偏宽松"},
            },
        })
        assert "CPI" in label
        assert "LPR" in label
        assert "通胀压力" in label or "偏宽松" in label


class TestCollectIndustryMerge:
    def test_merges_board_and_pe(self, monkeypatch):
        from lib.collector import collect_industry
        from lib.schema import SourceResult

        board = {"industry_name": "半导体", "recent_return_pct": 5.0}
        pe = {"industry_pe_median": 40.0, "industry_pe_avg": 45.0}

        def fake_parallel(tasks, dim):
            return [
                SourceResult("akshare.board", board, dim),
                SourceResult("akshare.pe", pe, dim),
            ]

        monkeypatch.setattr("lib.collector._run_sources_parallel", fake_parallel)
        monkeypatch.setattr("lib.collector.env.is_akshare_available", lambda: True)
        monkeypatch.setattr("lib.collector.akshare_push2_available", lambda: True)
        monkeypatch.setattr(
            "lib.collector._q_akshare_basic",
            lambda _symbol: {"行业": "半导体"},
        )

        dim = collect_industry("600460")
        data = dim["data"]
        assert data["recent_return_pct"] == 5.0
        assert data["industry_pe_median"] == 40.0
        assert dim["_meta"]["source"].startswith("merged:")


class TestPlannerDeepCopy:
    def test_mutate_plan_does_not_touch_preset(self):
        from lib.planner import generate_plan, INTENT_PRESETS

        plan = generate_plan("600176", "deep_analysis")
        plan.modules[0].weight = 0.0
        assert INTENT_PRESETS["deep_analysis"].modules[0].weight != 0.0


class TestEvidenceCvDetail:
    def test_divergence_shows_percentage(self):
        from lib.evidence import build_evidence_table

        dims = [{
            "dimension": "quote",
            "display": "实时行情",
            "data": {"close": 10.0},
            "_meta": {
                "source": "tushare",
                "cross_validation": "divergence",
                "cross_validation_detail": "跨源差异 9.5%",
                "all_sources": [
                    {"source": "a", "data_available": True, "confidence": "high",
                     "scalar_value": 10.0},
                    {"source": "b", "data_available": True, "confidence": "medium",
                     "scalar_value": 11.0},
                ],
            },
        }]
        rows = build_evidence_table(dims)
        assert "9.5%" in rows[0].cross_validation


class TestCollectAllPartialCounts:
    def test_partial_summary(self, monkeypatch):
        from lib import collector as col

        def fake_quote(symbol):
            return {
                "dimension": "quote", "display": "行情",
                "data": {"close": 1.0}, "status": "partial", "_meta": {},
            }

        monkeypatch.setitem(col.COLLECTORS, "quote", ("行情", fake_quote))
        result = col.collect_all("600176", dims=["quote"])
        assert result["summary"]["available"] == 1
        assert result["summary"]["all_partial"] is True


# ---- R-05: planner presets ----


class TestPlannerPresets:
    """R-05: 四种 intent preset 均生成有效 Plan，模块 ID 对应 collector COLLECTORS。"""

    def test_deep_analysis_has_valuation_financials_kline(self):
        from lib.planner import generate_plan

        plan = generate_plan("600176", "deep_analysis")
        dims = plan.dimension_list()
        assert "quote" in dims
        assert "valuation" in dims
        assert "financials" in dims
        assert "kline" in dims
        assert "basic_info" in dims

    def test_quick_check_is_subset(self):
        from lib.planner import generate_plan

        plan = generate_plan("600176", "quick_check")
        dims = plan.dimension_list()
        assert len(dims) <= 7  # fewer modules than deep_analysis
        assert "quote" in dims  # always first

    def test_catalyst_monitor_includes_research(self):
        from lib.planner import generate_plan

        plan = generate_plan("600176", "catalyst_monitor")
        dims = plan.dimension_list()
        assert "research" in dims
        assert "northbound" in dims

    def test_compare_minimal_dimensions(self):
        from lib.planner import generate_plan

        plan = generate_plan("600176", "compare")
        dims = plan.dimension_list()
        assert "quote" in dims
        assert "valuation" in dims
        assert "financials" in dims

    def test_unknown_intent_raises(self):
        import pytest
        from lib.planner import generate_plan

        with pytest.raises(ValueError, match="未知意图"):
            generate_plan("600176", "bogus_intent")

    def test_dimension_list_sorted_by_priority(self):
        from lib.planner import generate_plan

        plan = generate_plan("600176", "deep_analysis")
        dims = plan.dimension_list()
        # quote is priority=1, should be first
        assert dims[0] == "quote"
        # research is priority=3, should be last
        assert dims[-1] == "research"


# ---- R-10: brief/full 双模式 ----


class TestBriefMode:
    """R-10: --mode brief 输出精简，--mode full 输出完整。"""

    def test_brief_mode_is_shorter_than_full(self):
        from lib.render import render_report_v3
        from conftest import make_store_collection

        c = make_store_collection("600176")
        c["market_structure"] = {}
        c["research_summary"] = {"status": "no_data", "summary_text": ""}
        c["risk_data"] = {"triggers": []}

        brief = render_report_v3(c, "600176", mode="brief")
        full = render_report_v3(c, "600176", mode="full")
        assert len(brief) < len(full)

    def test_brief_mode_includes_risk_footer(self):
        from lib.render import render_report_v3
        from conftest import make_store_collection

        c = make_store_collection("600176")
        c["market_structure"] = {}
        c["research_summary"] = {"status": "no_data", "summary_text": ""}
        c["risk_data"] = {"triggers": []}

        brief = render_report_v3(c, "600176", mode="brief")
        assert "风险提示" in brief or "免责" in brief

    def test_brief_mode_skips_full_sections(self):
        from lib.render import render_report_v3
        from conftest import make_store_collection

        c = make_store_collection("600176")
        c["market_structure"] = {}
        c["research_summary"] = {"status": "no_data", "summary_text": ""}
        c["risk_data"] = {"triggers": []}

        brief = render_report_v3(c, "600176", mode="brief")
        full = render_report_v3(c, "600176", mode="full")
        # full-mode-only section headers absent from brief
        assert "## 3. 市场结构分析" not in brief
        assert "## 4. 静态基本面分析" not in brief
        assert "## 6. 左侧/右侧概率判断" not in brief
        # but present in full
        assert "## 3. 市场结构分析" in full

    def test_full_mode_default(self):
        from lib.render import render_report_v3
        from conftest import make_store_collection

        c = make_store_collection("600176")
        c["market_structure"] = {}
        c["research_summary"] = {"status": "no_data", "summary_text": ""}
        c["risk_data"] = {"triggers": []}

        result = render_report_v3(c, "600176")  # default mode
        assert "市场结构" in result or "核心矛盾" in result


# ---- P0-1: 执行摘要 ----


class TestExecutiveSummary:
    """P0-1: 执行摘要包含一行话定位 + 两条矛盾 + 三个观察点。"""

    @staticmethod
    def _make_dims(collection):
        """Build indexed dims dict matching _index_dims in render.py."""
        dims = {}
        for d in collection.get("dimensions", []):
            dims[d["dimension"]] = d
        return dims

    def test_summary_has_structural_elements(self):
        from lib.render import _section_executive_summary

        collection = {
            "symbol": "600176",
            "dimensions": [
                {"dimension": "basic_info", "display": "基本信息",
                 "data": {"name": "测试公司"}, "status": "available", "_meta": {}},
            ],
            "fusion": {},
        }
        dims = self._make_dims(collection)
        output = _section_executive_summary(collection, "600176", dims)
        assert "执行摘要" in output
        assert "核心矛盾" in output
        assert "观察点" in output

    def test_summary_shows_company_name_from_basic_info(self):
        from lib.render import _section_executive_summary

        collection = {
            "symbol": "600519",
            "dimensions": [
                {"dimension": "basic_info", "display": "基本信息",
                 "data": {"name": "贵州茅台"}, "status": "available", "_meta": {}},
            ],
            "fusion": {},
        }
        dims = self._make_dims(collection)
        output = _section_executive_summary(collection, "600519", dims)
        assert "贵州茅台" in output

    def test_summary_fallback_when_no_basic_info(self):
        from lib.render import _section_executive_summary

        collection = {"symbol": "000001", "dimensions": [], "fusion": {}}
        dims = self._make_dims(collection)
        output = _section_executive_summary(collection, "000001", dims)
        # Should still produce valid output even without basic_info
        assert "执行摘要" in output
        assert "核心矛盾" in output
