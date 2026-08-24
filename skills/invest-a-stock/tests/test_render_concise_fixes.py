"""code-review 清理：_concise/_v2 财务事实字段名与守卫收口的回归测试。

覆盖：OCF=0 不回退、亏损期不渲染负 OCF/净利比率、毛利率字段优先级
（grossprofit_margin → gross_margin → gross_profit_margin）、_v2 ROE 安全守卫。
"""

from __future__ import annotations

import sys
from pathlib import Path


_SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))


def _fin_dims(rows: list[dict]) -> dict:
    return {"financials": {"data": rows}}


def _row(end_date: str = "20260630", **kw) -> dict:
    r = {"end_date": end_date}
    r.update(kw)
    return r


class TestConciseFinancialSnapshot:
    def test_ocf_zero_does_not_fall_back_to_n_cashflow_act(self):
        """or 链会把 ocf=0.0（falsy）误回退到 n_cashflow_act=9.0 → 2.25。"""
        from lib.render_markdown._concise import _concise_financial_snapshot

        dims = _fin_dims([_row(ocf=0.0, n_cashflow_act=9.0, net_profit=4.0, roe=10.0)])
        out = _concise_financial_snapshot(dims)
        assert "OCF/净利润" in out
        assert "| OCF/净利润 | 0.00 |" in out
        assert "2.25" not in out

    def test_loss_making_no_ocf_ratio(self):
        """净利为负时不渲染 OCF/净利润（np > 0 守卫，与 _v3 口径一致）。"""
        from lib.render_markdown._concise import _concise_financial_snapshot

        dims = _fin_dims([_row(ocf=1.0, net_profit=-3.0, roe=-5.0)])
        out = _concise_financial_snapshot(dims)
        assert "OCF/净利润" not in out

    def test_gross_margin_field_priority(self):
        """grossprofit_margin（tushare 真名）优先，拼错旧键最后兜底。"""
        from lib.render_markdown._concise import _concise_financial_snapshot

        # 只有拼错旧键 → 兜底渲染
        only_typo = _concise_financial_snapshot(
            _fin_dims([_row(gross_profit_margin=20.0)]))
        assert "| 毛利率 | 20.00% |" in only_typo
        # 真名存在 → 优先（旧键也同时存在时）
        both = _concise_financial_snapshot(
            _fin_dims([_row(grossprofit_margin=25.0, gross_profit_margin=20.0)]))
        assert "| 毛利率 | 25.00% |" in both
        # gross_margin 第二优先
        second = _concise_financial_snapshot(
            _fin_dims([_row(gross_margin=30.0)]))
        assert "| 毛利率 | 30.00% |" in second


class TestConciseBearGrossMargin:
    def _bear_dims(self, key: str) -> dict:
        rows = [
            _row("20260331", **{key: 22.0}),
            _row("20260630", **{key: 20.0}),  # 降幅 2pp > 1pp
        ]
        return _fin_dims(rows)

    def test_declining_gross_margin_flagged_with_real_key(self):
        from lib.render_markdown._concise import _concise_bear

        out = _concise_bear({}, "600176", self._bear_dims("grossprofit_margin"),
                            {}, {}, None)
        assert "毛利率连续下滑" in out

    def test_declining_gross_margin_flagged_with_typo_key_fallback(self):
        from lib.render_markdown._concise import _concise_bear

        out = _concise_bear({}, "600176", self._bear_dims("gross_profit_margin"),
                            {}, {}, None)
        assert "毛利率连续下滑" in out


class TestV2QualityRoeGuard:
    def test_roe_none_uses_placeholder(self):
        from lib.render_markdown._v2 import _section_quality

        out = _section_quality(_fin_dims([_row(roe=None)]))
        assert "财务数据有限" in out

    def test_roe_nan_string_no_crash(self):
        """safe_float("nan") → None → 标题走 "?" / 结论兜底，不抛 TypeError。

        （表格行原样透传 r.get("roe") 属审查范围外，此处只锁定标题/结论守卫。）
        """
        from lib.render_markdown._v2 import _section_quality

        out = _section_quality(_fin_dims([_row(roe="nan")]))
        assert "财务数据有限" in out

    def test_roe_numeric_rendered(self):
        from lib.render_markdown._v2 import _section_quality

        out = _section_quality(_fin_dims([_row(roe=12.5)]))
        assert "ROE 12.5%" in out
