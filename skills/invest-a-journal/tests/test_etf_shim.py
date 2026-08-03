"""Shim smoke: journal etf_data re-exports invest-a-etf canonical module."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_LIB = Path(__file__).resolve().parent.parent / "scripts" / "lib"

from invest_path import invest_a_etf_lib_dir  # noqa: E402  # skills/lib via conftest


def _load_journal_shim():
    """Load journal shim by file path (avoid collision with canonical etf_data)."""
    for key in ("journal_etf_shim", "invest_a_etf_etf_data"):
        sys.modules.pop(key, None)
    spec = importlib.util.spec_from_file_location("journal_etf_shim", _LIB / "etf_data.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_shim_reexports_same_map_objects():
    shim = _load_journal_shim()
    canon = sys.modules["invest_a_etf_etf_data"]
    assert shim.ETF_HEDGE_MAP is canon.ETF_HEDGE_MAP
    assert shim.CSINDEX_MAP is canon.CSINDEX_MAP


def test_shim_reexports_maps_content():
    shim = _load_journal_shim()
    assert "510300" in shim.ETF_HEDGE_MAP
    assert shim.CSINDEX_MAP.get("563300") == "932000"


def test_shim_callables_are_canonical():
    shim = _load_journal_shim()
    canon = sys.modules["invest_a_etf_etf_data"]
    assert shim.query_etf_data is canon.query_etf_data
    assert shim.query_etf_quote is canon.query_etf_quote
    assert shim.query_etf_kline is canon.query_etf_kline
    assert shim.prefetch_etf_spot is canon.prefetch_etf_spot
    assert shim.rollup_etf_quality_status is canon.rollup_etf_quality_status


def test_shim_fetch_functions_are_canonical():
    """v0.2.3：data_bridge._import_etf_attr 在 journal 上下文解析到 shim，
    fetch_* 必须 re-export 且与 canonical 同一性一致。"""
    shim = _load_journal_shim()
    canon = sys.modules["invest_a_etf_etf_data"]
    for name in (
        "fetch_etf_spot_rows",
        "fetch_etf_index_pe",
        "fetch_etf_nav",
        "fetch_etf_index_daily",
        "fetch_etf_adj_factor",
        "fetch_etf_share_history",
        "fetch_etf_industry_alloc",
        "fetch_etf_category_sina",
    ):
        assert getattr(shim, name) is getattr(canon, name), name
        assert name in shim.__all__, name


def test_invest_a_etf_lib_dir_resolves():
    lib = invest_a_etf_lib_dir()
    assert lib.name == "lib"
    assert lib.parent.name == "scripts"
    assert (lib / "etf_data.py").is_file()
