"""技术分析纯计算模块。

输入: list[dict]（日 K 线数据，标准化字段 trade_date/open/high/low/close/vol）
输出: dict（各项指标的结构化结果）

原则:
  - 纯函数，无副作用，不依赖外部 API
  - 数据不足时对应字段返回 null，不抛异常
  - 不输出"金叉/死叉/买入/卖出"等交易信号词汇（用"DIF 上穿 DEA"等描述）

参考:
  John Murphy, _Technical Analysis of the Financial Markets_
"""

from __future__ import annotations

import math
from typing import Any


# ---- 内部辅助 ----

def _require_len(rows: list[dict], n: int, indicator: str) -> str | None:
    """数据不足时返回描述文本，否则返回 None。"""
    if len(rows) < n:
        return f"数据不足 {n} 日，{indicator} 不可得"
    return None


def _sma(seq: list[float], n: int) -> list[float | None]:
    """简单移动平均。前 N-1 位为 None。"""
    out: list[float | None] = []
    window: list[float] = []
    for v in seq:
        window.append(v)
        if len(window) > n:
            window.pop(0)
        if len(window) == n:
            out.append(sum(window) / n)
        else:
            out.append(None)
    return out


def _ema(seq: list[float], n: int) -> list[float | None]:
    """指数移动平均 (EMA)。前 N-1 位为 None。"""
    out: list[float | None] = []
    if len(seq) < n:
        return [None] * len(seq)
    multiplier = 2.0 / (n + 1)
    # 第一个 EMA 值用前 N 个的 SMA
    ema_val = sum(seq[:n]) / n
    # 前 n-1 位为 None
    for _ in range(n - 1):
        out.append(None)
    # 第 n 位为初始 SMA
    out.append(ema_val)
    # 后续用 EMA 公式
    for i in range(n, len(seq)):
        ema_val = (seq[i] - ema_val) * multiplier + ema_val
        out.append(ema_val)
    return out


# ---- 趋势类指标 ----

def _ma_values(closes: list[float], periods: tuple[int, ...]) -> dict[int, list[float | None]]:
    """计算多周期 SMA。"""
    return {p: _sma(closes, p) for p in periods}


def _ma_alignment(ma: dict[int, list[float | None]], key_periods: tuple[int, ...]) -> dict[str, Any]:
    """均线排列分析。返回短均线 vs 长均线相对位置。"""
    latest = {}
    for p in key_periods:
        vals = ma.get(p, [])
        latest[p] = vals[-1] if vals and vals[-1] is not None else None

    pairs: list[dict] = []
    periods = sorted(key_periods)
    for i in range(len(periods)):
        for j in range(i + 1, len(periods)):
            short_p = periods[i]
            long_p = periods[j]
            sv = latest.get(short_p)
            lv = latest.get(long_p)
            if sv is not None and lv is not None:
                relation = "上方" if sv > lv else ("下方" if sv < lv else "重合")
                desc = f"MA{short_p}({sv:.2f}) 位于 MA{long_p}({lv:.2f}){relation}"
            else:
                desc = f"MA{short_p}-MA{long_p} 数据不足"
            # sv==0 / lv==0 时 relation 正常计算（不等价于数据不足）
            pair_relation = relation if (sv is not None and lv is not None) else "insufficient"
            pairs.append({"short": short_p, "long": long_p, "relation": pair_relation, "desc": desc})

    # 多头/空头排列判断（短均线是否严格排序）
    valid = [(p, latest[p]) for p in sorted(key_periods) if latest[p] is not None]
    if len(valid) >= 2:
        values = [v for _, v in valid]
        if all(values[i] > values[i + 1] for i in range(len(values) - 1)):
            trend_label = "短中期多头排列（短均线在长均线上方）"
        elif all(values[i] < values[i + 1] for i in range(len(values) - 1)):
            trend_label = "短中期空头排列（短均线在长均线下方）"
        else:
            trend_label = "短中期均线交织，无明显排列方向"
    else:
        trend_label = "数据不足，无法判断均线排列"

    return {"latest": latest, "pairs": pairs, "trend_label": trend_label}


