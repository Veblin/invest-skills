"""数据采集模块。封装各数据源，依赖 env.py 做可用性检测。

设计模式（参考 last30days-skill 的 parallel fan-out）：
  每个维度下，对所有可用源并行查询 → SourceResult 归一化 → DimensionResult 合并。
  失败不阻塞，选取最优源为主数据。

数据源策略（v0.3+ 并行取证）：
  有 Token: Tushare ∥ akshare ∥ baostock ∥ 腾讯 → 各渠道并行查询 → 独立记录 → 汇总为证
  无 Token: akshare ∥ baostock ∥ 腾讯 → 各渠道并行查询 → 独立记录 → 汇总为证
"""

from __future__ import annotations

import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import redirect_stdout
from datetime import datetime, timezone, timedelta
from io import StringIO
from typing import Any, Callable

from .. import env
from ..nums import coalesce_field, safe_float
from ..proxy import (
    EASTMONEY_BLOCKED_KEYWORDS as _EASTMONEY_BLOCKED_KEYWORDS,
    EASTMONEY_FAILURE_PROXY_MARKER,
    EASTMONEY_FAILURE_TUN_MARKER,
    akshare_direct_session,
    akshare_push2_available,
    no_proxy_session,
    proxy_bypass,
)
from ..schema import SourceResult, DimensionResult

logger = logging.getLogger(__name__)


# ---- 日期工具（函数形式，避免导入时固化；A 股日历日统一上海时区） ----

def _shanghai_now() -> datetime:
    from zoneinfo import ZoneInfo
    return datetime.now(ZoneInfo("Asia/Shanghai"))


def _today() -> str:
    return _shanghai_now().strftime("%Y%m%d")


def _days_ago(n: int) -> str:
    return (_shanghai_now() - timedelta(days=n)).strftime("%Y%m%d")


from ..shared_dates import yyyymmdd_to_iso as _to_iso_date  # noqa: E402
from ..shared_codes import exchange_code as _exchange_code  # noqa: E402

_fred_date = _to_iso_date  # 向后兼容


def _latest_quarter_end() -> str:
    """返回最近一个已完整的季度末日期（0331/0630/0930/1231）。

    确保季度末日期的完整日已经过去（不提前返回当天）。
    """
    from datetime import date
    now = _shanghai_now()
    today = now.date()
    quarter_ends = [
        (now.year, "0331"),
        (now.year, "0630"),
        (now.year, "0930"),
        (now.year, "1231"),
    ]
    for y, md in reversed(quarter_ends):
        d = datetime.strptime(f"{y}{md}", "%Y%m%d")
        # 用 > 确保季度末整日已过（如 6/30 当天仍返回 Q1，7/1 起返回 Q2）
        # 注：季度末日当天（如 3/31）金融数据尚未披露，提前返回无害
        if today > d.date():
            return f"{y}{md}"
    return f"{now.year - 1}1231"


# ---- 交易所代码转换（共享函数，三种格式统一调度） ----

def _ts_code(symbol: str) -> str:
    """转为 Tushare 格式：600176 → 600176.SH（委托 _exchange_code）。"""
    return _exchange_code(symbol)["tushare"]


# 向后兼容：测试与外部调用仍可从 collector 导入 _proxy_bypass
_proxy_bypass = proxy_bypass

# Baostock 全局 socket 非线程安全，需串行化访问
_BAOSTOCK_LOCK = threading.Lock()

_EASTMONEY_PROXY_MSG = (
    "东方财富(East Money) API 连接失败。"
    f"{EASTMONEY_FAILURE_PROXY_MARKER}，请在 Clash 规则中将 DOMAIN-SUFFIX,eastmoney.com,DIRECT；"
    "或暂时关闭全局代理后重试。"
    "可改用 Tushare / Baostock 作为替代数据源。"
)
_EASTMONEY_TUN_OR_CDN_MSG = (
    f"东方财富 {EASTMONEY_FAILURE_TUN_MARKER}（非 HTTP 代理问题，可能为 TUN 劫持或 CDN 限制）。"
    "已使用 Tushare / Baostock 替代。"
)


