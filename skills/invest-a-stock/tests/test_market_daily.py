"""market_daily 落库三函数测试 — isolated_store fixture，不联网。"""

from __future__ import annotations

import pytest

from lib import store


def _rows(date: str, codes: list[str]) -> list[dict]:
    return [
        {
            "date": date,
            "ts_code": c,
            "open": 10.0, "high": 11.0, "low": 9.0, "close": 10.5,
            "pre_close": 10.0, "pct_chg": 5.0, "vol": 100000.0,
            "amount": 1000000.0, "turnover_rate": 2.5,
        }
        for c in codes
    ]


class TestMarketDailyStore:
    def test_save_load_roundtrip(self, isolated_store):
        n = store.save_market_daily(_rows("2026-08-13", ["600176.SH", "000001.SZ"]))
        assert n == 2
        assert store.latest_market_daily_date() == "2026-08-13"
        rows = store.load_market_daily(dates=["2026-08-13"])
        assert {r["ts_code"] for r in rows} == {"600176.SH", "000001.SZ"}
        assert all(r["date"] == "2026-08-13" for r in rows)

    def test_merge_idempotent(self, isolated_store):
        """同日二次写入：非 NULL 覆盖、NULL 保留旧值；无重复行。"""
        store.save_market_daily(_rows("2026-08-13", ["600176.SH"]))
        # 二次写入：amount None（模拟部分 fetch 失败）→ 旧值保留
        partial = _rows("2026-08-13", ["600176.SH"])
        partial[0]["amount"] = None
        partial[0]["close"] = 11.0  # 非 NULL 新值覆盖
        store.save_market_daily(partial)
        rows = store.load_market_daily(dates=["2026-08-13"])
        assert len(rows) == 1, f"应无重复行，实际 {len(rows)}"
        assert rows[0]["close"] == 11.0
        assert rows[0]["amount"] == 1000000.0  # NULL 不覆盖旧值

    def test_dates_and_empty(self, isolated_store):
        assert store.market_daily_dates() == set()
        assert store.latest_market_daily_date() is None
        store.save_market_daily(_rows("2026-08-13", ["600176.SH"]))
        store.save_market_daily(_rows("2026-08-14", ["600176.SH"]))
        assert store.market_daily_dates() == {"2026-08-13", "2026-08-14"}
        assert store.latest_market_daily_date() == "2026-08-14"

    def test_load_dates_empty(self, isolated_store):
        assert store.load_market_daily(dates=[]) == []
        assert store.load_market_daily(dates=["2026-08-13"]) == []
