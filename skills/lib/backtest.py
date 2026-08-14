"""回测纯计算模块 — 日历/事件窗口统计检验。

输入: (date, return) 序列（标准字段 date/ret，date 为 datetime.date）
输出: 窗口内 vs 窗口外对照的统计量（Welch t / permutation p / 描述统计 / 滚动年窗）

原则:
  - 纯函数，无副作用，不依赖外部 API（random.Random 显式 seed 保证可复现）
  - 统计量函数（welch_t/describe/cohen_d 等）样本不足时抛 ValueError（D5 fail loud——回测脚本
    调用方负责数据完整性）；daily_returns 对 <2 行返回空列表（无收益可算，非错误）
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

    按年份切块后逐 N 年滑窗聚合。注意：逐年效应会剔除窗口内外样本不全的年份，
    因此 span 标签可能覆盖少于标签所示数量的完整年份（如中间年份被剔除）。
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


# ---- M3: 回归 / 事件 / RS（v0.2.6 P1） ----


def _solve_normal(xtx: list[list[float]], xty: list[float]) -> list[float]:
    """正规方程求解 X'X b = X'y（Gauss 消元 + 回代，K ≤ 4 够用）。"""
    n = len(xtx)
    a = [row[:] + [xty[i]] for i, row in enumerate(xtx)]  # 增广矩阵
    for col in range(n):
        # 部分主元
        pivot = max(range(col, n), key=lambda r: abs(a[r][col]))
        a[col], a[pivot] = a[pivot], a[col]
        if abs(a[col][col]) < 1e-14:
            raise ValueError("正规方程奇异（设计矩阵列线性相关）")
        for r in range(col + 1, n):
            f = a[r][col] / a[col][col]
            for c in range(col, n + 1):
                a[r][c] -= f * a[col][c]
    b = [0.0] * n
    for r in range(n - 1, -1, -1):
        b[r] = (a[r][n] - sum(a[r][c] * b[c] for c in range(r + 1, n))) / a[r][r]
    return b


def ols_multi(y: list[float], xs: list[list[float]], names: list[str] | None = None) -> dict:
    """多元 OLS（含截距，normal equations + 高斯消元，stdlib-only）。

    y: 因变量；xs: 自变量列（各列与 y 等长）；names: 因子名（可选）。
    返回 {intercept, coefs, se, t_stats, r_squared, n, k, residual_sigma}。
    样本 < 3 或列长不一致抛 ValueError。
    """
    n = len(y)
    if n < 3:
        raise ValueError(f"OLS 需要 n≥3，实际 {n}")
    k = len(xs) + 1  # 含截距
    if any(len(col) != n for col in xs):
        raise ValueError("y 与 xs 各列长度必须一致")

    design = [[1.0] * n] + [list(col) for col in xs]
    xtx = [[sum(design[i][t] * design[j][t] for t in range(n)) for j in range(k)] for i in range(k)]
    xty = [sum(design[i][t] * y[t] for t in range(n)) for i in range(k)]
    beta = _solve_normal(xtx, xty)

    fitted = [sum(design[i][t] * beta[i] for i in range(k)) for t in range(n)]
    resid = [y[t] - fitted[t] for t in range(n)]
    rss = sum(r * r for r in resid)
    tss = sum((v - _mean(y)) ** 2 for v in y)
    r_squared = 1.0 - rss / tss if tss > 0 else 0.0
    dof = n - k
    if dof < 1:
        raise ValueError(f"自由度不足（n={n}, k={k}）")
    sigma2 = rss / dof
    # (X'X)^-1 同样高斯消元（k 阶单位矩阵右端）
    inv = []
    for i in range(k):
        e = [0.0] * k
        e[i] = 1.0
        inv.append(_solve_normal(xtx, e))
    se = [math.sqrt(sigma2 * inv[i][i]) for i in range(k)]
    t_stats = [beta[i] / se[i] if se[i] > 0 else 0.0 for i in range(k)]
    return {
        "intercept": beta[0],
        "coefs": beta[1:],
        "names": names or [f"x{i + 1}" for i in range(len(xs))],
        "se": se,
        "t_stats": t_stats,
        "r_squared": r_squared,
        "n": n,
        "k": k,
        "residual_sigma": math.sqrt(sigma2),
    }


def _hac_lag(n: int) -> int:
    """Newey-West 默认滞后：min(n-1, floor(1 + 4*(n/100)^(2/9)))。"""
    return max(1, min(n - 1, int(1 + 4 * (n / 100.0) ** (2.0 / 9.0))))


def hac_t_stats(
    y: list[float],
    xs: list[list[float]],
    names: list[str] | None = None,
    lag: int | None = None,
) -> dict:
    """Newey-West HAC t 统计量（Bartlett 核，重叠样本/自相关稳健）。

    基于 ols_multi 的残差构造 HAC 协方差：V = (X'X)^-1 S (X'X)^-1，
    S = Σ w_l (Σ x_t e_t x_{t-l}' e_{t-l})。lag 默认 NW 规则。
    返回 ols 结果 + {hac_se, hac_t_stats, lag}。
    """
    ols = ols_multi(y, xs, names)
    n = len(y)
    k = len(xs) + 1
    L = lag if lag is not None else _hac_lag(n)
    design = [[1.0] * n] + [list(col) for col in xs]
    beta = [ols["intercept"]] + ols["coefs"]
    resid = [y[t] - sum(design[i][t] * beta[i] for i in range(k)) for t in range(n)]

    xtx = [[sum(design[i][t] * design[j][t] for t in range(n)) for j in range(k)] for i in range(k)]
    xtx_inv = [_solve_normal(xtx, [1.0 if i == j else 0.0 for j in range(k)]) for i in range(k)]

    s = [[0.0] * k for _ in range(k)]
    for i in range(k):
        for j in range(k):
            for l in range(L + 1):
                w = 1.0 - l / (L + 1)  # Bartlett
                cross = 0.0
                for t in range(l, n):
                    cross += design[i][t] * resid[t] * design[j][t - l] * resid[t - l]
                s[i][j] += w * cross
    # V = inv(X'X) S inv(X'X)
    v = [[0.0] * k for _ in range(k)]
    for i in range(k):
        for j in range(k):
            acc = 0.0
            for p in range(k):
                for q in range(k):
                    acc += xtx_inv[i][p] * s[p][q] * xtx_inv[q][j]
            v[i][j] = acc
    hac_se = [math.sqrt(max(v[i][i], 0.0)) for i in range(k)]
    hac_t = [beta[i] / hac_se[i] if hac_se[i] > 0 else 0.0 for i in range(k)]
    ols["hac_se"] = hac_se
    ols["hac_t_stats"] = hac_t
    ols["lag"] = L
    return ols


def regime_split(
    stock_rets: list[float],
    gold_up: list[bool],
) -> tuple[list[float], list[float]]:
    """Baur (2014) 非对称分桶：金价上涨期/下跌期内的个股日收益。

    gold_up: 与 stock_rets 等长的日级布尔序列（True = 当日金价月/日方向为涨）。
    长度不一致抛 ValueError。返回 (up_rets, down_rets)。
    """
    if len(stock_rets) != len(gold_up):
        raise ValueError("stock_rets 与 gold_up 长度必须一致")
    up: list[float] = []
    down: list[float] = []
    for r, g in zip(stock_rets, gold_up):
        if g:
            up.append(r)
        else:
            down.append(r)
    return up, down


def spread_series(a: list[float], b: list[float]) -> list[float | None]:
    """对数收益差（材料 − 设备）：rs_t = ln(a_t/a_{t-1}) − ln(b_t/b_{t-1})。"""
    if len(a) != len(b) or len(a) < 2:
        raise ValueError("a/b 须等长且 ≥2")
    out: list[float | None] = [None]
    for i in range(1, len(a)):
        if a[i - 1] > 0 and b[i - 1] > 0 and a[i] > 0 and b[i] > 0:
            out.append(math.log(a[i] / a[i - 1]) - math.log(b[i] / b[i - 1]))
        else:
            out.append(None)
    return out


def rs_momentum(spread: list[float | None], lookback: int) -> list[float | None]:
    """RS 动量信号：近 L 日对数差累计（前 L-1 位为 None）。"""
    if lookback < 1:
        raise ValueError(f"lookback 须 ≥1，实际 {lookback}")
    out: list[float | None] = []
    window: list[float] = []
    for v in spread:
        if v is not None:
            window.append(v)
            if len(window) > lookback:
                window.pop(0)
        if len(window) == lookback:
            out.append(sum(window))
        else:
            out.append(None)
    return out


def binomial_test(k_success: int, n: int, p0: float = 0.5) -> dict:
    """双侧二项检验（H0: 成功概率 = p0）。

    小样本用精确二项累积，大样本（n≥30 且 np(1-p)≥5）用正态近似 + 连续性校正。
    返回 {p_value, z, proportion, k, n, p0}。n<1 或 k 越界抛 ValueError。
    """
    if n < 1:
        raise ValueError(f"二项检验需要 n≥1，实际 {n}")
    if not 0 <= k_success <= n:
        raise ValueError(f"k={k_success} 越界（0≤k≤{n}）")
    prop = k_success / n

    def _binom_cdf(limit: int) -> float:
        # P(X ≤ limit) 精确累积（n 较小时）
        import math as _m

        total = 0.0
        for x in range(limit + 1):
            total += _m.comb(n, x) * (p0**x) * ((1 - p0) ** (n - x))
        return total

    if n < 30 or n * p0 * (1 - p0) < 5:
        # 双侧精确：min 侧概率 ×2（截断到 1）
        if prop <= p0:
            p = min(1.0, 2 * _binom_cdf(k_success))
        else:
            p = min(1.0, 2 * (1 - _binom_cdf(k_success - 1)))
        z = (prop - p0) / math.sqrt(p0 * (1 - p0) / n) if p0 * (1 - p0) > 0 else 0.0
    else:
        z = (prop - p0) / math.sqrt(p0 * (1 - p0) / n)
        p = 2 * (1.0 - _normal_cdf(abs(z)))
    return {"p_value": p, "z": z, "proportion": prop, "k": k_success, "n": n, "p0": p0}


def _normal_cdf(z: float) -> float:
    """标准正态 CDF（Abramowitz-Stegun 7.1.26 近似，|z|≤8 精度 ~1e-7）。"""
    if z < -8.0:
        return 0.0
    if z > 8.0:
        return 1.0
    t = 1.0 / (1.0 + 0.2316419 * abs(z))
    pdf = math.exp(-0.5 * z * z) / math.sqrt(2 * math.pi)
    poly = ((((1.330274429 * t - 1.821255978) * t + 1.781477937) * t - 0.356563782) * t + 0.319381530) * t
    cdf = 1.0 - pdf * poly
    return cdf if z >= 0 else 1.0 - cdf


# ---- M4: 事件研究（H2/H1） ----


def market_adjusted(ev_returns: list[float], market_returns: list[float]) -> list[float]:
    """市场调整超额：逐事件相减（两组等长，长度不一致抛 ValueError）。"""
    if len(ev_returns) != len(market_returns):
        raise ValueError(f"事件收益与市场收益长度必须一致，实际 {len(ev_returns)}/{len(market_returns)}")
    return [e - m for e, m in zip(ev_returns, market_returns)]


def calendar_time_portfolio(ev_rets_by_date: dict, sort_key=None) -> list[float]:
    """事件聚类防护：同日多事件先按日聚合（等权），再输出日级收益序列。

    ev_rets_by_date: {date: [收益, ...]}。日级收益 = 当日事件等权均值
    （ABCD §3.1 第 1 条：事件聚类使同日事件 t 虚高，calendar-time portfolio 校正）。
    """
    out: list[float] = []
    for d in sorted(ev_rets_by_date, key=sort_key):
        vals = [v for v in ev_rets_by_date[d] if v is not None]
        if vals:
            out.append(_mean(vals))
    return out
