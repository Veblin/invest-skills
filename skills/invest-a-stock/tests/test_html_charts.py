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

    def test_band_xaxis_is_category(self):
        """B3 冒烟回修 Defect 1：xAxis 缺 type=category → ECharts 按 value 轴渲染，
        data 日期数组被当作无效值丢弃，x 轴显示 0..n-1 序号而非日期。
        B3-R A-1：用 >500 行（1210）样本真守卫——旧 lttb 采样只缩 xAxis.data
        而 series x 保持全量索引 → 越界。"""
        from lib.html_charts import build_valuation_band_options

        opts = build_valuation_band_options(make_daily_basic_series(1210))
        assert opts is not None
        assert opts["xAxis"].get("type") == "category"
        assert isinstance(opts["xAxis"]["data"], list) and len(opts["xAxis"]["data"]) > 0
        # series x 必须为 int 索引（与 category data 对位），不得是日期字符串
        curve = next(s for s in opts["series"] if s["name"] == "PE(TTM)")
        assert all(isinstance(pt[0], int) for pt in curve["data"])
        assert max(pt[0] for pt in curve["data"]) < len(opts["xAxis"]["data"])
        # 全量不降采样：轴与曲线均为 1210 段（lttb 已从 band 移除）
        assert len(curve["data"]) == 1210
        assert len(opts["xAxis"]["data"]) == 1210

    def test_band_median_matches_valuation_summary(self):
        """B3 冒烟回修 Defect 2：band 默认全量窗口 → median/window_label 与正文估值卡
        （valuation_summary）同口径；旧实现截近 250*4=1000 行，300308（1210 行）
        图内中位数 48.43x vs 正文 41.55x 不一致。"""
        from lib.html_charts import build_valuation_band_options, window_label
        from lib.valuation import valuation_summary

        rows = make_daily_basic_series(1200)  # > 1000 → 旧实现被截断
        opts = build_valuation_band_options(rows)
        assert opts is not None
        a = opts["annotation_payload"]
        summary = valuation_summary(
            [r["pe_ttm"] for r in rows], [r["pb"] for r in rows],
            window_label=window_label(len(rows)),
        )
        assert a["median"] == pytest.approx(summary["pe"]["median"], abs=0.01)
        assert a["window_label"] == summary["window_label"]
        # P10/P90 基于全量样本，而非截断后的尾部 1000 行
        srt = sorted(r["pe_ttm"] for r in rows)
        assert a["p10"] == pytest.approx(srt[int(len(srt) * 0.10)], abs=1e-9)

    def test_band_window_label_rule(self):
        """窗口标签规则与正文一致：1250+ → 近5年；≥250 → 近N年；否则上市以来（数据有限）。"""
        from lib.html_charts import window_label

        assert window_label(1200) == "近4年"
        assert window_label(1250) == "近5年"
        assert window_label(300) == "近1年"
        assert window_label(60) == "上市以来（数据有限）"


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
    # C-6/D-5：生产形态 flow_data 槽位 0 为全日期 YYYY-MM-DD
    return [[f"2026-07-{d:02d}", (1000.0 + d) * 10000, f"202607{d:02d}", None]
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
    return [("2026-07-13", 150.0), ("2026-07-14", 152.0), ("2026-07-15", 149.0),
            ("2026-07-16", 155.0), ("2026-07-17", 151.0), ("2026-07-18", 153.0),
            ("2026-07-19", 156.0)]


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
        """akshare 中文键降级（code-review #8）：无 rzye/rzrqye 时读取
        「融资余额」中文键（collector 全市场汇总形态），不再静默空系列。"""
        from lib.html_charts import build_flow_options

        margin_cn = [
            {"trade_date": f"202607{d:02d}", "融资余额": 100.0, "融资买入额": 12.0}
            for d in range(13, 20)
        ]
        opts = build_flow_options(_flow_data_7d(), margin_cn, _price_rows_7d())
        assert opts is not None
        margin_series = {s["name"]: s for s in opts["series"]}["融资余额(亿元)"]
        vals = [row[1] for row in margin_series["data"]
                if isinstance(row, list) and row[1] is not None]
        assert vals  # 中文键已被映射出值（不再全 None）
        note = opts["annotation_payload"]["margin_caliber_note"]
        assert "融资余额" in note and "亿元" in note

    def test_flow_no_data_returns_none(self):
        from lib.html_charts import build_flow_options

        assert build_flow_options([], None, []) is None
        assert build_flow_options(None, None, []) is None

    def test_flow_xaxis_is_category(self):
        """B3-R A-2：flow xAxis 缺 type=category → 字符串日期按 value 轴错位。"""
        from lib.html_charts import build_flow_options

        opts = build_flow_options(_flow_data_7d(), _margin_rows_7d(), _price_rows_7d())
        assert opts is not None
        assert opts["xAxis"].get("type") == "category"


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


# ── B3-R ①/③: NaN/Infinity JSON 安全 ──

class TestB3RJsonSafety:
    """B3-R ①（D-1/D-2/D-4）：非有限值不得产生裸 token 使 JSON.parse 死亡。"""

    def test_band_inf_and_nan_excluded(self):
        from lib.html_charts import build_valuation_band_options

        rows = make_daily_basic_series(120)
        rows[10] = dict(rows[10], pe_ttm=float("inf"))
        rows[20] = dict(rows[20], pe_ttm=float("nan"))
        rows[30] = dict(rows[30], pe_ttm=None)
        opts = build_valuation_band_options(rows)
        assert opts is not None
        raw = json.dumps(opts, ensure_ascii=False)
        assert "Infinity" not in raw and "NaN" not in raw
        assert json.loads(raw)  # JSON.parse 可存活

    def test_kline_nan_close_row_dropped_and_json_safe(self):
        from lib.html_charts import build_kline_options

        rows = make_kline_rows(120)
        rows[40] = dict(rows[40], close=float("nan"))
        opts = build_kline_options(rows)
        assert opts is not None
        assert opts["annotation_payload"]["kline_days"] == 119  # NaN 行被滤
        raw = json.dumps(opts, ensure_ascii=False)
        assert "NaN" not in raw and "Infinity" not in raw
        assert json.loads(raw)

    def test_kline_macd_aligned_after_close_drop(self):
        """C-3：compute 内部丢 NaN-close 行 → macd_series 短于 kline 行；
        按 macd_series.dates 日期对位后 MACD 柱日期 ⊆ 图表 dates，无错位。"""
        from lib.html_charts import build_kline_options
        from lib.technical import compute

        rows = make_kline_rows(120)
        rows[55] = dict(rows[55], close=float("nan"))  # 中部停牌残留行
        macd = compute(rows)["momentum"]["macd_series"]
        opts = build_kline_options(rows, macd_series=macd)
        assert opts is not None
        dates_set = set(opts["xAxis"][0]["data"])
        macd_bar = next(s for s in opts["series"] if s["name"] == "MACD")
        assert macd_bar["data"]  # 有柱
        for pt in macd_bar["data"]:
            assert pt[0] in dates_set
        raw = json.dumps(opts, ensure_ascii=False)
        assert "NaN" not in raw

    def test_flow_nan_values_json_safe(self):
        from lib.html_charts import build_flow_options

        flow = _flow_data_7d()
        flow[2] = [flow[2][0], float("nan"), flow[2][2], None]
        margin = _margin_rows_7d()
        margin[1] = dict(margin[1], rzye=float("nan"))
        price = [list(p) for p in _price_rows_7d()]
        price[3][1] = float("inf")
        opts = build_flow_options(flow, margin, price)
        assert opts is not None
        raw = json.dumps(opts, ensure_ascii=False)
        assert "NaN" not in raw and "Infinity" not in raw
        assert json.loads(raw)
        nb = next(s for s in opts["series"] if s["name"] == "北向净买入(万元)")
        assert all(isinstance(pt[1], (int, float)) for pt in nb["data"])


# ── B3-R ④: 财务图（ROE/EPS 双轴 + 扣非柱） ──

class TestFinancialOptions:
    def test_roe_eps_options_dual_axis(self):
        from lib.html_charts import build_financial_roe_options

        opts = build_financial_roe_options(
            ["26Q1", "26Q2"], [10.5, 12.3], [0.8, 1.1])
        assert opts is not None
        names = {s["name"] for s in opts["series"]}
        assert names == {"ROE(%)", "EPS(元)"}
        assert opts["yAxis"][1].get("position") == "right"
        assert opts["annotation_payload"]["latest_roe"] == 12.3
        assert json.loads(json.dumps(opts))  # JSON 安全

    def test_financial_empty_labels_none(self):
        from lib.html_charts import (build_financial_profit_options,
                                      build_financial_roe_options)

        assert build_financial_roe_options([], [], []) is None
        assert build_financial_profit_options([], []) is None

    def test_financial_profit_bar_colors_red_up_green_down(self):
        """④/B-F4：扣非净利盈利（≥0）红 / 亏损（<0）绿。"""
        from lib.html_charts import build_financial_profit_options

        opts = build_financial_profit_options(
            ["26Q1", "26Q2", "26Q3"], [12.5, -3.2, 8.1])
        assert opts is not None
        bar = next(s for s in opts["series"] if s["type"] == "bar")
        colors = [pt[2]["itemStyle"]["color"] for pt in bar["data"]]
        assert colors == ["#f87171", "#34d399", "#f87171"]
        assert opts["annotation_payload"]["latest_profit_yi"] == 8.1


# ── B3-R ⑤ C-2 / B-F5: band 排序 + tooltip 口径 ──

class TestB3RBandSortAndTooltip:
    def test_band_descending_input_uses_latest(self):
        """C-2：Tushare daily_basic 最新在前 → band 内部升序后 cur 为最新点，
        且与升序输入结果一致（单源）。"""
        from lib.html_charts import build_valuation_band_options

        asc = build_valuation_band_options(make_daily_basic_series(120))
        desc = build_valuation_band_options(
            make_daily_basic_series(120, descending=True))
        assert asc is not None and desc is not None
        # 升序序列最后一行 pe = 20 + 119*0.1 = 31.9
        assert asc["annotation_payload"]["cur"] == pytest.approx(31.9, abs=0.01)
        assert desc["annotation_payload"]["cur"] == asc["annotation_payload"]["cur"]
        assert desc["annotation_payload"]["cur_date"] == asc["annotation_payload"]["cur_date"]

    def test_flow_tooltip_carry_units(self):
        """B-F5：flow 各系列 tooltip.valueFormatter 带口径（_js 常量）。"""
        from lib.html_charts import build_flow_options

        opts = build_flow_options(_flow_data_7d(), _margin_rows_7d(),
                                  _price_rows_7d())
        assert opts is not None
        fmt = {s["name"]: s["tooltip"]["valueFormatter"]["_js"]
               for s in opts["series"]}
        assert "万元" in fmt["北向净买入(万元)"]
        assert "亿元" in fmt["融资余额(亿元)"]
        assert fmt["收盘价(元)"].endswith("'元'")

    def test_kline_tooltip_units(self):
        """B-F5：MA/成交量带口径；candlestick **不得**设 valueFormatter
        （code-review #5：ECharts 传多维 OHLC 数组 → v.toFixed TypeError，
        tooltip 永不渲染）。"""
        from lib.html_charts import build_kline_options
        from lib.technical import compute

        rows = make_kline_rows(120)
        opts = build_kline_options(rows, macd_series=compute(rows)["momentum"]["macd_series"])
        assert opts is not None
        by_name = {s["name"]: s for s in opts["series"]}
        assert "tooltip" not in by_name["K线"]  # 多维数组值不适配标量 formatter
        assert "'手'" in by_name["成交量"]["tooltip"]["valueFormatter"]["_js"]
        assert "'元'" in by_name["MA5"]["tooltip"]["valueFormatter"]["_js"]


class TestKlineRedUpGreenDown:
    def test_kline_series_red_up_green_down_isolated_from_theme(self):
        """B3-R ⑧（B-F7 已裁定 A 股红涨绿跌）：蜡烛红涨 #ef4444，options 内
        不引用 CSS var(--up)（与主题绿涨有意解耦）。"""
        from lib.html_charts import build_kline_options

        opts = build_kline_options(make_kline_rows(120))
        assert opts is not None
        k = next(s for s in opts["series"] if s["type"] == "candlestick")
        assert k["itemStyle"]["color"] == "#ef4444"       # 红涨
        assert k["itemStyle"]["color0"] == "#34d399"      # 绿跌
        assert "var(--up)" not in json.dumps(opts, ensure_ascii=False)
