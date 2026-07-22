"""Unit tests for ETF paths in query_data (_compute_technical / _summarize_quality)."""

from __future__ import annotations

import sys
from pathlib import Path

_LIB = Path(__file__).resolve().parent.parent / "scripts" / "lib"
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

from query_data import _compute_technical, _summarize_quality  # noqa: E402


def _etf_result(*, kline: dict, etf_data: dict | None = None) -> dict:
    result = {
        "asset_type": "etf",
        "kline": kline,
        "quote": {"status": "available"},
        "valuation": {"status": "not_applicable"},
        "technical": {},
        "macro": {"status": "ok"},
        "market_microstructure": {"status": "available"},
        "etf_data": etf_data,
    }
    return result


class TestComputeTechnicalEtf:
    def test_missing_kline_status(self):
        result = _etf_result(kline={"status": "missing", "rows": 0, "data": []})
        _compute_technical(result)
        assert result["technical"]["status"] == "missing"

    def test_zero_rows(self):
        result = _etf_result(kline={"status": "partial", "rows": 0, "data": []})
        _compute_technical(result)
        assert result["technical"]["status"] == "missing"

    def test_insufficient_rows(self):
        result = _etf_result(
            kline={
                "status": "partial",
                "rows": 10,
                "data": [{}] * 10,
                "latest_nav": 1.0,
                "volatility_annualized": 0.15,
            }
        )
        _compute_technical(result)
        assert result["technical"]["status"] == "insufficient"

    def test_available(self):
        result = _etf_result(
            kline={
                "status": "available",
                "rows": 60,
                "data": [{}] * 60,
                "latest_nav": 1.05,
                "volatility_annualized": 0.12,
                "rsi": 55.0,
                "ma20": 1.02,
                "ma60": 1.0,
            }
        )
        _compute_technical(result)
        assert result["technical"]["status"] == "available"
        assert result["technical"]["latest_close"] == 1.05
        assert result["technical"]["volatility_annualized"] == 0.12

    def test_partial_with_enough_rows(self):
        result = _etf_result(
            kline={"status": "partial", "rows": 25, "data": [{}] * 25}
        )
        _compute_technical(result)
        assert result["technical"]["status"] == "partial"


class TestSummarizeQualityEtf:
    def _base_result(self, etf_data: dict | None) -> dict:
        return {
            "quote": {"status": "available"},
            "kline": {"status": "available"},
            "valuation": {"status": "not_applicable"},
            "technical": {"status": "available"},
            "macro": {"status": "ok"},
            "market_microstructure": {"status": "available"},
            "etf_data": etf_data,
        }

    def test_not_applicable_when_no_etf_data(self):
        result = self._base_result(None)
        _summarize_quality(result)
        assert result["data_quality"]["etf"] == "not_applicable"

    def test_available_no_errors(self):
        etf = {
            "index_pe": 15.2,
            "_errors": [],
            "data_quality": {
                "index_pe": "available",
                "spot": "available",
                "hedge": "available",
            },
        }
        result = self._base_result(etf)
        _summarize_quality(result)
        dq = result["data_quality"]
        assert dq["etf"] == "available"
        assert dq["etf_index_pe"] == "available"
        assert dq["etf_spot"] == "available"
        assert dq["etf_hedge"] == "available"

    def test_partial_with_errors_and_data(self):
        etf = {
            "index_pe": 15.2,
            "_errors": ["etf_spot: not found"],
            "data_quality": {
                "index_pe": "available",
                "spot": "missing",
                "hedge": "available",
            },
        }
        result = self._base_result(etf)
        _summarize_quality(result)
        dq = result["data_quality"]
        assert dq["etf"] == "partial"
        assert dq["etf_spot"] == "missing"

    def test_missing_all_failed(self):
        etf = {
            "_errors": ["csindex_pe: timeout", "etf_spot: not found"],
            "data_quality": {
                "index_pe": "missing",
                "spot": "missing",
                "hedge": "available",
            },
        }
        result = self._base_result(etf)
        _summarize_quality(result)
        assert result["data_quality"]["etf"] == "missing"

    def test_legacy_single_error(self):
        etf = {"_error": "query failed"}
        result = self._base_result(etf)
        _summarize_quality(result)
        assert result["data_quality"]["etf"] == "missing"
