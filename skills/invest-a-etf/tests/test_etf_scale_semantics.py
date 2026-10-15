"""B02-1 规模/份额口径与流量语义（离线 fixture）。"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from etf_data import (
    _apply_spot_row_to_profile,
    _spot_row_to_quote,
    query_etf_est_net_assets,
    query_etf_share_history,
)


class TestSharePriceMvEst:
    def test_proxy_fields_explicit(self):
        """份额×交易价 = 代理字段（单位/口径/时点显式）；aum 为同值兼容别名。"""
        result: dict = {"_errors": []}
        _apply_spot_row_to_profile(result, {"最新份额": 5_000_000_000.0,
                                            "最新价": 3.605, "基金折价率": 0.0})
        est = result["share_price_mv_est"]
        assert est["value_yi"] == round(5_000_000_000.0 * 3.605 / 1e8, 2)
        assert est["unit"] == "亿元"
        assert "交易价代理" in est["caliber"]
        assert est["as_of"] is None and "spot 行无交易日字段" in est["as_of_note"]
        assert est["is_official_net_asset"] is False
        assert result["aum"] == est["value_yi"]  # 兼容别名
        assert "非官方资产净值" in result["aum_deprecated_note"]

    @pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf"), None])
    def test_bad_shares_or_price_no_proxy(self, bad):
        result: dict = {"_errors": []}
        _apply_spot_row_to_profile(result, {"最新份额": bad, "最新价": 3.6,
                                            "基金折价率": 0.0})
        assert result["share_price_mv_est"] is None
        assert result["aum"] is None

    def test_premium_caliber_attached(self):
        result: dict = {"_errors": []}
        _apply_spot_row_to_profile(result, {"最新份额": 1e9, "最新价": 1.0,
                                            "基金折价率": -0.5})
        cal = result["premium_discount_caliber"]
        assert cal["nav_time_point"].startswith("未知")
        assert "不得" in cal["note"]


class TestEstNetAssets:
    def _bridge(self, shares_rows, nav_rows):
        def fake(getter, *args):
            if getter == "get_etf_share_history":
                return {"status": "ok", "fund_share": shares_rows, "fund_daily": []}
            if getter == "get_etf_nav":
                return {"status": "ok", "rows": nav_rows}
            return None
        return fake

    def test_same_date_match_gives_estimate(self):
        with patch("etf_data._bridge_get", side_effect=self._bridge(
                [{"trade_date": "20261006", "fd_share": 5_000_000.0}],
                [{"date": "2026-10-06", "nav": 3.0},
                 {"date": "2026-09-30", "nav": 2.9}])):
            out = query_etf_est_net_assets("510300")
        assert out["status"] == "ok"
        assert out["value_yi"] == round(5_000_000.0 * 3.0 / 10000, 2)
        assert out["as_of"] == "2026-10-06"
        assert out["nav_date"] == out["shares_date"]
        assert out["not_official_disclosure"] is True

    def test_different_date_mismatch_no_value(self):
        """份额日无同日 NAV → 不给数字（不得用隔日 NAV 当同期）。"""
        with patch("etf_data._bridge_get", side_effect=self._bridge(
                [{"trade_date": "20261006", "fd_share": 5_000_000.0}],
                [{"date": "2026-09-30", "nav": 2.9}])):
            out = query_etf_est_net_assets("510300")
        assert out["status"] == "mismatch"
        assert out["value_yi"] is None
        assert out["shares_date"] == "2026-10-06" and out["nav_date"] == "2026-09-30"

    def test_missing_share_series(self):
        with patch("etf_data._bridge_get", return_value=None):
            out = query_etf_est_net_assets("510300")
        assert out["status"] == "missing" and out["value_yi"] is None


class TestShareHistoryCaliber:
    def test_caliber_note_and_flow_labels(self):
        env = {
            "status": "ok",
            "fund_share": [
                {"trade_date": "20261005", "fd_share": 1000.0},
                {"trade_date": "20261006", "fd_share": 1100.0},
            ],
            "fund_daily": [
                {"trade_date": "20261005", "open": 0.98, "high": 1.02, "low": 0.97,
                 "close": 1.0, "pre_close": 0.99, "pct_chg": 1.0, "vol": 80.0,
                 "amount": 800.0},
                {"trade_date": "20261006", "open": 1.0, "high": 1.1, "low": 0.9,
                 "close": 1.05, "pre_close": 1.0, "pct_chg": 5.0, "vol": 100.0,
                 "amount": 1000.0},
            ],
        }
        with patch("etf_data._bridge_get", return_value=env):
            out = query_etf_share_history("515050", days=20)
        assert out["available"] is True
        assert "估算" in out["caliber_note"]
        assert "价格涨跌幅 ≠ 资金净流入" in out["caliber_note"]
        rows = out["rows"]
        assert rows and rows[-1]["flow_est"] is not None  # 份额×均价估算保留
