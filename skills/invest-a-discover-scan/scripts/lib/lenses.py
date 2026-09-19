"""多透镜粗筛（DS-1 / T12-2）——纯函数，不联网。

- **L1 横截面便宜（命中门槛）**：`EY = 100/PE_TTM`；全 A 正 PE 子总体分位 ≤ 15%
  且行业内 PE_TTM 排名前 25%（设计 §2.1）
- **L3 定价-盈利 gap（加分项，非门槛）**：`gap = EY − (rf + 2pp)`；`g_implied` vs
  预告净利增速上限，各记 0/1
- **L4 市场语境不进规则**（仅报告头注记；Kirby 2023：状态标签不蕴含收益可预测性）

口径钉住：`pe_grank` 必须等于共享 `stats.percentile_rank_inclusive(...) / 100`
（**与 pulse 同口径**，含边界 `<=`）；分位返回**百分数**，故须 `/100`。

排序为**确定性规则**（设计 §2.3）：主键 `pe_grank` 升 → 次键 `gap_flags` 之和降 →
三级键 `ey_pct` 降 → **`ts_code` 兜底**（并列时保证同输入同输出）。
"""
from __future__ import annotations

from bisect import bisect_left, bisect_right

from stats import percentile_rank_inclusive  # noqa: E402 —— 共享统计库

import pool as pool_mod

# 预注册阈值（设计 §2.1 草案；改动须 bump rules_version 并记录原因）
PE_GRANK_MAX = 0.15
IND_RANK_MAX = 0.25
SPREAD_PP = 2.0
# PE 合理性下限：PE < 1 意味着「一年盈利 > 整个市值」——几乎必然含**一次性损益**
# （债务重组收益、资产处置）或数据异常。此类值在横截面分位上**必然排第一**，
# 静默排在榜首会把「困境股」当成「低估发现」（真机实测：港股池前三名全是 PE<1 的房企）
PE_ANOMALY_MIN = 1.0


def pe_anomaly(pe_ttm) -> str | None:
    """PE 异常的显式标注（None = 正常）。**不剔除**（剔除即静默丢信息），只标注。"""
    pe = _pos(pe_ttm)
    if pe is None:
        return None
    if pe < PE_ANOMALY_MIN:
        return (f"⚠️ **极低 PE（{pe:.2f}x < 1）**：一年盈利超过整个市值 →"
                f"盈利可能含**一次性损益**（重组/处置收益）或数据异常 ——"
                f"**须先核实利润构成**，不得直接读作「极度低估」")
    return None


def _pos(v) -> float | None:
    """正数值；None / 非数值 / ≤0 → None（亏损期不参与 L1）。"""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f or f <= 0:      # NaN 或非正
        return None
    return f


def ey_pct(pe_ttm) -> float | None:
    """盈利收益率（百分数）= ``100 / PE_TTM``；PE ≤ 0 或缺失 → None。"""
    pe = _pos(pe_ttm)
    return None if pe is None else 100.0 / pe


def implied_growth_pct(pe_ttm, payout: float = 1.0) -> float | None:
    """市场隐含永续增速（百分数）= ``payout / PE_TTM × 100``（payout=1 时即 EY）。

    仅作与预告增速上限的**对照项**——不是估值目标，不构成预测。
    """
    pe = _pos(pe_ttm)
    if pe is None:
        return None
    return payout * 100.0 / pe


def pe_grank(pe_ttm, universe_pes: list[float]) -> float | None:
    """全 A 正 PE 子总体分位（0–1，越小越便宜）。

    **口径钉住**：与 `skills/lib/stats.percentile_rank_inclusive` 同口径
    （含边界 `<=`，返回百分数 → `/100`）。序列为空 / PE 非正 → None。
    """
    pe = _pos(pe_ttm)
    vals = [v for v in (_pos(p) for p in universe_pes or []) if v is not None]
    if pe is None or not vals:
        return None
    return percentile_rank_inclusive(vals, pe) / 100.0


def industry_rank(pe_ttm, peers: list[float]) -> tuple[int, int] | None:
    """行业内 PE 排名 → ``(rk, n)``，rk 从 1 起，并列取 **min-rank**。

    min-rank 口径写死：否则同 PE 两只谁在前取决于遍历顺序，破坏排序确定性。
    """
    pe = _pos(pe_ttm)
    vals = [v for v in (_pos(p) for p in peers or []) if v is not None]
    if pe is None or not vals:
        return None
    return (1 + sum(1 for v in vals if v < pe), len(vals))


