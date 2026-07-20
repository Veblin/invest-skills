"""Tests for RRF fusion engine (R-08).

Tests cover:
- Source weight mapping
- Scalar extraction from various data formats
- weighted_rrf_for_dimension (single/multi source, consensus levels)
- fuse_from_source_results with DimensionResult objects
- fuse_from_legacy_dicts with legacy dict format
"""

from __future__ import annotations

import pytest

from lib.schema import SourceResult


# ---- Helper to build FusedDataPoint expectation for assertions ----


def _assert_fp_shape(fp, dimension: str):
    """Minimal shape assertion for a FusedDataPoint."""
    assert fp is not None
    assert fp.dimension == dimension
    assert isinstance(fp.source_values, dict)
    assert isinstance(fp.source_weights, dict)
    assert fp.consensus in ("strong", "moderate", "weak")
    assert isinstance(fp.max_diff_pct, (int, float))


class TestSourceWeight:
    def test_tushare_weight(self):
        from lib.fusion import _source_weight

        assert _source_weight("tushare.daily_basic") == 0.95

    def test_akshare_weight(self):
        from lib.fusion import _source_weight

        assert _source_weight("akshare.stock_zh_a_hist") == 0.75

    def test_baostock_weight(self):
        from lib.fusion import _source_weight

        assert _source_weight("baostock.kline") == 0.70

    def test_tencent_finance_weight(self):
        from lib.fusion import _source_weight

        assert _source_weight("tencent_finance") == 0.65

    def test_unknown_weight_defaults_to_50(self):
        from lib.fusion import _source_weight

        assert _source_weight("unknown.source") == 0.50

    def test_prefix_exact_match(self):
        """Prefer longest matching prefix."""
        from lib.fusion import _source_weight

        assert _source_weight("tushare.moneyflow") == 0.95


class TestWeightedRRFSingleSource:
    def test_single_source_returns_weak_consensus(self):
        from lib.fusion import weighted_rrf_for_dimension

        sources = {"tushare.daily_basic": 15.0}
        result = weighted_rrf_for_dimension("valuation", sources)
        assert result is not None
        _assert_fp_shape(result, "valuation")
        assert result.consensus == "weak"
        assert result.max_diff_pct == 0.0
        assert abs(result.fused_value - 15.0) < 0.001
        assert result.source_weights == {"tushare.daily_basic": 1.0}

    def test_single_akshare_source(self):
        from lib.fusion import weighted_rrf_for_dimension

        sources = {"akshare.snapshot": 42.5}
        result = weighted_rrf_for_dimension("quote", sources)
        assert result is not None
        assert result.fused_value == 42.5


