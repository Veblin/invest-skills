"""
环境诊断模块 env_check.py

预检数据采集环境：
- 代理检测：系统代理会否阻断关键 API
- API 连通性：EastMoney / Sina / Baostock 各端点是否可达
- Python 依赖：akshare / efinance / yfinance / baostock 是否安装且函数可用
- 环境变量：TUSHARE_TOKEN / FRED_API_KEY / TAVILY_API_KEY 是否配置

用法：
    python -m scripts.lib.env_check
    python -m scripts.lib.env_check --json  # 输出 JSON 供 Skill 调用
"""

from __future__ import annotations

import importlib
import json
import logging
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────
# 数据模型
# ─────────────────────────────────────────────────────────────

@dataclass
class ProxyStatus:
    system_proxy: str | None
    env_vars: list[str]
    net_broken: bool
    curl_works: bool
    recommendation: str


@dataclass
class ApiEndpointStatus:
    url: str
    label: str
    reachable: bool
    method: str  # "requests" | "curl" | "none"
    latency_ms: float | None
    note: str


@dataclass
class DepStatus:
    package: str
    installed: bool
    version: str | None
    key_functions: list[str]  # 关键函数是否可用
    missing_functions: list[str]


@dataclass
class EnvCheckResult:
    ok: bool
    proxy: ProxyStatus
    api_endpoints: list[ApiEndpointStatus]
    dependencies: list[DepStatus]
    env_variables: dict[str, bool]
    summary: str
    recommendations: list[str]


# ─────────────────────────────────────────────────────────────
# 代理检测
# ─────────────────────────────────────────────────────────────

PROXY_ENV_KEYS = [
    "HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy",
    "ALL_PROXY", "all_proxy",
]


def _detect_proxy() -> ProxyStatus:
    """检测当前环境的代理配置。"""
    env_vars = []
    proxy_addr = None

    for key in PROXY_ENV_KEYS:
        val = os.environ.get(key)
        if val:
            env_vars.append(f"{key}={val}")
            if proxy_addr is None:
                proxy_addr = val

    # 测试 Python requests（带代理）能否连通 EastMoney
    net_broken = False
    try:
        import requests
        r = requests.get("https://push2.eastmoney.com", timeout=5,
                         headers={"User-Agent": "Mozilla/5.0"})
        net_broken = (r.status_code != 200)
    except Exception:
        net_broken = True  # May be broken WITH proxy

    # Test with no proxy
    curl_works = False
    try:
        result = subprocess.run(
            ["env", "-i", "HOME", os.environ.get("HOME", ""),
             "PATH", os.environ.get("PATH", ""),
             "curl", "-s", "--connect-timeout", "5", "--max-time", "10",
             "-o", "/dev/null", "-w", "%{http_code}",
             "-H", "User-Agent: Mozilla/5.0",
             "https://push2.eastmoney.com/api/qt/stock/get?secid=0.300328&fields=f43"],
            capture_output=True, text=True, timeout=15,
        )
        curl_works = (result.stdout.strip() == "200")
    except Exception:
        curl_works = False

    # 建议
    if env_vars and net_broken and curl_works:
        recommendation = (
            f"⚠️ 系统代理（{proxy_addr}）阻拦了 EastMoney API。"
            "建议在 Python 脚本中使用 trust_env=False 或 env -i 清理代理变量。"
            "已实现 curl fallback 机制备选。"
        )
    elif not env_vars:
        recommendation = "✅ 未检测到系统代理，直连正常。"
    elif env_vars and not net_broken:
        recommendation = "⚠️ 代理已配置但直连仍可用，建议清除代理变量以获得更佳性能。"
    else:
        recommendation = "❌ 网络连通性异常，请检查网络设置。"

    return ProxyStatus(
        system_proxy=proxy_addr,
        env_vars=env_vars,
        net_broken=net_broken,
        curl_works=curl_works,
        recommendation=recommendation,
    )


# ─────────────────────────────────────────────────────────────
# API 连通性检测
# ─────────────────────────────────────────────────────────────

ENDPOINTS_TO_CHECK = [
    {
        "url": "https://push2.eastmoney.com/api/qt/stock/get?secid=0.300328&fields=f43,f57,f58",
        "label": "EastMoney 实时行情",
    },
    {
        "url": "https://push2his.eastmoney.com/api/qt/stock/kline/get?secid=0.300328&fields1=f1&fields2=f51&klt=101&fqt=1&beg=20250101&end=20250110",
        "label": "EastMoney 历史K线",
    },
    {
        "url": "https://quotes.sina.cn/cn/api/openapi.php/CompanyFinanceService.getFinanceReport2022?paperCode=sh300328&source=gjzb&type=0&page=1&num=1",
        "label": "新浪财经 财务报表",
    },
    {
        "url": "https://datacenter.eastmoney.com/securities/api/data/v1/get?reportName=RPT_F10_EH_Holdernum&filter=(SECURITY_CODE=%22300328%22)&pageSize=1",
        "label": "EastMoney 数据中台",
    },
]


