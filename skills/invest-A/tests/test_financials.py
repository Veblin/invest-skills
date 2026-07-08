"""Tests for lib.financials helpers and related bug fixes."""

from __future__ import annotations


class TestFinancialsHelpers:
    def test_normalize_end_date_formats(self):
        from lib.financials import normalize_end_date

        assert normalize_end_date("20231231") == "20231231"
        assert normalize_end_date("2023-12-31") == "20231231"

    def test_parse_end_date_dot_format(self):
        from lib.financials import parse_end_date

        parsed = parse_end_date("2026.01.01")
        assert parsed is not None
        assert parsed.year == 2026
        assert parsed.month == 1
        assert parsed.day == 1

    def test_prior_year_end_date_hyphenated(self):
        from lib.financials import prior_year_end_date

        assert prior_year_end_date("2023-12-31") == "20221231"
        assert prior_year_end_date("20240331") == "20230331"

    def test_find_yoy_row_hyphenated_prior_record(self):
        from lib.financials import find_yoy_row

        rows = [
            {"end_date": "2023-12-31", "revenue": 100.0},
            {"end_date": "20241231", "revenue": 120.0},
        ]
        latest = {"end_date": "20241231", "revenue": 120.0}
        yoy = find_yoy_row(rows, latest)
        assert yoy is not None
        assert yoy["revenue"] == 100.0

    def test_find_yoy_row_hyphenated_latest_record(self):
        from lib.financials import find_yoy_row

        rows = [
            {"end_date": "20231231", "revenue": 100.0},
            {"end_date": "2024-12-31", "revenue": 120.0},
        ]
        latest = {"end_date": "2024-12-31", "revenue": 120.0}
        yoy = find_yoy_row(rows, latest)
        assert yoy is not None
        assert yoy["revenue"] == 100.0

    def test_yoy_from_fina_rows_hyphenated_dates(self):
        from lib.store import _yoy_from_fina_rows

        rows = [
            {"end_date": "2022-12-31", "revenue": 100.0},
            {"end_date": "2023-12-31", "revenue": 120.0},
        ]
        assert _yoy_from_fina_rows(rows, "revenue") == 20.0

    def test_flow_amount_yuan_preserves_zero(self):
        from lib.collector import _flow_amount_yuan

        assert _flow_amount_yuan({"net_mf_amount": 0.0}) == 0.0
        assert _flow_amount_yuan({"net_mf_vol": 0.0}) == 0.0

    def test_equity_multiplier_low_debt_ratio(self):
        from lib.collector import _q_tushare_financials

        import pandas as pd
        from unittest.mock import MagicMock, patch

        df = pd.DataFrame([{
            "ts_code": "600176.SH",
            "end_date": "20231231",
            "debt_to_assets": 0.8,
            "eqt_to_debt": None,
        }])
        mock_tc = MagicMock()
        mock_tc.query.side_effect = lambda api, **kw: (
            df if api == "fina_indicator" else pd.DataFrame()
        )

        with patch("lib.collector._tushare_client", return_value=mock_tc), patch(
            "lib.env.is_tushare_available", return_value=True
        ), patch("lib.env.get_config", return_value={"TUSHARE_TOKEN": "x" * 32}):
            records = _q_tushare_financials("600176")

        assert records is not None
        em = records[0]["equity_multiplier"]
        assert abs(em - 1.008) < 0.001

    def test_interest_expense_zero_not_missing(self):
        from lib.risk_scanner import scan_financial_risks

        financials = [
            {"end_date": "20221231", "ebit": 1e8, "int_exp": 1e7},
            {"end_date": "20231231", "ebit": 1e8, "int_exp": 0.0},
        ]
        signals = scan_financial_risks(financials)
        int_sig = next(s for s in signals if s["id"] == "interest_coverage_weak")
        assert not int_sig["triggered"]
        assert "为零" in int_sig["detail"] or "不适用" in int_sig["detail"]


class TestFinancialSoftSignals:
    def test_revenue_acceleration_flag_uses_true_yoy_pairs(self):
        from lib.risk_scanner import revenue_acceleration_flag

        rows = [
            {"end_date": "2023-03-31", "revenue": 80.0},
            {"end_date": "2024-03-31", "revenue": 100.0},
            {"end_date": "2024-06-30", "revenue": 150.0},
            {"end_date": "2025-03-31", "revenue": 130.0},
            {"end_date": "2025-06-30", "revenue": 210.0},
        ]
        out = revenue_acceleration_flag(rows)
        assert out["triggered"] is True
        assert out["accel_pp"] == 10.0
        assert "2025-03-31=30.0%" in out["detail"]
        assert "2025-06-30=40.0%" in out["detail"]

    def test_revenue_acceleration_flag_requires_two_yoy_pairs(self):
        from lib.risk_scanner import revenue_acceleration_flag

        rows = [
            {"end_date": "2024-03-31", "revenue": 100.0},
            {"end_date": "2025-03-31", "revenue": 130.0},
            {"end_date": "2025-06-30", "revenue": 210.0},
        ]
        out = revenue_acceleration_flag(rows)
        assert out["triggered"] is False
        assert "缺少最近两组可比同比配对" in out["detail"]

    def test_ocf_np_divergence_flag(self):
        from lib.risk_scanner import ocf_np_divergence_flag

        rows = [{"end_date": "20231231", "n_cashflow_act": 1e6, "n_income_attr_p": 5e6}]
        out = ocf_np_divergence_flag(rows)
        assert out["triggered"] is True
        assert "ratio" in out

    def test_ocf_np_divergence_flag_uses_point_six_threshold(self):
        from lib.risk_scanner import ocf_np_divergence_flag

        rows = [{"end_date": "20231231", "n_cashflow_act": 0.55e6, "n_income_attr_p": 1e6}]
        out = ocf_np_divergence_flag(rows)
        assert out["triggered"] is True
        assert out["ratio"] == 0.55

    def test_ocf_np_divergence_flag_accepts_net_profit_field(self):
        from lib.risk_scanner import ocf_np_divergence_flag

        rows = [{"end_date": "20231231", "n_cashflow_act": 0.5e6, "net_profit": 1e6}]
        out = ocf_np_divergence_flag(rows)
        assert out["triggered"] is True
        assert out["ratio"] == 0.5
        assert "缺失" not in out["detail"]

    def test_ocf_np_divergence_flag_preserves_zero_net_profit(self):
        from lib.risk_scanner import ocf_np_divergence_flag

        rows = [{"end_date": "20231231", "n_cashflow_act": 1e6, "net_profit": 0.0}]
        out = ocf_np_divergence_flag(rows)
        assert out["triggered"] is False
        assert "净利润为 0" in out["detail"]
        assert "缺失" not in out["detail"]

    def test_ocf_np_divergence_flag_zero_primary_not_fallthrough(self):
        from lib.risk_scanner import ocf_np_divergence_flag

        # n_income_attr_p=0 must not fall through via `or` to net_profit
        rows = [{
            "end_date": "20231231",
            "n_cashflow_act": 1e6,
            "n_income_attr_p": 0.0,
            "net_profit": 1e6,
        }]
        out = ocf_np_divergence_flag(rows)
        assert out["triggered"] is False
        assert "净利润为 0" in out["detail"]
        assert "ratio" not in out
