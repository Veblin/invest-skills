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
    """清空 etf_data 的 L1 进程内缓存（30s TTL）。

    R2/A04 后 `etf_data` 与 `invest_a_etf_etf_data` 是**同一模块对象**
    （loader 按文件识别复用，见 skills/lib/tests/test_r2_module_identity.py）；
    仍按两个名字遍历清缓存——防御历史/边缘路径下别名未注册时的残留实例。
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


@patch("etf_data.akshare_direct_session")
def test_spot_failure_negative_cache_suppresses_repeat_fetch(mock_session, monkeypatch):
    """A7：全表源失败后短时间内不重复回源（同进程多次查询只发起 1 次网络）。

    反例场景：EM 断连时一次 report 会为每只标的重复拉全表（6×5s 无谓等待）。
    正控：失败标记过期/清缓存后允许再试（源恢复不被永久屏蔽）。
    """
    mock_session.return_value.__enter__ = MagicMock(return_value=None)
    mock_session.return_value.__exit__ = MagicMock(return_value=False)

    call_count = {"n": 0}

    def _boom():
        call_count["n"] += 1
        raise RuntimeError("RemoteDisconnected")

    ak = MagicMock()
    ak.fund_etf_spot_em = _boom
    monkeypatch.setitem(sys.modules, "akshare", ak)

    # 三次独立进入（不同 symbol 查询路径共用同一全表端点）→ 只 1 次网络
    from etf_data import _lookup_etf_spot_row, _get_etf_spot_df

    assert _get_etf_spot_df() is None
    assert _lookup_etf_spot_row("515790")[0] is None
    assert _get_etf_spot_df() is None
    assert call_count["n"] == 1

    # 清缓存（测试隔离 API）→ 失败标记同步清除，可再次发起
    clear_etf_spot_cache()
    assert _get_etf_spot_df() is None
    assert call_count["n"] == 2

    # 正控：成功后失败标记清零（后续 L1 命中不再回源）
    def _ok():
        call_count["n"] += 1
        return _fake_spot_df()

    ak.fund_etf_spot_em = _ok
    clear_etf_spot_cache()
    assert _get_etf_spot_df() is not None
    assert call_count["n"] == 3
    assert _get_etf_spot_df() is not None
    assert call_count["n"] == 3  # L1 命中
