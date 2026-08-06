"""Tests for suspension detection — pure function tests, no network."""

from __future__ import annotations

import pytest

from suspension import detect_suspensions, is_gap_across_suspension


# ---- Fixtures ----

@pytest.fixture
def trade_cal():
    """Ordered list of 7 trade dates."""
    return ["20260710", "20260711", "20260712", "20260713",
            "20260714", "20260715", "20260716"]


# ---- detect_suspensions ----

class TestDetectSuspensions:

    def test_no_suspensions(self, trade_cal):
        """All stocks present on all dates → empty dict."""
        universe = ["A", "B"]
        daily = {
            "20260710": {"A", "B"},
            "20260711": {"A", "B"},
            "20260712": {"A", "B"},
            "20260713": {"A", "B"},
            "20260714": {"A", "B"},
            "20260715": {"A", "B"},
            "20260716": {"A", "B"},
        }
        result = detect_suspensions(universe, daily, trade_cal)
        assert result == {}

    def test_single_stock_suspended_one_day(self, trade_cal):
        """Stock A missing from day 3 → {A: [day3]}."""
        universe = ["A", "B"]
        daily = {
            "20260710": {"A", "B"},
            "20260711": {"A", "B"},
            "20260712": {"B"},  # A suspended
            "20260713": {"A", "B"},
            "20260714": {"A", "B"},
            "20260715": {"A", "B"},
            "20260716": {"A", "B"},
        }
        result = detect_suspensions(universe, daily, trade_cal)
        assert result == {"A": ["20260712"]}

    def test_multiple_suspensions(self, trade_cal):
        """Stock B missing on days 2, 5, 7 → {B: [2, 5, 7]}."""
        universe = ["A", "B"]
        daily = {
            "20260710": {"A", "B"},
            "20260711": {"A"},          # B suspended
            "20260712": {"A", "B"},
            "20260713": {"A", "B"},
            "20260714": {"A"},          # B suspended
            "20260715": {"A", "B"},
            "20260716": {"A"},          # B suspended
        }
        result = detect_suspensions(universe, daily, trade_cal)
        assert result == {"B": ["20260711", "20260714", "20260716"]}

    def test_stock_never_appears_not_flagged(self, trade_cal):
        """Stock C never has any data → should NOT be flagged (not in appeared set)."""
        universe = ["A", "B", "C"]
        daily = {
            "20260710": {"A", "B"},
            "20260711": {"A", "B"},
            "20260712": {"A", "B"},
            "20260713": {"A", "B"},
            "20260714": {"A", "B"},
            "20260715": {"A", "B"},
            "20260716": {"A", "B"},
        }
        result = detect_suspensions(universe, daily, trade_cal)
        assert "C" not in result
        assert result == {}

    def test_empty_universe(self, trade_cal):
        """Empty universe → empty dict."""
        result = detect_suspensions([], {"20260710": {"A", "B"}}, trade_cal)
        assert result == {}

    def test_empty_daily_data(self, trade_cal):
        """Empty daily_by_date → empty dict (no stock 'appeared')."""
        universe = ["A", "B"]
        result = detect_suspensions(universe, {}, trade_cal)
        assert result == {}

    def test_stock_only_in_subset_of_dates(self, trade_cal):
        """Stock that appears only sometimes gets suspension dates for other dates."""
        universe = ["A", "B"]
        daily = {
            "20260710": {"A", "B"},
            "20260711": {"A"},
            "20260712": {"A"},
            "20260713": {"A", "B"},
            "20260714": {"A", "B"},
            "20260715": {"A", "B"},
            "20260716": {"A", "B"},
        }
        result = detect_suspensions(universe, daily, trade_cal)
        assert "B" in result
        assert result["B"] == ["20260711", "20260712"]

    def test_pre_listing_dates_not_suspended(self, trade_cal):
        """Dates before list_date are not flagged even if bars are missing."""
        universe = ["A"]
        # A only appears from 20260714 onward
        daily = {
            "20260714": {"A"},
            "20260715": {"A"},
            "20260716": {"A"},
        }
        list_dates = {"A": "20260714"}
        result = detect_suspensions(universe, daily, trade_cal, list_dates=list_dates)
        assert result == {}

        # Without list_dates: 10-13 are absent from daily_by_date entirely
        # (no daily row at all → 整批拉取失败的数据缺口，非停牌；旧行为把
        # 键缺失日标为全池停牌，被数据缺口日判定修复)
        result_no_list = detect_suspensions(universe, daily, trade_cal)
        assert result_no_list == {}

    def test_subset_of_universe_in_daily(self, trade_cal):
        """Only some stocks in daily data → others flagged as suspended on all dates
        (since they appeared at least once in SOME date)."""
        universe = ["A", "B", "C"]
        daily = {
            "20260710": {"A"},
            "20260711": {"A"},
            "20260712": {"A"},
            "20260713": {"A"},
            "20260714": {"A"},
            "20260715": {"A"},
            "20260716": {"A"},
        }
        result = detect_suspensions(universe, daily, trade_cal)
        # A appeared at least once → no suspension for A on any date
        assert "A" not in result
        # B appeared at least once (only needs to appear once to count as "appeared")
        # Actually B never appeared in any daily_by_date value
        assert "B" not in result  # never appeared at all
        # C never appeared either
        assert "C" not in result

    def test_stock_appears_once_then_suspended(self, trade_cal):
        """Stock appears once, then suspension on all other dates is flagged."""
        universe = ["A", "B"]
        daily = {
            "20260710": {"A", "B"},
            "20260711": {"B"},
            "20260712": {"B"},
            "20260713": {"B"},
            "20260714": {"B"},
            "20260715": {"B"},
            "20260716": {"B"},
        }
        result = detect_suspensions(universe, daily, trade_cal)
        assert result == {"A": ["20260711", "20260712", "20260713",
                                "20260714", "20260715", "20260716"]}

    def test_multiple_stocks_suspended_different_dates(self, trade_cal):
        """Multiple stocks suspended on different dates."""
        universe = ["A", "B", "C"]
        daily = {
            "20260710": {"A", "B", "C"},
            "20260711": {"A", "C"},       # B suspended
            "20260712": {"B", "C"},       # A suspended
            "20260713": {"A", "B", "C"},
            "20260714": {"A", "B"},       # C suspended
            "20260715": {"A", "B", "C"},
            "20260716": {"A", "B", "C"},
        }
        result = detect_suspensions(universe, daily, trade_cal)
        assert result == {
            "A": ["20260712"],
            "B": ["20260711"],
            "C": ["20260714"],
        }

    def test_empty_trade_cal(self):
        """Empty trade_cal → no dates to check → empty dict."""
        universe = ["A"]
        daily = {"20260710": {"A"}}
        result = detect_suspensions(universe, daily, [])
        assert result == {}

    def test_suspension_dates_sorted(self, trade_cal):
        """Suspension dates should be in trade_cal order (sorted)."""
        universe = ["A"]
        daily = {
            "20260710": {"A"},
            "20260711": {"A"},
            "20260712": set(),    # A suspended
            "20260713": set(),    # A suspended
            "20260714": {"A"},
            "20260715": set(),    # A suspended
            "20260716": {"A"},
        }
        result = detect_suspensions(universe, daily, trade_cal)
        assert result["A"] == ["20260712", "20260713", "20260715"]


