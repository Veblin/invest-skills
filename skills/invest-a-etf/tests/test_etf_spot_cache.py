"""Unit tests for ETF spot cache deduplication (no network)."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from etf_data import (
    clear_etf_spot_cache,
    query_etf_data,
    query_etf_quote,
)


@pytest.fixture(autouse=True)
def _reset_spot_cache():
    _clear_spot_caches()
    yield
    _clear_spot_caches()


def _clear_spot_caches() -> None:
    """清空所有 etf_data 实例的 L1 进程内缓存。

    测试直接 import 的 `etf_data` 与 data_bridge 经
    invest_path.load_invest_a_etf_module() 按固定名 `invest_a_etf_etf_data`
    加载的 canonical 实例是同一文件的两个独立模块对象，各持一份
    _SPOT_CACHE_DF/_SPOT_CACHE_TS（30s TTL）。只清测试侧实例会让
    canonical 实例的 L1 在文件/全量顺序下保持热（前一测试已回源写入），
    使 test_spot_served_by_l2_when_l1_cold 首查零网络（v0.2.4 测试隔离修复）。
    """
    for name in ("etf_data", "invest_a_etf_etf_data"):
        mod = sys.modules.get(name)
        if mod is not None:
            mod.clear_etf_spot_cache()


def _fake_spot_df() -> pd.DataFrame:
    return pd.DataFrame(
        [{
            "代码": "515790",
            "最新价": 4.5,
            "涨跌幅": 0.1,
            "成交量": 1000,
            "成交额": 4500,
            "基金折价率": 0.2,
            "最新份额": 2e10,
        }]
    )


@patch("etf_data.akshare_direct_session")
def test_spot_fetched_once_for_data_and_quote(mock_session, monkeypatch):
    mock_session.return_value.__enter__ = MagicMock(return_value=None)
    mock_session.return_value.__exit__ = MagicMock(return_value=False)

    fake_df = _fake_spot_df()
    call_count = {"n": 0}

    def _fake_spot_em():
        call_count["n"] += 1
        return fake_df

    ak = MagicMock()
    ak.fund_etf_spot_em = _fake_spot_em
    monkeypatch.setitem(sys.modules, "akshare", ak)

    profile = query_etf_data("515790")
    quote = query_etf_quote("515790")

    assert call_count["n"] == 1
    assert profile["premium_discount"] == pytest.approx(-0.2)
    assert quote["status"] == "available"
    assert quote["price"] == pytest.approx(4.5)


@patch("etf_data.akshare_direct_session")
def test_spot_served_by_l2_when_l1_cold(mock_session, monkeypatch):
    """L1 清空后 L2（data_bridge 磁盘缓存）仍命中：跨进程去重的核心路径。"""
    mock_session.return_value.__enter__ = MagicMock(return_value=None)
    mock_session.return_value.__exit__ = MagicMock(return_value=False)

    fake_df = _fake_spot_df()
    call_count = {"n": 0}

    def _fake_spot_em():
        call_count["n"] += 1
        return fake_df

    ak = MagicMock()
    ak.fund_etf_spot_em = _fake_spot_em
    monkeypatch.setitem(sys.modules, "akshare", ak)

    # 首次：L1/L2 双写（网络 1 次）
    assert query_etf_quote("515790")["status"] == "available"
    assert call_count["n"] == 1

    # 清 L1 后：L2 命中，零网络
    clear_etf_spot_cache()
    assert query_etf_quote("515790")["status"] == "available"
    assert call_count["n"] == 1

    # L2 已落盘（etf_spot/market 维度）
    import data_bridge

    assert data_bridge._cache.get("etf_spot", "market") is not None
