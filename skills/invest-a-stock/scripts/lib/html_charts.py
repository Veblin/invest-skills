"""HTML 报告 ECharts options 构建（R-B3 图表三件套）。

P0 红线：一切数值（分位/中位数/当前值/均线/MACD/偏离%）由本模块 Python 计算，
输出到 data-opts（JSON）— 前端只渲染，不做二次加工；aria 关键数字亦由此合成。

约定：
- 所有 build_*_options 返回 dict[str, Any] 或 None（无法成图返回 None）；
  结构含 ECharts option（xAxis/yAxis/series/dataZoom/tooltip/legend）
  与键 annotation_payload（aria 合成数据源）。
- 数据不足 / 无日期 / 无正数 → None，渲染侧输出占位。
- lttb() 仅允许在 Python 侧对折线（MA/估值）降采样；ECharts option 内
  **禁止** sampling:'lttb'（#18129/#20944 缺陷），K 线全量不降采样。
"""

from __future__ import annotations

import math
from typing import Any, Sequence

from .technical import sma, sort_kline_asc  # noqa: F401
from lib.nums import safe_float


def _pct_clamp(v: float) -> float:
    """百分比截断到 [0, 100]；NaN/None 归 0（防注入非法 y 值）。"""
    if v is None:
        return 0.0
    v = float(v)
    if math.isnan(v) or math.isinf(v):
        return 0.0
    return max(0.0, min(100.0, v))


def lttb(pts: Sequence[tuple[float, float]], target: int) -> list[tuple[float, float]]:
    """Largest-Triangle-Three-Buckets 降采样（Python 侧折线用）。

    保留首尾端点；target >= len 或 target < 3 时原样返回（list 副本）。
    ECharts option 内不得出现 sampling:'lttb'（#18129/#20944）— 仅此处允许。
    """
    n = len(pts)
    if target >= n or target < 3:
        return list(pts)
    sampled = [pts[0]]
    every = (n - 2) / (target - 2)
    a = 0
    for i in range(target - 2):
        bucket_start = int(1 + i * every)
        bucket_end = int(1 + (i + 1) * every)
        nxt_start = int(1 + (i + 1) * every)
        nxt_end = min(int(1 + (i + 2) * every), n)
        seg = pts[nxt_start:nxt_end] or [pts[-1]]
        nxt_avg_x = sum(p[0] for p in seg) / len(seg)
        nxt_avg_y = sum(p[1] for p in seg) / len(seg)
        x0, y0 = pts[a]
        best_j, best_area = bucket_start, -1.0
        for j in range(bucket_start, bucket_end):
            x1, y1 = pts[j]
            area = abs((x0 - nxt_avg_x) * (y1 - y0) - (x0 - x1) * (nxt_avg_y - y0))
            if area > best_area:
                best_area, best_j = area, j
        sampled.append(pts[best_j])
        a = best_j
    sampled.append(pts[-1])
    return sampled


# ── T3-2 估值历史分位带图 ──

def window_label(n_rows: int) -> str:
    """估值窗口标签（与 render_html._extract_valuation_data 同一规则，D11 去重）。"""
    if n_rows >= 1250:
        return "近5年"
    if n_rows >= 250:
        return f"近{n_rows // 250}年"
    return "上市以来（数据有限）"


