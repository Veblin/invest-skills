"""环境与 API Key 管理。集中配置加载，所有模块通过 get_config() 获取。

优先级: os.environ > 项目 .env > 全局 ~/.config/investment/.env
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _find_project_root() -> Path:
    cwd = Path.cwd()
    for parent in [cwd, *cwd.parents]:
        if (parent / ".env").exists() or (parent / "pyproject.toml").exists():
            return parent
        if parent == Path.home() or parent == parent.parent:
            break
    return cwd


PROJECT_ROOT = _find_project_root()
GLOBAL_CONFIG_DIR = Path.home() / ".config" / "investment"
GLOBAL_CONFIG_FILE = GLOBAL_CONFIG_DIR / ".env"
PROJECT_ENV_FILE = PROJECT_ROOT / ".env"
STORE_DIR = Path.home() / ".local" / "share" / "investment"
STORE_DB = STORE_DIR / "research.db"


def load_env_file(path: Path) -> dict[str, str]:
    if not path or not path.exists():
        return {}
    env: dict[str, str] = {}
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            m = re.match(r'^([A-Za-z_][A-Za-z0-9_]*)=(.*)$', line)
            if m:
                key = m.group(1)
                value = m.group(2).strip().strip('"').strip("'")
                if key and value:
                    env[key] = value
    except Exception as e:
        logger.warning("加载 %s 失败: %s", path, e)
    return env


def get_config() -> dict[str, Any]:
    global_env = load_env_file(GLOBAL_CONFIG_FILE)
    project_env = load_env_file(PROJECT_ENV_FILE)
    merged = {**global_env, **project_env}

    config: dict[str, Any] = {}
    for key in ["TUSHARE_TOKEN", "FRED_API_KEY", "TAVILY_API_KEY"]:
        config[key] = os.environ.get(key) or merged.get(key)

    config["_CONFIG_SOURCE"] = (
        f"project:{PROJECT_ENV_FILE}" if PROJECT_ENV_FILE.exists()
        else f"global:{GLOBAL_CONFIG_FILE}" if GLOBAL_CONFIG_FILE.exists()
        else "env_only"
    )
    return config


def is_tushare_available(config: dict[str, Any]) -> bool:
    token = config.get("TUSHARE_TOKEN")
    return bool(token and re.match(r'^[a-zA-Z0-9]{32,}$', token))


def is_fred_available(config: dict[str, Any]) -> bool:
    key = config.get("FRED_API_KEY", "")
    return bool(key and re.match(r'^[a-zA-Z0-9]{32}$', key))


# ---- Token 缺失提示 ----

_MISSING_TOKEN_INFO = {
    "TUSHARE_TOKEN": {
        "env_key": "TUSHARE_TOKEN",
        "description": "Tushare Token",
        "how_to_get": "前往 https://tushare.pro 注册并获取 API Token（需积分 ≥120）",
        "affected_features": [
            "PE/PB 历史分位与估值位置分析",
            "十大股东明细",
            "北向资金流向",
            "机构研报数据",
            "行业成分股对比",
            "基本面数据（Fina Indicator）",
        ],
    },
    "FRED_API_KEY": {
        "env_key": "FRED_API_KEY",
        "description": "FRED API Key",
        "how_to_get": "前往 https://fred.stlouisfed.org/docs/api/api_key.html 免费申请",
        "affected_features": [
            "美国 10 年期国债收益率",
            "美元/人民币汇率",
            "联邦基金利率",
            "美国 CPI / PPI / GDP",
        ],
    },
    "TAVILY_API_KEY": {
        "env_key": "TAVILY_API_KEY",
        "description": "Tavily API Key",
        "how_to_get": "前往 https://tavily.com 注册并获取 API Key",
        "affected_features": [
            "新闻包 Layer 3（Tavily 联网搜索）",
            "自动新闻摘要与事件驱动分析",
        ],
    },
}


def get_missing_tokens(config: dict[str, Any] | None = None) -> list[dict]:
    """检查缺失的可选 API Token，返回结构化信息列表。"""
    if config is None:
        config = get_config()
    missing: list[dict] = []
    for token_key, info in _MISSING_TOKEN_INFO.items():
        if not config.get(token_key):
            missing.append(dict(info))
    return missing


def format_missing_token_warnings(config: dict[str, Any] | None = None) -> str | None:
    """生成缺失 Token 的提示文本。全部已配置则返回 None。"""
    missing = get_missing_tokens(config)
    if not missing:
        return None
    lines = ["⚠️  以下 API Token 未配置，相关功能将降级或跳过：", ""]
    for m in missing:
        lines.append(f"  🔑 {m['description']}（{m['env_key']}）")
        lines.append(f"     📋 获取方式: {m['how_to_get']}")
        lines.append(f"     📉 受影响功能:")
        for feat in m["affected_features"]:
            lines.append(f"        - {feat}")
        lines.append("")
    lines.append(
        "  💡 配置方式: 将 Token 写入项目 .env 文件或全局 ~/.config/investment/.env"
    )
    return "\n".join(lines)


def print_missing_token_warnings(config: dict[str, Any] | None = None) -> None:
    """打印缺失 Token 警告到 stderr（全部已配置则不输出）。"""
    import sys
    msg = format_missing_token_warnings(config)
    if msg:
        print(msg, file=sys.stderr)


def is_tavily_available(config: dict[str, Any] | None = None) -> bool:
    if config is None:
        config = get_config()
    key = config.get("TAVILY_API_KEY", "")
    return bool(key and len(str(key).strip()) >= 8)


def is_akshare_available() -> bool:
    """检测 akshare 是否可用（导入成功即为可用）。"""
    try:
        import akshare  # noqa: F401
        return True
    except ImportError:
        return False


def is_baostock_available() -> bool:
    """检测 baostock 是否可用（导入成功即为可用）。"""
    try:
        import baostock  # noqa: F401
        return True
    except ImportError:
        return False


def is_tickflow_available() -> bool:
    """检测 TickFlow 是否可用（免费 tier，无需注册）。"""
    try:
        import tickflow  # noqa: F401
        return True
    except ImportError:
        return False


def is_tencent_available() -> bool:
    """检测腾讯行情是否可达（用于 diagnose 报告；与 collector 一致强制直连）。"""
    from .proxy import no_proxy_session

    try:
        with no_proxy_session() as sess:
            r = sess.get("http://qt.gtimg.cn/q=sh600519", timeout=3)
        return r.status_code == 200 and "~" in r.text
    except Exception:
        return False


def is_eastmoney_api_reachable() -> dict[str, Any]:
    """检测东方财富 push2 API 是否可达（用于 diagnose 报告）。"""
    from .proxy import probe_push2_eastmoney

    return probe_push2_eastmoney(timeout=8)


def diagnose(config: dict[str, Any] | None = None) -> dict[str, Any]:
    if config is None:
        config = get_config()
    from .proxy import clash_rules_yaml, proxy_status

    px = proxy_status(probe=True)
    eastmoney = px.get("push2") or is_eastmoney_api_reachable()
    sources = {
        "tushare": is_tushare_available(config),
        "fred": is_fred_available(config),
        "tencent": is_tencent_available(),
        "akshare": is_akshare_available(),
        "akshare_eastmoney_api": eastmoney,
        "baostock": is_baostock_available(),
        "tickflow": is_tickflow_available(),
    }
    hint_kind = px.get("hint_kind")
    bypass = px.get("bypass_effective")
    return {
        "config_source": config.get("_CONFIG_SOURCE", "unknown"),
        "project_root": str(PROJECT_ROOT),
        "store_db": str(STORE_DB),
        "store_exists": STORE_DB.exists(),
        "proxy_detected": px["detected"],
        "proxy_bypass_effective": bypass,
        "proxy_user_action_needed": px.get("user_action_needed"),
        "proxy_hint_kind": hint_kind,
        "proxy_env_keys": px["env_keys"],
        "proxy_system": px["system_proxies"],
        "proxy_requests_active": not bypass and px["detected"] if bypass is not None else False,
        "clash_rules_hint": clash_rules_yaml() if hint_kind == "clash_rules" else None,
        "sources": sources,
        "available_count": sum(
            1 for v in sources.values()
            if (isinstance(v, bool) and v) or (isinstance(v, dict) and v.get("reachable"))
        ),
        "total_count": len(sources),
    }


# P3-2: WebSearch 白名单（涨价信号触发时使用）
PRICE_NEWS_WHITELIST = [
    "stcn.com",              # 证券时报
    "cnstock.com",           # 上海证券报/中国证券网
    "cs.com.cn",             # 中国证券报
    "21jingji.com",          # 21世纪经济报道
    "eeo.com.cn",            # 经济观察报
    "finance.sina.com.cn",   # 新浪财经
    "10jqka.com.cn",         # 同花顺
    "cls.cn",                # 财联社
]
# ⚠️ 东方财富 (eastmoney.com) 因代理问题暂不列入

# cninfo 高管增减持全市场接口超时（秒）；超时则跳过该方向
CNINFO_HOLDER_TIMEOUT_SEC = max(
    5, int(os.environ.get("INVEST_CNINFO_HOLDER_TIMEOUT", "45"))
)


def ensure_env_loaded() -> None:
    """将 .env 变量注入 os.environ（向后兼容）。"""
    for key, value in load_env_file(PROJECT_ENV_FILE).items():
        if key not in os.environ:
            os.environ[key] = value
