"""Shim — re-exports shared skills/lib/invest_path (Batch D / X-02)."""

from __future__ import annotations

import sys
from pathlib import Path

_skills_lib = Path(__file__).resolve().parent.parent.parent.parent / "lib"
_s = str(_skills_lib)
if _s not in sys.path:
    sys.path.insert(0, _s)

from invest_path import ensure_invest_a_scripts_on_path

__all__ = ["ensure_invest_a_scripts_on_path"]
