"""HTTP 代理检测与国内数据源绕过。

采集器在 akshare / baostock 调用时通过 proxy_bypass() 注入 no_proxy；
akshare 东方财富接口使用 akshare_direct_session() 强制直连。
环境变量与 requests 补丁使用引用计数；锁仅保护 enter/exit，I/O 期间可并行。
"""

from __future__ import annotations

import os
import sys
import threading
import time
import urllib.request
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Any, Iterator

import requests
import requests.utils as ru

# 东方财富 API 封锁/阻断标识（共享给 collector / render / schema）。
EASTMONEY_BLOCKED_KEYWORDS = (
    "eastmoney.com",
    "东方财富",
    "East Money",
)

# collector 错误文案中的特征片段（render.sanitize_error 用于区分场景）。
EASTMONEY_FAILURE_PROXY_MARKER = "HTTP 代理无法自动绕过"
EASTMONEY_FAILURE_TUN_MARKER = "push2 接口不可达"

_REQUESTS_PATCH_ATTRS = (
    "request", "get", "post", "put", "patch", "delete", "head", "options",
)

PUSH2_EASTMONEY_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
PUSH2_CACHE_TTL_SEC = 300.0

CLASH_DIRECT_RULES = (
    "  - DOMAIN-SUFFIX,eastmoney.com,DIRECT\n"
    "  - DOMAIN-SUFFIX,push2.eastmoney.com,DIRECT\n"
    "  - DOMAIN-SUFFIX,push2his.eastmoney.com,DIRECT\n"
    "  - DOMAIN-SUFFIX,gtimg.cn,DIRECT\n"
    "  - DOMAIN-SUFFIX,baostock.com,DIRECT"
)

_PROXY_IO_LOCK = threading.RLock()

_env_bypass_depth = 0
_env_bypass_saved: dict[str, str | None] = {}

_requests_direct_depth = 0
_requests_direct_sess: requests.Session | None = None
_requests_direct_orig: list[tuple[Any, str, Any]] | None = None

_warned = False
_push2_cache: dict[str, Any] = {
    "reachable": None,
    "checked_at": 0.0,
    "detail": None,
}

_CN_FINANCE_NO_PROXY = (
    ".eastmoney.com,"
    ".push2.eastmoney.com,"
    ".push2his.eastmoney.com,"
    ".gtimg.cn,"
    ".baostock.com,"
    ".dfcfw.com,"
    ".10jqka.com.cn,"
    ".sina.com.cn"
)

_INTERFERING_PROXY_VARS = (
    "ALL_PROXY", "all_proxy",
    "HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy",
)


def clash_rules_yaml() -> str:
    """返回可粘贴到 Clash 配置的 rules 片段。"""
    return f"rules:\n{CLASH_DIRECT_RULES}\n  - MATCH,PROXY"


def detect_proxy() -> dict[str, Any]:
    """检测 env 代理、系统代理与 requests 层代理。"""
    env_keys = [k for k in os.environ if "proxy" in k.lower() and os.environ.get(k)]
    system = urllib.request.getproxies()
    req_proxies = ru.get_environ_proxies(PUSH2_EASTMONEY_URL)
    detected = bool(env_keys) or bool(system) or bool(req_proxies)
    return {
        "detected": detected,
        "env_keys": env_keys,
        "system_proxies": system,
        "requests_proxies": req_proxies,
    }


def requests_use_proxy(url: str = PUSH2_EASTMONEY_URL) -> bool:
    """诊断用：当前 requests 对给定 URL 是否会走代理。"""
    return bool(ru.get_environ_proxies(url, no_proxy=os.environ.get("no_proxy")))


def _force_akshare_em() -> bool:
    return os.environ.get("INVEST_A_FORCE_AKSHARE_EM", "").strip().lower() in (
        "1", "true", "yes",
    )


def _env_bypass_enter() -> None:
    """在已持有 _PROXY_IO_LOCK 时调用。"""
    global _env_bypass_depth, _env_bypass_saved
    if _env_bypass_depth == 0:
        _env_bypass_saved = {}
        for k in _INTERFERING_PROXY_VARS:
            _env_bypass_saved[k] = os.environ.pop(k, None)
        for k in ("no_proxy", "NO_PROXY"):
            old = os.environ.get(k, "")
            _env_bypass_saved[k] = old
            os.environ[k] = (old + "," + _CN_FINANCE_NO_PROXY) if old else _CN_FINANCE_NO_PROXY
    _env_bypass_depth += 1


