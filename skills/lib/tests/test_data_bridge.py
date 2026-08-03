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


# ═════════════════════════════════════════════════════
# ETF 维度（v0.2.3：etf_data 接入 data_bridge 缓存层）
# ═════════════════════════════════════════════════════


@pytest.fixture
def fake_etf_bridge(tmp_path, monkeypatch):
    """data_bridge with a tmp cache and a fake etf fetch backend."""
    monkeypatch.setattr(data_bridge, "_cache", DataCache(cache_dir=tmp_path / "cache"))
    calls: dict[str, int] = {}

    def fake_fetch(attr: str):
        def _fetch(*args, **kwargs):
            calls[attr] = calls.get(attr, 0) + 1
            if attr == "fetch_etf_spot_rows":
                return [{"代码": "510300", "最新价": 4.5}]
            if attr == "fetch_etf_index_pe":
                return {"status": "ok", "index_pe": 12.3, "rows": [{"date": "2026-08-01"}]}
            if attr == "fetch_etf_nav":
                return {"status": "ok", "rows": [{"date": "2026-08-01", "nav": 1.5}]}
            if attr == "fetch_etf_share_history":
                return {"status": "ok", "fund_share": [], "fund_daily": []}
            return {"status": "ok"}
        return _fetch

    monkeypatch.setattr(data_bridge, "_import_etf_attr", lambda attr: fake_fetch(attr))
    return calls


def test_get_etf_spot_rows_cached(fake_etf_bridge):
    first = data_bridge.get_etf_spot_rows()
    assert first == [{"代码": "510300", "最新价": 4.5}]
    second = data_bridge.get_etf_spot_rows()
    assert second == first
    assert fake_etf_bridge["fetch_etf_spot_rows"] == 1  # 第二次命中缓存


def test_get_etf_spot_rows_empty_not_cached(tmp_path, monkeypatch):
    """空表（非交易日/失败）不缓存：下次调用重新回源。"""
    monkeypatch.setattr(data_bridge, "_cache", DataCache(cache_dir=tmp_path / "cache"))
    calls = {"n": 0}

    def fake_fetch(*args, **kwargs):
        calls["n"] += 1
        return []

    monkeypatch.setattr(data_bridge, "_import_etf_attr", lambda attr: fake_fetch)
    assert data_bridge.get_etf_spot_rows() == []
    assert data_bridge.get_etf_spot_rows() == []
    assert calls["n"] == 2


def test_get_etf_index_pe_failure_envelope_not_cached(tmp_path, monkeypatch):
    """失败信封（status='missing'）不写缓存：源恢复后可重新抓取。"""
    monkeypatch.setattr(data_bridge, "_cache", DataCache(cache_dir=tmp_path / "cache"))
    calls = {"n": 0}

    def fake_missing(idx_code):
        calls["n"] += 1
        return {"status": "missing", "index_pe": None, "error": "csindex empty"}

    monkeypatch.setattr(data_bridge, "_import_etf_attr", lambda attr: fake_missing)
    first = data_bridge.get_etf_index_pe("000300")
    second = data_bridge.get_etf_index_pe("000300")
    assert first["status"] == "missing"
    assert second["status"] == "missing"
    assert "_from_cache" not in second
    assert calls["n"] == 2

    def fake_ok(idx_code):
        calls["n"] += 1
        return {"status": "ok", "index_pe": 12.3}

    monkeypatch.setattr(data_bridge, "_import_etf_attr", lambda attr: fake_ok)
    third = data_bridge.get_etf_index_pe("000300")
    fourth = data_bridge.get_etf_index_pe("000300")
    assert "_from_cache" not in third  # 恢复后重新回源
    assert fourth["_from_cache"] is True  # 且可被缓存
    assert calls["n"] == 3


def test_get_etf_nav_uses_own_dimension(fake_etf_bridge):
    """etf_nav 必须用独立维度，不得与 etf_index_pe 共用缓存槽位。"""
    data_bridge.get_etf_nav("588000")
    assert data_bridge._cache.is_fresh("etf_nav", "588000")
    assert not data_bridge._cache.is_fresh("etf_index_pe", "588000")


def test_get_etf_attr_missing_returns_none(tmp_path, monkeypatch):
    """etf_data 不在 sys.path 时 getter 返回 None 且不写缓存。"""
    monkeypatch.setattr(data_bridge, "_cache", DataCache(cache_dir=tmp_path / "cache"))
    monkeypatch.setattr(data_bridge, "_import_etf_attr", lambda attr: None)
    assert data_bridge.get_etf_spot_rows() is None
    assert data_bridge.get_etf_nav("588000") is None
    assert data_bridge._cache.stats()["total_entries"] == 0


def test_get_microstructure_cached(tmp_path, monkeypatch):
    """microstructure 走 5min TTL 缓存：两次调用只回源一次。"""
    monkeypatch.setattr(data_bridge, "_cache", DataCache(cache_dir=tmp_path / "cache"))
    calls = {"n": 0}

    def fake_snapshot():
        calls["n"] += 1
        return {"date": "20260803", "ad_ratio": 1.2, "label_breadth": "正常"}

    import types
    fake_mod = types.ModuleType("market_microstructure")
    fake_mod.snapshot = fake_snapshot
    monkeypatch.setitem(sys.modules, "market_microstructure", fake_mod)

    first = data_bridge.get_microstructure()
    second = data_bridge.get_microstructure()
    assert first["date"] == "20260803"
    assert second["_from_cache"] is True
    assert calls["n"] == 1


def test_invalidate_symbol_covers_etf_dims(fake_etf_bridge):
    """invalidate_symbol 遍历 DEFAULT_TTL：per-ETF 维度被清除，市场级不受影响。"""
    data_bridge.get_etf_nav("588000")
    data_bridge.get_etf_spot_rows()
    assert fake_etf_bridge["fetch_etf_nav"] == 1

    data_bridge.invalidate_symbol("588000")
    data_bridge.get_etf_nav("588000")
    data_bridge.get_etf_spot_rows()
    assert fake_etf_bridge["fetch_etf_nav"] == 2  # 重新回源
    assert fake_etf_bridge["fetch_etf_spot_rows"] == 1  # 市场级 symbol 不受影响

    data_bridge.invalidate_symbol("market")
    data_bridge.get_etf_spot_rows()
    assert fake_etf_bridge["fetch_etf_spot_rows"] == 2
