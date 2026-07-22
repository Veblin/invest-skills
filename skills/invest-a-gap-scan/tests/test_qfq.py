"""Tests for qfq (前复权) price adjustment — pure function tests, no network."""

from __future__ import annotations

import pandas as pd
import pytest

from qfq import PRICE_COLS, apply_qfq


# ---- Helpers ----

def _make_daily(dates, open_prices=None, high_prices=None,
                low_prices=None, close_prices=None, amounts=None):
    """Create a daily DataFrame with OHLC + amount columns."""
    n = len(dates)
    data = {
        "trade_date": dates,
        "open": open_prices or [float(i) for i in range(1, n + 1)],
        "high": high_prices or [float(i) + 0.5 for i in range(1, n + 1)],
        "low": low_prices or [float(i) - 0.3 for i in range(1, n + 1)],
        "close": close_prices or [float(i) * 10 for i in range(1, n + 1)],
        "amount": amounts or [1e8 + i * 1e7 for i in range(n)],
    }
    return pd.DataFrame(data)


def _make_adj(dates, factors):
    """Create an adj_factor DataFrame with trade_date and adj_factor columns."""
    return pd.DataFrame({
        "trade_date": dates,
        "adj_factor": factors,
    })


# ---- Basic adjustment ----

class TestApplyQfqBasic:

    def test_basic_adjustment(self):
        """Verify qfq_price = raw_price * adj_factor / latest_adj_factor."""
        daily = _make_daily(
            dates=["20260710", "20260711", "20260712"],
            close_prices=[10.0, 12.0, 15.0],
        )
        adj = _make_adj(
            dates=["20260710", "20260711", "20260712"],
            factors=[1.2, 1.1, 1.0],
        )
        result = apply_qfq(daily, adj)

        assert result is not None
        # latest bar: raw close * 1.0 / 1.0 = raw close
        latest_close_qfq = result.loc[result["trade_date"] == "20260712", "close_qfq"].iloc[0]
        assert latest_close_qfq == 15.0

        # 20260710: 10.0 * 1.2 / 1.0 = 12.0
        assert result.loc[result["trade_date"] == "20260710", "close_qfq"].iloc[0] == pytest.approx(12.0)

        # 20260711: 12.0 * 1.1 / 1.0 = 13.2
        assert result.loc[result["trade_date"] == "20260711", "close_qfq"].iloc[0] == pytest.approx(13.2)

    def test_latest_bar_identity(self):
        """The latest bar's qfq close equals its raw close (identity at latest point)."""
        daily = _make_daily(
            dates=["20260710", "20260711", "20260712"],
            close_prices=[20.0, 22.0, 25.0],
        )
        adj = _make_adj(
            dates=["20260710", "20260711", "20260712"],
            factors=[2.0, 1.5, 1.0],
        )
        result = apply_qfq(daily, adj)

        assert result is not None
        last = result.iloc[-1]
        assert last["close_qfq"] == last["close"]
        assert last["open_qfq"] == last["open"]
        assert last["high_qfq"] == last["high"]
        assert last["low_qfq"] == last["low"]

    def test_all_price_cols_have_qfq_versions(self):
        """All 4 price columns (open, high, low, close) have corresponding qfq columns."""
        daily = _make_daily(dates=["20260710", "20260711"])
        adj = _make_adj(dates=["20260710", "20260711"], factors=[1.5, 1.0])
        result = apply_qfq(daily, adj)

        assert result is not None
        for col in PRICE_COLS:
            assert f"{col}_qfq" in result.columns

    def test_amount_not_adjusted(self):
        """The amount column remains unchanged (no qfq version created)."""
        daily = _make_daily(
            dates=["20260710", "20260711", "20260712"],
            amounts=[1.0e8, 1.5e8, 2.0e8],
        )
        adj = _make_adj(
            dates=["20260710", "20260711", "20260712"],
            factors=[1.2, 1.1, 1.0],
        )
        result = apply_qfq(daily, adj)

        assert result is not None
        assert "amount" in result.columns
        assert "amount_qfq" not in result.columns
        assert result["amount"].tolist() == [1.0e8, 1.5e8, 2.0e8]

    def test_original_columns_preserved(self):
        """Original OHLC columns still exist alongside the qfq columns."""
        daily = _make_daily(dates=["20260710", "20260711"])
        adj = _make_adj(dates=["20260710", "20260711"], factors=[1.5, 1.0])
        result = apply_qfq(daily, adj)

        assert result is not None
        for col in PRICE_COLS:
            assert col in result.columns
            assert f"{col}_qfq" in result.columns

    def test_trade_date_column_preserved(self):
        """Original trade_date column is retained in the output."""
        daily = _make_daily(dates=["20260710", "20260711", "20260712"])
        adj = _make_adj(dates=["20260710", "20260711", "20260712"], factors=[1.2, 1.1, 1.0])
        result = apply_qfq(daily, adj)

        assert result is not None
        assert "trade_date" in result.columns
        assert result["trade_date"].tolist() == ["20260710", "20260711", "20260712"]


