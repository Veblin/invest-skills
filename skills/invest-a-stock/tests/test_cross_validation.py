"""Tests for auto cross-validation (R-01)."""
from __future__ import annotations

from lib.schema import (
    CrossValidation,
    DimensionResult,
    SourceResult,
    _auto_cross_validate,
    _extract_l2_scalar,
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

    def test_list_descending_rows_takes_newest(self):
        """Tushare 返回序（最新在前，降序）→ 仍取最新行（不依赖隐式升序假设）。"""
        data = [
            {"trade_date": "20260805", "total_mv": 152.0},
            {"trade_date": "20260701", "total_mv": 150.0},
        ]
        assert _extract_scalar(data, "valuation") == 152.0

    def test_list_ascending_rows_takes_newest(self):
        """akshare 等源升序序 → 同样取最新行。"""
        data = [
            {"trade_date": "20260701", "total_mv": 150.0},
            {"trade_date": "20260805", "total_mv": 152.0},
        ]
        assert _extract_scalar(data, "valuation") == 152.0


class TestExtractL2Scalar:
    def test_descending_rows_takes_newest(self):
        """Tushare daily_basic 返回序（最新在前，降序）→ 取最新行 total_mv。

        缺陷场景：修复前 reversed(data) 先取 data[-1]=最旧行。
        """
        rows = [
            {"trade_date": "20260805", "total_mv": 152.0, "pe_ttm": 15.3},
            {"trade_date": "20260701", "total_mv": 150.0, "pe_ttm": 15.0},
        ]
        assert _extract_l2_scalar(rows, ("total_mv", "total_mv_yi", "market_cap")) == 152.0

    def test_ascending_rows_takes_newest(self):
        rows = [
            {"trade_date": "20260701", "total_mv": 150.0, "pe_ttm": 15.0},
            {"trade_date": "20260805", "total_mv": 152.0, "pe_ttm": 15.3},
        ]
        assert _extract_l2_scalar(rows, ("total_mv", "total_mv_yi", "market_cap")) == 152.0

    def test_dict_whitelist_priority(self):
        """dict 输入：只认白名单字段（pe_ttm 不在白名单时不命中）。"""
        assert _extract_l2_scalar(
            {"pe_ttm": 15.0, "total_mv": 152.0}, ("total_mv", "total_mv_yi", "market_cap"),
        ) == 152.0
        assert _extract_l2_scalar({"pe_ttm": 15.0}, ("total_mv", "total_mv_yi", "market_cap")) is None


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

    def test_multi_source_l2_descending_rows_convergence(self):
        """Tushare 降序（最新在前）+ 腾讯快照 → convergence。

        缺陷场景：修复前误取最旧行（1.0e9 vs 1.52e9 ≈ 41% → 误标 divergence）；
        修复后取最新行（1.52e9 vs 1.52e9）→ convergence。
        """
        s1 = self._make_source("tushare.daily_basic", [
            {"trade_date": "20260805", "total_mv": 1.52e9},  # 最新
            {"trade_date": "20260701", "total_mv": 1.00e9},  # 最旧（误取 → 41% diff）
        ])
        s2 = self._make_source("tencent_finance", {"total_mv": 1.52e9})
        dim = DimensionResult("valuation", [s1, s2])
        assert dim.cross_validation is not None
        assert dim.cross_validation.status == "convergence"

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
