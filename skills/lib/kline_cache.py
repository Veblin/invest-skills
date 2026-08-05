"""K 线 TTL 缓存（pickle，源隔离）— 供各 skill 共享。

历史：gap-scan kline_cache.py（137 行）与 stock collector/_kline_cache.py
（115 行）为镜像实现，仅 TTL/键布局/空值处理/错误约定不同。统一收敛至此：
- TTL 参数化：gap 3 天 / stock 1 天，构造时传入，两者不互改
- 键布局由调用方以 parts 描述（{root}/{date}/{dirs...}/{stem}.pkl）
- 错误约定参数化：log_errors 时记录 warning（stock 语义），否则上抛（gap 语义）
"""

from __future__ import annotations

import logging
import pickle
import shutil
import time
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)


class KlineTTLCache:
    """Pickle TTL 缓存：{root}/{date_str}/{dirs...}/{stem}.pkl，mtime 基准 TTL。

    - root 每次调用解析（可传 callable）→ 测试 monkeypatch env.STORE_DIR 兼容
    - 损坏/截断 pickle 视为未命中（返回 None）
    - 无锁：mtime TTL + 损坏→miss 是既定姿态（D8 决策，非回归）
    """

    def __init__(self, root: Path | Callable[[], Path], ttl_seconds: int, *,
                 enabled: Callable[[], bool] | None = None) -> None:
        self._root = root
        self.ttl_seconds = ttl_seconds
        self._enabled = enabled

    def _root_dir(self) -> Path:
        return self._root() if callable(self._root) else Path(self._root)

    def _path(self, date_str: str, parts: tuple[str, ...]) -> Path:
        if not parts:
            raise ValueError("parts 必须至少含文件 stem（最后一项）")
        *dirs, stem = parts
        return self._root_dir().joinpath(date_str, *dirs, f"{stem}.pkl")

    def _is_enabled(self) -> bool:
        return self._enabled is None or self._enabled()

    def save(self, date_str: str, parts: tuple[str, ...], payload: Any, *,
             skip_empty: bool = False, log_errors: bool = False) -> None:
        """写入 pickle。

        skip_empty: None/空容器跳过（stock 空 list 语义）。
        log_errors: True 时失败记 warning 不上抛（stock 语义：失败不影响采集）；
                    False 时上抛（gap 语义）。
        """
        if not self._is_enabled():
            return
        if skip_empty and (payload is None
                           or (hasattr(payload, "__len__") and len(payload) == 0)):
            return
        path = self._path(date_str, parts)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "wb") as f:
                pickle.dump(payload, f)
        except Exception as exc:
            if log_errors:
                logger.warning("kline cache save failed: %s: %s", path, exc)
            else:
                raise

    def load(self, date_str: str, parts: tuple[str, ...], *,
             type_guard: type | None = None) -> Any | None:
        """读取缓存；未启用/不存在/过期/损坏/类型不符均返回 None（视为未命中）。"""
        if not self._is_enabled():
            return None
        path = self._path(date_str, parts)
        try:
            st = path.stat()  # 单次 stat（FileNotFoundError 由 except 兜底）
            if time.time() - st.st_mtime > self.ttl_seconds:
                return None
            with open(path, "rb") as f:
                data = pickle.load(f)
            if type_guard is not None and not isinstance(data, type_guard):
                return None
            return data
        except Exception:
            return None  # 不存在/损坏/截断 pickle → 视为未命中

    def cleanup_old(self, *, ignore_errors: bool = False) -> None:
        """清理超 TTL 的日期目录（按目录 mtime）。"""
        root = self._root_dir()
        if not root.exists():
            return
        now = time.time()
        for entry in root.iterdir():
            if entry.is_dir() and now - entry.stat().st_mtime > self.ttl_seconds:
                shutil.rmtree(entry, ignore_errors=ignore_errors)

    def load_or_fetch(self, date_str: str, parts: tuple[str, ...],
                      fetch: Callable[[], Any | None], *,
                      type_guard: type | None = None,
                      on_hit: Callable[[], None] | None = None) -> Any | None:
        """命中返回缓存（触发 on_hit）；未命中拉取后落盘。全路径异常安全。"""
        if not self._is_enabled():
            return fetch()
        hit = self.load(date_str, parts, type_guard=type_guard)
        if hit is not None:
            if on_hit is not None:
                on_hit()
            return hit
        data = fetch()
        if data:
            # 落盘失败只记 warning 不上抛（stock 不变量"写入缓存；失败不影响采集"）
            self.save(date_str, parts, data, log_errors=True)
        return data


__all__ = ["KlineTTLCache"]
