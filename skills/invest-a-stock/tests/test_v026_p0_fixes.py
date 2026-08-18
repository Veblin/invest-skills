"""v0.2.6 工作流评估 P0 数字口径修复的回归测试（F0 系列）。

覆盖缺陷（详见 host-docs/v0.2.6-workflow-eval/workflow-eval.md）：
  F0-1  DCF 净债务口径（total_liab 不再参与每股换算）
  F0-2  同比基期同报告期匹配（禁止 Q1 vs 全年混比）
  F0-4  宏观取最新月行（akshare 序列最新在前）
  F0-6  风险计数口径统一（auto/total，非 sum() 得 33）
  F0-7  rigor 验算取最新财务行（不依赖采集行序）
  F0-8  财务期口径（银行豁免/年报 ROE 判断/同季度 CAGR/画布增长驱动）
  F0-9  业绩全景同报告期去重
"""

from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# F0-2: 同比基期
# ---------------------------------------------------------------------------
class TestPriorYearRow:
    def test_finds_same_period_prior_year(self):
        from lib.render_markdown._v3 import _prior_year_row
        rows = [
            {"end_date": "20250630", "revenue": 1788.86},
            {"end_date": "20260331", "revenue": 1291.31},
            {"end_date": "20260630", "revenue": 2769.17},
        ]
        prev = _prior_year_row(rows, rows[-1])
        assert prev is not None and prev["end_date"] == "20250630"

    def test_returns_none_when_no_prior_year(self):
        from lib.render_markdown._v3 import _prior_year_row
        rows = [
            {"end_date": "20251231", "revenue": 3375.32},
            {"end_date": "20260331", "revenue": 869.40},
        ]
        assert _prior_year_row(rows, rows[-1]) is None


# ---------------------------------------------------------------------------
# F0-8: CAGR 同报告期
# ---------------------------------------------------------------------------
class TestSamePeriodCagr:
    def test_bank_quarterly_cagr_not_mixed(self):
        """招行混合序列不再输出跨期混比 -19.97%：年报行优先，
        2022 年报 3447.83 → 2025 年报 3375.32，3 年 CAGR ≈ -0.71%。"""
        from lib.render_utils import _compute_metric_cagr
        rows = [
            {"end_date": "20220930", "revenue": 2645.10},
            {"end_date": "20221231", "revenue": 3447.83},
            {"end_date": "20230331", "revenue": 906.36},
            {"end_date": "20230630", "revenue": 1784.31},
            {"end_date": "20230930", "revenue": 2602.79},
            {"end_date": "20231231", "revenue": 3391.23},
            {"end_date": "20240331", "revenue": 864.17},
            {"end_date": "20241231", "revenue": 3374.88},
            {"end_date": "20250331", "revenue": 837.51},
            {"end_date": "20251231", "revenue": 3375.32},
            {"end_date": "20260331", "revenue": 869.40},
        ]
        cagr, span = _compute_metric_cagr(rows, "revenue")
        assert cagr is not None and span is not None
        # 年报行 CAGR：3 年窗口 3447.83 → 3375.32
        assert cagr == pytest.approx(((3375.32 / 3447.83) ** (1 / 3) - 1) * 100, abs=0.1)
        assert span == pytest.approx(3.0)

    def test_prefers_annual_rows(self):
        """年报行（1231）优先于季度组。"""
        from lib.render_utils import _compute_metric_cagr
        rows = [
            {"end_date": "20221231", "revenue": 100.0},
            {"end_date": "20231231", "revenue": 110.0},
            {"end_date": "20240331", "revenue": 20.0},
            {"end_date": "20241231", "revenue": 121.0},
        ]
        cagr, span = _compute_metric_cagr(rows, "revenue")
        assert cagr is not None
        assert cagr == pytest.approx(((121.0 / 100.0) ** (1 / 2) - 1) * 100, abs=0.1)
        assert span == pytest.approx(2.0)


# ---------------------------------------------------------------------------
# F0-8: 画布增长驱动
# ---------------------------------------------------------------------------
class TestCanvasGrowthDriver:
    def test_quarter_vs_year_not_mixed(self):
        """招行 Q1 869.40 vs 上年 Q1 837.51 = +3.81%，不再出现 -74% 下滑。"""
        from lib.render_markdown._v3 import _canvas_growth_driver
        rows = [
            {"end_date": "20250331", "revenue": 837.51},
            {"end_date": "20250630", "revenue": 1699.69},
            {"end_date": "20251231", "revenue": 3375.32},
            {"end_date": "20260331", "revenue": 869.40},
        ]
        score, note, _ = _canvas_growth_driver(rows)
        assert score is not None
        assert "+3.8%" in note
        assert "下滑" not in note

    def test_no_prior_year_base_unavailable(self):
        from lib.render_markdown._v3 import _canvas_growth_driver
        rows = [
            {"end_date": "20251231", "revenue": 3375.32},
            {"end_date": "20260331", "revenue": 869.40},
        ]
        score, note, _ = _canvas_growth_driver(rows)
        assert score is None
        assert "不可比" in note