def _ma_slope(ma: dict[int, list[float | None]], k: int = 5) -> dict[int, float | None]:
    """均线斜率: (SMA(t) - SMA(t-k)) / SMA(t-k) * 100。"""
    slopes: dict[int, float | None] = {}
    for p, vals in ma.items():
        # 找到最新的两个有效值（间隔 k 个交易日）
        valid = [(i, v) for i, v in enumerate(vals) if v is not None]
        if len(valid) < 2:
            slopes[p] = None
            continue
        latest_idx, latest_val = valid[-1]
        # 向前找至少 k 个位置之前的值
        prev_val = None
        for i, v in reversed(valid[:-1]):
            if latest_idx - i >= k:
                prev_val = v
                break
        if prev_val is None:
            # 找不到 k 日前，取最早的有效值
            prev_val = valid[0][1]
        if prev_val and prev_val != 0:
            slopes[p] = (latest_val - prev_val) / prev_val * 100
        else:
            slopes[p] = None
    return slopes


# ---- 动量类指标 ----

def _macd(closes: list[float]) -> dict[str, Any]:
    """MACD 指标 (12, 26, 9)。

    Returns dict with:
      dif: list[float | None]
      dea: list[float | None]
      histogram: list[float | None]
      dif_cross: str | None  # 最近交叉状态
    """
    ema12 = _ema(closes, 12)
    ema26 = _ema(closes, 26)

    dif: list[float | None] = []
    for i in range(len(closes)):
        if ema12[i] is not None and ema26[i] is not None:
            dif.append(ema12[i] - ema26[i])
        else:
            dif.append(None)

    # DEA = EMA9(DIF)，需要从 DIF 的有效值开始计算
    dif_valid = [(i, v) for i, v in enumerate(dif) if v is not None]
    if len(dif_valid) < 9:
        dea: list[float | None] = [None] * len(closes)
        histogram: list[float | None] = [None] * len(closes)
        return {"dif": dif, "dea": dea, "histogram": histogram, "dif_cross": None}

    dea = [None] * len(closes)
    # 从第 26 个开始（EMA26 最早有效的位置）
    first_valid_dif = dif_valid[0][0]
    dea_start = max(first_valid_dif, 25)  # EMA26 在 index 25 才有效
    # 找到 dea_start 之后的 9 个有效 DIF
    valid_from_start = [v for v in dif[dea_start:] if v is not None]
    if len(valid_from_start) < 9:
        histogram = [None] * len(closes)
        # 填充能算的 DEA
        if valid_from_start:
            first_ema = sum(dif[dea_start:dea_start + 9]) / 9 if len(dif) > dea_start + 8 else sum(valid_from_start) / len(valid_from_start)
        else:
            return {"dif": dif, "dea": dea, "histogram": [None] * len(closes), "dif_cross": None}
    else:
        first_ema = sum(dif[dea_start:dea_start + 9]) / 9

    ema_val = first_ema
    multiplier = 2.0 / 10.0  # 9+1
    for i in range(dea_start, len(closes)):
        if dif[i] is not None:
            if i == dea_start:
                dea[i] = ema_val
            else:
                ema_val = (dif[i] - ema_val) * multiplier + ema_val
                dea[i] = ema_val

    histogram: list[float | None] = []
    for i in range(len(closes)):
        if dif[i] is not None and dea[i] is not None:
            histogram.append(2.0 * (dif[i] - dea[i]))
        else:
            histogram.append(None)

    # DIF/DEA 交叉检测
    dif_cross = _detect_cross(dif, dea)

    return {"dif": dif, "dea": dea, "histogram": histogram, "dif_cross": dif_cross}