def select_candidates(rows: list[dict], *, pe_grank_max: float = PE_GRANK_MAX,
                      ind_rank_max: float = IND_RANK_MAX,
                      skip_industry: bool = False) -> list[dict]:
    """L1 命中 = 全 A 分位门槛 ∧ 行业内排名门槛（两条均为 **≤**，含边界）。

    输入 ``[{ts_code, industry, pe_ttm}]``（已过池构建）。行业缺失 → **跳过行业条件**
    并置 ``industry_missing=True``（设计 §5：缺失不得静默剔除，应明示降级）。
    输出命中行 + ``ey_pct`` / ``pe_grank`` / ``ind_rk`` / ``ind_n``。
    """
    pos = [(r, _pos(r.get("pe_ttm"))) for r in rows]
    pos = [(r, pe) for r, pe in pos if pe is not None]
    if not pos:
        return []
    # D10：分位**一次排序 + bisect**，不要每只标的重扫全序列——
    # 原实现是 O(N²)（实测 5000 行 1.5s，3× 输入 8.6× 耗时）。
    # 口径与 `percentile_rank_inclusive` 一致：count(v <= x) / n（含边界）。
    universe = sorted(pe for _r, pe in pos)
    n_uni = len(universe)

    by_ind: dict[str, list[float]] = {}
    for r, pe in pos:
        by_ind.setdefault(str(r.get("industry") or pool_mod._MISSING_INDUSTRY), []).append(pe)
    by_ind_sorted = {k: sorted(v) for k, v in by_ind.items()}

    out: list[dict] = []
    for r, pe in pos:
        g = bisect_right(universe, pe) / n_uni
        if g is None or g > pe_grank_max:
            continue
        industry = str(r.get("industry") or pool_mod._MISSING_INDUSTRY)
        missing = pool_mod.industry_missing(industry)
        if skip_industry:
            # 港股池：universe 无行业字段（实测）→ **整条行业条件跳过**（walk-around 而非
            # 逐只置 missing，避免把「市场级缺字段」写成「个别标的缺字段」）
            missing = True
        rk, n = (None, None)
        if not missing:
            peers = by_ind_sorted.get(industry) or []
            if not peers:
                continue
            n = len(peers)
            rk = bisect_left(peers, pe) + 1        # min-rank（并列取最小名次）
            if rk / n > ind_rank_max:
                continue
        out.append({**r, "ey_pct": ey_pct(pe), "pe_grank": g,
                    "ind_rk": rk, "ind_n": n, "industry_missing": missing})
    return out


def universe_median_pe(rows: list[dict]) -> float | None:
    """正 PE 子总体的**中位 PE**（分位参照系的中心）。

    ⚠️ 报告里**不得只给分位**：CLAUDE.md 估值分位使用规则 3 要求分位数必须伴随
    中位数或均值，否则读者无法判断「0.2% 分位」是相对什么分布而言的
    （由 lint `percentile-without-median` 拦截）。
    """
    from stats import median as _median

    vals = [v for v in (_pos(r.get("pe_ttm")) for r in rows) if v is not None]
    return _median(vals)


def gap_pct(ey: float | None, rf_pct: float | None,
            spread_pp: float = SPREAD_PP) -> float | None:
    """L3 利差（百分点）= ``EY − (rf + 2pp)``；任一输入缺失 → None。

    用**百分点**口径（EY 与 rf 均已 ×100），避免与 `valuation_calc.calc_opportunity_cost`
    的小数口径混淆——本 skill 内部统一百分数，输出时标注单位。
    """
    if ey is None or rf_pct is None:
        return None
    try:
        return float(ey) - (float(rf_pct) + float(spread_pp))
    except (TypeError, ValueError):
        return None


def gap_flags(*, ey, rf_pct, pe_ttm, forecast_growth_max_pct) -> list[int]:
    """L3 两个加分项 → ``[利差>0, 隐含增速<预告上限]``（各 0/1）。

    输入缺失时该项记 0（**不加分**而非报错——L3 是排序项不是门槛）。
    """
    gap = gap_pct(ey, rf_pct)
    flag_gap = 1 if (gap is not None and gap > 0) else 0

    g_implied = implied_growth_pct(pe_ttm)
    flag_growth = 0
    if g_implied is not None and forecast_growth_max_pct is not None:
        try:
            if g_implied < float(forecast_growth_max_pct):
                flag_growth = 1
        except (TypeError, ValueError):
            flag_growth = 0
    return [flag_gap, flag_growth]


def rank_candidates(rows: list[dict], *, per_industry: int | None = 3) -> list[dict]:
    """确定性排序 + 行业分散。

    主键 ``pe_grank`` 升 → 次键 ``sum(gap_flags)`` 降 → 三级 ``ey_pct`` 降 →
    **``ts_code`` 兜底**；随后同行业最多 ``per_industry`` 只。

    ⚠️ ``per_industry=None`` = **不做行业分散**（港股池必须用）：港股全部行同属
    「—（港股无行业字段）」**一个桶**，若按默认 3 只封顶，第 4 名起会被静默丢弃
    （实测：`--pool hk --top 15` 恒只出 3 只，与上限无关）。

    不就地修改入参（D7）；返回新 dict 列表。
    """
    def _key(r: dict):
        return (r.get("pe_grank") if r.get("pe_grank") is not None else 9.9,
                -sum(r.get("gap_flags") or []),
                -(r.get("ey_pct") if r.get("ey_pct") is not None else -1e9),
                str(r.get("ts_code") or ""))

    ordered = sorted((dict(r) for r in rows), key=_key)
    kept: list[dict] = []
    per_ind: dict[str, int] = {}
    unlimited = per_industry is None
    for r in ordered:
        ind = str(r.get("industry") or pool_mod._MISSING_INDUSTRY)
        if not unlimited and per_ind.get(ind, 0) >= per_industry:
            continue
        per_ind[ind] = per_ind.get(ind, 0) + 1
        kept.append(r)
    return kept
