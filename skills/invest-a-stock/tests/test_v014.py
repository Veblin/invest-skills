"""v0.1.4 测试：模块 4/5/7 模板升级、5c 分歧逻辑、collector 边界。"""

from __future__ import annotations

import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from test_v013_phase3 import _collection_phase3


class TestBullBearDivergence:
    def test_high_pe_bearish_narrative(self):
        from lib.render import _bull_bear_valuation_divergence_text

        text = _bull_bear_valuation_divergence_text(92.0, "偏高", 15.0)
        assert "估值透支" in text
        assert "低估" not in text

    def test_low_pe_bullish_narrative(self):
        from lib.render import _bull_bear_valuation_divergence_text

        text = _bull_bear_valuation_divergence_text(12.0, "偏低", 8.0)
        assert "修复空间" in text or "偏悲观" in text
        assert "估值透支" not in text

    def test_section_5c_high_pe_no_undervalue_claim(self):
        from lib.render import _section_bull_bear, _v3_build_risk_report

        c = _collection_phase3()
        # 人为抬高 PE 分位：构造高 PE 序列
        for dim in c["dimensions"]:
            if dim["dimension"] == "valuation":
                dim["data"] = [
                    {"trade_date": f"20240{i:02d}01", "pe_ttm": 20.0, "pb": 2.0}
                    for i in range(1, 40)
                ] + [{"trade_date": "20240601", "pe_ttm": 80.0, "pb": 5.0}]
        dims = {d["dimension"]: d for d in c["dimensions"]}
        ms = c["market_structure"]
        c["industry_peers"]["target"]["revenue_yoy"] = 12.0
        val_cache: dict = {}
        risk = _v3_build_risk_report(c, dims, ms, val_cache=val_cache)
        mod5 = _section_bull_bear(c, "600176", dims, ms, risk, val_cache=val_cache)
        assert "5c." in mod5
        assert "估值低估" not in mod5
        assert "估值透支" in mod5 or "均值回归" in mod5


class TestFundamentalsP0:
    def test_core_judgment_and_panorama(self):
        from lib.render import _section_fundamentals_layered

        c = _collection_phase3()
        dims = {d["dimension"]: d for d in c["dimensions"]}
        text = _section_fundamentals_layered(dims, c, "600176")
        assert "核心判断摘要" in text
        assert "[结论]" in text
        assert "[事实]" in text
        assert "[分析]" in text
        assert "业绩全景（近8期）" in text
        assert "| EPS |" in text
        assert "12题回答状态" in text

    def test_ocf_status_uses_walkback(self):
        from lib.render import _section_fundamentals_layered

        c = _collection_phase3()
        for dim in c["dimensions"]:
            if dim["dimension"] == "financials":
                rows = dim["data"]
                rows[-1] = dict(rows[-1])
                rows[-1]["ocf"] = None
                rows[-1]["n_cashflow_act"] = None
        dims = {d["dimension"]: d for d in c["dimensions"]}
        text = _section_fundamentals_layered(dims, c, "600176")
        assert "OCF/净利=" in text


class TestCoreTension:
    def test_core_tension_between_modules(self):
        from lib.render import _section_core_tension, render_report_v3

        c = _collection_phase3()
        report = render_report_v3(c, "600176")
        assert "核心矛盾小结" in report
        tension = _section_core_tension(
            c, "600176",
            {d["dimension"]: d for d in c["dimensions"]},
            c["market_structure"],
        )
        assert "实质上集中在" in tension


class TestRiskModule7:
    def test_known_unknowns_slots(self):
        from lib.render import _section_risk_uncertainty, _v3_build_risk_report

        c = _collection_phase3()
        dims = {d["dimension"]: d for d in c["dimensions"]}
        ms = c["market_structure"]
        risk = _v3_build_risk_report(c, dims, ms)
        mod7 = _section_risk_uncertainty(c, "600176", dims, ms, risk)
        assert "订单可见度" in mod7
        assert "技术路线时间表" in mod7
        assert "政策/贸易变量" in mod7
        assert "报表风险（Financial Statement）" in mod7


class TestCollectorEdgeCases:
    def test_inverted_price_range_swapped(self):
        from lib.collector import _aggregate_sellside_price_range

        result = _aggregate_sellside_price_range([(55.0, 45.0)])
        assert result is not None
        assert result["min"] <= result["max"]
        assert result["min"] == 45.0
        assert result["max"] == 55.0

    def test_forecast_summary_without_pct(self):
        from lib.collector import _summarize_research

        fc = [{"end_date": "20261231", "type": "预增"}]
        summary = _summarize_research(None, fc, None)
        assert "None" not in summary["summary_text"]


class TestSchemaResearch:
    def test_research_summary_keys(self):
        from lib.schema import DIMENSIONS, RESEARCH_SUMMARY_KEYS

        assert DIMENSIONS["research"] == "机构研报"
        assert "status" in RESEARCH_SUMMARY_KEYS
        assert "summary_text" in RESEARCH_SUMMARY_KEYS


class TestRenderEdgeCases:
    def test_coalesce_fin_field_preserves_zero(self):
        from lib.render import _coalesce_fin_field

        rows = [
            {"end_date": "20231231", "grossprofit_margin": 0.0, "gross_margin": 5.0},
        ]
        assert _coalesce_fin_field(rows, "grossprofit_margin", "gross_margin") == 0.0

    def test_get_safe_numpy_float(self):
        import numpy as np
        from lib.render import _fmt_num, _get_safe

        rows = [{"end_date": "20231231", "roe": np.float64(42.5)}]
        roe = _get_safe(rows, "roe")
        assert _fmt_num(roe) == "42.50"
        assert f"{roe:.2f}" == "42.50"

    def test_cash_flow_negative_np_positive_ocf(self):
        from lib.render import _conclude_cash_flow_quality

        text = _conclude_cash_flow_quality(None, None, None, 3.0, np_v=-5.0)
        assert "覆盖比不适用" in text
        assert "严重背离" not in text

    def test_cash_flow_negative_cf_ratio(self):
        from lib.render import _conclude_cash_flow_quality

        text = _conclude_cash_flow_quality(-0.6, None, None, 3.0, np_v=5.0)
        assert "不适用" in text
        assert "严重背离" not in text

    def test_evidence_strength_empty_vs_weak(self):
        from lib.render import _evidence_strength_label

        assert _evidence_strength_label([]) == "数据不足"
        assert _evidence_strength_label([False, False]) == "❓ 弱"
        assert _evidence_strength_label([True, True]) == "✅ 强"
