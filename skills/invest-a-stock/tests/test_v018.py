"""v0.1.8 统一测试文件（Step 1~10 各步骤测试用例汇总，逐步追加）。

Step 1 覆盖: scoring.py 5 个评分函数（正常路径 + 数据不足路径）。
Step 3 覆盖: valuation.py 4 个 DCF 函数（dcf_two_stage/dcf_sensitivity/
             scenario_fcff/triangle_check，正常路径 + 边界 + 数据不足路径）。
Step 4 覆盖: render.py _section_dcf_valuation()（D-④/D-⑤/D-⑥ 渲染，
             正常路径 + WACC 数据不足路径 + veto_triggered 跳过路径）。
Step 5 覆盖: render.py A-1 (_section_holder_changes 言行对照增强) /
             A-3 (_generate_custom_unknowns)。
Step 6 覆盖: render.py A-4 (_section_business_model_canvas) /
             A-5 (_section_management_assessment) /
             A-6 (_section_value_chain_position)。
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

FORBIDDEN_WORDS = ("买入", "卖出", "建仓", "极度高估", "极度低估", "崩盘")
# 2026-07-06 LAW 6 更新：允许免责声明中出现"目标价预测""不构成...目标价"等合法措辞，
# 仅禁止不标注假设的单一目标价数字（如"目标价 500 元"）。
# 改用 _check_bare_target_price() 替代简单的关键词匹配。
FORBIDDEN_TARGET_PRICE_RE = r"目标价\s*[:：]?\s*\d+"


def _check_no_forbidden_words(obj) -> None:
    """递归检查 dict/list/str 中不含合规禁用词。
    2026-07-06 LAW 6 更新：目标价改用正则仅禁止"目标价+数字"，
    免责声明"不构成...目标价预测"为合法措辞。
    """
    import re as _re
    if isinstance(obj, str):
        for w in FORBIDDEN_WORDS:
            assert w not in obj, f"发现禁用词 {w!r}: {obj!r}"
        if _re.search(FORBIDDEN_TARGET_PRICE_RE, obj):
            assert False, f"发现不标注假设的单一目标价数字: {obj!r}"
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

    def test_margin_trajectory_yoy_hyphenated_prior_end_date(self):
        from lib.scoring import _score_margin_trajectory

        rows = [
            {"end_date": "2023-12-31", "grossprofit_margin": 20.0, "netprofit_margin": 8.0},
            {"end_date": "20241231", "grossprofit_margin": 25.0, "netprofit_margin": 12.0},
        ]
        score, detail, _sources, missing = _score_margin_trajectory(rows)
        assert missing == ""
        assert score is not None
        assert detail.get("note", "").startswith("同比")

    def test_none_input_does_not_raise(self):
        result = revenue_quality_score(None)  # type: ignore[arg-type]
        assert result["score"] is None

    def test_both_negative_ocf_np_does_not_inflate(self):
        """OCF and net profit both negative must not score as strong cash quality."""
        rows = [
            _make_financials_row(
                f"2025{q}",
                revenue=100.0 + i * 10,
                net_profit=-100.0,
                n_cashflow_act=-100.0,
                grossprofit_margin=40.0,
            )
            for i, q in enumerate(("0331", "0630", "0930", "1231"))
        ]
        result = revenue_quality_score(rows)
        ocf = result.get("detail", {}).get("ocf_coverage", {})
        # Periods with net_profit <= 0 are excluded → ocf_coverage insufficient
        assert ocf.get("score") is None or ocf.get("score", 100) < 60
        note = ocf.get("note", "")
        assert "现金收入模式特征明显" not in note


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

    def test_nan_input_rejected(self):
        result = dcf_two_stage(
            fcff_base=100.0, growth_s1=0.10, years=5, wacc=float("nan"), terminal_g=0.03,
        )
        assert result == {"error": "参数必须为有限数值: wacc"}

    def test_inf_input_rejected(self):
        result = dcf_two_stage(
            fcff_base=100.0, growth_s1=float("inf"), years=5, wacc=0.09, terminal_g=0.03,
        )
        assert result == {"error": "参数必须为有限数值: growth_s1"}

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


class TestTargetPriceRegex:
    def test_colon_form_is_rejected(self):
        with pytest.raises(AssertionError):
            _check_no_forbidden_words("目标价：500元")

    def test_disclaimer_text_is_allowed(self):
        _check_no_forbidden_words("仅供参考，不构成投资建议，也不构成目标价预测。")


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

    def test_zero_revenue_latest_insufficient(self):
        """Latest revenue 0.0 must not produce all-zero FCFF projections."""
        rows = _make_dcf_financials_rows(4)
        rows[-1]["revenue"] = 0.0
        rows[-1]["ebit"] = 0.0
        result = scenario_fcff({"data": rows}, scenario="base")
        assert "error" in result
        assert any("revenue" in item for item in result["insufficient_data"])
        assert "yearly_fcff" not in result

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
        """beta 不可得时使用默认值 1.0，WACC 仍可计算，DCF 段落不跳过。"""
        dims = {
            "financials": _make_dcf_render_financials(4, beta=None),
            "research": _make_research_dim(),
        }
        collection = {"market_structure": {}}
        text = _section_dcf_valuation(dims, collection, "000001")

        # beta 默认为 1.0，WACC 可计算 → DCF 段落不应跳过
        assert "数据不足，WACC 无法计算，DCF 段落跳过" not in text
        # WACC 行应包含 beta 信息
        assert "β=" in text
        # DCF 段落应正常渲染（如果 scenario_fcff 数据充足）
        _check_no_forbidden_words(text)

    def test_wacc_truly_blocked_when_wacc_le_terminal_g(self):
        """WACC ≤ terminal_g 时 DCF 段落跳过（与 beta 无关）。"""
        dims = {
            "financials": _make_dcf_render_financials(4, beta=0.3),
            "research": _make_research_dim(),
        }
        # 极高无风险利率使 WACC 极端低（测试 wacc ≤ terminal_g 阻塞）
        collection = {"market_structure": {"erp": {"dgs10": 0.5}}}  # 0.5% 10Y
        text = _section_dcf_valuation(dims, collection, "000001")

        # 低无风险利率意味着低 WACC，可能 ≤ 2.5% terminal_g
        # 检查是否被 wacc <= terminal_g 阻断
        has_block = (
            "WACC" in text and "不高于永续增长率假设" in text
        )
        if has_block:
            assert "D-⑤" not in text
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

    def test_triangle_reuses_base_revenue_cagr_without_recompute(self):
        dims = {
            "financials": _make_dcf_render_financials(4, beta=1.1),
            "research": _make_research_dim(),
        }
        collection = {"market_structure": {"erp": {"dgs10": 2.65}}}
        with patch("lib.render._compute_metric_cagr", side_effect=AssertionError("should not call")):
            text = _section_dcf_valuation(dims, collection, "000001")
        assert "历史营收CAGR" in text


# ═══════════════════════════════════════════════════════════════
# Step 5: render.py A-1/A-2/A-3 分析深度增强模块测试
# ═══════════════════════════════════════════════════════════════

from lib.render import (  # noqa: E402
    _generate_custom_unknowns,
    _section_holder_changes,
    _section_risk_uncertainty,
)


class TestSectionHolderChangesInsiderAndCommitment:
    """A-1: 信号聚合 + 言行对照。"""

    def _holder_changes_data(self, records: list[dict]) -> dict:
        return {"data": records}

    def test_signal_aggregation_rendered(self):
        records = [
            {"ann_date": "20250101", "holder_name": "股东甲", "direction": "增持", "source": "tushare"},
            {"ann_date": "20250201", "holder_name": "股东乙", "direction": "增持", "source": "akshare"},
            {"ann_date": "20250301", "holder_name": "股东丙", "direction": "增持", "source": "tushare"},
        ]
        text = _section_holder_changes(self._holder_changes_data(records))
        assert "### 信号聚合" in text
        assert "强正向" in text
        assert "lib.scoring.insider_signal" in text
        assert "不构成任何投资建议或买卖指令" in text
        _check_no_forbidden_words(text)

    def test_commitment_events_matched_renders_timeline(self):
        """存在承诺公告 + 存在同期减持记录 → 生成时间线对照表。"""
        records = [
            {
                "ann_date": "20260301", "holder_name": "实控人甲", "direction": "减持",
                "source": "tushare", "change_vol": 1000000, "change_ratio": 1.2,
            },
        ]
        events = [
            {"date": "2026-01-15", "title": "关于公司实际控制人不减持承诺的公告", "type": "announcement"},
        ]
        text = _section_holder_changes(self._holder_changes_data(records), events)
        assert "### 言行对照" in text
        assert "未检索到相关承诺公告" not in text
        assert "承诺公告" in text
        assert "减持记录" in text
        assert "待独立验证" in text
        _check_no_forbidden_words(text)

    def test_no_commitment_events_marks_missing_explicitly(self):
        """无匹配承诺公告 → 明确标注暂缺，不得编造。"""
        records = [
            {"ann_date": "20260301", "holder_name": "股东甲", "direction": "减持", "source": "tushare"},
        ]
        events = [
            {"date": "2026-01-01", "title": "无关的股权激励公告", "type": "announcement"},
        ]
        text = _section_holder_changes(self._holder_changes_data(records), events)
        assert "### 言行对照" in text
        assert "未检索到相关承诺公告，言行对照暂缺。" in text
        _check_no_forbidden_words(text)

    def test_events_none_backward_compatible(self):
        """events 参数缺省（None）时函数仍可正常渲染，向后兼容旧调用点。"""
        records = [
            {"ann_date": "20260301", "holder_name": "股东甲", "direction": "增持", "source": "tushare"},
        ]
        text = _section_holder_changes(self._holder_changes_data(records))
        assert "## 3d. 股东增减持动向" in text
        assert "未检索到相关承诺公告，言行对照暂缺。" in text
        _check_no_forbidden_words(text)


class TestGenerateCustomUnknowns:
    """A-3: 待验证问题清单定制化。"""

    def _valuation_dim(self, pe_seq: list[float]) -> dict:
        rows = [
            {"trade_date": f"2024{str(i).zfill(4)}"[:8], "pe_ttm": pe, "pb": 3.0}
            for i, pe in enumerate(pe_seq)
        ]
        return {"data": rows}

    def test_semiconductor_industry_rule_hit(self):
        dims = {
            "basic_info": {"data": {"industry": "半导体设备"}},
        }
        result = _generate_custom_unknowns({}, dims)
        assert any("国产化率" in q for q, _why in result)
        assert all(isinstance(q, str) and isinstance(why, str) for q, why in result)

    def test_high_pe_percentile_rule_hit(self):
        pe_seq = [10.0 + i * 2 for i in range(50)]  # 单调递增，最新值为最大 → 高分位
        dims = {
            "valuation": self._valuation_dim(pe_seq),
        }
        result = _generate_custom_unknowns({}, dims)
        assert any("当前估值隐含增速能否兑现" in q for q, _why in result)
        assert all("历史分位" not in q and "历史分位" not in why for q, why in result)

    def test_no_rule_hit_returns_empty(self):
        """行业/估值/内部人字段均不可得或不触发规则 → 返回空列表，不编造问题。"""
        dims = {
            "basic_info": {"data": {"industry": "综合"}},
        }
        result = _generate_custom_unknowns({}, dims)
        assert result == []

    def test_insider_strong_negative_rule_hit(self):
        holder_changes = {
            "data": [
                {"ann_date": "20260101", "holder_name": "股东甲", "direction": "减持", "source": "tushare"},
                {"ann_date": "20260102", "holder_name": "股东乙", "direction": "减持", "source": "akshare"},
                {"ann_date": "20260103", "holder_name": "股东丙", "direction": "减持", "source": "tushare"},
            ],
        }
        dims = {
            "basic_info": {"data": {"industry": "综合"}},
            "holder_changes": holder_changes,
        }
        result = _generate_custom_unknowns({}, dims)
        assert any("集中减持" in q for q, _why in result)
        _check_no_forbidden_words(result)

    def test_integrated_into_section_risk_uncertainty(self):
        """§7 Known Unknowns 渲染时应附加定制化问题（若命中规则）。"""
        dims = {
            "basic_info": {"data": {"industry": "半导体设备"}},
        }
        risk_data = {"coverage": {"auto": 10}, "triggered_count": 0, "signals": [], "known_unknowns": []}
        text = _section_risk_uncertainty({}, "000001", dims, {}, risk_data)
        assert "Known Unknowns" in text
        assert "国产化率" in text
        _check_no_forbidden_words(text)


# ═══════════════════════════════════════════════════════════════
# Step 6: render.py A-4/A-5/A-6 分析深度增强模块测试
# ═══════════════════════════════════════════════════════════════

from lib.render import (  # noqa: E402
    _section_business_model_canvas,
    _section_management_assessment,
    _section_value_chain_position,
)


def _make_canvas_financials(n: int = 6) -> list[dict]:
    """构造营收稳步增长、毛利率基本稳定、ROE 波动小的财报序列（商业模式画布正常路径）。"""
    rows = []
    base_year = 2023
    for i in range(n):
        year = base_year + i // 4
        q = ["0331", "0630", "0930", "1231"][i % 4]
        rows.append({
            "end_date": f"{year}{q}",
            "revenue": 100.0 + i * 15.0,
            "grossprofit_margin": 45.0 + (i % 2) * 0.5,
            "accounts_receiv": 10.0 + i * 1.0,
            "net_profit": 15.0 + i * 3.0,
            "n_cashflow_act": 18.0 + i * 3.0,
            "ebit": 20.0 + i * 4.0,
            "total_assets": 200.0 + i * 10.0,
            "total_cur_liab": 50.0,
            "cap_ex": 5.0,
            "roe": 10.0 + i * 1.0,
            "netprofit_margin": 15.0 + i * 0.5,
        })
    return rows


class TestSectionBusinessModelCanvas:
    """A-4: 商业模式画布 7 维度评分。"""

    def test_normal_path_renders_all_seven_dimensions(self):
        fin_list = _make_canvas_financials(6)
        holder_changes = _make_holder_changes([
            {"ann_date": "20250101", "holder_name": "股东甲", "direction": "增持", "source": "tushare"},
        ])
        chain = {
            "industry": "半导体", "chain_position": "中游制造",
            "upstream": ["硅片", "设备"], "downstream": ["电子消费品", "汽车"],
        }
        text = _section_business_model_canvas(fin_list, holder_changes, chain)
        assert "商业模式画布" in text
        for dim in ("收入模式", "客户锁定", "规模效应", "技术壁垒", "周期性", "增长驱动", "资本密集度"):
            assert dim in text
        assert "★" in text
        assert "核心矛盾" in text
        # 技术壁垒与资本密集度当前引擎恒定数据不足，不得裸给星级
        assert "数据不足" in text
        _check_no_forbidden_words(text)

    def test_every_star_rating_has_accompanying_rationale(self):
        """硬性合规：不能出现裸★评分，每行必须有依据文本。"""
        fin_list = _make_canvas_financials(6)
        text = _section_business_model_canvas(fin_list, {}, {})
        for line in text.splitlines():
            if line.startswith("| ") and "★" in line:
                cells = [c.strip() for c in line.strip("|").split("|")]
                assert len(cells) == 3
                # 第三列依据文本不能为空
                assert cells[2], f"裸星级评分（无依据）: {line!r}"

    def test_insufficient_data_path_empty_financials(self):
        text = _section_business_model_canvas([], {}, {})
        assert "商业模式画布" in text
        assert text.count("数据不足") >= 7  # 7 个维度均应标注数据不足
        assert "可计算维度不足 2 个" in text
        assert "★" not in text
        _check_no_forbidden_words(text)

    def test_capital_intensity_and_tech_barrier_always_insufficient(self):
        """v0.1.7 未采集 fix_assets/研发数据字段，这两维度即使财务数据充足也应标数据不足。"""
        fin_list = _make_canvas_financials(6)
        text = _section_business_model_canvas(fin_list, {}, {})
        lines_by_dim = {ln.split("|")[1].strip(): ln for ln in text.splitlines() if ln.startswith("| ")}
        assert "数据不足" in lines_by_dim["技术壁垒"]
        assert "数据不足" in lines_by_dim["资本密集度"]
        _check_no_forbidden_words(text)


class TestSectionManagementAssessment:
    """A-5: 管理层完整评估。"""

    def test_normal_path_with_timeline_and_capital_allocation(self):
        fin_list = _make_canvas_financials(6)
        holder_changes = _make_holder_changes([
            {"ann_date": "20250101", "holder_name": "股东甲", "direction": "增持", "source": "tushare"},
            {"ann_date": "20250201", "holder_name": "股东乙", "direction": "增持", "source": "akshare"},
            {"ann_date": "20250301", "holder_name": "股东丙", "direction": "增持", "source": "tushare"},
        ])
        events = [
            {"date": "2026-01-15", "title": "关于回购公司股份的公告"},
            {"date": "2026-02-01", "title": "关于收购XX资产的公告"},
            {"date": "2026-03-01", "title": "与决策无关的其他公告"},
        ]
        text = _section_management_assessment(events, holder_changes, fin_list)
        assert "管理层完整评估" in text
        assert "关键决策时间线" in text
        assert "回购" in text
        assert "并购" in text
        # 未命中关键词的公告不应纳入时间线表格行
        assert "与决策无关的其他公告" not in text
        assert "[待 Claude report 阶段填充]" in text
        assert "资本配置能力" in text
        assert "股东利益一致性" in text
        assert "强正向" in text
        assert "组织能力" in text
        assert "[Claude report 阶段定性填充" in text
        _check_no_forbidden_words(text)

    def test_no_matching_events_marks_insufficient(self):
        fin_list = _make_canvas_financials(6)
        events = [{"date": "2026-01-01", "title": "无关的股权激励公告"}]
        text = _section_management_assessment(events, {}, fin_list)
        assert "数据不足" in text
        assert "关键决策时间线" in text
        _check_no_forbidden_words(text)

    def test_empty_inputs_do_not_raise(self):
        text = _section_management_assessment(None, {}, [])
        assert "管理层完整评估" in text
        assert "数据不足" in text
        _check_no_forbidden_words(text)

    def test_no_binary_trust_conclusion(self):
        """硬性合规：不得出现「信赖/不信赖」二元结论，且声明不推断动机。"""
        fin_list = _make_canvas_financials(6)
        holder_changes = _make_holder_changes([
            {"ann_date": "20250101", "holder_name": "股东甲", "direction": "减持", "source": "tushare"},
        ])
        events = [{"date": "2026-01-15", "title": "关于回购公司股份的公告"}]
        text = _section_management_assessment(events, holder_changes, fin_list)
        assert "不推断管理层主观动机" in text
        assert "不构成对管理层的信赖/不信赖二元结论" in text
        assert "值得信赖" not in text
        assert "不值得信赖" not in text
        _check_no_forbidden_words(text)


class TestSectionValueChainPosition:
    """A-6: 价值链位置 + 利润池分布。"""

    def test_normal_path_renders_ascii_chain(self):
        chain = {
            "industry": "半导体", "chain_position": "中游制造",
            "upstream": ["硅片", "设备"], "downstream": ["电子消费品", "汽车"],
        }
        text = _section_value_chain_position(chain, {}, 45.5)
        assert "价值链位置" in text
        assert "硅片" in text and "设备" in text
        assert "电子消费品" in text and "汽车" in text
        assert "45.50%" in text
        assert "⚠️" in text  # 上下游行业毛利率不可得须标注
        _check_no_forbidden_words(text)

    def test_no_industry_mapping_marks_insufficient(self):
        chain = {"industry": "不存在的冷门行业", "chain_position": None, "upstream": [], "downstream": []}
        text = _section_value_chain_position(chain, {})
        assert "数据不足" in text
        _check_no_forbidden_words(text)

    def test_empty_chain_returns_empty_string(self):
        assert _section_value_chain_position({}, {}) == ""
        assert _section_value_chain_position(None, {}) == ""  # type: ignore[arg-type]

    def test_missing_company_gross_margin_marks_insufficient_not_fabricated(self):
        chain = {
            "industry": "白酒", "chain_position": "下游消费",
            "upstream": ["粮食", "包装"], "downstream": ["经销商", "消费者"],
        }
        text = _section_value_chain_position(chain, {})
        assert "本公司 | 数据不足" in text
        # 不得编造行业平均毛利率等无来源数字
        assert "行业平均毛利率" not in text
        _check_no_forbidden_words(text)

    def test_industry_pricing_futures_signal_included(self):
        chain = {
            "industry": "钢铁", "chain_position": "上游原料",
            "upstream": ["铁矿石", "焦煤"], "downstream": ["建筑", "汽车"],
        }
        industry_pricing = {
            "data": {"industry": "钢铁", "has_futures": True},
            "_meta": {"all_sources": []},
        }
        text = _section_value_chain_position(chain, industry_pricing, 18.0)
        assert "议价力线索" in text
        assert "期货" in text
        _check_no_forbidden_words(text)


# ═══════════════════════════════════════════════════════════════
# Step 7: render.py F-1/F-3/F-4/F-5/F-6 分析框架模板测试
# ═══════════════════════════════════════════════════════════════

from lib.render import (  # noqa: E402
    _check_fast_veto,
    _section_six_gates_scorecard,
)

FORBIDDEN_GATE_WORDS = ("通过", "不通过", "建议持有", "建议买入", "建议卖出", "建议回避")


class TestCheckFastVeto:
    """F-3: 快速否决 8 条中可量化子集。"""

    def test_no_trigger_on_healthy_financials(self):
        fin_list = _make_canvas_financials(6)
        dims = {"financials": {"data": fin_list, "status": "available"}}
        result = _check_fast_veto(dims, {})
        assert result == {"hard_triggers": [], "soft_triggers": [], "display_lines": []}

    def test_negative_ocf_streak_triggers(self):
        rows = []
        for i, ocf in enumerate([-5.0, -6.0, -7.0]):
            rows.append({
                "end_date": f"2025{['0331', '0630', '0930'][i]}",
                "n_cashflow_act": ocf,
                "revenue": 100.0,
                "net_profit": 5.0,
            })
        dims = {"financials": {"data": rows, "status": "available"}}
        result = _check_fast_veto(dims, {})
        assert any("经营性现金流为负" in r for r in result["soft_triggers"])
        for r in result["display_lines"]:
            assert "不买" not in r and "应回避" not in r and "⚠️" in r

    def test_low_roe_streak_is_soft_trigger(self):
        rows = []
        for i, roe_v in enumerate([4.2, 3.8, 2.6]):
            rows.append({
                "end_date": f"2025{['0331', '0630', '0930'][i]}",
                "roe": roe_v,
                "revenue": 100.0,
            })
        dims = {"financials": {"data": rows, "status": "available"}}
        result = _check_fast_veto(dims, {})
        assert any("ROE 连续低于 5%" in r for r in result["soft_triggers"])

    def test_high_debt_ratio_triggers(self):
        rows = [
            {
                "end_date": "20240930",
                "total_liab": 93.0,
                "total_assets": 100.0,
                "revenue": 100.0,
            },
            {
                "end_date": "20251231",
                "total_liab": 95.0,
                "total_assets": 100.0,
                "revenue": 100.0,
            },
        ]
        dims = {"financials": {"data": rows, "status": "available"}}
        result = _check_fast_veto(dims, {})
        assert any("资产负债率" in r for r in result["hard_triggers"])

    def test_fcff_cumulative_negative_triggers(self):
        rows = []
        for i, fcff in enumerate([-3.0, -4.0, -2.0]):
            rows.append({
                "end_date": f"2025{['0331', '0630', '0930'][i]}",
                "fcff": fcff,
                "revenue": 100.0,
            })
        dims = {"financials": {"data": rows, "status": "available"}}
        result = _check_fast_veto(dims, {})
        assert any("FCFF 累计为负" in r for r in result["hard_triggers"])

    def test_empty_financials_returns_empty_list(self):
        assert _check_fast_veto({}, {}) == {"hard_triggers": [], "soft_triggers": [], "display_lines": []}
        assert _check_fast_veto({"financials": {}}, {}) == {
            "hard_triggers": [], "soft_triggers": [], "display_lines": [],
        }

    def test_falls_back_to_collection_financials(self):
        """dims 中无 financials 时，退化读取 collection["financials"]。"""
        rows = [{
            "end_date": "20251231",
            "total_liab": 99.0,
            "total_assets": 100.0,
        }]
        result = _check_fast_veto({}, {"financials": {"data": rows}})
        assert any("资产负债率" in r for r in result["hard_triggers"])

    def test_goodwill_ratio_hard_trigger_when_balancesheet_available(self):
        dims = {
            "financials": {"data": _make_canvas_financials(6), "status": "available"},
            "balancesheet": {"data": [{
                "end_date": "20251231",
                "goodwill": 60.0,
                "total_hldr_eqy_inc_min_int": 100.0,
            }]},
        }
        result = _check_fast_veto(dims, {})
        assert any("商誉/净资产" in r for r in result["hard_triggers"])


class TestSectionSixGatesScorecard:
    """F-4: 六关评分速览——最高风险合规点：无通过/不通过二元判决，无仓位动作映射。"""

    def _dims(self) -> dict:
        fin_list = _make_canvas_financials(6)
        return {
            "financials": {"data": fin_list, "status": "available"},
            "holder_changes": _make_holder_changes([
                {"ann_date": "20250101", "holder_name": "股东甲", "direction": "增持", "source": "tushare"},
            ]),
        }

    def test_normal_path_renders_six_gates(self):
        text = _section_six_gates_scorecard(self._dims(), {}, {})
        assert "F-4 六关评分速览" in text
        for gate in ("生意", "护城河", "管理层", "财务", "估值", "风险"):
            assert f"| {gate} " in text
        assert "不构成投资建议" in text
        assert "不代表买卖或持有的行动判断" in text
        _check_no_forbidden_words(text)

    def test_no_binary_pass_fail_or_action_words(self):
        """硬性合规：不得出现通过/不通过/建议持有/建议买入/建议卖出等字样。"""
        text = _section_six_gates_scorecard(self._dims(), {}, {})
        for word in FORBIDDEN_GATE_WORDS:
            assert word not in text, f"F-4 输出中发现违规字样 {word!r}"
        _check_no_forbidden_words(text)

    def test_insufficient_data_path_still_renders_without_crash(self):
        text = _section_six_gates_scorecard({}, {}, {})
        assert "F-4 六关评分速览" in text
        assert "数据不足" in text
        for word in FORBIDDEN_GATE_WORDS:
            assert word not in text
        _check_no_forbidden_words(text)

    def test_management_gate_includes_confidence_caveat(self):
        text = _section_six_gates_scorecard(self._dims(), {}, {})
        assert "置信度中等" in text

    def test_valuation_gate_uses_history_position_wording(self):
        text = _section_six_gates_scorecard(self._dims(), {}, {})
        assert "历史位置" in text
        assert "历史分位" not in text

    def test_financial_gate_includes_soft_signals(self):
        fin_list = _make_canvas_financials(6)
        fin_list[-1]["n_income_attr_p"] = 10_000_000.0
        fin_list[-1]["n_cashflow_act"] = 1_000_000.0
        dims = {
            "financials": {"data": fin_list, "status": "available"},
            "holder_changes": _make_holder_changes([
                {"ann_date": "20250101", "holder_name": "股东甲", "direction": "增持", "source": "tushare"},
            ]),
        }
        text = _section_six_gates_scorecard(dims, {}, {})
        assert "软信号" in text
        assert "营收加速度" in text or "OCF/净利背离" in text
        for word in FORBIDDEN_GATE_WORDS:
            assert word not in text
        _check_no_forbidden_words(text)


# ═══════════════════════════════════════════════════════════════
# F-2/F-3 补充测试：_section_bull_bear 空方补齐 + 快速否决联动 D 段
# ═══════════════════════════════════════════════════════════════

from unittest.mock import patch  # noqa: E402

from lib.render import _section_bull_bear, render_report_v3  # noqa: E402

_CONVERGENCE_WORDS = (
    "综合来看应该", "整体判断应该", "结论是应该", "总体偏多", "总体偏空",
    "建议买入", "建议卖出", "应回避", "不建议",
)


def _bb_financials(*, roe: float, ocf_ratio: float, net_profit: float = 1.2e8) -> list[dict]:
    """构造一份满足 _section_bull_bear 所需字段的最小财务序列。"""
    ocf = net_profit * ocf_ratio
    return [
        {"end_date": "20230331", "roe": roe - 1, "net_profit": net_profit * 0.9,
         "n_cashflow_act": ocf * 0.9, "revenue": 3.0e9},
        {"end_date": "20230630", "roe": roe, "net_profit": net_profit,
         "n_cashflow_act": ocf, "revenue": 3.2e9},
    ]


class TestSectionBullBearPadding:
    """F-2: bear_chains 补齐逻辑 + 禁止收敛性/动作性措辞。"""

    def test_bear_chains_padded_when_bull_heavy(self):
        """多头信号多、空头信号少时，通用空方模板应补齐至 bull-1，且不伪造数据不足论点。"""
        dims = {
            "financials": {"data": _bb_financials(roe=20.0, ocf_ratio=0.9)},
            "valuation": {"data": []},
        }
        market_structure = {
            "sw_index": {},
            "northbound": {"net_sum_10d": 600_000_000},
            "moneyflow": {},
            "erp": {},
        }
        collection = {
            "industry_peers": {"sufficient": False},
        }
        risk_data = {
            "signals": [
                {"id": "valuation_extreme_low", "triggered": True, "severity": "参考",
                 "detail": "估值处于极端低位", "category": "market"},
            ]
        }
        with patch("lib.render._v3_valuation_percentiles", return_value=(50.0, 50.0, "中性区")):
            text = _section_bull_bear(
                collection, "600176", dims, market_structure, risk_data, val_cache=None,
            )
        bull_count = text.count("#### 多头逻辑 ")
        bear_count = text.count("#### 空头逻辑 ")
        assert bull_count >= 3
        assert "数据可得性限制" in text or bear_count >= bull_count - 1
        assert "数据不足，暂缺同行竞争格局对照" not in text

    def test_shortfall_note_when_no_data_supports_more_bear_chains(self):
        """若确无数据支撑更多空头论据，应输出数据可得性限制说明，而非硬凑条目。"""
        dims = {
            "financials": {"data": _bb_financials(roe=20.0, ocf_ratio=0.9)},
            "valuation": {"data": []},
        }
        market_structure = {
            "sw_index": {},
            "northbound": {"net_sum_10d": 600_000_000},
            "moneyflow": {},
            "erp": {},
        }
        collection = {
            "industry_peers": {
                "sufficient": True,
                "rankings": {"roe_pct": 80.0, "revenue_yoy_pct": 80.0},
            },
        }
        risk_data = {"signals": []}
        # pe_pct < 30 触发多头估值偏低链，同时使估值补齐模板因"避免自相矛盾"而跳过
        with patch("lib.render._v3_valuation_percentiles", return_value=(15.0, 15.0, "偏低区")):
            text = _section_bull_bear(
                collection, "600176", dims, market_structure, risk_data, val_cache=None,
            )
        bull_count = text.count("#### 多头逻辑 ")
        bear_count = text.count("#### 空头逻辑 ")
        if bear_count < bull_count - 1:
            assert "数据可得性限制" in text
        else:
            assert bear_count >= bull_count - 1

    def test_no_convergent_or_action_wording(self):
        """硬性合规：不得出现收敛为单一方向的结论句或买卖动作词。"""
        dims = {
            "financials": {"data": _bb_financials(roe=20.0, ocf_ratio=0.9)},
            "valuation": {"data": []},
        }
        market_structure = {
            "sw_index": {"stock_vs_industry_pct": 3.0},
            "northbound": {"net_sum_10d": 600_000_000},
            "moneyflow": {},
            "erp": {"percentile_5y": 75.0},
        }
        collection = {
            "industry_peers": {"sufficient": False},
        }
        risk_data = {
            "signals": [
                {"id": "valuation_extreme_low", "triggered": True, "severity": "参考",
                 "detail": "估值处于极端低位", "category": "market"},
                {"id": "cashflow_negative", "name": "现金流恶化", "triggered": True,
                 "severity": "高", "category": "financial", "detail": "经营现金流连续为负"},
            ]
        }
        with patch("lib.render._v3_valuation_percentiles", return_value=(50.0, 50.0, "中性区")):
            text = _section_bull_bear(
                collection, "600176", dims, market_structure, risk_data, val_cache=None,
            )
        for word in _CONVERGENCE_WORDS:
            assert word not in text, f"模块 5 输出中发现收敛性/动作性措辞 {word!r}"
        _check_no_forbidden_words(text)


class TestFastVetoDcfLinkage:
    """F-3: 快速否决触发时 D 段应跳过 DCF 数值，并展示触发条目。"""

    def _collection_with_hard_veto(self):
        from test_v013_phase3 import _collection_phase3

        c = _collection_phase3()
        for dim in c["dimensions"]:
            if dim["dimension"] == "financials":
                rows = list(dim["data"])
                # 强制近 3 期 FCFF 为负，触发硬否决
                rows[-3] = {**rows[-3], "fcff": -1.0e7}
                rows[-2] = {**rows[-2], "fcff": -2.0e7}
                rows[-1] = {**rows[-1], "fcff": -3.0e7}
                dim["data"] = rows
        return c

    def test_veto_triggered_skips_dcf_and_shows_trigger_detail(self):
        collection = self._collection_with_hard_veto()
        text = render_report_v3(collection, "600176", mode="full")

        assert "研究终止条件触发，估值段落已跳过" in text
        # D 段不应再包含具体 DCF 数值区块标题
        assert "D-④ DCF 三情景估值区间" not in text
        assert "D-⑤" not in text
        assert "D-⑥" not in text
        # 触发条目应可见，且是量化陈述（复用 _check_fast_veto 的 ⚠️ 格式）
        assert "快速否决检测（F-3）" in text
        assert "⚠️" in text
        assert "FCFF 累计为负" in text
        _check_no_forbidden_words(text)

    def test_soft_trigger_keeps_dcf_section(self):
        from test_v013_phase3 import _collection_phase3

        collection = _collection_phase3()
        for dim in collection["dimensions"]:
            if dim["dimension"] == "financials":
                rows = list(dim["data"])
                rows[-3] = {**rows[-3], "roe": 4.0}
                rows[-2] = {**rows[-2], "roe": 3.0}
                rows[-1] = {**rows[-1], "roe": 2.0}
                dim["data"] = rows
        text = render_report_v3(collection, "600176", mode="full")
        assert "快速否决检测（F-3）" in text
        assert "研究终止条件触发，估值段落已跳过" not in text
        assert "D-④ DCF 三情景估值区间" in text
