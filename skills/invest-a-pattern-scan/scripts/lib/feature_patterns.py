"""C11 三特征模板（R-B01）——纯函数，不联网。

源文档 §3 R-B01 的三个特征模板。**证据边界（强制，随输出走）**：

- C11 整体形态**无直接证据**（hypothesis-registry C11 = 「待验证假设——**不得作规则层依据**」）；
  元素级相邻文献**部分支持**，但
  - 涨停后一周**反转**方向的证据（Seasholes-Wu 2007，**上证 2001-2003**；
    Chen et al. 深交所**账户级**）与「涨停确认续涨」**方向相反**且**样本为早期**；
  - 特征③的**条件分层假设**出自 Liu (2015)（**中文期刊，待核验**）——**不得作为框架依据**。
- 故三者**只作筛选特征**：须**自家样本后验 + RC p**，且事件研究引用**须带样本期注记**
  并在**当前市场滚动复核**后方可考虑规则化。
- 命中输出一律带 ``evidence_note``。

## 参数表（默认值，改动须记录理由并同步 C11 预注册）

| 特征 | 参数 | 默认 | 含义 |
|---|---|---|---|
| ① MACD 底背离 | `fast/slow/signal` | 12/26/9 | MACD 参数 |
| | `pivot_window` | 10 | 局部极值半窗（左右各 N 根） |
| | `max_pivots` | 4 | 回看最近 N 个低点 |
| ② 缩量回踩 | `peak_window` | 20 | 前高回看窗 |
| | `pullback_min_pct` | 5.0 | 回踩幅度下限（%） |
| | `shrink_max_ratio` | 0.7 | 回踩期量能 / 前高期量能上限 |
| | `max_pivots` | 4 | 保留**最近** N 次命中（模块常量 `MAX_PIVOTS`） |
| ③ 涨停站上均线 | `limit_up_pct` | 9.8 | 涨停判定阈值（%，主板口径） |
| | `ma` | 60 | 中期均线 |
"""
from __future__ import annotations

import math

# --- 证据注记（每特征一份，随命中输出）------------------------------------------
EVIDENCE_NOTE_MACD = (
    "⚠️ **底背离无顶级期刊直接检验**——本结果为**自家样本统计**，非规则层依据；"
    "元素级相邻文献（恐慌后反转）**部分支持**，但与「背离确认续涨」非同一命题。"
    "须自家样本后验 + RC p（见 backtest_prereg/C11_预注册.md）")
EVIDENCE_NOTE_SHRINK = (
    "⚠️ **缩量回踩无顶级期刊直接检验**——「缩量跌不动」类表述属**从业者惯例**"
    "（report-conventions §3.5），本特征**只作筛选**；须自家样本后验 + RC p")
EVIDENCE_NOTE_LIMIT_UP = (
    "⚠️ 条件分层假设出自 **Liu (2015)（中文期刊，待核验）** 与 S&W/Chen 的**早期样本**"
    "事件研究——**涨停方向与「确认续涨」相反且样本期为上证 2001-2003 / 深交所账户级**；"
    "引用须带样本期注记，进入规则层前须**当前市场滚动复核**")

# --- 参数表（见模块 docstring）--------------------------------------------------
MACD_FAST, MACD_SLOW, MACD_SIGNAL = 12, 26, 9
PIVOT_WINDOW = 10
MAX_PIVOTS = 4

PEAK_WINDOW = 20
PULLBACK_MIN_PCT = 5.0
SHRINK_MAX_RATIO = 0.7

LIMIT_UP_PCT = 9.8            # 主板判定阈值（= 10% 法定 − 容差）；其余板块用
                              # `limit_up_detect_pct(technical.limit_pct_for_symbol(...))`
LIMIT_UP_TOL_PCT = 0.2        # 法定涨跌幅 → 判定阈值的容差（最小变动单位四舍五入）
MID_MA = 60


def _ema(vals: list[float], span: int) -> list[float]:
    """指数移动平均（首值以第一个样本初始化）。"""
    if not vals:
        return []
    k = 2.0 / (span + 1.0)
    out = [vals[0]]
    for v in vals[1:]:
        out.append(v * k + out[-1] * (1 - k))
    return out