class TestWeightedRRFMultiSource:
    def test_two_sources_agree_strong_consensus(self):
        """Two sources with identical values → strong consensus."""
        from lib.fusion import weighted_rrf_for_dimension

        sources = {"tushare.daily_basic": 15.0, "akshare.snapshot": 15.0}
        result = weighted_rrf_for_dimension("valuation", sources)
        assert result is not None
        _assert_fp_shape(result, "valuation")
        assert result.consensus == "strong"
        assert result.max_diff_pct == 0.0
        assert abs(result.fused_value - 15.0) < 0.01

    def test_two_sources_moderate_consensus(self):
        """Two sources with ~3% difference → moderate consensus."""
        from lib.fusion import weighted_rrf_for_dimension

        sources = {"tushare.daily_basic": 15.0, "akshare.snapshot": 15.4}
        result = weighted_rrf_for_dimension("valuation", sources)
        assert result is not None
        assert result.consensus == "moderate"
        assert 2.5 < result.max_diff_pct < 3.5
        # Fusion biased toward tushare (higher quality)
        assert abs(result.fused_value - 15.0) < 0.55

    def test_two_sources_weak_consensus(self):
        """Two sources with 10% difference → weak consensus."""
        from lib.fusion import weighted_rrf_for_dimension

        sources = {"tushare.daily_basic": 15.0, "akshare.snapshot": 16.5}
        result = weighted_rrf_for_dimension("valuation", sources)
        assert result is not None
        assert result.consensus == "weak"
        assert result.max_diff_pct > 5.0
        # Fusion closer to tushare (higher quality weight)
        assert result.fused_value < 16.0

    def test_three_sources(self):
        """Three sources, mix of weights."""
        from lib.fusion import weighted_rrf_for_dimension

        sources = {
            "tushare.daily_basic": 15.0,
            "akshare.snapshot": 15.2,
            "tencent_finance": 14.8,
        }
        result = weighted_rrf_for_dimension("valuation", sources)
        assert result is not None
        assert len(result.source_values) == 3
        assert result.consensus == "moderate"
        # Fusion should be close to tushare value
        assert abs(result.fused_value - 15.0) < 0.5

    def test_tushare_tencent_divergence(self):
        """Tushare vs tencent: fusion biased toward tushare."""
        from lib.fusion import weighted_rrf_for_dimension

        sources = {"tushare.daily_basic": 14.0, "tencent_finance": 16.0}
        result = weighted_rrf_for_dimension("valuation", sources)
        assert result is not None
        # Higher quality weight on tushare → fused closer to 14 than 16
        tushare_pct = result.source_weights.get("tushare.daily_basic", 0)
        tencent_pct = result.source_weights.get("tencent_finance", 0)
        assert tushare_pct > tencent_pct
        assert result.fused_value < 15.0

    def test_edge_consensus_boundaries(self):
        """Test consensus transition at 1% and 5% boundaries."""
        from lib.fusion import weighted_rrf_for_dimension

        # <1% difference → strong (≤1%)
        sources_099pct = {"tushare.daily_basic": 100.0, "akshare.snapshot": 100.99}
        r1 = weighted_rrf_for_dimension("test", sources_099pct)
        assert r1 is not None
        assert r1.consensus == "strong", f"max_diff_pct={r1.max_diff_pct}"

        # 2% difference → moderate (>1% but ≤5%)
        sources_2pct = {"tushare.daily_basic": 100.0, "akshare.snapshot": 102.0}
        r2 = weighted_rrf_for_dimension("test", sources_2pct)
        assert r2 is not None
        assert r2.consensus == "moderate", f"max_diff_pct={r2.max_diff_pct}"

        # >5% difference → weak
        sources_6pct = {"tushare.daily_basic": 100.0, "akshare.snapshot": 106.0}
        r3 = weighted_rrf_for_dimension("test", sources_6pct)
        assert r3 is not None
        assert r3.consensus == "weak", f"max_diff_pct={r3.max_diff_pct}"

    def test_four_sources_with_two_missing(self):
        """4 sources registered, 2 with data, 2 with None."""
        from lib.fusion import weighted_rrf_for_dimension

        sources = {
            "tushare.daily_basic": 15.0,
            "akshare.snapshot": 16.0,
            "baostock.kline": None,
            "tencent_finance": None,
        }
        result = weighted_rrf_for_dimension("valuation", sources)
        assert result is not None
        assert len(result.source_values) == 2

    def test_empty_sources_returns_none(self):
        """All None → None."""
        from lib.fusion import weighted_rrf_for_dimension

        sources = {"tushare.daily_basic": None, "akshare.snapshot": None}
        result = weighted_rrf_for_dimension("valuation", sources)
        assert result is None

    def test_all_none_returns_none(self):
        from lib.fusion import weighted_rrf_for_dimension

        result = weighted_rrf_for_dimension("test", {})
        assert result is None


class TestFuseFromSourceResults:
    def test_with_dimension_results(self):
        """Integration with SourceResult objects."""
        from lib.fusion import fuse_from_source_results
        from lib.schema import DimensionResult

        sources = [
            SourceResult("tushare.daily_basic", [{"pe_ttm": 15.0}], "valuation"),
            SourceResult("tencent_finance", {"pe_ttm": 15.5}, "valuation"),
        ]
        dim_results = {"valuation": DimensionResult("valuation", sources)}
        fused = fuse_from_source_results(dim_results)
        assert "valuation" in fused
        fp = fused["valuation"]
        assert fp.consensus in ("strong", "moderate")
        assert 15.0 <= fp.fused_value <= 15.5

    def test_with_missing_dimension_result(self):
        """DimensionResult not passed → skipped."""
        from lib.fusion import fuse_from_source_results

        fused = fuse_from_source_results({"some_other": "not a DimensionResult"})
        assert fused == {}

    def test_with_partial_source_data(self):
        """Some SourceResult with no data → skipped in fusion."""
        from lib.fusion import fuse_from_source_results
        from lib.schema import DimensionResult

        sources = [
            SourceResult("tushare.daily_basic", [{"pe_ttm": 15.0}], "valuation"),
            SourceResult("akshare.snapshot", None, "valuation", error="no data"),
        ]
        dim_results = {"valuation": DimensionResult("valuation", sources)}
        fused = fuse_from_source_results(dim_results)
        assert "valuation" in fused
        assert len(fused["valuation"].source_values) == 1


