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
