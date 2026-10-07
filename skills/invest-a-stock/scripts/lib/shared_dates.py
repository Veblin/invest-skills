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


def fmt_collection_period(collection: dict) -> str:
    """Display the sealed collection window; keep old snapshots readable."""
    start = parse_utc_iso(collection.get("collection_started_at"))
    end = parse_utc_iso(collection.get("collection_completed_at"))
    if start is None or end is None or end < start:
        return fmt_fetched_at(collection.get("fetched_at", ""))
    start_s = fmt_fetched_at(collection["collection_started_at"]).replace(" (北京时间)", "")
    same_local_day = (
        fmt_fetched_at(collection["collection_started_at"], pattern="%Y-%m-%d")
        == fmt_fetched_at(collection["collection_completed_at"], pattern="%Y-%m-%d")
    )
    end_s = fmt_fetched_at(
        collection["collection_completed_at"],
        pattern="%H:%M" if same_local_day else "%Y-%m-%d %H:%M",
    ).replace(" (北京时间)", "")
    return f"{start_s}–{end_s} (北京时间；各维度取数时点不同)"

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
    "fmt_collection_period",
]
