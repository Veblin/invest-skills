"""v0.2.8 修复测试：R-A3 多情景权重证据等级联动。"""
from __future__ import annotations

import re


class TestScenarioEvidenceLevel:
    """R-A3：三情景表概率单元格标注证据等级（_scenario_evidence，确定性规则）。"""

    def test_evidence_b_when_fcff_actual_and_wacc_full(self):
        from lib.render_dcf import _scenario_evidence

        base = {"fcff": {"fcff": 123.0}, "wacc_label": "实值参数"}
        assert _scenario_evidence({}, base) == "B"

    def test_evidence_c_when_fcff_missing(self):
        from lib.render_dcf import _scenario_evidence

        base = {"fcff": None, "wacc_label": "实值参数"}
        assert _scenario_evidence({}, base) == "C"

    def test_evidence_c_when_wacc_degraded(self):
        from lib.render_dcf import _scenario_evidence

        base = {"fcff": {"fcff": 1.0}, "wacc_label": "Beta 默认 1.0（近似）"}
        assert _scenario_evidence({}, base) == "C"

    def test_scenario_table_cells_carry_evidence_mark(self):
        """D-④ 三情景表概率单元格带（证据 X）标记。"""
        from test_v018 import _make_dcf_render_financials, _make_research_dim
        from lib.render_dcf import _section_dcf_valuation

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
        assert re.search(r"\|\s*.{0,6}情景\s*\|\s*\d+%\s*（证据 [BC]）", text)
