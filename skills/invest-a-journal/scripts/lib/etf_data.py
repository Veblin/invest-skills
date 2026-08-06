"""Thin shim — re-exports invest-a-etf etf_data (canonical owner).

journal ETF 评估路径继续 `from etf_data import query_etf_data`；
实现已迁至 skills/invest-a-etf/scripts/lib/etf_data.py。

加载与 skills/lib/data_bridge._import_etf_attr 统一走
invest_path.load_invest_a_etf_module()（v0.2.4 起共用同一加载器）：
按文件路径显式加载 canonical，避免与本 shim 模块名 ``etf_data`` 冲突，
且不依赖 sys.path 顺序（canonical 在任何 sys.path 布局下解析到同一
sys.modules 实例）。
"""

from __future__ import annotations

import sys
from pathlib import Path

_skills_lib = Path(__file__).resolve().parents[3] / "lib"
if str(_skills_lib) not in sys.path:
    sys.path.insert(0, str(_skills_lib))

from invest_path import load_invest_a_etf_module  # noqa: E402

_mod = load_invest_a_etf_module()

CSINDEX_MAP = _mod.CSINDEX_MAP
ETF_HEDGE_MAP = _mod.ETF_HEDGE_MAP
clear_etf_spot_cache = _mod.clear_etf_spot_cache
prefetch_etf_spot = _mod.prefetch_etf_spot
query_etf_data = _mod.query_etf_data
query_etf_kline = _mod.query_etf_kline
query_etf_quote = _mod.query_etf_quote
rollup_etf_quality_status = _mod.rollup_etf_quality_status
etf_share_flow = _mod.etf_share_flow
save_etf_share_snapshot = _mod.save_etf_share_snapshot

# fetch_* 自 v0.2.3 起 re-export 供 data_bridge getter 使用；v0.2.4 起
# data_bridge 经 invest_path 直接加载 canonical（不再解析到本 shim），
# 本组 re-export 保留供 `from etf_data import fetch_*` 调用方兼容。
fetch_etf_spot_rows = _mod.fetch_etf_spot_rows
fetch_etf_index_pe = _mod.fetch_etf_index_pe
fetch_etf_nav = _mod.fetch_etf_nav
fetch_etf_index_daily = _mod.fetch_etf_index_daily
fetch_etf_adj_factor = _mod.fetch_etf_adj_factor
fetch_etf_share_history = _mod.fetch_etf_share_history
fetch_etf_industry_alloc = _mod.fetch_etf_industry_alloc
fetch_etf_category_sina = _mod.fetch_etf_category_sina

__all__ = [
    "CSINDEX_MAP",
    "ETF_HEDGE_MAP",
    "clear_etf_spot_cache",
    "etf_share_flow",
    "prefetch_etf_spot",
    "query_etf_data",
    "query_etf_kline",
    "query_etf_quote",
    "rollup_etf_quality_status",
    "save_etf_share_snapshot",
    "fetch_etf_spot_rows",
    "fetch_etf_index_pe",
    "fetch_etf_nav",
    "fetch_etf_index_daily",
    "fetch_etf_adj_factor",
    "fetch_etf_share_history",
    "fetch_etf_industry_alloc",
    "fetch_etf_category_sina",
]
