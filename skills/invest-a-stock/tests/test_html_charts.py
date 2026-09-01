"""html_charts ECharts options 构建测试（T3-2/T3-3/T3-4）。

需求：R-B3① 估值历史分位带图 / R-B3② 资金流图 / R-B3③ K 线图。
P0 红线：分位/中位数/当前值等数值由 Python 计算进 options，禁前端/AI 心算。
"""

from __future__ import annotations

import json
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


# ── T3-3 资金流图（北向 + 两融叠加价格） ──

def _flow_data_7d() -> list[list]:
    return [[f"07-{d:02d}", (1000.0 + d) * 10000, f"072907{d:02d}", None]
            for d in range(13, 20)][:7]


def _margin_rows_7d() -> list[dict]:
    rows = []
    for d in range(13, 20):  # 生产形态：trade_date "20260713"（8 位，A5）
        rows.append({
            "trade_date": f"202607{d:02d}",
            "rzye": 10_000_000_000.0,   # 100 亿元（/1e8 → 100.0）
            "rqye": 100_000_000.0,
            "rzrqye": 1100_000_000.0 * (d - 12),
        })
    return rows


def _price_rows_7d() -> list[tuple]:
    return [("07-13", 150.0), ("07-14", 152.0), ("07-15", 149.0), ("07-16", 155.0),
            ("07-17", 151.0), ("07-18", 153.0), ("07-19", 156.0)]


class TestFlowOptions:
    def test_flow_series_and_axes(self):
        from lib.html_charts import build_flow_options

        opts = build_flow_options(_flow_data_7d(), _margin_rows_7d(), _price_rows_7d())
        assert opts is not None
        names = {s["name"] for s in opts["series"]}
        assert names == {"北向净买入(万元)", "融资余额(亿元)", "收盘价(元)"}
        y_names = [a.get("name", "") for a in opts["yAxis"]]
        assert any(("万元") in n for n in y_names) and any("亿元" in n for n in y_names)
        assert any("收盘价" in n for n in y_names)
        assert "dataZoom" in opts and "tooltip" in opts
        assert len(opts["xAxis"]["data"]) == 7

    def test_flow_dates_normalized_and_margin_yi(self):
        """A5：双端日期归一化 + 两融元→亿元（生产形态：_md 后 match）。"""
        from lib.html_charts import build_flow_options, _md

        assert _md("20260723") == "07-23"       # 8 位
        assert _md("2026-07-23") == "07-23"     # ISO
        assert _md("07-23") == "07-23"          # 已 MM-DD 幂等
        margin_all_iso = [
            {**r, "trade_date": f"2026-07-{d:02d}"} for r, d in zip(_margin_rows_7d(), range(13, 20))
        ]
        opts = build_flow_options(_flow_data_7d(), margin_all_iso, _price_rows_7d())
        assert opts is not None
        assert len(opts["xAxis"]["data"]) == 7          # 全部对齐，无并集膨胀
        margin_series = {s["name"]: s for s in opts["series"]}["融资余额(亿元)"]
        vals = [row[1] for row in margin_series["data"]
                if isinstance(row, list) and isinstance(row[1], (int, float))]
        assert vals[0] == pytest.approx(100.0)          # rzye 1e10 元 → 100.0 亿元

    def test_flow_akshare_chinese_key_tolerated(self):
        """akshare 中文键（融资余额/融资买入额）→ 无 rzye/rzrqye → 空槽 None 不崩。"""
        from lib.html_charts import build_flow_options

        margin_cn = [
            {"trade_date": f"202607{d:02d}", "融资余额": 100.0, "融资买入额": 12.0}
            for d in range(13, 20)
        ]
        opts = build_flow_options(_flow_data_7d(), margin_cn, _price_rows_7d())
        assert opts is not None
        margin_series = {s["name"]: s for s in opts["series"]}["融资余额(亿元)"]
        assert all(row[1] is None for row in margin_series["data"])

    def test_flow_no_data_returns_none(self):
        from lib.html_charts import build_flow_options

        assert build_flow_options([], None, []) is None
        assert build_flow_options(None, None, []) is None


# ── T3-4 K 线图（OHLC + MA5/20/60 + 成交量 + MACD） ──

class TestKlineOptions:
    def test_kline_series_full_and_styles(self):
        from lib.technical import compute
        from lib.html_charts import build_kline_options

        rows = make_kline_rows(120)
        macd = compute(rows)["momentum"]["macd_series"]
        opts = build_kline_options(rows, macd_series=macd)
        assert opts is not None
        names = {s["name"] for s in opts["series"]}
        assert any(s["type"] == "candlestick" for s in opts["series"])
        assert {"MA5", "MA20", "MA60"} <= names
        assert any("成交量" in s["name"] for s in opts["series"])
        assert any(s["name"] == "MACD" for s in opts["series"])
        # A10：v6 默认主题重做 → candlestick 显式 itemStyle 涨红跌绿
        c = next(s for s in opts["series"] if s["type"] == "candlestick")
        assert c["itemStyle"]["color"] == "#ef4444"
        assert c["itemStyle"]["color0"] == "#34d399"
        # 三区 grid + MACD 键序（histogram 非 hist，A2）+ 禁 lttb option
        assert len(opts["grid"]) == 3
        assert "lttb" not in json.dumps(opts)
        assert len(c["data"]) == 120          # 窗口内 K 线全量（不降采样）
        assert c["data"][0][0] == rows[0]["open"]   # ECharts 序 [open, close, low, high]

    def test_kline_insufficient_none(self):
        from lib.html_charts import build_kline_options

        assert build_kline_options([], None) is None
        assert build_kline_options(make_kline_rows(20)) is None