def _detect_cross(dif: list[float | None], dea: list[float | None]) -> dict[str, Any] | None:
    """检测最近两日 DIF 与 DEA 的相对位置变化。"""
    # 找最近两个同时有效的 index
    valid_indices = [i for i in range(len(dif)) if dif[i] is not None and dea[i] is not None]
    if len(valid_indices) < 2:
        return None

    i_prev, i_curr = valid_indices[-2], valid_indices[-1]
    prev_rel = dif[i_prev] - dea[i_prev]  # type: ignore[operator]
    curr_rel = dif[i_curr] - dea[i_curr]  # type: ignore[operator]

    result: dict[str, Any] = {
        "prev_date_index": i_prev,
        "curr_date_index": i_curr,
        "prev_dif": round(dif[i_prev], 4),  # type: ignore[arg-type]
        "prev_dea": round(dea[i_prev], 4),  # type: ignore[arg-type]
        "curr_dif": round(dif[i_curr], 4),  # type: ignore[arg-type]
        "curr_dea": round(dea[i_curr], 4),  # type: ignore[arg-type]
    }

    if prev_rel <= 0 and curr_rel > 0:
        result["type"] = "bullish_cross"  # DIF 上穿 DEA
        result["desc"] = "DIF 上穿 DEA"
    elif prev_rel >= 0 and curr_rel < 0:
        result["type"] = "bearish_cross"  # DIF 下穿 DEA
        result["desc"] = "DIF 下穿 DEA"
    elif curr_rel > 0:
        result["type"] = "dif_above"
        result["desc"] = "DIF 位于 DEA 上方"
    else:
        result["type"] = "dif_below"
        result["desc"] = "DIF 位于 DEA 下方"

    return result


# ---- 超买超卖 ----

def _rsi(closes: list[float], n: int) -> list[float | None]:
    """Wilder 平滑 RSI (Relative Strength Index)。

    RSI = 100 - 100 / (1 + avg_gain / avg_loss)
    使用 Wilder 平滑法（不是简单平均）。
    """
    if len(closes) < n + 1:
        return [None] * len(closes)

    out: list[float | None] = [None] * n  # 前 n 天无 RSI

    changes = [closes[i] - closes[i - 1] for i in range(1, len(closes))]

    # 初始均值（简单平均）
    avg_gain = sum(max(c, 0) for c in changes[:n]) / n
    avg_loss = sum(max(-c, 0) for c in changes[:n]) / n

    if avg_loss == 0:
        out.append(100.0)
    else:
        rs = avg_gain / avg_loss
        out.append(100.0 - 100.0 / (1.0 + rs))

    # Wilder 平滑后续值
    for i in range(n, len(changes)):
        gain = max(changes[i], 0)
        loss = max(-changes[i], 0)
        avg_gain = (avg_gain * (n - 1) + gain) / n
        avg_loss = (avg_loss * (n - 1) + loss) / n
        if avg_loss == 0:
            out.append(100.0)
        else:
            rs = avg_gain / avg_loss
            out.append(100.0 - 100.0 / (1.0 + rs))

    return out


def rsi_series(closes: list[float], period: int) -> list[float | None]:
    """Wilder RSI 序列（公开 API，与 compute() 内 RSI 计算一致）。"""
    return _rsi(closes, period)


def _kdj(highs: list[float], lows: list[float], closes: list[float],
         n: int = 9, m1: int = 3, m2: int = 3) -> dict[str, list[float | None]]:
    """KDJ 随机指标。

    RSV(n) = (C - L_n) / (H_n - L_n) * 100
    K = EMA_m1(RSV)，首日用 50
    D = EMA_m2(K)
    J = 3*K - 2*D
    """
    length = len(closes)
    k_vals: list[float | None] = [None] * length
    d_vals: list[float | None] = [None] * length
    j_vals: list[float | None] = [None] * length

    if length < n:
        return {"k": k_vals, "d": d_vals, "j": j_vals}

    # 计算 RSV
    rsv: list[float | None] = [None] * length
    for i in range(n - 1, length):
        h_max = max(highs[i - n + 1:i + 1])
        l_min = min(lows[i - n + 1:i + 1])
        if h_max == l_min:
            rsv[i] = 50.0  # 避免除零
        else:
            rsv[i] = (closes[i] - l_min) / (h_max - l_min) * 100.0

    # K/D 平滑（类似 EMA）
    k_mult = 2.0 / (m1 + 1)
    d_mult = 2.0 / (m2 + 1)

    k_val = 50.0  # 初始值
    d_val = 50.0

    for i in range(length):
        if rsv[i] is not None:
            k_val = rsv[i] * k_mult + k_val * (1 - k_mult)
            k_vals[i] = k_val
            d_val = k_val * d_mult + d_val * (1 - d_mult)
            d_vals[i] = d_val
            j_vals[i] = 3 * k_val - 2 * d_val

    return {"k": k_vals, "d": d_vals, "j": j_vals}