def _check_api_endpoints() -> list[ApiEndpointStatus]:
    """检测各 API 端点的连通性。"""
    results = []
    for ep in ENDPOINTS_TO_CHECK:
        reachable = False
        method = "none"
        latency = None

        # 直接用 curl（绕过代理）
        t0 = time.time()
        try:
            result = subprocess.run(
                ["env", "-i", "HOME", os.environ.get("HOME", ""),
                 "PATH", os.environ.get("PATH", ""),
                 "curl", "-s", "--connect-timeout", "5", "--max-time", "10",
                 "-o", "/dev/null", "-w", "%{http_code}",
                 "-H", "User-Agent: Mozilla/5.0",
                 ep["url"]],
                capture_output=True, text=True, timeout=15,
            )
            latency = round((time.time() - t0) * 1000, 0)
            if result.stdout.strip() == "200":
                reachable = True
                method = "curl"
        except Exception:
            latency = None

        note = ""
        if not reachable:
            note = "端点不可达"
        elif latency and latency > 3000:
            note = f"延迟较高 ({latency}ms)"

        results.append(ApiEndpointStatus(
            url=ep["url"],
            label=ep["label"],
            reachable=reachable,
            method=method,
            latency_ms=latency,
            note=note,
        ))

    return results


# ─────────────────────────────────────────────────────────────
# 依赖检测
# ─────────────────────────────────────────────────────────────

DEP_CHECKS = [
    {
        "package": "akshare",
        "key_functions": [
            "stock_financial_abstract",
            "stock_individual_info_em",
            "stock_profile_cninfo",
            "stock_zh_a_hist",
            "stock_profit_sheet_by_report_em",
            "stock_balance_sheet_by_report_em",
            "stock_shareholder",
            "stock_institute_research",
        ],
    },
    {
        "package": "efinance",
        "key_functions": [
            "stock.get_base_info",
            "stock.get_quote_history",
            "stock.get_latest_holder_number",
            "stock.get_realtime_quotes",
        ],
    },
    {
        "package": "yfinance",
        "key_functions": [
            "Ticker",
            "download",
        ],
    },
    {
        "package": "pandas",
        "key_functions": [
            "DataFrame",
            "read_csv",
        ],
    },
    {
        "package": "requests",
        "key_functions": [
            "Session",
            "get",
        ],
    },
]


def _check_dependencies() -> list[DepStatus]:
    """检测 Python 依赖的安装状态和关键函数可用性。"""
    results = []
    for dep in DEP_CHECKS:
        installed = False
        version = None
        missing_functions = []

        try:
            mod = importlib.import_module(dep["package"])
            installed = True
            version = getattr(mod, "__version__", None)

            # 检测关键函数
            for func_path in dep["key_functions"]:
                parts = func_path.split(".")
                obj = mod
                for part in parts:
                    obj = getattr(obj, part, None)
                    if obj is None:
                        break
                if obj is None:
                    missing_functions.append(func_path)
        except ImportError:
            installed = False

        results.append(DepStatus(
            package=dep["package"],
            installed=installed,
            version=version,
            key_functions=dep["key_functions"],
            missing_functions=missing_functions,
        ))

    return results


# ─────────────────────────────────────────────────────────────
# 环境变量检测
# ─────────────────────────────────────────────────────────────

_ENV_VAR_CHECKS = [
    ("TUSHARE_TOKEN", "可选，启用后走官方数据源"),
    ("FRED_API_KEY", "可选，启用后获取美联储宏观数据"),
    ("TAVILY_API_KEY", "可选，启用后走 Tavily 新闻搜索"),
]


def _check_env_variables() -> dict[str, bool]:
    """检测必要环境变量是否配置。"""
    return {
        name: bool(os.environ.get(name))
        for name, _desc in _ENV_VAR_CHECKS
    }


# ─────────────────────────────────────────────────────────────
# 主编排
# ─────────────────────────────────────────────────────────────

