"""R1/T7(+) 停源预警回归：交易日口径滞后 + 源发布日期 + skip 路径停更信号。离线。

H2 修正要点（R1 验收审查）：
- F7：滞后按**交易日**计（长假后不误报）；日历不可用降级自然日并在文本标注
- F2：weekly 滞后按**源发布日期**判（采集日 ≠ 源日期）
- F6：sector 停更信号挂在「权威交易日 + 全等跳过」路径（写后比较已删除）
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterator

import pytest

import industry_snapshot as inds  # noqa: E402  (conftest 已注入 scripts/lib)
import sector_flow as sf  # noqa: E402


@pytest.fixture
def isolated_store(tmp_path: Path) -> Iterator[Any]:
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
    """固定「最近交易日」= 2026-09-10。"""
    import dates

    monkeypatch.setattr(dates, "shanghai_session_date", lambda: "20260910")


@pytest.fixture
def fake_calendar(monkeypatch):
    """控制 (as_of, session] 交易日计数（freshness 调用时 import lib.trade_cal）。"""
    import lib.trade_cal as tc

    state = {"n": 2, "raise": False}

    def fake(start, end):
        if state["raise"]:
            raise RuntimeError("calendar offline")
        return ([f"2026{i:06d}" for i in range(state["n"])], False)

    monkeypatch.setattr(tc, "fetch_trade_cal", fake)
    return state


# ── T7-1：sector-flow as_of 滞后（交易日口径）─────────────────────────────

def test_sector_stale_note_trading_day_caliber(fixed_session, fake_calendar):
    fake_calendar["n"] = 4
    note = sf.sector_flow_stale_note("20260904")
    assert note and "4 个交易日" in note
    fake_calendar["n"] = 2          # 长假场景：自然日差大但交易日少 → 不告警
    assert sf.sector_flow_stale_note("20260904") is None


def test_sector_stale_note_degraded_fallback(fixed_session, fake_calendar):
    fake_calendar["raise"] = True   # 日历不可用 → 自然日粗判且显式标注
    note = sf.sector_flow_stale_note("20260904")    # native lag 6 > 3
    assert note and "粗判" in note
    assert sf.sector_flow_stale_note("20260910") is None   # lag 0 不告警


def test_sector_stale_note_unparseable(fixed_session, fake_calendar):
    fake_calendar["n"] = 9
    assert sf.sector_flow_stale_note(None) is None
    assert sf.sector_flow_stale_note("bad-date") is None


# ── T7-2（F6 修正）：skip 路径停更信号 ────────────────────────────────────

_WINDOWS = {1: 0.5, 3: 1.5, 5: 0.9, 10: 2.0}
_WINDOWS_B = {1: 0.1, 3: -0.5, 5: 0.2, 10: -1.0}
_ROWS_SAME = [
    (ind, wd, val)
    for ind, vals in (("半导体", _WINDOWS), ("元件", _WINDOWS_B))
    for wd, val in vals.items()
]


def _insert_sf(store_mod, date: str, rows) -> None:
    with store_mod._connection() as c:
        c.executemany(
            "INSERT OR REPLACE INTO sector_flow_snapshots "
            "(date, industry, window_days, net_flow) VALUES (?, ?, ?, ?)",
            [(date, ind, wd, net) for ind, wd, net in rows],
        )
        c.commit()


def _snapshot(date: str) -> dict:
    return {
        "date": date, "available": True, "errors": [],
        "industries": {
            "半导体": {wd: {"net": v} for wd, v in _WINDOWS.items()},
            "元件": {wd: {"net": v} for wd, v in _WINDOWS_B.items()},
        },
    }


def test_sector_skip_path_stale_suspect(isolated_store, monkeypatch):
    sf._ensure_table()
    _insert_sf(isolated_store, "20260908", _ROWS_SAME)

    monkeypatch.setattr(sf, "_is_trading_day", lambda d: True)
    out = sf.save_sector_flow_snapshot(_snapshot("20260909"))
    assert out["skipped"] is True
    assert out.get("stale_suspect") is True and "停更" in out["note"]

    monkeypatch.setattr(sf, "_is_trading_day", lambda d: False)
    out2 = sf.save_sector_flow_snapshot(_snapshot("20260909"))
    assert out2["skipped"] and out2.get("stale_suspect") is False
    assert "非交易日" in out2["note"]

    monkeypatch.setattr(sf, "_is_trading_day", lambda d: None)
    out3 = sf.save_sector_flow_snapshot(_snapshot("20260909"))
    assert out3["skipped"] and out3.get("stale_suspect") is False
    assert "盘前" in out3["note"]


# ── T7-3：weekly 全同 + 源日期滞后 ───────────────────────────────────────

def _ensure_weekly_table(store_mod) -> None:
    with store_mod._connection() as c:
        c.execute(
            "CREATE TABLE IF NOT EXISTS industry_weekly ("
            "index_code TEXT, index_name TEXT, date TEXT, pe REAL, "
            "pb REAL, chg_pct REAL, turnover_pct REAL, dividend_yield REAL, "
            "mkt_cap REAL, PRIMARY KEY (index_code, date))"
        )
        try:  # 与生产 collect 相同的幂等迁移（init_db 已有旧表时 CREATE 不生效）
            c.execute("ALTER TABLE industry_weekly ADD COLUMN src_date TEXT")
        except Exception:
            pass
        c.commit()


def _insert_weekly(store_mod, rows) -> None:
    with store_mod._connection() as c:
        c.executemany(
            "INSERT OR REPLACE INTO industry_weekly "
            "(index_code, index_name, date, src_date, pe, pb, chg_pct, turnover_pct) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        c.commit()


_WEEKLY_BASE = [
    ("801080", "电子", "20260904", "20220831", 30.0, 3.0, 1.0, 2.0),
    ("801770", "通信", "20260904", "20220831", 40.0, 4.0, -1.0, 1.5),
]


def test_weekly_unchanged_semantics(isolated_store):
    _ensure_weekly_table(isolated_store)
    _insert_weekly(isolated_store, _WEEKLY_BASE)
    _insert_weekly(isolated_store, [
        ("801080", "电子", "20260911", "20220907", 30.0, 3.0, 1.0, 2.0),
        ("801770", "通信", "20260911", "20220907", 40.0, 4.0, -1.0, 1.5),
    ])
    assert inds.weekly_unchanged_vs_previous("20260911") is True
    _insert_weekly(isolated_store, [
        ("801080", "电子", "20260912", "20220914", 31.0, 3.0, 1.0, 2.0),
        ("801770", "通信", "20260912", "20220914", 40.0, 4.0, -1.0, 1.5),
    ])
    assert inds.weekly_unchanged_vs_previous("20260912") is False
    assert inds.weekly_unchanged_vs_previous("20260801") is None


def test_weekly_stale_uses_src_date(fixed_session, fake_calendar):
    fake_calendar["n"] = 10
    note = inds.industry_snapshot_stale_note("20260911", src_date="20221104")
    assert note and "源数据日期 20221104" in note      # F2：按源发布日期判滞后
    fake_calendar["n"] = 5
    assert inds.industry_snapshot_stale_note("20260911", src_date="20221104") is None
    fake_calendar["n"] = 10
    fallback = inds.industry_snapshot_stale_note("20260601")   # src 缺失 → 回退标注
    assert fallback and "回退粗判" in fallback
    assert inds.industry_snapshot_stale_note(None, src_date=None) is None


def test_normalize_src_date():
    assert inds._normalize_src_date("2022-11-04") == "20221104"
    assert inds._normalize_src_date("20221104") == "20221104"
    assert inds._normalize_src_date(None) is None
    assert inds._normalize_src_date("") is None
