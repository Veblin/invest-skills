"""中性统计工具：无业务模块依赖，供 valuation / technical / collector 共用。"""

from __future__ import annotations

import statistics
from typing import Any


def percentile_rank(seq: list[float], current: float) -> float | None:
    """计算 current 在 seq 中的百分位（严格低于 current 的比例 × 100）。

    percentile = count_(v < current) / total × 100
    即：值越低，百分位越小 → "低于历史 X% 的时间"

    使用严格小于（不包含等于），避免当前值等于历史极值时
    分位被推向极端（最小值→0%，最大值→100%），使 zone 判断更稳健。

    Args:
        seq: 历史估值序列（正数）
        current: 当前值

    Returns:
        百分位 [0, 100]，数据不足时返回 None
    """
    valid = [v for v in seq if v is not None and v > 0]
    if not valid:
        return None
    below = sum(1 for v in valid if v < current)
    return (below / len(valid)) * 100


def calc_beta(
    stock_returns: list[float],
    market_returns: list[float],
) -> dict[str, Any]:
    """从收益率序列计算 Beta。

    Args:
        stock_returns: 个股月度/周度收益率
        market_returns: 基准（沪深300）同期收益率

    Returns:
        {"beta": float, "r_squared": float, "observations": int}
        或 {"beta": None, "error": str}
    """
    n = min(len(stock_returns), len(market_returns))
    if n < 12:
        return {"beta": None, "error": f"数据点不足: {n} < 12"}

    mean_s = statistics.mean(stock_returns[:n])
    mean_m = statistics.mean(market_returns[:n])

    cov = sum(
        (s - mean_s) * (m - mean_m)
        for s, m in zip(stock_returns[:n], market_returns[:n])
    ) / (n - 1)
    var_m = sum((m - mean_m) ** 2 for m in market_returns[:n]) / (n - 1)

    if abs(var_m) < 1e-12:
        return {"beta": None, "error": "市场方差为零"}

    beta = cov / var_m

    ss_res = sum(
        (s - (mean_s + beta * (m - mean_m))) ** 2
        for s, m in zip(stock_returns[:n], market_returns[:n])
    )
    ss_tot = sum((s - mean_s) ** 2 for s in stock_returns[:n])
    r_squared = 1 - ss_res / ss_tot if ss_tot != 0 else 0

    return {
        "beta": round(beta, 4),
        "r_squared": round(r_squared, 4),
        "observations": n,
    }
