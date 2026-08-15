"""futures_basis 查询测试 — mock store，不联网。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS / "lib"))
sys.path.insert(0, str(_SCRIPTS))
_ROOT = Path(__file__).resolve().parent.parent.parent.parent
for _p in (str(_ROOT / "skills"), str(_ROOT / "skills" / "lib"), str(_ROOT / "skills" / "invest-a-stock" / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from futures_basis import futures_symbol_for_etf, query_futures_basis  # noqa: E402


def _fake_rows(n: int = 100) -> list[dict]:
    return [
        {
            "date": f"2026-08-{i % 28 + 1:02d}", "symbol": "IC", "contract": "IC2608.CFX",
            "basis_pct": round(-0.5 + (i - 50) * 0.01, 4), "basis_pts": -40.0,
            "oi": 30000.0 + i * 10, "source": "tushare",
        }
        for i in range(n)
    ]


class TestMapping:
    def test_etf_to_futures(self):
        assert futures_symbol_for_etf("510500") == "IC"
        assert futures_symbol_for_etf("510300") == "IF"
        assert futures_symbol_for_etf("512100") == "IM"
        assert futures_symbol_for_etf("510050") == "IH"
        assert futures_symbol_for_etf("563300") is None  # 中证2000 无期货


class TestQuery:
    def test_available_with_percentile(self, monkeypatch):
        import lib.store as store_mod  # noqa: E402

        monkeypatch.setattr(store_mod, "load_futures_daily", lambda symbol=None, limit=1000: _fake_rows(100))
        r = query_futures_basis("510500")
        assert r["available"] is True
        assert r["futures_symbol"] == "IC"
        assert r["percentile"] is not None
        assert r["median_basis_pct"] is not None
        assert r["current_basis_pct"] == pytest.approx(-0.5 + 49 * 0.01)  # 最后一行 i=99

    def test_no_futures_etf(self, monkeypatch):
        import lib.store as store_mod  # noqa: E402

        monkeypatch.setattr(store_mod, "load_futures_daily", lambda symbol=None, limit=1000: [])
        r = query_futures_basis("563300")
        assert r["available"] is False
        assert "无股指期货" in r["note"]

    def test_insufficient_history(self, monkeypatch):
        import lib.store as store_mod  # noqa: E402

        monkeypatch.setattr(store_mod, "load_futures_daily", lambda symbol=None, limit=1000: _fake_rows(10))
        r = query_futures_basis("510500")
        assert r["available"] is False
        assert "历史数据不足" in r["note"]