# ---- 波动类 ----

def _boll(closes: list[float], n: int = 20, k: float = 2.0) -> dict[str, list[float | None]]:
    """BOLL 布林带。

    中轨 = MA(n)
    上轨 = 中轨 + k * σ_n
    下轨 = 中轨 - k * σ_n
    """
    mid = _sma(closes, n)
    upper: list[float | None] = []
    lower: list[float | None] = []
    width: list[float | None] = []  # (upper - lower) / mid * 100

    for i in range(len(closes)):
        if mid[i] is None:
            upper.append(None)
            lower.append(None)
            width.append(None)
            continue
        # 计算该点前 n 日的标准差
        window = closes[i - n + 1:i + 1]
        mean = sum(window) / n
        variance = sum((x - mean) ** 2 for x in window) / n
        std = math.sqrt(variance)
        u = mid[i] + k * std  # type: ignore[operator]
        l = mid[i] - k * std  # type: ignore[operator]
        upper.append(u)
        lower.append(l)
        if mid[i] != 0:  # type: ignore[operator]
            width.append((u - l) / mid[i] * 100)  # type: ignore[operator]
        else:
            width.append(None)

    return {"mid": mid, "upper": upper, "lower": lower, "width": width}


def _atr(highs: list[float], lows: list[float], closes: list[float], n: int = 14) -> list[float | None]:
    """ATR 平均真实波幅 (Average True Range)。"""
    if len(closes) < n + 1:
        return [None] * len(closes)

    tr: list[float] = [0.0]  # 首日 TR 为 None 占位
    for i in range(1, len(closes)):
        h_l = highs[i] - lows[i]
        h_pc = abs(highs[i] - closes[i - 1])
        l_pc = abs(lows[i] - closes[i - 1])
        tr.append(max(h_l, h_pc, l_pc))

    out: list[float | None] = [None] * n  # 前 n 天
    atr_val = sum(tr[1:n + 1]) / n
    out.append(atr_val)
    for i in range(n + 1, len(closes)):
        atr_val = (atr_val * (n - 1) + tr[i]) / n
        out.append(atr_val)

    return out


# ---- 成交量 ----

def _volume_ratio(vols: list[float], n: int = 5) -> list[float | None]:
    """量比 = 当日 vol / MA(vol, n)。"""
    ma_vol = _sma(vols, n)
    ratios: list[float | None] = []
    for i in range(len(vols)):
        if ma_vol[i] is not None and ma_vol[i] != 0:
            ratios.append(vols[i] / ma_vol[i])
        else:
            ratios.append(None)
    return ratios


# ---- 结构类 ----

def _n_day_extremes(rows: list[dict], ns: tuple[int, ...]) -> dict[int, dict]:
    """N 日极值（最高/最低收盘价和日期）。"""
    closes = [r.get("close", 0) or 0 for r in rows]
    dates = [r.get("trade_date", "") for r in rows]

    result: dict[int, dict] = {}
    for n in ns:
        if len(closes) < n:
            result[n] = {"available": False, "reason": f"数据不足 {n} 日"}
            continue
        window = closes[-n:]
        max_val = max(window)
        min_val = min(window)
        # 取最右侧（最新）的索引，而非首次出现
        reversed_window = list(reversed(window))
        max_idx = len(window) - 1 - reversed_window.index(max_val)
        min_idx = len(window) - 1 - reversed_window.index(min_val)
        max_date_idx = len(closes) - n + max_idx
        min_date_idx = len(closes) - n + min_idx
        is_high_today = closes[-1] >= max_val
        is_low_today = closes[-1] <= min_val
        result[n] = {
            "available": True,
            "max": max_val,
            "max_date": dates[max_date_idx] if max_date_idx < len(dates) else "",
            "min": min_val,
            "min_date": dates[min_date_idx] if min_date_idx < len(dates) else "",
            "is_n_day_high": is_high_today,
            "is_n_day_low": is_low_today,
        }
    return result


