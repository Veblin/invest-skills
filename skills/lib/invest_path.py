"""Shared path bootstrap for cross-skill import of invest-a-stock scripts.

Canonical implementation (Batch D / X-02). Skill-local `_invest_path.py` files
are thin shims that re-export from here.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

__all__ = [
    "invest_a_scripts_dir",
    "invest_a_etf_lib_dir",
    "ensure_invest_a_scripts_on_path",
    "ensure_shared_lib_on_path",
    "load_invest_a_etf_module",
]


def invest_a_scripts_dir() -> Path:
    """skills/invest-a-stock/scripts — resolved from skills/lib/."""
    return Path(__file__).resolve().parent.parent / "invest-a-stock" / "scripts"


def invest_a_etf_lib_dir() -> Path:
    """skills/invest-a-etf/scripts/lib — canonical ETF data module."""
    return Path(__file__).resolve().parent.parent / "invest-a-etf" / "scripts" / "lib"


def gap_scan_lib_dir() -> Path:
    """skills/invest-a-gap-scan/scripts/lib — canonical gap-scan data pipeline."""
    return Path(__file__).resolve().parent.parent / "invest-a-gap-scan" / "scripts" / "lib"


# 固定模块注册名：pattern-scan shim 与调用方共用同一 canonical 实例
_GAP_SCAN_KLINE_SOURCE_NAME = "gap_scan_kline_source"


def load_gap_scan_module(module_file: str = "kline_source"):
    """按显式路径加载 gap-scan canonical 模块（仿 load_invest_a_etf_module）。

    返回 sys.modules 缓存的模块实例；canonical 文件缺失抛 ImportError。
    module_file: "kline_source" 或 "universe"。
    """
    mod_name = f"{_GAP_SCAN_KLINE_SOURCE_NAME}_{module_file}"
    mod = sys.modules.get(mod_name)
    if mod is not None:
        return mod
    lib = gap_scan_lib_dir()
    s = str(lib)
    if s not in sys.path:
        sys.path.insert(0, s)
    import importlib.util as _ilu

    spec = _ilu.spec_from_file_location(mod_name, lib / f"{module_file}.py")
    if spec is None or spec.loader is None:
        raise ImportError(f"gap-scan canonical {module_file}.py missing at {lib}")
    mod = _ilu.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


def ensure_invest_a_scripts_on_path() -> Path:
    """Insert invest-a-stock/scripts on sys.path (idempotent). Returns the path."""
    scripts = invest_a_scripts_dir()
    s = str(scripts)
    if s not in sys.path:
        sys.path.insert(0, s)
    return scripts


def ensure_shared_lib_on_path() -> Path:
    """Insert skills/lib on sys.path (idempotent). Returns the directory.

    Note: chicken-and-egg — modules *outside* skills/lib must inline a
    bootstrap before importing this (see skill-local ``_invest_path.py``).
    """
    d = Path(__file__).resolve().parent
    s = str(d)
    if s not in sys.path:
        sys.path.insert(0, s)
    return d


# 固定模块注册名：journal shim 与 data_bridge 共用同一 canonical 实例
_INVEST_A_ETF_MODULE_NAME = "invest_a_etf_etf_data"


def load_invest_a_etf_module():
    """Load invest-a-etf's canonical ``scripts/lib/etf_data.py`` by explicit path.

    Deterministic replacement for a bare ``import etf_data``, which resolves
    via ``sys.path`` order and silently degrades to ``None`` (ImportError)
    whenever invest-a-etf's lib dir is not on the path (e.g. invest-a-stock
    context). This loader:

    1. ensures ``invest-a-etf/scripts/lib`` is on ``sys.path`` so the
       module's own top-level imports (``_invest_path`` → shared
       ``invest_path``) resolve the same way they do inside invest-a-etf;
    2. loads ``etf_data.py`` by explicit file path under the fixed module
       name ``invest_a_etf_etf_data``, cached in ``sys.modules`` so
       data_bridge and the journal shim share one instance.

    Raises :exc:`ImportError` when the canonical file is missing.
    """
    mod = sys.modules.get(_INVEST_A_ETF_MODULE_NAME)
    if mod is not None:
        return mod
    lib = invest_a_etf_lib_dir()
    s = str(lib)
    if s not in sys.path:
        sys.path.insert(0, s)
    path = lib / "etf_data.py"
    spec = importlib.util.spec_from_file_location(_INVEST_A_ETF_MODULE_NAME, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load invest-a-etf etf_data from {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[_INVEST_A_ETF_MODULE_NAME] = mod
    spec.loader.exec_module(mod)
    return mod
