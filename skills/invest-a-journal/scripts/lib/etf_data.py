"""Thin shim — re-exports invest-a-etf etf_data (canonical owner).

journal ETF 评估路径继续 `from etf_data import query_etf_data`；
实现已迁至 skills/invest-a-etf/scripts/lib/etf_data.py。

使用 importlib 按文件路径加载，避免与本 shim 模块名 ``etf_data`` 冲突。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_skills_lib = Path(__file__).resolve().parents[3] / "lib"
if str(_skills_lib) not in sys.path:
    sys.path.insert(0, str(_skills_lib))

from invest_path import invest_a_etf_lib_dir  # noqa: E402

_ETF_LIB = invest_a_etf_lib_dir()
_ETF_LIB_S = str(_ETF_LIB)
if _ETF_LIB_S not in sys.path:
    sys.path.insert(0, _ETF_LIB_S)

_spec = importlib.util.spec_from_file_location(
    "invest_a_etf_etf_data",
    _ETF_LIB / "etf_data.py",
)
if _spec is None or _spec.loader is None:
    raise ImportError(f"cannot load invest-a-etf etf_data from {_ETF_LIB}")
_mod = importlib.util.module_from_spec(_spec)
sys.modules["invest_a_etf_etf_data"] = _mod
_spec.loader.exec_module(_mod)

CSINDEX_MAP = _mod.CSINDEX_MAP
ETF_HEDGE_MAP = _mod.ETF_HEDGE_MAP
clear_etf_spot_cache = _mod.clear_etf_spot_cache
prefetch_etf_spot = _mod.prefetch_etf_spot
query_etf_data = _mod.query_etf_data
query_etf_kline = _mod.query_etf_kline
query_etf_quote = _mod.query_etf_quote
rollup_etf_quality_status = _mod.rollup_etf_quality_status

__all__ = [
    "CSINDEX_MAP",
    "ETF_HEDGE_MAP",
    "clear_etf_spot_cache",
    "prefetch_etf_spot",
    "query_etf_data",
    "query_etf_kline",
    "query_etf_quote",
    "rollup_etf_quality_status",
]
