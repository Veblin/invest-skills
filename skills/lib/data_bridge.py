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

import logging
from typing import Any, Callable

try:
    from .cache import DataCache, default_cache  # 同包相对导入（最安全）
except ImportError:
    from cache import DataCache, default_cache  # 降级：sys.path 裸导入

logger = logging.getLogger(__name__)

_cache: DataCache = default_cache()

# ═════════════════════════════════════════════════════
# TTL 配置（秒） — 基准值，运行时根据交易时段动态调整
# ═════════════════════════════════════════════════════

DEFAULT_TTL: dict[str, int] = {
    "quote":         5 * 60,       # 实时行情：5 分钟
    "kline":         4 * 3600,     # K 线：4 小时
    "financials":    7 * 86400,    # 财务报表：7 天
    "macro":         7 * 86400,    # 宏观指标：7 天（VIX 盘中例外）
    "basic_info":   30 * 86400,    # 基本信息：30 天
    "northbound":    1 * 86400,    # 北向资金：1 天
    "margin":        1 * 86400,    # 两融余额：1 天
    "ad_ratio":      5 * 60,       # 涨跌比：5 分钟
    "lu_ld_ratio":   5 * 60,       # 涨跌停比：5 分钟
    "microstructure": 5 * 60,      # 市场微观结构快照：5 分钟
}


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
        ttl = ttl_override or DEFAULT_TTL.get(dimension, 3600)
        _cache.set(dimension, symbol, data, ttl_seconds=ttl, source="data_bridge")

    return data


# ═════════════════════════════════════════════════════
# 维度级访问函数
# ═════════════════════════════════════════════════════

def get_kline(symbol: str, *, force: bool = False) -> dict | None:
    """K 线数据（缓存 4h）。"""
    from lib.collector import collect_kline  # noqa: E402
    return _fetch_dimension("kline", symbol, collect_kline, symbol, force=force)


def get_quote(symbol: str, *, force: bool = False) -> dict | None:
    """实时行情（缓存 5min）。"""
    from lib.collector import collect_quote  # noqa: E402
    return _fetch_dimension("quote", symbol, collect_quote, symbol, force=force)


def get_financials(symbol: str, *, force: bool = False) -> dict | None:
    """财务报表（缓存 7d）。"""
    from lib.collector import collect_financials  # noqa: E402
    return _fetch_dimension("financials", symbol, collect_financials, symbol, force=force)


def get_basic_info(symbol: str, *, force: bool = False) -> dict | None:
    """基本信息（缓存 30d）。"""
    from lib.collector import collect_basic_info  # noqa: E402
    return _fetch_dimension("basic_info", symbol, collect_basic_info, symbol, force=force)


def get_northbound(symbol: str, *, force: bool = False) -> dict | None:
    """北向资金（缓存 1d）。"""
    from lib.collector import collect_northbound  # noqa: E402
    return _fetch_dimension("northbound", symbol, collect_northbound, symbol, force=force)


def get_macro(*, force: bool = False) -> dict | None:
    """宏观快照（缓存 7d）。"""
    from lib.macro import collect_macro_context  # noqa: E402
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
