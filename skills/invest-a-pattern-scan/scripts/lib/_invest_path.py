"""Shim — re-exports shared skills/lib/invest_path（仿 invest-a-stock shim）。

canonical 模块（gap-scan universe/kline_source）依赖 `ensure_invest_a_scripts_on_path`
与 `ensure_shared_lib_on_path`，两者一并 re-export 防止同名遮蔽。
"""
from __future__ import annotations

import sys
from pathlib import Path

_skills_lib = Path(__file__).resolve().parent.parent.parent.parent.parent / "lib"
_s = str(_skills_lib)
if _s not in sys.path:
    sys.path.insert(0, _s)

from invest_path import (  # noqa: E402
    ensure_invest_a_scripts_on_path,
    ensure_shared_lib_on_path,
    load_gap_scan_module,
)

__all__ = [
    "ensure_invest_a_scripts_on_path",
    "ensure_shared_lib_on_path",
    "load_gap_scan_module",
]
