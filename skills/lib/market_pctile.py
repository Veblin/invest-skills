"""全市场 20 日均值横截面分位（v0.2.6 全市场分位数据层读路径）。

纯函数，不联网：输入 market_daily 明细行（store.load_market_daily 口径），
输出横截面映射与单标的分位。分位语义 = 20 日平均成交额/换手率的
全市场横向排名（percentile_rank_inclusive，值越大越活跃）。

数据不足/标的不在池内 → available=False + reason（D5 降级，None 合法）。
"""

from __future__ import annotations

from typing import Any

from stats import percentile_rank_inclusive  # noqa: E402 — 共享统计库

_PCTILE_DAYS = 20


def build_cross_section(rows: list[dict], days: int = _PCTILE_DAYS) -> dict[str, dict]:
    """market_daily 明细行 → {ts_code: {avg_amount, avg_turnover, n_days}}。

    对每个 ts_code 取其最近 N 个交易日的 amount/turnover_rate 均值
    （行已按 date ASC；缺失值跳过）。至少 1 个有效日才进入横截面。
    """
    by_code: dict[str, dict[str, list[float]]] = {}
    for r in rows:
        code = r.get("ts_code")
        amount = r.get("amount")
        turnover = r.get("turnover_rate")
        if not code or amount is None and turnover is None:
            continue
        slot = by_code.setdefault(code, {"amount": [], "turnover": []})
        if amount is not None:
            slot["amount"].append(float(amount))
        if turnover is not None:
            slot["turnover"].append(float(turnover))

    cross: dict[str, dict] = {}
    for code, slot in by_code.items():
        amt = slot["amount"][-days:]
        trn = slot["turnover"][-days:]
        if not amt and not trn:
            continue
        cross[code] = {
            "avg_amount": sum(amt) / len(amt) if amt else None,
            "avg_turnover": sum(trn) / len(trn) if trn else None,
            "n_days": max(len(amt), len(trn)),
        }
    return cross


def pctile_20d(cross: dict[str, dict], symbol: str) -> dict:
    """单标的分位 → {amount_pctile, turnover_pctile, n_stocks, available, reason}。

    symbol 归一化：接受 '600176' 或 '600176.SH'（tushare ts_code 后缀匹配）。
    """
    code = symbol.upper()
    entry = cross.get(code)
    if entry is None:
        # 6 位代码 → 找带后缀的 ts_code（600176 → 600176.SH）
        candidates = [k for k in cross if k.startswith(f"{code}.")]
        entry = cross.get(candidates[0]) if len(candidates) == 1 else None
    if entry is None:
        return {
            "amount_pctile": None,
            "turnover_pctile": None,
            "n_stocks": len(cross),
            "available": False,
            "reason": f"{symbol} 不在全市场横截面内（或数据不足）",
        }

    def _rank(values: list[float], v: float | None) -> float | None:
        if v is None:
            return None
        return round(percentile_rank_inclusive(values, v), 2)

    amounts = [e["avg_amount"] for e in cross.values() if e["avg_amount"] is not None]
    turnovers = [e["avg_turnover"] for e in cross.values() if e["avg_turnover"] is not None]
    result = {
        "amount_pctile": _rank(amounts, entry["avg_amount"]),
        "turnover_pctile": _rank(turnovers, entry["avg_turnover"]),
        "n_stocks": len(cross),
        "available": True,
    }
    if result["amount_pctile"] is None and result["turnover_pctile"] is None:
        result["available"] = False
        result["reason"] = f"{symbol} 无有效 amount/turnover 数据"
    return result


def inject_distances_pctiles(tech: dict[str, Any], symbol: str, cross: dict[str, dict] | None) -> None:
    """把分位就地注入 technical.compute() 输出的 distances（消费方调用，compute 保持纯函数）。

    cross 为 None/空 → 置 None + pctile_note 说明（D4：字段名 + 覆盖范围标注）。
    """
    distances = tech.setdefault("distances", {})
    if not cross:
        distances["amount_pctile_20d"] = None
        distances["turnover_pctile_20d"] = None
        distances["pctile_note"] = "全市场分位不可得（market_daily 无数据）"
        return
    p = pctile_20d(cross, symbol)
    distances["amount_pctile_20d"] = p["amount_pctile"]
    distances["turnover_pctile_20d"] = p["turnover_pctile"]
    if p["available"]:
        distances["pctile_note"] = f"全市场 {p['n_stocks']} 只横截面（20 日均值）"
    else:
        distances["pctile_note"] = p.get("reason") or "全市场分位不可得"