def _env_bypass_exit() -> None:
    """在已持有 _PROXY_IO_LOCK 时调用。"""
    global _env_bypass_depth, _env_bypass_saved
    _env_bypass_depth -= 1
    if _env_bypass_depth > 0:
        return
    if _env_bypass_depth < 0:
        _env_bypass_depth = 0
    saved = _env_bypass_saved
    _env_bypass_saved = {}
    if not saved:
        return
    for k, v in saved.items():
        if v is None or v == "":
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def _requests_direct_enter() -> None:
    """在已持有 _PROXY_IO_LOCK 时调用：patch requests 为直连。"""
    global _requests_direct_depth, _requests_direct_sess, _requests_direct_orig
    if _requests_direct_depth == 0:
        import requests as req_mod
        import requests.api as api_mod

        sess = req_mod.Session()
        sess.trust_env = False
        sess.proxies = {"http": None, "https": None}

        class _DirectSession(req_mod.Session):
            def __init__(self, *a, **kw):
                super().__init__(*a, **kw)
                self.trust_env = False
                self.proxies = {"http": None, "https": None}

        def _request(method: str, url: str, **kwargs: Any):
            return sess.request(method, url, **kwargs)

        def _verb(method: str):
            def _fn(url: str, **kwargs: Any):
                return sess.request(method, url, **kwargs)

            return _fn

        saved: list[tuple[Any, str, Any]] = []
        for mod in (req_mod, api_mod):
            for attr in _REQUESTS_PATCH_ATTRS:
                saved.append((mod, attr, getattr(mod, attr)))
            mod.request = _request  # type: ignore[assignment]
            mod.get = _verb("GET")  # type: ignore[assignment]
            mod.post = _verb("POST")  # type: ignore[assignment]
            mod.put = _verb("PUT")  # type: ignore[assignment]
            mod.patch = _verb("PATCH")  # type: ignore[assignment]
            mod.delete = _verb("DELETE")  # type: ignore[assignment]
            mod.head = _verb("HEAD")  # type: ignore[assignment]
            mod.options = _verb("OPTIONS")  # type: ignore[assignment]
        saved.append((req_mod, "Session", req_mod.Session))
        req_mod.Session = _DirectSession  # type: ignore[misc]
        _requests_direct_sess = sess
        _requests_direct_orig = saved
    _requests_direct_depth += 1


def _requests_direct_exit() -> None:
    """在已持有 _PROXY_IO_LOCK 时调用。"""
    global _requests_direct_depth, _requests_direct_sess, _requests_direct_orig
    _requests_direct_depth -= 1
    if _requests_direct_depth > 0:
        return
    if _requests_direct_depth < 0:
        _requests_direct_depth = 0
    if _requests_direct_orig is None:
        return
    for mod, attr, orig in _requests_direct_orig:
        setattr(mod, attr, orig)
    if _requests_direct_sess is not None:
        _requests_direct_sess.close()
    _requests_direct_sess = None
    _requests_direct_orig = None


def _probe_push2_eastmoney_unlocked(timeout: float) -> dict[str, Any]:
    """在直连会话已激活时探测 push2（调用方负责 enter/exit）。"""
    result: dict[str, Any] = {
        "reachable": False,
        "http_status": None,
        "error": None,
    }
    now = datetime.now()
    beg = (now - timedelta(days=10)).strftime("%Y%m%d")
    end = now.strftime("%Y%m%d")
    try:
        r = requests.get(
            PUSH2_EASTMONEY_URL,
            params={
                "fields1": "f1,f2,f3,f4,f5,f6",
                "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f116",
                "ut": "7eea3edcaed734bea9cbfc24409ed989",
                "klt": "101",
                "fqt": "0",
                "secid": "0.300750",
                "beg": beg,
                "end": end,
            },
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                "Referer": "https://quote.eastmoney.com/",
            },
            timeout=timeout,
        )
        result["http_status"] = r.status_code
        if r.status_code == 200 and r.json().get("data") is not None:
            result["reachable"] = True
        elif r.status_code == 200:
            result["error"] = "HTTP 200: 响应不包含 data 字段"
        else:
            result["error"] = f"HTTP {r.status_code}: 请求失败"
    except Exception as e:
        result["error"] = f"{type(e).__name__}: {e}"
    return result


@contextmanager
def _direct_scope(*, patch_requests: bool) -> Iterator[None]:
    """临时激活 env 绕过；patch_requests=True 时同时 patch requests（锁仅包裹 enter/exit）。"""
    with _PROXY_IO_LOCK:
        _env_bypass_enter()
        if patch_requests:
            _requests_direct_enter()
    try:
        yield
    finally:
        with _PROXY_IO_LOCK:
            if patch_requests:
                _requests_direct_exit()
            _env_bypass_exit()


def _update_push2_cache(detail: dict[str, Any]) -> None:
    global _push2_cache
    _push2_cache = {
        "reachable": bool(detail.get("reachable")),
        "checked_at": time.monotonic(),
        "detail": detail,
    }


def _push2_cache_fresh(*, force_probe: bool) -> dict[str, Any] | None:
    """缓存未过期时返回 push2 探测详情副本。"""
    if force_probe:
        return None
    now = time.monotonic()
    with _PROXY_IO_LOCK:
        detail = _push2_cache.get("detail")
        age = now - float(_push2_cache.get("checked_at") or 0.0)
        if isinstance(detail, dict) and age < PUSH2_CACHE_TTL_SEC:
            return dict(detail)
    return None


