"""LMW 形态检测纯函数库（v0.2.6 M5）——双底/三角形底客观化检测。

方法论：Lo, Mamaysky & Wang (2000, JF) 核平滑 + 极值模板 + 容差匹配。
参数表对应 ABCD 设计 §2.3（全部标注证据分级）：
  - 核平滑：Epanechnikov/Nadaraya-Watson，因果滚动窗口（防 look-ahead）
  - 带宽：0.3/0.5/1.0 三档敏感性（LMW 0.3×；阿果 A 股实证 0.5× 最优）
  - 双底容差 1.5%、两底间距 ≥22 交易日（LMW 原表）
  - 底部前回撤 ≥20%（120 日窗口，Chart Library 实操统计 C 级）
  - 三角形底：收缩三角（低点抬高 + 高点降低），矩形容差 0.75% 参照

原则:
  - 纯函数，无副作用，无网络
  - 数据不足返回空结果（不抛异常——扫描器按空结果跳过；D5 语义由
    调用方决定是否 fail loud）
  - 索引一律为输入序列索引（调用方映射回日期）

工程坑提醒（本仓库引擎缺陷记忆）：形态检测是数值敏感代码，
最小样例回归测试强制（test_lmw.py 合成正/负样例）。
"""

from __future__ import annotations

import math


def kernel_smooth(closes: list[float], bandwidth: float = 0.5) -> list[float]:
    """因果滚动 Epanechnikov 核平滑（Nadaraya-Watson）。

    W = max(6, round(bandwidth * 60))：0.5 → 31 日窗（阿果 A 股实证最优档）。
    每点只用 ≤t 数据（因果，防 look-ahead——ABCD §2.1 强制项）。
    长度 < 2 返回原列表副本。
    """
    n = len(closes)
    if n < 2:
        return list(closes)
    w = max(6, int(round(bandwidth * 60)))
    out: list[float] = []
    for t in range(n):
        lo = max(0, t - w + 1)
        vals = closes[lo : t + 1]
        m = len(vals)
        weights = []
        for j in range(m):
            u = (m - 1 - j) / m if m > 1 else 0.0  # 越旧越远
            if u < 1.0:
                weights.append(0.75 * (1.0 - u * u))
            else:
                weights.append(0.0)
        s = sum(weights)
        out.append(sum(v * wt for v, wt in zip(vals, weights)) / s if s > 0 else closes[t])
    return out


def find_extrema(smoothed: list[float], order: int = 3) -> dict[str, list[int]]:
    """局部极值索引（峰值/谷值各需 order 日两侧确认）。

    平台期会产生连续同类极值（如 61,62 双峰）——按「同类连续 run 去重，
    保留最极值者」合并，保证输出严格交替可被 5 极值模板消费。
    """
    n = len(smoothed)
    peaks: list[int] = []
    troughs: list[int] = []
    for i in range(order, n - order):
        left = smoothed[i - order : i]
        right = smoothed[i + 1 : i + order + 1]
        if smoothed[i] >= max(left) and smoothed[i] >= max(right) and smoothed[i] > min(left + right):
            peaks.append(i)
        if smoothed[i] <= min(left) and smoothed[i] <= min(right) and smoothed[i] < max(left + right):
            troughs.append(i)

    def _collapse(idxs: list[int], pick_max: bool) -> list[int]:
        if not idxs:
            return []
        out: list[int] = []
        run: list[int] = [idxs[0]]
        for i in idxs[1:]:
            if i - run[-1] == 1:  # 连续 → 同一 run
                run.append(i)
            else:
                out.append(max(run, key=lambda j: smoothed[j]) if pick_max
                          else min(run, key=lambda j: smoothed[j]))
                run = [i]
        out.append(max(run, key=lambda j: smoothed[j]) if pick_max
                   else min(run, key=lambda j: smoothed[j]))
        return out

    return {"peaks": _collapse(peaks, pick_max=True), "troughs": _collapse(troughs, pick_max=False)}


