"""回测纯计算模块 — 日历/事件窗口统计检验。

输入: (date, return) 序列（标准字段 date/ret，date 为 datetime.date）
输出: 窗口内 vs 窗口外对照的统计量（Welch t / permutation p / 描述统计 / 滚动年窗）

原则:
  - 纯函数，无副作用，不依赖外部 API（random.Random 显式 seed 保证可复现）
  - 样本 < 2 时抛 ValueError（D5 fail loud——回测脚本调用方负责数据完整性）
  - 统计口径: ABCD 设计 §3.2 统一显著性分级（✅ t≥3.0 / ⚠️ 2.0≤t<3.0 / ❌ t<2.0）

参考:
  MacKinlay (1997, JEL) 事件研究；Harvey, Liu & Zhu (2016, RFS) t≥3.0；
  Newey & West (1987) 重叠样本（滚动窗实现）；White (2000) Reality Check 思想（permutation）
"""

from __future__ import annotations

import math
import random
from datetime import date

WINDOW_START = (8, 15)  # H5 主窗口：8/15-8/31
WINDOW_END = (8, 31)


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs)


def _var(xs: list[float]) -> float:
    """样本方差（ddof=1）。"""
    n = len(xs)
    if n < 2:
        raise ValueError(f"样本量 {n} < 2，无法计算样本方差")
    m = _mean(xs)
    return sum((x - m) ** 2 for x in xs) / (n - 1)


def daily_returns(rows: list[dict]) -> list[tuple[date, float]]:
    """从按日期升序的 rows（含 date/close）计算日收益率序列（首行无收益，跳过）。"""
    if len(rows) < 2:
        return []
    out: list[tuple[date, float]] = []
    prev_close = None
    for row in rows:
        d = row["date"]
        close = row["close"]
        if not isinstance(d, date):
            raise ValueError(f"date 字段须为 datetime.date，实际 {type(d)}")
        if prev_close is not None and prev_close != 0:
            out.append((d, (close - prev_close) / prev_close * 100.0))
        prev_close = close
    return out


def in_window(d: date, start: tuple[int, int] = WINDOW_START, end: tuple[int, int] = WINDOW_END) -> bool:
    """是否落在 (start_month, start_day) ~ (end_month, end_day) 区间内（含两端）。"""
    sm, sd = start
    em, ed = end
    return (d.month, d.day) >= (sm, sd) and (d.month, d.day) <= (em, ed)


def split_window(
    rets: list[tuple[date, float]],
    start: tuple[int, int] = WINDOW_START,
    end: tuple[int, int] = WINDOW_END,
) -> tuple[list[float], list[float]]:
    """按日历窗口切分收益率序列 → (窗口内, 窗口外)。"""
    inside: list[float] = []
    outside: list[float] = []
    for d, r in rets:
        if in_window(d, start, end):
            inside.append(r)
        else:
            outside.append(r)
    return inside, outside


def welch_t(a: list[float], b: list[float]) -> tuple[float, float]:
    """Welch's t 检验（不等方差）→ (t, dof)。样本 < 2 抛 ValueError。"""
    na, nb = len(a), len(b)
    if na < 2 or nb < 2:
        raise ValueError(f"Welch t 需要两组样本量 ≥2，实际 {na}/{nb}")
    ma, mb = _mean(a), _mean(b)
    va, vb = _var(a), _var(b)
    se2 = va / na + vb / nb
    if se2 == 0:
        if ma == mb:
            return 0.0, na + nb - 2
        raise ValueError("两组均为常数且均值不同，Welch t 未定义")
    t = (ma - mb) / math.sqrt(se2)
    dof = se2**2 / ((va / na) ** 2 / (na - 1) + (vb / nb) ** 2 / (nb - 1))
    return t, dof


def welch_t_abs(a: list[float], b: list[float]) -> float:
    """permutation 用的单值统计量：|Welch t|。"""
    return abs(welch_t(a, b)[0])


def permutation_test(
    a: list[float],
    b: list[float],
    statistic=welch_t_abs,
    n_perm: int = 10000,
    seed: int = 42,
) -> dict:
    """标签洗牌检验 → {p_value, observed, n_perm}。

    H0: 两组来自同一分布。p = 洗牌后统计量 ≥ 观测值的比例。
    """
    na = len(a)
    if na < 1 or len(b) < 1:
        raise ValueError(f"permutation 需要两组样本量 ≥1，实际 {na}/{len(b)}")
    obs = statistic(a, b)
    combined = list(a) + list(b)
    rng = random.Random(seed)
    count = 0
    for _ in range(n_perm):
        rng.shuffle(combined)
        if statistic(combined[:na], combined[na:]) >= obs:
            count += 1
    return {"p_value": count / n_perm, "observed": obs, "n_perm": n_perm}