def macd_series(closes: list[float], *, fast: int = MACD_FAST,
                slow: int = MACD_SLOW, signal: int = MACD_SIGNAL) -> dict:
    """MACD 三线（dif / dea / hist）。样本不足 → 空列表。"""
    if len(closes) < slow + signal:
        return {"dif": [], "dea": [], "hist": []}
    ef, es = _ema(closes, fast), _ema(closes, slow)
    dif = [a - b for a, b in zip(ef, es)]
    dea = _ema(dif, signal)
    return {"dif": dif, "dea": dea, "hist": [d - e for d, e in zip(dif, dea)]}


def _pivot_lows(vals: list[float], *, half: int = PIVOT_WINDOW,
                max_n: int = MAX_PIVOTS) -> list[int]:
    """局部低点索引（左右各 half 根内最小），取最近 max_n 个（升序）。"""
    idxs: list[int] = []
    for i in range(half, len(vals) - half):
        seg = vals[i - half: i + half + 1]
        if vals[i] == min(seg) and vals[i] < vals[i - 1]:
            idxs.append(i)
    return idxs[-max_n:]


def detect_macd_divergence(closes: list[float], *, pivot_window: int = PIVOT_WINDOW,
                           max_pivots: int = MAX_PIVOTS) -> list[dict]:
    """① MACD 底背离：**价格创新低 + MACD 低点抬高**。

    量化定义：取最近 `max_pivots` 个价格局部低点；若**相邻两低点**满足
    `price[i2] < price[i1]` 且 `dif[i2] > dif[i1]` → 在后一低点记一次底背离。
    """
    macd = macd_series(closes)
    dif = macd["dif"]
    if not dif:
        return []
    lows = _pivot_lows(closes, half=pivot_window, max_n=max_pivots)
    out: list[dict] = []
    for i1, i2 in zip(lows, lows[1:]):
        if closes[i2] < closes[i1] and dif[i2] > dif[i1]:
            out.append({
                "endpoint_idx": i2,
                "detail": {
                    "kind": "macd_divergence",
                    "prev_low_idx": i1, "low_idx": i2,
                    "price_prev": round(closes[i1], 4), "price_now": round(closes[i2], 4),
                    "dif_prev": round(dif[i1], 6), "dif_now": round(dif[i2], 6),
                    "params": {"macd": f"{MACD_FAST}/{MACD_SLOW}/{MACD_SIGNAL}",
                               "pivot_window": pivot_window, "max_pivots": max_pivots},
                },
                "evidence_note": EVIDENCE_NOTE_MACD,
            })
    return out


def _f(v) -> float | None:
    """数值化并**剔除 NaN/Inf**——NaN 会穿过 `is not None` 让比较恒为 False，
    使闸门静默失效（实测量价：NaN 量能 → 放量回踩被报成「缩量回踩」，且 NaN 落盘）。"""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if (f != f or math.isinf(f)) else f


