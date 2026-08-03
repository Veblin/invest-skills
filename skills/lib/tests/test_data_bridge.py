"""Tests for skills/lib/data_bridge.py — caching facade over collectors.

Monkeypatches data_bridge._cache (tmp dir) and _import_lib_module_attr
(fake collector); no network involved.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_SKILLS_LIB = Path(__file__).resolve().parents[1]
if str(_SKILLS_LIB) not in sys.path:
    sys.path.insert(0, str(_SKILLS_LIB))

import data_bridge  # noqa: E402
from cache import DataCache  # noqa: E402


@pytest.fixture
def fake_bridge(tmp_path, monkeypatch):
    """data_bridge with a tmp cache and a fake collector backend."""
    monkeypatch.setattr(data_bridge, "_cache", DataCache(cache_dir=tmp_path / "cache"))

    calls: dict[str, int] = {"quote": 0, "kline": 0, "valuation": 0, "basic_info": 0}

    def fake_collector(name: str):
        def _collect(symbol: str, **kwargs) -> dict:
            calls[name] += 1
            return {"dimension": name, "display": f"fake-{name}", "symbol": symbol,
                    "data": {"x": 1}, "status": "ok", "_meta": {"source": "fake"}}
        return _collect

    monkeypatch.setattr(
        data_bridge,
        "_import_lib_module_attr",
        lambda module_name, attr: fake_collector(attr.removeprefix("collect_")),
    )
    return calls


def test_get_quote_cached(fake_bridge):
    first = data_bridge.get_quote("600176")
    assert first["status"] == "ok"
    assert "_from_cache" not in first  # fresh fetch

    second = data_bridge.get_quote("600176")
    assert second["_from_cache"] is True  # cache hit
    assert fake_bridge["quote"] == 1  # collector called exactly once


def test_get_kline_with_kwargs_bypasses_cache(fake_bridge):
    a = data_bridge.get_kline("600176", start_date="20260101")
    b = data_bridge.get_kline("600176", start_date="20260101")
    # kwargs → 直调 collector，不写缓存
    assert "_from_cache" not in a
    assert "_from_cache" not in b
    assert fake_bridge["kline"] == 2


def test_get_kline_force_bypasses_cache(fake_bridge):
    data_bridge.get_kline("600176")
    data_bridge.get_kline("600176", force=True)
    assert fake_bridge["kline"] == 2


def test_get_valuation_uses_own_dimension(fake_bridge):
    data_bridge.get_valuation("600176")
    # 估值必须用独立 valuation 维度，不得与 financials 共用缓存槽位
    assert data_bridge._cache.is_fresh("valuation", "600176")
    assert not data_bridge._cache.is_fresh("financials", "600176")


def test_missing_envelope_not_cached(tmp_path, monkeypatch):
    """失败信封（status='missing'）不写缓存：源恢复后可重新抓取（review fix #4）。"""
    monkeypatch.setattr(data_bridge, "_cache", DataCache(cache_dir=tmp_path / "cache"))
    calls = {"n": 0}

    def fake_collector(symbol: str, **kwargs) -> dict:
        calls["n"] += 1
        return {"dimension": "quote", "display": "行情", "data": None,
                "status": "missing", "error": "rate limit", "_meta": {"source": "none"}}

    monkeypatch.setattr(
        data_bridge,
        "_import_lib_module_attr",
        lambda module_name, attr: fake_collector,
    )
    first = data_bridge.get_quote("600176")
    second = data_bridge.get_quote("600176")
    assert first["status"] == "missing"
    assert second["status"] == "missing"
    assert "_from_cache" not in second
    assert calls["n"] == 2  # 每次 miss 都回源，未被 TTL 缓存


def test_all_failed_envelope_not_cached(tmp_path, monkeypatch):
    """macro 全失败信封（status='all_failed'）不写缓存（review fix #5）。"""
    monkeypatch.setattr(data_bridge, "_cache", DataCache(cache_dir=tmp_path / "cache"))
    calls = {"n": 0}

    def fake_collector(symbol: str, **kwargs) -> dict:
        calls["n"] += 1
        return {"status": "all_failed", "indicators": {}, "error": "eastmoney/FRED/SOX 全失败"}

    monkeypatch.setattr(
        data_bridge,
        "_import_lib_module_attr",
        lambda module_name, attr: fake_collector,
    )
    first = data_bridge.get_macro()
    second = data_bridge.get_macro()
    assert first["status"] == "all_failed"
    assert second["status"] == "all_failed"
    assert "_from_cache" not in second
    assert calls["n"] == 2  # 未被 7d TTL 缓存


def test_invalidate_symbol_clears_all_dimensions(fake_bridge):
    data_bridge.get_quote("600176")
    data_bridge.get_kline("600176")
    data_bridge.get_basic_info("600176")
    assert fake_bridge["quote"] == 1
    assert data_bridge.invalidate_symbol("600176") >= 3
    # 失效后重新采集（miss → 回源），而非命中旧缓存
    refreshed = data_bridge.get_quote("600176")
    assert "_from_cache" not in refreshed
    assert fake_bridge["quote"] == 2


def test_cache_stats_and_clear(fake_bridge):
    data_bridge.get_quote("600176")
    st = data_bridge.cache_stats()
    assert st["total_entries"] >= 1
    assert data_bridge.cache_clear() >= 1
    assert data_bridge._cache.stats()["total_entries"] == 0
