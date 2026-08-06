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
    em_request_with_retry,
    no_proxy_session,
    proxy_bypass,
)
from ..schema import SourceResult, DimensionResult

logger = logging.getLogger(__name__)


# ---- 日期工具（skills/lib/dates 统一实现，A 股日历日上海时区） ----

from ..shared_dates import (  # noqa: E402
    shanghai_days_ago as _days_ago,
    shanghai_now as _shanghai_now,
    shanghai_today as _today,
    yyyymmdd_to_iso as _to_iso_date,
)
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

def _env_max_workers(default: int = 8) -> int:
    """INVEST_MAX_WORKERS 环境变量 → worker 数（钳制下限 1，防 0/负值挂死信号量）。"""
    try:
        return max(1, int(os.environ.get("INVEST_MAX_WORKERS", str(default))))
    except ValueError:
        return max(1, default)


def _map_parallel(
    items: list[Any],
    fn: Callable[[Any], Any],
    *,
    on_error: Callable[[Any, Exception], None] | None = None,
) -> list[tuple[Any, Any]]:
    """并行 fan-out 样板：ThreadPoolExecutor + submit-dict + as_completed。

    此前 _ms_fetch_put_call_ratio 与 _ms_fetch_new_high_ratio 各自手写一份
    （~15 行近同样板，worker 公式一致）；异常处理/worker 上限语义须手动
    保持同步——收敛为共享实现。

    Args:
        items: 待处理元素列表。
        fn: 单元素执行函数；**调用方须保证其内部单次执行有超时兜底**
            （如 _run_with_timeout），否则挂起任务会拖住 with 块的 join
            （与 _run_sources_parallel 的 daemon 线程方案不同，这里用
            `with` 是因为调用方已各自内部限时）。
        on_error: 单元素异常回调（item, exc）；提供后该元素返回
            (item, None) 占位，否则异常向上传播。

    Returns:
        [(item, result), ...]，按 items 原序（全部任务已结束，顺序确定）。
    """
    if not items:
        return []
    n = len(items)
    # worker 上限钳制下限 1（0/负值会让 ThreadPoolExecutor 拒绝 max_workers）
    max_w = max(1, min(n, _env_max_workers()))
    results: dict[int, tuple[Any, Any]] = {}
    with ThreadPoolExecutor(max_workers=max_w) as ex:
        fut_to_idx = {ex.submit(fn, item): i for i, item in enumerate(items)}
        for fut in as_completed(fut_to_idx):
            i = fut_to_idx[fut]
            item = items[i]
            try:
                results[i] = (item, fut.result())
            except Exception as exc:
                if on_error is not None:
                    on_error(item, exc)
                    results[i] = (item, None)
                else:
                    raise
    return [results[i] for i in range(n)]


