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


def enabled() -> bool:
    """缓存总开关。INVEST_KLINE_CACHE=0 禁用。"""
    return os.environ.get("INVEST_KLINE_CACHE", "1") != "0"


def _cache_parts(symbol: str, source: str, sd: str, ed: str, qfq: bool) -> tuple[str, str]:
    marker = "__qfq" if qfq else ""
    return (source, f"{symbol}__{sd}_{ed}{marker}")


def load(symbol: str, source: str, sd: str, ed: str,
         date_str: str | None = None, qfq: bool = False) -> list[dict] | None:
    """读取缓存；未启用/不存在/过期/损坏均返回 None（视为未命中）。

    门控由 canonical _CACHE 统一判定（INVEST_KLINE_CACHE 读取单点）。
    """
    date_str = date_str or shanghai_today()
    return _CACHE.load(date_str, _cache_parts(symbol, source, sd, ed, qfq),
                       type_guard=list)


def save(symbol: str, source: str, sd: str, ed: str,
         rows: list[dict], date_str: str | None = None,
         qfq: bool = False) -> None:
    """写入缓存；失败不影响采集。"""
    date_str = date_str or shanghai_today()
    _CACHE.save(date_str, _cache_parts(symbol, source, sd, ed, qfq), rows,
                skip_empty=True, log_errors=True)


def cleanup_old() -> None:
    """清理超 TTL 的日期目录（按目录 mtime）。"""
    _CACHE.cleanup_old(ignore_errors=True)


def load_or_fetch(symbol: str, source: str, sd: str, ed: str,
                  fetch: Callable[[], list[dict] | None],
                  qfq: bool = False) -> list[dict] | None:
    """collect_kline 的包装：命中返回缓存，未命中拉取后落盘。全路径异常安全。

    qfq: 数据是否为前复权语义（写入缓存键，避免新旧语义混用）。
    """
    date_str = shanghai_today()
    return _CACHE.load_or_fetch(
        date_str, _cache_parts(symbol, source, sd, ed, qfq), fetch,
        type_guard=list,
        on_hit=lambda: logger.info("kline cache hit: %s %s %s..%s%s", source, symbol,
                                   sd, ed, " (qfq)" if qfq else ""),
    )


__all__ = ["enabled", "load", "save", "cleanup_old", "load_or_fetch",
           "CACHE_TTL_SEC"]
