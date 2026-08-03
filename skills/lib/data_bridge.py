"""带缓存的维度数据访问层。

在 collector 外围包装缓存层，所有 skill 通过此模块获取维度数据，
自动享受缓存命中/回源逻辑。**不修改 invest-a-stock/collector.py。**

用法::

    from skills_lib.data_bridge import get_kline, get_quote, cache_stats

    kline = get_kline("600176")
    kline = get_kline("600176", force=True)   # 强制跳过缓存回源
    stats = cache_stats()
"""

from __future__ import annotations

import importlib
import logging
from typing import Any, Callable

try:
    from .cache import DataCache, default_cache  # 同包相对导入（正常路径）
except ImportError:
    # 降级：sys.path 裸导入（当 __package__ 未设置时，如直接运行脚本）
    # 注意：此路径仅在 skills/lib/ 已在 sys.path 时有效
    from cache import DataCache, default_cache  # noqa: F811

logger = logging.getLogger(__name__)

_cache: DataCache = default_cache()

# ═════════════════════════════════════════════════════
# TTL 配置（秒） — 基准值，运行时根据交易时段动态调整
# ═════════════════════════════════════════════════════

DEFAULT_TTL: dict[str, int] = {
    "quote":         5 * 60,       # 实时行情：5 分钟
    "kline":         4 * 3600,     # K 线：4 小时
    "financials":    7 * 86400,    # 财务报表：7 天
    "valuation":     7 * 86400,    # 估值分析：7 天（独立维度，勿与 financials 共用槽位）
    "macro":         7 * 86400,    # 宏观指标：7 天（VIX 盘中例外）
    "basic_info":   30 * 86400,    # 基本信息：30 天
    "northbound":    1 * 86400,    # 北向资金：1 天
    "margin":        1 * 86400,    # 两融余额：1 天
    "ad_ratio":      5 * 60,       # 涨跌比：5 分钟
    "lu_ld_ratio":   5 * 60,       # 涨跌停比：5 分钟
    "microstructure": 5 * 60,      # 市场微观结构快照：5 分钟
    # ETF 维度（invest-a-etf canonical；L1=引擎内进程缓存，L2=本缓存层）
    "etf_spot":           60,      # ETF 全市场现价表（L1 30s 进程内，L2 跨进程）
    "etf_index_pe":       1 * 86400,  # csindex 指数 PE（日频）
    "etf_nav":            1 * 86400,  # ETF 净值序列（盘后更新）
    "etf_index_daily":    1 * 86400,  # 指数日 K（日频）
    "etf_adj_factor":     7 * 86400,  # Tushare 复权因子（仅除权日变化）
    "etf_share_history":  1 * 86400,  # Tushare 份额 + fund_daily
    "etf_industry_alloc": 7 * 86400,  # 行业配置（季度报告期）
    "etf_category_sina":  7 * 86400,  # sina 分类表（低频）
}

# 失败状态集合：collector legacy 信封的 missing + macro 全失败（macro.py:376）
_FAILURE_STATUSES = ("missing", "all_failed")


# ═════════════════════════════════════════════════════
# 通用缓存包装器
# ═════════════════════════════════════════════════════

def _fetch_dimension(
    dimension: str,
    symbol: str,
    collector_func: Callable[..., Any],
    *args: Any,
    force: bool = False,
    ttl_override: int | None = None,
    **kwargs: Any,
) -> Any:
    """通用缓存包装器：先查缓存，miss 则回源采集并写入缓存。

    Parameters
    ----------
    dimension : str
        维度名（对应 DEFAULT_TTL 中的 key）。
    symbol : str
        标的代码（缓存 key 的组成部分）。
    collector_func : callable
        回源采集函数。miss 时调用，结果写入缓存。
    force : bool
        为 True 时跳过缓存直接回源。
    ttl_override : int | None
        覆盖 DEFAULT_TTL 的自定义 TTL（秒）。

    Returns
    -------
    Any
        collector_func 的返回值，或缓存中的 data 字段。
    """
    if not force:
        cached = _cache.get(dimension, symbol)
        if cached is not None:
            logger.debug("cache hit: %s:%s", dimension, symbol)
            return cached

    logger.debug("cache miss: %s:%s, fetching...", dimension, symbol)
    data = collector_func(*args, **kwargs)

    if data is not None:
        # 跳过空集合缓存（[] / {}），避免非交易日/错误结果阻止后续重新抓取
        if isinstance(data, (list, dict)) and len(data) == 0:
            logger.debug("skipping cache for empty %s:%s result", dimension, symbol)
        elif isinstance(data, dict) and data.get("status") in _FAILURE_STATUSES:
            # 失败信封（missing / macro all_failed）不缓存：否则会在整个
            # TTL（kline 4h / financials 7d / basic_info 30d / macro 7d）内持续
            # 服务 stale 失败结果，源恢复后 journal/portfolio_review 仍读不到数据
            logger.debug("skipping cache for failed %s:%s result", dimension, symbol)
        else:
            ttl = ttl_override or DEFAULT_TTL.get(dimension, 3600)
            _cache.set(dimension, symbol, data, ttl_seconds=ttl, source="data_bridge")

    return data


