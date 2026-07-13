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
        s2 = self._make_source("b", 10.5)  # 5% diff, per execution plan spec
        result = _auto_cross_validate("test", [s1, s2])
        assert result is not None
        assert result.status == "divergence"

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

    def test_multi_source_triggers_cv(self):
        s1 = self._make_source("tushare.daily_basic", [{"pe_ttm": 15.0}])
        s2 = self._make_source("tencent_finance", {"pe_ttm": 15.2})
        dim = DimensionResult("valuation", [s1, s2])
        assert dim.cross_validation is not None
        # 15.0 vs 15.2 ≈ 1.3% diff > 1% threshold → divergence
        assert dim.cross_validation.status == "divergence"

    def test_single_source_no_cv(self):
        s1 = self._make_source("tencent_finance", {"pe_ttm": 15.0})
        dim = DimensionResult("valuation", [s1])
        assert dim.cross_validation is None
