"""Tests for group_daily_by_ts_code + build_stock_kline daily_by_ts path."""

from __future__ import annotations

import pandas as pd

from kline_source import build_stock_kline, group_daily_by_ts_code


def _daily_two_stocks() -> pd.DataFrame:
    rows = []
    for code, base in [("600176.SH", 10.0), ("000001.SZ", 20.0)]:
        for i, d in enumerate(["20260710", "20260711", "20260712"]):
            p = base + i
            rows.append({
                "ts_code": code,
                "trade_date": d,
                "open": p,
                "high": p + 0.5,
                "low": p - 0.3,
                "close": p,
                "vol": 1e6,
                "amount": 1e8,
            })
    return pd.DataFrame(rows)


class TestGroupDailyByTsCode:
    def test_none_or_empty(self):
        assert group_daily_by_ts_code(None) == {}  # type: ignore[arg-type]
        assert group_daily_by_ts_code(pd.DataFrame()) == {}

    def test_missing_ts_code_col(self):
        assert group_daily_by_ts_code(pd.DataFrame({"a": [1]})) == {}

    def test_groups_two_codes(self):
        g = group_daily_by_ts_code(_daily_two_stocks())
        assert set(g) == {"600176.SH", "000001.SZ"}
        assert len(g["600176.SH"]) == 3


class TestBuildStockKlineDailyByTs:
    def test_lookup_via_pregroup_already_qfq(self):
        daily = _daily_two_stocks()
        by_ts = group_daily_by_ts_code(daily)
        kline = build_stock_kline(
            daily,
            adj_factor_df=None,
            ts_code="600176.SH",
            min_bars=2,
            already_qfq=True,
            daily_by_ts=by_ts,
        )
        assert kline is not None
        assert len(kline) == 3
        assert "close_qfq" in kline.columns

    def test_missing_code_returns_none(self):
        by_ts = group_daily_by_ts_code(_daily_two_stocks())
        assert build_stock_kline(
            None,  # type: ignore[arg-type]
            None,
            "999999.SH",
            min_bars=1,
            already_qfq=True,
            daily_by_ts=by_ts,
        ) is None
