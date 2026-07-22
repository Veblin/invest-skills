"""Bootstrap ``skills/lib`` onto sys.path (shared_dates / shared_codes shims)."""

from __future__ import annotations

import sys
from pathlib import Path


def ensure_skills_lib_on_path() -> Path:
    """Insert skills/lib on sys.path (idempotent). Returns the directory."""
    root = Path(__file__).resolve().parent.parent.parent.parent / "lib"
    s = str(root)
    if s not in sys.path:
        sys.path.insert(0, s)
    return root