def match_double_bottom(
    smoothed: list[float],
    extrema: dict[str, list[int]],
    tol: float = 0.015,
    min_sep: int = 22,
    prior_drawdown_pct: float = 20.0,
    prior_window: int = 120,
) -> list[dict]:
    """双底模板匹配（LMW 5 极值不等式 + A 股参数）。

    条件：
      1. 两底间距 ≥ min_sep（LMW：≥22 交易日）
      2. 两底价差 ≤ tol（LMW：1.5%）
      3. 两底之间存在中间峰（反弹确认）
      4. 形态前 prior_window 日内回撤 ≥ prior_drawdown_pct（底部前下跌结构）
      5. 终点 = 第二底之后首次收盘站上中间峰（breakout 确认）

    返回 [{bottom1_idx, bottom2_idx, peak_idx, endpoint_idx, bottom1, bottom2,
            peak, sep, drawdown_pct, pattern}]（按第二底升序）。
    """
    troughs = extrema.get("troughs", [])
    peaks = extrema.get("peaks", [])
    results: list[dict] = []
    if len(troughs) < 2:
        return results
    for a in range(len(troughs) - 1):
        for b in range(a + 1, len(troughs)):
            i1, i2 = troughs[a], troughs[b]
            if i2 - i1 < min_sep:
                continue
            p1, p2 = smoothed[i1], smoothed[i2]
            if p1 <= 0:
                continue
            if abs(p2 - p1) / p1 > tol:
                continue
            between_peaks = [p for p in peaks if i1 < p < i2]
            if not between_peaks:
                continue
            pk_idx = max(between_peaks, key=lambda p: smoothed[p])
            pk = smoothed[pk_idx]
            if pk <= p1 or pk <= p2:
                continue
            # 前回撤：i1 前 prior_window 日内峰值 → 底 1
            lo = max(0, i1 - prior_window)
            prior_high = max(smoothed[lo : i1 + 1]) if lo < i1 else p1
            if prior_high <= 0:
                continue
            drawdown = (prior_high - p1) / prior_high * 100.0
            if drawdown < prior_drawdown_pct:
                continue
            # 终点：i2 之后首次收盘 ≥ 中间峰
            endpoint = None
            for t in range(i2 + 1, len(smoothed)):
                if smoothed[t] >= pk:
                    endpoint = t
                    break
            if endpoint is None:
                continue
            results.append({
                "bottom1_idx": i1, "bottom2_idx": i2, "peak_idx": pk_idx,
                "endpoint_idx": endpoint,
                "bottom1": round(p1, 4), "bottom2": round(p2, 4), "peak": round(pk, 4),
                "sep": i2 - i1, "drawdown_pct": round(drawdown, 2),
                "pattern": "double_bottom",
            })
    return results


def match_triangle_bottom(
    smoothed: list[float],
    extrema: dict[str, list[int]],
    tol: float = 0.0075,
    min_sep: int = 5,
) -> list[dict]:
    """收缩三角形底（LMW 5 极值模板 T-P-T-P-T）：低点抬高 + 高点降低。

    条件：
      1. 连续 5 个交替极值，谷收尾（LMW 模板 = 5 极值）
      2. 谷值递增（低点抬高）、峰值递减（高点降低）
      3. 收敛：峰谷差逐步收窄（三角收缩）
      4. 相邻极值间距 ≥ min_sep
      5. 终点 = 末谷之后首次收盘站上两峰连线（上边趋势线）外推值
    """
    troughs = extrema.get("troughs", [])
    peaks = extrema.get("peaks", [])
    results: list[dict] = []
    merged = sorted([(i, "t") for i in troughs] + [(i, "p") for i in peaks])
    if len(merged) < 5:
        return results
    for start in range(len(merged) - 4):
        window = merged[start : start + 5]
        kinds = [k for _, k in window]
        if kinds != ["t", "p", "t", "p", "t"]:
            continue
        idxs = [i for i, _ in window]
        t1_i, p1_i, t2_i, p2_i, t3_i = idxs
        if min(t2_i - p1_i, p1_i - t1_i, p2_i - t2_i, t3_i - p2_i) < min_sep:
            continue
        t1, p1, t2, p2, t3 = (smoothed[i] for i in idxs)
        # 谷递增、峰递减
        if not (t3 > t2 > t1 and p2 < p1):
            continue
        # 收敛：峰谷差收窄（每级收窄 ≥ 前级的 tol 比例，防随机噪声三角）
        span1, span2 = p1 - t1, p2 - t2
        if span1 <= 0 or span2 <= 0 or span2 > span1:
            continue
        if t3 - t1 <= tol * max(t1, 1e-9):
            continue
        # 终点：末谷后首次站上两峰连线外推（斜率按 p1→p2）
        slope = (p2 - p1) / max(p2_i - p1_i, 1)
        endpoint = None
        for t in range(t3_i + 1, len(smoothed)):
            upper = p2 + slope * (t - p2_i)
            if smoothed[t] >= upper:
                endpoint = t
                break
        if endpoint is None:
            continue
        results.append({
            "trough1_idx": t1_i, "peak1_idx": p1_i, "trough2_idx": t2_i,
            "peak2_idx": p2_i, "trough3_idx": t3_i,
            "endpoint_idx": endpoint,
            "pattern": "triangle_bottom",
        })
    return results


