"""entry_check: flow scoring uses raw 亿元 floats (Q10), not display strings."""

from __future__ import annotations

import sys
import importlib.util
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd

_ENTRY = Path(__file__).resolve().parents[3] / "scripts" / "research" / "entry_check.py"


def _load_entry_check():
    name = "entry_check_under_test"
    sys.modules.pop(name, None)
    spec = importlib.util.spec_from_file_location(name, _ENTRY)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_flow_score_uses_raw_floats_not_rounded_display():
    """1.004亿 formats as +1.00亿; scoring must still treat smart > 1."""
    mod = _load_entry_check()
    spot = pd.DataFrame(
        [
            {
                "代码": "588000",
                "超大单净流入-净额": 1.004e8,
                "大单净流入-净额": 0.0,
                "中单净流入-净额": 0.0,
                "小单净流入-净额": 0.0,
                "换手率": 1.0,
            }
        ]
    )
    with patch.object(mod, "akshare_direct_session", MagicMock()):
        with patch.object(mod.ak, "fund_etf_spot_em", return_value=spot):
            score, info = mod._flow_score("588000")

    assert info["超大单"] == "+1.00亿"  # display rounded
    assert info["主力态度"] == "积极流入"  # raw 1.004 > 1
    assert score == 21  # 15 + 6


def test_entry_check_help_exit_0():
    import subprocess

    r = subprocess.run(
        [sys.executable, str(_ENTRY), "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert r.returncode == 0
    assert "条件评分" in r.stdout or "symbol" in r.stdout.lower()


# ── review fixes: 单根 K 线 / RSI(6) 全涨 / 共享引擎一致性 / NaN 资金流 ──

def _kline_df(closes):
    return pd.DataFrame({"收盘": [float(c) for c in closes]})


def test_technical_score_single_bar_neutral_fallback():
    """1 根 K 线：不崩溃（IndexError 回归），中性回退 12 分。"""
    mod = _load_entry_check()
    df = _kline_df([10.0])
    with patch.object(mod, "akshare_direct_session", MagicMock()):
        with patch.object(mod.ak, "fund_etf_hist_em", return_value=df):
            score, info = mod._technical_score("588000")

    assert score == 12
    assert "K线不足" in info["error"]


def test_technical_score_rsi_all_up_is_100():
    """6 日全涨：RSI(6)=100 并触发超买惩罚，而非 NaN 回退中性 50。"""
    mod = _load_entry_check()
    closes = [10.0 + i for i in range(7)]  # 7 根 K 线 = 6 个上涨日
    df = _kline_df(closes)
    with patch.object(mod, "akshare_direct_session", MagicMock()):
        with patch.object(mod.ak, "fund_etf_hist_em", return_value=df):
            score, info = mod._technical_score("588000")

    assert info["rsi6"] == 100.0
    assert info["rsi_zone"] == "超买"
    assert score == 15 - 8  # 超买惩罚生效（-8），非中性 15


def test_technical_score_matches_shared_engine():
    """同序列：entry_check 输出与 shared lib/technical 引擎同值（无实现漂移）。"""
    from lib.technical import boll_latest, rsi_series, sma

    mod = _load_entry_check()
    rng = np.random.default_rng(42)
    closes = np.round(np.cumsum(rng.normal(0.0, 1.0, 40)) + 100.0, 3)
    df = _kline_df(closes)
    with patch.object(mod, "akshare_direct_session", MagicMock()):
        with patch.object(mod.ak, "fund_etf_hist_em", return_value=df):
            score, info = mod._technical_score("588000")

    closes_list = [float(c) for c in closes]

    shared_rsi = rsi_series(closes_list, 6)
    assert info["rsi6"] == round(float(shared_rsi[-1]), 1)

    shared_ma20 = sma(closes_list, 20)[-1]
    expected_dev = (closes_list[-1] / shared_ma20 - 1) * 100
    assert info["ma20_dev"] == f"{expected_dev:+.1f}%"

    shared_boll = boll_latest(closes_list, 20, 2.0)
    expected_pos = (
        (closes_list[-1] - shared_boll["lower"])
        / (shared_boll["upper"] - shared_boll["lower"])
    )
    assert info["boll_pos"] == f"{expected_pos:.0%}"

    expected_day = (closes_list[-1] / closes_list[-2] - 1) * 100
    assert info["day_change"] == f"{expected_day:+.2f}%"
    assert 0 <= score <= 25


def test_flow_score_nan_marks_not_available():
    """NaN 资金流：不输出 'nan'，缺失标 not available，不误判为'平衡'。"""
    mod = _load_entry_check()
    spot = pd.DataFrame(
        [
            {
                "代码": "588000",
                "超大单净流入-净额": np.nan,
                "大单净流入-净额": np.nan,
                "中单净流入-净额": np.nan,
                "小单净流入-净额": np.nan,
                "换手率": np.nan,
            }
        ]
    )
    with patch.object(mod, "akshare_direct_session", MagicMock()):
        with patch.object(mod.ak, "fund_etf_spot_em", return_value=spot):
            score, info = mod._flow_score("588000")

    for v in info.values():
        assert "nan" not in str(v).lower()
    assert info["超大单"] == "not available"
    assert info["大单"] == "not available"
    assert info["换手率"] == "not available"
    assert "主力态度" not in info  # 缺失 ≠ 平衡
    assert "主力净额" not in info
    assert score == 15  # 中性，无错误加减分


def test_flow_score_partial_nan_no_balance_mislabel():
    """超大单有效 + 大单 NaN：不输出 nan、不把缺失当'平衡'、不误算主力净额。"""
    mod = _load_entry_check()
    spot = pd.DataFrame(
        [
            {
                "代码": "588000",
                "超大单净流入-净额": 2.0e8,
                "大单净流入-净额": np.nan,
                "中单净流入-净额": 0.0,
                "小单净流入-净额": 0.0,
                "换手率": 5.0,
            }
        ]
    )
    with patch.object(mod, "akshare_direct_session", MagicMock()):
        with patch.object(mod.ak, "fund_etf_spot_em", return_value=spot):
            score, info = mod._flow_score("588000")

    assert info["超大单"] == "+2.00亿"
    assert info["大单"] == "not available"
    assert "nan" not in str(info).lower()
    assert "主力态度" not in info
    assert score == 15
