"""limit_up_store：涨停扫描持久化。"""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

import limit_up_store as lus_mod

_SH = ZoneInfo("Asia/Shanghai")


def _today_yyyymmdd() -> str:
    return datetime.now(_SH).strftime("%Y%m%d")


def _days_ago_yyyymmdd(days: int) -> str:
    d = datetime.now(_SH).date() - timedelta(days=days)
    return d.strftime("%Y%m%d")


def _stock(
    symbol: str = "600000",
    name: str = "浦发银行",
    sector: str = "银行",
    max_consecutive: int = 1,
    close: float = 10.0,
) -> dict:
    return {
        "symbol": symbol,
        "name": name,
        "sector": sector,
        "market": "主板",
        "max_consecutive": max_consecutive,
        "total_appearances": 1,
        "first_date": "20260701",
        "last_date": "20260701",
        "is_st": False,
        "flags": {"sealed": True},
        "appearances": [{"close": close, "change_pct": 10.0, "date": "20260701"}],
        "float_mkt_cap": 1e10,
        "market_cap": 2e10,
    }


def _result(scan_date: str, stocks: list[dict] | None = None) -> dict:
    stocks = stocks or [_stock()]
    return {
        "scan_date": scan_date,
        "trading_days_scanned": 5,
        "market_breadth": {
            "total_unique_stocks": len(stocks),
            "avg_daily_count": float(len(stocks)),
            "days_with_limit_ups": 1,
            "consecutive_dist": {"1": len(stocks)},
            "seal_quality": {"early_seal_rate": 0.5},
        },
        "enrichment": {"tushare": True},
        "errors": [],
        "stocks": stocks,
    }


@pytest.fixture
def lus(tmp_path: Path):
    """Isolate both store and limit_up_store onto the same temp DB."""
    import lib.store as store_mod

    prev_store = store_mod._db_override
    prev_lus = lus_mod._db_override
    db = tmp_path / "research.db"
    store_mod._db_override = db
    lus_mod._db_override = None  # must follow store override (#1)
    try:
        yield lus_mod
    finally:
        store_mod._db_override = prev_store
        lus_mod._db_override = prev_lus


