"""Suspension detection using trade calendar data.

Given a fixed universe of stocks, a mapping of trade date → set of stocks
present in the daily data, and a full ordered list of trade dates, this
module flags dates on which a stock was likely suspended (present in the
universe but absent from the daily data on a trading day).

A helper is also provided to check whether a gap formed across a suspension
period — i.e. the trading day immediately before the gap date was a
suspension for that stock.
"""

from __future__ import annotations


def detect_suspensions(
    universe: list[str],
    daily_by_date: dict[str, set[str]],
    trade_cal: list[str],
    list_dates: dict[str, str] | None = None,
) -> dict[str, list[str]]:
    """Detect which stocks were suspended on which dates.

    For each stock in *universe*, every date in *trade_cal* is checked.  A
    stock is considered suspended on a given trading day if:

    1. That date exists in *trade_cal*.
    2. The stock is **not** present in *daily_by_date* for that date.
    3. The stock **has** appeared on at least one other date (to avoid
       flagging stocks that never had any data at all).
    4. The date is on or after the stock's listing date when provided
       via *list_dates* (pre-listing absences are not suspensions).

    Args:
        universe: List of ``ts_code`` strings in the scan universe.
        daily_by_date: Mapping of ``trade_date`` → ``set[ts_code]`` of
            stocks that have a daily bar on that date.
        trade_cal: Ordered list of all trade dates in the window (format
            ``yyyymmdd``).
        list_dates: Optional ``ts_code`` → listing date (``yyyymmdd``).
            Dates strictly before listing are not flagged as suspensions.

    Returns:
        A dictionary mapping ``ts_code`` → sorted list of suspension
        dates (``yyyymmdd``).  Stocks with no suspensions are omitted.
    """
    # Build the set of stocks that have appeared at least once
    appeared: set[str] = set()
    for codes in daily_by_date.values():
        appeared.update(codes)

    list_dates = list_dates or {}
    result: dict[str, list[str]] = {}

    for ts_code in universe:
        if ts_code not in appeared:
            # Never had any data at all — not suspension, just no data
            continue
        list_date = (list_dates.get(ts_code) or "").strip()
        susp_dates: list[str] = []
        for trade_date in trade_cal:
            if list_date and trade_date < list_date:
                continue
            present = daily_by_date.get(trade_date, set())
            if ts_code not in present:
                susp_dates.append(trade_date)
        if susp_dates:
            result[ts_code] = susp_dates

    return result


def is_gap_across_suspension(
    gap_date: str,
    suspension_dates: list[str],
    trade_cal: list[str],
) -> bool:
    """Check if a gap formed across a suspension period.

    A gap is considered "across suspension" when the trading day immediately
    before *gap_date* is a suspension date for the stock — meaning trading
    resumed after a suspension, and the gap formed on the resumption day.

    Args:
        gap_date: The date the gap was detected (``yyyymmdd``).
        suspension_dates: Sorted list of suspension dates for the stock
            (as returned by :func:`detect_suspensions`).
        trade_cal: Ordered list of all trade dates in the window.

    Returns:
        ``True`` if the trading day immediately preceding *gap_date* is a
        suspension date, ``False`` otherwise.  Returns ``False`` when
        *gap_date* is the first date in *trade_cal* (no preceding day).
    """
    if not suspension_dates or not trade_cal:
        return False

    # Find the index of gap_date in trade_cal
    try:
        gap_idx = trade_cal.index(gap_date)
    except ValueError:
        return False

    # If gap_date is the first trade date, no preceding day exists
    if gap_idx == 0:
        return False

    prev_date = trade_cal[gap_idx - 1]
    return prev_date in suspension_dates
