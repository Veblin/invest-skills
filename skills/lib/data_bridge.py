"""带缓存的维度数据访问层。

在 collector 外围包装缓存层，所有 skill 通过此模块获取维度数据，
自动享受缓存命中/回源逻辑。**不修改 invest-a-stock/collector.py。**

用法（bootstrap 后裸导入，与各消费方一致）::

    from data_bridge import get_kline, get_quote

    kline = get_kline("600176")
    kline = get_kline("600176", force=True)   # 强制跳过缓存回源
"""

from __future__ import annotations

import importlib
import logging
from typing import Any, Callable

try:
    from .cache import DataCache, default_cache, _is_trading_hour  # 同包相对导入（正常路径）
except ImportError:
    # 降级：sys.path 裸导入（当 __package__ 未设置时，如直接运行脚本）
    # 注意：此路径仅在 skills/lib/ 已在 sys.path 时有效
    from cache import DataCache, default_cache, _is_trading_hour  # noqa: F811

try:
    from .invest_path import load_invest_a_etf_module  # 同包相对导入（正常路径）
except ImportError:
    from invest_path import load_invest_a_etf_module  # noqa: F811  # sys.path 裸导入

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
    "macro":         7 * 86400,    # 宏观指标：7 天（A 股交易时段 TTL 覆盖为 4h，见 get_macro）
    "basic_info":   30 * 86400,    # 基本信息：30 天
    "margin":        1 * 86400,    # 两融余额：1 天
    "ad_ratio":      5 * 60,       # 涨跌比：5 分钟
    "lu_ld_ratio":   5 * 60,       # 涨跌停比：5 分钟
    "microstructure": 5 * 60,      # 市场微观结构快照：5 分钟
    "market_daily_pctiles": 4 * 3600,  # 全市场分位横截面（v0.2.6）：4 小时
    "futures_basis": 4 * 3600,          # 股指期货基差/持仓（v0.2.6 F 系列）：盘中 4h、盘后日更
    # ETF 维度（invest-a-etf canonical；L1=引擎内进程缓存，L2=本缓存层）
    "etf_spot":           60,      # ETF 全市场现价表（L1 30s 进程内，L2 跨进程）
    "etf_index_pe":       1 * 86400,  # csindex 指数 PE（日频）
    "etf_nav":            6 * 3600,    # ETF 净值序列（净值 T-1 晚间公布，6h 盘中≈4.8h 保证公布后重拉，避免报告用 T-2 序列）
    "etf_index_daily":    1 * 86400,  # 指数日 K（日频）
    "etf_adj_factor":     6 * 3600,   # Tushare 复权因子：6h（TTL×0.8 盘中/×2 盘后，保证除息日开盘前必过期重拉；
                                      #   7d 时除息后 stale 因子 + 日更 NAV 会跨断点算收益率 → 假跳价）
    "etf_share_history":  1 * 86400,  # Tushare 份额 + fund_daily
    "etf_industry_alloc": 1 * 86400,  # 行业配置（季度报告期，1d 保证新报告 1d 内可见；7d/盘后×2=14d 曾让新季度配置滞后近两周）
    "etf_holdings":       1 * 86400,  # 前十大持仓（季度报告期，1d 保证新季度 1d 内可见；与 etf_industry_alloc 同惯例）
    "etf_category_sina":  7 * 86400,  # sina 分类表（低频）
}

# 失败状态集合：collector legacy 信封的 missing + macro 全失败（macro.py:376）
_FAILURE_STATUSES = ("missing", "all_failed")

