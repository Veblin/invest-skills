"""情景预案模板（R11c；A2 修订 2026-10-10）：回撤档位 → 核验深度 + 当前状态。

预案 = 研究核查流程规则，非买卖指令。回撤档位只定义「触发核验深度」
（例行记录 → 归因核查 → 三步全流程 → 框架重估），不预设任何买入/卖出
方向；输出禁用「无动作/如何应对」等动作化表述。操作决策由用户根据自身
持有周期与仓位自行做出（见 LAW6A_DISCLAIMER）。

A2 修订（用户裁决 2026-10-10）——**取消累计回撤的 σ 统计解释**：

- 删除 ``sigma_multiple`` 与「60 日日均波动」挂钩：绝对回撤档位不对应任何
  统计概率/分布假设，不得以 σ、正态尾概率或 ``σ×√n`` 等口径重述
  （后两者同样不成立，不引入新回撤分布模型）；
- 档位保留为**普通研究核查分档**，并输出**当前状态**：当前回撤（自窗口
  峰值的回撤）与各档「已达/未达」——避免「当前回撤已越过所有档仍按四个
  未来触发」呈现。
"""

from __future__ import annotations

from typing import Any

from _invest_path import ensure_invest_a_scripts_on_path  # noqa: E402

ensure_invest_a_scripts_on_path()

from lib.nums import safe_float  # noqa: E402

LAW6A_DISCLAIMER = (
    "预案为研究流程规则而非买卖指令：回撤档位只定义「触发核验深度」，"
    "不预设任何买入/卖出/持有动作。操作决策由用户根据自身持有周期与仓位自行做出。"
)

# 回撤档位 → 触发核验深度（档位为绝对百分比分档；**不换算 σ/概率**——A2）
_DRAWDOWN_LEVELS: list[dict[str, Any]] = [
    {"level_pct": -3.0, "verification_depth": "例行记录"},
    {"level_pct": -5.0, "verification_depth": "归因核查"},
    {"level_pct": -8.0, "verification_depth": "三步核查全流程"},
    {"level_pct": -12.0, "verification_depth": "框架重估"},
]

#: 档位口径说明（随 payload 冻结，报告引用不另起措辞）
LEVELS_BASIS = ("档位=绝对百分比回撤分档（研究核查流程：触发核验深度），"
                "非统计概率/非操作阈值；不设 σ/尾部概率口径")


def current_drawdown(rows: list[dict]) -> dict | None:
    """当前回撤（自窗口内峰值的回撤）与峰值信息（引擎计算，AI 不得心算）。

    ``rows`` 为历史行情行（``date`` + ``nav`` 或 ``close``）。定义：
    当前回撤% = (最新值 / 窗口内峰值 − 1) × 100；峰值取窗口内**最大收盘**，
    可出现在最新值之前或即为最新值（此时回撤 = 0%）。无有效行 → None。

    Returns
    -------
    dict | None
        ``{current_drawdown_pct, peak_date, peak_close, latest_date,
        latest_close, rows_observed}``；不推断缺失日期（date 缺失记 None）。
    """
    values: list[tuple[str | None, float]] = []
    for r in rows or []:
        v = safe_float(r.get("nav", r.get("close")))
        if v is None or v <= 0:
            continue
        d = r.get("date")
        values.append((str(d) if d is not None else None, v))
    if not values:
        return None
    peak_i = max(range(len(values)), key=lambda i: values[i][1])
    peak_date, peak_close = values[peak_i]
    latest_date, latest_close = values[-1]
    return {
        "current_drawdown_pct": round((latest_close / peak_close - 1) * 100, 2),
        "peak_date": peak_date,
        "peak_close": peak_close,
        "latest_date": latest_date,
        "latest_close": latest_close,
        "rows_observed": len(values),
    }


def drawdown_levels(current_dd_pct: float | None, *,
                    rows_observed: int = 0) -> list[dict]:
    """回撤档位表（引擎计算）：档位 + 触发核验深度 + **当前状态**。

    ``state``：``"已达"``（当前回撤 ≤ 档位，即已越过该档）/ ``"未达"``；
    ``current_dd_pct`` 为 None（当前回撤不可算）时 state=None——不猜。
    输出不含任何动作化表述，也不含 σ/概率字段（A2）。
    """
    out: list[dict[str, Any]] = []
    for lv in _DRAWDOWN_LEVELS:
        state = None
        if current_dd_pct is not None:
            state = "已达" if current_dd_pct <= lv["level_pct"] else "未达"
        out.append({
            "level_pct": lv["level_pct"],
            "verification_depth": lv["verification_depth"],
            "state": state,
            "rows_observed": rows_observed,
        })
    return out


def current_verification(levels: list[dict]) -> dict | None:
    """按当前状态归结「当前应执行的核验深度」（最深已越过档）。

    避免结构性问题：当前回撤已越过全部档时，报告仍把它写成四个未来触发。
    未越过任何档 → ``deepest_reached_level_pct=None``（当前无档位触发）。
    各档 state 为 None（当前回撤不可算）→ 返回 None，不猜。
    """
    if not levels or any(lv.get("state") is None for lv in levels):
        return None
    reached = [lv for lv in levels if lv["state"] == "已达"]
    deepest = reached[-1] if reached else None
    return {
        "reached_levels": len(reached),
        "deepest_reached_level_pct": deepest["level_pct"] if deepest else None,
        "deepest_reached_depth": deepest["verification_depth"] if deepest else None,
        "note": ("已越过档位按当前状态记录核验深度；未越过档位为未达——"
                 "档位不是未来预测/概率"),
    }


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
