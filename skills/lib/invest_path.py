"""Shared path bootstrap for cross-skill import of invest-a-stock scripts.

Canonical implementation (Batch D / X-02). Skill-local `_invest_path.py` files
are thin shims that re-export from here.
"""

from __future__ import annotations

import sys
from pathlib import Path

__all__ = ["invest_a_scripts_dir", "ensure_invest_a_scripts_on_path"]


def invest_a_scripts_dir() -> Path:
    """skills/invest-a-stock/scripts — resolved from skills/lib/."""
    return Path(__file__).resolve().parent.parent / "invest-a-stock" / "scripts"


def ensure_invest_a_scripts_on_path() -> Path:
    """Insert invest-a-stock/scripts on sys.path (idempotent). Returns the path."""
    scripts = invest_a_scripts_dir()
    s = str(scripts)
    if s not in sys.path:
        sys.path.insert(0, s)
    return scripts
