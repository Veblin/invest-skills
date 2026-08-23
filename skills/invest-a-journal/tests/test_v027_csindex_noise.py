"""v0.2.7 P2-4：erp 双 fallback（csindex / bond）404 静默降级。

batch-review：csindex 404 曾无保护传播（HTTPError 崩栈 invest.py
cmd_market_status / 污染 query_data._error）；补 try/except 后仅 debug 级，
真实缺失仍由「erp: HS300 PE unavailable」诚实上报（单行、无 traceback）。
"""
from __future__ import annotations

import sys
import urllib.error
from contextlib import nullcontext

import pytest

import market_microstructure as mm  # noqa: E402


class _FakeAk404:
    """fake akshare：csindex 与 bond 接口均抛 404。"""

    def stock_zh_index_value_csindex(self, symbol):
        raise urllib.error.HTTPError(symbol, 404, "Not Found", None, None)

    def bond_zh_us_rate(self):
        raise urllib.error.HTTPError("bond", 404, "Not Found", None, None)


@pytest.fixture
def _erp_offline(monkeypatch):
    """Tushare/FRED 不可用 + akshare 双接口 404。"""
    monkeypatch.setitem(sys.modules, "akshare", _FakeAk404())
    monkeypatch.setattr(mm, "akshare_direct_session", nullcontext)
    monkeypatch.setattr(mm.env, "is_tushare_available", lambda cfg: False)
    monkeypatch.setattr(mm.env, "is_fred_available", lambda cfg: False)


def test_erp_akshare_fallback_404_silent_degrade(monkeypatch, caplog, _erp_offline):
    """fallback 404 不传播、不注入「erp: HTTP Error 404」，仅诚实上报不可用。"""
    result = {"_errors": [], "erp": None}
    with caplog.at_level("DEBUG", logger="market_microstructure"):
        mm._fetch_erp(result)
    assert result.get("erp") is None
    assert any("HS300 PE unavailable" in e for e in result["_errors"])
    assert not any("404" in e for e in result["_errors"])
    # 无 WARNING 及以上记录（静默降级）
    assert not any(rec.levelno >= 30 for rec in caplog.records)


def test_erp_pe_ok_bond_404_silent_degrade(monkeypatch, caplog):
    """HS300 PE 正常、bond 404 → erp 照常计算，不因 fallback 探测失败中断。"""
    class _FakeAkBond404:
        def stock_zh_index_value_csindex(self, symbol):
            import pandas as pd
            return pd.DataFrame([
                {"日期": "2026-08-21", "市盈率1": 13.0},
                {"日期": "2026-08-22", "市盈率1": 13.2},
            ])

        def bond_zh_us_rate(self):
            raise urllib.error.HTTPError("bond", 404, "Not Found", None, None)

    monkeypatch.setitem(sys.modules, "akshare", _FakeAkBond404())
    monkeypatch.setattr(mm, "akshare_direct_session", nullcontext)
    monkeypatch.setattr(mm.env, "is_tushare_available", lambda cfg: False)
    monkeypatch.setattr(mm.env, "is_fred_available", lambda cfg: False)

    result = {"_errors": [], "erp": None}
    with caplog.at_level("DEBUG", logger="market_microstructure"):
        mm._fetch_erp(result)
    assert any("10Y yield unavailable" in e for e in result["_errors"])
    assert not any("404" in e for e in result["_errors"])
    assert not any(rec.levelno >= 30 for rec in caplog.records)
