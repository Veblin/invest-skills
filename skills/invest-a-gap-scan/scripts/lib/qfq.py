"""Qian-fu-quan (前复权) price adjustment using adj_factor from Tushare.

Formula: qfq_price = raw_price * adj_factor / latest_adj_factor

This aligns the price series so that the most recent bar's adjusted price
equals its raw (unadjusted) price, matching the display convention of most
Chinese securities terminals.
"""

from __future__ import annotations

import math

import pandas as pd

PRICE_COLS = ("open", "high", "low", "close")

# Reject zero / near-zero / non-positive factors that would explode qfq prices.
_ADJ_EPS = 1e-12


def _adj_invalid(val: float) -> bool:
    """True if *val* is non-finite, non-positive, or below ``_ADJ_EPS``."""
    return (not math.isfinite(val)) or val <= _ADJ_EPS


def apply_qfq(daily_df: pd.DataFrame, adj_factor_df: pd.DataFrame | None) -> pd.DataFrame | None:
    """Apply qian-fu-quan adjustment to daily price data.

    Merges *adj_factor_df* onto *daily_df* on ``trade_date``, then computes
    qfq-adjusted OHLC columns.  The original columns are preserved alongside
    the new ``{col}_qfq`` columns.

    Args:
        daily_df: Daily bars with at least columns
            ``[trade_date, open, high, low, close]``.  Other columns (e.g.
            ``amount``, ``vol``) are passed through unmodified.
        adj_factor_df: Adjustment factor series with columns
            ``[trade_date, adj_factor]``, typically fetched via
            Tushare's ``adj_factor`` API.  **May be None or empty.**
            Assumed **dense daily** (one factor per trade date).  A sparse
            (ex-rights-only) series would NaN-out most rows after the left
            merge and hard-reject; no forward-fill is applied.

    Returns:
        A new DataFrame with the original columns **plus** the qfq-adjusted
        columns named ``open_qfq``, ``high_qfq``, ``low_qfq``, ``close_qfq``.
        Returns **None** if *adj_factor_df* is None, empty, has no overlapping
        dates, any daily bar lacks a factor, any factor is non-finite /
        non-positive / near-zero (``<= 1e-12``), or OHLC contains NaN
        (whole-stock exclude — never emit partial-NaN or astronomical qfq
        prices).

    Notes:
        - Uses pandas vectorized operations — no row-level loops.
        - ``amount`` is **not** adjusted (it is already in monetary terms).
        - Partial adj_factor coverage (any NaN after merge) causes a hard
          reject so gap/MA60 logic never sees corrupted prices.
        - Dense-daily adj_factor is a hard assumption; sparse series are
          documentation debt (no ffill) until a safe fill policy is proven.
    """
    if adj_factor_df is None or adj_factor_df.empty:
        return None

    # Merge on trade_date (left join to preserve all daily rows).
    # Dense daily adj_factor assumed — sparse (ex-rights-only) → mostly NaN → reject.
    merged = daily_df.merge(
        adj_factor_df[["trade_date", "adj_factor"]],
        on="trade_date",
        how="left",
    )

    # Reject incomplete coverage — including trailing NaN that would poison scale
    if merged["adj_factor"].isna().any():
        return None

    factors = merged["adj_factor"].astype(float)
    # Row-level + latest: reject non-finite / <=0 / near-zero (avoids astronomical prices)
    if any(_adj_invalid(float(v)) for v in factors):
        return None

    latest_adj = float(factors.iloc[-1])
    scale = factors / latest_adj

    # Reject if any OHLC column contains NaN (R7)
    for col in PRICE_COLS:
        if col in merged.columns and merged[col].isna().any():
            return None

    for col in PRICE_COLS:
        merged[f"{col}_qfq"] = merged[col] * scale

    return merged
