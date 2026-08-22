"""Backward-compat layer — explicit re-exports from sub-modules.

v0.2.3 Phase 2: 真正拆分。_legacy.py 不再包含业务逻辑。
code-review #6（C8 系列收尾）：弃用 dir()-copy 隐式再导出——此前
for _name in vars(_mod) 会把子模块的全部 import 名（env/math/time/
_BAOSTOCK_LOCK 等）摊入包命名空间，掩蔽死名问题（C8b/C8c 清理的
正是这种死名）。此处只显式列出全仓库消费方（生产 + 测试）实际使用的
名字；新增名字须同步消费方 import 或本清单。
"""
# 模块名供包 facade 复制与 `lib.collector.<submodule>` 点访问消费方使用
from . import _base, _orchestrate, _sources  # noqa: F401

from ._base import (  # noqa: F401
    _EASTMONEY_PROXY_MSG,
    _EASTMONEY_TUN_OR_CDN_MSG,
    _baostock_code,
    _fred_date,
    _proxy_bypass,
    _ts_code,
    akshare_direct_session,
    env,
)
from ._sources import (  # noqa: F401
    _akshare_top10_code,
    _flow_amount_yuan,
    _latest_quarter_dates,
    _map_akshare_financial_keys,
    _map_akshare_kline_keys,
    _map_akshare_northbound_keys,
    _merge_cashflow_into_financials,
    _normalize_northbound_records,
    _parse_akshare_num,
    _q_akshare_shareholders,
    _q_tushare_basic,
    _q_tushare_financials,
    _qp_baostock,
)
from ._orchestrate import (  # noqa: F401
    COLLECTORS,
    _DEFAULT_DIMS,
    _PCR_MAX_DAILY_QUERIES,
    _aggregate_sellside_price_range,
    _akshare_hs300_dated_closes,
    _collect_dimension,
    _detect_price_shock,
    _dgs10_for_trade_date,
    _first_present,
    _holder_avg_price,
    _hsgt_top10_cached,
    _infer_holder_direction,
    _merge_holder_records,
    _ms_fetch_erp,
    _ms_fetch_margin,
    _ms_fetch_moneyflow,
    _ms_fetch_northbound_stock,
    _ms_fetch_sw_index,
    _ms_fetch_sw_index_akshare,
    _ms_lookup_sw_index_code,
    _ms_lookup_sw_index_code_at_level,
    _ms_subsample_trade_dates,
    _ms_sw_index_availability_label,
    _news_date_within,
    _parse_holder_change_vol,
    _peer_metrics_from_fina,
    _prior_year_end_date,
    _q_akshare_company_news_price,
    _q_akshare_management_hold,
    _q_akshare_research,
    _resolve_industry_for_pricing,
    _revenue_yoy_from_fina_rows,
    _source_has_data,
    _summarize_research,
    attach_market_structure,
    attach_phase2_extras,
    collect_all,
    collect_basic_info,
    collect_financials,
    collect_holder_changes,
    collect_industry,
    collect_industry_peers,
    collect_industry_pricing_dim,
    collect_kline,
    collect_market_structure,
    collect_northbound,
    collect_peer_comparison,
    collect_research,
    extract_industry_from_basic_info,
)
