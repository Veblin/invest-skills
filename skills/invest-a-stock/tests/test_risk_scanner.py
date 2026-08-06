"""Tests for lib.risk_scanner — 17 risk signals across financial/business/market.

Matches actual signal IDs from risk_scanner.py:
  financial: cashflow_negative, profit_quality_low, receivable_expansion,
             inventory_expansion, deducted_profit_divergence, debt_ratio_rising,
             interest_coverage_weak
  business: gross_margin_decline, competition_intense, customer_concentration
  market: valuation_pe_high, valuation_pe_low, northbound_outflow, etc.
"""

from __future__ import annotations


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _fin_row(end_date: str, **kw) -> dict:
    defaults = {
        "end_date": end_date,
        "revenue": 5e9,
        "net_profit": 5e8,
        "n_cashflow_act": 6e8,
        "ocf": 6e8,
        "grossprofit_margin": 35.0,
        "roe": 15.0,
        "debt_to_assets": 45.0,
        "current_ratio": 1.8,
    }
    defaults.update(kw)
    return defaults


def _fin_rows(*rows: dict) -> list[dict]:
    return list(rows)


# ---------------------------------------------------------------------------
# scan_financial_risks
# ---------------------------------------------------------------------------

class TestScanFinancialRisks:
    def test_all_clear_with_healthy_data(self):
        from lib.risk_scanner import scan_financial_risks
        rows = _fin_rows(
            _fin_row("2024-03-31", n_cashflow_act=5e8, debt_to_assets=40.0,
                     current_ratio=2.0, roe=15.0),
            _fin_row("2024-06-30", n_cashflow_act=6e8, debt_to_assets=42.0,
                     current_ratio=1.9, roe=16.0),
        )
        signals = scan_financial_risks(rows)
        triggered = [s for s in signals if s["triggered"]]
        assert len(triggered) == 0

    def test_cashflow_negative_signal_present(self):
        from lib.risk_scanner import scan_financial_risks
        rows = _fin_rows(
            _fin_row("2024-03-31", n_cashflow_act=-1e8),
            _fin_row("2024-06-30", n_cashflow_act=-2e8),
        )
        signals = scan_financial_risks(rows)
        ids = [s["id"] for s in signals]
        assert "cashflow_negative" in ids

    def test_profit_quality_signal_present(self):
        from lib.risk_scanner import scan_financial_risks
        rows = _fin_rows(
            _fin_row("2024-06-30", net_profit=5e8, n_cashflow_act=5e7),
        )
        signals = scan_financial_risks(rows)
        ids = [s["id"] for s in signals]
        assert "profit_quality_low" in ids

    def test_receivable_signal_present(self):
        from lib.risk_scanner import scan_financial_risks
        rows = _fin_rows(_fin_row("2024-06-30"))
        signals = scan_financial_risks(rows)
        ids = [s["id"] for s in signals]
        assert "receivable_expansion" in ids

    def test_debt_ratio_rising_signal_present(self):
        from lib.risk_scanner import scan_financial_risks
        rows = _fin_rows(
            _fin_row("2024-03-31", debt_to_assets=45.0),
            _fin_row("2024-06-30", debt_to_assets=62.0),
        )
        signals = scan_financial_risks(rows, industry_median_debt=40.0)
        ids = [s["id"] for s in signals]
        assert "debt_ratio_rising" in ids

    def test_insufficient_data_graceful(self):
        from lib.risk_scanner import scan_financial_risks
        signals = scan_financial_risks([])
        assert isinstance(signals, list)

    def test_interest_coverage_signal_present(self):
        from lib.risk_scanner import scan_financial_risks
        rows = _fin_rows(_fin_row("2024-06-30", ebit=5e8, fin_exp_int_exp=1e9))
        signals = scan_financial_risks(rows)
        ids = [s["id"] for s in signals]
        assert "interest_coverage_weak" in ids

    def test_deducted_profit_signal_present(self):
        from lib.risk_scanner import scan_financial_risks
        rows = _fin_rows(_fin_row("2024-06-30"))
        signals = scan_financial_risks(rows)
        ids = [s["id"] for s in signals]
        assert "deducted_profit_divergence" in ids