def build_valuation_band_options(
    rows: Sequence[dict[str, Any]]
) -> dict[str, Any] | None:
    """估值历史分位带图：PE(TTM) 曲线 + 历史分位带（P10-P90）+ 中/现值线。

    rows：valuation 维度数据（list[dict]，行含 trade_date/pe_ttm）。
    先窗口（后 filter）：亏损期占比按窗口内全量（含 None/<=0）统计，
    分位带按窗口内正数序列计算（D3/A4 修正）。
    窗口内有效正数 < 20 → None（渲染侧占位）。

    窗口不变式（B3-R A-5/A-6，单源）：窗口 = 全量升序序列——内部先
    sort_kline_asc（Tushare daily_basic 最新在前），与正文估值卡
    （valuation_summary 对同一升序序列计算）同窗口口径；window_days 参数已
    删除（无 caller 传值，属虚假承诺——旧实现固定截 250*4=1000 行与正文
    近 N 年窗口不一致的历史缺陷由全量窗口修复）。
    """
    if not rows:
        return None
    # C-2/A-4：Tushare daily_basic 最新在前 → 未排序时 ys[-1] 取到最旧点、
    # aria「最新 PE（截至…）」谎报。内部先升序。
    rows = sort_kline_asc(list(rows))
    pe = [(r.get("trade_date"), r.get("pe_ttm")) for r in rows]
    pe = [x for x in pe if x[0]]
    if len(pe) < 20:
        return None
    w_raw = pe
    if len(w_raw) < 20:
        return None
    loss_days = sum(
        1 for _, v in w_raw
        if (f := safe_float(v)) is None or f <= 0)  # NaN/Inf 并入缺失组（D-4）
    loss_ratio_pct = round(loss_days / len(w_raw) * 100.0, 1)
    cx = [(d, f) for d, v in w_raw
          if (f := safe_float(v)) is not None and f > 0]
    if len(cx) < 20:
        return None
    xs = [d for d, _ in cx]
    ys = [v for _, v in cx]
    srt = sorted(ys)
    mid = len(srt) // 2
    median = (srt[mid - 1] + srt[mid]) / 2.0 if len(srt) % 2 == 0 else srt[mid]
    # 全量曲线（不降采样）。B3-R A-1 修复：lttb 只采样 xAxis.data（≤500 段）
    # 而 series x=全量索引 → 索引越界（n>500 时 max(x) >= len(axis)）；
    # band 窗口 ≤~1210 行，全量渲染无性能问题。
    idx = [(i, ys[i]) for i in range(len(ys))]
    curve_xs = [xs[i] for i, _ in idx]
    p10 = srt[int(len(srt) * 0.10)]
    p90 = srt[int(len(srt) * 0.90)]
    cur, cur_date = ys[-1], xs[-1]
    note = ""
    if loss_ratio_pct > 30.0:
        note = (
            f"该标的历史约 {loss_ratio_pct:.1f}% 时间处于亏损期（PE<=0），"
            "PE 分位数仅作位置参考，不反映估值贵贱"
        )
    opts: dict[str, Any] = {
        # type=category：data 为日期标签数组，series x 用索引对位（
        # 缺省 value 轴会丢弃 data 数组 → x 轴渲染 0..n-1 序号，B3 冒烟回修）
        "xAxis": {"type": "category", "data": curve_xs, "axisLabel": {"rotate": 45}},
        "yAxis": {"name": "PE(TTM)", "scale": True},
        "dataZoom": [{"type": "inside"}, {"type": "slider", "height": 16}],
        "tooltip": {"trigger": "axis"},
        "legend": {"bottom": 28, "data": ["PE(TTM)", "P10 带", "P90 带"]},
        "series": [
            {
                "name": "PE(TTM)",
                "type": "line",
                "showSymbol": False,
                "data": [[i, v] for i, v in idx],
                # B-F5：tooltip 口径（PE 倍数）
                "tooltip": {"valueFormatter": {"_js": _JS_TOOLTIP_PEX}},
                # xAxis.data 全量日期，series x=索引 → 重建 date 轴
                "markLine": {
                    "symbol": "none",
                    "data": [
                        {
                            "name": "中位数",
                            "yAxis": round(median, 3),
                            "lineStyle": {"color": "#94a3b8", "type": "dashed"},
                            "label": {"formatter": f"中位数 {median:.2f}x"},
                        },
                        {
                            "name": "当前值",
                            "yAxis": round(cur, 3),
                            "lineStyle": {"color": "#f59e0b", "type": "dashed"},
                            "label": {"formatter": f"当前 {cur:.2f}x"},
                        },
                    ],
                },
            },
            # 历史分位带：P10 基线 + (P90-P10) 高度，stack 填充 P10~P90 区间
            {
                "name": "P10 带",
                "type": "line",
                "stack": "band",
                "showSymbol": False,
                "lineStyle": {"color": "#94a3b8", "type": "dashed", "width": 1},
                "emphasis": {"disabled": True},
                "data": [[i, p10] for i, _ in idx],
            },
            {
                "name": "P90 带",
                "type": "line",
                "stack": "band",
                "showSymbol": False,
                "lineStyle": {"color": "#94a3b8", "type": "dashed", "width": 1},
                "areaStyle": {"color": "rgba(148,163,184,0.18)"},
                "emphasis": {"disabled": True},
                "data": [[i, p90 - p10] for i, _ in idx],
            },
        ],
        "annotation_payload": {
            "cur": plain_num(cur),
            "cur_date": cur_date,
            "median": plain_num(median),
            "p10": plain_num(p10),
            "p90": plain_num(p90),
            "loss_ratio_pct": loss_ratio_pct,
            "window_label": window_label(len(w_raw)),
            "note": note,
        },
    }
    return opts


