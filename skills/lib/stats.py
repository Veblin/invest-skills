"""中性统计工具：无业务模块依赖，供 valuation / technical / collector 共用。"""

from __future__ import annotations

import statistics
from typing import Any


def median(seq: list[float]) -> float | None:
    """中位数（偶数样本取两中值平均）；空序列返回 None。"""
    if not seq:
        return None
    return statistics.median(seq)


def percentile_rank_inclusive(
    seq: list[float], current: float | None, *, round_to: int | None = None,
) -> float | None:
    """含边界（<=）百分位：count(v <= current) / len × 100。

    与 :func:`percentile_rank`（严格 <、>0 过滤）互补：此处不剔除非正值，
    仅剔除 None（调用方按需预过滤）；``round_to`` 指定四舍五入位数。
    ``seq`` 为空或 ``current`` 为 None 时返回 None。
    """
    valid = [v for v in seq if v is not None]
    if current is None or not valid:
        return None
    pct = sum(1 for v in valid if v <= current) / len(valid) * 100
    return round(pct, round_to) if round_to is not None else pct


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
    # 仅保留正数 PE（亏损期 PE 无估值意义，已剔除）
    # 若亏损占比 >30%，调用方应标注"仅作位置参考"
    valid = [v for v in seq if v is not None and v > 0]
    if not valid:
        return None
    below = sum(1 for v in valid if v < current)
    return (below / len(valid)) * 100


def percentile_rank_mid(
    seq: list[float], current: float, *, round_to: int | None = None,
) -> float | None:
    """mid-rank 百分位：count(<cur)/n + 0.5×count(==cur)/n × 100。

    介于严格百分位（`percentile_rank`）与含边界百分位
    （`percentile_rank_inclusive`）之间：平局各计半票，冻结序列
    （全值相等）稳定落在 50%——严格版会报 0%（或 100% 取决于边界），
    含边界版恒报 100%，均对"序列无变动"给出误导性极值。

    适用于负值有意义的序列（如融券增速，percentile_rank 的 >0 过滤
    会把负增速日从分母剔除，系统性抬高分位）。

    Args:
        seq: 历史序列（允许负值；None 剔除）
        current: 当前值
        round_to: 指定则四舍五入到该位小数

    Returns:
        百分位 [0, 100]，seq 为空或 current 为 None 时返回 None
    """
    valid = [v for v in seq if v is not None]
    if current is None or not valid:
        return None
    below = sum(1 for v in valid if v < current)
    ties = sum(1 for v in valid if v == current)
    pct = (below + ties / 2.0) / len(valid) * 100.0
    return round(pct, round_to) if round_to is not None else pct


def expanding_percentile_rank(
    seq: list[float], *, min_history: int = 1,
) -> list[float | None]:
    """截至当日的历史分位（expanding window）——每个元素的分位只依赖它
    自己及之前的序列，杜绝事件标签中的未来信息泄漏（look-ahead）。
    None/NaN 元素输出 None（NaN 不参与分母，与含边界分位语义一致）。

    min_history: 有效样本数（含当日，None/NaN 不计）低于该值时输出 None。
    首行 inclusive 分位恒为 100——无暖机会在序列首日产生幻影极值事件
    （F1/F2 传入 30：恢复旧代码 ≥30 日历史要求）。
    """
    out: list[float | None] = []
    seen: list[float] = []
    for v in seq:
        if v is None or (isinstance(v, float) and v != v):
            out.append(None)
        else:
            seen.append(v)
            out.append(percentile_rank_inclusive(seen, v) if len(seen) >= min_history else None)
    return out


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
