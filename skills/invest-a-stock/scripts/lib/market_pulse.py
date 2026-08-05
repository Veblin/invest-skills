"""Shim: canonical implementation at skills/lib/market_pulse.py. Backward compatible."""
from __future__ import annotations

import sys
from pathlib import Path

_lib = str(Path(__file__).resolve().parent.parent.parent.parent / "lib")
if _lib not in sys.path:
    sys.path.insert(0, _lib)

from market_pulse import fetch_margin_account_info  # noqa: E402, F401
