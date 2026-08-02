"""Bootstrap ``skills/lib`` onto sys.path (shared_dates / shared_codes shims).

Delegates to the canonical ``invest_path.ensure_shared_lib_on_path`` after
an inline bootstrap (chicken-and-egg: skills/lib must be importable before
``invest_path`` itself can be imported).
"""

from __future__ import annotations

import sys
from pathlib import Path


def ensure_skills_lib_on_path() -> Path:
    """Insert skills/lib on sys.path (idempotent). Returns the directory."""
    _inline_bootstrap()
    from invest_path import ensure_shared_lib_on_path
    return ensure_shared_lib_on_path()


def _inline_bootstrap() -> None:
    """Insert skills/lib on sys.path without importing from it (one-shot)."""
    root = Path(__file__).resolve().parent.parent.parent.parent / "lib"
    s = str(root)
    if s not in sys.path:
        sys.path.insert(0, s)
