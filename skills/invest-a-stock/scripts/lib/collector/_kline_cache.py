"""同日 K 线缓存（pickle，源隔离）。

路径: {STORE_DIR}/collect_kline_cache/{yyyymmdd}/{source}/{symbol}__{sd}_{ed}{__qfq}.pkl
- 键含 source：tushare.daily/akshare/baostock 与 tickflow.kline 互不污染
- 键含 qfq 标记：前复权语义变更时新键生效，不复权旧缓存自动失效
- 键含 sd/ed 查询窗口：默认 400 日 与 --deep 730 日 互不误用（只按 symbol 缓存
  会导致 deep 模式复用 400 日截断数据）
- TTL 1 天（mtime）：同日重复采集命中；次日必然 miss（与 gap-scan 语义一致）
- INVEST_KLINE_CACHE=0 禁用（逃生口）

镜像 skills/invest-a-gap-scan/scripts/lib/kline_cache.py 模式，但存 list[dict]
（与 collector/_sources.py 的 _q_* 返回类型一致）而非 DataFrame。
"""

from __future__ import annotations

import logging
import os
import pickle
import shutil
import time
from pathlib import Path
from typing import Any, Callable

from .. import env
from ..shared_dates import shanghai_today

logger = logging.getLogger(__name__)

CACHE_TTL_SEC = 86400  # 1 天（mtime 基准）


def enabled() -> bool:
    """缓存总开关。INVEST_KLINE_CACHE=0 禁用。"""
    return os.environ.get("INVEST_KLINE_CACHE", "1") != "0"


def _cache_root() -> Path:
    return env.STORE_DIR / "collect_kline_cache"


def _cache_path(symbol: str, source: str, sd: str, ed: str,
                date_str: str, qfq: bool = False) -> Path:
    marker = "__qfq" if qfq else ""
    return (_cache_root() / date_str / source
            / f"{symbol}__{sd}_{ed}{marker}.pkl")


def load(symbol: str, source: str, sd: str, ed: str,
         date_str: str | None = None, qfq: bool = False) -> list[dict] | None:
    """读取缓存；未启用/不存在/过期/损坏均返回 None（视为未命中）。"""
    if not enabled():
        return None
    date_str = date_str or shanghai_today()
    path = _cache_path(symbol, source, sd, ed, date_str, qfq=qfq)
    try:
        if not path.exists():
            return None
        if time.time() - path.stat().st_mtime > CACHE_TTL_SEC:
            return None
        data = pickle.load(open(path, "rb"))
        return data if isinstance(data, list) else None
    except Exception:
        return None  # 损坏/截断 pickle → 视为未命中


def save(symbol: str, source: str, sd: str, ed: str,
         rows: list[dict], date_str: str | None = None,
         qfq: bool = False) -> None:
    """写入缓存；失败不影响采集。"""
    if not enabled() or not rows:
        return
    date_str = date_str or shanghai_today()
    path = _cache_path(symbol, source, sd, ed, date_str, qfq=qfq)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as f:
            pickle.dump(rows, f)
    except Exception as exc:
        logger.warning("kline cache save failed: %s: %s", path, exc)


def cleanup_old() -> None:
    """清理超 TTL 的日期目录（按目录 mtime）。"""
    root = _cache_root()
    if not root.exists():
        return
    now = time.time()
    for entry in root.iterdir():
        if entry.is_dir() and now - entry.stat().st_mtime > CACHE_TTL_SEC:
            shutil.rmtree(entry, ignore_errors=True)


def load_or_fetch(symbol: str, source: str, sd: str, ed: str,
                  fetch: Callable[[], list[dict] | None],
                  qfq: bool = False) -> list[dict] | None:
    """collect_kline 的包装：命中返回缓存，未命中拉取后落盘。全路径异常安全。

    qfq: 数据是否为前复权语义（写入缓存键，避免新旧语义混用）。
    """
    if not enabled():
        return fetch()
    hit = load(symbol, source, sd, ed, qfq=qfq)
    if hit is not None:
        logger.info("kline cache hit: %s %s %s..%s%s", source, symbol, sd, ed,
                    " (qfq)" if qfq else "")
        return hit
    data = fetch()
    if data:
        save(symbol, source, sd, ed, data, qfq=qfq)
    return data


__all__ = ["enabled", "load", "save", "cleanup_old", "load_or_fetch",
           "CACHE_TTL_SEC"]