class TestDbPathIsolation:
    def test_follows_store_override(self, lus, tmp_path: Path):
        """#1: store._db_override alone must not touch real research.db."""
        import lib.store as store_mod

        assert lus._db_override is None
        assert lus._get_path() == store_mod._get_path()
        assert lus._get_path().parent == tmp_path

        sid = lus.save_scan(_result(_today_yyyymmdd()))
        assert sid > 0
        assert (tmp_path / "research.db").exists()

    def test_store_init_db_uses_same_path(self, lus, tmp_path: Path):
        import lib.store as store_mod

        store_mod.init_db()
        # limit_up tables created independently (not via init_db side-effect)
        lus.init_limit_up_db()
        import sqlite3

        c = sqlite3.connect(str(tmp_path / "research.db"))
        names = {
            r[0]
            for r in c.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "limit_up_scans" in names
        assert "collections" in names


class TestSaveAndUpsert:
    def test_save_and_get_scan(self, lus):
        date = _today_yyyymmdd()
        sid = lus.save_scan(_result(date, [_stock("600000"), _stock("600001")]))
        got = lus.get_scan(scan_id=sid)
        assert got is not None
        assert got["scan_date"] == date
        assert len(got["stocks"]) == 2
        assert got["breadth"]["total_unique_stocks"] == 2

    def test_upsert_same_date_replaces_stocks(self, lus):
        date = _today_yyyymmdd()
        sid1 = lus.save_scan(_result(date, [_stock("600000")]))
        sid2 = lus.save_scan(_result(date, [_stock("000001", name="平安银行")]))
        assert sid1 == sid2
        got = lus.get_scan(scan_date=date)
        assert got is not None
        symbols = {s["symbol"] for s in got["stocks"]}
        assert symbols == {"000001"}
        assert lus.get_stats()["total_stock_records"] == 1

    def test_upsert_preserves_created_at(self, lus):
        """Same-day re-scan must keep first-insert created_at."""
        date = _today_yyyymmdd()
        lus.save_scan(_result(date, [_stock("600000")]))
        first = lus.get_scan(scan_date=date)
        assert first is not None
        created = first["created_at"]
        assert created

        lus.save_scan(_result(date, [_stock("000001", name="平安银行")]))
        again = lus.get_scan(scan_date=date)
        assert again is not None
        assert again["created_at"] == created
        assert {s["symbol"] for s in again["stocks"]} == {"000001"}


class TestDeleteScan:
    def test_delete_removes_child_stocks(self, lus):
        """#4: delete_scan must not leave orphan limit_up_stocks rows."""
        date = _today_yyyymmdd()
        lus.save_scan(_result(date, [_stock("600000"), _stock("600001")]))
        assert lus.get_stats()["total_stock_records"] == 2

        assert lus.delete_scan(date) is True
        stats = lus.get_stats()
        assert stats["total_scans"] == 0
        assert stats["total_stock_records"] == 0
        assert lus.get_scan(scan_date=date) is None

    def test_delete_missing_returns_false(self, lus):
        assert lus.delete_scan("19990101") is False


class TestSectorTop:
    def test_filters_by_calendar_lookback(self, lus):
        """#2: YYYYMMDD cutoff — old scans excluded, recent included."""
        old = _days_ago_yyyymmdd(60)
        recent = _today_yyyymmdd()
        lus.save_scan(
            _result(old, [_stock("688001", name="旧票", sector="半导体", max_consecutive=2)])
        )
        lus.save_scan(
            _result(
                recent,
                [_stock("688002", name="新票", sector="半导体设备", max_consecutive=3)],
            )
        )

        rows = lus.get_sector_top("半导体", days=30, top_n=10)
        symbols = {r["symbol"] for r in rows}
        assert "688002" in symbols
        assert "688001" not in symbols

    def test_fuzzy_sector_match(self, lus):
        lus.save_scan(
            _result(
                _today_yyyymmdd(),
                [_stock("688002", sector="半导体设备", max_consecutive=2)],
            )
        )
        rows = lus.get_sector_top("半导体", days=7)
        assert len(rows) == 1
        assert rows[0]["symbol"] == "688002"


class TestQueries:
    def test_stock_history_and_list_scans(self, lus):
        d1 = _days_ago_yyyymmdd(2)
        d2 = _today_yyyymmdd()
        lus.save_scan(_result(d1, [_stock("600000")]))
        lus.save_scan(_result(d2, [_stock("600000", max_consecutive=2)]))

        hist = lus.get_stock_history("600000", limit=10)
        assert len(hist) == 2
        assert hist[0]["scan_date"] >= hist[1]["scan_date"]

        scans = lus.list_scans(limit=5)
        assert len(scans) == 2

    def test_breadth_trend_parses_json(self, lus):
        lus.save_scan(_result(_today_yyyymmdd()))
        trend = lus.get_breadth_trend(days=10)
        assert len(trend) == 1
        assert trend[0]["consecutive_dist"] == {"1": 1}
        assert trend[0]["seal_quality"]["early_seal_rate"] == 0.5

    def test_breadth_trend_uses_calendar_cutoff(self, lus):
        """days is calendar lookback, not SQL LIMIT."""
        lus.save_scan(_result(_days_ago_yyyymmdd(60), [_stock("600000")]))
        lus.save_scan(_result(_today_yyyymmdd(), [_stock("600001")]))
        trend = lus.get_breadth_trend(days=30)
        dates = {r["scan_date"] for r in trend}
        assert _today_yyyymmdd() in dates
        assert _days_ago_yyyymmdd(60) not in dates


class TestSaveGuards:
    def test_reject_empty_failed_scan(self, lus):
        with pytest.raises(ValueError, match="refusing empty"):
            lus.save_scan({
                "scan_date": _today_yyyymmdd(),
                "trading_days_scanned": 0,
                "stocks": [],
                "market_breadth": {},
                "errors": ["push2 down"],
            })

    def test_filter_params_empty_dict_persisted(self, lus):
        sid = lus.save_scan(_result(_today_yyyymmdd()), filter_params={})
        got = lus.get_scan(scan_id=sid)
        assert got is not None
        assert got["filter_params"] == {}

    def test_sector_top_escapes_like_wildcards(self, lus):
        """underscore in query must not act as SQL single-char wildcard."""
        lus.save_scan(
            _result(
                _today_yyyymmdd(),
                [
                    _stock("688001", name="A", sector="半导体"),
                    _stock("688002", name="B", sector="半X导体"),
                ],
            )
        )
        # Without escaping, "半_导体" would match both via `_` → any char
        rows = lus.get_sector_top("半_导体", days=7)
        symbols = {r["symbol"] for r in rows}
        assert "688001" not in symbols
        assert "688002" not in symbols
