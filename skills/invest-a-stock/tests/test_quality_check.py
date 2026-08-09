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
        assert any("金融" in e or "financial" in e for e in result["exemptions"])

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


class TestMetricFcf5y:
    """R12 修复：5 年 FCF 必须用去重叠口径（只取年报 1231 行求和）。

    背景：n_cashflow_act 为财年累计口径，旧实现把 5 条重叠累计行直接相加 →
    同一时期重复计 ~2.75x；季节负 Q1 的公司 5 行累计可能 <0 被误否决。
    """

    def _row(self, end_date: str, ocf: float, capex: float) -> dict:
        return {"end_date": end_date, "n_cashflow_act": ocf, "cap_ex": capex}

    def test_annual_rows_only_no_overlap(self):
        """5 条重叠累计行（2024 年报 + 2025 Q1/H1/3Q/年报）→ 只按年报去重求和。"""
        from lib.quality_check import _metric_fcf_5y

        rows = [
            self._row("20241231", ocf=30.0, capex=10.0),   # FCF 20
            self._row("20250331", ocf=10.0, capex=3.0),
            self._row("20250630", ocf=35.0, capex=6.0),
            self._row("20250930", ocf=60.0, capex=9.0),
            self._row("20251231", ocf=90.0, capex=15.0),   # FCF 75
        ]
        m = _metric_fcf_5y(rows)
        # 旧实现: 20+7+29+51+75=182（同一时期重复计）；新实现: 20+75=95
        assert m["status"] == "pass"
        assert m["value"] == 95.0
        assert "年报期" in m["detail"]
        assert m["type"] == "veto"

    def test_no_false_veto_on_seasonal_negative_q1(self):
        """季节负 Q1 的 5 行重叠累计 <0 → 不再误否决（按年报去重后为正）。"""
        from lib.quality_check import _metric_fcf_5y

        rows = [
            self._row("20241231", ocf=40.0, capex=10.0),   # FCF 30
            self._row("20250331", ocf=-50.0, capex=0.0),
            self._row("20250630", ocf=-30.0, capex=0.0),
            self._row("20250930", ocf=-10.0, capex=0.0),
            self._row("20251231", ocf=-5.0, capex=0.0),    # FCF -5
        ]
        m = _metric_fcf_5y(rows)
        # 旧实现: 30+(-50)+(-30)+(-10)+(-5) = -65 → fail（误否决）
        # 新实现: 30+(-5) = 25 → pass
        assert m["status"] == "pass"
        assert m["value"] == 25.0

    def test_negative_annual_fcf_still_fails(self):
        """多个年报期累计确实为负 → 仍正常否决（修复不放松真实否决）。"""
        from lib.quality_check import _metric_fcf_5y

        rows = [
            self._row("20231231", ocf=20.0, capex=5.0),    # FCF 15
            self._row("20241231", ocf=-10.0, capex=2.0),   # FCF -12
            self._row("20251231", ocf=-30.0, capex=2.0),   # FCF -32
        ]
        m = _metric_fcf_5y(rows)
        assert m["status"] == "fail"
        assert m["value"] == -29.0

    def test_insufficient_annual_rows_skips(self):
        """只有 1 个年报期（其余为累计季度行）→ 标不可得，不硬算。"""
        from lib.quality_check import _metric_fcf_5y

        rows = [
            self._row("20250331", ocf=10.0, capex=2.0),
            self._row("20250630", ocf=35.0, capex=6.0),
            self._row("20250930", ocf=60.0, capex=9.0),
            self._row("20251231", ocf=90.0, capex=15.0),
        ]
        m = _metric_fcf_5y(rows)
        assert m["status"] == "skip"
        assert "年报期" in m["detail"]
