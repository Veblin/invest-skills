"""Tests for skills/lib/market_pulse.py — 两融余额抓取（无网络）。

D13：canonical 在函数内惰性 `from lib.proxy import akshare_direct_session`
（调用时查找点）→ monkeypatch 真实 lib.proxy 的该属性；akshare 经
sys.modules["akshare"] 注入。**不替换 sys.modules["lib"]**——全局替换会在
测试窗口内劫持其他进程内惰性 `from lib import ...` 的绑定。
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pandas as pd
import pytest

_SKILLS_LIB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SKILLS_LIB))  # 无条件插 0：防其他 skill 目录先行入 path 遮蔽同名模块

# canonical 的 `from lib.proxy import ...` 需要 invest-a-stock/scripts 在 path
_STOCK_SCRIPTS = Path(__file__).resolve().parents[2] / "invest-a-stock" / "scripts"
sys.path.insert(0, str(_STOCK_SCRIPTS))

import lib.proxy  # noqa: E402
from market_pulse import fetch_margin_account_info  # noqa: E402


class _SessionCtx:
    """akshare_direct_session 假实现（context manager，逐调用断言）。"""

    def __init__(self):
        self.entered = 0
        self.exited = 0

    def __enter__(self):
        self.entered += 1
        return None

    def __exit__(self, *exc):
        self.exited += 1
        return False


def _install_fakes(monkeypatch, df) -> tuple[_SessionCtx, types.ModuleType]:
    """注入假 akshare + patch 真实 lib.proxy.akshare_direct_session。

    真实 akshare_direct_session 是返回 context manager 的函数 → fake 也须可调用。
    """
    ctx = _SessionCtx()

    fake_ak = types.ModuleType("akshare")
    fake_ak.stock_margin_account_info = lambda: df
    monkeypatch.setitem(sys.modules, "akshare", fake_ak)

    monkeypatch.setattr(lib.proxy, "akshare_direct_session", lambda: ctx)
    return ctx, fake_ak


class TestFetchMarginAccountInfo:
    def test_returns_dataframe(self, monkeypatch):
        df = pd.DataFrame({"交易日期": ["2026-08-01"], "融资余额": [1.0]})
        ctx, _ = _install_fakes(monkeypatch, df)
        out = fetch_margin_account_info()
        assert out is df
        assert ctx.entered == 1 and ctx.exited == 1

    def test_empty_df_returns_none(self, monkeypatch):
        ctx, _ = _install_fakes(monkeypatch, pd.DataFrame())
        assert fetch_margin_account_info() is None
        assert ctx.entered == 1 and ctx.exited == 1

    def test_none_returns_none(self, monkeypatch):
        ctx, _ = _install_fakes(monkeypatch, None)
        assert fetch_margin_account_info() is None
        assert ctx.entered == 1 and ctx.exited == 1

    def test_exception_propagates(self, monkeypatch):
        def _boom():
            raise RuntimeError("api down")

        fake_ak = types.ModuleType("akshare")
        fake_ak.stock_margin_account_info = _boom
        monkeypatch.setitem(sys.modules, "akshare", fake_ak)
        monkeypatch.setattr(lib.proxy, "akshare_direct_session", lambda: _SessionCtx())

        with pytest.raises(RuntimeError, match="api down"):
            fetch_margin_account_info()
