"""Unit tests for kline RSI / aligned returns (no network)."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

_LIB = Path(__file__).resolve().parent.parent / "scripts" / "lib"
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

from etf_data import _aligned_nav_returns, _latest_rsi, rollup_etf_quality_status  # noqa: E402


def test_aligned_nav_returns_skips_rows_without_nav():
    df = pd.DataFrame([
        {"单位净值": 1.0, "日增长率": 1.0},
        {"单位净值": None, "日增长率": 0.5},
        {"单位净值": 1.01, "日增长率": 1.0},
    ])
    navs, returns = _aligned_nav_returns(df)
    assert len(navs) == 2
    assert len(returns) == 2
    assert navs == pytest.approx([1.0, 1.01])
    assert returns == pytest.approx([0.01, 0.01])


def test_aligned_nav_returns_derives_from_nav_when_chg_missing():
    df = pd.DataFrame([
        {"单位净值": 1.0, "日增长率": None},
        {"单位净值": 1.02, "日增长率": None},
    ])
    navs, returns = _aligned_nav_returns(df)
    assert len(navs) == 1
    assert returns == pytest.approx([0.02])


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