# ═════════════════════════════════════════════════════
# 维度级访问函数
# ═════════════════════════════════════════════════════

def _import_lib_module_attr(module_name: str, attr: str):
    """Lazy-import *attr* from scripts/lib/<module_name>.py with actionable error.

    Raises :exc:`ModuleNotFoundError` with clear guidance when the
    invest-a-stock scripts directory is not on ``sys.path`` (i.e.,
    ``ensure_invest_a_scripts_on_path()`` hasn't been called
    before ``data_bridge`` is used).
    """
    try:
        mod = importlib.import_module(f"lib.{module_name}")
        return getattr(mod, attr)
    except ModuleNotFoundError as e:
        raise ModuleNotFoundError(
            f"Cannot import 'lib.{module_name}.{attr}' — the invest-a-stock "
            "scripts directory is not on sys.path. Call "
            "ensure_invest_a_scripts_on_path() before using data_bridge."
        ) from e


def get_kline(symbol: str, *, force: bool = False, **kwargs: Any) -> dict | None:
    """K 线数据（缓存 4h）。

    额外 kwargs 透传至 collector；**带 kwargs 时跳过缓存**——缓存 key
    不编码参数（如 start_date），不同参数产生不同数据，直调更安全。
    """
    collect_kline = _import_lib_module_attr("collector", "collect_kline")  # noqa: E402
    if kwargs:
        return collect_kline(symbol, **kwargs)
    return _fetch_dimension("kline", symbol, collect_kline, symbol, force=force)


def get_quote(symbol: str, *, force: bool = False, **kwargs: Any) -> dict | None:
    """实时行情（缓存 5min）。额外 kwargs 透传并跳过缓存。"""
    collect_quote = _import_lib_module_attr("collector", "collect_quote")  # noqa: E402
    if kwargs:
        return collect_quote(symbol, **kwargs)
    return _fetch_dimension("quote", symbol, collect_quote, symbol, force=force)


def get_financials(symbol: str, *, force: bool = False, **kwargs: Any) -> dict | None:
    """财务报表（缓存 7d）。额外 kwargs 透传并跳过缓存。"""
    collect_financials = _import_lib_module_attr("collector", "collect_financials")  # noqa: E402
    if kwargs:
        return collect_financials(symbol, **kwargs)
    return _fetch_dimension("financials", symbol, collect_financials, symbol, force=force)


def get_basic_info(symbol: str, *, force: bool = False, **kwargs: Any) -> dict | None:
    """基本信息（缓存 30d）。额外 kwargs 透传并跳过缓存。"""
    collect_basic_info = _import_lib_module_attr("collector", "collect_basic_info")  # noqa: E402
    if kwargs:
        return collect_basic_info(symbol, **kwargs)
    return _fetch_dimension("basic_info", symbol, collect_basic_info, symbol, force=force)


def get_valuation(symbol: str, *, force: bool = False, **kwargs: Any) -> dict | None:
    """估值分析（缓存 7d，独立 valuation 维度）。额外 kwargs 透传并跳过缓存。

    注意：维度 key 必须是 "valuation" 而非 "financials"——两者负载不同
    （估值含 PE 历史序列，财报含报表字段），共用缓存槽位会互相污染。
    """
    collect_valuation = _import_lib_module_attr("collector", "collect_valuation")  # noqa: E402
    if kwargs:
        return collect_valuation(symbol, **kwargs)
    return _fetch_dimension("valuation", symbol, collect_valuation, symbol, force=force)


def get_northbound(symbol: str, *, force: bool = False) -> dict | None:
    """北向资金（缓存 1d）。"""
    collect_northbound = _import_lib_module_attr("collector", "collect_northbound")  # noqa: E402
    return _fetch_dimension("northbound", symbol, collect_northbound, symbol, force=force)


def get_macro(*, force: bool = False) -> dict | None:
    """宏观快照（缓存 7d）。"""
    collect_macro_context = _import_lib_module_attr("macro", "collect_macro_context")  # noqa: E402
    # symbol='' 是故意的：宏观数据（PMI/CPI/LPR/VIX）非个股维度，不按 symbol 筛选
    return _fetch_dimension("macro", "all", collect_macro_context, "", force=force)


