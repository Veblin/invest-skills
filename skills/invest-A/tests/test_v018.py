"""v0.1.8 统一测试文件（Step 1~10 各步骤测试用例汇总，逐步追加）。

Step 1 覆盖: scoring.py 5 个评分函数（正常路径 + 数据不足路径）。
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
