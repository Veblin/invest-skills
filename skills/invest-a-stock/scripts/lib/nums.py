"""Shim: canonical implementation at skills/lib/nums.py. Backward compatible."""
from __future__ import annotations

import sys
from pathlib import Path

_lib = str(Path(__file__).resolve().parent.parent.parent.parent / "lib")
if _lib not in sys.path:
    sys.path.insert(0, _lib)

from nums import safe_float, coalesce_field, fmt_amount  # noqa: E402, F401
