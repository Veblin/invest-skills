"""Thin shim — re-exports invest-a-etf etf_data (canonical owner).

journal ETF 评估路径继续 `from etf_data import query_etf_data`；
实现已迁至 skills/invest-a-etf/scripts/lib/etf_data.py。

加载与 skills/lib/data_bridge._import_etf_attr 统一走
invest_path.load_invest_a_etf_module()（v0.2.4 起共用同一加载器）：
按文件路径显式加载 canonical，避免与本 shim 模块名 ``etf_data`` 冲突，
且不依赖 sys.path 顺序（canonical 在任何 sys.path 布局下解析到同一
sys.modules 实例）。

**模块身份（F1，独立复检 2026-10-09）**：仅静态别名只能让 import 名可用，
不能让 `patch("etf_data.X", fake)` 生效——patch 改的是本 shim 的命名空间，
而 canonical 函数按**自身模块 globals** 查找 `_bridge_get`/`query_etf_kline`
等名字（D13 mock 失效），journal-first 预加载 shim 的 pytest 会真实联网。
故本文件在转发完毕后就地做 **sys.modules 自替换**：``etf_data`` 这一名字
从此解析到 canonical 模块本体（import 机制支持执行期替换 sys.modules[自身名]），
journal 上下文里的属性读写与 patch 都直接落在 canonical 上。
静态别名与 ``_mod``/``__all__`` 保留：按路径加载本文件的测试与
``from etf_data import X`` 在替换生效前的窗口期同样可用。
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

# 完整转发 canonical 导出（含测试辅助 clear_etf_spot_cache 与 fetch_*）——
# shim 是 journal 上下文里 etf_data 的完整代理：pytest 会话中 journal lib
# 可能遮蔽 canonical（conftest 顺序依赖），任一 `from etf_data import X`
# 都必须可用，否则 etf 测试在 etf 目录在前的调用顺序下 collection 失败
# （review 第三轮 #1，实证复现）。
fetch_etf_spot_rows = _mod.fetch_etf_spot_rows
fetch_etf_index_pe = _mod.fetch_etf_index_pe
fetch_etf_nav = _mod.fetch_etf_nav
fetch_etf_index_daily = _mod.fetch_etf_index_daily
fetch_etf_adj_factor = _mod.fetch_etf_adj_factor
fetch_etf_share_history = _mod.fetch_etf_share_history
fetch_etf_industry_alloc = _mod.fetch_etf_industry_alloc
fetch_etf_category_sina = _mod.fetch_etf_category_sina
fetch_etf_holdings = _mod.fetch_etf_holdings
query_etf_holdings = _mod.query_etf_holdings
build_holdings_clusters = _mod.build_holdings_clusters  # A04 公开聚类 API（访问层富化经此）

# v0.3.2 B 数据包新增公开面（journal ETF 评估路径的完整代理，继续全表面转发）
query_etf_identity = _mod.query_etf_identity
query_etf_fees = _mod.query_etf_fees
query_etf_index_exposure = _mod.query_etf_index_exposure
query_etf_est_net_assets = _mod.query_etf_est_net_assets
fetch_etf_index_cons = _mod.fetch_etf_index_cons
fetch_etf_index_weight = _mod.fetch_etf_index_weight
compute_etf_tracking = _mod.compute_etf_tracking
compute_tracking_metrics = _mod.compute_tracking_metrics
build_evidence_budget = _mod.build_evidence_budget
build_data_status = _mod.build_data_status

# R15：消费面完整转发（按仓库全量 `from etf_data import X` 扫描补齐）——
# journal lib 在 sys.path 前位时本 shim 会遮蔽 canonical，缺名会让 ETF 测试
# collection 直接 ImportError。不用模块级 __getattr__ 惰性代理：读转发做不到
# 写转发（patch 不生效，D13 反例见文末 sys.modules 自替换），且隐式面不可审计。
# 新增消费名请同步加到这里，test_etf_shim 的消费面扫描会兜底检出遗漏。
# —— 常量/表 ——
ETF_IDENTITY = _mod.ETF_IDENTITY
ETF_FEE_SCHEDULE = _mod.ETF_FEE_SCHEDULE
ETF_TO_SW_INDUSTRY = _mod.ETF_TO_SW_INDUSTRY
SECTOR_VALUATION_MAP = _mod.SECTOR_VALUATION_MAP
HOLDINGS_CLUSTER_MAP = _mod.HOLDINGS_CLUSTER_MAP
HOLDINGS_EVIDENCE_FIELDS = _mod.HOLDINGS_EVIDENCE_FIELDS
HOLDING_SCOPE_TOP10 = _mod.HOLDING_SCOPE_TOP10
DIMENSION_STATES = _mod.DIMENSION_STATES
TRADING_DAYS_PER_YEAR = _mod.TRADING_DAYS_PER_YEAR
TrackingCaliberError = _mod.TrackingCaliberError
_CATEGORY_LABELS = _mod._CATEGORY_LABELS
_PE_CALIBER_SHARE = _mod._PE_CALIBER_SHARE
_PE_CALIBER_CIRCULATING = _mod._PE_CALIBER_CIRCULATING
# —— 查询/计算 API（续）——
query_etf_category = _mod.query_etf_category
resolve_tracked_index = _mod.resolve_tracked_index
query_etf_share_history = _mod.query_etf_share_history
query_etf_kline_history = _mod.query_etf_kline_history
query_sector_valuation_guide = _mod.query_sector_valuation_guide
compute_history_stats = _mod.compute_history_stats
baostock_code = _mod.baostock_code
fetch_etf_kline_baostock = _mod.fetch_etf_kline_baostock
# —— 私有消费者（模块内互调与测试直接从 etf_data 取用；同上必须显式转发）——
_bridge_get = _mod._bridge_get
_lookup_etf_spot_row = _mod._lookup_etf_spot_row
_lookup_hedge = _mod._lookup_hedge
_q_tencent_etf_quote = _mod._q_tencent_etf_quote
_spot_row_to_quote = _mod._spot_row_to_quote
_apply_spot_row_to_profile = _mod._apply_spot_row_to_profile
_em_to_premium_discount = _mod._em_to_premium_discount
_read_via = _mod._read_via
_auto_flags = _mod._auto_flags
_fetch_csindex_pe = _mod._fetch_csindex_pe
_attach_valuation_guide = _mod._attach_valuation_guide
_infer_category_from_name = _mod._infer_category_from_name
_align_legs_by_date = _mod._align_legs_by_date
_dated_values = _mod._dated_values
_norm_trade_date = _mod._norm_trade_date
_tracking_deviation_raw = _mod._tracking_deviation_raw
_aligned_nav_returns = _mod._aligned_nav_returns
_latest_rsi = _mod._latest_rsi
_build_holdings_clusters = _mod._build_holdings_clusters
_decode_em_page = _mod._decode_em_page
_holdings_missing = _mod._holdings_missing
_parse_holdings_blocks = _mod._parse_holdings_blocks

__all__ = [
    "CSINDEX_MAP",
    "ETF_HEDGE_MAP",
    "ETF_IDENTITY",
    "ETF_FEE_SCHEDULE",
    "ETF_TO_SW_INDUSTRY",
    "SECTOR_VALUATION_MAP",
    "HOLDINGS_CLUSTER_MAP",
    "HOLDINGS_EVIDENCE_FIELDS",
    "HOLDING_SCOPE_TOP10",
    "DIMENSION_STATES",
    "TRADING_DAYS_PER_YEAR",
    "TrackingCaliberError",
    "clear_etf_spot_cache",
    "etf_share_flow",
    "prefetch_etf_spot",
    "query_etf_data",
    "query_etf_kline",
    "query_etf_kline_history",
    "query_etf_quote",
    "query_etf_category",
    "resolve_tracked_index",
    "query_etf_share_history",
    "query_sector_valuation_guide",
    "rollup_etf_quality_status",
    "save_etf_share_snapshot",
    "compute_history_stats",
    "baostock_code",
    "fetch_etf_spot_rows",
    "fetch_etf_index_pe",
    "fetch_etf_nav",
    "fetch_etf_index_daily",
    "fetch_etf_adj_factor",
    "fetch_etf_share_history",
    "fetch_etf_industry_alloc",
    "fetch_etf_category_sina",
    "fetch_etf_kline_baostock",
    "build_holdings_clusters",
    "query_etf_identity",
    "query_etf_fees",
    "query_etf_index_exposure",
    "query_etf_est_net_assets",
    "fetch_etf_index_cons",
    "fetch_etf_index_weight",
    "compute_etf_tracking",
    "compute_tracking_metrics",
    "build_evidence_budget",
    "build_data_status",
]

# ── F1（独立复检）：模块身份透明——journal-first 下 `etf_data` 即 canonical ──
# 反例（2026-10-09 实证）：预加载本 shim 后运行 ETF 测试，`patch("etf_data._bridge_get")`
# 只改 shim 命名空间，canonical 的 query_etf_kline/compute_etf_tracking 仍按自身
# globals 查找 → mock 失效、真实联网（tushare 40101 / akshare 进度条，13 项失败）。
# 在转发完成后把「本名字」换成 canonical 本体：后续 import/属性读写/patch 全部
# 作用于 canonical（import 机制支持执行期替换 sys.modules[自身名]，替换后返回
# 的是替换对象）。按路径加载本文件（测试 `_load_journal_shim`）时 `__name__`
# 是加载名，替换只影响该名字的 sys.modules 条目，返回对象仍是本模块（别名齐全）。
sys.modules[__name__] = _mod
