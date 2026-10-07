"""Universe module smoke — ts_code mapping covered by skills/lib/tests/test_codes.py."""

from __future__ import annotations

import pytest

import universe


def test_universe_module_imports_shared_codes():
    """Gap-scan universe delegates symbol/board helpers to skills/lib/codes."""
    assert hasattr(universe, "symbol_to_ts_code")
    assert hasattr(universe, "classify_board")
    assert universe.symbol_to_ts_code("600176") == "600176.SH"
    assert universe.classify_board("688001.SH") == "科创板"


# ======================================================================
# 成分股来源可追溯（评审 2026-09-23）：来源 sidecar
# ======================================================================


def test_provenance_roundtrip_and_cache_path(tmp_path, monkeypatch):
    """sidecar 与缓存同目录同日期；写入后可读回。"""
    from lib import env as _env

    import universe as uni

    monkeypatch.setattr(_env, "STORE_DIR", tmp_path)
    path = uni._provenance_path("20260923")
    assert path.name == "universe_20260923.provenance.json"
    assert path.parent == uni._cache_path("20260923").parent
    assert uni._load_provenance(path) is None  # 不存在 → None（按未记录处理）

    uni._save_provenance(path, {"csi300": "akshare index_stock_cons_sina", "star50": None})
    assert uni._load_provenance(path) == {
        "csi300": "akshare index_stock_cons_sina", "star50": None}


def test_build_universe_cache_hit_without_sidecar_reports_unknown(tmp_path, monkeypatch):
    """复用缓存且无 sidecar → per_index 为空（调用方须按「未记录」呈现）。"""
    import pickle

    from lib import env as _env

    import universe as uni

    monkeypatch.setattr(_env, "STORE_DIR", tmp_path)
    monkeypatch.setattr(uni, "shanghai_today", lambda: "20260923")
    cache_path = uni._cache_path("20260923")
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    stocks = [uni.StockInfo(ts_code="000001.SZ", name="平安银行",
                            index_membership=["csi300"], board="主板", list_date="")]
    with open(cache_path, "wb") as f:
        pickle.dump(stocks, f)

    prov: dict = {}
    out = uni.build_universe(provenance=prov)
    assert [s.ts_code for s in out] == ["000001.SZ"]
    assert prov == {"from_cache": True, "per_index": {}}


def test_build_universe_cache_hit_reads_sidecar(tmp_path, monkeypatch):
    """复用缓存但 sidecar 存在 → 来源如实回填（含降级到 sina / 未纳入的指数）。"""
    import pickle

    from lib import env as _env

    import universe as uni

    monkeypatch.setattr(_env, "STORE_DIR", tmp_path)
    monkeypatch.setattr(uni, "shanghai_today", lambda: "20260923")
    cache_path = uni._cache_path("20260923")
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    stocks = [uni.StockInfo(ts_code="000001.SZ", name="平安银行",
                            index_membership=["csi300"], board="主板", list_date="")]
    with open(cache_path, "wb") as f:
        pickle.dump(stocks, f)
    uni._save_provenance(uni._provenance_path("20260923"), {
        "csi300": "akshare index_stock_cons_sina",
        "a500": "Tushare index_weight",
        "star50": None,
    })

    prov: dict = {}
    uni.build_universe(provenance=prov)
    assert prov["from_cache"] is True
    assert prov["per_index"]["csi300"] == "akshare index_stock_cons_sina"
    assert prov["per_index"]["a500"] == "Tushare index_weight"
    assert prov["per_index"]["star50"] is None


@pytest.mark.parametrize("payload", ["[]", "null", '"text"', "123"])
def test_load_provenance_non_object_is_missing(tmp_path, monkeypatch, payload):
    """合法 JSON 但顶层不是对象 → 按「来源未记录」处理，不得抛异常。

    评审续二：`data.get(...)` 抛在 try 之外，会一路打断 build_universe 让整次
    扫描中止（loader 却承诺「损坏返回 None」）。
    """
    from lib import env as _env

    import universe as uni

    monkeypatch.setattr(_env, "STORE_DIR", tmp_path)
    path = uni._provenance_path("20260923")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload, encoding="utf-8")
    assert uni._load_provenance(path) is None


def test_build_universe_non_object_sidecar_does_not_abort(tmp_path, monkeypatch):
    """非对象 sidecar 不得中止扫描：缓存照样可用，来源记为未记录。"""
    import pickle

    from lib import env as _env

    import universe as uni

    monkeypatch.setattr(_env, "STORE_DIR", tmp_path)
    monkeypatch.setattr(uni, "shanghai_today", lambda: "20260923")
    cache_path = uni._cache_path("20260923")
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    with open(cache_path, "wb") as f:
        pickle.dump([uni.StockInfo("000001.SZ", "平安银行", ["csi300"], "主板", "")], f)
    uni._provenance_path("20260923").write_text("[]", encoding="utf-8")

    prov: dict = {}
    out = uni.build_universe(provenance=prov)   # 不得抛异常
    assert [s.ts_code for s in out] == ["000001.SZ"]
    assert prov == {"from_cache": True, "per_index": {}}
