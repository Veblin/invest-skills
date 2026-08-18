"""v0.2.3 回归：etf_data 缓存语义修复（code-review #3/#5/#6/#7）。

无网络：monkeypatch _bridge_get / spot 缓存 / data_bridge getter。
"""

from __future__ import annotations

import datetime
import sys
from pathlib import Path

import pandas as pd
import pytest

_LIB = Path(__file__).resolve().parent.parent / "scripts" / "lib"
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

from etf_data import (  # noqa: E402
    _lookup_etf_spot_row,
    clear_etf_spot_cache,
    prefetch_etf_spot,
    query_etf_kline,
    query_etf_share_history,
)


@pytest.fixture(autouse=True)
def _reset_spot_cache():
    clear_etf_spot_cache()
    yield
    clear_etf_spot_cache()


@pytest.fixture(autouse=True)
def _pin_shanghai_today(monkeypatch):
    """固定"今日"，使窗口切片与真实墙钟解耦。

    fixture 日期固定（2026-05-01 起），而 query_etf_kline 的窗口起点 =
    今日 - N 自然日，随真实日期漂移：2026-08-12 起窗口起点越过 fixture 首行，
    裁剪分支（aligned_rows 切片）首次被执行，暴露了未复权路径的 j+1 越界
    （list index out of range）。钉住今日保证裁剪分支每次都执行——这正是
    该缺陷逃逸时未被覆盖的路径。
    """
    import etf_data as _ed

    fixed_today = datetime.date(2026, 8, 12)
    monkeypatch.setattr(
        _ed, "shanghai_days_ago",
        lambda n: (fixed_today - datetime.timedelta(days=n)).strftime("%Y%m%d"),
    )


# ---------------------------------------------------------------------------
# #5: prefetch_etf_spot 空表/失败语义（[] is not None → 曾误报成功）
# ---------------------------------------------------------------------------

def test_prefetch_false_on_empty_rows(monkeypatch):
    """L2 返回 []（空表）→ False，不得伪装成功。"""
    import data_bridge

    monkeypatch.setattr(data_bridge, "get_etf_spot_rows", lambda: [])
    assert prefetch_etf_spot() is False


def test_prefetch_false_on_none(monkeypatch):
    """L2 失败（None）→ False。"""
    import data_bridge

    monkeypatch.setattr(data_bridge, "get_etf_spot_rows", lambda: None)
    assert prefetch_etf_spot() is False


def test_prefetch_true_on_rows(monkeypatch):
    """非空表 → True。"""
    import data_bridge

    monkeypatch.setattr(data_bridge, "get_etf_spot_rows", lambda: [{"代码": "510300"}])
    assert prefetch_etf_spot() is True


# ---------------------------------------------------------------------------
# #6: _lookup_etf_spot_row — L1 新鲜 miss → 穿透 L2（新上市 ETF 30s 内可见）
# ---------------------------------------------------------------------------

def test_lookup_l1_miss_consults_l2(monkeypatch):
    """L1 新鲜但缺符号 X → 继续查 L2，找到更晚更新的数据。"""
    df = pd.DataFrame([{"代码": "510300", "最新价": 4.0}])
    monkeypatch.setattr("etf_data._peek_etf_spot_df", lambda: df)

    def bridge(getter, *a):
        if getter == "get_etf_spot_rows":
            return [{"代码": "510500", "最新价": 5.0}]
        return None

    monkeypatch.setattr("etf_data._bridge_get", bridge)
    row, err = _lookup_etf_spot_row("510500")
    assert err is None
    assert row["代码"] == "510500"


def test_lookup_l1_hit_does_not_need_l2(monkeypatch):
    """L1 命中 → 不触发 L2/网络。"""
    df = pd.DataFrame([{"代码": "510300", "最新价": 4.0}])
    monkeypatch.setattr("etf_data._peek_etf_spot_df", lambda: df)
    called = {"n": 0}

    def bridge(getter, *a):
        called["n"] += 1
        return None

    monkeypatch.setattr("etf_data._bridge_get", bridge)
    row, err = _lookup_etf_spot_row("510300")
    assert err is None
    assert called["n"] == 0


def test_lookup_l1_miss_l2_miss_returns_not_found(monkeypatch):
    """L1/L2 均无该符号 → not found（不再吞成 empty response）。"""
    df = pd.DataFrame([{"代码": "510300", "最新价": 4.0}])
    monkeypatch.setattr("etf_data._peek_etf_spot_df", lambda: df)

    def bridge(getter, *a):
        if getter == "get_etf_spot_rows":
            return [{"代码": "510300", "最新价": 4.0}]
        return None

    monkeypatch.setattr("etf_data._bridge_get", bridge)
    row, err = _lookup_etf_spot_row("510500")
    assert row is None
    assert "not found" in err