def _md(d: Any) -> str:
    """日期字符串归一化为 MM-DD：兼容 8 位（'20260723'）、ISO（'2026-07-23'）、已 MM-DD（幂等）。

    双端日期归一化（A5）：北向 flow_data md 与 margin trade_date 形态不一致 →
    统一经 _md 后再对表。
    """
    s = str(d) if d is not None else ""
    if len(s) >= 10:
        return s[5:10]
    if len(s) >= 8:
        return s[4:6] + "-" + s[6:8]
    return s


def _fd(d: Any) -> str:
    """日期字符串归一化为 YYYY-MM-DD：8 位（'20260723'）转全日期，ISO 与
    已全日期幂等（B3-R C-6/D-5：flow 轴改全日期携带年份，aria 不再跨年
    MM-DD 碰撞；axisLabel 用 formatter 只显 MM-DD）。"""
    s = str(d) if d is not None else ""
    if len(s) == 8 and s.isdigit():
        return f"{s[0:4]}-{s[4:6]}-{s[6:8]}"
    return s


def build_flow_options(
    flow_data: Sequence[Sequence[Any]] | None,
    margin_rows: Sequence[dict[str, Any]] | None,
    price_rows: Sequence[Sequence[Any]] | None,
) -> dict[str, Any] | None:
    """资金流图：北向净流入（bar，万元）+ 融资余额（亿元）+ 收盘价（元，右轴）。

    flow_data：_extract_northbound_data 产物 [[fd, nv(元), td, None], ...]（fd 全日期
    YYYY-MM-DD，B3-R C-6/D-5 改全日期携带年份）；
    margin_rows：collection 的 market_structure.margin（trade_date 8 位/ISO，金额单位元）；
    price_rows：[(fd 或 trade_date, close), ...]。
    任一关键源为空 → None（渲染侧占位）；两融/价格缺席仅去系列。
    """
    if not flow_data:
        return None
    nb_dates = []
    nb_series = []
    for row in flow_data:
        fd = _fd(row[0]) if len(row) else ""
        if not fd:
            continue
        nv = row[1] if len(row) > 1 else 0
        nv_wan = (safe_float(nv) or 0.0) / 1e4  # 元 → 万元（D-2：NaN/Inf 归 0）
        nb_dates.append(fd)
        nb_series.append([fd, round(nv_wan, 2)])
    if not nb_dates:
        return None
    margin_dates = set()
    margin_by = {}
    # 口径统一（code-review #13 二轮 B）：rzrqye（融资融券合计 ≈2× 融资余额）
    # 与 rzye（融资余额）不可行级混用——列级选择；**渲染层口径随 caliber
    # 动态命名**（series/yAxis/aria），fallback 数值不得再标「融资余额」。
    has_rzye = any(
        r.get("rzye") is not None for r in (margin_rows or []))
    has_rzrqye = any(
        r.get("rzrqye") is not None for r in (margin_rows or []))
    has_cn = any(
        r.get("融资余额") is not None for r in (margin_rows or []))
    if has_rzye:
        margin_caliber = "rzye"
    elif has_rzrqye:
        margin_caliber = "rzrqye"
    elif has_cn:
        margin_caliber = "akshare_cn"  # 全市场汇总（非个股，尺度不同）
    else:
        margin_caliber = None
    for r in margin_rows or []:
        td_raw = r.get("trade_date") or r.get("信用交易日期")
        d = _fd(td_raw)
        if not d or d in margin_by:
            continue
        # 收紧：caliber 选定后只取该键——行级缺键留 None（不再跨键混取不同
        # 尺度值；akshare 中文键仅在无 tushare 键时整体启用）
        raw = {
            "rzye": r.get("rzye"),
            "rzrqye": r.get("rzrqye"),
            "akshare_cn": r.get("融资余额"),
        }.get(margin_caliber or "")
        sf = safe_float(raw)
        margin_by[d] = round(sf / 1e8, 2) if sf is not None else None
        margin_dates.add(d)
    price_by = {}
    for row in price_rows or []:
        if len(row) < 2:
            continue
        d = _fd(row[0])
        sf = safe_float(row[1]) if d else None
        if d and sf is not None:
            price_by[d] = round(sf, 2)
    xaxis = sorted(set(nb_dates) | margin_dates | set(price_by))
    # 二轮 B：口径标签须在 series 构建前定（series/yAxis 名动态随 caliber）
    _CALIBER_LABEL = {
        "rzye": "融资余额(亿元)",
        "rzrqye": "融资融券合计(亿元)",
        "akshare_cn": "融资余额(亿元，全市场汇总)",
    }
    margin_label = _CALIBER_LABEL.get(margin_caliber or "", "融资余额(亿元)")
    series = [
        {
            "name": "北向净买入(万元)",
            "type": "bar",
            "data": [[md, v, {"itemStyle": {"color": _c_hex(v)}}] for md, v in nb_series],
            "yAxisIndex": 0,
            # B-F5：tooltip 口径（_js 常量表达式经前端 revive 执行）
            "tooltip": {"valueFormatter": {"_js": _JS_TOOLTIP_WAN}},
        },
        {
            # 二轮 B：系列名随 caliber 动态（rzrqye ≈2× / akshare 全市场汇总
            # 不得标「融资余额」——口径必须出现在用户可见层）
            "name": margin_label,
            "type": "line",
            "showSymbol": False,
            "connectNulls": True,
            "data": [[d, margin_by.get(d)] for d in xaxis],
            "yAxisIndex": 2,
            "tooltip": {"valueFormatter": {"_js": _JS_TOOLTIP_YI}},
        },
        {
            "name": "收盘价(元)",
            "type": "line",
            "showSymbol": False,
            "connectNulls": True,
            "data": [[d, price_by.get(d)] for d in xaxis],
            "yAxisIndex": 1,
            "tooltip": {"valueFormatter": {"_js": _JS_TOOLTIP_YUAN}},
        },
    ]
    total_wan = sum(v for _, v in nb_series)
    pos = sum(1 for _, v in nb_series if v > 0)

    def _last_of(m: dict) -> Any:
        """取序列最后非 None 值（code-review #10：xaxis[-1] 并集末位可能只有
        单系列——某系列早一交易日结束时 aria 谎报 N/A）。"""
        for k in reversed(xaxis):
            v = m.get(k)
            if v is not None:
                return v
        return None

    payload = {
        "net_total": plain_num(total_wan),
        "pos_days": pos,
        "close_latest": plain_num(_last_of(price_by)),
        "margin_latest": plain_num(_last_of(margin_by)),
        "margin_caliber": margin_caliber or "不可得",
        "margin_caliber_note": margin_label,
    }
    return {
        # A-2：type=category 缺失时 ECharts 按 value 轴处理字符串 data → 错位。
        # C-6/D-5：轴数据全日期（跨年不碰撞），axisLabel formatter 只显 MM-DD。
        "xAxis": {
            "type": "category",
            "data": xaxis,
            "axisLabel": {
                "rotate": 45,
                "formatter": {"_js": _JS_AXIS_MMDD},
            },
        },
        "yAxis": [
            {"name": "北向净买入(万元)", "scale": True},
            {"name": "收盘价(元)", "scale": True, "position": "right", "offset": 48},
            {"name": margin_label, "scale": True, "position": "right"},
        ],
        "dataZoom": [{"type": "inside"}, {"type": "slider", "height": 16}],
        "tooltip": {"trigger": "axis"},
        "legend": {"bottom": 28, "data": [s["name"] for s in series]},
        "series": series,
        "annotation_payload": payload,
    }


