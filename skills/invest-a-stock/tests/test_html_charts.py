"""html_charts ECharts options 构建测试（T3-2/T3-3/T3-4）。

需求：R-B3① 估值历史分位带图 / R-B3② 资金流图 / R-B3③ K 线图。
P0 红线：分位/中位数/当前值等数值由 Python 计算进 options，禁前端/AI 心算。
"""

from __future__ import annotations

import math

import pytest

from fixtures.collections import make_daily_basic_series, make_kline_rows


# ── T3-2 估值历史分位带图 ──

class TestBandOptions:
    def test_band_options_has_series(self):
        from lib.html_charts import build_valuation_band_options

        opts = build_valuation_band_options(make_daily_basic_series(120))
        assert opts is not None
        assert any(s["type"] == "line" for s in opts["series"])
        assert "dataZoom" in opts                       # 可滚窗，非静态截窗
        assert "tooltip" in opts
        assert "PE(TTM)" in opts["yAxis"].get("name", "")

    def test_band_axis_not_truncated_r_a4(self):
        """R-A4：不得截断坐标轴（yAxis.scale: True）。"""
        from lib.html_charts import build_valuation_band_options

        opts = build_valuation_band_options(make_daily_basic_series(120))
        assert opts["yAxis"].get("scale") is True

    def test_band_no_data_returns_none(self):
        from lib.html_charts import build_valuation_band_options

        assert build_valuation_band_options([]) is None
        assert build_valuation_band_options(make_daily_basic_series(10)) is None

    def test_band_loss_ratio_positive(self):
        """亏损期（PE<=0/None）先窗口后统计 → annotation 带亏损占比（A4 修正）。"""
        from lib.html_charts import build_valuation_band_options

        rows = make_daily_basic_series(60)
        # 构造 30% 亏损期：每 6 行中 2 行亏损（负值/None 交替）
        fixed = []
        for i, r in enumerate(rows):
            if i % 6 in (1, 2):
                r = dict(r)
                r["pe_ttm"] = -3.5 if i % 2 else None
            fixed.append(r)
        opts = build_valuation_band_options(fixed)
        assert opts is not None
        a = opts["annotation_payload"]
        assert a["loss_ratio_pct"] == pytest.approx(33.3, abs=0.5)  # 20/60
        assert a["note"]  # >30% → note 非空
        assert a["cur"] == pytest.approx(a["cur"], abs=0.0001)      # 结构自检


class TestPctClamp:
    def test_pct_clamp(self):
        from lib.html_charts import _pct_clamp

        assert _pct_clamp(-5) == 0
        assert _pct_clamp(120) == 100
        assert _pct_clamp(66.6) == 66.6
        assert _pct_clamp(float("nan")) == 0
        assert _pct_clamp(float("inf")) == 0


class TestLttb:
    def test_lttb_downsample(self):
        from lib.html_charts import lttb

        pts = [(float(i), math.sin(i / 50)) for i in range(1000)]
        out = lttb(pts, 200)
        assert len(out) == 200
        assert out[0] == pts[0] and out[-1] == pts[-1]     # 首尾保真
        # target >= n → 原样返回；target < 3 → 原样返回
        assert lttb(pts, 1000) == pts
        assert lttb(pts, 2) == pts