def describe(rets: list[float]) -> dict:
    """描述统计：n / 日均% / 日波动%(样本标准差) / 下跌概率 / 上涨概率 / 中位数。"""
    if not rets:
        raise ValueError("空序列无法描述统计")
    n = len(rets)
    return {
        "n": n,
        "mean_daily_pct": _mean(rets),
        "std_daily_pct": math.sqrt(_var(rets)) if n >= 2 else 0.0,
        "down_prob": sum(1 for r in rets if r < 0) / n,
        "up_prob": sum(1 for r in rets if r > 0) / n,
        "median_daily_pct": sorted(rets)[n // 2] if n % 2 else (sorted(rets)[n // 2 - 1] + sorted(rets)[n // 2]) / 2,
    }


def cohen_d(a: list[float], b: list[float]) -> float:
    """效应量 Cohen's d（合并标准差）。"""
    na, nb = len(a), len(b)
    if na < 1 or nb < 1:
        raise ValueError(f"Cohen d 需要两组样本量 ≥1，实际 {na}/{nb}")
    pooled = math.sqrt(((na - 1) * _var(a) + (nb - 1) * _var(b)) / (na + nb - 2)) if na + nb > 2 else 0.0
    if pooled == 0:
        return 0.0
    return (_mean(a) - _mean(b)) / pooled


def yearly_effects(
    rets: list[tuple[date, float]],
    start: tuple[int, int] = WINDOW_START,
    end: tuple[int, int] = WINDOW_END,
) -> list[dict]:
    """逐年效应 → [{year, n_in, mean_in_pct, n_out, mean_out_pct, diff_pct}]（按年份升序）。

    重叠样本注意：同日多事件/序列自相关不在本函数处理，逐年报告仅作 AMH 滚动窗输入。
    """
    by_year: dict[int, tuple[list[float], list[float]]] = {}
    for d, r in rets:
        inside, outside = by_year.setdefault(d.year, ([], []))
        if in_window(d, start, end):
            inside.append(r)
        else:
            outside.append(r)
    out = []
    for year in sorted(by_year):
        inside, outside = by_year[year]
        if not inside or not outside:
            continue
        out.append(
            {
                "year": year,
                "n_in": len(inside),
                "mean_in_pct": _mean(inside),
                "n_out": len(outside),
                "mean_out_pct": _mean(outside),
                "diff_pct": _mean(inside) - _mean(outside),
            }
        )
    return out


def rolling_span_effects(
    rets: list[tuple[date, float]],
    span_years: int = 5,
    start: tuple[int, int] = WINDOW_START,
    end: tuple[int, int] = WINDOW_END,
) -> list[dict]:
    """滚动 N 年窗效应（AMH：效应时变性检查）→ [{span, n_in, mean_in_pct, n_out, mean_out_pct, diff_pct}]。

    按交易日而非日历年滚动：将全序列按年份切块后逐 5 年滑窗聚合。
    """
    yearly = yearly_effects(rets, start, end)
    if len(yearly) < span_years:
        return []
    out = []
    for i in range(len(yearly) - span_years + 1):
        span = yearly[i : i + span_years]
        n_in = sum(s["n_in"] for s in span)
        n_out = sum(s["n_out"] for s in span)
        mean_in = sum(s["mean_in_pct"] * s["n_in"] for s in span) / n_in if n_in else 0.0
        mean_out = sum(s["mean_out_pct"] * s["n_out"] for s in span) / n_out if n_out else 0.0
        out.append(
            {
                "span": f"{span[0]['year']}-{span[-1]['year']}",
                "n_in": n_in,
                "mean_in_pct": mean_in,
                "n_out": n_out,
                "mean_out_pct": mean_out,
                "diff_pct": mean_in - mean_out,
            }
        )
    return out


def significance_grade(t: float) -> str:
    """统一显著性分级（ABCD §3.2）：✅ t≥3.0 / ⚠️ 2.0≤t<3.0 / ❌ t<2.0。"""
    if t >= 3.0:
        return "✅"
    if t >= 2.0:
        return "⚠️"
    return "❌"
