"""Tests for lib.financial_rigor (v0.1.9)."""

from __future__ import annotations


def _sample_collection() -> dict:
    return {
        "symbol": "600176",
        "dimensions": [
            {
                "dimension": "quote",
                "data": {"price": 10.0, "total_mv": 400.0, "pe_ratio": 20.0},
                "_meta": {
                    "all_sources": [
                        {"source": "a", "success": True, "data": {"close": 10.0}},
                        {"source": "b", "success": True, "data": {"close": 10.6}},
                    ],
                },
            },
            {
                "dimension": "basic_info",
                "data": {"name": "测试", "总股本": "400000万"},
            },
            {
                "dimension": "financials",
                "data": [
                    {
                        "end_date": "20231231",
                        "n_income_attr_p": 2e8,
                        "total_hldr_eqy_exc_min_int": 2e9,
                        "roe": 10.0,
                        "n_cashflow_act": 1.5e8,
                    },
                ],
            },
        ],
    }


class TestFinancialRigor:
    def test_verify_market_cap(self):
        from lib.financial_rigor import verify_market_cap

        reports = verify_market_cap(_sample_collection())
        assert reports
        assert reports[0].command == "verify-market-cap"

    def test_cross_validate_fail_threshold(self):
        from lib.financial_rigor import cross_validate, FAIL_THRESHOLD_PCT

        reports = cross_validate(_sample_collection())
        assert any(r.command == "cross-validate" for r in reports)
        quote_cv = next((r for r in reports if r.field == "quote"), None)
        if quote_cv and quote_cv.deviation_pct > FAIL_THRESHOLD_PCT:
            assert quote_cv.status == "fail"

    def test_calc_decimal(self):
        from lib.financial_rigor import calc

        r = calc("0.1 + 0.2")
        assert r.status == "pass"
        assert float(r.computed_value) == 0.3

    def test_run_rigor_all(self):
        from lib.financial_rigor import run_rigor

        reports = run_rigor(_sample_collection(), ["verify-market-cap", "verify-valuation", "cross-validate"])
        cmds = {r.command for r in reports}
        assert "verify-market-cap" in cmds
        assert "verify-valuation" in cmds
        assert "cross-validate" in cmds

    def test_pe_skips_non_annual(self):
        from lib.financial_rigor import verify_valuation

        coll = _sample_collection()
        coll["dimensions"][2]["data"][0]["end_date"] = "20240331"
        reports = verify_valuation(coll)
        pe = next(r for r in reports if r.field == "pe_ttm")
        assert pe.status == "warn"
        assert pe.computed_value is None
        assert "非年报" in pe.detail

    def test_pe_verifies_annual(self):
        from lib.financial_rigor import verify_valuation

        reports = verify_valuation(_sample_collection())
        pe = next(r for r in reports if r.field == "pe_ttm")
        assert pe.computed_value is not None
        assert pe.status in ("pass", "warn", "fail")