# ---------------------------------------------------------------------------
# #3: query_etf_kline 超窗显式告警（days 超出 fetch 窗口不再静默截断）
# ---------------------------------------------------------------------------

def _nav_env() -> dict:
    rows = []
    for i in range(40):
        d = (datetime.date(2026, 5, 1) + datetime.timedelta(days=i)).isoformat()
        rows.append({"date": d, "nav": 1.0 + i * 0.01, "change_pct": 1.0})
    return {"status": "ok", "source": "fund_etf_fund_info_em", "rows": rows, "error": None}


def _bridge_nav_only(getter, *a):
    if getter == "get_etf_nav":
        return _nav_env()
    return None  # adj_factor / index_daily → 降级 None


def test_kline_over_window_sets_note(monkeypatch):
    """days=600（>700 自然日窗口）→ 显式 note，不再静默截断。"""
    monkeypatch.setattr("etf_data._bridge_get", _bridge_nav_only)
    out = query_etf_kline("510300", days=600)
    assert out["status"] == "available"
    assert "超过取数上限" in out["note"]


def test_kline_within_window_no_note(monkeypatch):
    """常规窗口（days=60）→ 无 note，行为不变。"""
    monkeypatch.setattr("etf_data._bridge_get", _bridge_nav_only)
    out = query_etf_kline("510300", days=60)
    assert out["status"] == "available"
    assert "note" not in out


def test_kline_exposes_latest_nav_date(monkeypatch):
    """修复 #4：结果暴露数据末端日期（L2 缓存命中时可能滞后，供识别陈旧）。"""
    monkeypatch.setattr("etf_data._bridge_get", _bridge_nav_only)
    out = query_etf_kline("510300", days=60)
    assert out["status"] == "available"
    assert out["latest_nav_date"] is not None
    assert out["latest_nav_date"] == out["nav_history"][-1]["date"]


def test_kline_switch_row_as_first_row_no_fake_jump(monkeypatch):
    """修复 #2：复权切换行恰为窗口首行时，上下文行保证 prev_nav 连续性校验。

    旧行为：切片首行 = 切换行（prev_nav=None）→ 维持旧因子 → 假低点 + 假跳变。
    新行为：切片多取 2 自然日上下文，切换日采用新因子，序列连续。
    """
    # 构造：切换行（除权日，净值已拆后）位于窗口起点附近；days=60 → 窗口 102 自然日
    adj_env = {
        "status": "ok",
        "adj_map": {"20260101": 1.0, "20260102": 1.0, "20260103": 1.0,
                    "20260104": 3.0, "20260105": 3.0},
    }
    # 净值序列：除权日 01-02 已拆后（3.0 → 1.0），其后连续
    rows = []
    base = datetime.date(2026, 1, 1)
    navs_seq = [3.0, 1.0, 1.01, 1.02, 1.03, 1.04, 1.05, 1.06]
    for i, nav in enumerate(navs_seq):
        d = (base + datetime.timedelta(days=i)).isoformat()
        rows.append({"date": d, "nav": nav, "change_pct": 1.0})
    # 从 L2 缓存的角度：fetch_etf_nav 返回 700 自然日窗口（1 月初起），窗口起点
    # 恰在除权日（01-02）附近 —— days 选大值使 start 落在 01-02 前后
    env = {"status": "ok", "source": "fund_etf_fund_info_em", "rows": rows, "error": None}

    def bridge(getter, *a):
        if getter == "get_etf_nav":
            return env
        if getter == "get_etf_adj_factor":
            return adj_env
        return None

    monkeypatch.setattr("etf_data._bridge_get", bridge)
    out = query_etf_kline("510300", days=470)
    assert out["status"] == "available"
    # 无 0.33 假低点（旧行为 navs[0] ≈ 1.0/3.0）；复权后序列连续
    assert out["nav_history"][0]["nav"] > 0.5
    assert out["latest_nav"] == pytest.approx(1.06, abs=1e-3)


# ---------------------------------------------------------------------------
# #7: query_etf_share_history 超窗显式标注（不再静默少返回）
# ---------------------------------------------------------------------------

def _share_env(n_rows: int = 10) -> dict:
    """与 fetch_etf_share_history 的 records 契约一致（list of dicts）。"""
    base = datetime.date(2026, 7, 1)
    dates = [(base + datetime.timedelta(days=i)).strftime("%Y%m%d") for i in range(n_rows)]
    fund_share = [{"trade_date": d, "fd_share": 1e5 + i} for i, d in enumerate(dates)]
    fund_daily = [
        {"trade_date": d, "open": 4.0, "high": 4.1, "low": 3.9,
         "close": 4.0 + i * 0.01, "pre_close": 3.99, "pct_chg": 0.1,
         "vol": 10000, "amount": 40000}
        for i, d in enumerate(dates)
    ]
    return {"status": "ok", "fund_share": fund_share, "fund_daily": fund_daily, "note": None}