def _c_hex(v: float) -> str:
    """A 股惯例：正值红、负值绿（图表 canvas 内不能引用 CSS 变量）。"""
    return "#f87171" if v >= 0 else "#34d399"


def build_kline_options(
    rows: Sequence[dict[str, Any]] | None,
    latest_n: int = 500,
    macd_series: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """K 线图：candlestick + MA5/20/60 + 成交量 + MACD 面板（三区 grid）。

    rows：kline 行（trade_date/open/high/low/close/vol），窗口内行数 < 30 → None。
    K 线窗内全量（500 日不降采样，option 内禁 sampling:'lttb'）；MA 现算 sma
    （窗口 ≤500 项，无需再 Python 降采样）。
    macd_series：technical.compute 的 momentum.macd_series（dif/dea/histogram/dates）；
    消费端须先截窗口再 compute（A3：停牌行过滤后索引对齐），None → 无 MACD 面板。
    """
    rows = [r for r in (rows or []) if r.get("trade_date")]
    if len(rows) < 30:
        return None
    rows = rows[-latest_n:]
    # D-1：过滤 close 非有限行（镜像 technical.compute 丢行语义 :541）。
    # NaN 是真值，`float(v) or 0` 拦不住 → NaN 流入 sma → data-opts 裸 token。
    rows = [r for r in rows if safe_float(r.get("close")) is not None]
    if len(rows) < 30:
        return None
    dates = [str(r.get("trade_date")) for r in rows]
    closes = [c for c in (safe_float(r.get("close")) for r in rows)
              if c is not None]
    candles: list[list[Any]] = []
    vols: list[list[Any]] = []
    for r in rows:
        o = safe_float(r.get("open")) or 0
        c = safe_float(r.get("close")) or 0
        h = safe_float(r.get("high")) or 0
        l = safe_float(r.get("low")) or 0
        candles.append([o, c, l, h])  # ECharts candlestick 序：open, close, low, high
        v = safe_float(r.get("vol")) or 0
        vols.append([_c_hex(c - o), v])
    ma_series = []
    for p in (5, 20, 60):
        vals = sma(closes, p)
        ma_series.append({
            "name": f"MA{p}",
            "type": "line",
            "showSymbol": False,
            "lineStyle": {"width": 1},
            "data": [[d, v] for d, v in zip(dates, vals)],
            "xAxisIndex": 0,
            "yAxisIndex": 0,
            # B-F5：MA 价格口径（元）
            "tooltip": {"valueFormatter": {"_js": _JS_TOOLTIP_YUAN}},
        })
    xaxis: list[dict[str, Any]] = [
        {"type": "category", "data": dates, "gridIndex": 0, "axisLabel": {"show": False}},
        {"type": "category", "data": dates, "gridIndex": 1, "axisLabel": {"show": False}},
        {"type": "category", "data": dates, "gridIndex": 2, "axisPointer": {"show": True}},
    ]
    series: list[dict[str, Any]] = [
        {
            "name": "K线",
            "type": "candlestick",
            "data": candles,
            "xAxisIndex": 0,
            "yAxisIndex": 0,
            # A 股惯例红涨 #ef4444 / 绿跌 #34d399（B3-R B-F7 裁定）。
            # 二轮 D：全局主题现亦红涨（--up #f87171）——canvas 常量无法引用
            # CSS 变量，此处与 --up 同义不同值；改色时两处同步，勿再写「与
            # 主题解耦」（前提已被 2026-09-03 全局统一移除）。
            "itemStyle": {
                "color": "#ef4444", "color0": "#34d399",
                "borderColor": "#ef4444", "borderColor0": "#34d399",
            },
            # B-F5 注：candlestick 不设 valueFormatter——ECharts 对 K 线系列
            # 传多维 OHLC 数组给 tooltip valueFormatter，v.toFixed 抛 TypeError
            # 使 tooltip 永不渲染（code-review #5，jsdom 实证）。MA/成交量
            # 标量系列保留各自口径 formatter。
        },
        *ma_series,
        {
            "name": "成交量",
            "type": "bar",
            "data": [[dates[i], v, {"itemStyle": {"color": col}}]
                     for i, (col, v) in enumerate(vols)],
            "xAxisIndex": 1,
            "yAxisIndex": 1,
            "barWidth": "60%",
            # B-F5：tooltip 口径（手）
            "tooltip": {"valueFormatter": {"_js": _JS_TOOLTIP_SHOU}},
        },
    ]
    if macd_series and macd_series.get("histogram") is not None:
        hist = macd_series["histogram"]
        dif = macd_series["dif"]
        dea = macd_series["dea"]
        mdates = macd_series.get("dates")
        if mdates and len(mdates) == len(hist):
            # C-3：technical.compute 内部丢停牌行（None close）→ 序列短于图表
            # dates 且位置错位；macd_series 自带 dates（同源），按日期对位重建。
            # 图表 dates 中缺失的键 → 柱跳过 / 线 None（connectNulls 衔接）。
            hist_by = {str(d): v for d, v in zip(mdates, hist)}
            dif_by = {str(d): v for d, v in zip(mdates, dif)}
            dea_by = {str(d): v for d, v in zip(mdates, dea)}
            hdata = []
            dif_line = []
            dea_line = []
            for dd in dates:
                hv = hist_by.get(dd)
                if hv is not None:
                    hdata.append([dd, (plain_num(hv) or 0.0),
                                  {"itemStyle": {"color": _c_hex(hv)}}])
                dif_line.append([dd, plain_num(dif_by.get(dd))])
                dea_line.append([dd, plain_num(dea_by.get(dd))])
        else:
            # 回退：无 dates（旧形态）→ 位置截齐
            n = min(len(dates), len(hist), len(dif), len(dea))
            hdata = [
                [dates[i], (plain_num(hist[i]) or 0.0),
                 {"itemStyle": {"color": _c_hex(hist[i])}}]
                for i in range(n) if hist[i] is not None
            ]
            dif_line = [[dates[i], plain_num(dif[i])] for i in range(n)]
            dea_line = [[dates[i], plain_num(dea[i])] for i in range(n)]
        series.append({
            "name": "MACD",
            "type": "bar",
            "data": hdata,
            "xAxisIndex": 2,
            "yAxisIndex": 2,
            "barWidth": "50%",
        })
        series.append({
            "name": "DIF",
            "type": "line",
            "showSymbol": False,
            "data": dif_line,
            "xAxisIndex": 2,
            "yAxisIndex": 2,
            "connectNulls": True,
        })
        series.append({
            "name": "DEA",
            "type": "line",
            "showSymbol": False,
            "data": dea_line,
            "xAxisIndex": 2,
            "yAxisIndex": 2,
            "connectNulls": True,
        })
    ma5 = latest_non_none(ma_series[0]["data"])
    ma20 = latest_non_none(ma_series[1]["data"])
    ma60 = latest_non_none(ma_series[2]["data"])
    payload = {
        "kline_days": len(rows),
        "latest_close": plain_num(closes[-1]),
        "ma5": plain_num(ma5),
        "ma20": plain_num(ma20),
        "ma60": plain_num(ma60),
    }
    if macd_series:
        h = plain_num(hist[-1]) if macd_series.get("histogram") and hist and hist[-1] is not None else None
        payload["macd_dif"] = plain_num(dif[-1]) if macd_series.get("dif") and dif and dif[-1] is not None else None
        payload["macd_dea"] = plain_num(dea[-1]) if macd_series.get("dea") and dea and dea[-1] is not None else None
        payload["macd_hist"] = h
    return {
        "xAxis": xaxis,
        "yAxis": [
            {"name": "价格(元)", "scale": True, "gridIndex": 0},
            {"name": "成交量", "scale": True, "gridIndex": 1},
            {"name": "MACD", "scale": True, "gridIndex": 2},
        ],
        "grid": [
            {"left": 48, "right": 12, "top": 24, "height": "52%"},
            {"left": 48, "right": 12, "top": "62%", "height": "12%"},
            {"left": 48, "right": 12, "top": "78%", "height": "14%"},
        ],
        "dataZoom": [
            {"type": "inside", "xAxisIndex": [0, 1, 2]},
            {"type": "slider", "xAxisIndex": [0, 1, 2], "height": 14, "bottom": 4},
        ],
        "tooltip": {"trigger": "axis", "axisPointer": {"type": "cross"}},
        "legend": {"bottom": 22, "data": [s["name"] for s in series]},
        "series": series,
        "annotation_payload": payload,
    }


def _fin_labels(labels: Sequence[str]) -> list[str]:
    """财务短标签透传（'26Q2' 类），空/None 滤除。"""
    return [str(x) for x in labels if x]


def build_financial_roe_options(
    labels: Sequence[str],
    roe_data: Sequence[float | None],
    eps_data: Sequence[float | None],
) -> dict[str, Any] | None:
    """财务趋势图一：ROE(%) + EPS(元，右轴) 双轴折线（近 8 期季报）。

    B3-R ④ 回归恢复（pre-T3 roeChart 在 T3 迁移中被静默删除）——ECharts 版。
    labels 空 → None（渲染侧占位，与 test_insufficient_kline_no_crash 同构）。
    """
    labs = _fin_labels(labels)
    if not labs:
        return None
    roe = [plain_num(v) for v in roe_data]
    eps = [plain_num(v) for v in eps_data]
    payload = {
        "latest_roe": next((v for v in reversed(roe) if v is not None), None),
        "latest_eps": next((v for v in reversed(eps) if v is not None), None),
        "unit_note": "ROE(%) / EPS(元)",
    }
    return {
        "xAxis": {"type": "category", "data": labs},
        "yAxis": [
            {"name": "ROE(%)", "scale": True},
            {"name": "EPS(元)", "scale": True, "position": "right"},
        ],
        "tooltip": {"trigger": "axis"},
        "legend": {"bottom": 8, "data": ["ROE(%)", "EPS(元)"]},
        "series": [
            {"name": "ROE(%)", "type": "line", "showSymbol": False,
             "connectNulls": True,
             "data": [[i, roe[i]] for i in range(len(labs))],
             "yAxisIndex": 0},
            {"name": "EPS(元)", "type": "line", "showSymbol": False,
             "connectNulls": True,
             "data": [[i, eps[i]] for i in range(len(labs))],
             "yAxisIndex": 1},
        ],
        "annotation_payload": payload,
    }


def build_financial_profit_options(
    labels: Sequence[str],
    profit_data: Sequence[float | None],
) -> dict[str, Any] | None:
    """财务趋势图二：扣非净利润（亿元）柱状（近 8 期季报）。

    B3-R ④ 回归恢复（pre-T3 profitChart 被静默删除）——ECharts 版。
    柱色 B-F4 约定：盈利（≥0）红 / 亏损（<0）绿（A 股惯例，与 _c_hex 一致）。
    """
    labs = _fin_labels(labels)
    if not labs:
        return None
    prof = [plain_num(v) for v in profit_data]
    bars = [
        [i, v, {"itemStyle": {"color": _c_hex(v)}}]
        for i, v in enumerate(prof) if v is not None
    ]
    payload = {
        "latest_profit_yi": next((v for v in reversed(prof) if v is not None), None),
        "unit_note": "扣非净利润(亿元)",
    }
    return {
        "xAxis": {"type": "category", "data": labs},
        "yAxis": [{"name": "扣非净利润(亿元)", "scale": True}],
        "tooltip": {"trigger": "axis"},
        "legend": {"bottom": 8, "data": ["扣非净利(亿元)"]},
        "series": [
            {"name": "扣非净利(亿元)", "type": "bar", "data": bars,
             "barWidth": "50%"},
        ],
        "annotation_payload": payload,
    }


def latest_non_none(data: Sequence[list[Any]]) -> Any:
    """取序列最后一个非 None 值（MA/MACD 前置 None 段）。"""
    for item in reversed(data):
        if isinstance(item, list) and len(item) >= 2 and item[1] is not None:
            return item[1]
    return None


def plain_num(v: float) -> float:
    """JSON 安全化：NaN/None → None（NaN 非标准 JSON，避免 JSON.parse 失败）。"""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None  # type: ignore[return-value]
    if math.isnan(f) or math.isinf(f):
        return None  # type: ignore[return-value]
    return round(f, 4)


# B3-R B-F5：tooltip/axis formatter 常量表达式（_js 适配器约定——引擎只写常量
# lambda，经前端 revive() 的 new Function 执行；禁止插值任何数据/用户串）
_JS_AXIS_MMDD = "v => String(v).slice(5)"
_JS_TOOLTIP_WAN = "v => v == null ? '-' : v.toFixed(0) + '万元'"
_JS_TOOLTIP_YI = "v => v == null ? '-' : v.toFixed(2) + '亿元'"
_JS_TOOLTIP_YUAN = "v => v == null ? '-' : v.toFixed(2) + '元'"
_JS_TOOLTIP_PEX = "v => v == null ? '-' : v.toFixed(2) + 'x'"
_JS_TOOLTIP_SHOU = "v => v == null ? '-' : v.toFixed(0) + '手'"


def _json_safe(obj: Any) -> Any:
    """递归 JSON 安全化：非有限 float（NaN/±Inf）→ None。

    plain_num 只覆盖 annotation_payload；series data 数组（蜡烛/均线/带/资金流）
    的兜底防线——dumps 前整体过一遍，杜绝 'NaN'/'Infinity' 裸 token 使
    JSON.parse 死亡（B3-R ① D-1/D-2/D-4）。
    """
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_json_safe(v) for v in obj]
    if isinstance(obj, float) and not math.isfinite(obj):
        return None
    return obj
