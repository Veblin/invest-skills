"""Tests for skills/lib/market_pulse.py — 两融余额抓取（无网络）。

D13：canonical 在函数内惰性 import akshare 与 lib.proxy →
打 sys.modules["akshare"] 与 sys.modules["lib.proxy"]（调用时查找点）。
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pandas as pd
import pytest

_SKILLS_LIB = Path(__file__).resolve().parents[1]
if str(_SKILLS_LIB) not in sys.path:
    sys.path.insert(0, str(_SKILLS_LIB))

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
    """注入假 akshare / lib.proxy 模块；返回 ctx 供断言进出会话次数。

    真实 akshare_direct_session 是返回 context manager 的函数 → fake 也须可调用。
    """
    ctx = _SessionCtx()

    fake_ak = types.ModuleType("akshare")
    fake_ak.stock_margin_account_info = lambda: df
    monkeypatch.setitem(sys.modules, "akshare", fake_ak)

    fake_proxy = types.ModuleType("lib.proxy")
    fake_proxy.akshare_direct_session = lambda: ctx
    monkeypatch.setitem(sys.modules, "lib", types.ModuleType("lib"))
    monkeypatch.setitem(sys.modules, "lib.proxy", fake_proxy)
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
        fake_proxy = types.ModuleType("lib.proxy")
        fake_proxy.akshare_direct_session = lambda: _SessionCtx()
        monkeypatch.setitem(sys.modules, "lib", types.ModuleType("lib"))
        monkeypatch.setitem(sys.modules, "lib.proxy", fake_proxy)

        with pytest.raises(RuntimeError, match="api down"):
            fetch_margin_account_info()