def _drawdown(closes: list[float], dates: list[str], n: int = 60) -> dict[str, Any]:
    """N 日最大回撤：(max_{-N} - P_now) / max_{-N} * 100。"""
    if len(closes) < n:
        return {"available": False, "reason": f"数据不足 {n} 日"}
    window = closes[-n:]
    peak = max(window)
    peak_idx = window.index(peak)
    peak_date = dates[len(closes) - n + peak_idx] if len(dates) > len(closes) - n + peak_idx else ""
    current = closes[-1]
    dd_pct = (peak - current) / peak * 100 if peak != 0 else 0
    return {
        "available": True,
        "peak": peak,
        "peak_date": peak_date,
        "current": current,
        "drawdown_pct": dd_pct,
    }


# ---- 主入口 ----

def sort_kline_asc(rows: list[dict]) -> list[dict]:
    """按日期升序排列（trade_date 或 end_date，Tushare 等源常为降序）。"""
    return sorted(rows, key=lambda r: str(r.get("trade_date") or r.get("end_date", "")))


def compute(rows: list[dict]) -> dict[str, Any]:
    """从日 K 线数据计算全部技术指标。

    Args:
        rows: list[dict]，每条含 trade_date, open, high, low, close, vol。
              内部会按 trade_date 升序排序后再计算。

    Returns:
        dict 包含所有指标的结构化结果。字段说明见各子函数。
    """
    if not rows:
        return {"error": "empty_kline_data", "message": "K 线数据为空，无法计算技术指标"}

    rows = sort_kline_asc(rows)

    closes = [r.get("close", 0) or 0 for r in rows]
    highs = [r.get("high", 0) or 0 for r in rows]
    lows = [r.get("low", 0) or 0 for r in rows]
    opens = [r.get("open", 0) or 0 for r in rows]
    vols = [r.get("vol", 0) or 0 for r in rows]
    dates = [r.get("trade_date", "") for r in rows]

    n_rows = len(rows)
    result: dict[str, Any] = {
        "n_rows": n_rows,
        "first_date": dates[0] if dates else "",
        "last_date": dates[-1] if dates else "",
        "latest_close": closes[-1] if closes else None,
    }

    # --- 趋势 ---
    ma_periods = (5, 10, 20, 60, 120, 250)
    ma_dict = _ma_values(closes, ma_periods)
    alignment = _ma_alignment(ma_dict, (5, 10, 20, 60))
    slopes = _ma_slope(ma_dict, k=5)

    trend: dict[str, Any] = {
        "ma": {str(p): vals for p, vals in ma_dict.items()},
        "alignment": alignment,
        "slope": {str(p): round(v, 2) if v is not None else None for p, v in slopes.items()},
    }
    # 为每个均线标注数据不足
    trend["ma_availability"] = {}
    for p in ma_periods:
        err = _require_len(rows, p, f"MA{p}")
        trend["ma_availability"][str(p)] = err  # None=可用, str=不可用原因

    # 趋势描述文本（供渲染使用）
    trend_sentences: list[str] = []
    for p in (20, 60, 120):
        ma_vals = ma_dict.get(p, [])
        latest_ma = ma_vals[-1] if ma_vals and ma_vals[-1] is not None else None
        slope = slopes.get(p)
        if latest_ma is not None and slope is not None:
            direction = f"斜率{'+' if slope >= 0 else ''}{slope:.1f}%"
            pos = "上方" if closes[-1] > latest_ma else ("下方" if closes[-1] < latest_ma else "附近")
            trend_sentences.append(f"MA{p}={latest_ma:.2f}（{direction}），收盘价位于其{pos}")
        elif latest_ma is not None:
            trend_sentences.append(f"MA{p}={latest_ma:.2f}，斜率数据不足")
        else:
            err = _require_len(rows, p, f"MA{p}")
            if err:
                trend_sentences.append(err)
    trend["summary_sentences"] = trend_sentences
    result["trend"] = trend

    # --- 动量 (MACD) ---
    macd_result = _macd(closes)
    # 提取最新值
    macd_latest: dict[str, Any] = {"available": True}
    if macd_result["dif_cross"] is None and all(v is None for v in macd_result["dif"]):
        macd_latest["available"] = False
        macd_latest["reason"] = _require_len(rows, 26, "MACD") or "MACD 数据不足"
    else:
        dif_last = _last_valid(macd_result["dif"])
        dea_last = _last_valid(macd_result["dea"])
        hist_last = _last_valid(macd_result["histogram"])
        macd_latest["dif"] = round(dif_last, 4) if dif_last is not None else None
        macd_latest["dea"] = round(dea_last, 4) if dea_last is not None else None
        macd_latest["histogram"] = round(hist_last, 4) if hist_last is not None else None
        macd_latest["cross"] = macd_result["dif_cross"]

        # 柱体放大/缩小趋势
        hist_valid = [(i, v) for i, v in enumerate(macd_result["histogram"]) if v is not None]
        if len(hist_valid) >= 5:
            recent_abs = [abs(v) for _, v in hist_valid[-5:]]
            if all(recent_abs[i] > recent_abs[i - 1] for i in range(1, len(recent_abs))):
                macd_latest["histogram_trend"] = "近5日柱体连续放大"
            elif all(recent_abs[i] < recent_abs[i - 1] for i in range(1, len(recent_abs))):
                macd_latest["histogram_trend"] = "近5日柱体连续缩小"
            else:
                macd_latest["histogram_trend"] = "柱体无连续方向"
        else:
            macd_latest["histogram_trend"] = None

    result["momentum"] = {"macd": macd_latest}

    # --- 超买超卖 ---
    rsi_periods = (6, 12, 24)
    rsi_data: dict[str, Any] = {}
    for n in rsi_periods:
        vals = _rsi(closes, n)
        latest = vals[-1] if vals else None
        if latest is not None:
            zone = "偏高" if latest > 70 else ("偏低" if latest < 30 else "中性")
            rsi_data[str(n)] = {"value": round(latest, 2), "zone": zone, "available": True}
        else:
            err = _require_len(rows, n + 1, f"RSI({n})")
            rsi_data[str(n)] = {"value": None, "zone": None, "available": False, "reason": err}
    result["overbought_oversold"] = {"rsi": rsi_data}

    # KDJ
    kdj_data = _kdj(highs, lows, closes)
    kdj_latest: dict[str, Any] = {}
    for key in ("k", "d", "j"):
        vals = kdj_data[key]
        latest = vals[-1] if vals else None
        kdj_latest[key] = round(latest, 2) if latest is not None else None
    kdj_latest["available"] = kdj_latest["k"] is not None
    if not kdj_latest["available"] and n_rows < 9:
        kdj_latest["reason"] = _require_len(rows, 9, "KDJ")
    result["overbought_oversold"]["kdj"] = kdj_latest

    # --- 波动 ---
    boll_data = _boll(closes)
    boll_latest: dict[str, Any] = {}
    for key in ("mid", "upper", "lower", "width"):
        vals = boll_data[key]
        boll_latest[key] = round(vals[-1], 2) if vals and vals[-1] is not None else None
    boll_latest["available"] = boll_latest["mid"] is not None
    if not boll_latest["available"]:
        boll_latest["reason"] = _require_len(rows, 20, "BOLL")
    else:
        # 判断收盘价在布林带的位置
        c = closes[-1]
        if boll_latest["upper"] and boll_latest["lower"]:
            band_range = boll_latest["upper"] - boll_latest["lower"]
            if band_range > 0:
                pos_pct = (c - boll_latest["lower"]) / band_range * 100
                if pos_pct > 80:
                    boll_latest["position"] = "近上轨"
                elif pos_pct < 20:
                    boll_latest["position"] = "近下轨"
                else:
                    boll_latest["position"] = "近中轨"
    result["volatility"] = {"boll": boll_latest}

    atr_vals = _atr(highs, lows, closes)
    atr_latest = atr_vals[-1] if atr_vals else None
    result["volatility"]["atr"] = {
        "value": round(atr_latest, 2) if atr_latest is not None else None,
        "available": atr_latest is not None,
    }
    if atr_latest is None and n_rows < 15:
        result["volatility"]["atr"]["reason"] = _require_len(rows, 15, "ATR(14)")

    # --- 成交量 ---
    vr = _volume_ratio(vols, 5)
    vr_latest = vr[-1] if vr else None
    vol_status = None
    if vr_latest is not None:
        if vr_latest > 1.5:
            vol_status = f"量比 {vr_latest:.2f}（高于近5日均量）"
        elif vr_latest < 0.5:
            vol_status = f"量比 {vr_latest:.2f}（低于近5日均量）"
        else:
            vol_status = f"量比 {vr_latest:.2f}（正常）"

    # 近20日放量/缩量天数
    vol_spike_days = 0
    vol_dry_days = 0
    for v in vr[-20:]:
        if v is not None:
            if v > 1.5:
                vol_spike_days += 1
            elif v < 0.5:
                vol_dry_days += 1

    result["volume"] = {
        "latest_ratio": round(vr_latest, 2) if vr_latest is not None else None,
        "status": vol_status,
        "avg_vol_5d": round(sum(vols[-5:]) / min(5, len(vols)), 2) if vols else None,
        "recent_spike_days": vol_spike_days,
        "recent_dry_days": vol_dry_days,
    }

    # --- 结构 ---
    extremes = _n_day_extremes(rows, (20, 60, 120))
    dd = _drawdown(closes, dates, 60)
    result["structure"] = {
        "extremes": extremes,
        "drawdown_60d": dd,
    }

    # --- v0.1.9 extensions ---
    ich = ichimoku_summary(highs, lows, closes)
    if ich:
        result["ichimoku"] = ich
    cone = volatility_cone(closes)
    if cone:
        result["volatility_cone"] = cone

    return result


