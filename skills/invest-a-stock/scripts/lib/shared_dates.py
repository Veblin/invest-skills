"""Re-export shared ``yyyymmdd_to_iso`` from ``skills/lib/dates.py``.

Canonical implementation lives in skills/lib; this module only bootstraps
the path and re-exports for invest-a-stock internal imports.
"""

from __future__ import annotations

from ._invest_path import ensure_skills_lib_on_path

ensure_skills_lib_on_path()

from dates import (  # noqa: E402
    fmt_fetched_at,
    latest_month_row,
    normalize_end_date,
    parse_date,
    parse_utc_iso,
    shanghai_days_ago,
    shanghai_now,
    shanghai_today,
    yyyymmdd_to_iso,
)

__all__ = [
    "parse_date",
    "yyyymmdd_to_iso",
    "shanghai_now",
    "shanghai_today",
    "shanghai_days_ago",
    "normalize_end_date",
    "latest_month_row",
    "parse_utc_iso",
    "fmt_fetched_at",
]
