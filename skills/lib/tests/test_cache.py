"""Tests for skills/lib/cache.py — DataCache TTL/invalidate/stats.

Uses a tmp_path cache dir; no network, no touching the real cache.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_SKILLS_LIB = Path(__file__).resolve().parents[1]
if str(_SKILLS_LIB) not in sys.path:
    sys.path.insert(0, str(_SKILLS_LIB))

from cache import DataCache  # noqa: E402


def _make_cache(tmp_path: Path) -> DataCache:
    return DataCache(cache_dir=tmp_path / "cache")


def test_set_and_get_roundtrip(tmp_path):
    c = _make_cache(tmp_path)
    c.set("quote", "600176", {"price": 10.5}, ttl_seconds=300, source="test")
    got = c.get("quote", "600176")
    assert got == {"price": 10.5, "_from_cache": True}


def test_get_missing_returns_none(tmp_path):
    c = _make_cache(tmp_path)
    assert c.get("quote", "999999") is None


def test_ttl_expiry_via_max_age(tmp_path):
    c = _make_cache(tmp_path)
    c.set("quote", "600176", {"price": 1.0}, ttl_seconds=3600, source="test")
    # max_age_seconds=0 → 必然过期
    assert c.get("quote", "600176", max_age_seconds=0) is None


def test_invalidate_single(tmp_path):
    c = _make_cache(tmp_path)
    c.set("quote", "600176", {"price": 1.0}, ttl_seconds=300)
    assert c.invalidate("quote", "600176") == 1
    assert c.get("quote", "600176") is None


def test_invalidate_dimension(tmp_path):
    c = _make_cache(tmp_path)
    c.set("quote", "600176", {"price": 1.0}, ttl_seconds=300)
    c.set("quote", "000001", {"price": 2.0}, ttl_seconds=300)
    c.set("kline", "600176", {"rows": 10}, ttl_seconds=300)
    assert c.invalidate("quote") == 2
    assert c.get("quote", "600176") is None
    assert c.get("quote", "000001") is None
    # 其他维度不受影响
    assert c.get("kline", "600176") is not None


def test_invalidate_all(tmp_path):
    c = _make_cache(tmp_path)
    c.set("quote", "600176", {"price": 1.0}, ttl_seconds=300)
    c.set("kline", "600176", {"rows": 10}, ttl_seconds=300)
    assert c.invalidate() == 2
    assert c.get("quote", "600176") is None
    assert c.get("kline", "600176") is None


def test_stats_reflect_entries(tmp_path):
    c = _make_cache(tmp_path)
    c.set("quote", "600176", {"price": 1.0}, ttl_seconds=300)
    c.set("quote", "000001", {"price": 2.0}, ttl_seconds=300)
    c.get("quote", "600176")  # hit
    c.get("quote", "999999")  # miss
    st = c.stats()
    assert st["total_entries"] == 2
    assert st["dimension_distribution"] == {"quote": 2}
    assert st["session_hits"] == 1
    assert st["session_misses"] >= 1


def test_is_fresh(tmp_path):
    c = _make_cache(tmp_path)
    assert not c.is_fresh("quote", "600176")
    c.set("quote", "600176", {"price": 1.0}, ttl_seconds=300)
    assert c.is_fresh("quote", "600176")
    c.invalidate("quote", "600176")
    assert not c.is_fresh("quote", "600176")


def test_corrupt_file_treated_as_miss(tmp_path):
    c = _make_cache(tmp_path)
    path = c._cache_path("quote", "600176")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{corrupt json", encoding="utf-8")
    assert c.get("quote", "600176") is None


def test_numpy_scalars_roundtrip_as_native_types(tmp_path):
    """np.int64 等标量不得经 str() 写回为字符串（review fix #5）。"""
    np = pytest.importorskip("numpy")
    c = _make_cache(tmp_path)
    c.set("kline", "600176", {
        "volume": np.int64(123456),
        "close": np.float64(10.5),
        "flag": np.bool_(True),
        "arr": np.array([1, 2]),
    }, ttl_seconds=300, source="test")
    got = c.get("kline", "600176")
    assert got["volume"] == 123456
    assert type(got["volume"]) is int
    assert got["close"] == 10.5
    assert type(got["close"]) is float
    assert got["flag"] is True
    assert got["arr"] == [1, 2]
    assert type(got["arr"]) is list


def test_numpy_int_inside_records_roundtrip_as_int(tmp_path):
    """df.to_dict('records') 嵌套的 np.int64 值读回为 int 而非字符串。"""
    np = pytest.importorskip("numpy")
    c = _make_cache(tmp_path)
    records = [{"trade_date": "20260801", "volume": np.int64(88)}]
    c.set("kline", "600176", records, ttl_seconds=300, source="test")
    got = c.get("kline", "600176")
    assert got[0]["volume"] == 88
    assert type(got[0]["volume"]) is int


def test_pandas_timestamp_roundtrip(tmp_path):
    pd = pytest.importorskip("pandas")
    c = _make_cache(tmp_path)
    c.set("kline", "600176", {"trade_time": pd.Timestamp("2026-08-01 10:30:00")},
          ttl_seconds=300, source="test")
    got = c.get("kline", "600176")
    assert got["trade_time"] == "2026-08-01T10:30:00"


def test_np_timedelta64_roundtrip_as_seconds(tmp_path):
    """np.timedelta64 不得让 DataCache.set 崩溃（review fix #2）。"""
    np = pytest.importorskip("numpy")
    c = _make_cache(tmp_path)
    c.set("kline", "600176", {"dur": np.timedelta64(5, "D")},
          ttl_seconds=300, source="test")
    got = c.get("kline", "600176")
    assert got["dur"] == 432000.0  # 5 天 = 432000 秒


def test_np_complex_roundtrip_no_crash(tmp_path):
    """np.complex128 的 item() 返回 complex → 兜底 str，不崩溃。"""
    np = pytest.importorskip("numpy")
    c = _make_cache(tmp_path)
    c.set("kline", "600176", {"phase": np.complex128(1 + 2j)},
          ttl_seconds=300, source="test")
    got = c.get("kline", "600176")
    assert isinstance(got["phase"], str)
