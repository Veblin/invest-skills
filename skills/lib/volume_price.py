"""量价观察特征（R-A03）+ 条件性反转观测窗（R-A04）——纯函数，不联网。

**供 stock / etf / journal / pattern-scan 共用**（源文档 §3 R-A03 明定落点为 `skills/lib` 数据层）。

## 证据边界（须随输出走，不得省略）

- R-A03 是**自定义观察特征**——**不声称复现任何论文机制**；须**自家样本后验后**才考虑规则化
  （hypothesis-registry C7 = 未落地 → 本轮登记；验证方案见 `references/backtest_prereg/C7_预注册.md`）。
- LMSW 量-收益交互系数 b2 的**符号仅作统计方向描述**（正 = 随量延续、负 = 随量反转）；
  「投机/知情 vs 风险分担」的**身份解释仅限原论文美国样本（NYSE/AMEX 个股）语境**——
  **A 股输出不得用作交易者身份 / 资金行为分类标签**。
- 缩量字段的语义**限定为「低活动 / 信息真空态」**，**禁止**映射为「支撑成立 / 不会破位」。
- 退潮特征（放量日 + 次日动量衰减）是**自定义观测**，**非任何论文机制的复现**——
  Chen et al. 2019 / Seasholes-Wu 2007 的机制建立在**账户级净买入**与**涨停事件**语境之上，
  量价代理不可复现其机制。

## 时间口径

`rows` 为**按日期升序**的行情行（至少含 `trade_date` / `close` / `vol`）。
所有分位均在**样本内**校准（窗口可配），不引外部参数。
"""
from __future__ import annotations

import math

from backtest import ols_multi  # noqa: E402 —— 共享回归工具（含系数/标准误/t/n）
from stats import percentile_rank_inclusive  # noqa: E402 —— 共享统计库

# --- R-A03 阈值（样本内校准；改动须记录理由并同步 C7 预注册） -------------------
VOLUME_HIGH_PCTILE = 90.0     # > 此分位 = 放量日
VOLUME_LOW_PCTILE = 40.0      # < 此分位 = 缩量日
DEFAULT_WINDOW = 250          # 量分位窗口（约一年交易日）
LMSM_WINDOW = 60              # b2 回归窗口
RISK_MA = 60                  # 风险态的「关键均线」

_CALIBER = ("量分位/放量缩量判定 = 样本内自历史分位（窗口可配）；"
            "b2 = OLS(r[t+1] ~ r[t] + V[t]·r[t]) 的第二个系数，V 取**量分位/100**（0–1）；"
            "所有特征为**观察性描述**，非信号")
_IDENTITY_NOTE = ("b2 符号**仅作统计方向描述**（正=随量延续 / 负=随量反转）；"
                  "「投机/知情 vs 风险分担」**身份解释仅限原论文美国样本（NYSE/AMEX 个股）语境**"
                  "——**A 股输出不得用作交易者身份/资金行为分类标签**")
_LOW_VOL_SEMANTICS = ("缩量 = **低活动 / 信息真空态**；"
                      "**禁止**映射为「支撑成立 / 不会破位」（二者无实证对应）")


def _vals(rows: list[dict], key: str) -> list[float | None]:
    out: list[float | None] = []
    for r in rows or []:
        v = r.get(key)
        try:
            f = float(v) if v is not None else None
        except (TypeError, ValueError):
            f = None
        if f is not None and (f != f or math.isinf(f)):
            f = None
        out.append(f)
    return out


def _volume_series(rows: list[dict]) -> list[float | None]:
    """成交量序列；缺 `vol` 时回退 `amount`（额）。"""
    v = _vals(rows, "vol")
    if any(x is not None for x in v):
        return v
    return _vals(rows, "amount")


def _state_of(pctl: float | None) -> str:
    if pctl is None:
        return "不可得"
    if pctl > VOLUME_HIGH_PCTILE:
        return "放量日"
    if pctl < VOLUME_LOW_PCTILE:
        return "缩量日"
    return "常态"


# ---------------------------------------------------------------------------
# R-A03 ① 量比 / 换手分位
# ---------------------------------------------------------------------------

