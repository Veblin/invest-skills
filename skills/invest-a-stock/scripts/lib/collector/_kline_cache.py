"""同日 K 线缓存（pickle，源隔离）— 委托 skills/lib/kline_cache.KlineTTLCache。

路径: {STORE_DIR}/collect_kline_cache/{yyyymmdd}/{source}/{symbol}__{sd}_{ed}{__qfq}.pkl
- 键含 source：tushare.daily/akshare/baostock 与 tickflow.kline 互不污染
- 键含 qfq 标记：前复权语义变更时新键生效，不复权旧缓存自动失效
- 键含 sd/ed 查询窗口：默认 400 日 与 --deep 730 日 互不误用（只按 symbol 缓存
  会导致 deep 模式复用 400 日截断数据）
- TTL 1 天（mtime）：同日重复采集命中；次日必然 miss（本 skill 语义，参数化在 canonical）
- INVEST_KLINE_CACHE=0 禁用（逃生口）
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Callable

from .._invest_path import ensure_invest_a_scripts_on_path

ensure_invest_a_scripts_on_path()

from .. import env  # noqa: E402
from ..kline_cache import KlineTTLCache  # noqa: E402
from ..shared_dates import shanghai_today  # noqa: E402

logger = logging.getLogger(__name__)

CACHE_TTL_SEC = 86400  # 1 天（mtime 基准）

_CACHE = KlineTTLCache(
    lambda: env.STORE_DIR / "collect_kline_cache",
    CACHE_TTL_SEC,
    enabled=lambda: os.environ.get("INVEST_KLINE_CACHE", "1") != "0",
)


def _cache_parts(symbol: str, source: str, sd: str, ed: str, qfq: bool) -> tuple[str, str]:
    marker = "__qfq" if qfq else ""
    return (source, f"{symbol}__{sd}_{ed}{marker}")


def cleanup_old() -> None:
    """清理超 TTL 的日期目录（按目录 mtime）。"""
    _CACHE.cleanup_old(ignore_errors=True)


def _suppress_if_abandoned(fetch: Callable[[], list[dict] | None]
                           ) -> Callable[[], list[dict] | None]:
    """包装 fetch：当前线程已被调用方放弃时抑制结果（返回 None）。

    _base._run_in_thread 超时会给线程对象置 `abandoned` 标记；此处返回 None
    使 KlineTTLCache.load_or_fetch 的 `if data:` 跳过落盘——僵尸线程的迟到
    结果不得写进同日 pickle 缓存（否则「已超时源」会在同日稍后的采集中被
    缓存复活）。非超时路径的线程无该标记，行为不变。
    """
    def _wrapped() -> list[dict] | None:
        data = fetch()
        if getattr(threading.current_thread(), "abandoned", False):
            return None
        return data
    return _wrapped


def load_or_fetch(symbol: str, source: str, sd: str, ed: str,
                  fetch: Callable[[], list[dict] | None],
                  qfq: bool = False) -> list[dict] | None:
    """collect_kline 的包装：命中返回缓存，未命中拉取后落盘。全路径异常安全。

    qfq: 数据是否为前复权语义（写入缓存键，避免新旧语义混用）。
    """
    date_str = shanghai_today()
    return _CACHE.load_or_fetch(
        date_str, _cache_parts(symbol, source, sd, ed, qfq),
        _suppress_if_abandoned(fetch),
        type_guard=list,
        on_hit=lambda: logger.info("kline cache hit: %s %s %s..%s%s", source, symbol,
                                   sd, ed, " (qfq)" if qfq else ""),
    )


__all__ = ["cleanup_old", "load_or_fetch", "CACHE_TTL_SEC"]