def get_microstructure(*, force: bool = False) -> dict | None:
    """市场微观结构快照（缓存 5min）。

    注意：依赖 invest-a-journal 的 market_microstructure 模块，
    仅在 journal skill 上下文中可用；其他 skill 调用会返回 None + 日志警告。
    """
    try:
        from market_microstructure import snapshot  # noqa: E402
    except ImportError:
        logger.warning(
            "get_microstructure() requires invest-a-journal on sys.path; "
            "call from within journal skill context or ensure path bootstrap. "
            "Returning None — callers should guard against."
        )
        return None
    return _fetch_dimension(
        "microstructure", "market", snapshot,
        force=force, ttl_override=300,
    )


def _import_etf_attr(attr: str) -> Callable[..., Any] | None:
    """Lazy-import *attr* from etf_data (invest-a-etf canonical / journal shim).

    上下文解析：
    - journal：importlib 解析到 journal shim（re-export fetch_*，见
      skills/invest-a-journal/scripts/lib/etf_data.py）
    - invest-a-etf：解析到 canonical
    - 其他上下文：ImportError/AttributeError → None + 日志警告（调用方需防 None）
    """
    try:
        mod = importlib.import_module("etf_data")
        return getattr(mod, attr)
    except (ImportError, AttributeError) as exc:
        logger.warning(
            "get_etf_*(%s) requires invest-a-etf etf_data on sys.path; "
            "returning None — callers should guard against. %s", attr, exc)
        return None


def get_etf_spot_rows(*, force: bool = False) -> list | None:
    """ETF 全市场现价表 records（缓存 60s，市场级共享一份文件）。"""
    fetch = _import_etf_attr("fetch_etf_spot_rows")
    if fetch is None:
        return None
    return _fetch_dimension("etf_spot", "market", fetch, force=force)


def get_etf_index_pe(idx_code: str, *, force: bool = False) -> dict | None:
    """csindex 指数 PE（缓存 1d；同一指数多 ETF 共享缓存键）。"""
    fetch = _import_etf_attr("fetch_etf_index_pe")
    if fetch is None:
        return None
    return _fetch_dimension("etf_index_pe", idx_code, fetch, idx_code, force=force)


def get_etf_nav(symbol: str, *, force: bool = False) -> dict | None:
    """ETF 净值历史序列（缓存 1d，fetch 内固定 400 自然日窗口）。"""
    fetch = _import_etf_attr("fetch_etf_nav")
    if fetch is None:
        return None
    return _fetch_dimension("etf_nav", symbol, fetch, symbol, force=force)


def get_etf_index_daily(idx_code: str, *, force: bool = False) -> dict | None:
    """指数日 K（缓存 1d；sh/sz 前缀路由在 fetch 内，不参与缓存键）。"""
    fetch = _import_etf_attr("fetch_etf_index_daily")
    if fetch is None:
        return None
    return _fetch_dimension("etf_index_daily", idx_code, fetch, idx_code, force=force)


def get_etf_adj_factor(symbol: str, *, force: bool = False) -> dict | None:
    """ETF 复权因子（缓存 7d，仅除权日变化）。"""
    fetch = _import_etf_attr("fetch_etf_adj_factor")
    if fetch is None:
        return None
    return _fetch_dimension("etf_adj_factor", symbol, fetch, symbol, force=force)


def get_etf_share_history(symbol: str, *, force: bool = False) -> dict | None:
    """ETF 份额历史 + fund_daily（缓存 1d，fetch 内固定 100 自然日窗口）。"""
    fetch = _import_etf_attr("fetch_etf_share_history")
    if fetch is None:
        return None
    return _fetch_dimension("etf_share_history", symbol, fetch, symbol, force=force)


def get_etf_industry_alloc(symbol: str, *, force: bool = False) -> dict | None:
    """ETF 行业配置（缓存 7d，季度报告期数据）。"""
    fetch = _import_etf_attr("fetch_etf_industry_alloc")
    if fetch is None:
        return None
    return _fetch_dimension("etf_industry_alloc", symbol, fetch, symbol, force=force)


def get_etf_category_sina(*, force: bool = False) -> dict | None:
    """sina ETF 分类表（缓存 7d，低频，市场级共享一份文件）。"""
    fetch = _import_etf_attr("fetch_etf_category_sina")
    if fetch is None:
        return None
    return _fetch_dimension("etf_category_sina", "market", fetch, force=force)


# ═════════════════════════════════════════════════════
# 管理函数
# ═════════════════════════════════════════════════════

def invalidate_symbol(symbol: str) -> int:
    """清除某标的所有维度的缓存。

    Returns
    -------
    int
        删除的缓存条目数。
    """
    count = 0
    for dim in DEFAULT_TTL:
        count += _cache.invalidate(dim, symbol)
    return count


def cache_stats() -> dict:
    """查看缓存状态。"""
    return _cache.stats()


def cache_clear() -> int:
    """清空全部缓存。"""
    return _cache.invalidate(None, None)
