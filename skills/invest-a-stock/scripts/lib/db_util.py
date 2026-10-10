"""Shim: canonical implementation at skills/lib/db_util.py. Backward compatible."""
from __future__ import annotations

from ._invest_path import ensure_skills_lib_on_path

ensure_skills_lib_on_path()

from db_util import (  # noqa: E402, F401
    close_connection,
    connect_db,
    connection,
    hist_ex_today,
    load_recent_rows,
    open_connection,
    safe_close,
    upsert_daily_rows,
)