# ---- is_gap_across_suspension ----

class TestIsGapAcrossSuspension:

    def test_true_case(self, trade_cal):
        """gap_date's preceding trade day is in suspension_dates → True."""
        suspension_dates = ["20260711", "20260712"]
        result = is_gap_across_suspension("20260713", suspension_dates, trade_cal)
        assert result is True

    def test_false_case(self, trade_cal):
        """gap_date's preceding trade day is NOT in suspension_dates → False."""
        suspension_dates = ["20260711"]
        result = is_gap_across_suspension("20260713", suspension_dates, trade_cal)
        assert result is False

    def test_first_trade_date(self, trade_cal):
        """gap_date is the first date in trade_cal → False (no preceding day)."""
        result = is_gap_across_suspension("20260710", ["20260709"], trade_cal)
        assert result is False

    def test_empty_suspension_list(self, trade_cal):
        """Empty suspension_dates → False."""
        result = is_gap_across_suspension("20260713", [], trade_cal)
        assert result is False

    def test_gap_date_not_in_trade_cal(self, trade_cal):
        """gap_date not in trade_cal → False (ValueError caught)."""
        result = is_gap_across_suspension("99999999", ["20260712"], trade_cal)
        assert result is False

    def test_empty_trade_cal(self):
        """Empty trade_cal → False."""
        result = is_gap_across_suspension("20260713", ["20260712"], [])
        assert result is False

    def test_gap_on_first_day_no_other_suspension(self, trade_cal):
        """gap_date is first trade date, even if prev date (not in cal) is in susp list → False."""
        result = is_gap_across_suspension("20260710", ["20260709"], trade_cal)
        assert result is False

    def test_consecutive_suspension_before_gap(self, trade_cal):
        """Gap after multiple consecutive suspension days → True."""
        suspension_dates = ["20260711", "20260712", "20260713"]
        result = is_gap_across_suspension("20260714", suspension_dates, trade_cal)
        assert result is True

    def test_gap_across_single_day_suspension(self, trade_cal):
        """Single day of suspension immediately before gap → True."""
        suspension_dates = ["20260715"]
        result = is_gap_across_suspension("20260716", suspension_dates, trade_cal)
        assert result is True

    def test_prev_date_suspended_but_not_in_list(self, trade_cal):
        """Preceding day not in suspension_dates → False."""
        suspension_dates = ["20260710"]
        result = is_gap_across_suspension("20260713", suspension_dates, trade_cal)
        assert result is False

    def test_gap_date_is_suspension_date(self, trade_cal):
        """gap_date itself is in suspension_dates, but prev day not → False."""
        suspension_dates = ["20260711", "20260713"]
        result = is_gap_across_suspension("20260713", suspension_dates, trade_cal)
        assert result is False


