"""情景预案模板（R11c，v0.2.4）：回撤档位 σ 分级 + 三步核查清单（LAW 6a）。

预案 = 研究流程规则，非买卖指令。回撤档位只定义「触发核验深度」
（例行记录 → 归因核查 → 三步全流程 → 框架重估），不预设任何买入/卖出
方向；输出禁用「无动作/如何应对」等动作化表述。操作决策由用户根据自身
持有周期与仓位自行做出（见 LAW6A_DISCLAIMER）。
"""

from __future__ import annotations

import math
from typing import Any

from lib.nums import safe_float

LAW6A_DISCLAIMER = (
    "预案为研究流程规则而非买卖指令：回撤档位只定义「触发核验深度」，"
    "不预设任何买入/卖出/持有动作。操作决策由用户根据自身持有周期与仓位自行做出。"
)

# 回撤档位 → 触发核验深度（档位固定，σ 倍数 = 档位% ÷ 60 日日均波动，引擎计算）
_DRAWDOWN_LEVELS: list[dict[str, Any]] = [
    {"level_pct": -3.0, "verification_depth": "例行记录"},
    {"level_pct": -5.0, "verification_depth": "归因核查"},
    {"level_pct": -8.0, "verification_depth": "三步核查全流程"},
    {"level_pct": -12.0, "verification_depth": "框架重估"},
]


def daily_vol_pct(closes: list[float], window: int = 60) -> float | None:
    """最近 window 个日收益率的日波动（%）= 标准差 × 100（引擎计算）。

    588000 实例：60 日日均波动 4.01% → -8% 档位 ≈ 2.0σ。
    行数不足 window+1 返回 None。
    """
    closes = [safe_float(c) for c in closes if safe_float(c) is not None]
    if len(closes) < window + 1:
        return None
    returns = []
    for i in range(1, len(closes)):
        if closes[i - 1] > 0:
            returns.append(closes[i] / closes[i - 1] - 1.0)
    if len(returns) < window:
        return None
    window_returns = returns[-window:]
    mean = sum(window_returns) / len(window_returns)
    var = sum((r - mean) ** 2 for r in window_returns) / len(window_returns)
    return math.sqrt(var) * 100


def drawdown_levels(closes: list[float], vol_60d_daily: float | None) -> list[dict]:
    """回撤档位表（引擎计算，AI 不得心算）。

    Parameters
    ----------
    closes : list[float]
        收盘价序列（仅用于行数上下文标注，σ 计算只用 vol_60d_daily）。
    vol_60d_daily : float | None
        60 日日均波动（%），来自 ``daily_vol_pct`` 或引擎 derived
        ``daily_volatility_pct``（口径一致：60 日年化波动 ÷ √252）。

    Returns
    -------
    list[dict]
        [{level_pct, sigma_multiple, verification_depth}]。
        sigma_multiple = |level_pct| ÷ vol_60d_daily（保留 2 位小数）；
        vol_60d_daily 不可用时为 None。输出不含任何动作化表述。
    """
    out: list[dict[str, Any]] = []
    for lv in _DRAWDOWN_LEVELS:
        sigma = None
        if vol_60d_daily is not None and vol_60d_daily > 0:
            sigma = round(abs(lv["level_pct"]) / vol_60d_daily, 2)
        out.append({
            "level_pct": lv["level_pct"],
            "sigma_multiple": sigma,
            "verification_depth": lv["verification_depth"],
            "rows_observed": len(closes),
        })
    return out


def three_step_checklist() -> list[str]:
    """固定三步核查模板（R11c）——按序执行，输出框架状态判定，非动作指令。"""
    return [
        "STEP 1 归因核查：当日/近 3 日是否存在可归因事件？按 行业/结构/个股 三分类记录；"
        "无事件则如实记录「原因不明回撤」，禁止事后强行归因",
        "STEP 2 框架对照：对照（a）盈利证据链（财报/预告/指引）、"
        "（b）叙事前提（如 AI 资本开支指引）、（c）资金先行指标（份额流/成交额）逐一核验",
        "STEP 3 框架重估：输出三态判定——「未失效」（证据链完好）/"
        "「需验证」（部分证据缺失或冲突）/「已证伪」（关键假设被否定）",
    ]