class TestFuseFromLegacyDicts:
    def test_legacy_dict_format_with_scalar_value(self):
        """Test fusion reads scalar_value from all_sources."""
        from lib.fusion import fuse_from_legacy_dicts

        dimensions = [
            {
                "dimension": "valuation",
                "data": [{"pe_ttm": 15.0}],
                "_meta": {
                    "all_sources": [
                        {"source": "tushare.daily_basic", "scalar_value": 15.0, "data_available": True},
                        {"source": "akshare.snapshot", "scalar_value": 15.5, "data_available": True},
                    ],
                },
            },
        ]
        fused = fuse_from_legacy_dicts(dimensions)
        assert "valuation" in fused
        fp = fused["valuation"]
        assert len(fp.source_values) == 2
        assert fp.consensus in ("strong", "moderate")

    def test_legacy_dict_single_source_skipped_when_policy_requires_multi(self):
        """Single source still produces a fusion result (weak consensus)."""
        from lib.fusion import fuse_from_legacy_dicts

        dimensions = [
            {
                "dimension": "quote",
                "data": {"price": 100.0},
                "_meta": {
                    "all_sources": [
                        {"source": "tencent_finance", "scalar_value": 100.0, "data_available": True},
                    ],
                },
            },
        ]
        fused = fuse_from_legacy_dicts(dimensions)
        assert "quote" in fused
        assert fused["quote"].consensus == "weak"

    def test_legacy_dict_no_scalar_values(self):
        """No scalar_value → empty fusion."""
        from lib.fusion import fuse_from_legacy_dicts

        dimensions = [
            {
                "dimension": "research",
                "data": [],
                "_meta": {
                    "all_sources": [
                        {"source": "tushare.report_rc", "scalar_value": None, "data_available": True},
                    ],
                },
            },
        ]
        fused = fuse_from_legacy_dicts(dimensions)
        assert fused == {}

    def test_legacy_dict_no_all_sources(self):
        from lib.fusion import fuse_from_legacy_dicts

        dimensions = [{"dimension": "basic_info", "data": {}, "_meta": {}}]
        fused = fuse_from_legacy_dicts(dimensions)
        assert fused == {}

    def test_legacy_dict_none_dimension_skipped(self):
        from lib.fusion import fuse_from_legacy_dicts

        dimensions = [None]
        fused = fuse_from_legacy_dicts(dimensions)
        assert fused == {}


class TestSchemaScalarValue:
    """Verify that SourceResult.to_dict() now includes scalar_value."""

    def test_to_dict_includes_scalar_from_dict_data(self):
        sr = SourceResult("tushare.daily_basic", {"pe_ttm": 15.5}, "valuation")
        d = sr.to_dict()
        assert d.get("scalar_value") == 15.5

    def test_to_dict_includes_scalar_from_list_data(self):
        sr = SourceResult("akshare.kline", [{"close": 100.0}], "kline")
        d = sr.to_dict()
        assert d.get("scalar_value") == 100.0

    def test_to_dict_scalar_none_when_no_numeric_data(self):
        sr = SourceResult("akshare.research", ["non-numeric"], "research")
        d = sr.to_dict()
        assert d.get("scalar_value") is None

    def test_to_dict_scalar_none_when_data_is_none(self):
        sr = SourceResult("tushare.stock_basic", None, "basic_info", error="failed")
        d = sr.to_dict()
        assert d.get("scalar_value") is None
