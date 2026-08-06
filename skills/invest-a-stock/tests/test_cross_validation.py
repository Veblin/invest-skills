"""Tests for auto cross-validation (R-01)."""
from __future__ import annotations

from lib.schema import (
    CrossValidation,
    DimensionResult,
    SourceResult,
    _auto_cross_validate,
    _extract_scalar,
)


class TestExtractScalar:
    def test_from_float(self):
        assert _extract_scalar(3.14) == 3.14

    def test_from_int(self):
        assert _extract_scalar(42) == 42.0

    def test_from_dict_with_pe(self):
        assert _extract_scalar({"pe": 15.5}) == 15.5

    def test_from_dict_with_close(self):
        assert _extract_scalar({"close": 100.0}) == 100.0

    def test_from_none(self):
        assert _extract_scalar(None) is None

    def test_from_empty_dict(self):
        assert _extract_scalar({}) is None

    def test_quote_change_pct_zero_with_dimension(self):
        assert _extract_scalar({"change_pct": 0.0}, "quote") == 0.0

    def test_northbound_list_net_mf_vol_with_dimension(self):
        data = [{"trade_date": "20260101", "net_mf_vol": 5e7}]
        assert _extract_scalar(data, "northbound") == 5e7


class TestAutoCrossValidate:
    def _make_source(self, source, data):
        return SourceResult(source, data, "test_dim")

    def test_two_sources_converge(self):
        s1 = self._make_source("a", 10.0)
        s2 = self._make_source("b", 10.05)  # 0.5% diff
        result = _auto_cross_validate("test", [s1, s2])
        assert result is not None
        assert result.status == "convergence"

    def test_two_sources_diverge(self):
        s1 = self._make_source("a", 10.0)
        s2 = self._make_source("b", 10.6)  # ≈5.83% diff > 5% threshold (R12h C5)
        result = _auto_cross_validate("test", [s1, s2])
        assert result is not None
        assert result.status == "divergence"

    def test_five_percent_diff_is_convergence(self):
        """R12h C5：5% 边界内 → convergence（阈值 1% → 5%）。"""
        s1 = self._make_source("a", 10.0)
        s2 = self._make_source("b", 10.5)  # 10.0 vs 10.5 ≈ 4.88% < 5%
        result = _auto_cross_validate("test", [s1, s2])
        assert result is not None
        assert result.status == "convergence"

    def test_single_source_returns_none(self):
        s1 = self._make_source("a", 10.0)
        result = _auto_cross_validate("test", [s1])
        assert result is None

    def test_non_numeric_returns_none(self):
        s1 = self._make_source("a", {"name": "test"})
        s2 = self._make_source("b", {"industry": "tech"})
        result = _auto_cross_validate("test", [s1, s2])
        assert result is None

    def test_mixed_data_and_none(self):
        s1 = self._make_source("a", 10.0)
        s2 = self._make_source("b", None)  # failed source
        result = _auto_cross_validate("test", [s1, s2])
        assert result is None  # only 1 valid value


class TestDimensionResultCrossValidation:
    def _make_source(self, source, data):
        return SourceResult(source, data, "valuation")

    def test_multi_source_l2_field_convergence(self):
        """市值（L2 字段）1.3% 差异 → convergence（阈值 5%）。"""
        s1 = self._make_source("tushare.daily_basic", [{"total_mv": 1.5e9}])
        s2 = self._make_source("tencent_finance", {"total_mv": 1.52e9})
        dim = DimensionResult("valuation", [s1, s2])
        assert dim.cross_validation is not None
        assert dim.cross_validation.status == "convergence"

    def test_multi_source_l2_field_divergence(self):
        """市值（L2 字段）6.5% 差异 → divergence。"""
        s1 = self._make_source("tushare.daily_basic", [{"total_mv": 1.5e9}])
        s2 = self._make_source("tencent_finance", {"total_mv": 1.6e9})
        dim = DimensionResult("valuation", [s1, s2])
        assert dim.cross_validation is not None
        assert dim.cross_validation.status == "divergence"

    def test_pe_ratio_no_longer_annotated(self):
        """R12h C5：PE/PB（比率/分位类）不再标注——即使差异极大（沃格 2548.1% 类假警报根因）。"""
        s1 = self._make_source("tushare.daily_basic", [{"pe_ttm": 15.0}])
        s2 = self._make_source("tencent_finance", {"pe_ttm": 100.0})
        dim = DimensionResult("valuation", [s1, s2])
        assert dim.cross_validation is None

    def test_single_source_no_cv(self):
        s1 = self._make_source("tencent_finance", {"total_mv": 1.5e9})
        dim = DimensionResult("valuation", [s1])
        assert dim.cross_validation is None
