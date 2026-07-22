"""Tests for gap_scanner.py -- all synthetic data, no network calls.

Covers gap scanning logic: gap detection, tolerance rule (newest-first),
MA60 streak, gap unfilled, vol ratio, across-suspension detection,
and exclusion reasons.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from gap_scanner import GapInfo, _build_scan_hit, _check_unfilled, scan_all
from skip_reasons import ExcludeReason, NonHitReason


# ======================================================================
# Helpers
# ======================================================================


class MockStock:
    def __init__(self, ts_code, name="测试", index_membership=None, board="主板"):
        self.ts_code = ts_code
        self.name = name
        self.index_membership = index_membership or []
        self.board = board


def _make_kline(
    n_bars=200,
    base_price=10.0,
    gap_at=None,
    gap_pct=1.5,
    break_ma60=False,
    fill_gap=False,
    amounts=None,
):
    """Build synthetic K-line DataFrame.

    Creates a slight uptrend.  When a gap is created, post-gap bars are
    automatically lifted so their lows stay above the gap upper bound
    (so the gap is *unfilled*).  ``break_ma60`` and ``fill_gap`` override
    specific bars to break the MA60 streak or deliberately fill the gap.

    Parameters
    ----------
    gap_at : int, optional
        Index at which to create an upward gap.
    gap_pct : float
        Gap magnitude in percent (e.g. 1.5 = 1.5%).
    break_ma60 : bool
        Drop post-gap closes to 3.0 from *gap_at+10* onward so MA60 breaks.
    fill_gap : bool
        Insert a low below the gap's upper bound at *gap_at+10*.
    amounts : np.ndarray, optional
        Per-bar amount in yuan.  Default 5e8 per bar.
    """
    dates = pd.date_range("2025-01-01", periods=n_bars, freq="B")
    close = np.linspace(base_price, base_price * 1.2, n_bars)

    high = close * 1.01
    low = close * 0.99
    open_p = close * 1.00

    if gap_at is not None:
        prev_high = high[gap_at - 1]
        gap_upper = prev_high * (1 + gap_pct / 100)  # = low[gap_at]; gap upper bound

        # Gap day OHLC
        low[gap_at] = gap_upper
        open_p[gap_at] = gap_upper + (gap_upper - prev_high) * 0.3
        high[gap_at] = gap_upper * 1.01
        close[gap_at] = gap_upper * 1.005

        # Post-gap adjustments
        fill_idx = min(gap_at + 10, n_bars - 1)
        for i in range(gap_at + 1, n_bars):
            if break_ma60 and gap_at + 10 <= i < gap_at + 60:
                close[i] = 3.0
                low[i] = 2.9
                high[i] = 3.1
                open_p[i] = 3.0
            elif fill_gap and i == fill_idx:
                # Deliberately fill the gap with a low below the upper bound.
                # Keep close reasonable so MA60 check still passes.
                low[i] = gap_upper - 0.01
                if close[i] < gap_upper * 1.005:
                    close[i] = gap_upper * 1.005
                    high[i] = close[i] * 1.01
                    open_p[i] = close[i] * 0.998
            else:
                # Maintain the gap: lift the close so low > gap_upper
                close[i] = max(close[i], gap_upper * 1.02)
                low[i] = close[i] * 0.99
                high[i] = close[i] * 1.01
                open_p[i] = close[i] * 0.998

    if amounts is None:
        amounts = np.full(n_bars, 5e8)

    df = pd.DataFrame(
        {
            "trade_date": [d.strftime("%Y%m%d") for d in dates],
            "open_qfq": open_p,
            "high_qfq": high,
            "low_qfq": low,
            "close_qfq": close,
            "amount": amounts,
        }
    )
    return df


def _make_two_gap_kline(
    n_bars=200,
    base_price=10.0,
    gaps=None,
    amounts=None,
):
    """Build synthetic K-line with multiple gap entries.

    Parameters
    ----------
    gaps : list of (int, float, bool)
        Each element is (gap_index, gap_pct, fill_gap).  Gaps are applied in
        chronological order (oldest first, determined by sorting by index).
    """
    dates = pd.date_range("2025-01-01", periods=n_bars, freq="B")
    close = np.linspace(base_price, base_price * 1.2, n_bars)
    high = close * 1.01
    low = close * 0.99
    open_p = close * 1.00

    # Same post-gap maintenance as _make_kline, applied per gap in order
    for gap_at, gap_pct, fill in sorted(gaps or []):
        prev_high = high[gap_at - 1]
        gap_upper = prev_high * (1 + gap_pct / 100)

        # Gap day
        low[gap_at] = gap_upper
        open_p[gap_at] = gap_upper + (gap_upper - prev_high) * 0.3
        high[gap_at] = gap_upper * 1.01
        close[gap_at] = gap_upper * 1.005

        # Post-gap adjustments (same logic as _make_kline)
        fill_idx = min(gap_at + 10, n_bars - 1)
        for i in range(gap_at + 1, n_bars):
            if fill and i == fill_idx:
                low[i] = gap_upper - 0.01
                if close[i] < gap_upper * 1.005:
                    close[i] = gap_upper * 1.005
                    high[i] = close[i] * 1.01
                    open_p[i] = close[i] * 0.998
            else:
                close[i] = max(close[i], gap_upper * 1.02)
                low[i] = close[i] * 0.99
                high[i] = close[i] * 1.01
                open_p[i] = close[i] * 0.998

    if amounts is None:
        amounts = np.full(n_bars, 5e8)

    df = pd.DataFrame(
        {
            "trade_date": [d.strftime("%Y%m%d") for d in dates],
            "open_qfq": open_p,
            "high_qfq": high,
            "low_qfq": low,
            "close_qfq": close,
            "amount": amounts,
        }
    )
    return df


def _valid_adj() -> pd.DataFrame:
    """A non-None, non-empty adj_factor DataFrame (passes the presence check)."""
    return pd.DataFrame({"trade_date": ["20250101"], "adj_factor": [1.0]})


# ======================================================================
# Constants
# ======================================================================

DEFAULT_PARAMS = {
    "gap_min_pct": 1.0,
    "gap_lookback": 60,
    "gap_min_vol_ratio": 1.0,
    "min_avg_amount": 1e8,
    "min_list_days": 120,
}

STOCK = MockStock("000001.SZ")

# With n_bars=200 and gap_lookback=60, _find_candidate_gaps searches
# indices [max(1, 200-60), 200) = [140, 200).  All gaps placed for
# lookback-sensitive tests must be at idx >= 140.
_GAP_IDX = 150  # well within the lookback window


# ======================================================================
# Tests
# ======================================================================


class TestExcludeReasons:
    """Stocks excluded before ever reaching gap scanning."""

    def test_insufficient_kline(self):
        """Fewer bars than min_list_days -> INSUFFICIENT_KLINE."""
        kline = _make_kline(n_bars=50)
        result = scan_all(
            stocks=[STOCK],
            stock_kline_map={"000001.SZ": kline},
            adj_factor_map={"000001.SZ": _valid_adj()},
            suspension_map={},
            params=DEFAULT_PARAMS,
        )
        assert result.exclude_reasons[ExcludeReason.INSUFFICIENT_KLINE] == 1
        assert len(result.hits) == 0
        assert result.total_in_universe == 1
        assert result.total_scanned == 0

    def test_min_list_days_default_60(self):
        """Current CLI default min_list_days=60: 61 bars passes, 59 bars excluded."""
        params = {**DEFAULT_PARAMS, "min_list_days": 60}

        # 61 bars → passes
        kline_ok = _make_kline(n_bars=61)
        result = scan_all(
            stocks=[STOCK],
            stock_kline_map={"000001.SZ": kline_ok},
            adj_factor_map={"000001.SZ": _valid_adj()},
            suspension_map={},
            params=params,
        )
        assert result.exclude_reasons.get(ExcludeReason.INSUFFICIENT_KLINE, 0) == 0

        # 59 bars → excluded
        kline_short = _make_kline(n_bars=59)
        result = scan_all(
            stocks=[STOCK],
            stock_kline_map={"000001.SZ": kline_short},
            adj_factor_map={"000001.SZ": _valid_adj()},
            suspension_map={},
            params=params,
        )
        assert result.exclude_reasons[ExcludeReason.INSUFFICIENT_KLINE] == 1

    def test_missing_adj_factor(self):
        """Kline is None AND adj_factor is None -> MISSING_ADJ_FACTOR."""
        result = scan_all(
            stocks=[STOCK],
            stock_kline_map={"000001.SZ": None},
            adj_factor_map={"000001.SZ": None},
            suspension_map={},
            params=DEFAULT_PARAMS,
        )
        assert result.exclude_reasons[ExcludeReason.MISSING_ADJ_FACTOR] == 1
        assert result.total_fetch_errors == 1
        assert len(result.hits) == 0

    def test_already_qfq_missing_is_fetch_error(self):
        """Baostock path (already_qfq): absent kline -> FETCH_ERROR, not missing adj."""
        result = scan_all(
            stocks=[STOCK],
            stock_kline_map={},
            adj_factor_map={"000001.SZ": None},
            suspension_map={},
            params=DEFAULT_PARAMS,
            already_qfq=True,
        )
        assert result.exclude_reasons[ExcludeReason.FETCH_ERROR] == 1
        assert result.exclude_reasons[ExcludeReason.MISSING_ADJ_FACTOR] == 0
        assert result.total_with_kline == 0

    def test_low_liquidity(self):
        """20-day avg amount below min_avg_amount -> LOW_LIQUIDITY."""
        amounts = np.full(200, 1e5)  # 100k yuan per day, well below 1e8
        kline = _make_kline(n_bars=200, amounts=amounts)
        result = scan_all(
            stocks=[STOCK],
            stock_kline_map={"000001.SZ": kline},
            adj_factor_map={"000001.SZ": _valid_adj()},
            suspension_map={},
            params=DEFAULT_PARAMS,
        )
        assert result.exclude_reasons[ExcludeReason.LOW_LIQUIDITY] == 1
        assert len(result.hits) == 0


class TestNonHitReasons:
    """Stocks scanned but with no qualifying gap."""

    def test_no_gap(self):
        """Clean 200-bar k-line with no gaps -> NO_GAP."""
        kline = _make_kline(n_bars=200)
        result = scan_all(
            stocks=[STOCK],
            stock_kline_map={"000001.SZ": kline},
            adj_factor_map={"000001.SZ": _valid_adj()},
            suspension_map={},
            params=DEFAULT_PARAMS,
        )
        assert result.non_hit_reasons[NonHitReason.NO_GAP] == 1
        assert len(result.hits) == 0

    def test_below_threshold(self):
        """Gap magnitude (0.5%) below gap_min_pct (1.0) -> BELOW_THRESHOLD."""
        kline = _make_kline(n_bars=200, gap_at=_GAP_IDX, gap_pct=0.5)
        result = scan_all(
            stocks=[STOCK],
            stock_kline_map={"000001.SZ": kline},
            adj_factor_map={"000001.SZ": _valid_adj()},
            suspension_map={},
            params=DEFAULT_PARAMS,
        )
        assert result.non_hit_reasons[NonHitReason.BELOW_THRESHOLD] == 1
        assert len(result.hits) == 0

    def test_outside_lookback(self):
        """Gap at index 50 is before the lookback window (start=140) -> NO_GAP."""
        kline = _make_kline(n_bars=200, gap_at=50)
        result = scan_all(
            stocks=[STOCK],
            stock_kline_map={"000001.SZ": kline},
            adj_factor_map={"000001.SZ": _valid_adj()},
            suspension_map={},
            params=DEFAULT_PARAMS,
        )
        # Gap at idx=50 is before the lookback window [140, 200),
        # so _find_candidate_gaps finds nothing -> NO_GAP
        assert result.non_hit_reasons[NonHitReason.NO_GAP] == 1
        assert len(result.hits) == 0

    def test_ma60_broken(self):
        """Post-gap closes drop below MA60 -> MA60_BROKEN."""
        kline = _make_kline(n_bars=200, gap_at=_GAP_IDX, break_ma60=True)
        result = scan_all(
            stocks=[STOCK],
            stock_kline_map={"000001.SZ": kline},
            adj_factor_map={"000001.SZ": _valid_adj()},
            suspension_map={},
            params=DEFAULT_PARAMS,
        )
        assert result.non_hit_reasons[NonHitReason.MA60_BROKEN] == 1
        assert len(result.hits) == 0

    def test_gap_filled(self):
        """Post-gap low dips below gap_high -> GAP_FILLED (no suspension)."""
        kline = _make_kline(n_bars=200, gap_at=_GAP_IDX, fill_gap=True)
        result = scan_all(
            stocks=[STOCK],
            stock_kline_map={"000001.SZ": kline},
            adj_factor_map={"000001.SZ": _valid_adj()},
            suspension_map={},
            params=DEFAULT_PARAMS,
        )
        assert result.non_hit_reasons[NonHitReason.GAP_FILLED] == 1
        assert len(result.hits) == 0

    def test_touching_gap_high_counts_as_filled(self):
        """low == gap_high is filled per SKILL (strict > for unfilled)."""
        assert _check_unfilled([10.0, 11.0, 11.0], gap_idx=1, gap_high=11.0) is False
        assert _check_unfilled([10.0, 11.0, 11.01], gap_idx=1, gap_high=11.0) is True

        kline = _make_kline(n_bars=200, gap_at=_GAP_IDX)
        gap_high = float(kline.iloc[_GAP_IDX]["low_qfq"])
        touch_idx = _GAP_IDX + 10
        kline.loc[touch_idx, "low_qfq"] = gap_high
        # Keep close above MA60 so only fill reason triggers
        kline.loc[touch_idx, "close_qfq"] = max(
            float(kline.loc[touch_idx, "close_qfq"]), gap_high * 1.02
        )
        kline.loc[touch_idx, "high_qfq"] = max(
            float(kline.loc[touch_idx, "high_qfq"]),
            float(kline.loc[touch_idx, "close_qfq"]) * 1.01,
        )
        result = scan_all(
            stocks=[STOCK],
            stock_kline_map={"000001.SZ": kline},
            adj_factor_map={"000001.SZ": _valid_adj()},
            suspension_map={},
            params=DEFAULT_PARAMS,
        )
        assert result.non_hit_reasons[NonHitReason.GAP_FILLED] == 1
        assert len(result.hits) == 0

    def test_vol_ratio_low(self):
        """Gap-day volume ratio below gap_min_vol_ratio -> VOL_RATIO_LOW."""
        amounts = np.full(200, 2e8)  # flat amount, ratio=1.0
        kline = _make_kline(n_bars=200, gap_at=_GAP_IDX, amounts=amounts)
        params = {**DEFAULT_PARAMS, "gap_min_vol_ratio": 1.5}
        result = scan_all(
            stocks=[STOCK],
            stock_kline_map={"000001.SZ": kline},
            adj_factor_map={"000001.SZ": _valid_adj()},
            suspension_map={},
            params=params,
        )
        # Gap qualifies, MA60 passes, unfilled, but vol_ratio=1.0 < 1.5
        assert result.non_hit_reasons[NonHitReason.VOL_RATIO_LOW] == 1
        assert len(result.hits) == 0

    def test_vol_ratio_default_fp_noise_no_filter(self):
        """gap_min_vol_ratio within 1e-9 of 1.0 is treated as no filter (isclose)."""
        amounts = np.full(200, 2e8)  # flat → vol_ratio ≈ 1.0
        kline = _make_kline(n_bars=200, gap_at=_GAP_IDX, amounts=amounts)
        # FP noise that would falsely trip `!= 1.0` but is within abs_tol=1e-9
        params = {**DEFAULT_PARAMS, "gap_min_vol_ratio": 1.0 + 1e-15}
        result = scan_all(
            stocks=[STOCK],
            stock_kline_map={"000001.SZ": kline},
            adj_factor_map={"000001.SZ": _valid_adj()},
            suspension_map={},
            params=params,
        )
        assert len(result.hits) == 1
        assert result.non_hit_reasons.get(NonHitReason.VOL_RATIO_LOW, 0) == 0


class TestHits:
    """Stocks that produce a qualifying gap hit."""

    def test_qualifying_gap(self):
        """1.5% gap within lookback, no break, no fill -> 1 regular hit.
        Verify gap_date, gap_pct, gap_low, gap_high.
        """
        kline = _make_kline(n_bars=200, gap_at=_GAP_IDX, gap_pct=1.5)
        result = scan_all(
            stocks=[STOCK],
            stock_kline_map={"000001.SZ": kline},
            adj_factor_map={"000001.SZ": _valid_adj()},
            suspension_map={},
            params=DEFAULT_PARAMS,
        )
        assert len(result.hits) == 1
        hit = result.hits[0]
        assert hit.ts_code == "000001.SZ"
        assert hit.name == "测试"
        assert hit.board == "主板"
        assert hit.gap.gap_pct >= 1.0  # above threshold
        assert hit.gap.gap_date == str(kline.iloc[_GAP_IDX]["trade_date"])
        # gap_low = high[i-1], gap_high = low[i]
        assert hit.gap.gap_low == kline.iloc[_GAP_IDX - 1]["high_qfq"]
        assert hit.gap.gap_high == kline.iloc[_GAP_IDX]["low_qfq"]
        assert hit.gap.is_across_suspension is False
        # Should be within a reasonable range above MA60
        assert hit.pct_from_ma60 > 0
        assert hit.pct_from_gap_high > 0
        assert hit.vol_ratio >= 1.0
        assert isinstance(hit.avg_amount_20d, float) and hit.avg_amount_20d > 0

    def test_gap_day_is_latest(self):
        """Gap on the last bar -> vacuously unfilled -> hit."""
        kline = _make_kline(n_bars=200, gap_at=199)
        result = scan_all(
            stocks=[STOCK],
            stock_kline_map={"000001.SZ": kline},
            adj_factor_map={"000001.SZ": _valid_adj()},
            suspension_map={},
            params=DEFAULT_PARAMS,
        )
        assert len(result.hits) == 1
        hit = result.hits[0]
        assert hit.gap.gap_date == str(kline.iloc[199]["trade_date"])
        # current_price should be the last close
        assert hit.current_price == kline.iloc[199]["close_qfq"]

    def test_tolerance_rule_older_gap_hits(self):
        """Two gaps: newer (idx 160) filled, older (idx 80) valid.
        Tolerance rule falls back to the older gap -> hit on idx 80.
        """
        kline = _make_two_gap_kline(
            n_bars=200,
            gaps=[(80, 1.5, False), (160, 1.5, True)],
        )
        # Increase lookback so both gaps are in the search window:
        # start = max(1, 200-150) = 50, both idx 80 and 160 are in [50, 200)
        params = {**DEFAULT_PARAMS, "gap_lookback": 150}
        result = scan_all(
            stocks=[STOCK],
            stock_kline_map={"000001.SZ": kline},
            adj_factor_map={"000001.SZ": _valid_adj()},
            suspension_map={},
            params=params,
        )
        assert len(result.hits) == 1
        hit = result.hits[0]
        # Hit should be on the older gap (idx 80) — newer one was filled
        assert hit.gap.gap_date == str(kline.iloc[80]["trade_date"])
        assert hit.gap.gap_pct >= 1.0

    def test_tolerance_vol_ratio_falls_back_to_older(self):
        """Newer gap vol_ratio low; older gap ok -> hit older gap."""
        amounts = np.full(200, 2e8)
        amounts[160] = 2e8  # ratio ~1.0 vs 20d avg
        amounts[80] = 4e8  # ratio ~2.0
        kline = _make_two_gap_kline(
            n_bars=200,
            gaps=[(80, 1.5, False), (160, 1.5, False)],
            amounts=amounts,
        )
        params = {**DEFAULT_PARAMS, "gap_lookback": 150, "gap_min_vol_ratio": 1.5}
        result = scan_all(
            stocks=[STOCK],
            stock_kline_map={"000001.SZ": kline},
            adj_factor_map={"000001.SZ": _valid_adj()},
            suspension_map={},
            params=params,
        )
        assert len(result.hits) == 1
        assert result.hits[0].gap.gap_date == str(kline.iloc[80]["trade_date"])

    def test_short_history_vacuous_ma60(self):
        """Gap at index 5 with only 80 bars total: MA60 is None for bars
        5–58, so _check_ma60_streak skips 54 of 59 remaining bars.
        The gap hits despite almost no real MA60 coverage, demonstrating
        why the data window must provide ≥ gap_lookback + 59 bars
        (Fix #1: start_date derived from gap_lookback).
        """
        kline = _make_kline(n_bars=80, gap_at=5)
        # Extend lookback to include index 5: start = max(1, 80-80) = 1
        params = {**DEFAULT_PARAMS, "min_list_days": 60, "gap_lookback": 80}
        result = scan_all(
            stocks=[STOCK],
            stock_kline_map={"000001.SZ": kline},
            adj_factor_map={"000001.SZ": _valid_adj()},
            suspension_map={},
            params=params,
        )
        # Gap passes — but only ~5 post-gap bars had a real MA60 check
        assert len(result.hits) == 1
        assert result.hits[0].gap.gap_pct >= 1.0


class TestAcrossSuspension:
    """Gap detected across a suspension period."""

    def test_unfilled_across_suspension_side_list(self):
        """Unfilled gap after suspension day -> across_suspension_hits, not main hits."""
        kline = _make_kline(n_bars=200, gap_at=_GAP_IDX, fill_gap=False)
        trade_cal = kline["trade_date"].tolist()
        gap_date = trade_cal[_GAP_IDX]
        prev_date = trade_cal[_GAP_IDX - 1]
        suspension_map = {"000001.SZ": [prev_date]}

        result = scan_all(
            stocks=[STOCK],
            stock_kline_map={"000001.SZ": kline},
            adj_factor_map={"000001.SZ": _valid_adj()},
            suspension_map=suspension_map,
            params=DEFAULT_PARAMS,
            trade_cal=trade_cal,
        )
        assert len(result.across_suspension_hits) == 1
        assert len(result.hits) == 0
        susp_hit = result.across_suspension_hits[0]
        assert susp_hit.gap.gap_date == gap_date
        assert susp_hit.gap.is_across_suspension is True

    def test_filled_across_suspension_falls_back(self):
        """Filled newer across-suspension gap must not block older valid hit."""
        kline = _make_two_gap_kline(
            n_bars=200,
            gaps=[(80, 1.5, False), (160, 1.5, True)],
        )
        trade_cal = kline["trade_date"].tolist()
        prev_at_160 = trade_cal[159]
        suspension_map = {"000001.SZ": [prev_at_160]}
        params = {**DEFAULT_PARAMS, "gap_lookback": 150}

        result = scan_all(
            stocks=[STOCK],
            stock_kline_map={"000001.SZ": kline},
            adj_factor_map={"000001.SZ": _valid_adj()},
            suspension_map=suspension_map,
            params=params,
            trade_cal=trade_cal,
        )
        assert len(result.across_suspension_hits) == 0
        assert len(result.hits) == 1
        assert result.hits[0].gap.gap_date == str(kline.iloc[80]["trade_date"])

    def test_across_suspension_respects_vol_ratio(self):
        """Across-suspension candidate must still pass vol_ratio filter."""
        amounts = np.full(200, 2e8)  # ratio=1.0
        kline = _make_kline(n_bars=200, gap_at=_GAP_IDX, amounts=amounts)
        trade_cal = kline["trade_date"].tolist()
        prev_date = trade_cal[_GAP_IDX - 1]
        params = {**DEFAULT_PARAMS, "gap_min_vol_ratio": 1.5}

        result = scan_all(
            stocks=[STOCK],
            stock_kline_map={"000001.SZ": kline},
            adj_factor_map={"000001.SZ": _valid_adj()},
            suspension_map={"000001.SZ": [prev_date]},
            params=params,
            trade_cal=trade_cal,
        )
        assert len(result.hits) == 0
        assert len(result.across_suspension_hits) == 0
        assert result.non_hit_reasons[NonHitReason.VOL_RATIO_LOW] == 1


class TestEdgeCases:
    """Near-zero MA60 and invalid vol_ratio baselines."""

    def test_ma60_near_zero_pct_from_ma60(self):
        """MA60 within 1e-9 of zero → pct_from_ma60=0.0, not huge division."""
        gap = GapInfo(
            gap_date="20250101",
            gap_pct=1.5,
            gap_low=10.0,
            gap_high=10.15,
        )
        n = 200
        closes = [12.0] * n
        ma60_list: list[float | None] = [None] * 59 + [12.0] * (n - 60)
        ma60_list[-1] = 1e-12
        amounts = [5e8] * n

        hit = _build_scan_hit(
            STOCK, gap, _GAP_IDX, closes, ma60_list, amounts, 5e8, 1.0,
        )
        assert hit.pct_from_ma60 == 0.0
        assert hit.ma60 == 1e-12

    def test_negative_local_avg_vol_ratio_zero(self):
        """Negative gap-local average → vol_ratio=0.0, fails vol filter."""
        amounts = np.full(200, 2e8)
        # Pre-gap window for idx 150 is [130, 150) — make average negative
        amounts[130:150] = -1e8
        kline = _make_kline(n_bars=200, gap_at=_GAP_IDX, amounts=amounts)
        params = {**DEFAULT_PARAMS, "gap_min_vol_ratio": 1.5}
        result = scan_all(
            stocks=[STOCK],
            stock_kline_map={"000001.SZ": kline},
            adj_factor_map={"000001.SZ": _valid_adj()},
            suspension_map={},
            params=params,
        )
        assert result.non_hit_reasons[NonHitReason.VOL_RATIO_LOW] == 1
        assert len(result.hits) == 0

    def test_near_zero_local_avg_vol_ratio_zero(self):
        """Gap-local average near zero → vol_ratio=0.0, fails vol filter."""
        amounts = np.full(200, 2e8)
        amounts[130:150] = 1e-15
        kline = _make_kline(n_bars=200, gap_at=_GAP_IDX, amounts=amounts)
        params = {**DEFAULT_PARAMS, "gap_min_vol_ratio": 1.5}
        result = scan_all(
            stocks=[STOCK],
            stock_kline_map={"000001.SZ": kline},
            adj_factor_map={"000001.SZ": _valid_adj()},
            suspension_map={},
            params=params,
        )
        assert result.non_hit_reasons[NonHitReason.VOL_RATIO_LOW] == 1
        assert len(result.hits) == 0


class TestScanResultStructure:
    """Structural invariants of the ScanResult object."""

    def test_result_counters(self):
        """Combined counts match total_in_universe."""
        kline = _make_kline(n_bars=200, gap_at=_GAP_IDX)
        result = scan_all(
            stocks=[STOCK],
            stock_kline_map={"000001.SZ": kline},
            adj_factor_map={"000001.SZ": _valid_adj()},
            suspension_map={},
            params=DEFAULT_PARAMS,
        )
        total_excluded = sum(result.exclude_reasons.values())
        total_non_hit = sum(result.non_hit_reasons.values())
        total_hits = len(result.hits) + len(result.across_suspension_hits)
        assert result.total_in_universe == 1
        assert result.total_with_kline == 1
        assert result.total_scanned == result.total_in_universe - total_excluded - result.total_fetch_errors
        assert total_hits + total_non_hit == result.total_scanned
        assert result.params == DEFAULT_PARAMS

    def test_total_with_kline_includes_liquidity_exclude(self):
        """Usable kline counted even when later excluded for low liquidity."""
        amounts = np.full(200, 1e5)
        kline = _make_kline(n_bars=200, amounts=amounts)
        result = scan_all(
            stocks=[STOCK],
            stock_kline_map={"000001.SZ": kline},
            adj_factor_map={"000001.SZ": _valid_adj()},
            suspension_map={},
            params=DEFAULT_PARAMS,
        )
        assert result.total_with_kline == 1
        assert result.exclude_reasons[ExcludeReason.LOW_LIQUIDITY] == 1
        assert result.total_scanned == 0

    def test_mixed_suspension_detection_covers_all_stocks(self):
        """Fix #2 regression: suspension detection must cover all stocks in
        stock_kline_map, not just those present in daily_raw (baostock
        partial-cache-hit scenario).  Stock A is correctly flagged as
        across-suspension even when only stock B would appear in daily_raw.
        """
        n_bars = 200
        kline_a = _make_kline(n_bars=n_bars, gap_at=_GAP_IDX)
        kline_b = _make_kline(n_bars=n_bars, gap_at=_GAP_IDX)
        stock_b = MockStock("000002.SZ")

        trade_cal = kline_a["trade_date"].tolist()
        gap_date = trade_cal[_GAP_IDX]
        prev_date = trade_cal[_GAP_IDX - 1]

        # Stock A has a real suspension before the gap → across-suspension
        # Stock B has no suspension → regular hit
        suspension_map = {"000001.SZ": [prev_date]}

        result = scan_all(
            stocks=[STOCK, stock_b],
            stock_kline_map={"000001.SZ": kline_a, "000002.SZ": kline_b},
            adj_factor_map={
                "000001.SZ": _valid_adj(),
                "000002.SZ": _valid_adj(),
            },
            suspension_map=suspension_map,
            params=DEFAULT_PARAMS,
            trade_cal=trade_cal,
        )
        # Stock A: across-suspension (had suspension before gap)
        # Stock B: regular hit (no suspension)
        assert len(result.across_suspension_hits) == 1
        assert result.across_suspension_hits[0].ts_code == "000001.SZ"
        assert len(result.hits) == 1
        assert result.hits[0].ts_code == "000002.SZ"
        assert result.total_in_universe == 2
        assert result.total_with_kline == 2