def volume_percentile(rows: list[dict], *, window: int = DEFAULT_WINDOW) -> dict:
    """量分位（样本内自历史分位）→ 放量日 / 缩量日 / 常态。"""
    vols = [v for v in _volume_series(rows) if v is not None]
    if len(vols) < 20:
        return {"available": False, "vol_pctile": None, "state": "不可得",
                "n": len(vols), "window": window, "reason": "量序列不足 20 点",
                "semantics": _LOW_VOL_SEMANTICS}
    tail = vols[-window:]
    cur = tail[-1]
    base = tail[:-1] or tail
    pctl = percentile_rank_inclusive(base, cur)
    return {"available": True, "vol_pctile": round(pctl, 2),
            "state": _state_of(pctl), "n": len(base), "window": window,
            "semantics": _LOW_VOL_SEMANTICS}


# ---------------------------------------------------------------------------
# R-A03 ② LMSW 型量-收益交互 b2（滚动系数）
# ---------------------------------------------------------------------------

def lmsm_b2(rows: list[dict], *, window: int = LMSM_WINDOW) -> dict:
    """``r(t+1) ~ r(t) + V(t)·r(t)`` 的 b2（含**系数值 / 95% CI / 样本量**）。

    V(t) 取**量分位/100**（0–1，有界且跨标的可比）。样本不足或 rank 退化 → ``available=False``
    （**不臆造系数**）。
    """
    closes = _vals(rows, "close")
    vols = _volume_series(rows)
    rets: list[float | None] = [None]
    for i in range(1, len(closes)):
        a, b = closes[i], closes[i - 1]
        rets.append((a / b - 1.0) if (a is not None and b) else None)

    vp_ = percentile_rank_inclusive
    items: list[tuple[float, float, float]] = []      # (r[t], V[t], r[t+1])
    for t in range(1, len(rets) - 1):
        r_t, r_n, v_t = rets[t], rets[t + 1], vols[t]
        if r_t is None or r_n is None or v_t is None:
            continue
        base_vols = [v for v in vols[max(0, t - window):t] if v is not None]
        if len(base_vols) < 20:
            continue
        v_norm = vp_(base_vols, v_t) / 100.0
        items.append((r_t, v_norm, r_n))

    if len(items) < 30:
        return {"available": False, "b2": None, "ci95_low": None, "ci95_high": None,
                "t_stat": None, "n": len(items), "window": window,
                "reason": f"有效样本 {len(items)} < 30，不足以估计 b2",
                "sign_note": "符号仅作统计方向描述", "identity_note": _IDENTITY_NOTE,
                "caliber": _CALIBER}
    tail = items[-window:] if len(items) > window else items
    y = [it[2] for it in tail]
    x1 = [it[0] for it in tail]
    x2 = [it[0] * it[1] for it in tail]
    if len(set(x2)) < 3:
        return {"available": False, "b2": None, "ci95_low": None, "ci95_high": None,
                "t_stat": None, "n": len(tail), "window": window,
                "reason": "交互项退化（量分位几乎不变），不可估计",
                "sign_note": "符号仅作统计方向描述", "identity_note": _IDENTITY_NOTE,
                "caliber": _CALIBER}
    res = ols_multi(y, [x1, x2], names=["r_t", "V_r"])
    b2, se2, t2 = res["coefs"][1], res["se"][2], res["t_stats"][2]
    return {"available": True, "b2": round(b2, 6),
            "ci95_low": round(b2 - 1.96 * se2, 6),
            "ci95_high": round(b2 + 1.96 * se2, 6),
            "t_stat": round(t2, 3), "n": res["n"], "window": window,
            "sign_note": ("符号仅作统计方向描述（正=随量延续 / 负=随量反转）；"
                          "**不含因果与身份含义**"),
            "identity_note": _IDENTITY_NOTE, "caliber": _CALIBER}


# ---------------------------------------------------------------------------
# R-A03 ③ 风险态观测：放量 + 跌破关键均线（+ 高换手，若有）
# ---------------------------------------------------------------------------

