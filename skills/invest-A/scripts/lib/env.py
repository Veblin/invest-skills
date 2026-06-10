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
    for key in ["TUSHARE_TOKEN", "FRED_API_KEY"]:
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


def is_akshare_available() -> bool:
    """检测 akshare 是否可用（导入成功即为可用）。"""
    try:
        import akshare  # noqa: F401
        return True
    except ImportError:
        return False


def is_tencent_available() -> bool:
    import requests
    try:
        r = requests.get("http://qt.gtimg.cn/q=sh600519", timeout=3)
        return r.status_code == 200 and "~" in r.text
    except Exception:
        return False


def diagnose(config: dict[str, Any] | None = None) -> dict[str, Any]:
    if config is None:
        config = get_config()
    sources = {
        "tushare": is_tushare_available(config),
        "fred": is_fred_available(config),
        "tencent": is_tencent_available(),
        "akshare": is_akshare_available(),
    }
    return {
        "config_source": config.get("_CONFIG_SOURCE", "unknown"),
        "project_root": str(PROJECT_ROOT),
        "store_db": str(STORE_DB),
        "store_exists": STORE_DB.exists(),
        "sources": sources,
        "available_count": sum(1 for v in sources.values() if v),
        "total_count": len(sources),
    }


def ensure_env_loaded() -> None:
    """将 .env 变量注入 os.environ（向后兼容）。"""
    for key, value in load_env_file(PROJECT_ENV_FILE).items():
        if key not in os.environ:
            os.environ[key] = value