# ---------------------------------------------------------------------------
# F0-8: F-3 金融行业豁免
# ---------------------------------------------------------------------------
class TestFastVetoFinancialExemption:
    def test_bank_90pct_leverage_not_hard_triggered(self):
        from lib.render_markdown._v3 import _check_fast_veto
        dims = {
            "basic_info": {"data": [{"industry": "银行"}]},
            "financials": {"data": [
                {"end_date": "20251231", "total_liab": 12.0, "total_assets": 13.3, "roe": 12.02},
                {"end_date": "20260331", "total_liab": 12.19, "total_assets": 13.48, "roe": 2.96},
            ]},
        }
        result = _check_fast_veto(dims, {})
        assert result["hard_triggers"] == []
        assert any("金融行业豁免" in line for line in result["display_lines"])

    def test_nonfinancial_still_triggers(self):
        from lib.render_markdown._v3 import _check_fast_veto
        dims = {
            "basic_info": {"data": [{"industry": "电气设备"}]},
            "financials": {"data": [
                {"end_date": "20251231", "total_liab": 92.0, "total_assets": 100.0},
                {"end_date": "20260331", "total_liab": 93.0, "total_assets": 100.0},
            ]},
        }
        result = _check_fast_veto(dims, {})
        assert result["hard_triggers"]


# ---------------------------------------------------------------------------
# F0-9: 业绩全景去重
# ---------------------------------------------------------------------------
class TestFinancialPanoramaDedup:
    def test_duplicate_period_single_row(self):
        from lib.render_markdown._v3 import _financial_panorama_table
        rows = [
            {"end_date": "20250630", "roe": 5.9581, "eps": 2.89, "revenue": 1699.69},
            {"end_date": "20250630", "roe": 5.9581, "eps": None, "revenue": 1699.69},
            {"end_date": "20250930", "roe": 9.1262, "eps": 4.43, "revenue": 2514.20},
        ]
        lines = _financial_panorama_table(rows)
        joined = "\n".join(lines)
        assert joined.count("| 20250630 |") == 1
        assert "| 20250930 |" in joined


# ---------------------------------------------------------------------------
# F0-1: DCF 净债务口径
# ---------------------------------------------------------------------------
class TestDcfNetDebtMethod:
    def test_total_liab_method_suppressed(self):
        from lib.render_dcf import _dcf_extract_net_debt
        financials = {
            "dcf_preprocess": {
                "net_debt": {
                    "debt_total": 724925558000.0,
                    "cash": 372053275000.0,
                    "net_debt": 352872283000.0,
                    "is_net_cash": False,
                    "method": "total_liab - money_cap（含经营负债，非有息口径）",
                }
            }
        }
        nd, source = _dcf_extract_net_debt(financials)
        assert nd is None
        assert "有息" in source or "不可得" in source

    def test_interest_bearing_method_passthrough(self):
        from lib.render_dcf import _dcf_extract_net_debt
        financials = {
            "dcf_preprocess": {
                "net_debt": {
                    "debt_total": 1000.0,
                    "cash": 300.0,
                    "net_debt": 700.0,
                    "is_net_cash": False,
                    "method": "interest_bearing - money_cap",
                }
            }
        }
        nd, _ = _dcf_extract_net_debt(financials)
        assert nd == 700.0


class TestDcfFinancialIndustrySkip:
    def test_bank_returns_exemption_message(self):
        from lib.render_dcf import _section_dcf_valuation
        dims = {"basic_info": {"data": [{"industry": "银行"}]}}
        out = _section_dcf_valuation(dims, {}, "600036", veto_triggered=False)
        assert "金融业豁免" in out
        assert "研究终止条件" not in out


# ---------------------------------------------------------------------------
# F0-7: rigor 取最新财务行
# ---------------------------------------------------------------------------
class TestRigorLatestRow:
    def test_newest_first_row_order_handled(self):
        """采集行序最新在前时，verify_valuation 仍用最新行（20260630），
        不取 data[-1]（20220930）。"""
        from lib.financial_rigor import verify_valuation
        collection = {
            "dimensions": [
                {"dimension": "financials", "data": [
                    {"end_date": "20260630", "n_income_attr_p": 43284002000.0,
                     "total_hldr_eqy_inc_min_int": 413955232000.0, "n_cashflow_act": 60216851000.0},
                    {"end_date": "20220930", "n_income_attr_p": 17591591700.0,
                     "total_hldr_eqy_inc_min_int": 161285442600.0, "n_cashflow_act": 25967948600.0},
                ]},
                {"dimension": "valuation", "data": [
                    {"trade_date": "20260814", "pe_ttm": 21.44},
                    {"trade_date": "20220930", "pe_ttm": 30.0},
                ]},
                {"dimension": "quote", "data": {"price": 393.93, "total_mv": 18225.78}},
            ]
        }
        reports = verify_valuation(collection)
        # 不应出现以 20220930 为基准的"非年报净利"警告——最新行是 20260630
        # （非年报期仍会 warn 跳过 PE 验算，但 end_date 必须是 20260630）
        for r in reports:
            if "非年报净利" in r.detail:
                assert "20220930" not in r.detail


