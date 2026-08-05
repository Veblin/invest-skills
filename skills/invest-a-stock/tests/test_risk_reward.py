"""Tests for lib.risk_reward — DCF scenario risk-reward calculation."""

from __future__ import annotations

import pytest


class TestCalcRiskReward:
    """Pure math function tests — no data dependencies."""

    def test_basic_calculation(self):
        from lib.risk_reward import calc_risk_reward

        result = calc_risk_reward(
            current_price=100.0,
            bull_target=150.0,
            base_target=120.0,
            bear_target=80.0,
            bull_prob=0.20,
            base_prob=0.50,
            bear_prob=0.30,
        )

        assert result["current_price"] == 100.0
        assert result["upside_pct"] == 50.0
        assert result["downside_pct"] == 20.0
        assert result["base_return_pct"] == 20.0
        # expected = 0.2*0.5 + 0.5*0.2 + 0.3*(-0.2) = 0.10 + 0.10 - 0.06 = 0.14
        assert result["expected_return_pct"] == pytest.approx(14.0, abs=0.1)
        # rr = (0.2 * 0.5) / (0.3 * 0.2) = 0.1 / 0.06 = 1.67
        assert result["risk_reward_ratio"] == pytest.approx(1.67, abs=0.01)
        assert result["meets_threshold"] is False  # 1.67 < 2.0

    def test_meets_threshold(self):
        from lib.risk_reward import calc_risk_reward

        # 3:1 upside:downside with equal probabilities
        result = calc_risk_reward(
            current_price=50.0,
            bull_target=100.0,   # +100%
            base_target=60.0,     # +20%
            bear_target=40.0,     # -20%
        )
        # rr = (0.2 * 1.0) / (0.3 * 0.2) = 0.2/0.06 = 3.33
        assert result["risk_reward_ratio"] == pytest.approx(3.33, abs=0.1)
        assert result["meets_threshold"] is True

    def test_zero_price_error(self):
        from lib.risk_reward import calc_risk_reward

        result = calc_risk_reward(0, 150, 120, 80)
        assert "error" in result
        assert result["meets_threshold"] is False

    def test_negative_price_error(self):
        from lib.risk_reward import calc_risk_reward

        result = calc_risk_reward(-10, 150, 120, 80)
        assert "error" in result

    def test_infinite_ratio_when_zero_downside(self):
        from lib.risk_reward import calc_risk_reward

        # Bear target above current price → no downside
        result = calc_risk_reward(100, 150, 130, 110)
        assert result["risk_reward_ratio"] == float("inf")
        assert result["meets_threshold"] is True

    def test_custom_probabilities(self):
        from lib.risk_reward import calc_risk_reward

        result = calc_risk_reward(
            100, 200, 120, 50,
            bull_prob=0.10, base_prob=0.60, bear_prob=0.30,
        )
        assert result["scenarios"]["bull"]["probability"] == "10%"
        assert result["scenarios"]["base"]["probability"] == "60%"
        assert result["scenarios"]["bear"]["probability"] == "30%"


class TestFormatRiskRewardTable:
    def test_basic_output(self):
        from lib.risk_reward import calc_risk_reward, format_risk_reward_table

        result = calc_risk_reward(100.0, 150.0, 120.0, 80.0)
        output = format_risk_reward_table(result)

        assert "盈亏比" in output
        assert "100.00" in output
        assert "+50.0%" in output
        assert "情景明细" in output

    def test_error_output(self):
        from lib.risk_reward import format_risk_reward_table

        output = format_risk_reward_table({"error": "test error"})
        assert "❌" in output
        assert "test error" in output