def risk_state(rows: list[dict], *, ma: int = RISK_MA,
               window: int = DEFAULT_WINDOW) -> dict:
    """「放量 + 跌破关键均线（+ 高换手）」风险态观测。

    换手率字段（`turnover_rate`）缺失时该项标 ``None``（**不冒充已判定**），
    整体结论以两项可得者为准。
    """
    closes = [c for c in _vals(rows, "close") if c is not None]
    if len(closes) < ma + 1:
        return {"available": False, "is_risk_state": None, "components": {},
                "reason": f"K 线不足 {ma + 1} 行", "n": len(closes),
                "caliber": _CALIBER}
    ma_val = sum(closes[-ma:]) / ma
    cur = closes[-1]
    vp_out = volume_percentile(rows, window=window)
    high_volume = (vp_out.get("vol_pctile") is not None
                   and vp_out["vol_pctile"] > VOLUME_HIGH_PCTILE)
    below_ma = cur < ma_val
    turns = _vals(rows, "turnover_rate")
    turn_pctl = None
    if len([t for t in turns if t is not None]) >= 20:
        base = [t for t in turns if t is not None][:-1]
        if base:
            turn_pctl = percentile_rank_inclusive(base, turns[-1])
    high_turn = (turn_pctl is not None and turn_pctl > VOLUME_HIGH_PCTILE)
    return {
        "available": True,
        "is_risk_state": bool(high_volume and below_ma),
        "components": {"high_volume": high_volume, "below_ma": below_ma,
                       "high_turnover": high_turn,
                       "ma_value": round(ma_val, 4), "close": cur,
                       "turnover_pctile": None if turn_pctl is None else round(turn_pctl, 2)},
        "n": len(closes),
        "note": ("风险态 = **观察标签**（放量 ∧ 跌破关键均线）；"
                 "换手率字段可得时并列展示，缺失不冒充"),
        "caliber": _CALIBER,
    }


# ---------------------------------------------------------------------------
# R-A03 ④ 自定义退潮观察：放量日 + 次日动量衰减
# ---------------------------------------------------------------------------

def fade_feature(rows: list[dict], *, window: int = DEFAULT_WINDOW) -> dict:
    """退潮观察特征 = **放量日 + 次日动量衰减**（自定义观测，**非论文复现**）。"""
    closes = _vals(rows, "close")
    vols = _volume_series(rows)
    n_vol_days = n_faded = 0
    for i in range(20, len(closes) - 1):
        if vols[i] is None or closes[i] is None or closes[i + 1] is None or closes[i-1] is None:
            continue
        base = [v for v in vols[max(0, i - window):i] if v is not None]
        if len(base) < 20:
            continue
        if percentile_rank_inclusive(base, vols[i]) <= VOLUME_HIGH_PCTILE:
            continue
        n_vol_days += 1
        if closes[i + 1] < closes[i]:      # 次日动量衰减（下跌）
            n_faded += 1
    return {
        "available": n_vol_days > 0,
        "n": len(rows or []),                      # 样本量（与其余子特征同口径）
        "n_volume_days": n_vol_days, "n_faded": n_faded,
        "fade_share_pct": (round(n_faded / n_vol_days * 100, 2) if n_vol_days else None),
        "disclaimer": ("**非任何论文机制的复现**：Chen et al. 2019 / Seasholes-Wu 2007 的机制"
                       "建立在账户级净买入与涨停事件语境之上，量价代理不可复现其机制——"
                       "本字段为自定义观测，须自家样本后验"),
        "caliber": _CALIBER,
    }


# ---------------------------------------------------------------------------
# R-A03 ⑤ 汇总
# ---------------------------------------------------------------------------

def volume_price_features(rows: list[dict], *, window: int = DEFAULT_WINDOW,
                          lmsm_window: int = LMSM_WINDOW, ma: int = RISK_MA) -> dict:
    """量价观察特征汇总（每字段带 ``available`` / 样本量 / 口径）。"""
    return {
        "volume_percentile": volume_percentile(rows, window=window),
        "lmsm_b2": lmsm_b2(rows, window=lmsm_window),
        "risk_state": risk_state(rows, ma=ma, window=window),
        "fade": fade_feature(rows, window=window),
        "n_rows": len(rows or []),
        "caliber": _CALIBER,
        "evidence_note": ("R-A03 为**自定义观察特征**——不声称复现任何论文机制；"
                          "须自家样本后验后才考虑规则化（hypothesis-registry C7；"
                          "验证方案见 references/backtest_prereg/C7_预注册.md）"),
    }


# ---------------------------------------------------------------------------
# R-A04 条件性反转观测窗（事后统计，非事前信号）
# ---------------------------------------------------------------------------

