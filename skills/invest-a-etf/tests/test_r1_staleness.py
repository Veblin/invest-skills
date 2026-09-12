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


@pytest.fixture
def estimated_calendar(monkeypatch):
    """估算日历（无 token / 取数失败）→ fetch_trade_cal 返回 is_estimated=True。

    上方 fake_calendar 恒返回 (dates, False)，估算路径无覆盖——而无 TUSHARE_TOKEN
    部署下它是常态（trade_cal 文档：「无 token/不可用/失败 → 自然日去周末估算」）。
    """
    import lib.trade_cal as tc

    state = {"n": 7}

    def fake(start, end):
        return ([f"2026{i:06d}" for i in range(1, state["n"] + 1)], True)

    monkeypatch.setattr(tc, "fetch_trade_cal", fake)
    return state


def test_trading_day_lag_estimated_calendar_degraded(estimated_calendar):
    """估算日历不是权威交易日历 → degraded=True 且滞后回退自然日。

    回归：trading_day_lag('20260930','20261009') 曾返回 (7, False)——长假后真实
    滞后 1-2 交易日，却被当作权威交易日数发布（估算值 7 还会跨过阈值告警）。
    """
    from freshness import trading_day_lag

    lag, degraded = trading_day_lag("20260930", "20261009")
    assert degraded is True, "估算日历必须降级（否则「日历不可用」标记永不出现）"
    assert lag == 9, "降级后应为自然日差，不得让估算值 7 冒充交易日数"


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


def test_is_trading_day_distrusts_estimated_calendar(monkeypatch):
    """估算日历对两个方向都不可信 → None（不得把法定假日误判为交易日）。

    回归：估算日历含 2026-10-01（国庆，周四）→ _is_trading_day 曾返回 True →
    C5 全等跳过被归因「疑数据源停更」，法定假日发出源冻结告警。原实现只对
    False 方向不信任（防调休工作日误判），True 方向同样不可信。
    """
    import lib.trade_cal as tc

    monkeypatch.setattr(tc, "fetch_trade_cal", lambda s, e: (["20261001"], True))
    assert sf._is_trading_day("20261001") is None      # 假日被估算当作工作日
    monkeypatch.setattr(tc, "fetch_trade_cal", lambda s, e: ([], True))
    assert sf._is_trading_day("20261003") is None      # 周末方向亦不可信
    monkeypatch.setattr(tc, "fetch_trade_cal", lambda s, e: (["20261009"], False))
    assert sf._is_trading_day("20261009") is True      # 权威日历：两方向均可用
    monkeypatch.setattr(tc, "fetch_trade_cal", lambda s, e: ([], False))
    assert sf._is_trading_day("20261001") is False


def test_sector_stale_note_estimated_calendar_marked(fixed_session, estimated_calendar):
    """估算日历 → 提示须带「粗判」标记，且数字为自然日差（不在阈值错侧给精确交易日数）。"""
    note = sf.sector_flow_stale_note("20260904")     # 自然日差 6 > 阈值 3
    assert note and "粗判" in note
    assert "6 天(自然日粗判)" in note


def test_weekly_stale_note_estimated_calendar_marked(fixed_session, estimated_calendar):
    """degraded 分支曾为死代码——is_estimated 被丢弃 → 该分支永不进入。"""
    note = inds.industry_snapshot_stale_note("20260901")   # 自然日差 9 > 阈值 7
    assert note and "粗判" in note
    assert "9 天(自然日粗判)" in note


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


def test_values_equal_nulls_equal_opt_in():
    """nulls_equal=True：双侧同空视为相等（停更检测语义）；默认契约不变。

    共享 helper 的「NULL 永不判等」来自写入门（任一侧缺失 → 保守判有变化 →
    写入恢复数据），对**检测**语义恰好相反：源发布空单元格时判「有变化」，
    冻结源不再告警。
    """
    from freshness import maps_equal, values_equal

    assert values_equal(None, None) is False            # 默认契约（写入门）不变
    assert values_equal((1.0, None), (1.0, None)) is False
    assert values_equal(None, None, nulls_equal=True) is True
    assert values_equal(None, 1.0, nulls_equal=True) is False
    assert values_equal((1.0, None), (1.0, None), nulls_equal=True) is True
    assert values_equal((1.0, None), (1.0, 2.0), nulls_equal=True) is False
    assert maps_equal({"a": (1.0, None)}, {"a": (1.0, None)}, nulls_equal=True) is True
    assert maps_equal({"a": (1.0, None)}, {"a": (1.0, 2.0)}, nulls_equal=True) is False


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


def test_weekly_unchanged_with_null_cells(isolated_store):
    """双侧同为 NULL 的单元格不得让「全同」判定翻转为「有变化」。

    回归：源长期冻结且在空单元格（涨跌幅/换手率）上发布 NULL 时，weekly 停更
    检测返回 False → 「数值与上一期全同，疑数据源停更」告警静默消失。
    """
    _ensure_weekly_table(isolated_store)
    _insert_weekly(isolated_store, [
        ("801080", "电子", "20260904", "20220831", 30.0, 3.0, None, None),
        ("801770", "通信", "20260904", "20220831", 40.0, 4.0, 1.5, None),
    ])
    _insert_weekly(isolated_store, [
        ("801080", "电子", "20260911", "20220907", 30.0, 3.0, None, None),
        ("801770", "通信", "20260911", "20220907", 40.0, 4.0, 1.5, None),
    ])
    assert inds.weekly_unchanged_vs_previous("20260911") is True
    # 单侧 NULL（数据退化）→ 仍须判「有变化」，不得误报全同
    _insert_weekly(isolated_store, [
        ("801080", "电子", "20260912", "20220914", 30.0, 3.0, 1.2, None),
        ("801770", "通信", "20260912", "20220914", 40.0, 4.0, 1.5, None),
    ])
    assert inds.weekly_unchanged_vs_previous("20260912") is False


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


# ── 源日期取值（R2 审查：真值陷阱致 src_date 恒 NULL）─────────────────────
# 上游帧由 `pd.to_datetime(..., errors="coerce").dt.date` 产出 → 缺失值是 NaT/NaN
# （**真值**），`row.get("发布日期") or row.get("日期")` 因此永不回落；pd.NA 更直接：
# `bool(pd.NA)` 抛 TypeError。任一情形都会把 src_date 写成 NULL，使停更检测退化为
# 按采集日算滞后 —— 源冻结数年也报「无异常」（R1-F2 的成果被静默回退）。

def test_pick_src_date_falls_back_when_publish_date_is_nat():
    import pandas as pd
    row = {"发布日期": pd.NaT, "日期": "2022-11-04"}
    assert inds._pick_src_date(row) == "20221104"


def test_pick_src_date_treats_nan_and_na_as_missing():
    import pandas as pd
    assert inds._pick_src_date({"发布日期": float("nan"), "日期": pd.NaT}) is None
    # bool(pd.NA) 抛 TypeError —— 判定不得让它冒出来（可空列会变硬失败）
    assert inds._pick_src_date({"发布日期": pd.NA}) is None


def test_pick_src_date_prefers_publish_date_and_tolerates_absence():
    assert inds._pick_src_date({"发布日期": "2022-11-04", "日期": "2022-11-06"}) == "20221104"
    assert inds._pick_src_date({"日期": "2022-11-06"}) == "20221106"
    assert inds._pick_src_date({}) is None
