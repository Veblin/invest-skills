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
