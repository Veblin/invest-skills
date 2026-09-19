"""配置方法回测框架（R-F02）——DCA / 一次投入 / 容忍带再平衡对照。纯函数，不联网。

源文档 §3 R-F02。**证据边界（强制，随输出走）**：

- C17 的学术地位为**方法层部分支持**：**DCA = 行为工程、非收益优化**；再平衡有据
  （数值待原文）；**网格策略无学术文献**；黄金对 A 股的避险角色**文献冲突**（并列不裁决）。
- ⚠️ **引用 Yu-Wang (2026) 的方向结论前须原文表格核验**（源文档裁决：数值无页码/表号可复核）。
- 本模块**只做方法层对照**，**不产出「哪种更好」的结论**——对照表的语义是「在同一价格序列下
  各方法的表现差异」，不是建议（LAW 6）。
"""
from __future__ import annotations

import math

# --- 证据注记 -------------------------------------------------------------------
GRID_NOTE = ("⚠️ **网格策略无学术文献**支持。其机械行为与「**容忍带再平衡 + 止损**」有"
             "**松散对应**（价格下穿带下沿买入、上穿上沿卖出），但二者不等价——"
             "网格还含显式的成交/滑点假设与止损模块。**不得**将网格等同于含文献支持的方法。")
GOLD_HEDGE_NOTE = ("⚠️ 黄金对 A 股的避险角色**文献冲突**：Baur-McDermott (2010) 支持"
                   "（全球样本），另有 2025 中国样本研究称避险（**待核验**）——"
                   "两说**并列呈现，不裁决**（事实边界 §2.3 禁止替来源选边）。")
DCA_NOTE = ("DCA（定期定额）的定位是**行为工程，而非收益优化**——它降低的是"
            "「一次性买在高点」的时点风险与决策负担，**不提高期望收益**；"
            "在长期上行序列中其收益**低于**一次投入（现金拖累），这是同一枚硬币的两面。")


def _clean(prices: list[float]) -> list[float]:
    return [float(p) for p in prices if p is not None and float(p) > 0]


def _stats(final_value: float, contributed: float, n_periods: int) -> dict:
    if contributed <= 0:
        return {"final_value": None, "contributed": contributed,
                "return_pct": None, "n_periods": n_periods}
    return {"final_value": round(final_value, 4), "contributed": round(contributed, 4),
            "return_pct": round((final_value / contributed - 1.0) * 100, 3),
            "n_periods": n_periods}


def dca(prices: list[float], *, amount_per_period: float = 1000.0,
        every: int = 20) -> dict:
    """定期定额：每 `every` 个价格点投入固定金额。"""
    p = _clean(prices)
    if len(p) < every + 1:
        return {"available": False, "reason": f"序列不足（{len(p)} < {every + 1}）",
                "note": DCA_NOTE}
    units = 0.0
    contributed = 0.0
    n = 0
    for i in range(0, len(p), every):
        units += amount_per_period / p[i]
        contributed += amount_per_period
        n += 1
    return {"available": True, **_stats(units * p[-1], contributed, n), "note": DCA_NOTE}


def lump_sum(prices: list[float], *, amount: float = 10000.0) -> dict:
    """一次投入（首日全额）。"""
    p = _clean(prices)
    if len(p) < 2:
        return {"available": False, "reason": "序列不足", "note": DCA_NOTE}
    units = amount / p[0]
    return {"available": True, **_stats(units * p[-1], amount, 1), "note": DCA_NOTE}


def tolerance_rebalance(prices: list[float], *, weights: tuple[float, float] = (0.5, 0.5),
                        band: float = 0.05, amount: float = 10000.0) -> dict:
    """容忍带再平衡：两资产（现金 0 收益 vs 价格序列）按目标权重 ± band 触发。

    ⚠️ 简化模型：现金端按 **0 利率**处理（真实场景须给现金收益率）；
    该简化**会低估**再平衡的长期表现，结论须带此限定。
    """
    p = _clean(prices)
    if len(p) < 2:
        return {"available": False, "reason": "序列不足", "note": DCA_NOTE}
    w_risk, w_cash = weights
    if abs(w_risk + w_cash - 1.0) > 1e-9:
        raise ValueError(f"权重之和须为 1，实得 {weights}")
    units = amount * w_risk / p[0]
    cash = amount * w_cash
    n_reb = 0
    for px in p[1:]:
        total = units * px + cash
        if total <= 0:
            continue
        w_now = units * px / total
        if abs(w_now - w_risk) > band:
            target_risk = total * w_risk
            units = target_risk / px
            cash = total - target_risk
            n_reb += 1
    final = units * p[-1] + cash
    # v0.3.0 B4（顺带）：n_periods 原报 `len(p)`，但循环只走 `p[1:]`（len-1 次）
    # ——三腿的 n_periods 口径须一致为「实际发生的期数」，否则对照表读数互相矛盾。
    return {"available": True, **_stats(final, amount, len(p) - 1), "n_rebalances": n_reb,
            "band": band, "cash_rate": 0.0,
            "note": ("容忍带再平衡（现金端按 **0 利率**简化 → 低估其长期表现，"
                     "结论须带此限定）")}


def config_backtest(prices: list[float], *, amount: float = 10000.0,
                    every: int = 20, band: float = 0.05) -> dict:
    """三方法对照表（**同一价格序列**下的表现差异，三腿本金相等）。"""
    n = len(_clean(prices))
    # v0.3.0 B4：DCA 的投入次数以 `dca` 自身循环边界为唯一权威 =
    # len(range(0, n, every)) = ceil(n / every)。此前用 `n // every`（floor）做
    # 分母 → 每期金额偏大、DCA 腿本金高于另两腿（len=252/every=20 → 13 vs 12 期
    # = +8.33%；贴近边界时更夸张，len=21 时 2/1 = +100%），而本表宣称「同一价格
    # 序列下的方法层对照」→ 实为不等本金对比。
    n_periods = (n + every - 1) // every
    return {
        "n_prices": n,
        "methods": {
            "dca": dca(prices, amount_per_period=amount / max(1, n_periods), every=every),
            "lump_sum": lump_sum(prices, amount=amount),
            "tolerance_rebalance": tolerance_rebalance(prices, band=band, amount=amount),
        },
        "caveat": ("**方法层对照，非建议**（LAW 6）：三者语义不同——DCA 是行为工程、"
                   "一次投入是基准、容忍带再平衡是风险控制；**不产出「哪种更好」的结论**"),
        "evidence_notes": {"dca": DCA_NOTE, "grid": GRID_NOTE, "gold_hedge": GOLD_HEDGE_NOTE},
        "grid_equivalence": GRID_NOTE,
        "gold_hedge": GOLD_HEDGE_NOTE,
    }
