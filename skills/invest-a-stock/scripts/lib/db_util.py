"""Shim: canonical implementation at skills/lib/db_util.py. Backward compatible."""
from __future__ import annotations

import sys
from pathlib import Path

_lib = str(Path(__file__).resolve().parent.parent.parent.parent / "lib")
if _lib not in sys.path:
    sys.path.insert(0, _lib)

from db_util import (  # noqa: E402, F401
    connect_db,
    load_recent_rows,
    safe_close,
    upsert_daily_rows,
)