# ---- Edge cases ----

class TestApplyQfqEdgeCases:

    def test_none_adj_factor_returns_none(self):
        """adj_factor_df=None should return None."""
        daily = _make_daily(dates=["20260710"])
        result = apply_qfq(daily, None)
        assert result is None

    def test_empty_adj_factor_returns_none(self):
        """Empty adj_factor DataFrame should return None."""
        daily = _make_daily(dates=["20260710"])
        adj = pd.DataFrame(columns=["trade_date", "adj_factor"])
        result = apply_qfq(daily, adj)
        assert result is None

    def test_all_nan_adj_factor_returns_none(self):
        """If adj_factor column is all NaN after merge, returns None."""
        daily = _make_daily(dates=["20260710", "20260711"])
        # Adj factor data has completely non-overlapping dates
        adj = _make_adj(dates=["20250101", "20250102"], factors=[1.5, 1.0])
        result = apply_qfq(daily, adj)
        assert result is None

    def test_partial_adj_factor_coverage(self):
        """Partial adj_factor coverage → whole-stock reject (None)."""
        daily = _make_daily(
            dates=["20260710", "20260711", "20260712"],
            close_prices=[10.0, 12.0, 15.0],
        )
        # Only two dates have adj factors; middle date is missing
        adj = _make_adj(
            dates=["20260710", "20260712"],
            factors=[1.2, 1.0],
        )
        result = apply_qfq(daily, adj)
        assert result is None

    def test_trailing_nan_adj_factor_returns_none(self):
        """Latest bar missing adj_factor → None (avoids NaN scale poison)."""
        daily = _make_daily(
            dates=["20260710", "20260711", "20260712"],
            close_prices=[10.0, 12.0, 15.0],
        )
        adj = _make_adj(
            dates=["20260710", "20260711"],
            factors=[1.2, 1.1],
        )
        result = apply_qfq(daily, adj)
        assert result is None

    def test_zero_latest_adj_factor_returns_none(self):
        """Latest adj_factor == 0 → None."""
        daily = _make_daily(
            dates=["20260710", "20260711"],
            close_prices=[10.0, 12.0],
        )
        adj = _make_adj(dates=["20260710", "20260711"], factors=[1.2, 0.0])
        result = apply_qfq(daily, adj)
        assert result is None

    def test_near_zero_latest_adj_factor_returns_none(self):
        """Near-zero latest adj_factor (1e-15) must hard-reject, not explode prices."""
        daily = _make_daily(
            dates=["20260710", "20260711"],
            close_prices=[10.0, 12.0],
        )
        adj = _make_adj(dates=["20260710", "20260711"], factors=[1.2, 1e-15])
        result = apply_qfq(daily, adj)
        assert result is None

    def test_negative_latest_adj_factor_returns_none(self):
        """Negative latest adj_factor → None."""
        daily = _make_daily(
            dates=["20260710", "20260711"],
            close_prices=[10.0, 12.0],
        )
        adj = _make_adj(dates=["20260710", "20260711"], factors=[1.2, -1.0])
        result = apply_qfq(daily, adj)
        assert result is None

    def test_near_zero_row_adj_factor_returns_none(self):
        """Any row-level near-zero adj_factor → whole-stock reject."""
        daily = _make_daily(
            dates=["20260710", "20260711", "20260712"],
            close_prices=[10.0, 12.0, 15.0],
        )
        adj = _make_adj(
            dates=["20260710", "20260711", "20260712"],
            factors=[1e-15, 1.1, 1.0],
        )
        result = apply_qfq(daily, adj)
        assert result is None

    def test_negative_row_adj_factor_returns_none(self):
        """Any row-level negative adj_factor → whole-stock reject."""
        daily = _make_daily(
            dates=["20260710", "20260711", "20260712"],
            close_prices=[10.0, 12.0, 15.0],
        )
        adj = _make_adj(
            dates=["20260710", "20260711", "20260712"],
            factors=[-0.5, 1.1, 1.0],
        )
        result = apply_qfq(daily, adj)
        assert result is None

    def test_inf_adj_factor_returns_none(self):
        """Non-finite (inf) adj_factor → None."""
        daily = _make_daily(
            dates=["20260710", "20260711"],
            close_prices=[10.0, 12.0],
        )
        adj = _make_adj(dates=["20260710", "20260711"], factors=[1.2, float("inf")])
        result = apply_qfq(daily, adj)
        assert result is None

    def test_single_row(self):
        """Single-row daily with matching adj_factor works correctly."""
        daily = _make_daily(dates=["20260710"], close_prices=[42.0])
        adj = _make_adj(dates=["20260710"], factors=[1.0])
        result = apply_qfq(daily, adj)

        assert result is not None
        assert result["close_qfq"].iloc[0] == 42.0

    def test_single_row_with_non_one_factor(self):
        """Single-row: latest factor is the only factor, so identity holds."""
        daily = _make_daily(dates=["20260710"], close_prices=[42.0])
        adj = _make_adj(dates=["20260710"], factors=[0.8])
        result = apply_qfq(daily, adj)

        assert result is not None
        # latest_adj = 0.8, scale = 0.8 / 0.8 = 1.0
        assert result["close_qfq"].iloc[0] == 42.0

    def test_open_qfq_computed(self):
        """Verify open_qfq is computed correctly (not just close)."""
        daily = _make_daily(
            dates=["20260710", "20260711", "20260712"],
            open_prices=[9.5, 11.8, 14.5],
        )
        adj = _make_adj(
            dates=["20260710", "20260711", "20260712"],
            factors=[1.2, 1.1, 1.0],
        )
        result = apply_qfq(daily, adj)

        assert result is not None
        # 20260710: 9.5 * 1.2 / 1.0 = 11.4
        assert result.loc[result["trade_date"] == "20260710", "open_qfq"].iloc[0] == pytest.approx(11.4)
        # Latest: identity
        assert result.loc[result["trade_date"] == "20260712", "open_qfq"].iloc[0] == result.loc[
            result["trade_date"] == "20260712", "open"].iloc[0]

    def test_high_and_low_qfq_computed(self):
        """Verify high_qfq and low_qfq are computed correctly."""
        daily = _make_daily(
            dates=["20260710", "20260711"],
            high_prices=[11.0, 13.0],
            low_prices=[9.0, 11.0],
        )
        adj = _make_adj(
            dates=["20260710", "20260711"],
            factors=[2.0, 1.0],
        )
        result = apply_qfq(daily, adj)

        assert result is not None
        # 20260710: high = 11.0 * 2.0 / 1.0 = 22.0, low = 9.0 * 2.0 = 18.0
        assert result.loc[result["trade_date"] == "20260710", "high_qfq"].iloc[0] == pytest.approx(22.0)
        assert result.loc[result["trade_date"] == "20260710", "low_qfq"].iloc[0] == pytest.approx(18.0)

    def test_extra_columns_preserved(self):
        """Extra columns (e.g. vol, change_pct) are passed through unchanged."""
        daily = _make_daily(dates=["20260710", "20260711"])
        daily["vol"] = [100000, 200000]
        daily["change_pct"] = [1.5, 2.0]
        adj = _make_adj(dates=["20260710", "20260711"], factors=[1.5, 1.0])
        result = apply_qfq(daily, adj)

        assert result is not None
        assert result["vol"].tolist() == [100000, 200000]
        assert result["change_pct"].tolist() == [1.5, 2.0]

    def test_factor_increasing(self):
        """With multiple non-equal factors, ensure scale factors are correct."""
        daily = _make_daily(
            dates=["20260708", "20260709", "20260710"],
            close_prices=[5.0, 6.0, 7.0],
        )
        adj = _make_adj(
            dates=["20260708", "20260709", "20260710"],
            factors=[3.0, 2.0, 1.0],
        )
        result = apply_qfq(daily, adj)

        assert result is not None
        # Latest adj = 1.0
        # 20260708: 5.0 * 3.0 / 1.0 = 15.0
        assert result.loc[result["trade_date"] == "20260708", "close_qfq"].iloc[0] == pytest.approx(15.0)
        # 20260709: 6.0 * 2.0 / 1.0 = 12.0
        assert result.loc[result["trade_date"] == "20260709", "close_qfq"].iloc[0] == pytest.approx(12.0)
        # 20260710: identity
        assert result.loc[result["trade_date"] == "20260710", "close_qfq"].iloc[0] == 7.0
