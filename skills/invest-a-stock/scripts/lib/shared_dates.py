"""Re-export shared ``yyyymmdd_to_iso`` from ``skills/lib/dates.py``.

Canonical implementation lives in skills/lib; this module only bootstraps
the path and re-exports for invest-a-stock internal imports.
"""

from __future__ import annotations

from ._skills_lib_path import ensure_skills_lib_on_path

ensure_skills_lib_on_path()

from dates import yyyymmdd_to_iso  # noqa: E402

__all__ = ["yyyymmdd_to_iso"]
