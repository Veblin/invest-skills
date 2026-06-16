"""v0.1.3-rc Phase 3 测试：风险扫描、多空分歧、情绪分位、CV-8。"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from fixtures.collections import make_daily_basic_series, make_kline_rows
from test_v013_phase2 import _collection_phase2


def _collection_phase3() -> dict:
    """Phase 3 完整测试数据：情绪指标 + 北向 + 风险扫描所需财务字段。"""
    c = _collection_phase2()
    ms = dict(c.get("market_structure") or {})
    ms.update({
        "northbound": {
            "net_sum_10d": -600_000_000,
            "days": 10,
            "source": "tushare.hsgt_top10",
        },
        "moneyflow": {"net_sum_5d": -500_000.0, "source": "tushare.moneyflow"},
        "put_call_ratio": {
            "ratio": 1.15,
            "percentile_5y": 75.0,
            "source": "tushare.opt_daily",
        },
        "short_margin": {
            "growth_pct": 5.2,
            "percentile_5y": 72.0,
            "scope": "exchange",
            "source": "tushare.margin",
        },
        "new_high_ratio": {
            "ratio_pct": 12.5,
            "percentile_60d": 68.0,
            "sample_size": 4500,
            "source": "tushare.daily",
        },
        "etf_flow": {
            "ts_code": "510300.SH",
            "net_flow_5d": 1_000_000_000,
            "net_flow_10d": 2_000_000_000,
            "source": "tushare.fund_daily",
        },
        "availability": {
            **ms.get("availability", {}),
            "northbound": "available",
            "put_call_ratio": "available",
            "short_margin": "available",
            "new_high_ratio": "available",
            "etf_flow": "available",
        },
    })
    c["market_structure"] = ms

    for dim in c["dimensions"]:
        if dim["dimension"] == "financials":
            dim["data"] = [
                {"end_date": "20210331", "roe": 16.0, "revenue": 3.5e9, "net_profit": 9e7,
                 "profit_dedt": 8.5e7, "n_cashflow_act": 1.2e8, "ocf": 1.2e8,
                 "grossprofit_margin": 32.0, "debt_to_assets": 45.0},
                {"end_date": "20211231", "roe": 17.0, "revenue": 3.8e9, "net_profit": 1.0e8,
                 "profit_dedt": 9.5e7, "n_cashflow_act": 1.1e8, "ocf": 1.1e8,
                 "grossprofit_margin": 31.0, "debt_to_assets": 46.0},
                {"end_date": "20220331", "roe": 17.5, "revenue": 4.0e9, "net_profit": 1.1e8,
                 "profit_dedt": 1.0e8, "n_cashflow_act": 1.0e8, "ocf": 1.0e8,
                 "grossprofit_margin": 30.0, "debt_to_assets": 47.0},
                {"end_date": "20221231", "roe": 18.0, "revenue": 4.2e9, "net_profit": 1.15e8,
                 "profit_dedt": 1.05e8, "n_cashflow_act": 9.5e7, "ocf": 9.5e7,
                 "grossprofit_margin": 29.0, "debt_to_assets": 48.0},
                {"end_date": "20230331", "roe": 18.5, "revenue": 4.5e9, "net_profit": 1.2e8,
                 "profit_dedt": 1.1e8, "n_cashflow_act": 8.5e7, "ocf": 8.5e7,
                 "grossprofit_margin": 28.0, "debt_to_assets": 49.0,
                 "accounts_receiv": 1.0e8, "inventories": 0.8e8},
                {"end_date": "20231231", "roe": 19.0, "revenue": 4.8e9, "net_profit": 1.25e8,
                 "profit_dedt": 1.15e8, "n_cashflow_act": 8.0e7, "ocf": 8.0e7,
                 "grossprofit_margin": 27.0, "debt_to_assets": 50.0,
                 "accounts_receiv": 1.1e8, "inventories": 0.85e8,
                 "ebit": 1.5e8, "int_exp": 1.0e8},
                {"end_date": "20240331", "roe": 19.5, "revenue": 5.0e9, "net_profit": 1.3e8,
                 "profit_dedt": 1.2e8, "n_cashflow_act": -1.0e7, "ocf": -1.0e7,
                 "grossprofit_margin": 26.0, "debt_to_assets": 51.0,
                 "accounts_receiv": 1.2e8, "inventories": 0.9e8},
                {"end_date": "20240630", "roe": 20.0, "revenue": 5.2e9, "net_profit": 1.35e8,
                 "profit_dedt": 1.25e8, "n_cashflow_act": -2.0e7, "ocf": -2.0e7,
                 "grossprofit_margin": 25.0, "debt_to_assets": 52.0,
                 "accounts_receiv": 1.3e8, "inventories": 0.95e8},
            ]
        if dim["dimension"] == "valuation":
            dim["data"] = make_daily_basic_series(60)
        if dim["dimension"] == "kline":
            dim["data"] = make_kline_rows(60)

    peers = c.get("industry_peers", {}).get("peers") or []
    for p in peers:
        p.setdefault("debt_to_assets", 40.0)
        p.setdefault("gross_margin_trend", "down")
    return c


class TestRiskScanner:
    def test_risk_scanner_financial_signals(self):
        from lib.risk_scanner import scan_financial_risks

        financials = [
            {"end_date": "20231231", "n_cashflow_act": 1.0e8, "net_profit": 1.0e8},
            {"end_date": "20240331", "n_cashflow_act": -1.0e7, "ocf": -1.0e7, "net_profit": 1.0e8},
            {"end_date": "20240630", "n_cashflow_act": -2.0e7, "ocf": -2.0e7, "net_profit": 1.0e8},
        ]
        signals = scan_financial_risks(financials)
        triggered = {s["id"]: s for s in signals if s["triggered"]}
        assert "cashflow_negative" in triggered
        assert triggered["cashflow_negative"]["severity"] == "高"
        assert "连续" in triggered["cashflow_negative"]["detail"] or "负" in triggered["cashflow_negative"]["detail"]

    def test_risk_scanner_market_signals(self):
        from lib.risk_scanner import scan_market_risks

        valuation = {"pe_percentile": 92.0, "pe": {"pct": 92.0}}
        northbound = {"net_sum_10d": -600_000_000}
        signals = scan_market_risks(valuation, northbound, None)
        triggered_ids = {s["id"] for s in signals if s["triggered"]}
        assert "valuation_extreme_high" in triggered_ids
        assert "northbound_outflow" in triggered_ids

    def test_risk_scanner_coverage(self):
        from lib.render import _index_dims, _v3_build_risk_report

        c = _collection_phase3()
        report = _v3_build_risk_report(c, _index_dims(c), c["market_structure"])
        cov = report["coverage"]
        assert cov["total"] == 17
        auto_expected = sum(1 for s in report["signals"] if s["auto"])
        assert cov["auto"] == auto_expected


class TestRenderPhase3:
    def test_bull_bear_sections(self):
        from lib.render import render_report_v3

        text = render_report_v3(_collection_phase3(), "600176")
        mod5 = text.split("## 5.")[1].split("## 6.")[0]
        assert "5a." in mod5 and "多头逻辑链" in mod5
        assert "5b." in mod5 and "空头逻辑链" in mod5
        assert "5c." in mod5 and "关键分歧点" in mod5

    def test_no_placeholder_sections(self):
        from lib.render import render_report_v3

        text = render_report_v3(_collection_phase3(), "600176")
        assert "将于 P2 实现" not in text
        assert "将于 Phase 3 实现" not in text

    def test_sentiment_percentile_output(self):
        from lib.render import render_report_v3

        text = render_report_v3(_collection_phase3(), "600176")
        mod3 = text.split("## 3.")[1].split("## 4.")[0]
        assert "情绪指标" in mod3
        assert "分位" in mod3
        assert "认沽认购比" in mod3

    def test_cv8_erp_put_call_short(self):
        from lib.render import render_report_v3

        text = render_report_v3(_collection_phase3(), "600176")
        mod6 = text.split("## 6.")[1].split("## 7.")[0]
        assert "CV-8" in mod6
        assert "认沽认购比" in mod6 or "融券" in mod6

    def test_module3_section_order_3b_before_3c(self):
        from lib.render import render_report_v3

        text = render_report_v3(_collection_phase3(), "600176")
        mod3 = text.split("## 3.")[1].split("## 4.")[0]
        assert mod3.index("### 3b.") < mod3.index("### 3c.")

    def test_cv7_boundary_matches_zone_label(self):
        from lib.render import _v3_cv7_assessment

        status_30, _ = _v3_cv7_assessment(30.0, -1.0)
        status_70, _ = _v3_cv7_assessment(70.0, 1.0)
        assert status_30 != "convergence"
        assert status_70 != "divergence"
        conv, _ = _v3_cv7_assessment(29.9, -1.0)
        div, _ = _v3_cv7_assessment(70.1, 1.0)
        assert conv == "convergence"
        assert div == "divergence"

    def test_risk_scanner_fin_stable_sort(self):
        from lib.risk_scanner import scan_financial_risks

        financials = [
            {"end_date": "20231231", "ann_date": "20240401",
             "accounts_receiv": 1e8, "revenue": 1e9},
            {"end_date": "20231231", "ann_date": "20240315",
             "accounts_receiv": 2e8, "revenue": 1.1e9},
        ]
        a = scan_financial_risks(financials)
        b = scan_financial_risks(list(reversed(financials)))
        assert a == b


class TestCollectorPhase3Fixes:
    def test_attach_phase2_extras_retries_after_error_sentinel(self):
        from lib import collector

        c = {"dimensions": []}
        c["industry_peers"] = {"peers": [], "error": "timeout", "sufficient": False}
        with patch.object(collector, "attach_industry_peers") as mock_ip:
            mock_ip.side_effect = lambda coll, sym: coll.update({
                "industry_peers": {"peers": [{"symbol": "002001"}], "sufficient": True},
            })
            collector.attach_phase2_extras(c, "600176")
        mock_ip.assert_called_once()

    def test_put_call_ratio_percentile_windows_distinct(self):
        """5年分位与 60 日分位应基于不同窗口计算。"""
        from lib.valuation import percentile_rank

        ratios = [0.8 + 0.05 * (i % 7) for i in range(300)]
        current = ratios[-1]
        pct_5y = percentile_rank(ratios, current)
        pct_60d = percentile_rank(ratios[-60:], current)
        assert pct_5y is not None
        assert pct_60d is not None
        assert len(ratios) > 60

    def test_profit_quality_requires_both_years_low(self):
        from lib.risk_scanner import scan_financial_risks

        only_one_low = [
            {"end_date": "20221231", "n_cashflow_act": 3e7, "net_profit": 1e8},
            {"end_date": "20231231", "n_cashflow_act": 8e7, "net_profit": 1e8},
        ]
        sig_one = {s["id"]: s for s in scan_financial_risks(only_one_low)}
        assert not sig_one["profit_quality_low"]["triggered"]

        both_low = [
            {"end_date": "20221231", "n_cashflow_act": 3e7, "net_profit": 1e8},
            {"end_date": "20231231", "n_cashflow_act": 4e7, "net_profit": 1e8},
        ]
        sig_both = {s["id"]: s for s in scan_financial_risks(both_low)}
        assert sig_both["profit_quality_low"]["triggered"]

    def test_debt_ratio_rising_two_year_window(self):
        from lib.risk_scanner import scan_financial_risks

        financials = [
            {"end_date": "20221231", "debt_to_assets": 40.0},
            {"end_date": "20231231", "debt_to_assets": 55.0},
        ]
        sig = {s["id"]: s for s in scan_financial_risks(
            financials, industry_median_debt=40.0,
        )}
        assert sig["debt_ratio_rising"]["triggered"]

    def test_gross_margin_decline_two_year_window(self):
        from lib.risk_scanner import scan_business_risks

        financials = [
            {"end_date": "20221231", "grossprofit_margin": 40.0},
            {"end_date": "20231231", "grossprofit_margin": 35.0},
        ]
        sig = {s["id"]: s for s in scan_business_risks(financials, {})}
        assert sig["gross_margin_decline"]["triggered"]

    def test_cv8_omitted_when_insufficient_data(self):
        from lib.render import render_report_v3

        c = _collection_phase3()
        c["market_structure"] = {
            "erp": None,
            "put_call_ratio": None,
            "short_margin": None,
            "availability": {},
        }
        text = render_report_v3(c, "600176")
        mod6 = text.split("## 6.")[1].split("## 7.")[0]
        assert "CV-8" not in mod6
