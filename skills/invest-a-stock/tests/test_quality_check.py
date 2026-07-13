"""Tests for lib.quality_check (v0.1.9)."""

from __future__ import annotations

from unittest.mock import patch


def _fin_collection(rows: list[dict], industry: str = "制造业", list_date: str = "20150101") -> dict:
    return {
        "symbol": "600176",
        "dimensions": [
            {"dimension": "basic_info", "data": {"industry": industry, "list_date": list_date}},
            {"dimension": "financials", "data": rows},
        ],
    }


class TestQualityCheck:
    def test_ocf_np_warning(self):
        from lib.quality_check import run_quality_check

        rows = [{
            "end_date": "20231231",
            "n_income_attr_p": 100,
            "n_cashflow_act": 40,
            "revenue": 1000,
            "ebit": 50,
            "fin_exp_int_exp": 10,
            "grossprofit_margin": 20,
        }]
        result = run_quality_check(_fin_collection(rows))
        m5 = next(m for m in result["metrics"] if m["id"] == 5)
        assert m5["status"] == "warn"

    def test_exemption_financial(self):
        from lib.quality_check import run_quality_check

        rows = [{"end_date": "20231231", "n_income_attr_p": -1, "revenue": 1}]
        result = run_quality_check(_fin_collection(rows, industry="银行"))
        assert any("行业特殊" in e for e in result["exemptions"])

    def test_disclaimer_present(self):
        from lib.quality_check import run_quality_check

        result = run_quality_check(_fin_collection([]))
        assert "启发式" in result["disclaimer"]

    def test_roic_decimal_converted_to_pct(self):
        from lib.quality_check import _metric_roic

        with patch("lib.quality_check._score_roic_trend") as mock:
            mock.return_value = (
                25.0,
                {"metric": "ROIC", "series": [0.12, 0.15, 1.20]},
                ["ebit"],
                "",
            )
            m = _metric_roic([{}])
        assert m["status"] == "pass"
        assert m["value"] > 5.0

    def test_roe_proxy_not_double_multiplied(self):
        from lib.quality_check import _metric_roic

        with patch("lib.quality_check._score_roic_trend") as mock:
            mock.return_value = (
                15.0,
                {"metric": "代理指标: ROE", "series": [10.0, 12.0, 14.0]},
                ["roe"],
                "",
            )
            m = _metric_roic([{}])
        assert m["value"] == 12.0

    def test_transform_exemption_skips_veto(self):
        from lib.quality_check import run_quality_check

        rows = [
            {"end_date": "20201231", "revenue": 100, "n_income_attr_p": 10,
             "n_cashflow_act": -50, "ebit": 1, "fin_exp_int_exp": 10},
            {"end_date": "20211231", "revenue": 110, "n_income_attr_p": 11,
             "n_cashflow_act": -50, "ebit": 1, "fin_exp_int_exp": 10},
            {"end_date": "20221231", "revenue": 120, "n_income_attr_p": 12,
             "n_cashflow_act": -50, "ebit": 1, "fin_exp_int_exp": 10},
            {"end_date": "20231231", "revenue": 200, "n_income_attr_p": 20,
             "n_cashflow_act": -50, "ebit": 1, "fin_exp_int_exp": 10},
        ]
        result = run_quality_check(_fin_collection(rows))
        assert any("转型期" in e for e in result["exemptions"])
        veto_fails = [
            m for m in result["metrics"]
            if m.get("type") == "veto" and m.get("status") == "fail"
        ]
        assert not veto_fails
        assert any(m.get("status") == "exempted" for m in result["metrics"])

    def test_fin_rows_sorted_ascending(self):
        from lib.quality_check import _sorted_fin_rows

        coll = _fin_collection([
            {"end_date": "20231231", "revenue": 3},
            {"end_date": "20211231", "revenue": 1},
            {"end_date": "20221231", "revenue": 2},
        ])
        rows = _sorted_fin_rows(coll)
        assert [r["revenue"] for r in rows] == [1, 2, 3]
