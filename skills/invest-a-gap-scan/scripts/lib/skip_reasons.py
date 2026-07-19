"""Enums for tracking why stocks are excluded or didn't hit during gap scanning.

ExcludeReason covers reasons a stock never enters gap scanning (pool-level filtering).
NonHitReason covers reasons a scanned stock had no qualifying gap (scan-level filtering).
Both use str, Enum so values can be JSON-serialized without custom encoders.
"""

from __future__ import annotations

from collections import Counter
from enum import Enum


class ExcludeReason(str, Enum):
    """Reasons a stock never entered gap scanning (pool-level exclusion).

    Each value is a short snake_case string suitable for JSON serialization
    and Counter-based aggregation.
    """

    ST_STOCK = "st_stock"
    DELIST = "delist"
    INSUFFICIENT_KLINE = "insufficient_kline"
    MISSING_ADJ_FACTOR = "missing_adj_factor"
    FETCH_ERROR = "fetch_error"
    LOW_LIQUIDITY = "low_liquidity"


class NonHitReason(str, Enum):
    """Reasons a scanned stock had no qualifying gap (scan-level non-hit).

    Each value is a short snake_case string suitable for JSON serialization
    and Counter-based aggregation.
    """

    NO_GAP = "no_gap"
    BELOW_THRESHOLD = "below_threshold"
    MA60_BROKEN = "ma60_broken"
    GAP_FILLED = "gap_filled"
    VOL_RATIO_LOW = "vol_ratio_low"


def new_counters() -> tuple[Counter, Counter]:
    """Return (exclude_counter, non_hit_counter) fresh Counter pair.

    Returns:
        A tuple of two empty Counters — the first for ExcludeReason counts,
        the second for NonHitReason counts.
    """
    return Counter(), Counter()
