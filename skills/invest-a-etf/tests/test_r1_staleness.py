"""R1/T7 回归：停源预警 helper（as_of 滞后 + 全同检测）。离线，isolated_store 注入。

T7-1 sector-flow as_of 滞后 / T7-2 sector-flow 全同 / T7-3 weekly 全同 + industry-pe staleness。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterator

import pytest

import industry_snapshot as inds  # noqa: E402  (conftest 已注入 scripts/lib)
import sector_flow as sf  # noqa: E402


@pytest.fixture
def isolated_store(tmp_path: Path) -> Iterator[Any]:
    """临时 SQLite 隔离（仿 test_sector_flow.isolated_store）。"""
    from lib import store as store_mod

    previous = store_mod._db_override
    store_mod._db_override = tmp_path / "test_research.db"
    try:
        store_mod.init_db()
        yield store_mod
    finally:
        store_mod._db_override = previous


@pytest.fixture
def fixed_session(monkeypatch):
    """固定「最近交易日」= 2026-09-10，使滞后阈值可确定断言。"""
    import dates

    monkeypatch.setattr(dates, "shanghai_session_date", lambda: "20260910")


# ── T7-1：sector-flow as_of 滞后 ─────────────────────────────────────────

def test_sector_stale_note_threshold(fixed_session):
    note = sf.sector_flow_stale_note("20260904")     # lag 6 > 阈值 5
    assert note and "滞后 6 天" in note
    assert sf.sector_flow_stale_note("20260906") is None   # lag 4 ≤ 5
    assert sf.sector_flow_stale_note("20260910") is None   # lag 0
    assert sf.sector_flow_stale_note(None) is None
    assert sf.sector_flow_stale_note("bad-date") is None   # 不可解析 → 不误报


# ── T7-2：sector-flow 全同 vs 上一快照 ───────────────────────────────────

_ROWS_SAME = [("半导体", 3, 1.5), ("半导体", 10, 2.0), ("元件", 3, -0.5)]


def _insert_sf(store_mod, date: str, rows: list[tuple[str, int, float]]) -> None:
    with store_mod._connection() as c:
        c.executemany(
            "INSERT OR REPLACE INTO sector_flow_snapshots "
            "(date, industry, window_days, net_flow) VALUES (?, ?, ?, ?)",
            [(date, ind, wd, net) for ind, wd, net in rows],
        )
        c.commit()


def test_snapshot_unchanged_vs_previous(isolated_store):
    sf._ensure_table()
    _insert_sf(isolated_store, "20260908", _ROWS_SAME)
    _insert_sf(isolated_store, "20260909", _ROWS_SAME)          # 与上一日全同
    assert sf.snapshot_unchanged_vs_previous("20260909") is True
    _insert_sf(isolated_store, "20260910",
               [("半导体", 3, 1.5), ("半导体", 10, 2.0), ("元件", 3, 0.1)])  # 有差异
    assert sf.snapshot_unchanged_vs_previous("20260910") is False
    assert sf.snapshot_unchanged_vs_previous("20260901") is None  # 非最新/无上一日
    # 名单增删 → False（保守，不误报停更）
    _insert_sf(isolated_store, "20260911",
               _ROWS_SAME + [("证券", 3, 0.7)])
    assert sf.snapshot_unchanged_vs_previous("20260911") is False


# ── T7-3：weekly 全同 + industry-pe staleness ───────────────────────────

_WEEKLY_SAME = [
    ("801080", "电子", "20260904", 30.0, 3.0, 1.0, 2.0),
    ("801770", "通信", "20260904", 40.0, 4.0, -1.0, 1.5),
]


def _ensure_weekly_table(store_mod) -> None:
    with store_mod._connection() as c:
        c.execute(
            "CREATE TABLE IF NOT EXISTS industry_weekly ("
            "index_code TEXT, index_name TEXT, date TEXT, pe REAL, pb REAL, "
            "chg_pct REAL, turnover_pct REAL, dividend_yield REAL, mkt_cap REAL, "
            "PRIMARY KEY (index_code, date))"
        )
        c.commit()


def _insert_weekly(store_mod, rows) -> None:
    with store_mod._connection() as c:
        c.executemany(
            "INSERT OR REPLACE INTO industry_weekly "
            "(index_code, index_name, date, pe, pb, chg_pct, turnover_pct) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        c.commit()


def test_weekly_unchanged_and_stale(isolated_store, fixed_session):
    _ensure_weekly_table(isolated_store)
    _insert_weekly(isolated_store, _WEEKLY_SAME)
    _insert_weekly(isolated_store, [
        ("801080", "电子", "20260911", 30.0, 3.0, 1.0, 2.0),
        ("801770", "通信", "20260911", 40.0, 4.0, -1.0, 1.5),
    ])
    assert inds.weekly_unchanged_vs_previous("20260911") is True
    _insert_weekly(isolated_store, [("801080", "电子", "20260912", 31.0, 3.0, 1.0, 2.0),
                                    ("801770", "通信", "20260912", 40.0, 4.0, -1.0, 1.5)])
    assert inds.weekly_unchanged_vs_previous("20260912") is False
    assert inds.weekly_unchanged_vs_previous("20260801") is None

    assert inds.industry_snapshot_stale_note("20260904") is None      # lag 6 ≤ 7
    note = inds.industry_snapshot_stale_note("20260801")              # lag 40 > 7
    assert note and "滞后 40 天" in note
    assert inds.industry_snapshot_stale_note(None) is None