def _is_eastmoney_blocked_error(error: str) -> bool:
    """检测异常消息是否明确指向东方财富。"""
    return any(kw in str(error) for kw in _EASTMONEY_BLOCKED_KEYWORDS)


def _eastmoney_failure_message() -> str:
    from ..proxy import proxy_status

    status = proxy_status(probe=False)
    if status.get("bypass_effective"):
        return _EASTMONEY_TUN_OR_CDN_MSG
    return _EASTMONEY_PROXY_MSG


def _reraise_eastmoney_api_error(exc: Exception) -> None:
    """在东方财富 akshare 接口内，将连接失败转为可操作提示。

    仅在已知调用东方财富 API 的函数中使用，避免误伤同花顺等其他源。
    """
    msg = _eastmoney_failure_message()
    if _is_eastmoney_blocked_error(str(exc)):
        raise RuntimeError(msg) from exc
    err = str(exc)
    if any(kw in err for kw in (
        "Connection", "Remote end closed", "RemoteDisconnected", "ProxyError",
        "Max retries exceeded",
    )):
        raise RuntimeError(msg) from exc
    raise exc


def _baostock_code(symbol: str) -> str:
    """Baostock 证券代码：sz. / sh. / bj. 前缀（委托 _exchange_code）。"""
    return _exchange_code(symbol)["baostock"]


# ---- 并行执行辅助 ----

def _run_sources_parallel(tasks: list[tuple[str, Callable[[], Any]]],
                          dimension: str) -> list[SourceResult]:
    """并行执行多个源查询任务，返回 SourceResult 列表。

    last30days 的 ThreadPoolExecutor fan-out 模式：
    - 每个任务独立提交
    - 失败不阻塞其他任务
    - 返回所有结果（含失败）供合并

    Args:
        tasks: [(source_name, callable), ...]
        dimension: 维度标识
    """
    if not tasks:
        return []

    sources: list[SourceResult | None] = []
    # max_workers=8：平衡并发效率与 Tushare/akshare 限流（可通过 INVEST_MAX_WORKERS 环境变量覆盖）
    max_w = int(os.environ.get("INVEST_MAX_WORKERS", "8"))
    with ThreadPoolExecutor(max_workers=min(len(tasks), max_w)) as executor:
        futures = {
            executor.submit(_run_one_source, name, fn, dimension): i
            for i, (name, fn) in enumerate(tasks)
        }
        results: dict[int, SourceResult] = {}
        for future in as_completed(futures):
            idx = futures[future]
            try:
                results[idx] = future.result()
            except Exception as exc:
                results[idx] = SourceResult(
                    source=f"__internal__",
                    data=None,
                    dimension=dimension,
                    error=f"Executor failure: {exc}",
                )
        sources = [results.get(i) for i in range(len(tasks))]

    return [s for s in sources if s is not None]


def _annotate_query_params(result_map: dict[str, SourceResult],
                           params: dict[str, str]) -> None:
    """为 result_map 中的 SourceResult 设置 query_params（无论成功/失败）。"""
    for name, qp in params.items():
        if name in result_map:
            result_map[name].query_params = qp


def _run_one_source(name: str, fn: Callable[[], Any], dimension: str) -> SourceResult:
    """包装单个源查询为 SourceResult。"""
    start = time.time()
    try:
        data = fn()
        elapsed = (time.time() - start) * 1000
        if data is not None:
            return SourceResult(name, data, dimension, latency_ms=elapsed)
        return SourceResult(name, None, dimension, error="No data returned",
                           latency_ms=elapsed)
    except Exception as e:
        elapsed = (time.time() - start) * 1000
        logger.warning("Source %s failed: %s", name, e)
        return SourceResult(name, None, dimension, error=str(e),
                           latency_ms=elapsed)


