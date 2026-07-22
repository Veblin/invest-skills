"""Unit tests for ETF _auto_flags + 588000 hedge map (no network)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from etf_data import (
    ETF_HEDGE_MAP,
    _auto_flags,
    _em_to_premium_discount,
    _fetch_csindex_pe,
    query_etf_data,
)


def _base(**overrides) -> dict:
    r: dict = {
        "aum": 10.0,
        "premium_discount": 0.0,
        "hedge_coverage": {"coverage": "high"},
    }
    r.update(overrides)
    return r


class TestEmToPremiumDiscount:
    """EM 基金折价率 must be negated so + = 溢价 in premium_discount."""

    def test_em_positive_is_discount_normalized_negative(self):
        assert _em_to_premium_discount(0.11) == pytest.approx(-0.11)

    def test_em_negative_is_premium_normalized_positive(self):
        assert _em_to_premium_discount(-3.2) == pytest.approx(3.2)

    def test_em_none_stays_none(self):
        assert _em_to_premium_discount(None) is None


class TestAutoFlagsPremiumDiscount:
    """premium_discount is normalized: + = 溢价, - = 折价."""

    def test_premium_gt_2_triggers_yijia(self):
        r = _base(premium_discount=3.0)
        _auto_flags(r)
        assert any("溢价" in f and "3.0" in f for f in r["flags"])
        assert not any("折价" in f for f in r["flags"])

    def test_discount_lt_minus_2_triggers_zhejia(self):
        r = _base(premium_discount=-3.0)
        _auto_flags(r)
        assert any("折价" in f and "3.0" in f for f in r["flags"])
        assert not any("溢价" in f for f in r["flags"])

    def test_near_zero_no_pd_flag(self):
        r = _base(premium_discount=0.1)
        _auto_flags(r)
        assert not any("溢价" in f or "折价" in f for f in r["flags"])

    def test_inf_nan_marked_abnormal(self):
        for bad in (float("inf"), float("-inf"), float("nan")):
            r = _base(premium_discount=bad)
            _auto_flags(r)
            assert any("折溢价数据异常" in f for f in r["flags"])
            assert not any("溢价" in f or "折价" in f for f in r["flags"]
                           if "数据异常" not in f)


class TestAutoFlagsAumAndHedge:
    def test_aum_below_2(self):
        r = _base(aum=1.5)
        _auto_flags(r)
        assert any("AUM" in f for f in r["flags"])

    def test_coverage_none(self):
        r = _base(hedge_coverage={"coverage": "none"})
        _auto_flags(r)
        assert any("无可用" in f for f in r["flags"])

    def test_coverage_low(self):
        r = _base(hedge_coverage={"coverage": "low"})
        _auto_flags(r)
        assert any("覆盖有限" in f for f in r["flags"])


class TestQueryEtfDataMetadata:
    @patch("etf_data._lookup_etf_spot_row", return_value=(None, "etf_spot: test skip"))
    def test_not_mapped_index_pe_status_for_theme_etf(self, _mock_spot):
        r = query_etf_data("515790")
        assert r["index_pe_status"] == "not_mapped"
        assert r["index_pe"] is None
        assert "csindex" in r.get("index_pe_note", "")

    @patch("etf_data._lookup_etf_spot_row", return_value=(None, "etf_spot: test skip"))
    def test_data_quality_populated(self, _mock_spot):
        r = query_etf_data("515790")
        dq = r.get("data_quality", {})
        assert dq.get("index_pe") == "not_applicable"
        assert dq.get("hedge") == "available"

    @patch("etf_data._lookup_etf_spot_row", return_value=(None, "etf_spot: test skip"))
    def test_tracking_error_note_no_fake_estimate(self, _mock_spot):
        r = query_etf_data("510300")
        assert r["tracking_error"] is None
        assert "0.05" not in r.get("tracking_error_note", "")
        assert "未实现" in r.get("tracking_error_note", "")


class Test588000HedgeMap:
    def test_has_star50_options_and_high_coverage(self):
        entry = ETF_HEDGE_MAP["588000"]
        assert entry["options"] == "科创50ETF期权"
        assert entry["coverage"] == "high"


class TestFetchCsindexPe:
    @patch("akshare.stock_zh_index_value_csindex")
    @patch("etf_data.akshare_direct_session")
    def test_pe1_zero_preserves_zero_not_falls_back_to_pe2(
        self, mock_session, mock_csindex
    ):
        mock_session.return_value.__enter__ = MagicMock(return_value=None)
        mock_session.return_value.__exit__ = MagicMock(return_value=False)
        mock_csindex.return_value = pd.DataFrame(
            [{"市盈率1": 0.0, "市盈率2": 15.5}]
        )

        result: dict = {"_errors": []}
        _fetch_csindex_pe(result, "000300")

        assert result["index_pe"] == 0.0
        assert result["index_pe"] != 15.5