# ---- v0.1.9: Ichimoku / Volatility Cone / RS / Beta ----

def _ichimoku(
    highs: list[float],
    lows: list[float],
    closes: list[float],
    tenkan_n: int = 9,
    kijun_n: int = 26,
    senkou_b_n: int = 52,
    displacement: int = 26,
) -> dict[str, list[float | None]]:
    """Ichimoku five lines."""
    length = len(closes)
    tenkan: list[float | None] = [None] * length
    kijun: list[float | None] = [None] * length
    senkou_a: list[float | None] = [None] * length
    senkou_b: list[float | None] = [None] * length
    chikou: list[float | None] = [None] * length

    for i in range(length):
        if i >= tenkan_n - 1:
            h = max(highs[i - tenkan_n + 1:i + 1])
            l = min(lows[i - tenkan_n + 1:i + 1])
            tenkan[i] = (h + l) / 2.0
        if i >= kijun_n - 1:
            h = max(highs[i - kijun_n + 1:i + 1])
            l = min(lows[i - kijun_n + 1:i + 1])
            kijun[i] = (h + l) / 2.0
        if i >= displacement:
            chikou[i - displacement] = closes[i]

    for i in range(length):
        if tenkan[i] is not None and kijun[i] is not None:
            a_val = (tenkan[i] + kijun[i]) / 2.0
            if i + displacement < length:
                senkou_a[i + displacement] = a_val
        if i >= senkou_b_n - 1:
            h = max(highs[i - senkou_b_n + 1:i + 1])
            l = min(lows[i - senkou_b_n + 1:i + 1])
            b_val = (h + l) / 2.0
            if i + displacement < length:
                senkou_b[i + displacement] = b_val

    return {
        "tenkan": tenkan,
        "kijun": kijun,
        "senkou_a": senkou_a,
        "senkou_b": senkou_b,
        "chikou": chikou,
    }


