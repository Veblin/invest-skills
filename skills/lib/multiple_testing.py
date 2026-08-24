"""多重检验与数据窥探防护纯函数库（v0.2.6 M2）。

White (2000) Reality Check 最大统计量 bootstrap + Benjamini-Hochberg FDR +
分位 bootstrap CI。供形态扫描器规则宇宙校正与回测多重比较使用。

与 backtest.permutation_test 正交互补：
  - permutation_test：两组标签洗牌（H0：组间同分布，组间比较用）
  - reality_check：单组多规则时间块重采样（H0：最优规则统计量来自噪声，
    全市场规则宇宙校正用）

原则:
  - 纯函数，无副作用（random.Random 显式 seed 可复现）
  - 样本不足 fail loud（ValueError）
  - 统计口径: 规则收益矩阵为 T×K（行=交易日，列=规则），须已扣基准
"""

from __future__ import annotations

import math
import random
from typing import Callable


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs)


def block_bootstrap_indices(
    n: int,
    block_size: int = 5,
    n_boot: int = 10000,
    seed: int = 42,
) -> list[list[int]]:
    """循环块 bootstrap 索引 → n_boot 组，每组 n 个原始行索引。

    块抽样保留日内/邻日自相关结构（重叠样本 Newey-West 的 bootstrap 对应物，
    ABCD §3.2 统计纪律）。n < 2 或 block_size < 1 抛 ValueError。
    """
    if n < 2:
        raise ValueError(f"bootstrap 需要 n≥2，实际 {n}")
    if block_size < 1:
        raise ValueError(f"block_size 须 ≥1，实际 {block_size}")
    rng = random.Random(seed)
    n_blocks = math.ceil(n / block_size)
    out: list[list[int]] = []
    for _ in range(n_boot):
        # 每块独立随机起点（标准 CBS）：单起点循环移位会使 boot 样本
        # ≈ 全样本置换（每个索引恰出现一次），boot 均值恒等于全样本均值——
        # 零分布退化，p 恒 0。
        idx: list[int] = []
        for _ in range(n_blocks):
            start = rng.randrange(n)
            for j in range(block_size):
                idx.append((start + j) % n)
        out.append(idx[:n])
    return out


def reality_check(
    rule_returns: list[list[float]],
    n_boot: int = 10000,
    block_size: int = 5,
    seed: int = 42,
    statistic: Callable[[list[float]], float] = _mean,
) -> dict:
    """White (2000) Reality Check — 最优规则是否显著优于零（已扣基准）。

    输入 rule_returns: T×K 矩阵（行=交易日，列=规则），每列须已扣基准收益。
    检验：观测最优规则统计量 vs 块 bootstrap 下"最优规则统计量"的零分布
    （H0：全部规则真实期望 ≤ 0，即最优者来自噪声）。
    p = P(boot_max ≥ obs_max)。

    返回 {best_rule, best_stat, p_value, n_rules, n_obs, block_size, n_boot}。
    规则数 K < 2 或行数 T < 2 抛 ValueError。
    """
    n_obs = len(rule_returns)
    n_rules = len(rule_returns[0]) if n_obs else 0
    if n_obs < 2:
        raise ValueError(f"reality_check 需要 T≥2 行，实际 {n_obs}")
    if n_rules < 2:
        raise ValueError(f"reality_check 需要 K≥2 条规则，实际 {n_rules}")
    if any(len(row) != n_rules for row in rule_returns):
        raise ValueError("rule_returns 必须为 T×K 矩形矩阵（每行 K 列）")

    # 列统计量：每规则一列（T 个观测）
    stats = [statistic([rule_returns[i][j] for i in range(n_obs)]) for j in range(n_rules)]
    obs_max = max(stats)
    best_rule = stats.index(obs_max)

    # 块 bootstrap（H0 重定心）：零分布必须强制 H0（各规则真实期望 ≤ 0），
    # 即 bootstrap 重采样前每列减去自身样本均值——否则带漂移规则在
    # 零分布里也恒为正，p 恒 1（White 2000 的 centered RC 做法）。
    indices = block_bootstrap_indices(n_obs, block_size, n_boot, seed)
    count = 0
    for idx in indices:
        boot_max = max(
            statistic([rule_returns[i][j] - stats[j] for i in idx])
            for j in range(n_rules)
        )
        if boot_max >= obs_max:
            count += 1
    return {
        "best_rule": best_rule,
        "best_stat": obs_max,
        "p_value": count / n_boot,
        "n_rules": n_rules,
        "n_obs": n_obs,
        "block_size": block_size,
        "n_boot": n_boot,
    }


def bh_fdr(p_values: list[float], alpha: float = 0.05) -> dict:
    """Benjamini-Hochberg FDR 校正。

    p_values 可为 None（剔除，不参与校正）。返回
    {significant: [bool]*len(原序), q_values: [float|None], n_rejected, alpha}。
    空列表 → significant=[] 且 n_rejected=0（不抛）。
    """
    if not p_values:
        return {"significant": [], "q_values": [], "n_rejected": 0, "alpha": alpha}
    m = len(p_values)
    # 排序（保留原索引），None 剔除
    indexed = sorted(
        ((p, i) for i, p in enumerate(p_values) if p is not None),
        key=lambda x: x[0],
    )
    k = len(indexed)
    q_raw: list[float] = []
    rejected: set[int] = set()
    max_q = None  # 从大到小回溯的 q 值
    for rank, (p, i) in reversed(list(enumerate(indexed, start=1))):
        q = p * k / rank
        if max_q is None or q < max_q:
            max_q = q
        q_adj = min(max_q, 1.0)  # 单调化 q 值（BH 单调保持,与 q_values 输出一致）
        q_raw.append((i, q_adj))
        # 判定必须用单调化 q 值: q_values 暴露的是 q_adj——用 raw q 判定时
        # 「存储的 q 值 ≤ α」与「拒绝集」脱节: 小 p 因前置大 p 折衷而 q_adj
        # 小于 raw q 时 raw 判定漏拒,拒绝集不再嵌套（code-review 第五轮,
        # 例: [0.02,0.033,0.049] α=0.05 标准 BH 全拒,raw 判定漏拒首项）。
        if q_adj <= alpha:
            rejected.add(i)
    q_values: list[float | None] = [None] * m
    for i, q in q_raw:
        q_values[i] = q
    significant = [i in rejected for i in range(m)]
    return {
        "significant": significant,
        "q_values": q_values,
        "n_rejected": len(rejected),
        "alpha": alpha,
    }


def bootstrap_ci(
    xs: list[float],
    statistic: Callable[[list[float]], float] = _mean,
    n_boot: int = 10000,
    seed: int = 42,
    ci_level: float = 0.95,
) -> dict:
    """分位 bootstrap 置信区间（单序列，独立重采样）。

    返回 {lower, upper, observed, ci_level, n_boot}。样本 < 2 抛 ValueError。
    """
    n = len(xs)
    if n < 2:
        raise ValueError(f"bootstrap_ci 需要 n≥2，实际 {n}")
    rng = random.Random(seed)
    alpha_tail = (1.0 - ci_level) / 2
    lo_q = alpha_tail
    hi_q = 1.0 - alpha_tail
    boot = [statistic([xs[rng.randrange(n)] for _ in range(n)]) for _ in range(n_boot)]
    boot.sort()
    observed = statistic(xs)
    return {
        "lower": boot[int(lo_q * (n_boot - 1))],
        "upper": boot[int(hi_q * (n_boot - 1))],
        "observed": observed,
        "ci_level": ci_level,
        "n_boot": n_boot,
    }