# ok 信封的 payload 字段：全部为空视为空信封（防御 fetch 侧漏网，v0.2.3 补丁 #3）
_OK_ENVELOPE_PAYLOAD_KEYS = (
    "rows", "adj_map", "fund_share", "fund_daily", "allocation", "index_pe",
)


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
    max_age_seconds: int | None = None,
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
        覆盖 DEFAULT_TTL 的自定义 TTL（秒，写入时烘焙）。
    max_age_seconds : int | None
        读路径新鲜度上限（秒）：覆盖条目自带 TTL，过期即回源。
        与 ttl_override 互补——写入时点不在交易时段时，读路径仍能
        强制盘中刷新。

    Returns
    -------
    Any
        collector_func 的返回值，或缓存中的 data 字段。
    """
    if not force:
        cached = _cache.get(dimension, symbol, max_age_seconds=max_age_seconds)
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
        elif isinstance(data, dict) and data.get("status") == "ok":
            # 防御：ok 信封但 payload 字段全空（如 fetch 窗口过滤后 rows=[] 漏网）
            # → 视同失败不缓存，否则源恢复后整个 TTL 内服务空数据
            payload_keys = [k for k in _OK_ENVELOPE_PAYLOAD_KEYS if k in data]
            if payload_keys and all(not data.get(k) for k in payload_keys):
                logger.debug("skipping cache for ok-but-empty %s:%s result",
                             dimension, symbol)
            else:
                ttl = ttl_override or DEFAULT_TTL.get(dimension, 3600)
                _cache.set(dimension, symbol, data, ttl_seconds=ttl, source="data_bridge")
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


def get_macro(*, force: bool = False) -> dict | None:
    """宏观快照（缓存 7d；A 股交易时段 9:30-15:00 读路径新鲜度上限 4h）。

    新鲜度只在读路径判定（max_age_seconds，cache.get 内按 age > N 硬判定，
    无 _effective_ttl 乘子），与写入时点无关：盘后/盘前写入的条目在交易
    时段读取时按 4h 过期并回源，保证 9:30 取数恒为隔夜新鲜数据（VIX/SOX
    盘中有更新需求）。写入侧恒烘焙 7d（DEFAULT_TTL）——不再按交易时段
    烘焙短 TTL，避免盘中写入的条目当晩过期触发无谓回源（读路径单旋钮
    已覆盖全部新鲜度语义）。
    """
    collect_macro_context = _import_lib_module_attr("macro", "collect_macro_context")  # noqa: E402
    # symbol='' 是故意的：宏观数据（PMI/CPI/LPR/VIX）非个股维度，不按 symbol 筛选
    return _fetch_dimension(
        "macro", "all", collect_macro_context, "",
        force=force,
        max_age_seconds=4 * 3600 if _is_trading_hour() else None,
    )


def get_market_daily_pctiles(*, force: bool = False) -> dict | None:
    """全市场 20 日均值横截面（v0.2.6 分位数据层，缓存 4h）。

    返回 {ts_code: {avg_amount, avg_turnover, n_days}} | None（不可得）。
    fetch = 惰性增量回填最近 25 个交易日（只补缺失日）→ market_pctile.build_cross_section。
    依赖 invest-a-stock 的 market_daily/store，仅在路径可用时工作。
    """
    try:
        from lib.market_daily import pctile_as_of_rows  # noqa: E402 — invest-a-stock 路径引导
        from market_pctile import build_cross_section  # noqa: E402 — skills/lib
    except ImportError:
        logger.warning(
            "get_market_daily_pctiles() requires invest-a-stock lib on sys.path; "
            "Returning None — callers should guard against."
        )
        return None

    def _collect() -> dict | None:
        try:
            from lib.market_daily import ensure_market_daily  # noqa: E402

            ensure_market_daily(max_missing=25)
        except Exception:  # noqa: BLE001 — 增量失败不阻塞读缓存旧数据
            logger.debug("market_daily ensure failed; falling back to stored rows", exc_info=True)
        rows = pctile_as_of_rows(days=25)
        return build_cross_section(rows) or None

    return _fetch_dimension(
        "market_daily_pctiles", "market", _collect,
        force=force,
    )


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
    """Lazy-import *attr* from invest-a-etf canonical etf_data module.

    解析（v0.2.4 修复）：显式经 invest_path.load_invest_a_etf_module()
    按文件路径加载 canonical（invest-a-etf/scripts/lib/etf_data.py），与
    journal shim 同一加载器、同一 sys.modules 实例——不再裸
    ``import etf_data``（其解析依赖 sys.path 顺序：invest-a-etf lib 不在
    首位/不在路径上时 ImportError → 静默 None）。
    ImportError/AttributeError/OSError → None + 日志警告（调用方需防
    None）；OSError：canonical 文件缺失时 spec 加载抛 FileNotFoundError
    （非 ImportError 子类），同样按环境降级处理。
    """
    try:
        mod = load_invest_a_etf_module()
        return getattr(mod, attr)
    except (ImportError, AttributeError, OSError) as exc:
        logger.warning(
            "get_etf_*(%s) requires invest-a-etf etf_data; "
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
    """ETF 净值历史序列（缓存 1d，fetch 内固定 700 自然日窗口）。"""
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
    """ETF 复权因子（缓存 6h：盘中 ×0.8≈4.8h/盘后 ×2=12h，保证除息日开盘前
    必过期重拉；7d 时除息后 stale 因子 + 日更 NAV 会跨断点算收益率 → 假跳价）。"""
    fetch = _import_etf_attr("fetch_etf_adj_factor")
    if fetch is None:
        return None
    return _fetch_dimension("etf_adj_factor", symbol, fetch, symbol, force=force)


def get_etf_share_history(symbol: str, *, force: bool = False) -> dict | None:
    """ETF 份额历史 + fund_daily（缓存 1d，fetch 内固定 250 自然日窗口）。"""
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


def get_etf_holdings(symbol: str, *, force: bool = False) -> dict | None:
    """ETF 前十大持仓（缓存 1d，季度报告期数据；裸 HTTP 天天基金 jjcc 页）。

    信封与 canonical query_etf_holdings 对齐：附加 clusters（HOLDINGS_CLUSTER_MAP
    聚合，etf_data._build_holdings_clusters）——直读本桥的路径（journal ETF 等）
    不会退化回 AI 手算聚类（P0）。富化失败不静默：clusters=None + clusters_error
    字段（与「合法未映射 → []」可区分），日志 warning。
    """
    fetch = _import_etf_attr("fetch_etf_holdings")
    if fetch is None:
        return None
    env = _fetch_dimension("etf_holdings", symbol, fetch, symbol, force=force)
    if env and env.get("status") == "ok" and env.get("rows"):
        try:
            etf_data = load_invest_a_etf_module()
            env["clusters"] = etf_data._build_holdings_clusters(env["rows"])
        except Exception as exc:
            logger.warning("get_etf_holdings clusters enrich failed: %s", exc)
            env["clusters"] = None
            env["clusters_error"] = f"{type(exc).__name__}: {exc}"
    elif env is not None:
        env.setdefault("clusters", [])
    return env


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


