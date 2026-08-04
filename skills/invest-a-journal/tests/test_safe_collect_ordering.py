"""Regression tests: _safe_collect_* 必须先升序再取最新（Tushare 等源为降序）。

v0.2.3 review fix #2：修复前降序数据被 data[-1] 当"最新"，报价滞后 7-10 个
交易日，kline first/last_date 颠倒。
"""

from __future__ import annotations

import query_data
from query_data import (_safe_collect_kline, _safe_collect_quote, _safe_collect_valuation, _status_from_raw)


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


class TestSafeCollectValuationNegativeFilter:
    """F3: 亏损期负 PE 不得进入分位/中位数总体（CLAUDE.md P0-2 口径）。"""

    def test_negative_pe_excluded(self, monkeypatch):
        rows = [
            {"trade_date": "20260101", "pe_ttm": 8.0, "pb": 1.0},
            {"trade_date": "20260201", "pe_ttm": -3.0, "pb": 0.8},  # 亏损期
            {"trade_date": "20260301", "pe_ttm": 9.0, "pb": 1.1},
        ]
        monkeypatch.setattr(query_data, "get_valuation", lambda s: {
            "data": rows, "status": "available",
            "_meta": {"source": "tushare.daily_basic", "success": True},
        })
        r = _safe_collect_valuation("600176")
        assert r["pe_current"] == 9.0
        assert r["pe_median"] == 8.5  # (8+9)/2，负值不拖低中位数
        assert r["pe_percentile"] == 100.0
        assert r["history_rows"] == 2
        assert r["pe_date"] == "20260301"  # 最新正 PE 的报告期

    def test_latest_loss_current_falls_back_to_last_positive(self, monkeypatch):
        rows = [
            {"trade_date": "20260101", "pe_ttm": -5.0, "pb": 0.7},
            {"trade_date": "20260201", "pe_ttm": 8.0, "pb": 1.0},
            {"trade_date": "20260301", "pe_ttm": -2.0, "pb": 0.9},  # 最新期亏损
        ]
        monkeypatch.setattr(query_data, "get_valuation", lambda s: {
            "data": rows, "status": "available",
            "_meta": {"source": "tushare.daily_basic", "success": True},
        })
        r = _safe_collect_valuation("600176")
        assert r["pe_current"] == 8.0  # 最近正 PE（与 valuation.py 一致）
        assert r["pe_date"] == "20260201"  # 亏损期回退旧值仍带日期上下文
        assert r["pe_median"] == 8.0
        assert r["pe_percentile"] == 100.0


class TestStatusFromRaw:
    """F9b: available 判定用维度级 success（source_group 恒不存在于源条目）。"""

    def test_available_with_success(self):
        assert _status_from_raw({"status": "available",
                                 "_meta": {"success": True}}) == "available"

    def test_available_without_success_degraded(self):
        assert _status_from_raw({"status": "available",
                                 "_meta": {"success": False}}) == "degraded"

    def test_partial_passthrough(self):
        assert _status_from_raw({"status": "partial", "_meta": {}}) == "partial"

    def test_missing_default(self):
        assert _status_from_raw({"status": "missing"}) == "missing"
        assert _status_from_raw({}) == "missing"
