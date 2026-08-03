"""Regression tests: _safe_collect_* 必须先升序再取最新（Tushare 等源为降序）。

v0.2.3 review fix #2：修复前降序数据被 data[-1] 当"最新"，报价滞后 7-10 个
交易日，kline first/last_date 颠倒。
"""

from __future__ import annotations

import query_data
from query_data import _safe_collect_kline, _safe_collect_quote, _safe_collect_valuation


def _desc_rows() -> list[dict]:
    """Tushare 风格降序行情行（最新在前）。"""
    return [
        {"trade_date": "20260730", "close": 9.8, "pct_chg": -1.0,
         "pe_ttm": 8.0, "pb": 1.0, "total_mv": 1e10},
        {"trade_date": "20260731", "close": 10.2, "pct_chg": 1.0,
         "pe_ttm": 8.5, "pb": 1.1, "total_mv": 1e10},
        {"trade_date": "20260801", "close": 10.5, "pct_chg": 2.0,
         "pe_ttm": 9.0, "pb": 1.2, "total_mv": 1e10},
    ]


class TestSafeCollectQuoteOrdering:
    def test_descending_list_uses_newest(self, monkeypatch):
        monkeypatch.setattr(query_data, "get_quote", lambda s: {
            "data": _desc_rows(),
            "status": "available",
            "_meta": {"source": "tushare.daily"},
        })
        r = _safe_collect_quote("600176")
        assert r["price"] == 10.5
        assert r["change_pct"] == 2.0
        assert r["pe_ttm"] == 9.0

    def test_dict_snapshot_untouched(self, monkeypatch):
        monkeypatch.setattr(query_data, "get_quote", lambda s: {
            "data": {"close": 10.5, "pct_chg": 2.0},
            "status": "available",
            "_meta": {"source": "tencent_finance"},
        })
        r = _safe_collect_quote("600176")
        assert r["price"] == 10.5


class TestSafeCollectKlineOrdering:
    def test_descending_kline_dates_not_swapped(self, monkeypatch):
        monkeypatch.setattr(query_data, "get_kline", lambda s: {
            "data": _desc_rows(),
            "status": "available",
            "_meta": {"source": "tushare.daily"},
        })
        r = _safe_collect_kline("600176")
        assert r["first_date"] == "20260730"
        assert r["last_date"] == "20260801"
        assert r["rows"] == 3
        assert r["data"][0]["trade_date"] <= r["data"][-1]["trade_date"]


class TestSafeCollectValuationOrdering:
    def test_descending_valuation_uses_newest_pe(self, monkeypatch):
        monkeypatch.setattr(query_data, "get_valuation", lambda s: {
            "data": _desc_rows(),
            "status": "available",
            "_meta": {"source": "tushare.daily_basic"},
        })
        r = _safe_collect_valuation("600176")
        assert r["pe_current"] == 9.0
        assert r["pb_current"] == 1.2
        assert r["pe_median"] == 8.5
        assert r["history_available"] is True
