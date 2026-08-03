"""Unit tests for kline RSI / aligned returns (no network)."""

from __future__ import annotations

import pandas as pd
import pytest

from etf_data import _aligned_nav_returns, _latest_rsi, rollup_etf_quality_status


def test_aligned_nav_returns_skips_rows_without_nav():
    df = pd.DataFrame([
        {"单位净值": 1.0, "日增长率": 1.0},
        {"单位净值": None, "日增长率": 0.5},
        {"单位净值": 1.01, "日增长率": 1.0},
    ])
    navs, returns, rows = _aligned_nav_returns(df)
    assert len(navs) == 2
    assert len(returns) == 2
    assert len(rows) == 2
    assert navs == pytest.approx([1.0, 1.01])
    assert returns == pytest.approx([0.01, 0.01])


def test_aligned_nav_returns_derives_from_nav_when_chg_missing():
    df = pd.DataFrame([
        {"单位净值": 1.0, "日增长率": None},
        {"单位净值": 1.02, "日增长率": None},
    ])
    navs, returns, rows = _aligned_nav_returns(df)
    # fix #2: 首行 NAV 不再被丢弃；navs 保留锚点行 + 推导行
    assert navs == pytest.approx([1.0, 1.02])
    assert returns == pytest.approx([0.02])
    assert len(rows) == 2


def test_latest_rsi_uses_wilder_on_nav_closes():
    """Aligned with lib.technical.rsi_series (not simple-mean on returns)."""
    navs = [1.0 + 0.01 * i for i in range(30)]
    assert _latest_rsi(navs, 14) == 100.0


def test_latest_rsi_insufficient_navs():
    assert _latest_rsi([1.0, 1.01], 14) is None


def test_rollup_etf_quality_status():
    assert rollup_etf_quality_status({"index_pe": 15.0, "hedge_coverage": {}}) == "available"
    assert rollup_etf_quality_status({"_errors": ["x"], "index_pe": 1.0}) == "partial"
    assert rollup_etf_quality_status({"_errors": ["x"]}) == "missing"


def test_aligned_nav_returns_switch_day_misalignment():
    """515050 真实场景：Tushare 因子 05-13 变 3.0，但净值 05-12 已是拆后值。

    前复权必须在 05-12 行用新因子（3.0），否则产生 0.3698 假低点 →
    +211% 假跳变（污染波动率/MA/RSI/BOLL）。
    """
    adj_map = {
        "20260508": 1.0, "20260511": 1.0, "20260512": 1.0,
        "20260513": 3.0, "20260514": 3.0,
    }
    df = pd.DataFrame([
        {"净值日期": "2026-05-08", "单位净值": 3.1712, "日增长率": 0.42},
        {"净值日期": "2026-05-11", "单位净值": 3.2935, "日增长率": 3.86},
        {"净值日期": "2026-05-12", "单位净值": 1.1095, "日增长率": 1.06},  # 拆后，因子却仍 1.0
        {"净值日期": "2026-05-13", "单位净值": 1.1524, "日增长率": 3.87},
        {"净值日期": "2026-05-14", "单位净值": 1.1310, "日增长率": -1.86},
    ])
    navs, returns, rows = _aligned_nav_returns(df, adj_map=adj_map)
    # 复权后连续：无 0.3698 假低点
    assert navs == pytest.approx([1.0571, 1.0978, 1.1095, 1.1524, 1.1310], abs=1e-3)
    assert returns == pytest.approx([0.0385, 0.0107, 0.0387, -0.0186], abs=1e-3)


def test_aligned_nav_returns_no_misalignment_untouched():
    """因子与净值同日切换（无错位）：切换日前一行不误伤（保持旧因子）。"""
    adj_map = {"20260511": 1.0, "20260512": 3.0, "20260513": 3.0}
    df = pd.DataFrame([
        {"净值日期": "2026-05-11", "单位净值": 1.0978, "日增长率": 1.0},  # 拆前，因子 1.0
        {"净值日期": "2026-05-12", "单位净值": 0.3699, "日增长率": 0.5},  # 拆后，因子 3.0
        {"净值日期": "2026-05-13", "单位净值": 0.3710, "日增长率": 0.3},
    ])
    navs, returns, rows = _aligned_nav_returns(df, adj_map=adj_map)
    # 05-11 保持旧因子（×1/3），05-12 起新因子（×3/3）→ 序列连续
    assert navs == pytest.approx([0.3659, 0.3699, 0.3710], abs=1e-3)
    assert returns == pytest.approx([0.0109, 0.0030], abs=1e-3)
