"""v0.2.3 回归：snapshot() 信封 status 键 + 全失败快照不缓存（code-review #1 修复）。

验证：
1. 全部 8 个数据维度失败 → snapshot()["status"] == "all_failed"
2. 任一维度成功 → "ok"
3. data_bridge._fetch_dimension 对 all_failed 信封不写入 L2 缓存
   （否则全 None 快照被 5min 缓存服务，污染评估与 market-status --save）

无网络：patch 全部 _fetch_*。
"""

from __future__ import annotations

import sys
from pathlib import Path

_LIB = Path(__file__).resolve().parent.parent / "scripts" / "lib"
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

import market_microstructure  # noqa: E402

# snapshot() 内部调用的全部采集函数（独立 patch，避免真实网络）
_FETCHERS = (
    "_fetch_margin", "_fetch_ad_ratio", "_fetch_limit_pools",
    "_fetch_turnover", "_fetch_erp", "_fetch_pcr",
    "_fetch_below_book_pct", "_fetch_northbound",
)


def _fail_all(monkeypatch) -> None:
    for name in _FETCHERS:
        monkeypatch.setattr(
            market_microstructure, name,
            lambda result, _n=name: result["_errors"].append(f"{_n}: boom"),
        )


def test_snapshot_all_failed_marks_all_failed(monkeypatch):
    _fail_all(monkeypatch)
    snap = market_microstructure.snapshot()
    assert snap["status"] == "all_failed"
    # 无任何数据维度有值
    assert all(snap[k] is None for k in market_microstructure._SNAPSHOT_DATA_KEYS)


def test_snapshot_partial_success_marks_ok(monkeypatch):
    _fail_all(monkeypatch)

    def _fake_ad_ratio(result):
        result["ad_ratio"] = 1.2

    monkeypatch.setattr(market_microstructure, "_fetch_ad_ratio", _fake_ad_ratio)
    snap = market_microstructure.snapshot()
    assert snap["status"] == "ok"


def test_all_failed_snapshot_not_cached_by_data_bridge(monkeypatch, tmp_path):
    """全失败信封不得写入 L2：否则 TTL 内每次评估都被 all-None 快照污染。"""
    _fail_all(monkeypatch)

    from cache import DataCache
    import data_bridge

    monkeypatch.setattr(data_bridge, "_cache", DataCache(cache_dir=tmp_path / "cache"))

    snap = market_microstructure.snapshot()
    assert snap["status"] == "all_failed"

    out = data_bridge._fetch_dimension(
        "microstructure", "market", market_microstructure.snapshot,
        ttl_override=300,
    )
    assert out["status"] == "all_failed"
    assert data_bridge._cache.get("microstructure", "market") is None


def test_ok_snapshot_cached_by_data_bridge(monkeypatch, tmp_path):
    """正常快照照常缓存（5min 去重不回归）。"""
    _fail_all(monkeypatch)

    def _fake_ad_ratio(result):
        result["ad_ratio"] = 1.2

    monkeypatch.setattr(market_microstructure, "_fetch_ad_ratio", _fake_ad_ratio)

    from cache import DataCache
    import data_bridge

    monkeypatch.setattr(data_bridge, "_cache", DataCache(cache_dir=tmp_path / "cache"))

    out = data_bridge._fetch_dimension(
        "microstructure", "market", market_microstructure.snapshot,
        ttl_override=300,
    )
    assert out["status"] == "ok"
    assert data_bridge._cache.get("microstructure", "market") is not None