def detect_shrink_pullback(closes: list[float], vols: list[float | None], *,
                           peak_window: int = PEAK_WINDOW,
                           pullback_min_pct: float = PULLBACK_MIN_PCT,
                           shrink_max_ratio: float = SHRINK_MAX_RATIO) -> list[dict]:
    """② 缩量回踩：自前高**回踩幅度** ≥ 阈值，且回踩期**量能收缩** ≤ 阈值。

    **历史事件检测**（对每个 i 判定），不是「当前状态」判定：
    终点若恒为最后一根 K，则前向收益窗口恒空 → 该规则在 RC 规则矩阵里恒为全 0 列
    （实测：终点恒 35/35，三窗口前向全空；RC 实际只在 24 条而非 27 条规则上算）。

    ⚠️ 「缩量」语义限定为**低活动/信息真空态**，**禁止**映射「支撑成立/不会破位」。
    """
    n = len(closes)
    if n < peak_window + 5:
        return []
    vals = [_f(v) for v in (vols or [])]
    out: list[dict] = []
    for i in range(peak_window, n - 1):        # 留 1 根：终点须可被后续确认
        peak_i = max(range(i - peak_window, i + 1), key=lambda k: closes[k])
        peak_px = closes[peak_i]
        if peak_px <= 0 or peak_i >= i:        # 前高须早于当前点
            continue
        pullback_pct = (peak_px - closes[i]) / peak_px * 100.0
        if pullback_pct < pullback_min_pct:
            continue
        peak_vols = [v for v in vals[max(0, peak_i - 3): peak_i + 4] if v is not None]
        pull_vols = [v for v in vals[peak_i + 1: i + 1] if v is not None]
        # ⚠️ 量能**不可得 → 不构成「缩量回踩」**（该特征的定义就是量能条件）。
        # 曾把「无量」当命中发出（`shrink_ratio=None` + `volume_available=False`）——
        # 该命中会被 `pattern_scanner` 记为 `shrink_pullback` 并进入 RC 规则矩阵
        # `shrink_pullback_+h`，等于拿**从未观测到量能**的事件去检验 C11 的缩量假设。
        # 全 NaN 同理：NaN 会穿过闸门（`nan > 阈值` 恒 False）把放量踩踏报成缩量。
        if not peak_vols or not pull_vols:
            continue
        pv, cv = sum(peak_vols) / len(peak_vols), sum(pull_vols) / len(pull_vols)
        shrink_ratio = (cv / pv) if pv else None
        if shrink_ratio is None or shrink_ratio > shrink_max_ratio:
            continue
        out.append({
            "endpoint_idx": i,
            "detail": {
                "kind": "shrink_pullback",
                "peak_idx": peak_i, "peak_price": round(peak_px, 4),
                "pullback_pct": round(pullback_pct, 2),
                "shrink_ratio": round(shrink_ratio, 3) if shrink_ratio is not None else None,
                "volume_available": True,     # 缺量/全 NaN 已在上方闸门拦下，不会再发命中
                "semantics": ("缩量 = **低活动/信息真空态**；**禁止**映射「支撑成立/不会破位」"),
                "params": {"peak_window": peak_window, "pullback_min_pct": pullback_min_pct,
                           "shrink_max_ratio": shrink_max_ratio},
            },
            "evidence_note": EVIDENCE_NOTE_SHRINK,
        })
    return out[-MAX_PIVOTS:] if len(out) > MAX_PIVOTS else out


def detect_limit_up_above_ma(closes: list[float], *, limit_up_pct: float = LIMIT_UP_PCT,
                             ma: int = MID_MA) -> list[dict]:
    """③ 涨停站上中期均线（**条件分层假设，待核验**）。

    涨停 = 当日涨幅 ≥ 阈值（主板 9.8%，**不含**创业板/科创板 20% 与 ST 5% 口径——
    口径差异须在调用侧按板块分流或显式标注）。
    """
    n = len(closes)
    if n < ma + 1:
        return []
    out: list[dict] = []
    for i in range(ma, n):
        prev = closes[i - 1]
        if not prev:
            continue
        chg = (closes[i] / prev - 1.0) * 100.0
        if chg < limit_up_pct:
            continue
        ma_val = sum(closes[i - ma + 1: i + 1]) / ma
        if closes[i] > ma_val:
            out.append({
                "endpoint_idx": i,
                "detail": {
                    "kind": "limit_up_above_ma",
                    "chg_pct": round(chg, 2), "ma_value": round(ma_val, 4),
                    "close": round(closes[i], 4),
                    "layering_hypothesis": ("条件分层假设（连板数/换手/公告性质）"
                                            "出自 Liu (2015)（中文期刊，**待核验**）"),
                    "params": {"limit_up_pct": limit_up_pct, "ma": ma},
                },
                "evidence_note": EVIDENCE_NOTE_LIMIT_UP,
            })
    return out


FEATURES: tuple[str, ...] = ("macd_divergence", "shrink_pullback", "limit_up_above_ma")
EVIDENCE_NOTES: dict[str, str] = {
    "macd_divergence": EVIDENCE_NOTE_MACD,
    "shrink_pullback": EVIDENCE_NOTE_SHRINK,
    "limit_up_above_ma": EVIDENCE_NOTE_LIMIT_UP,
}