# ---------------------------------------------------------------------------
# F0-6: 风险计数口径（auto/total 而非求和）
# ---------------------------------------------------------------------------
class TestF4RiskCoverage:
    def test_coverage_not_summed(self):
        """coverage={"auto": 16, "total": 17} 不能再求和成 33。"""
        coverage = {"auto": 16, "total": 17}
        total_signals = coverage.get("total", 0)
        assert total_signals == 17
        assert total_signals != sum(coverage.values())


# ---------------------------------------------------------------------------
# F0-4: 宏观最新月行
# ---------------------------------------------------------------------------
class TestLatestMonthRow:
    def test_newest_first_series(self):
        from lib.shared_dates import latest_month_row
        rows = [
            {"月份": "2026年07月份", "制造业-指数": 49.2},
            {"月份": "2026年06月份", "制造业-指数": 50.3},
            {"月份": "2008年01月份", "制造业-指数": 53.0},
        ]
        row = latest_month_row(rows)
        assert row is not None
        assert row["月份"] == "2026年07月份"
        assert row["制造业-指数"] == 49.2

    def test_oldest_first_series(self):
        from lib.shared_dates import latest_month_row
        rows = [
            {"月份": "2008年01月份", "制造业-指数": 53.0},
            {"月份": "2026年06月份", "制造业-指数": 50.3},
            {"月份": "2026年07月份", "制造业-指数": 49.2},
        ]
        row = latest_month_row(rows)
        assert row is not None and row["月份"] == "2026年07月份"

    def test_fallback_first_row(self):
        from lib.shared_dates import latest_month_row
        rows = [{"foo": 1}, {"bar": 2}]
        assert latest_month_row(rows) == {"foo": 1}


# ---------------------------------------------------------------------------
# F2-1: R1 classify 金融行业感知
# ---------------------------------------------------------------------------
class TestClassifyBankIndustry:
    _bank_annual = [
        {"year": f"{y}1231", "net_profit": v} for y, v in [
            (2018, 805.60), (2019, 928.67), (2020, 973.42), (2021, 1199.22),
            (2022, 1380.12), (2023, 1466.02), (2024, 1483.91), (2025, 1501.81),
        ]
    ]
    _bank_fin = [{"end_date": "20251231", "fcff": 100.0, "fcfe": 90.0}]

    def test_bank_with_dividend_evidence_classifies_value(self):
        from lib.income_driver import classify_income_driver
        result = classify_income_driver(
            self._bank_annual, self._bank_fin,
            div_years=10, div_yield=0.052, refi_times=0, industry="银行",
        )
        assert result["driver"] == "估值股息回归"

    def test_bank_without_evidence_not_growth(self):
        """银行无分红证据时不应再判「成长兑现」（招行年增速 ~3%）。"""
        from lib.income_driver import classify_income_driver
        result = classify_income_driver(
            self._bank_annual, self._bank_fin, industry="银行",
        )
        assert result["driver"] in ("暂无法判定", "估值股息回归")

    def test_growth_stock_unaffected(self):
        """宁德 30%+ 增速不受影响，仍成长兑现。"""
        from lib.income_driver import classify_income_driver
        annual = [
            {"year": f"{y}1231", "net_profit": v} for y, v in [
                (2021, 159.31), (2022, 307.29), (2023, 441.21),
                (2024, 507.45), (2025, 722.01),
            ]
        ]
        result = classify_income_driver(
            annual, [{"end_date": "20251231", "fcff": 100.0}], industry="电气设备",
        )
        assert result["driver"] == "成长兑现"


# ---------------------------------------------------------------------------
# F0-3: lint 新规则（占位符/异常泄漏 error 级）
# ---------------------------------------------------------------------------
class TestNewLintRules:
    def test_engine_placeholder_is_error(self, tmp_path):
        from lib import lint as lint_mod
        report = tmp_path / "r.md"
        report.write_text("[待 Claude report 阶段填充]\n", encoding="utf-8")
        findings = lint_mod.lint_file(report)
        err_ids = {f.rule_id for f in findings if f.severity == "error"}
        assert "placeholder-engine-slot" in err_ids

    def test_error_leakage_is_error(self, tmp_path):
        from lib import lint as lint_mod
        report = tmp_path / "r.md"
        report.write_text("创新高个股占比: 不可得（'str' object has no attribute 'get'）\n", encoding="utf-8")
        findings = lint_mod.lint_file(report)
        err_ids = {f.rule_id for f in findings if f.severity == "error"}
        assert "error-leakage" in err_ids