def _bridge_share_only(getter, *a):
    if getter == "get_etf_share_history":
        return _share_env()
    return None


def test_share_history_clipped_sets_note(monkeypatch):
    """days=200 超出 10 行可用数据 → note 标注截断，不再静默少返回。"""
    monkeypatch.setattr("etf_data._bridge_get", _bridge_share_only)
    out = query_etf_share_history("515050", days=200)
    assert out["available"] is True
    assert "取数上限" in out["note"]
    assert out["summary"]["row_count"] < 200


def test_share_history_within_window_no_note(monkeypatch):
    """常规窗口（days=5 ≤ 10 行）→ 无 note。"""
    monkeypatch.setattr("etf_data._bridge_get", _bridge_share_only)
    out = query_etf_share_history("515050", days=5)
    assert out["available"] is True
    assert "note" not in out
    assert out["summary"]["row_count"] == 5


def _share_env_with_reversal(n_rows: int = 12) -> dict:
    """前段净流入 + 近端 5 行净流出（份额转向，batch-test P1-4）。

    份额单位万份：+20000 万份 × 均价 4 元 / 1e4 = +8 亿；-15000 → -6 亿。
    前 6 行累计 +48 亿，后 5 行累计 -30 亿 → 整体净流入但近端转流出。
    """
    base = datetime.date(2026, 7, 1)
    dates = [(base + datetime.timedelta(days=i)).strftime("%Y%m%d")
             for i in range(n_rows)]
    shares = []
    s = 1_000_000.0
    for i in range(n_rows):
        s += 20000.0 if i < n_rows - 5 else -15000.0
        shares.append(round(s, 2))
    fund_share = [{"trade_date": d, "fd_share": sh} for d, sh in zip(dates, shares)]
    fund_daily = [
        {"trade_date": d, "open": 4.0, "high": 4.1, "low": 3.9,
         "close": 4.0, "pre_close": 3.99, "pct_chg": 0.1,
         "vol": 10000, "amount": 40000}
        for d in dates
    ]
    return {"status": "ok", "fund_share": fund_share,
            "fund_daily": fund_daily, "note": None}


def test_share_history_trend_flags_recent_reversal(monkeypatch):
    """batch-test P1-4：整体净流入但近 5 日净流出 → trend 附带近端提示，
    不得仅按 20 日合计定性「持续净流入」。"""
    def _bridge_reversal(getter, *a):
        if getter == "get_etf_share_history":
            return _share_env_with_reversal()
        return None

    monkeypatch.setattr("etf_data._bridge_get", _bridge_reversal)
    out = query_etf_share_history("515050", days=12)
    s = out["summary"]
    assert s["total_flow_est"] > 5          # 整体净流入（>5 阈值）
    assert s["recent_flow_est"] < 0         # 近端 5 日净流出
    assert s["recent_flow_days"] == 5
    assert "🟢" in s["trend"] and "转净流出" in s["trend"]


def test_share_history_trend_span_counts_missing_share_days(monkeypatch):
    """batch-test P1-4 二次修复：fund_share T+1 延迟使尾端 1-2 日无份额
    → 「近 5 日」窗口实际跨 >5 个交易日，trend 标注必须用实际跨度。"""
    n_rows = 12
    base = datetime.date(2026, 7, 1)
    dates = [(base + datetime.timedelta(days=i)).strftime("%Y%m%d")
             for i in range(n_rows)]
    # 份额：7/2~7/6 每行 +20000（+8 亿/日，前段大流入）→ 7/7~7/10 每行 -15000
    # （-6 亿/日，近端净流出）；尾端 2 行（7/11、7/12）fund_share 缺失（T+1 延迟）
    deltas = [20000.0] * 5 + [-15000.0] * 4  # 对应 7/2~7/6 与 7/7~7/10
    shares = []
    s = 1_000_000.0
    shares.append(s)  # 7/1（首行，无 prev 不产生 flow）
    for d in deltas:
        s += d
        shares.append(round(s, 2))
    fund_share = [{"trade_date": d, "fd_share": sh}
                  for d, sh in zip(dates[:10], shares)]
    fund_share = [{"trade_date": d, "fd_share": sh}
                  for d, sh in zip(dates[:-2], shares)]
    fund_daily = [
        {"trade_date": d, "open": 4.0, "high": 4.1, "low": 3.9,
         "close": 4.0, "pre_close": 3.99, "pct_chg": 0.1,
         "vol": 10000, "amount": 40000}
        for d in dates
    ]
    env = {"status": "ok", "fund_share": fund_share,
           "fund_daily": fund_daily, "note": None}

    def _bridge_delayed(getter, *a):
        if getter == "get_etf_share_history":
            return env
        return None

    monkeypatch.setattr("etf_data._bridge_get", _bridge_delayed)
    out = query_etf_share_history("515050", days=12)
    s = out["summary"]
    # rows=12 行，首行（7/1）无 prev_share → detail_rows=11 行（7/2~7/12）
    # flows 只含 9 行（7/2~7/10；7/11、7/12 无份额被滤）：
    #   7/2~7/6 流入（+8 亿×5）+ 7/7~7/10 流出（-6 亿×4）→ 合计 +16 亿 > 5 → 基础🟢
    # recent_flows = flows[-5:] = 7/6~7/10 = +8-6-6-6-6 = -16 亿 → 转净流出
    # 第 5 个可算行（7/6）在 detail 索引 4 → span = 11-4 = 7（非硬编码 5）
    assert s["row_count"] == 11
    assert s["total_flow_est"] > 5
    assert s["recent_flow_est"] < 0
    assert s["recent_flow_days"] == 7  # T+1 延迟：最近 5 个可算行实际跨 7 个交易日
    assert "近 7 日转净流出" in s["trend"]


