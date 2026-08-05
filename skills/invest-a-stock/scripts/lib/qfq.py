"""Shim: canonical implementation at skills/lib/qfq.py. Backward compatible."""
from __future__ import annotations

import sys
from pathlib import Path

_lib = str(Path(__file__).resolve().parent.parent.parent.parent / "lib")
if _lib not in sys.path:
    sys.path.insert(0, _lib)

from qfq import PRICE_COLS, apply_qfq, apply_qfq_rows  # noqa: E402, F401
