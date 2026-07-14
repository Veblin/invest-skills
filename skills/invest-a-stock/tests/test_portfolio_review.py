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
            {"weight": 0.5},  # missing symbol — quietly skipped
            {"symbol": "600176", "weight": 0.5},
        ]
        with patch.object(col, "collect_basic_info", side_effect=_fake_basic), \
             patch.object(col, "collect_kline", side_effect=_fake_kline):
            result = pr.review_portfolio(holdings, stress=False)
        assert "disclaimer" in result
        # Empty-symbol holding is continue'd — must not appear in skipped_symbols
        skipped = result.get("skipped_symbols") or []
        assert "" not in skipped
        # Valid symbol with mocked kline should be reviewed, not skipped
        assert "600176" not in skipped
        # Only the valid symbol contributes to industry concentration
        conc = result["industry_concentration"]
        assert conc == [("制造业", 0.5)]


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

    def test_industry_concentration(self):
        """行业集中度按权重降序排列."""
        from lib import portfolio_review as pr
        import lib.collector as col

        holdings = [
            {"symbol": "600176", "weight": 0.4},
            {"symbol": "000858", "weight": 0.35},
            {"symbol": "600519", "weight": 0.25},
        ]
        industries = {"600176": "建材", "000858": "食品饮料", "600519": "食品饮料"}

        def _basic(sym):
            return {"data": {"industry": industries.get(sym, "未知")}}

        with patch.object(col, "collect_basic_info", side_effect=_basic), \
             patch.object(col, "collect_kline", side_effect=_fake_kline):
            result = pr.review_portfolio(holdings, stress=False)

        conc = result["industry_concentration"]
        assert len(conc) >= 1
        # 食品饮料 应合并权重 0.35+0.25=0.6，排第一
        assert conc[0][0] == "食品饮料"
        assert conc[0][1] == 0.6

    def test_correlation_skipped_when_under_three(self):
        """持仓 < 3 只 → 相关性跳过."""
        from lib import portfolio_review as pr
        import lib.collector as col

        holdings = [
            {"symbol": "600176", "weight": 0.6},
            {"symbol": "000858", "weight": 0.4},
        ]
        with patch.object(col, "collect_basic_info", side_effect=_fake_basic), \
             patch.object(col, "collect_kline", side_effect=_fake_kline):
            result = pr.review_portfolio(holdings, stress=False)

        assert "skipped" in result["correlation"]
        assert "3" in result["correlation"]["skipped"]

    def test_correlation_matrix_with_three_symbols(self):
        """≥3 标的 → 输出相关性矩阵."""
        from lib import portfolio_review as pr
        import lib.collector as col

        holdings = [
            {"symbol": "A", "weight": 0.4},
            {"symbol": "B", "weight": 0.3},
            {"symbol": "C", "weight": 0.3},
        ]
        with patch.object(col, "collect_basic_info", side_effect=_fake_basic), \
             patch.object(col, "collect_kline", side_effect=_fake_kline):
            result = pr.review_portfolio(holdings, stress=False)

        assert "matrix" in result["correlation"]
        matrix = result["correlation"]["matrix"]
        # 3×3 对称矩阵
        assert len(matrix) == 3
        for sym in ("A", "B", "C"):
            assert sym in matrix
            assert sym in matrix[sym]
            # 自相关应为 1.0（或接近）
            assert matrix[sym][sym] == 1.0

    def test_empty_holdings_no_crash(self):
        """空持仓列表不崩溃."""
        from lib import portfolio_review as pr

        result = pr.review_portfolio([], stress=False)
        assert result["industry_concentration"] == []
        assert result["correlation"]["skipped"] == "持仓 < 3 只，跳过相关性"

    def test_missing_weight_defaults_to_zero(self):
        """无 weight 字段 → 默认 0."""
        from lib import portfolio_review as pr
        import lib.collector as col

        holdings = [
            {"symbol": "600176"},  # no weight
            {"symbol": "000858", "weight": 0.3},
        ]
        with patch.object(col, "collect_basic_info", side_effect=_fake_basic), \
             patch.object(col, "collect_kline", side_effect=_fake_kline):
            result = pr.review_portfolio(holdings, stress=False)

        # 权重和=0.3，偏离 1.0 → 有 warning
        assert result.get("weight_warning")

    def test_stress_no_warning_when_normalized(self):
        """权重和 ≈ 1.0 → 无 weight_warning."""
        from lib import portfolio_review as pr
        import lib.collector as col

        holdings = [
            {"symbol": "A", "weight": 0.5},
            {"symbol": "B", "weight": 0.5},
        ]
        with patch.object(col, "collect_basic_info", side_effect=_fake_basic), \
             patch.object(col, "collect_kline", side_effect=_fake_kline):
            result = pr.review_portfolio(holdings, stress=True)

        assert result.get("weight_warning") is None
        assert result["stress"]["-10%"] == -0.1  # scale=1.0

    def test_disclaimer_present(self):
        """输出包含 LAW 6 免责声明."""
        from lib import portfolio_review as pr
        import lib.collector as col

        holdings = [{"symbol": "600176", "weight": 1.0}]
        with patch.object(col, "collect_basic_info", side_effect=_fake_basic), \
             patch.object(col, "collect_kline", side_effect=_fake_kline):
            result = pr.review_portfolio(holdings, stress=False)

        assert "不构成投资建议" in result["disclaimer"]
