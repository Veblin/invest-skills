"""Tests for gap_scanner.py -- all synthetic data, no network calls.

Covers gap scanning logic: gap detection, tolerance rule (newest-first),
MA60 streak, gap unfilled, vol ratio, across-suspension detection,
and exclusion reasons.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest

from gap_scanner import (
    GapInfo,
    is_cached_bar_settled,
    _build_scan_hit,
    _check_ma60_streak,
    _check_unfilled,
    _find_candidate_gaps,
    _ma60_streak_stats,
    _normalize_date,
    _resolve_after_close,
    scan_all,
)
from lib.technical import sma  # noqa: E402（与引擎同源，用于覆盖度独立复算）
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


def _now_at(ymd: str, hhmm: str = "1030") -> datetime:
    """构造上海时区的固定时点，供 after_close 的日期感知判定注入。

    合成 K 线的日期起点是 2025-01-01（远早于墙钟「今天」），注入固定时点
    才能确定性地复现「盘中未完成 / 盘前已完成」两种情形。
    """
    return datetime.strptime(f"{ymd}{hhmm}", "%Y%m%d%H%M").replace(
        tzinfo=ZoneInfo("Asia/Shanghai"))


def _last_bar(kline: pd.DataFrame) -> str:
    return str(kline.iloc[-1]["trade_date"])


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

    def test_gap_day_is_latest_intraday_unconfirmed(self):
        """最新 bar 尚未完成（当日盘中）→ 两条结论都不下 → GAP_UNCONFIRMED。

        bar 未完成时「未回补」（`_check_unfilled` 对无后续数据的 bar 返回
        False）与「已回补」都不成立，故单列终结桶；若没有该分支，缺口会被
        误归入「缺口回补」（旧注释称「不判则恒命中」方向相反，已改正）。
        """
        kline = _make_kline(n_bars=200, gap_at=199)
        result = scan_all(
            stocks=[STOCK],
            stock_kline_map={"000001.SZ": kline},
            adj_factor_map={"000001.SZ": _valid_adj()},
            suspension_map={},
            params=DEFAULT_PARAMS,
            now=_now_at(_last_bar(kline), "1030"),  # 当日盘中
        )
        assert len(result.hits) == 0
        assert result.non_hit_reasons[NonHitReason.GAP_UNCONFIRMED] == 1
        # 未完成 ≠ 已回补：不得混入 GAP_FILLED
        assert result.non_hit_reasons.get(NonHitReason.GAP_FILLED, 0) == 0

    def test_check_unfilled_last_bar_returns_false(self):
        """_check_unfilled 对最新 bar（无后续数据）返回 False，不做空洞真值。"""
        assert _check_unfilled([10.0, 12.0], gap_idx=1, gap_high=11.0) is False

    def test_check_unfilled_after_close_confirms_latest_gap(self):
        """review #1：收盘后最新 bar 缺口未回补（lows[gap_idx] > gap_low）。

        回归 38a7e1e：此前比较 lows[gap_idx] > gap_high（gap_high 即该 bar
        自身 low）恒 False——收盘后扫描把最新 bar 缺口误判为已回补。
        """
        assert _check_unfilled([10.0, 12.0], gap_idx=1, gap_high=12.0,
                               after_close=True, gap_low=11.0) is True

    def test_gap_day_is_latest_after_close_hits(self):
        """review #1：收盘后（after_close=True）最新 bar 缺口确认命中，
        而非误判 GAP_FILLED（38a7e1e 回归前恒空命中名单）。"""
        kline = _make_kline(n_bars=200, gap_at=199)
        result = scan_all(
            stocks=[STOCK],
            stock_kline_map={"000001.SZ": kline},
            adj_factor_map={"000001.SZ": _valid_adj()},
            suspension_map={},
            params=DEFAULT_PARAMS,
            after_close=True,
        )
        assert len(result.hits) == 1
        assert result.hits[0].gap.gap_date == str(kline.iloc[199]["trade_date"])
        assert result.non_hit_reasons.get(NonHitReason.GAP_FILLED, 0) == 0

    def test_ma60_streak_yesterday_gap_passes(self):
        """review #4：缺口在倒数第二根 bar（total_count==2）时不得被
        绝对下限 3 恒拒（MA60 全有效、close≥MA60 应通过）。"""
        closes = [10.0 + i for i in range(60)] + [70.0, 72.0]
        ma60 = [None] * 59 + [50.0] * 3  # 59-61 有效
        assert _check_ma60_streak(closes, ma60, gap_idx=len(closes) - 2) is True

    def test_latest_bar_gap_falls_back_to_older_confirmed(self):
        """最新 bar（盘中未完成）跳空待收盘确认，容错规则回退到更早的已确认缺口。"""
        kline = _make_two_gap_kline(
            n_bars=200,
            gaps=[(80, 1.5, False), (199, 1.5, False)],
        )
        params = {**DEFAULT_PARAMS, "gap_lookback": 150}
        result = scan_all(
            stocks=[STOCK],
            stock_kline_map={"000001.SZ": kline},
            adj_factor_map={"000001.SZ": _valid_adj()},
            suspension_map={},
            params=params,
            now=_now_at(_last_bar(kline), "1030"),  # 当日盘中
        )
        assert len(result.hits) == 1
        assert result.hits[0].gap.gap_date == str(kline.iloc[80]["trade_date"])

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

    def test_gap_high_near_zero_pct_from_gap_high(self):
        """gap_high within 1e-9 of zero → pct_from_gap_high=0.0, not huge division."""
        gap = GapInfo(
            gap_date="20250101",
            gap_pct=1.5,
            gap_low=10.0,
            gap_high=1e-12,
        )
        n = 200
        closes = [12.0] * n
        ma60_list: list[float | None] = [None] * 59 + [12.0] * (n - 60)
        amounts = [5e8] * n

        hit = _build_scan_hit(
            STOCK, gap, _GAP_IDX, closes, ma60_list, amounts, 5e8, 1.0,
        )
        assert hit.pct_from_gap_high == 0.0
        assert hit.gap.gap_high == 1e-12

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


class TestZeroPriceBarGuard:
    """F14: 零价 bar 不得触发除零，且单股异常不终止全池扫描。"""

    def test_zero_prior_high_skips_candidate(self):
        kline = _make_kline(n_bars=100)
        # bar50 收盘为零价毛刺 → bar51 low(5.0) > bar50 high(0.0) 修复前触发除零
        kline.loc[kline.index[50], "high_qfq"] = 0.0
        kline.loc[kline.index[51], "low_qfq"] = 5.0
        all_c, qualified = _find_candidate_gaps(kline, 60, 1.0)
        assert all_c == []  # 零高 bar 不作为缺口前一日

    def test_tiny_positive_high_spike_filtered(self):
        """0.001 级毛刺 high 后接正常 bar → 天文 gap 被 _MAX_GAP_PCT 过滤。"""
        kline = _make_kline(n_bars=100)
        kline.loc[kline.index[50], "high_qfq"] = 0.001
        kline.loc[kline.index[51], "low_qfq"] = 5.0
        all_c, qualified = _find_candidate_gaps(kline, 60, 1.0)
        # gap_pct ≈ +499,900% 超上限 → 不作为缺口候选
        assert all_c == []

    def test_relisting_giant_gap_not_filtered(self):
        """复牌首日真实大跳空（>50%）不被 _MAX_GAP_PCT 拦截（盐湖 +347% 场景）。

        F1: 前一日在停牌表内 → 放行大缺口并保留跨停牌标注机会；
        同场景无停牌信息时毛刺拦截仍生效。
        """
        dates = [d.strftime("%Y%m%d")
                 for d in pd.date_range("2025-01-01", periods=100, freq="B")]
        kline = _make_kline(n_bars=100)
        # bar50 停牌残留低基准，bar51 复牌日 low 跳空 +347%
        kline.loc[kline.index[50], "high_qfq"] = 1.0
        kline.loc[kline.index[51], "low_qfq"] = 4.47
        all_c, qualified = _find_candidate_gaps(
            kline, 60, 1.0,
            suspensions=[dates[50]], trade_cal=dates,
        )
        assert len(all_c) == 1
        assert all_c[0][1].gap_pct > 50.0
        assert all_c[0][1].gap_date == dates[51]
        # 无停牌信息 → 仍按毛刺拦截
        all_c2, _ = _find_candidate_gaps(kline, 60, 1.0)
        assert all_c2 == []

    def test_scan_all_isolates_stock_exception(self, monkeypatch):
        kline = _make_kline(n_bars=200)

        def boom(*a, **k):
            raise ValueError("boom")

        monkeypatch.setattr("gap_scanner._scan_stock", boom)
        result = scan_all(
            stocks=[STOCK],
            stock_kline_map={"000001.SZ": kline},
            adj_factor_map={"000001.SZ": _valid_adj()},
            suspension_map={},
            params=DEFAULT_PARAMS,
        )
        assert result.exclude_reasons[ExcludeReason.FETCH_ERROR] == 1
        assert result.total_fetch_errors == 1
        assert len(result.hits) == 0


class TestMaxGapFailOpenAndBoard:
    """B1/B2: 估计日历 fail-open + 按板块阈值（/code-review max）。"""

    def test_estimated_calendar_fail_open_keeps_relisting_gap(self):
        """suspension_map={}（估计日历）→ 347% 复牌跳空不再被静默丢弃。"""
        kline = _make_kline(n_bars=200, gap_at=_GAP_IDX, gap_pct=347, fill_gap=False)
        trade_cal = kline["trade_date"].tolist()
        result = scan_all(
            stocks=[STOCK],
            stock_kline_map={"000001.SZ": kline},
            adj_factor_map={"000001.SZ": _valid_adj()},
            suspension_map={},  # 估计日历路径：无停牌信息 → fail-open
            params=DEFAULT_PARAMS,
            trade_cal=trade_cal,
        )
        assert len(result.hits) == 1
        assert len(result.across_suspension_hits) == 0

    def test_real_calendar_still_filters_giant_gap(self):
        """停牌数据可用 + 非跨停牌 → 347% 缺口仍按毛刺过滤（fail-closed）。"""
        kline = _make_kline(n_bars=200, gap_at=_GAP_IDX, gap_pct=347, fill_gap=False)
        trade_cal = kline["trade_date"].tolist()
        result = scan_all(
            stocks=[STOCK],
            stock_kline_map={"000001.SZ": kline},
            adj_factor_map={"000001.SZ": _valid_adj()},
            suspension_map={"000001.SZ": []},  # 数据可用（该股无停牌记录）
            params=DEFAULT_PARAMS,
            trade_cal=trade_cal,
        )
        assert len(result.hits) == 0
        assert len(result.across_suspension_hits) == 0

    def test_max_gap_pct_for_code(self):
        from gap_scanner import _max_gap_pct_for_code

        assert _max_gap_pct_for_code("000001.SZ") == 30.0
        assert _max_gap_pct_for_code("300001.SZ") == 60.0
        assert _max_gap_pct_for_code("301001.SZ") == 60.0
        assert _max_gap_pct_for_code("688001.SH") == 60.0
        assert _max_gap_pct_for_code("920001.BJ") == 95.0
        assert _max_gap_pct_for_code("832001.BJ") == 95.0
        assert _max_gap_pct_for_code("430001.BJ") == 95.0
        assert _max_gap_pct_for_code("") == 30.0

    def test_cyb_gap_above_main_cap_kept_by_board_threshold(self):
        """创业板 55% 缺口：主板阈值（30）下过滤、板块阈值（60）下保留。"""
        kline = _make_kline(n_bars=50, gap_at=30, gap_pct=55, fill_gap=False)
        dates = kline["trade_date"].tolist()
        all_c, _ = _find_candidate_gaps(
            kline, 40, 1.0, suspensions=[], trade_cal=dates, ts_code="000001.SZ")
        assert all_c == []
        all_c, _ = _find_candidate_gaps(
            kline, 40, 1.0, suspensions=[], trade_cal=dates, ts_code="300001.SZ")
        assert len(all_c) == 1
        assert all_c[0][1].gap_pct == pytest.approx(55.0)


class TestAfterCloseDateAware:
    """V31-59：`after_close` 按**数据日期**判定，不按墙钟小时。

    实测（2026-09-23 复现）：同一份缓存数据（最新 bar = 2026-09-18 周五，已完成）
    周六 23:46 扫描得 10 命中，周一 06:40 扫描得 9 命中 + 1 条「最新bar待收盘确认」
    —— 墙钟 `hour >= 15` 把已完成的 bar 当成未收盘，丢掉可判定的命中。
    """

    def test_resolve_premarket_prev_bar_complete(self):
        """盘前：最新 bar 是上一交易日（更早日期）→ 已完成。"""
        assert _resolve_after_close("20260918", _now_at("20260921", "0640")) is True

    def test_resolve_weekend_prev_bar_complete(self):
        """周末：最新 bar 是周五 → 已完成。"""
        assert _resolve_after_close("20260918", _now_at("20260919", "2346")) is True

    def test_resolve_same_day_intraday_incomplete(self):
        """当日盘中：最新 bar 就是今日且未收盘 → 未完成。"""
        assert _resolve_after_close("20260923", _now_at("20260923", "1030")) is False

    def test_resolve_same_day_after_close_complete(self):
        """当日 15:00 后：日线已发布 → 已完成。"""
        assert _resolve_after_close("20260923", _now_at("20260923", "1530")) is True
        assert _resolve_after_close("20260923", _now_at("20260923", "1500")) is True

    def test_resolve_unparsable_date_is_conservative(self):
        """日期不可解析 → 保守判未完成（不猜成已完成）。"""
        assert _resolve_after_close("", _now_at("20260923", "1530")) is False
        assert _resolve_after_close("2026-13-99x", _now_at("20260923", "1530")) is False

    def test_resolve_accepts_dashed_date(self):
        """兼容带连字符的日期串（源差异）。"""
        assert _resolve_after_close("2026-09-18", _now_at("20260921", "0640")) is True

    def test_premarket_latest_bar_gap_confirms_hit(self):
        """盘前（周末/节假日）最新 bar 已完成 → 缺口确认命中（V31-59 回归）。

        修复前：hour(6) < 15 → after_close=False → 归入 GAP_UNCONFIRMED，丢失命中。
        """
        kline = _make_kline(n_bars=200, gap_at=199)
        result = scan_all(
            stocks=[STOCK],
            stock_kline_map={"000001.SZ": kline},
            adj_factor_map={"000001.SZ": _valid_adj()},
            suspension_map={},
            params=DEFAULT_PARAMS,
            # 周一 06:40 运行，最新 bar 是上周五（已完成）
            now=_now_at("20251013", "0640"),
        )
        assert len(result.hits) == 1
        assert result.hits[0].gap.gap_date == _last_bar(kline)
        assert result.non_hit_reasons.get(NonHitReason.GAP_UNCONFIRMED, 0) == 0

    def test_explicit_after_close_false_still_honored(self):
        """显式传 after_close=False 时不做日期感知（测试/重放用）。"""
        kline = _make_kline(n_bars=200, gap_at=199)
        result = scan_all(
            stocks=[STOCK],
            stock_kline_map={"000001.SZ": kline},
            adj_factor_map={"000001.SZ": _valid_adj()},
            suspension_map={},
            params=DEFAULT_PARAMS,
            after_close=False,
        )
        assert len(result.hits) == 0
        assert result.non_hit_reasons[NonHitReason.GAP_UNCONFIRMED] == 1


class TestHitProvenance:
    """V31-38/41/42：命中携带可复算分母、MA60 覆盖度与数据日（报告据此自证口径）。"""

    def test_vol_ratio_is_recomputable(self):
        """量比 = 缺口日成交额 / 分母（缺口日前至多 20 根 bar 日均额）。"""
        kline = _make_kline(n_bars=200, gap_at=150)
        result = scan_all(
            stocks=[STOCK],
            stock_kline_map={"000001.SZ": kline},
            adj_factor_map={"000001.SZ": _valid_adj()},
            suspension_map={},
            params=DEFAULT_PARAMS,
            now=_now_at(_last_bar(kline), "1030"),
        )
        hit = result.hits[0]
        assert hit.gap_day_amount == pytest.approx(5e8)
        assert hit.vol_ratio_denom == pytest.approx(5e8)
        assert hit.vol_ratio == pytest.approx(hit.gap_day_amount / hit.vol_ratio_denom)

    def test_vol_ratio_denominator_uses_gap_local_window(self):
        """分母是缺口日前 20 根（不是当前尾部）——两窗口取值不同。"""
        amounts = np.full(200, 2e8)
        amounts[130:150] = 4e8          # 缺口前 20 根被放大
        amounts[180:] = 1e8             # 当前尾部相反
        kline = _make_kline(n_bars=200, gap_at=150, amounts=amounts)
        result = scan_all(
            stocks=[STOCK],
            stock_kline_map={"000001.SZ": kline},
            adj_factor_map={"000001.SZ": _valid_adj()},
            suspension_map={},
            params=DEFAULT_PARAMS,
            now=_now_at(_last_bar(kline), "1030"),
        )
        hit = result.hits[0]
        assert hit.vol_ratio_denom == pytest.approx(4e8)      # 缺口前 20 根
        assert hit.avg_amount_20d == pytest.approx(1e8)       # 当前尾部 20 根
        assert hit.vol_ratio_denom != hit.avg_amount_20d

    def test_gap_local_window_capped_at_20_bars(self):
        """缺口靠前时分母只取 `min(20, gap_idx)` 根（至多 20）。"""
        amounts = np.full(200, 1e8)
        amounts[1] = 3e8
        amounts[5:25] = 2e8
        kline = _make_kline(n_bars=130, gap_at=25, amounts=amounts[:130])
        # lookback 放大到 120 才能把 idx=25 纳入搜索窗口。
        # 缺口 idx=25 → 分母窗口 = amounts[5:25]（20 根，均值 2e8）
        params = {**DEFAULT_PARAMS, "gap_lookback": 120}
        result = scan_all(
            stocks=[STOCK],
            stock_kline_map={"000001.SZ": kline},
            adj_factor_map={"000001.SZ": _valid_adj()},
            suspension_map={},
            params=params,
            now=_now_at(_last_bar(kline), "1030"),
        )
        hit = result.hits[0]
        assert hit.vol_ratio_denom == pytest.approx(2e8)

    def test_ma60_coverage_fields_present(self):
        """MA60 覆盖度随命中输出（报告披露判定强度）。"""
        kline = _make_kline(n_bars=200, gap_at=150)
        result = scan_all(
            stocks=[STOCK],
            stock_kline_map={"000001.SZ": kline},
            adj_factor_map={"000001.SZ": _valid_adj()},
            suspension_map={},
            params=DEFAULT_PARAMS,
            now=_now_at(_last_bar(kline), "1030"),
        )
        hit = result.hits[0]
        assert hit.ma60_total_bars == 200 - 150
        assert hit.ma60_valid_bars == 200 - 150  # 数据充足：全部 bar 有 MA60
        # 与引擎内部复算一致（覆盖度字段不是独立心算，而是同一判定的输出）
        passed, valid, total = _ma60_streak_stats(
            kline["close_qfq"].tolist(),
            sma(kline["close_qfq"].tolist(), 60),
            150,
        )
        assert passed is True
        assert (valid, total) == (hit.ma60_valid_bars, hit.ma60_total_bars)

    def test_ma60_coverage_partial_history_included(self):
        """短历史但覆盖度达标（74 根、缺口 idx=14）→ 命中且覆盖度 15/60。

        74 根标的的 MA60 仅 bar 59 起有效：缺口起 60 根 bar 中只有 15 根
        有 MA60 值，恰好满足门槛 `max(ceil(60×0.25), 3) = 15`。
        """
        kline = _make_kline(n_bars=74, gap_at=14, gap_pct=1.5)
        params = {**DEFAULT_PARAMS, "min_list_days": 60}
        result = scan_all(
            stocks=[STOCK],
            stock_kline_map={"000001.SZ": kline},
            adj_factor_map={"000001.SZ": _valid_adj()},
            suspension_map={},
            params=params,
            now=_now_at(_last_bar(kline), "1030"),
        )
        hit = result.hits[0]
        assert (hit.ma60_valid_bars, hit.ma60_total_bars) == (15, 60)

    def test_ma60_coverage_below_threshold_is_ma60_broken(self):
        """覆盖度不足（64 根、缺口 idx=10 → 5/54）→ 归入 MA60破，不报命中。

        这是**有意收紧**：覆盖度不足时 MA60 判定无强度含义，宁可归入 MA60破
        也不放行一个没有 MA60 支撑的命中（报告会披露覆盖度，见 SKILL.md）。
        """
        kline = _make_kline(n_bars=64, gap_at=10, gap_pct=1.5)
        params = {**DEFAULT_PARAMS, "min_list_days": 60}
        result = scan_all(
            stocks=[STOCK],
            stock_kline_map={"000001.SZ": kline},
            adj_factor_map={"000001.SZ": _valid_adj()},
            suspension_map={},
            params=params,
            now=_now_at(_last_bar(kline), "1030"),
        )
        assert len(result.hits) == 0
        assert result.non_hit_reasons[NonHitReason.MA60_BROKEN] == 1
        _, valid, total = _ma60_streak_stats(
            kline["close_qfq"].tolist(),
            sma(kline["close_qfq"].tolist(), 60),
            10,
        )
        assert (valid, total) == (5, 54)

    def test_data_as_of_and_hit_data_date(self):
        """`data_as_of` = 全池最新 bar；命中携带各自数据日。"""
        kline = _make_kline(n_bars=200, gap_at=150)
        result = scan_all(
            stocks=[STOCK],
            stock_kline_map={"000001.SZ": kline},
            adj_factor_map={"000001.SZ": _valid_adj()},
            suspension_map={},
            params=DEFAULT_PARAMS,
            now=_now_at(_last_bar(kline), "1030"),
        )
        assert result.data_as_of == _last_bar(kline)
        # 归一化为 yyyymmdd（不含连字符）
        assert "-" not in result.data_as_of
        assert result.hits[0].data_date == _last_bar(kline)

    def test_data_as_of_picks_latest_across_stocks(self):
        """多标的时取最新 bar（停牌股更早不得拉低数据截止日）。"""
        kline_a = _make_kline(n_bars=200, gap_at=150)
        kline_b = _make_kline(n_bars=180, gap_at=140)  # 更早结束
        stock_b = MockStock("000002.SZ")
        result = scan_all(
            stocks=[STOCK, stock_b],
            stock_kline_map={"000001.SZ": kline_a, "000002.SZ": kline_b},
            adj_factor_map={"000001.SZ": _valid_adj(), "000002.SZ": _valid_adj()},
            suspension_map={},
            params=DEFAULT_PARAMS,
            now=_now_at(_last_bar(kline_a), "1030"),
        )
        assert result.data_as_of == _last_bar(kline_a)
        assert _last_bar(kline_b) < _last_bar(kline_a)


class TestCachedBarSettled:
    """评审续（2026-09-23）：缓存里的**盘中快照**不得在隔日被当成已完成 bar。

    实测缺陷：盘中 10:30 写入的缓存（末根 bar = 当日）在次日 09:00 被复用，
    日期比较「昨日 < 今日」成立 → 用未走完的低点断言「缺口未回补」，凭空产出命中；
    而当日真实收盘该缺口根本不成立。判据因此是「写入时刻晚于**该 bar 自身交易日**的收盘」。
    """

    def test_history_bar_written_after_its_close_is_settled(self):
        """昨日 bar + 缓存在该 bar 收盘后写入 → 定稿。"""
        assert is_cached_bar_settled(
            "20260923", _now_at("20260923", "1600").timestamp(),
            _now_at("20260924", "0900")) is True
        assert is_cached_bar_settled(
            "20260923", _now_at("20260923", "1500").timestamp(),
            _now_at("20260924", "0900")) is True

    def test_history_bar_written_intraday_is_not_settled(self):
        """昨日 bar 但缓存在该 bar **盘中**写入 → 未定稿（本缺陷的核心场景）。"""
        assert is_cached_bar_settled(
            "20260923", _now_at("20260923", "1030").timestamp(),
            _now_at("20260924", "0900")) is False

    def test_today_bar_written_intraday_is_not_settled(self):
        assert is_cached_bar_settled(
            "20260923", _now_at("20260923", "1030").timestamp(),
            _now_at("20260923", "1030")) is False

    def test_today_bar_written_after_close_is_settled(self):
        assert is_cached_bar_settled(
            "20260923", _now_at("20260923", "1530").timestamp(),
            _now_at("20260923", "2000")) is True

    def test_refetch_after_close_next_day_is_settled(self):
        """次日盘前重拉得到的「昨日 bar」→ 写入时刻晚于该 bar 收盘 → 定稿。"""
        assert is_cached_bar_settled(
            "20260923", _now_at("20260924", "0900").timestamp(),
            _now_at("20260924", "0902")) is True

    def test_missing_mtime_is_not_settled(self):
        """写入时刻不可得（stat 失败）→ 保守判未定稿。"""
        assert is_cached_bar_settled("20260923", None, _now_at("20260924", "0900")) is False

    def test_future_or_malformed_date_is_not_settled(self):
        now = _now_at("20260923", "2000")
        assert is_cached_bar_settled("20260924", 0.0, now) is False  # 未来 bar
        assert is_cached_bar_settled("", 0.0, now) is False
        assert is_cached_bar_settled("not-a-date", 0.0, now) is False

    def test_dashed_date_accepted(self):
        assert is_cached_bar_settled(
            "2026-09-22", _now_at("20260922", "1600").timestamp(),
            _now_at("20260923", "0900")) is True


class TestCachedBarSettledNeverRaises:
    """评审续三：缓存校验谓词**永不抛异常**——调用点在逐股异常隔离之外。

    8 位但非法的日历日期（`20260230`/`20260000`）此前会在此抛 ValueError，
    一路冒泡中止整次扫描（实测 `_run_scan` 直接抛错），而函数本意是「不可信 →
    按未定稿处理 → 该股重拉」。这里参数化钉住「任何输入都返回 bool」。
    """

    @pytest.mark.parametrize("bad_date", [
        "20260230",   # 2 月无 30 日（ValueError: day is out of range）
        "20260000",   # 月/日为 00
        "20261301",   # 月 13（字符串比较已拦，仍须不抛）
        "2026-02-30",  # 带连字符的非法日期（归一化后同上）
        "202609023",  # 9 位
        "2026",       # 位数不足
        "",           # 空
        "not-a-date",  # 非数字
        "20260931",   # 9 月无 31 日
    ])
    def test_malformed_dates_return_false_without_raising(self, bad_date):
        now = _now_at("20260924", "0900")
        assert is_cached_bar_settled(bad_date, now.timestamp(), now) is False

    def test_out_of_range_mtime_does_not_raise(self):
        """时间戳越界同样归入未定稿，不抛出。"""
        now = _now_at("20260924", "0900")
        assert is_cached_bar_settled("20260922", 1e30, now) is False


class TestNormalizeDateNoTruncation:
    """`_normalize_date` 不得把坏数据**截断**成看似合理的日期。"""

    @pytest.mark.parametrize("value,expected", [
        ("20260923", "20260923"),
        ("2026-09-23", "20260923"),
        ("2026-09-23 00:00:00", "20260923"),   # pandas Timestamp 形式
        ("202609023", ""),                      # 9 位 → 不截断
        ("2026092345", ""),                     # 10 位数字 → 不截断
        ("2026092", ""),                        # 位数不足
        ("", ""),
        ("not-a-date", ""),
    ])
    def test_normalize(self, value, expected):
        assert _normalize_date(value) == expected