def limit_up_detect_pct(limit_pct: float) -> float:
    """法定涨跌幅（主板 10 / 创业板·科创板 20 / 北交所 30 / 主板 ST 5）→ **判定阈值**。

    扣 `LIMIT_UP_TOL_PCT` 容差（涨跌停价按最小变动单位四舍五入，实际涨幅略低于法定值）。
    **调用方须按板块传入**——全池套 9.8 会把 10%+ 的创业板/科创板正常波动当涨停
    （C11 预注册明示的坑）。阈值表唯一权威见 `technical.limit_pct_for_symbol`。
    """
    return round(float(limit_pct) - LIMIT_UP_TOL_PCT, 4)


def detect_all(closes: list[float], vols: list[float | None] | None = None, *,
               limit_up_pct: float = LIMIT_UP_PCT) -> list[dict]:
    """三个 C11 特征模板一起跑；每条命中带 ``evidence_note``。

    ``limit_up_pct`` 须由调用方**按标的板块**给出（见 `limit_up_detect_pct`）；
    默认值保留主板口径以兼容既有调用。
    """
    out = detect_macd_divergence(closes)
    out += detect_shrink_pullback(closes, vols or [None] * len(closes))
    out += detect_limit_up_above_ma(closes, limit_up_pct=limit_up_pct)
    return out


def rule_keys(horizons: tuple[int, ...] = (5, 10, 20)) -> list[str]:
    """三特征在规则宇宙中的键（无带宽维度——三者都不是核平滑形态）。"""
    return [f"{f}_+{h}" for f in FEATURES for h in horizons]


# ---------------------------------------------------------------------------
# R-B02 左右侧双组回测
# ---------------------------------------------------------------------------

def group_stats(returns: list[float]) -> dict:
    """单组统计：样本量 / 胜率 / 均值 / 中位 / 最大回撤（自累计净值曲线）。"""
    vals = [float(r) for r in (returns or []) if r is not None]
    n = len(vals)
    if not n:
        return {"n": 0, "win_rate_pct": None, "mean_pct": None,
                "median_pct": None, "max_drawdown_pct": None}
    srt = sorted(vals)
    eq = [1.0]
    for r in vals:
        eq.append(eq[-1] * (1.0 + r))
    peak, mdd = eq[0], 0.0
    for v in eq:
        peak = max(peak, v)
        if peak > 0:
            mdd = min(mdd, v / peak - 1.0)
    return {
        "n": n,
        "win_rate_pct": round(sum(1 for r in vals if r > 0) / n * 100, 2),
        "mean_pct": round(sum(vals) / n * 100, 3),
        "median_pct": round(srt[n // 2] * 100, 3),
        "max_drawdown_pct": round(mdd * 100, 3),
    }


def left_right_backtest(*, right_returns: list[float], left_returns: list[float],
                        horizon: int, right_label: str = "突破确认组（右侧）",
                        left_label: str = "未确认底背离组（左侧）") -> dict:
    """双组条件回测（**同池同窗**）：胜率 / 超额 / 最大回撤对照。

    ⚠️ **禁止预设结论**：输出只给数值与样本量，措辞模板固定为
    「本池样本内右侧胜率 X% vs 左侧 Y%，样本 N」——**不得**写「右侧更优/更稳健」。
    """
    r, l = group_stats(right_returns), group_stats(left_returns)
    return {
        "horizon": horizon,
        "groups": {right_label: r, left_label: l},
        "wording_template": (
            f"本池样本内{right_label}胜率 {r['win_rate_pct']}% vs {left_label} "
            f"{l['win_rate_pct']}%，样本 {r['n']}/{l['n']}"
            if (r["n"] and l["n"]) else "样本不足，不出对照结论"),
        # 措辞刻意**不复现**被禁短语——禁令引用禁词会让「禁预设结论」的自检
        # 把 caveat 自身判成违规（本会话第四次同类）
        "caveat": ("**禁止预设结论**：文献对左右侧的相对优劣无定论"
                   "（美股动量 vs A 股反转/规则失效），须由本地样本回答；"
                   "本表只并列数值，**不作任何「哪侧更好」的判断**"),
        "note": ("A 股语境注记：涨跌停/T+1/散户结构影响可执行性；"
                 "事件研究引用须带样本期（S&W 上证 2001-2003 / Chen 深交所账户级）"),
    }