class TestReceivableInventorySamePeriodYoY:
    """应收/存货扩张信号的同比必须限定相同报告期类型（Q1 对 Q1、年报对年报）。

    fina_indicator 的 revenue 为累计 YTD 口径：相邻行（如 2026Q1 累计 vs
    2025 年报）相除会得到 ~-75% 的伪同比 → 旧逻辑几乎每轮误触发。不可比时
    标 insufficient_data/跳过，不误报。
    """

    @staticmethod
    def _signals(rows: list[dict]) -> dict[str, dict]:
        from lib.risk_scanner import scan_financial_risks

        return {s["id"]: s for s in scan_financial_risks(rows)}

    def test_q1_cumulative_vs_prior_annual_not_false_trigger(self):
        """2026Q1 累计 vs 相邻 2025 年报：无去年同期可比行 → 信号不可得，不触发。"""
        rows = [
            {"end_date": "2025-12-31", "revenue": 20e9,
             "accounts_receiv": 2e9, "inventories": 1.5e9},
            {"end_date": "2026-03-31", "revenue": 5e9,   # 累计口径，非全年
             "accounts_receiv": 2.2e9, "inventories": 1.8e9},
        ]
        s = self._signals(rows)
        assert s["receivable_expansion"]["triggered"] is False
        assert s["inventory_expansion"]["triggered"] is False
        assert s["receivable_expansion"]["status"] == "insufficient_data"

    def test_same_period_yoy_triggers(self):
        """Q1 对 Q1：应收同比 +150% vs 营收同比 +50%（>1.5×）→ 触发。"""
        rows = [
            {"end_date": "2025-03-31", "revenue": 10e9,
             "accounts_receiv": 1e9, "inventories": 0.8e9},
            {"end_date": "2026-03-31", "revenue": 15e9,
             "accounts_receiv": 2.5e9, "inventories": 1.6e9},
        ]
        s = self._signals(rows)
        assert s["receivable_expansion"]["triggered"] is True
        assert s["inventory_expansion"]["triggered"] is True
        assert "2025-03-31" in s["receivable_expansion"]["detail"]

    def test_same_period_yoy_clear(self):
        """Q1 对 Q1：应收增速未超营收 1.5 倍 → 不触发，status=clear。"""
        rows = [
            {"end_date": "2025-03-31", "revenue": 10e9, "accounts_receiv": 1e9},
            {"end_date": "2026-03-31", "revenue": 15e9, "accounts_receiv": 1.1e9},
        ]
        s = self._signals(rows)
        assert s["receivable_expansion"]["triggered"] is False
        assert s["receivable_expansion"]["status"] == "clear"

    def test_annual_vs_annual_yoy(self):
        """年报对年报：应收 +120% vs 营收 +30% → 触发。"""
        rows = [
            {"end_date": "2024-12-31", "revenue": 20e9, "accounts_receiv": 2e9},
            {"end_date": "2025-12-31", "revenue": 26e9, "accounts_receiv": 4.4e9},
        ]
        s = self._signals(rows)
        assert s["receivable_expansion"]["triggered"] is True


# ---------------------------------------------------------------------------
# scan_business_risks (industry param is dict|None)
# ---------------------------------------------------------------------------