def _probe_push2_cached(*, force_probe: bool = False, timeout: float = 6) -> dict[str, Any]:
    """探测 push2 并写入进程内缓存（TTL 内复用，避免重复网络请求）。"""
    if _force_akshare_em():
        detail = {"reachable": True, "http_status": 200, "error": None}
        with _PROXY_IO_LOCK:
            _update_push2_cache(detail)
        return detail

    cached = _push2_cache_fresh(force_probe=force_probe)
    if cached is not None:
        return cached

    with _direct_scope(patch_requests=True):
        detail = _probe_push2_eastmoney_unlocked(timeout)
    with _PROXY_IO_LOCK:
        _update_push2_cache(detail)
    return detail


def probe_push2_eastmoney(timeout: float = 6) -> dict[str, Any]:
    """探测东方财富 push2 可达性（与 akshare 行情接口同源，走直连会话）。"""
    return _probe_push2_cached(timeout=timeout)


def akshare_push2_available(*, force_probe: bool = False) -> bool:
    """东方财富 push2 是否可达（进程内缓存 + TTL，单次探测）。"""
    if _force_akshare_em():
        return True
    if not force_probe and not detect_proxy()["detected"]:
        return True
    detail = _probe_push2_cached(force_probe=force_probe, timeout=5)
    return bool(detail.get("reachable"))


def get_push2_cache_detail() -> dict[str, Any] | None:
    """返回最近一次 push2 探测详情（供 diagnose）。"""
    with _PROXY_IO_LOCK:
        detail = _push2_cache.get("detail")
        if isinstance(detail, dict):
            return dict(detail)
        return None


def proxy_status(*, probe: bool = False) -> dict[str, Any]:
    """汇总代理环境：是否检测到代理、绕过是否生效、是否需要用户操作。"""
    info = detect_proxy()
    push2: dict[str, Any] | None = None
    should_probe = probe and info["detected"]

    with _direct_scope(patch_requests=True):
        bypass_effective = not requests_use_proxy(PUSH2_EASTMONEY_URL)

    if should_probe:
        push2 = _probe_push2_cached(timeout=6)

    if not info["detected"]:
        return {
            **info,
            "bypass_effective": bypass_effective,
            "push2": push2,
            "user_action_needed": False,
            "hint_kind": None,
        }

    if bypass_effective:
        if should_probe and push2 and not push2.get("reachable"):
            return {
                **info,
                "bypass_effective": True,
                "push2": push2,
                "user_action_needed": True,
                "hint_kind": "tun_or_cdn",
            }
        return {
            **info,
            "bypass_effective": True,
            "push2": push2,
            "user_action_needed": False,
            "hint_kind": None,
        }

    return {
        **info,
        "bypass_effective": False,
        "push2": push2,
        "user_action_needed": True,
        "hint_kind": "clash_rules",
    }


def proxy_hint_lines(kind: str | None = None) -> list[str]:
    """按场景返回提示文案。"""
    if kind == "tun_or_cdn":
        return [
            "ℹ️  东方财富 push2 接口不可达（已自动跳过 akshare 行情/基本信息，使用 Tushare/Baostock 替代）。",
            "若需恢复：暂时关闭 Clash TUN / 全局代理后重试；或设置 INVEST_A_FORCE_AKSHARE_EM=1 强制探测。",
        ]
    if kind == "clash_rules":
        return [
            "⚠️  检测到 HTTP 代理且无法自动绕过国内金融域名。请在 Clash 规则中添加：",
            "",
            clash_rules_yaml(),
            "",
            "TUN 模式需在网卡层配置；或采集时暂时关闭全局代理。",
        ]
    return []


def warn_if_proxy_detected(*, probe: bool = False) -> None:
    """采集/报告前按需提示（每进程最多一次）。

    probe=True 且检测到代理时，才探测 push2 可达性。
    """
    global _warned
    if _warned:
        return
    status = proxy_status(probe=probe)
    if not status["user_action_needed"]:
        return
    lines = proxy_hint_lines(status.get("hint_kind"))
    if not lines:
        return
    _warned = True
    print("\n".join(lines), file=sys.stderr)
    print(file=sys.stderr)


@contextmanager
def no_proxy_session() -> Iterator[requests.Session]:
    """返回 trust_env=False 的 Session，不修改进程级环境（腾讯行情等强制直连）。"""
    sess = requests.Session()
    sess.trust_env = False
    sess.proxies = {"http": None, "https": None}
    try:
        yield sess
    finally:
        sess.close()


@contextmanager
def _akshare_direct_session_unlocked() -> Iterator[None]:
    """兼容测试 mock：在已持有 _PROXY_IO_LOCK 时同步 enter/exit（勿在 I/O 期间使用）。"""
    _env_bypass_enter()
    _requests_direct_enter()
    try:
        yield
    finally:
        _requests_direct_exit()
        _env_bypass_exit()


@contextmanager
def akshare_direct_session() -> Iterator[None]:
    """akshare 东方财富调用：清除代理并 patch requests 为 trust_env=False（可并行 I/O）。"""
    with _direct_scope(patch_requests=True):
        yield


@contextmanager
def proxy_bypass() -> Iterator[None]:
    """采集国内金融数据时临时绕过 HTTP 代理（baostock / 同花顺 akshare 等，可并行 I/O）。"""
    with _direct_scope(patch_requests=False):
        yield
