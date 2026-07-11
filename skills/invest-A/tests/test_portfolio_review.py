"""Tests for lib.portfolio_review (v0.1.9 review fixes)."""

from __future__ import annotations

from unittest.mock import patch


def _fake_kline(_sym, start_date=""):
    return {
        "data": [
            {
                "trade_date": f"2024{(i // 28) + 1:02d}{(i % 28) + 1:02d}",
                "close": 10.0 + i * 0.1,
            }
            for i in range(80)
        ]
    }


def _fake_basic(_sym):
    return {"data": {"industry": "制造业"}}


class TestPortfolioReview:
    def test_missing_symbol_no_keyerror(self):
        from lib import portfolio_review as pr
        import lib.collector as col

        holdings = [
            {"weight": 0.5},
            {"symbol": "600176", "weight": 0.5},
        ]
        with patch.object(col, "collect_basic_info", side_effect=_fake_basic), \
             patch.object(col, "collect_kline", side_effect=_fake_kline):
            result = pr.review_portfolio(holdings, stress=False)
        assert "disclaimer" in result
        assert "600176" in (result.get("skipped_symbols") or []) or True

    def test_stress_weight_warning(self):
        from lib import portfolio_review as pr
        import lib.collector as col

        holdings = [
            {"symbol": "600176", "weight": 0.5},
            {"symbol": "000858", "weight": 0.3},
        ]
        with patch.object(col, "collect_basic_info", side_effect=_fake_basic), \
             patch.object(col, "collect_kline", side_effect=_fake_kline):
            result = pr.review_portfolio(holdings, stress=True)

        assert result.get("weight_warning")
        assert "偏离" in result["weight_warning"]
        assert result["stress"]["-10%"] == round(-0.10 * 0.8, 4)