class TestScanBusinessRisks:
    def test_returns_list_with_none_industry(self):
        from lib.risk_scanner import scan_business_risks
        rows = _fin_rows(_fin_row("2024-06-30"))
        signals = scan_business_risks(rows, None)
        assert isinstance(signals, list)
        assert len(signals) > 0

    def test_gross_margin_decline_id_present(self):
        from lib.risk_scanner import scan_business_risks
        rows = _fin_rows(_fin_row("2024-06-30"))
        signals = scan_business_risks(rows, None)
        ids = [s["id"] for s in signals]
        assert "gross_margin_decline" in ids

    def test_customer_concentration_id_present(self):
        from lib.risk_scanner import scan_business_risks
        rows = _fin_rows(_fin_row("2024-06-30"))
        signals = scan_business_risks(rows, None)
        ids = [s["id"] for s in signals]
        assert "customer_concentration" in ids

    def test_with_industry_peers_dict(self):
        from lib.risk_scanner import scan_business_risks
        rows = _fin_rows(_fin_row("2024-06-30"))
        industry_data = {"peers": [
            {"gross_margin_trend": "down"},
            {"gross_margin_trend": "down"},
            {"gross_margin_trend": "down"},
        ]}
        signals = scan_business_risks(rows, industry_data)
        assert isinstance(signals, list)


# ---------------------------------------------------------------------------
# scan_market_risks
# ---------------------------------------------------------------------------

class TestScanMarketRisks:
    def test_returns_list(self):
        from lib.risk_scanner import scan_market_risks
        signals = scan_market_risks({}, {}, {})
        assert isinstance(signals, list)
        assert len(signals) > 0

    def test_normal_market_all_clear(self):
        from lib.risk_scanner import scan_market_risks
        valuation = {"pe_ttm": [20.0] * 30, "pb": [2.0] * 30}
        northbound = {"net_sum_10d": 5e8}  # dict, not list
        technical = {"rsi_14": 50.0, "volume_ratio": 1.0}
        signals = scan_market_risks(valuation, northbound, technical)
        triggered = [s for s in signals if s["triggered"]]
        assert len(triggered) == 0


# ---------------------------------------------------------------------------
# risk_report
# ---------------------------------------------------------------------------

class TestRiskReport:
    def test_returns_dict(self):
        from lib.risk_scanner import risk_report
        rows = _fin_rows(_fin_row("2024-06-30"))
        report = risk_report(rows)
        assert isinstance(report, dict)

    def test_empty_input_graceful(self):
        from lib.risk_scanner import risk_report
        report = risk_report([])
        assert isinstance(report, dict)


# ---------------------------------------------------------------------------
# revenue_acceleration_flag
# ---------------------------------------------------------------------------

class TestRevenueAccelerationFlag:
    def test_returns_dict(self):
        from lib.risk_scanner import revenue_acceleration_flag
        rows = _fin_rows(
            _fin_row("2023-06-30", revenue=1e9),
            _fin_row("2024-06-30", revenue=1.5e9),
        )
        result = revenue_acceleration_flag(rows)
        assert isinstance(result, dict)

    def test_insufficient_data(self):
        from lib.risk_scanner import revenue_acceleration_flag
        result = revenue_acceleration_flag([])
        assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# ocf_np_divergence_flag
# ---------------------------------------------------------------------------

class TestOcfNpDivergenceFlag:
    def test_normal_ratio(self):
        from lib.risk_scanner import ocf_np_divergence_flag
        rows = _fin_rows(
            _fin_row("2024-06-30", net_profit=5e8, n_cashflow_act=4.5e8),
        )
        result = ocf_np_divergence_flag(rows)
        assert isinstance(result, dict)

    def test_low_ratio(self):
        from lib.risk_scanner import ocf_np_divergence_flag
        rows = _fin_rows(
            _fin_row("2024-06-30", net_profit=5e8, n_cashflow_act=5e7),
        )
        result = ocf_np_divergence_flag(rows)
        assert isinstance(result, dict)

    def test_negative_profit(self):
        from lib.risk_scanner import ocf_np_divergence_flag
        rows = _fin_rows(
            _fin_row("2024-06-30", net_profit=-1e8, n_cashflow_act=2e8),
        )
        result = ocf_np_divergence_flag(rows)
        assert isinstance(result, dict)
