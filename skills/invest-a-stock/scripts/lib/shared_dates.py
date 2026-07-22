"""Re-export shared ``yyyymmdd_to_iso`` from ``skills/lib/dates.py``.

Canonical implementation lives in skills/lib; this module only bootstraps
the path and re-exports for invest-a-stock internal imports.
"""

from __future__ import annotations

import sys
from pathlib import Path

_skills_lib = Path(__file__).resolve().parent.parent.parent.parent / "lib"
_s = str(_skills_lib)
if _s not in sys.path:
    sys.path.insert(0, _s)

from dates import yyyymmdd_to_iso  # noqa: E402

__all__ = ["yyyymmdd_to_iso"]
