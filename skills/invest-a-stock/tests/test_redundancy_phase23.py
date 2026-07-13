"""Phase 2/3 冗余修复回归：_collect_dimension、C5 估值缓存、stats、avg_price。"""

from __future__ import annotations

import pytest


class TestHolderAvgPrice:
    def test_numeric_string(self):
        from lib.collector import _holder_avg_price

        assert _holder_avg_price({"成交均价": "12.5"}, "成交均价") == pytest.approx(12.5)

    def test_non_numeric_falls_back_to_raw(self):
        from lib.collector import _holder_avg_price

        assert _holder_avg_price({"成交均价": "12.50元"}, "成交均价") == "12.50元"

    def test_prefers_first_numeric_key(self):
        from lib.collector import _holder_avg_price

        row = {"成交均价": "坏值", "交易均价": 9.8}
        assert _holder_avg_price(row, "成交均价", "交易均价") == pytest.approx(9.8)

    def test_numeric_preferred_over_column_order(self):
        """先列非数字、后列数字 → 取数字（数值优先于列顺序）。"""
        from lib.collector import _holder_avg_price

        row = {"成交均价": "12.50元", "交易均价": 9.8}
        assert _holder_avg_price(row, "成交均价", "交易均价") == pytest.approx(9.8)

    def test_zero_preserved(self):
        from lib.collector import _holder_avg_price

        assert _holder_avg_price({"交易均价": 0}, "交易均价") == 0.0


class TestFacadeAwareValuation:
    def test_patch_lib_render_percentiles_via_markdown_wrapper(self, monkeypatch):
        from lib.render_markdown import _v3_valuation_percentiles

        monkeypatch.setattr(
            "lib.render._v3_valuation_percentiles",
            lambda dims, cache: (91.0, 40.0, "偏高"),
        )
        assert _v3_valuation_percentiles({}, {}) == (91.0, 40.0, "偏高")

    def test_patch_render_utils_still_works_via_wrapper(self, monkeypatch):
        from lib.render_markdown import _v3_valuation_percentiles

        monkeypatch.setattr(
            "lib.render_utils._v3_valuation_percentiles",
            lambda dims, cache: (77.0, 33.0, "适中"),
        )
        assert _v3_valuation_percentiles({}, {}) == (77.0, 33.0, "适中")

    def test_patch_lib_render_load_summary_via_markdown_wrapper(self, monkeypatch):
        from lib.render_markdown import _v3_load_valuation_summary

        sentinel = {"pe": {"pct": 1.0}}
        monkeypatch.setattr(
            "lib.render._v3_load_valuation_summary",
            lambda dims, cache: sentinel,
        )
        assert _v3_load_valuation_summary({}, {}) is sentinel


class TestCollectDimension:
    def test_parallel_success_and_query_params(self, monkeypatch):
        from lib import collector
        from lib.schema import SourceResult

        def _ok():
            return {"x": 1}

        def _fail():
            raise RuntimeError("boom")

        monkeypatch.setattr(
            collector,
            "_run_sources_parallel",
            lambda tasks, dim: [
                SourceResult("a", {"x": 1}, dim, latency_ms=1.0),
                SourceResult("b", None, dim, error="boom", latency_ms=0.5),
            ],
        )
        annotated: list = []

        def _annotate(by_source, qp):
            annotated.append((sorted(by_source), qp))

        monkeypatch.setattr(collector, "_annotate_query_params", _annotate)

        out = collector._collect_dimension(
            "demo",
            [("a", _ok), ("b", _fail)],
            query_params={"a": "qa", "b": "qb"},
        )
        assert out["dimension"] == "demo"
        assert out.get("data") == {"x": 1}
        assert annotated and annotated[0][1] == {"a": "qa", "b": "qb"}

    def test_empty_tasks_returns_empty_result(self):
        from lib.collector import _collect_dimension

        sentinel = {"dimension": "x", "data": None, "note": "empty"}
        assert _collect_dimension("x", [], empty_result=sentinel) is sentinel

    def test_postprocess_receives_legacy_and_results(self, monkeypatch):
        from lib import collector
        from lib.schema import SourceResult

        monkeypatch.setattr(
            collector,
            "_run_sources_parallel",
            lambda tasks, dim: [
                SourceResult("s1", [1, 2], dim, latency_ms=0.1),
            ],
        )
        seen: dict = {}

        def _pp(legacy, results):
            seen["legacy"] = legacy
            seen["n"] = len(results)
            legacy = dict(legacy)
            legacy["tagged"] = True
            return legacy

        out = collector._collect_dimension("d", [("s1", lambda: [1, 2])], postprocess=_pp)
        assert out["tagged"] is True
        assert seen["n"] == 1


class TestValuationSummaryCache:
    def test_load_once_per_cache(self, monkeypatch):
        from lib import render_utils

        calls = {"n": 0}

        def _fake_summary(*_a, **_k):
            calls["n"] += 1
            return {
                "pe": {"pct": 90.0, "median": 20.0, "zone": "偏高", "current": 40.0},
                "pb": {"pct": 50.0, "median": 2.0, "zone": "适中", "current": 2.1},
                "ps": {},
                "window_label": "近4年",
            }

        monkeypatch.setattr("lib.valuation.valuation_summary", _fake_summary)
        monkeypatch.setattr(
            "lib.valuation.valuation_window_label",
            lambda n: f"n={n}",
        )

        dims = {
            "valuation": {
                "data": [
                    {"pe_ttm": 10.0, "pb": 1.0, "ps_ttm": 2.0, "dv_ratio": 1.5},
                    {"pe_ttm": 40.0, "pb": 2.1, "ps_ttm": 3.0, "dv_ratio": 1.2},
                ],
            },
        }
        cache: dict = {}
        s1 = render_utils._v3_load_valuation_summary(dims, cache)
        s2 = render_utils._v3_load_valuation_summary(dims, cache)
        pe1, _, _ = render_utils._v3_valuation_percentiles(dims, cache)
        assert s1 is s2
        assert s1 is not None
        assert pe1 == pytest.approx(90.0)
        assert calls["n"] == 1


class TestStatsModule:
    def test_percentile_rank_via_stats(self):
        from lib.stats import percentile_rank

        assert percentile_rank([10.0, 20.0, 30.0], 25.0) == pytest.approx(2 / 3 * 100)

    def test_calc_beta_basic(self):
        from lib.stats import calc_beta

        market = [0.01 * ((i % 5) - 2) for i in range(20)]
        stock = [1.5 * m + 0.001 for m in market]
        out = calc_beta(stock, market)
        assert out["beta"] is not None
        assert out["observations"] == 20
        assert out["beta"] == pytest.approx(1.5, abs=0.05)

    def test_valuation_reexports_stats(self):
        from lib import stats, valuation

        assert valuation.percentile_rank is stats.percentile_rank
        assert valuation.calc_beta is stats.calc_beta