def run_env_check() -> EnvCheckResult:
    """执行完整环境诊断。

    Returns:
        EnvCheckResult — 结构化环境状态
    """
    proxy = _detect_proxy()
    endpoints = _check_api_endpoints()
    deps = _check_dependencies()
    env_vars = _check_env_variables()

    # 计算整体状态
    endpoints_ok = sum(1 for ep in endpoints if ep.reachable)
    deps_ok = sum(1 for d in deps if d.installed)
    missing_funcs = [(d.package, f) for d in deps for f in d.missing_functions]
    total_endpoints = len(ENDPOINTS_TO_CHECK)

    recommendations = [proxy.recommendation]

    if endpoints_ok < total_endpoints:
        fail_labels = [ep.label for ep in endpoints if not ep.reachable]
        recommendations.append(
            f"⚠️ {len(fail_labels)}/{total_endpoints} API 端点不可达: {', '.join(fail_labels)}"
        )

    if missing_funcs:
        for pkg, func in missing_funcs:
            recommendations.append(f"⚠️ {pkg} 缺少函数 {func}，相关功能将降级")

    if deps_ok < len(DEP_CHECKS):
        missing_pkgs = [d.package for d in deps if not d.installed]
        recommendations.append(
            f"❌ 缺失依赖: {', '.join(missing_pkgs)}。运行 pip install -r requirements.txt"
        )

    ok = (
        endpoints_ok >= 2  # 至少 2 个端点可达
        and deps_ok >= 3    # 至少核心依赖（akshare、pandas、requests）可用
    )

    summary = (
        f"{'✅' if ok else '❌'} 环境诊断: "
        f"API {endpoints_ok}/{total_endpoints} 可达, "
        f"依赖 {deps_ok}/{len(DEP_CHECKS)} 可用, "
        f"Token {sum(1 for v in env_vars.values() if v)}/{len(env_vars)} 已配置"
    )

    return EnvCheckResult(
        ok=ok,
        proxy=proxy,
        api_endpoints=endpoints,
        dependencies=deps,
        env_variables=env_vars,
        summary=summary,
        recommendations=recommendations,
    )


# ─────────────────────────────────────────────────────────────
# CLI & JSON 输出
# ─────────────────────────────────────────────────────────────

def _format_result(result: EnvCheckResult) -> str:
    """格式化人类可读输出。"""
    lines = [f"=== Environment Check ===\n{result.summary}\n"]

    # Proxy
    lines.append("--- Proxy ---")
    lines.append(f"  System Proxy: {result.proxy.system_proxy or 'None'}")
    if result.proxy.env_vars:
        for v in result.proxy.env_vars:
            lines.append(f"    {v}")
    lines.append(f"  Requests blocked: {result.proxy.net_broken}")
    lines.append(f"  Curl fallback: {result.proxy.curl_works}")
    lines.append(f"  {result.proxy.recommendation}\n")

    # API
    lines.append("--- API Endpoints ---")
    for ep in result.api_endpoints:
        icon = "✅" if ep.reachable else "❌"
        lines.append(
            f"  {icon} {ep.label} ({ep.method}, {ep.latency_ms or '--'}ms)"
            + (f" — {ep.note}" if ep.note else "")
        )
    lines.append("")

    # Dependencies
    lines.append("--- Dependencies ---")
    for dep in result.dependencies:
        icon = "✅" if dep.installed else "❌"
        ver = f" v{dep.version}" if dep.version else ""
        lines.append(f"  {icon} {dep.package}{ver}")
        if dep.missing_functions:
            for f in dep.missing_functions:
                lines.append(f"      ⚠️ Missing: {f}")
    lines.append("")

    # Env vars
    lines.append("--- Environment Variables ---")
    for name, desc in _ENV_VAR_CHECKS:
        icon = "✅" if result.env_variables[name] else "⚪"
        lines.append(f"  {icon} {name} ({desc})")
    lines.append("")

    # Recommendations
    if result.recommendations:
        lines.append("--- Recommendations ---")
        for r in result.recommendations:
            lines.append(f"  {r}")

    return "\n".join(lines)


def _to_json(result: EnvCheckResult) -> dict[str, Any]:
    """转为 JSON 可序列化结构。"""
    return {
        "ok": result.ok,
        "summary": result.summary,
        "proxy": {
            "system_proxy": result.proxy.system_proxy,
            "env_vars": result.proxy.env_vars,
            "net_broken": result.proxy.net_broken,
            "curl_works": result.proxy.curl_works,
            "recommendation": result.proxy.recommendation,
        },
        "api_endpoints": [
            {
                "label": ep.label,
                "url": ep.url,
                "reachable": ep.reachable,
                "method": ep.method,
                "latency_ms": ep.latency_ms,
                "note": ep.note,
            }
            for ep in result.api_endpoints
        ],
        "dependencies": [
            {
                "package": d.package,
                "installed": d.installed,
                "version": d.version,
                "missing_functions": d.missing_functions,
            }
            for d in result.dependencies
        ],
        "env_variables": result.env_variables,
        "recommendations": result.recommendations,
    }


# ─────────────────────────────────────────────────────────────
# 测试入口
# ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    result = run_env_check()

    if "--json" in sys.argv:
        print(json.dumps(_to_json(result), ensure_ascii=False, indent=2, default=str))
    else:
        print(_format_result(result))

    sys.exit(0 if result.ok else 1)