def ichimoku_summary(highs: list[float], lows: list[float], closes: list[float]) -> dict[str, Any]:
    """Ichimoku latest values for rendering."""
    if len(closes) < 52:
        return {"error": _require_len(
            [{"close": c} for c in closes], 52, "Ichimoku",
        )}
    lines = _ichimoku(highs, lows, closes)
    tenkan = _last_valid(lines["tenkan"])
    kijun = _last_valid(lines["kijun"])
    sa = _last_valid(lines["senkou_a"])
    sb = _last_valid(lines["senkou_b"])
    price = closes[-1]
    cloud_top = max(sa or 0, sb or 0) if sa is not None and sb is not None else None
    cloud_bot = min(sa or 0, sb or 0) if sa is not None and sb is not None else None
    pos = "—"
    if cloud_top is not None and cloud_bot is not None:
        if price > cloud_top:
            pos = "云带上方"
        elif price < cloud_bot:
            pos = "云带下方"
        else:
            pos = "云带内部"
    return {
        "tenkan_latest": round(tenkan, 2) if tenkan else None,
        "kijun_latest": round(kijun, 2) if kijun else None,
        "senkou_a_latest": round(sa, 2) if sa else None,
        "senkou_b_latest": round(sb, 2) if sb else None,
        "price_vs_cloud": pos,
    }