def panic_selloff_days(rows: list[dict], *, window: int = DEFAULT_WINDOW,
                       drop_pctile: float = 10.0,
                       vol_pctile: float = VOLUME_HIGH_PCTILE) -> list[dict]:
    """恐慌放量杀跌日 = **跌幅分位**（≤ `drop_pctile`）**× 量分位**（≥ `vol_pctile`）。

    两个条件**同时**满足才算——单看跌幅会漏掉「阴跌」，单看量会漏掉「缩量跌」。
    """
    closes = _vals(rows, "close")
    vols = _volume_series(rows)
    rets: list[float | None] = [None]
    for i in range(1, len(closes)):
        a, b = closes[i], closes[i - 1]
        rets.append((a / b - 1.0) if (a is not None and b) else None)

    out: list[dict] = []
    for i in range(20, len(closes)):
        r, v = rets[i], vols[i]
        if r is None or v is None:
            continue
        base_r = [x for x in rets[max(0, i - window):i] if x is not None]
        base_v = [x for x in vols[max(0, i - window):i] if x is not None]
        if len(base_r) < 20 or len(base_v) < 20:
            continue
        rp = percentile_rank_inclusive(base_r, r)
        vp_ = percentile_rank_inclusive(base_v, v)
        if rp <= drop_pctile and vp_ >= vol_pctile:
            out.append({"date": str(rows[i].get("trade_date") or i),
                        "ret_pct": round(r * 100, 3), "drop_pctl": round(rp, 2),
                        "vol_pctl": round(vp_, 2)})
    return out


def conditional_reversal_table(rows: list[dict], *, benchmark_returns: dict[str, float],
                               horizons: tuple[int, ...] = (5, 10, 20),
                               window: int = DEFAULT_WINDOW,
                               min_events: int = 5) -> dict:
    """杀跌日 → 未来 N 日**超额收益回填统计表**（事后窗口，**非事前信号**）。

    并列输出**趋势延续子模型**（Hurst 反证：反转与延续两股力量并存），
    并给**无条件基线对照**与样本量。
    """
    events = panic_selloff_days(rows, window=window)
    dates = [str(r.get("trade_date") or "") for r in rows or []]
    closes = _vals(rows, "close")
    if len(events) < min_events:
        return {"available": False, "reason": f"杀跌日样本 {len(events)} < {min_events}",
                "n_events": len(events), "events": events,
                "note": "事后统计、非预测；无单点事件信号"}

    idx_of = {d: i for i, d in enumerate(dates)}
    horizons_out: dict[str, dict] = {}
    for h in horizons:
        excess, base_excess, cont = [], [], 0
        for ev in events:
            i = idx_of.get(ev["date"])
            if i is None or i + h >= len(closes):
                continue
            a, b = closes[i + h], closes[i]
            if a is None or not b:
                continue
            stock_ret = a / b - 1.0
            bm = sum(benchmark_returns.get(d, 0.0) for d in dates[i + 1: i + h + 1])
            ex = stock_ret - bm
            excess.append(ex)
            if ex < 0:
                cont += 1
        # 无条件基线：全样本同长度前向超额均值
        for i in range(len(closes) - h):
            a, b = closes[i + h], closes[i]
            if a is None or not b:
                continue
            bm = sum(benchmark_returns.get(d, 0.0) for d in dates[i + 1: i + h + 1])
            base_excess.append((a / b - 1.0) - bm)
        n = len(excess)
        horizons_out[str(h)] = {
            "n": n,
            "mean_excess_pct": (round(sum(excess) / n * 100, 3) if n else None),
            "median_excess_pct": (round(sorted(excess)[n // 2] * 100, 3) if n else None),
            "win_rate_pct": (round(sum(1 for e in excess if e > 0) / n * 100, 2) if n else None),
            "baseline_excess_pct": (round(sum(base_excess) / len(base_excess) * 100, 3)
                                    if base_excess else None),
            "continuation_share_pct": (round(cont / n * 100, 2) if n else None),
            "baseline_n": len(base_excess),
        }
    return {
        "available": True,
        "n_events": len(events),
        "horizons": horizons_out,
        "events": events,
        "continuation": {
            "meaning": "「延续」= 杀跌日后 N 日超额仍为负（趋势延续而非反转）",
            "note": ("**Hurst 反证**：趋势延续与短期反转两股力量并存于文献——"
                     "故本表**并列**输出反转视角（mean/median/win）与延续占比，**不选边**"),
        },
        "note": ("⚠️ **事后统计、非预测**；条件分层（波动/超卖/量冲击）随样本量回填；"
                 "**无单点事件信号**——不得据此对单一日期做方向判断"),
        "caliber": _CALIBER,
    }
