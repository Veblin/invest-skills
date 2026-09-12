"""中过滤（质量闸门）（DS-1 / T12-2）——纯函数，不联网。

设计 §2.2 第 3/4 条 + §5 降级三档。**口径以 T0 勘察实测为准**
（`host-docs/v0.3.0/round-plans/r4-20260912.md` §1.2），三处与早期设计表述不同：

1. 设计写 `ROE_TTM`，但 `fina_indicator`（108 列）**无 `roe_ttm` 字段** →
   用 **`roe_yearly`（年化 ROE）**：跨期同口径可比
   （实测 600519.SH 20260630：`roe`=17.95 半年 vs `roe_yearly`=35.91 年化；年报两者相等）。
2. 设计写「归母净利 ≥ 0」，但**无原始 `netprofit` 列** → 用 **`profit_dedt`（扣非归母净利）**
   ——**口径加严**（剔除非经常性损益），须在 `rules.md` 与报告头显式标注。
3. `fina_indicator` 对同一 `end_date` 会返回**重复行** → 取「最近一期」前必须去重
   （同期取 `ann_date` 最大者），否则期次数值不确定。

**三档（T0 门定档）**：`fina`（首选，实测可用）/ `forecast`（降级：预告口径 +
ROE 下限降级为「股息率>0 或预告净利>0」）/ `skipped`（该过滤项跳过并明示）。

`pass=None` = **不可评估**（数据不可得）——绝不等于 `True`（「不冒充完整过滤」）。
"""
from __future__ import annotations

QUALITY_TIER = "fina"
ROE_MIN_PCT = 8.0
ROE_FIELD = "roe_yearly"          # 见模块 docstring 第 1 条
PROFIT_FIELD = "profit_dedt"      # 见模块 docstring 第 2 条
_TIERS = ("fina", "forecast", "skipped")


def _num(v) -> float | None:
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else f


def latest_report(fina_rows: list[dict] | None) -> dict | None:
    """最近一期财报行（去重后）；无数据 → None。

    期次优先（`end_date` 最大），同期多条取 `ann_date` 最新者。
    """
    rows = [r for r in (fina_rows or []) if r.get("end_date")]
    if not rows:
        return None
    best: dict | None = None
    for r in rows:
        if best is None:
            best = r
            continue
        cur = (str(r.get("end_date")), str(r.get("ann_date") or ""))
        top = (str(best.get("end_date")), str(best.get("ann_date") or ""))
        if cur > top:
            best = r
    return best


def _result(passed, tier, *, roe=None, profit=None, reason="", warning=None) -> dict:
    return {"pass": passed, "tier": tier, "roe_yearly": roe, "profit_dedt": profit,
            "reason": reason, "warning": warning}


def pass_quality(row: dict, fina_rows: list[dict] | None,
                 forecast_rows: list[dict] | None, *, tier: str = QUALITY_TIER) -> dict:
    """质量闸门判定。

    返回 ``{pass: True|False|None, tier, roe_yearly, profit_dedt, reason, warning}``；
    ``pass=None`` = 不可评估（跳过 + warning，**不得当通过**）。
    """
    if tier not in _TIERS:
        raise ValueError(f"未知质量档位 {tier!r}（须为 {_TIERS} 之一）——静默按通过处理会冒充完整过滤")

    latest = latest_report(fina_rows)
    fc = (forecast_rows or [None])[0] if forecast_rows else None

    if tier == "skipped":
        return _result(None, tier, reason="数据源不可得",
                       warning="质量过滤已跳过（fina_indicator 与业绩预告均不可得）——"
                               "本清单**未经质量过滤**，不得读作已筛出优质标的")

    if tier == "fina":
        if latest is None:
            return _result(None, tier, reason="无定期报告数据",
                           warning="质量过滤不可评估（fina_indicator 空返回）——该过滤项跳过")
        roe = _num(latest.get(ROE_FIELD))
        profit = _num(latest.get(PROFIT_FIELD))
        if roe is None or profit is None:
            return _result(None, tier, roe=roe, profit=profit, reason="关键字段缺失",
                           warning=f"质量过滤不可评估（{ROE_FIELD}/{PROFIT_FIELD} 缺失）——该过滤项跳过")
        if roe < ROE_MIN_PCT:
            return _result(False, tier, roe=roe, profit=profit,
                           reason=f"ROE（年化）{roe:.2f}% < {ROE_MIN_PCT}%")
        if profit < 0:
            return _result(False, tier, roe=roe, profit=profit,
                           reason=f"扣非归母净利 {profit:.4g} < 0（报告期 {latest.get('end_date')}）")
        return _result(True, tier, roe=roe, profit=profit,
                       reason=f"ROE（年化）{roe:.2f}% ≥ {ROE_MIN_PCT}% 且扣非净利 ≥ 0"
                              f"（{latest.get('end_date')}）")

    # tier == "forecast"：预告口径 + ROE 下限降级为「股息率 > 0 或预告净利 > 0」
    warn = "⚠️ 质量过滤降级：fina_indicator 不可用，改用业绩预告口径（ROE 下限降级为「股息率>0 或预告净利>0」）"
    if fc is None:
        dv = _num(row.get("dv_ratio"))
        if dv is not None and dv > 0:
            return _result(True, tier, reason="降级档：股息率 > 0", warning=warn)
        return _result(None, tier, reason="预告不可得且股息率不满足",
                       warning=warn + "；该过滤项跳过")
    hi = _num(fc.get("net_profit_max"))
    lo = _num(fc.get("net_profit_min"))
    top = hi if hi is not None else lo
    if top is None:
        return _result(None, tier, reason="预告净利字段缺失", warning=warn)
    if top < 0:
        return _result(False, tier, profit=top, reason=f"预告净利上限 {top:.4g} < 0", warning=warn)
    return _result(True, tier, profit=top, reason=f"预告净利上限 {top:.4g} ≥ 0", warning=warn)