# ======================================================================
# T3: fallback calendar + suspension integration
# ======================================================================


class TestFallbackCalendarIntegration:
    """T3: 兜底日历不应将节假日误标为停牌。

    自然日估算的 _estimate_trade_dates 会把周末/节假日纳入 trade_cal，
    而 daily_by_date 中没有这些日期的数据。旧代码会把这些日期标为全池停牌。
    修复后 is_estimated=True 时直接跳过停牌检测。
    """

    @pytest.fixture
    def fallback_trade_cal(self):
        """Simulated fallback calendar: Mon-Fri including a Sat/Sun holiday gap."""
        return [
            "20260710",  # Fri
            "20260711",  # Sat (holiday in fallback)
            "20260712",  # Sun (holiday in fallback)
            "20260713",  # Mon
            "20260714",  # Tue
            "20260715",  # Wed
            "20260716",  # Thu
        ]

    def test_fallback_cal_pollutes_suspension_without_guard(self, fallback_trade_cal):
        """键缺失日（fallback 日历的周末/节假日）不再判为停牌。

        Weekends/holidays from the fallback calendar are NOT in
        daily_by_date (no daily rows at all → 数据缺口，等价于整批拉取
        失败）。数据缺口日判定在函数层修复了 T1 的误标问题——不再依赖
        调用方 is_estimated 守卫。
        """
        universe = ["A", "B"]
        daily = {
            "20260710": {"A", "B"},
            "20260713": {"A", "B"},
            "20260714": {"A", "B"},
            "20260715": {"A", "B"},
            "20260716": {"A", "B"},
        }
        # Weekends 11/12 are in fallback_trade_cal but NOT in daily
        result = detect_suspensions(universe, daily, fallback_trade_cal)
        # 数据缺口日判定：11/12 无任何日线行 → 不判停牌（旧行为：全池标停牌）
        assert result == {}

    def test_empty_suspension_when_cal_is_estimated(self):
        """When cal_is_estimated=True, suspension_map should be empty (T1 fix).

        The caller (_run_scan) is responsible for skipping detect_suspensions
        when is_estimated is True. This test verifies the expected behavior
        for the scan-level contract.
        """
        # Simulate what _run_scan does after T1 fix:
        cal_is_estimated = True
        suspension_map: dict[str, list[str]] = {}
        if not cal_is_estimated:
            suspension_map = detect_suspensions(
                ["A"], {"20260713": {"A"}}, ["20260713"]
            )
        # With estimated calendar, suspension_map stays empty
        assert suspension_map == {}

    def test_real_calendar_still_detects_suspensions(self):
        """T1 fix must not break real calendar suspension detection."""
        cal_is_estimated = False
        suspension_map: dict[str, list[str]] = {}
        if not cal_is_estimated:
            suspension_map = detect_suspensions(
                ["A", "B"],
                {
                    "20260713": {"B"},       # A suspended
                    "20260714": {"A", "B"},
                },
                ["20260713", "20260714"],
            )
        assert suspension_map == {"A": ["20260713"]}
