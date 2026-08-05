"""Tests for skills/lib/kline_cache.py — KlineTTLCache（无网络）。"""

from __future__ import annotations

import os
import pickle
import sys
import time
from pathlib import Path

_SKILLS_LIB = Path(__file__).resolve().parents[1]
if str(_SKILLS_LIB) not in sys.path:
    sys.path.insert(0, str(_SKILLS_LIB))

import pytest

from kline_cache import KlineTTLCache  # noqa: E402

_TTL = 86400


def _cache(tmp_path: Path, **kw) -> KlineTTLCache:
    return KlineTTLCache(tmp_path / "cache", _TTL, **kw)


def _make_old(path: Path, seconds: float) -> None:
    """把文件/目录 mtime 拨旧，模拟 TTL 过期。"""
    old = time.time() - seconds
    os.utime(path, (old, old))


class TestSaveLoad:
    def test_roundtrip_layout(self, tmp_path: Path):
        c = _cache(tmp_path)
        c.save("20260722", ("tushare", "000001.SZ"), {"close": [1.0]})
        p = tmp_path / "cache" / "20260722" / "tushare" / "000001.SZ.pkl"
        assert p.is_file()
        loaded = c.load("20260722", ("tushare", "000001.SZ"))
        assert loaded == {"close": [1.0]}

    def test_ttl_expiry(self, tmp_path: Path):
        c = _cache(tmp_path)
        c.save("20260722", ("s", "x"), 1)
        p = tmp_path / "cache" / "20260722" / "s" / "x.pkl"
        _make_old(p, _TTL + 10)
        assert c.load("20260722", ("s", "x")) is None

    def test_corrupt_pickle_returns_none(self, tmp_path: Path):
        c = _cache(tmp_path)
        p = tmp_path / "cache" / "20260722" / "s" / "x.pkl"
        p.parent.mkdir(parents=True)
        p.write_bytes(b"not a pickle!!")
        assert c.load("20260722", ("s", "x")) is None

    def test_type_guard_mismatch(self, tmp_path: Path):
        c = _cache(tmp_path)
        c.save("20260722", ("s", "x"), [1, 2])
        assert c.load("20260722", ("s", "x"), type_guard=list) == [1, 2]
        assert c.load("20260722", ("s", "x"), type_guard=dict) is None

    def test_missing_returns_none(self, tmp_path: Path):
        assert _cache(tmp_path).load("20260722", ("s", "nope")) is None


class TestSaveOptions:
    def test_skip_empty_list(self, tmp_path: Path):
        c = _cache(tmp_path)
        c.save("20260722", ("s", "x"), [], skip_empty=True)
        assert not (tmp_path / "cache" / "20260722" / "s" / "x.pkl").exists()
        # skip_empty=False（gap 语义）时照存
        c.save("20260722", ("s", "y"), [], skip_empty=False)
        assert (tmp_path / "cache" / "20260722" / "s" / "y.pkl").exists()

    def test_skip_empty_none(self, tmp_path: Path):
        c = _cache(tmp_path)
        c.save("20260722", ("s", "x"), None, skip_empty=True)
        assert not (tmp_path / "cache" / "20260722" / "s" / "x.pkl").exists()

    def test_log_errors_swallows(self, tmp_path: Path, caplog):
        """log_errors=True（stock 语义）失败记 warning 不上抛；残留部分文件视为未命中。"""
        import logging

        c = _cache(tmp_path)
        unpicklable = lambda: 1  # noqa: E731 — pickle 必失败
        with pytest.raises(Exception):
            c.save("20260722", ("s", "x"), unpicklable)  # 上抛（gap 语义）
        with caplog.at_level(logging.WARNING, logger="kline_cache"):
            c.save("20260722", ("s", "x"), unpicklable, log_errors=True)  # 不抛（stock 语义）
        assert any("kline cache save failed" in r.message for r in caplog.records)
        # pickle.dump 可能残留部分文件 → 损坏 pickle → load 视为未命中
        assert c.load("20260722", ("s", "x")) is None


class TestEnabledGate:
    def test_disabled_skips_save_load(self, tmp_path: Path):
        c = _cache(tmp_path, enabled=lambda: False)
        c.save("20260722", ("s", "x"), 1)
        assert not (tmp_path / "cache").exists()
        assert c.load("20260722", ("s", "x")) is None

    def test_enabled_none_defaults_on(self, tmp_path: Path):
        c = _cache(tmp_path)
        c.save("20260722", ("s", "x"), 1)
        assert c.load("20260722", ("s", "x")) == 1


class TestCleanupOld:
    def test_removes_only_expired_dirs(self, tmp_path: Path):
        c = _cache(tmp_path)
        c.save("20260701", ("s", "a"), 1)
        c.save("20260722", ("s", "b"), 2)
        old_dir = tmp_path / "cache" / "20260701"
        _make_old(old_dir, _TTL + 100)
        c.cleanup_old()
        assert not old_dir.exists()
        assert (tmp_path / "cache" / "20260722").exists()

    def test_cleanup_ignore_errors(self, tmp_path: Path):
        c = _cache(tmp_path)
        c.save("20260701", ("s", "a"), 1)
        _make_old(tmp_path / "cache" / "20260701", _TTL + 100)
        c.cleanup_old(ignore_errors=True)  # 不抛即可
        assert not (tmp_path / "cache" / "20260701").exists()


class TestLoadOrFetch:
    def test_hit_returns_cached(self, tmp_path: Path):
        c = _cache(tmp_path)
        c.save("20260722", ("s", "x"), "cached")
        calls = []
        out = c.load_or_fetch("20260722", ("s", "x"),
                              lambda: calls.append(1) or "fetched")
        assert out == "cached"
        assert calls == []

    def test_miss_fetches_and_saves(self, tmp_path: Path):
        c = _cache(tmp_path)
        out = c.load_or_fetch("20260722", ("s", "x"), lambda: [1, 2])
        assert out == [1, 2]
        assert c.load("20260722", ("s", "x")) == [1, 2]

    def test_on_hit_callback(self, tmp_path: Path):
        c = _cache(tmp_path)
        c.save("20260722", ("s", "x"), 1)
        hits = []
        c.load_or_fetch("20260722", ("s", "x"), lambda: 2, on_hit=lambda: hits.append(1))
        assert hits == [1]

    def test_disabled_bypasses_cache(self, tmp_path: Path):
        c = _cache(tmp_path, enabled=lambda: False)
        assert c.load_or_fetch("20260722", ("s", "x"), lambda: "live") == "live"


class TestCallableRoot:
    def test_root_resolved_per_call(self, tmp_path: Path):
        """root 为 callable 时每次调用解析（monkeypatch env.STORE_DIR 兼容）。"""
        holder = {"root": tmp_path / "a"}
        c = KlineTTLCache(lambda: holder["root"], _TTL)
        c.save("20260722", ("s", "x"), 1)
        assert (tmp_path / "a" / "20260722" / "s" / "x.pkl").is_file()
        holder["root"] = tmp_path / "b"
        assert c.load("20260722", ("s", "x")) is None
        c.save("20260722", ("s", "x"), 2)
        assert (tmp_path / "b" / "20260722" / "s" / "x.pkl").is_file()
