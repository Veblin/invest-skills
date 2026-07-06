"""v0.1.8 统一测试文件（Step 1~10 各步骤测试用例汇总，逐步追加）。

Step 1 覆盖: scoring.py 5 个评分函数（正常路径 + 数据不足路径）。
Step 3 覆盖: valuation.py 4 个 DCF 函数（dcf_two_stage/dcf_sensitivity/
             scenario_fcff/triangle_check，正常路径 + 边界 + 数据不足路径）。
Step 4 覆盖: render.py _section_dcf_valuation()（D-④/D-⑤/D-⑥ 渲染，
             正常路径 + WACC 数据不足路径 + veto_triggered 跳过路径）。
"""

from __future__ import annotations

import pytest

from lib.scoring import (
    confidence_matrix,
    customer_lockin_score,
    insider_signal,
    management_ability_proxy,
    revenue_quality_score,
)
from lib.valuation import (
    attach_dcf_preprocess,
    dcf_sensitivity,
    dcf_two_stage,
    scenario_fcff,
    triangle_check,
)

FORBIDDEN_WORDS = ("买入", "卖出", "建仓", "目标价", "极度高估", "极度低估", "崩盘")


def _check_no_forbidden_words(obj) -> None:
    """递归检查 dict/list/str 中不含合规禁用词。"""
    if isinstance(obj, str):
        for w in FORBIDDEN_WORDS:
            assert w not in obj, f"发现禁用词 {w!r}: {obj!r}"
    elif isinstance(obj, dict):
        for v in obj.values():
            _check_no_forbidden_words(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            _check_no_forbidden_words(v)


def _make_financials_row(
    end_date: str,
    *,
    revenue: float = 100.0,
    accounts_receiv: float = 10.0,
    grossprofit_margin: float = 40.0,
    netprofit_margin: float = 15.0,
    net_profit: float = 15.0,
    ocf: float | None = None,
    n_cashflow_act: float | None = None,
    ebit: float = 20.0,
    total_assets: float = 200.0,
    total_cur_liab: float = 50.0,
    cap_ex: float = 5.0,
    roe: float = 12.0,
    income_tax: float = 3.0,
    total_profit: float = 18.0,
) -> dict:
    row = {
        "end_date": end_date,
        "revenue": revenue,
        "accounts_receiv": accounts_receiv,
        "grossprofit_margin": grossprofit_margin,
        "netprofit_margin": netprofit_margin,
        "net_profit": net_profit,
        "ebit": ebit,
        "total_assets": total_assets,
        "total_cur_liab": total_cur_liab,
        "cap_ex": cap_ex,
        "roe": roe,
        "income_tax": income_tax,
        "total_profit": total_profit,
    }
    if n_cashflow_act is not None:
        row["n_cashflow_act"] = n_cashflow_act
    if ocf is not None:
        row["ocf"] = ocf
    return row


def _make_growing_financials(n: int = 6) -> list[dict]:
    """构造一系列营收/利润/ROIC/毛利率稳步改善的季度财报（正常路径 mock）。"""
    rows = []
    base_year = 2023
    for i in range(n):
        year = base_year + i // 4
        quarter_end = ["0331", "0630", "0930", "1231"][i % 4]
        rows.append(
            _make_financials_row(
                f"{year}{quarter_end}",
                revenue=100.0 + i * 15.0,
                accounts_receiv=10.0 + i * 1.0,
                grossprofit_margin=45.0 + (i % 2) * 0.5,
                netprofit_margin=15.0 + i * 0.5,
                net_profit=15.0 + i * 3.0,
                n_cashflow_act=18.0 + i * 3.0,
                ebit=20.0 + i * 4.0,
                total_assets=200.0 + i * 10.0,
                total_cur_liab=50.0,
                cap_ex=5.0,
                roe=10.0 + i * 1.0,
            )
        )
    return rows


def _make_holder_changes(records: list[dict]) -> dict:
    return {"dimension": "holder_changes", "data": records, "status": "available"}


class TestRevenueQualityScore:
    def test_normal_path(self):
        financials = _make_growing_financials(6)
        result = revenue_quality_score(financials)
        assert result["score"] is not None
        assert 0 <= result["score"] <= 100
        assert result["partial"] is True  # 递延收入恒定缺失
        assert any("deferred_rev" in item for item in result["insufficient_data"])
        assert "sources" in result and isinstance(result["sources"], list)
        assert "revenue" in result["sources"] or "grossprofit_margin" in result["sources"]
        _check_no_forbidden_words(result)

    def test_insufficient_data_path(self):
        """单期数据（无法计算任何子信号）不应抛异常，且明确标注数据不足。"""
        financials = [_make_financials_row("20250630")]
        result = revenue_quality_score(financials)
        assert result["score"] is None
        assert len(result["insufficient_data"]) >= 3  # 三个子信号 + deferred_rev
        _check_no_forbidden_words(result)

    def test_empty_input(self):
        result = revenue_quality_score([])
        assert result["score"] is None
        assert result["partial"] is True
        _check_no_forbidden_words(result)

    def test_none_input_does_not_raise(self):
        result = revenue_quality_score(None)  # type: ignore[arg-type]
        assert result["score"] is None


class TestCustomerLockinScore:
    def test_normal_path(self):
        financials = _make_growing_financials(6)
        result = customer_lockin_score(financials)
        assert result["score"] is not None
        assert 0 <= result["score"] <= 100
        assert "grossprofit_margin" in result["sources"]
        _check_no_forbidden_words(result)

    def test_strong_lockin_case(self):
        """高毛利 + 稳定毛利率 + 高应收周转 → 分数应显著偏高。"""
        rows = [
            _make_financials_row(f"2025{q}", revenue=1000.0, accounts_receiv=20.0, grossprofit_margin=60.0)
            for q in ("0331", "0630", "0930", "1231")
        ]
        result = customer_lockin_score(rows)
        assert result["score"] is not None
        assert result["score"] >= 70

    def test_insufficient_data_path(self):
        result = customer_lockin_score([])
        assert result["score"] is None
        assert result["partial"] is True
        assert len(result["insufficient_data"]) == 3
        _check_no_forbidden_words(result)


class TestInsiderSignal:
    def test_strong_positive(self):
        records = [
            {"ann_date": "20250101", "holder_name": "股东甲", "direction": "增持", "source": "tushare"},
            {"ann_date": "20250201", "holder_name": "股东乙", "direction": "增持", "source": "akshare"},
            {"ann_date": "20250301", "holder_name": "股东丙", "direction": "增持", "source": "tushare"},
        ]
        signal = insider_signal(_make_holder_changes(records))
        assert signal == "强正向"

    def test_strong_negative(self):
        records = [
            {"ann_date": "20250101", "holder_name": "股东甲", "direction": "减持", "source": "tushare"},
            {"ann_date": "20250201", "holder_name": "股东乙", "direction": "减持", "source": "akshare"},
            {"ann_date": "20250301", "holder_name": "股东丙", "direction": "减持", "source": "tushare"},
        ]
        signal = insider_signal(_make_holder_changes(records))
        assert signal == "强负向"

    def test_divergence(self):
        records = [
            {"ann_date": "20250101", "holder_name": "股东甲", "direction": "增持", "source": "tushare"},
            {"ann_date": "20250201", "holder_name": "股东乙", "direction": "减持", "source": "tushare"},
        ]
        signal = insider_signal(_make_holder_changes(records))
        assert signal == "分歧"

    def test_insufficient_data_empty(self):
        assert insider_signal({}) == "数据不足"
        assert insider_signal({"data": []}) == "数据不足"
        assert insider_signal(None) == "数据不足"  # type: ignore[arg-type]

    def test_insufficient_data_bad_dates(self):
        records = [{"ann_date": "不是日期", "holder_name": "股东甲", "direction": "增持"}]
        assert insider_signal(_make_holder_changes(records)) == "数据不足"

    def test_result_is_string_label(self):
        records = [
            {"ann_date": "20250101", "holder_name": "股东甲", "direction": "增持", "source": "tushare"},
        ]
        signal = insider_signal(_make_holder_changes(records))
        assert signal in ("强正向", "正向", "分歧", "负向", "强负向", "数据不足")


class TestManagementAbilityProxy:
    def test_normal_path(self):
        financials = _make_growing_financials(6)
        holder_changes = _make_holder_changes([
            {"ann_date": "20250101", "holder_name": "股东甲", "direction": "增持", "source": "tushare"},
            {"ann_date": "20250201", "holder_name": "股东乙", "direction": "增持", "source": "akshare"},
            {"ann_date": "20250301", "holder_name": "股东丙", "direction": "增持", "source": "tushare"},
        ])
        result = management_ability_proxy(financials, holder_changes)
        assert result["score"] is not None
        assert 0 <= result["score"] <= 100
        assert "定量代理评分" in result["note"]
        assert "置信度中等" in result["note"]
        _check_no_forbidden_words(result)

    def test_insufficient_data_path(self):
        result = management_ability_proxy([], {})
        assert result["score"] is None
        assert result["partial"] is True
        assert len(result["insufficient_data"]) == 4  # 4 个子信号全部缺失
        assert "定量代理评分" in result["note"]
        _check_no_forbidden_words(result)

    def test_roe_fallback_when_roic_fields_missing(self):
        """ebit/total_assets/total_cur_liab 缺失时应回退到 roe 代理指标，而非抛异常或虚构 ROIC。"""
        rows = []
        for i, q in enumerate(("0331", "0630", "0930", "1231")):
            rows.append({
                "end_date": f"2025{q}",
                "revenue": 100.0 + i * 10,
                "net_profit": 10.0,
                "grossprofit_margin": 40.0,
                "netprofit_margin": 15.0,
                "roe": 8.0 + i * 1.5,
            })
        result = management_ability_proxy(rows, {})
        assert result["detail"]["roic_trend"]["metric"] == "代理指标: ROE"
        _check_no_forbidden_words(result)


class TestConfidenceMatrix:
    def _dim(self, status: str, multi_source: bool = False) -> dict:
        return {"status": status, "_meta": {"multi_source": multi_source}}

    def test_normal_path(self):
        collection = {
            "financials": self._dim("available", multi_source=True),
            "holder_changes": self._dim("available"),
            "research": self._dim("partial"),
            "industry": self._dim("missing"),
            "northbound": self._dim("available", multi_source=True),
            "kline": self._dim("available"),
        }
        result = confidence_matrix(collection)
        assert len(result["rows"]) == 8
        modules = {r["module"]: r["confidence"] for r in result["rows"]}
        assert modules["财务数据分析"] == "高"
        assert modules["行业与产业链分析"] == "低"
        assert modules["估值判断"] == "中/低"
        assert modules["周期拐点判断"] == "低"
        _check_no_forbidden_words(result)

    def test_empty_collection_does_not_raise(self):
        result = confidence_matrix({})
        assert len(result["rows"]) == 8
        for r in result["rows"]:
            assert r["confidence"] in ("高", "中", "低", "中/低")
        # 全部维度缺失 → 除固定项外均应为低置信度
        modules = {r["module"]: r["confidence"] for r in result["rows"]}
        assert modules["财务数据分析"] == "低"
        assert modules["周期拐点判断"] == "低"
        _check_no_forbidden_words(result)

    def test_none_input_does_not_raise(self):
        result = confidence_matrix(None)  # type: ignore[arg-type]
        assert len(result["rows"]) == 8

    def test_valuation_never_high_confidence(self):
        """估值判断/周期拐点判断固定为中低置信度，不随数据完整性提升（学术共识）。"""
        collection = {k: self._dim("available", multi_source=True) for k in
                      ("financials", "holder_changes", "research", "industry", "northbound", "kline")}
        result = confidence_matrix(collection)
        modules = {r["module"]: r["confidence"] for r in result["rows"]}
        assert modules["估值判断"] != "高"
        assert modules["周期拐点判断"] != "高"


# ═══════════════════════════════════════════════════════════════
# Step 3: valuation.py DCF 函数测试
# ═══════════════════════════════════════════════════════════════


def _make_dcf_financials_rows(n_years: int = 4) -> list[dict]:
    """构造 n_years 期年报记录（营收稳步增长，供 scenario_fcff 正常路径使用）。"""
    rows = []
    base_revenue = 1000.0
    for i in range(n_years):
        year = 2022 + i
        revenue = base_revenue * (1.15 ** i)
        rows.append({
            "end_date": f"{year}1231",
            "revenue": round(revenue, 2),
            "ebit": round(revenue * 0.18, 2),
            "grossprofit_margin": 35.0 + i * 0.3,
            "cap_ex": round(revenue * 0.06, 2),
            "depr_amort": round(revenue * 0.03, 2),
            "income_tax": round(revenue * 0.18 * 0.25, 2),
            "total_profit": round(revenue * 0.18, 2),
        })
    return rows


def _make_dcf_financials(n_years: int = 4) -> dict:
    return {"dimension": "financials", "data": _make_dcf_financials_rows(n_years), "status": "available"}


class TestDcfTwoStage:
    def test_normal_path(self):
        result = dcf_two_stage(fcff_base=100.0, growth_s1=0.10, years=5, wacc=0.09, terminal_g=0.03)
        assert "error" not in result
        assert result["enterprise_value"] > 0
        assert len(result["yearly_fcff"]) == 5
        assert result["yearly_fcff"][0]["year"] == 1
        # 每股价值/目标价字段绝不应出现
        assert "per_share" not in result
        assert "target_price" not in result
        _check_no_forbidden_words(result)

    def test_wacc_equal_terminal_g_boundary(self):
        result = dcf_two_stage(fcff_base=100.0, growth_s1=0.10, years=5, wacc=0.05, terminal_g=0.05)
        assert result == {"error": "WACC 必须大于永续增长率"}

    def test_wacc_less_than_terminal_g(self):
        result = dcf_two_stage(fcff_base=100.0, growth_s1=0.10, years=5, wacc=0.03, terminal_g=0.05)
        assert "error" in result

    def test_invalid_years(self):
        result = dcf_two_stage(fcff_base=100.0, growth_s1=0.10, years=0, wacc=0.09, terminal_g=0.03)
        assert "error" in result

    def test_enterprise_value_increases_with_lower_wacc(self):
        higher_wacc = dcf_two_stage(fcff_base=100.0, growth_s1=0.08, years=5, wacc=0.11, terminal_g=0.03)
        lower_wacc = dcf_two_stage(fcff_base=100.0, growth_s1=0.08, years=5, wacc=0.09, terminal_g=0.03)
        assert lower_wacc["enterprise_value"] > higher_wacc["enterprise_value"]


class TestDcfSensitivity:
    def test_normal_path_matrix_shape(self):
        wacc_range = [0.07, 0.08, 0.09, 0.10, 0.11]
        terminal_g_range = [0.01, 0.02, 0.03, 0.04, 0.05]
        result = dcf_sensitivity(
            fcff_base=100.0, growth_s1=0.10, years=5,
            wacc_range=wacc_range, terminal_g_range=terminal_g_range,
        )
        assert len(result["matrix"]) == 5
        assert all(len(row) == 5 for row in result["matrix"])
        assert len(result["wacc_labels"]) == 5
        assert len(result["terminal_g_labels"]) == 5
        _check_no_forbidden_words(result)

    def test_illegal_combination_marked_na_not_raised(self):
        """terminal_g_range 中存在 >= 部分 wacc 的档位时，该格标注 N/A 而非报错中断。"""
        wacc_range = [0.03, 0.05, 0.07, 0.09, 0.11]
        terminal_g_range = [0.05, 0.05, 0.05, 0.05, 0.05]
        result = dcf_sensitivity(
            fcff_base=100.0, growth_s1=0.08, years=5,
            wacc_range=wacc_range, terminal_g_range=terminal_g_range,
        )
        flat = [cell for row in result["matrix"] for cell in row]
        assert "N/A" in flat
        # wacc=0.07/0.09/0.11 > terminal_g=0.05 应正常计算出数值
        assert any(isinstance(c, (int, float)) for c in flat)

    def test_empty_range_returns_error(self):
        result = dcf_sensitivity(fcff_base=100.0, growth_s1=0.1, years=5, wacc_range=[], terminal_g_range=[0.03])
        assert "error" in result


class TestScenarioFcff:
    def test_base_scenario_normal_path(self):
        financials = _make_dcf_financials(4)
        result = scenario_fcff(financials, scenario="base")
        assert "error" not in result
        assert result["assumption_type"] == "rule_based_proxy"
        assert len(result["yearly_fcff"]) == 5
        assert result["scenario"] == "base"
        assert "默认假设" in result["assumptions"]["note"]
        _check_no_forbidden_words(result)

    def test_bear_bull_relative_to_base(self):
        financials = _make_dcf_financials(4)
        base = scenario_fcff(financials, scenario="base")
        bear = scenario_fcff(financials, scenario="bear")
        bull = scenario_fcff(financials, scenario="bull")
        assert bear["assumptions"]["revenue_growth"] == pytest.approx(
            base["assumptions"]["revenue_growth"] * 0.5, abs=1e-3
        )
        assert bull["assumptions"]["revenue_growth"] == pytest.approx(
            base["assumptions"]["revenue_growth"] * 1.5, abs=1e-3
        )
        assert bear["assumptions"]["gross_margin_assumption"] == pytest.approx(
            base["assumptions"]["gross_margin_assumption"] - 3.0, abs=1e-6
        )
        assert bull["assumptions"]["gross_margin_assumption"] == pytest.approx(
            base["assumptions"]["gross_margin_assumption"] + 2.0, abs=1e-6
        )
        # 营收持续正增长场景下，bull 情景 5 年期 FCFF 应不低于 bear 情景
        assert bull["yearly_fcff"][-1]["fcff"] >= bear["yearly_fcff"][-1]["fcff"]

    def test_insufficient_data_path(self):
        result = scenario_fcff({"data": []}, scenario="base")
        assert "error" in result
        assert result["assumption_type"] == "rule_based_proxy"
        assert len(result["insufficient_data"]) > 0
        _check_no_forbidden_words(result)

    def test_single_period_insufficient(self):
        financials = {"data": [_make_dcf_financials_rows(4)[0]]}
        result = scenario_fcff(financials, scenario="base")
        assert "error" in result

    def test_unknown_scenario(self):
        result = scenario_fcff(_make_dcf_financials(4), scenario="neutral")
        assert "error" in result

    def test_custom_probabilities(self):
        financials = _make_dcf_financials(4)
        probs = {"bear": 0.5, "base": 0.3, "bull": 0.2}
        bear = scenario_fcff(financials, scenario="bear", probabilities=probs)
        base = scenario_fcff(financials, scenario="base", probabilities=probs)
        bull = scenario_fcff(financials, scenario="bull", probabilities=probs)
        assert bear["probability"] == pytest.approx(0.5)
        assert base["probability"] == pytest.approx(0.3)
        assert bull["probability"] == pytest.approx(0.2)

    def test_default_probabilities_equal(self):
        financials = _make_dcf_financials(4)
        result = scenario_fcff(financials, scenario="base")
        assert result["probability"] == pytest.approx(1 / 3, rel=1e-3)

    def test_partial_probabilities_fill_default_for_missing_keys(self):
        financials = _make_dcf_financials(4)
        result = scenario_fcff(financials, scenario="bull", probabilities={"bear": 0.6})
        assert result["probability"] == pytest.approx(1 / 3, rel=1e-3)

    def test_accepts_list_of_rows_directly(self):
        """兼容直接传入 list[dict] 财报行（非 legacy dict 包装）。"""
        rows = _make_dcf_financials_rows(4)
        result = scenario_fcff(rows, scenario="base")
        assert "error" not in result


class TestTriangleCheck:
    def test_all_available(self):
        result = triangle_check(dcf_growth=0.12, consensus_growth=0.14, hist_growth=0.18)
        assert len(result["rows"]) == 3
        assert all(r["value"] is not None for r in result["rows"])
        assert result["divergence_note"] is not None
        assert "低估" not in result["divergence_note"]
        assert "高估" not in result["divergence_note"]
        _check_no_forbidden_words(result)

    def test_no_divergence_when_close(self):
        result = triangle_check(dcf_growth=0.12, consensus_growth=0.13, hist_growth=0.125)
        assert result["divergence_note"] is None

    def test_partial_missing(self):
        result = triangle_check(dcf_growth=0.12, consensus_growth=None, hist_growth=0.18)
        rows_by_label = {r["label"]: r for r in result["rows"]}
        assert rows_by_label["机构一致预期增速"]["value"] is None
        assert rows_by_label["机构一致预期增速"]["display"] == "不可得"
        assert rows_by_label["自算DCF隐含增速"]["value"] is not None
        # 任一缺失时不生成 divergence_note（三者都存在才生成）
        assert result["divergence_note"] is None
        _check_no_forbidden_words(result)

    def test_all_missing(self):
        result = triangle_check(dcf_growth=None, consensus_growth=None, hist_growth=None)
        assert len(result["rows"]) == 3
        assert all(r["value"] is None for r in result["rows"])
        assert all(r["display"] == "不可得" for r in result["rows"])
        assert result["divergence_note"] is None

    def test_divergence_note_uses_numeric_comparison_not_adjectives(self):
        result = triangle_check(dcf_growth=0.05, consensus_growth=0.06, hist_growth=0.20)
        assert result["divergence_note"] is not None
        for bad_word in ("低估", "高估", "极度高估", "极度低估"):
            assert bad_word not in result["divergence_note"]
        assert "%" in result["divergence_note"]


# ═══════════════════════════════════════════════════════════════
# Step 4: render.py _section_dcf_valuation() 渲染测试
# ═══════════════════════════════════════════════════════════════

from lib.render import _section_dcf_valuation  # noqa: E402


def _make_dcf_render_financials(n_years: int = 4, *, beta: float | None = 1.1) -> dict:
    """构造带 beta 兜底字段的 financials legacy dict，并附加 dcf_preprocess。"""
    legacy: dict = {
        "dimension": "financials",
        "data": _make_dcf_financials_rows(n_years),
        "status": "available",
    }
    if beta is not None:
        legacy["beta"] = beta
    attach_dcf_preprocess(legacy)
    return legacy


def _make_research_dim(profit_forecasts: list[dict] | None = None) -> dict:
    return {
        "dimension": "research",
        "research_summary": {
            "status": "ok",
            "profit_forecasts": profit_forecasts or [],
        },
    }


class TestSectionDcfValuation:
    def test_normal_path(self):
        dims = {
            "financials": _make_dcf_render_financials(4, beta=1.1),
            "research": _make_research_dim([
                {"quarter": "2026", "avg_np_100m": 10.0, "n_analysts": 3},
                {"quarter": "2028", "avg_np_100m": 13.0, "n_analysts": 3},
            ]),
        }
        collection = {
            "market_structure": {"erp": {"dgs10": 2.65, "source": "FRED.DGS10"}},
        }
        text = _section_dcf_valuation(dims, collection, "000001")

        assert "D-④" in text
        assert "D-⑤" in text
        assert "D-⑥" in text
        # 三情景区间：乐观/中性/悲观 + 概率权重 + 假设前提，禁止单一目标价数字
        assert "乐观情景" in text
        assert "中性情景" in text
        assert "悲观情景" in text
        assert "概率" in text
        assert "仅供参考，不构成投资建议" in text
        # 三角对照表
        assert "自算DCF隐含增速" in text
        assert "机构一致预期增速" in text
        assert "历史营收CAGR" in text
        # 敏感性矩阵：5x5（表头 1 行 + 5 个 WACC 行）
        assert "WACC" in text
        assert "永续增长率" in text
        _check_no_forbidden_words(text)

    def test_wacc_insufficient_data_skips_section(self):
        """beta 不可得（真实报告的默认状态）时，整段标注数据不足并跳过，不编造 WACC。"""
        dims = {
            "financials": _make_dcf_render_financials(4, beta=None),
            "research": _make_research_dim(),
        }
        collection = {"market_structure": {}}
        text = _section_dcf_valuation(dims, collection, "000001")

        assert "数据不足，WACC 无法计算，DCF 段落跳过" in text
        assert "beta" in text
        # 不应出现任何情景企业价值数值渲染
        assert "乐观情景" not in text
        assert "D-⑤" not in text
        assert "D-⑥" not in text
        _check_no_forbidden_words(text)

    def test_veto_triggered_skips_all_values(self):
        dims = {
            "financials": _make_dcf_render_financials(4, beta=1.1),
            "research": _make_research_dim(),
        }
        collection = {"market_structure": {"erp": {"dgs10": 2.65}}}
        text = _section_dcf_valuation(dims, collection, "000001", veto_triggered=True)

        assert "研究终止条件触发，估值段落已跳过" in text
        assert "D-④" not in text
        assert "D-⑤" not in text
        assert "D-⑥" not in text
        assert "乐观情景" not in text
        _check_no_forbidden_words(text)

    def test_consensus_growth_unavailable_shows_not_available(self):
        """profit_forecasts 无可解析年度标签时，三角对照表机构一致预期一行显示"不可得"。"""
        dims = {
            "financials": _make_dcf_render_financials(4, beta=1.1),
            "research": _make_research_dim([
                {"quarter": "Q1", "avg_np_100m": 10.0, "n_analysts": 3},
            ]),
        }
        collection = {"market_structure": {"erp": {"dgs10": 2.65}}}
        text = _section_dcf_valuation(dims, collection, "000001")

        assert "机构一致预期增速 | 不可得" in text
        _check_no_forbidden_words(text)
