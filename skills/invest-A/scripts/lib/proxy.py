"""HTTP 代理绕过工具。集中管理，避免 collector / env 各自 mutate os.environ。"""

from __future__ import annotations

import os
import threading
from contextlib import contextmanager
from typing import Iterator

import requests

# 东方财富 API 封锁/阻断标识（共享给 collector / render / schema）。
# 注意：不包含 ProxyError — 本地代理配置错误也可能产生 ProxyError，
# 不应自动归因于东方财富封锁。
EASTMONEY_BLOCKED_KEYWORDS = (
    "eastmoney.com",
    "Connection aborted",
    "Remote end closed",
    "东方财富",
    "East Money",
)

_PROXY_KEYS = (
    "HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy",
    "ALL_PROXY", "all_proxy", "NO_PROXY", "no_proxy",
)
_proxy_lock = threading.RLock()
_bypass_depth = 0
_bypass_saved: dict[str, str | None] | None = None


@contextmanager
def no_proxy_session() -> Iterator[requests.Session]:
    """返回 trust_env=False 的 Session，不修改进程级 os.environ。"""
    sess = requests.Session()
    sess.trust_env = False
    sess.proxies = {"http": None, "https": None}
    try:
        yield sess
    finally:
        sess.close()


@contextmanager
def proxy_bypass() -> Iterator[None]:
    """清除代理环境变量（供 akshare 等读取 os.environ 的库使用）。

    使用 RLock + 引用计数确保多线程安全：
    - 第一个线程进入时保存原始 env 并清除代理变量
    - 后续线程只增加计数，不重复 save/restore
    - 最后一个线程退出时恢复原始 env
    - 锁仅在 save/restore 瞬间持有，不阻塞 yield 期间的并行 HTTP 请求
    """
    global _bypass_depth, _bypass_saved
    with _proxy_lock:
        if _bypass_depth == 0:
            _bypass_saved = {k: os.environ.get(k) for k in _PROXY_KEYS}
            for k in _PROXY_KEYS:
                os.environ.pop(k, None)
            os.environ["NO_PROXY"] = "*"
            os.environ["no_proxy"] = "*"
        _bypass_depth += 1
    # 锁在 yield 前释放，允许并行采集线程同时执行 HTTP 请求
    try:
        yield
    finally:
        with _proxy_lock:
            _bypass_depth -= 1
            if _bypass_depth == 0 and _bypass_saved is not None:
                for k, v in _bypass_saved.items():
                    if v is None:
                        os.environ.pop(k, None)
                    else:
                        os.environ[k] = v
                _bypass_saved = None