def test_share_history_total_change_matches_detail_rows(monkeypatch):
    """份额总变化与 detail_rows/date_range 同口径：r0（无 prev、被 rows[1:]
    丢弃）不得计入。修复前 latest-earliest 取自 merged 全量（含 r0→r1 首间隔
    +200000），days=5 时 6 行→5 行 detail，虚增恰为该首间隔。"""
    n_rows = 6
    base = datetime.date(2026, 7, 1)
    dates = [(base + datetime.timedelta(days=i)).strftime("%Y%m%d")
             for i in range(n_rows)]
    fund_share = [{"trade_date": d, "fd_share": sh} for d, sh in zip(
        dates, [1_000_000.0, 1_200_000.0, 1_210_000.0,
                1_220_000.0, 1_230_000.0, 1_240_000.0])]
    fund_daily = [
        {"trade_date": d, "open": 4.0, "high": 4.1, "low": 3.9,
         "close": 4.0, "pre_close": 3.99, "pct_chg": 0.1,
         "vol": 10000, "amount": 40000}
        for d in dates
    ]
    env = {"status": "ok", "fund_share": fund_share,
           "fund_daily": fund_daily, "note": None}

    def _bridge(getter, *a):
        if getter == "get_etf_share_history":
            return env
        return None

    monkeypatch.setattr("etf_data._bridge_get", _bridge)
    out = query_etf_share_history("515050", days=5)
    s = out["summary"]
    assert s["row_count"] == 5                      # 6 行 - r0 = 5 行 detail
    assert out["date_range"] == f"{dates[1]} ~ {dates[-1]}"
    assert s["share_total_change"] == 40_000.0      # 1_240_000 - 1_200_000
    # 修复前该值 = 1_240_000 - 1_000_000 = 240_000.0（虚含被丢弃的首间隔）
    # 正/负流日计数聚合（引擎字段——报告层禁止对 rows 目视计数）
    assert s["inflow_days"] == 5                   # 份额单调递增 → 全为流入
    assert s["outflow_days"] == 0
    assert s["flat_days"] == 0
    # 5 行 flow：首行 +200000 万份×4 元=80 亿，后 4 行各 +10000 万份×4 元=4 亿 → 96 亿
    assert s["inflow_sum_est"] == pytest.approx(96.0)
    assert s["outflow_sum_est"] == 0.0


def test_share_history_total_change_none_when_single_share_row(monkeypatch):
    """边界：detail_rows 内有效份额行 <2 时 share_total_change=None
    （旧行为返回 0.0，渲染端 etf.py 已占位 '-'）。"""
    n_rows = 3
    base = datetime.date(2026, 7, 1)
    dates = [(base + datetime.timedelta(days=i)).strftime("%Y%m%d")
             for i in range(n_rows)]
    # 份额仅首行有效，其余缺失（T+1 尾端延迟）
    fund_share = [{"trade_date": dates[0], "fd_share": 1_000_000.0}]
    fund_daily = [
        {"trade_date": d, "open": 4.0, "high": 4.1, "low": 3.9,
         "close": 4.0, "pre_close": 3.99, "pct_chg": 0.1,
         "vol": 10000, "amount": 40000}
        for d in dates
    ]
    env = {"status": "ok", "fund_share": fund_share,
           "fund_daily": fund_daily, "note": None}

    def _bridge(getter, *a):
        if getter == "get_etf_share_history":
            return env
        return None

    monkeypatch.setattr("etf_data._bridge_get", _bridge)
    out = query_etf_share_history("515050", days=5)
    assert out["summary"]["share_total_change"] is None
