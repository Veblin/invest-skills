"""Shared path bootstrap for cross-skill import of invest-a-stock scripts."""

from __future__ import annotations

import sys
from pathlib import Path


def invest_a_scripts_dir() -> Path:
    """skills/invest-a-stock/scripts — resolved from this file under invest-a-limit-up/scripts/lib/."""
    return (
        Path(__file__).resolve().parent.parent.parent.parent
        / "invest-a-stock"
        / "scripts"
    )


def ensure_invest_a_scripts_on_path() -> Path:
    """Insert invest-a-stock/scripts on sys.path (idempotent). Returns the path."""
    scripts = invest_a_scripts_dir()
    s = str(scripts)
    if s not in sys.path:
        sys.path.insert(0, s)
    return scripts
