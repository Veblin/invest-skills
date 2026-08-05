"""Financial row helpers shared across collector, store, risk_scanner, and scoring."""

from __future__ import annotations

from datetime import date
from typing import Any

from lib.nums import safe_float

# normalize_end_date 已提升至 skills/lib/dates.py（共用库提升），此处 re-export 保持 BC
from .shared_dates import normalize_end_date  # noqa: E402, F401


def parse_end_date(raw: Any) -> date | None:
    """Parse a date string (YYYYMMDD / YYYY-MM-DD / YYYY.MM.DD) to a ``date`` object."""
    if raw is None:
        return None
    s = normalize_end_date(str(raw))
    if len(s) < 8 or not s[:8].isdigit():
        return None
    try:
        return date(int(s[:4]), int(s[4:6]), int(s[6:8]))
    except ValueError:
        return None


def prior_year_end_date(end_date: str) -> str:
    """Report period → same calendar date one year earlier (YYYYMMDD)."""
    norm = normalize_end_date(end_date)
    if len(norm) < 8 or not norm[:8].isdigit():
        return ""
    return f"{int(norm[:4]) - 1}{norm[4:8]}"


def find_yoy_row(rows: list[dict], latest: dict) -> dict | None:
    """Locate the record with same calendar month-day, one year earlier.

    Compares normalized ``end_date`` values so ``2023-12-31`` matches ``20231231``.
    """
    yoy_end = prior_year_end_date(str(latest.get("end_date", "")))
    if not yoy_end:
        return None
    for r in rows:
        if not isinstance(r, dict):
            continue
        if normalize_end_date(str(r.get("end_date", ""))) == yoy_end:
            return r
    return None


def gross_margin_annual_series(fin_rows: list[dict]) -> list[tuple[str, float]]:
    """Latest gross margin per calendar year, sorted ascending."""
    by_year: dict[str, float] = {}
    for r in fin_rows:
        y = normalize_end_date(str(r.get("end_date", "")))[:4]
        gm = safe_float(r.get("grossprofit_margin") or r.get("gross_margin"))
        if y and gm is not None:
            by_year[y] = gm
    return sorted(by_year.items())


def gross_margin_trend_from_rows(
    fin_rows: list[dict], *, threshold: float = 0.5,
) -> str | None:
    """Year-over-year gross margin direction (up / down / flat)."""
    annual = gross_margin_annual_series(fin_rows)
    if len(annual) < 2:
        return None
    (_, m0), (_, m1) = annual[-2], annual[-1]
    if m1 < m0 - threshold:
        return "down"
    if m1 > m0 + threshold:
        return "up"
    return "flat"