def _run_sources_parallel(tasks: list[tuple[str, Callable[[], Any]]],
                          dimension: str,
                          deadline_sec: float | None = None) -> list[SourceResult]:
    """并行执行多个源查询任务，按任务索引顺序返回 SourceResult 列表。

    线程方案（daemon 线程 + 信号量限流 + 锁保护结果字典）：
    - 不用 `with ThreadPoolExecutor`：其 __exit__ 会 join 所有 worker，
      挂起源会拖死整个维度（实测曾挂死 20 分钟）。
    - daemon 线程：deadline 到期后本函数立即返回；解释器退出时挂起线程
      被直接杀死，不阻塞进程退出。
    - 语义等价旧 as_completed：结果按任务索引排列，含失败/超时占位。
    - 残留副作用：deadline 后仍有后台线程在跑（持 _BAOSTOCK_LOCK、消耗
      tushare 配额）——行为边界由 env.configure_socket_timeout() 的 socket
      默认超时保证（INVEST_SOCKET_TIMEOUT，默认 30s）。

    Args:
        tasks: [(source_name, callable), ...]
        dimension: 维度标识
        deadline_sec: 单源 deadline；None 时用 env.SOURCE_DEADLINE_SEC
            （INVEST_SOURCE_TIMEOUT）；0 或 None 表示不设限（旧语义）
    """
    if not tasks:
        return []

    n = len(tasks)
    # max_workers=8：平衡并发效率与 Tushare/akshare 限流（可通过 INVEST_MAX_WORKERS 环境变量覆盖；
    # 钳制下限 1：0/负值会让 Semaphore(0) 永久关闭 → 全部 worker 挂死）
    max_w = min(n, _env_max_workers())
    if deadline_sec is None:
        deadline_sec = env.SOURCE_DEADLINE_SEC
    if deadline_sec is not None and deadline_sec <= 0:
        deadline_sec = None  # 0 = 不设限

    results: dict[int, SourceResult] = {}
    lock = threading.Lock()
    sem = threading.Semaphore(max_w)
    done = threading.Event()
    remaining = n

    def worker(i: int, name: str, fn: Callable[[], Any]) -> None:
        nonlocal remaining
        try:
            with sem:  # 队列限流，等价 max_workers
                res = _run_one_source(name, fn, dimension)
        except Exception as exc:  # 防御：_run_one_source 已全捕获
            res = SourceResult(name, None, dimension,
                               error=f"Executor failure: {exc}")
        with lock:
            results[i] = res
            remaining -= 1
            if remaining == 0:
                done.set()

    threads = []
    for i, (name, fn) in enumerate(tasks):
        t = threading.Thread(target=worker, args=(i, name, fn),
                             name=f"src:{dimension}:{name}", daemon=True)
        t.start()
        threads.append(t)  # 仅保留引用防 GC，不 join

    start = time.monotonic()
    if deadline_sec is None:
        done.wait()  # 逃生口：等全部完成（旧语义）
    elif not done.wait(timeout=deadline_sec):
        elapsed = time.monotonic() - start  # deadline 到期：立即返回
        timed_out: list[str] = []
        with lock:
            for i, (name, _fn) in enumerate(tasks):
                if i not in results:
                    results[i] = SourceResult(
                        name, None, dimension,
                        error=f"timeout after {elapsed:.1f}s",
                        latency_ms=elapsed * 1000)
                    timed_out.append(name)
        logger.warning("dimension=%s source timeout after %.1fs: %s",
                       dimension, elapsed, ", ".join(timed_out))

    return [results[i] for i in range(n)]  # 每个索引必有值（worker 或超时占位）


def _annotate_query_params(result_map: dict[str, SourceResult],
                           params: dict[str, str]) -> None:
    """为 result_map 中的 SourceResult 设置 query_params（无论成功/失败）。"""
    for name, qp in params.items():
        if name in result_map:
            result_map[name].query_params = qp


def _run_sources_cascade(tasks: list[tuple[str, Callable[[], Any]]],
                         dimension: str) -> list[SourceResult]:
    """按优先级顺序执行源查询（R12h：首选源单发，失败才启动下一源）。

    与 _run_sources_parallel 语义对齐：结果按任务索引顺序排列；
    未执行的后续源标记「未尝试」（data=None 且无 error → 渲染层显示"未尝试"）。
    耗时：首选源成功 → 单源耗时（验收基准：非 L2 单源 ≤ 旧全量双源）。
    """
    results: list[SourceResult] = []
    succeeded = False
    for name, fn in tasks:
        if succeeded:
            results.append(SourceResult(name, None, dimension))  # 未尝试
            continue
        res = _run_one_source(name, fn, dimension)
        results.append(res)
        if res.data is not None:
            succeeded = True
    return results


def _run_one_source(name: str, fn: Callable[[], Any], dimension: str) -> SourceResult:
    """包装单个源查询为 SourceResult。"""
    start = time.time()
    try:
        data = fn()
    except Exception as e:
        elapsed = (time.time() - start) * 1000
        logger.warning("Source %s failed: %s", name, e)
        res = SourceResult(name, None, dimension, error=str(e),
                           latency_ms=elapsed)
    else:
        elapsed = (time.time() - start) * 1000
        if data is not None:
            res = SourceResult(name, data, dimension, latency_ms=elapsed)
        else:
            res = SourceResult(name, None, dimension, error="No data returned",
                               latency_ms=elapsed)
    logger.info("source=%s dim=%s latency_ms=%.0f success=%s",
                name, dimension, res.latency_ms, res.success)
    if res.latency_ms > 60_000:  # 慢源告警（开发日志与 stderr 均可观测）
        logger.warning("slow source: %s dim=%s took %.1fs",
                       name, dimension, res.latency_ms / 1000)
    return res