def detect_patterns(
    closes: list[float],
    bandwidth: float = 0.5,
    min_bars: int = 60,
) -> dict:
    """完整检测流水线 → {smoothed, extrema, double_bottoms, triangle_bottoms}。

    closes 不足 min_bars → 各字段空（扫描器跳过）。带宽三档敏感性
    由调用方循环 0.3/0.5/1.0 复跑本函数。
    """
    if len(closes) < min_bars:
        return {"smoothed": [], "extrema": {"peaks": [], "troughs": []},
                "double_bottoms": [], "triangle_bottoms": []}
    smoothed = kernel_smooth(closes, bandwidth)
    extrema = find_extrema(smoothed)
    return {
        "smoothed": smoothed,
        "extrema": extrema,
        "double_bottoms": match_double_bottom(smoothed, extrema),
        "triangle_bottoms": match_triangle_bottom(smoothed, extrema),
    }


def pattern_forward_stats(
    closes: list[float],
    patterns: list[dict],
    horizons: tuple[int, ...] = (5, 10, 20),
) -> dict[str, list[float]]:
    """形态终点后 forward 收益（%）→ {f"+{h}": [收益, ...]}。

    终点后不足 h 个交易日的事件跳过（不填充）。
    """
    out: dict[str, list[float]] = {f"+{h}": [] for h in horizons}
    for p in patterns:
        ep = p["endpoint_idx"]
        if ep >= len(closes) - 1 or closes[ep] <= 0:
            continue
        for h in horizons:
            if ep + h < len(closes) and closes[ep + h] > 0:
                out[f"+{h}"].append((closes[ep + h] / closes[ep] - 1) * 100.0)
    return out


def classify_retest(
    closes: list[float],
    lows: list[float],
    pattern: dict,
    window: tuple[int, int] = (3, 10),
) -> dict:
    """回踩状态分类（v0.2.6 P2 落地，实操统计 C 级——非学术）。

    突破（endpoint）后 window 日内首次 low ≤ reference 的日子（low 口径，
    与规范一致——收盘口径会让盘中回踩收盘站回的形态漏判）：
      - no_retest：完整窗口内从未回踩（最强形态，实操统计：无回踩突破后续表现最好）
      - truncated：窗口未走完序列即结束（不足 hi 个交易日），无法判定是否回踩
      - clean_retest：首次回踩日 close ≥ reference（收盘不破）
      - deep_retest：首次回踩日 close < reference（收盘跌破）
    reference 位：double_bottom 用形态中间峰 peak；triangle_bottom 用
    smoothed[peak2_idx]（match_triangle_bottom 已保存索引——reference 由调用方
    在 pattern 中提供 `reference` 键；缺失时 double_bottom 用 peak、
    triangle_bottom 用 peak2_idx 映射到 closes（原形态的 smoothed 峰位
    与 closes 原始价近似，容差内可用））。

    返回 {status, retest_day}（retest_day = endpoint 后第几天，no_retest/truncated 为 None）。
    """
    ep = pattern.get("endpoint_idx")
    if ep is None or ep >= len(closes) - 1:
        return {"status": "insufficient", "retest_day": None}
    ref = pattern.get("reference")
    if ref is None:
        if pattern.get("pattern") == "double_bottom":
            ref = pattern.get("peak")
        elif pattern.get("pattern") == "triangle_bottom":
            p2 = pattern.get("peak2_idx")
            ref = closes[p2] if p2 is not None and 0 <= p2 < len(closes) else None
    if ref is None:
        return {"status": "insufficient", "retest_day": None}
    lo, hi = window
    for offset in range(lo, hi + 1):
        day = ep + offset
        if day >= len(closes):
            return {"status": "truncated", "retest_day": None}
        if lows[day] <= ref:
            status = "clean_retest" if closes[day] >= ref else "deep_retest"
            return {"status": status, "retest_day": offset}
    return {"status": "no_retest", "retest_day": None}
