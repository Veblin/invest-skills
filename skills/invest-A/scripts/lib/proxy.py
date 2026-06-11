"""HTTP 代理检测与国内数据源 Clash 规则提示。

不强制绕过用户代理；检测到本机代理时提示将国内金融域名加入 Clash DIRECT 规则。
"""

from __future__ import annotations

import os
import urllib.request
from contextlib import contextmanager
from typing import Iterator

import requests
import requests.utils as ru

# 东方财富 API 封锁/阻断标识（共享给 collector / render / schema）。
EASTMONEY_BLOCKED_KEYWORDS = (
    "eastmoney.com",
    "东方财富",
    "East Money",
)

CLASH_DIRECT_RULES = (
    "  - DOMAIN-SUFFIX,eastmoney.com,DIRECT\n"
    "  - DOMAIN-SUFFIX,gtimg.cn,DIRECT\n"
    "  - DOMAIN-SUFFIX,baostock.com,DIRECT"
)

_warned = False


def clash_rules_yaml() -> str:
    """返回可粘贴到 Clash 配置的 rules 片段。"""
    return f"rules:\n{CLASH_DIRECT_RULES}\n  - MATCH,PROXY"


def detect_proxy() -> dict:
    """检测 env 代理、系统代理与 requests 层代理。"""
    env_keys = [k for k in os.environ if "proxy" in k.lower() and os.environ.get(k)]
    system = urllib.request.getproxies()
    req_proxies = ru.get_environ_proxies("https://push2his.eastmoney.com")
    detected = bool(env_keys) or bool(system) or bool(req_proxies)
    return {
        "detected": detected,
        "env_keys": env_keys,
        "system_proxies": system,
        "requests_proxies": req_proxies,
    }


def proxy_hint_lines() -> list[str]:
    return [
        "⚠️  检测到本机 HTTP 代理（Clash/V2Ray 等）。国内数据源应直连，请在 Clash 规则中添加：",
        "",
        clash_rules_yaml(),
        "",
        "TUN 模式需在网卡层配置；或采集时暂时关闭全局代理。",
    ]


def warn_if_proxy_detected() -> None:
    """采集/报告前提示一次（每进程）。"""
    global _warned
    if _warned:
        return
    if detect_proxy()["detected"]:
        _warned = True
        print("\n".join(proxy_hint_lines()))
        print()


@contextmanager
def no_proxy_session() -> Iterator[requests.Session]:
    """返回 trust_env=False 的 Session，不修改进程级环境（仅用于可选探针）。"""
    sess = requests.Session()
    sess.trust_env = False
    sess.proxies = {"http": None, "https": None}
    try:
        yield sess
    finally:
        sess.close()


@contextmanager
def proxy_bypass() -> Iterator[None]:
    """向后兼容空操作（v0.1.2 起不再强制绕过代理）。

    注意：此 context manager 当前为空操作，不会修改代理环境变量。
    akshare/baostock 等库会读取系统代理设置。如果采集国内数据源
    （eastmoney、baostock 等）时遇到连接失败，请检查 Clash/VPN 规则，
    确保将以下域名加入 DIRECT：

      - DOMAIN-SUFFIX,eastmoney.com,DIRECT
      - DOMAIN-SUFFIX,gtimg.cn,DIRECT
      - DOMAIN-SUFFIX,baostock.com,DIRECT

    或在采集前暂时关闭全局代理。
    运行 ``invest.py diagnose`` 可查看当前代理状态与各数据源连通性。
    """
    yield


def requests_use_proxy(url: str = "https://eastmoney.com") -> bool:
    """诊断用：当前 requests 对给定 URL 是否会走代理。"""
    return bool(ru.get_environ_proxies(url, no_proxy=os.environ.get("no_proxy")))