def volatility_cone(
    closes: list[float],
    window: int = 252,
    windows: tuple[int, ...] = (20, 60, 120, 252),
) -> dict[str, Any]:
    """Historical volatility cone (annualized HV %)."""
    if len(closes) < 30:
        return {"error": "数据不足，波动率锥不可得"}

    def _hv(series: list[float]) -> float | None:
        if len(series) < 2:
            return None
        rets = []
        for i in range(1, len(series)):
            if series[i - 1] > 0 and series[i] > 0:
                rets.append(math.log(series[i] / series[i - 1]))
        if len(rets) < 2:
            return None
        mean = sum(rets) / len(rets)
        var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
        return math.sqrt(var) * math.sqrt(252) * 100

    hvs: list[float] = []
    for end in range(window, len(closes) + 1):
        hv = _hv(closes[end - window:end])
        if hv is not None:
            hvs.append(hv)

    current = _hv(closes[-min(window, len(closes)):])
    if current is None:
        return {"error": "当前 HV 不可得"}

    percentile = None
    if hvs:
        below = sum(1 for h in hvs if h <= current)
        percentile = below / len(hvs) * 100

    by_window: dict[str, float | None] = {}
    for w in windows:
        by_window[str(w)] = _hv(closes[-w:]) if len(closes) >= w else None

    return {
        "current_hv": round(current, 2),
        "percentile": round(percentile, 1) if percentile is not None else None,
        "window": window,
        "by_window": by_window,
    }


def relative_strength(
    stock_closes: list[float],
    benchmark_closes: list[float],
) -> dict[str, Any]:
    """RS_t = (stock/benchmark) × 100, base period = first aligned point."""
    n = min(len(stock_closes), len(benchmark_closes))
    if n < 2:
        return {"error": "数据不足"}
    s0, b0 = stock_closes[-n], benchmark_closes[-n]
    if not s0 or not b0:
        return {"error": "基准或股价为零"}
    rs_series = [s / b * 100 * (b0 / s0) for s, b in zip(stock_closes[-n:], benchmark_closes[-n:])]
    return {
        "rs_latest": round(rs_series[-1], 2),
        "rs_start": round(rs_series[0], 2),
        "n": n,
    }


def rolling_beta(
    stock_closes: list[float],
    benchmark_closes: list[float],
    windows: list[int] | None = None,
) -> dict[str, Any]:
    """Rolling beta via valuation.calc_beta on return series."""
    from .valuation import calc_beta

    if windows is None:
        windows = [60, 120, 252]

    n = min(len(stock_closes), len(benchmark_closes))
    if n < 12:
        return {"error": "数据不足", "windows": {}}

    def _returns(closes: list[float]) -> list[float]:
        out: list[float] = []
        for i in range(1, len(closes)):
            if closes[i - 1] > 0:
                out.append(closes[i] / closes[i - 1] - 1)
        return out

    stock_rets = _returns(stock_closes[-n:])
    bench_rets = _returns(benchmark_closes[-n:])

    result_windows: dict[str, dict] = {}
    for w in windows:
        if len(stock_rets) < w or len(bench_rets) < w:
            result_windows[str(w)] = {"beta": None, "error": f"需要 ≥{w} 日"}
            continue
        info = calc_beta(stock_rets[-w:], bench_rets[-w:])
        result_windows[str(w)] = info

    return {"windows": result_windows, "benchmark": "000300.SH"}


def _last_valid(seq: list[float | None]) -> float | None:
    """返回序列最后一个非 None 值。"""
    for v in reversed(seq):
        if v is not None:
            return v
    return None
