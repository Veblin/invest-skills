"""L1 横截面 / L3 gap 透镜 — 离线单测（设计 §2.1 / §2.3）。"""
from __future__ import annotations

from stats import percentile_rank_inclusive

import lenses


# ── L1：EY / 分位 / 行业排名 ─────────────────────────────────────────────

def test_ey_pct_is_inverse_pe_in_percent():
    assert lenses.ey_pct(20.0) == 5.0
    assert lenses.ey_pct(8.0) == 12.5


def test_ey_pct_none_for_non_positive_or_missing():
    """亏损期 PE ≤ 0 不参与 L1（不得当成「极便宜」）。"""
    for bad in (None, 0, -5.0, "x"):
        assert lenses.ey_pct(bad) is None


def test_pe_grank_matches_shared_percentile_caliber():
    """口径钉住：必须等于共享 stats.percentile_rank_inclusive / 100（与 pulse 同口径）。"""
    pes = [10.0, 20.0, 30.0, 40.0]
    for pe in pes:
        assert lenses.pe_grank(pe, pes) == percentile_rank_inclusive(pes, pe) / 100.0


def test_pe_grank_none_on_bad_input():
    assert lenses.pe_grank(None, [1.0]) is None
    assert lenses.pe_grank(10.0, []) is None
    assert lenses.pe_grank(0, [1.0, 2.0]) is None


def test_industry_rank_and_size():
    peers = [5.0, 10.0, 15.0, 20.0]
    assert lenses.industry_rank(5.0, peers) == (1, 4)
    assert lenses.industry_rank(15.0, peers) == (3, 4)
    assert lenses.industry_rank(20.0, peers) == (4, 4)


def test_industry_rank_ties_share_min_rank():
    """并列取 min-rank（口径写死；否则同 PE 的两只谁在前取决于遍历顺序）。"""
    peers = [10.0, 10.0, 20.0]
    assert lenses.industry_rank(10.0, peers) == (1, 3)


# ── L1 命中（交集 + 门槛边界）────────────────────────────────────────────

def _rows():
    # 一个行业 8 只：PE 5,6,7,8,9,10,11,12（行业前 25% = 前 2 名）
    return [{"ts_code": f"60000{i}.SH", "industry": "银行", "pe_ttm": float(5 + i)}
            for i in range(8)] + [
        {"ts_code": "000001.SZ", "industry": "白酒", "pe_ttm": 30.0},
        {"ts_code": "000002.SZ", "industry": "白酒", "pe_ttm": 100.0},
    ]


def test_select_candidates_applies_both_gates():
    out = lenses.select_candidates(_rows())
    codes = {r["ts_code"] for r in out}
    assert out, "至少应命中行业前 25% 的低 PE 标的"
    for r in out:
        assert r["pe_grank"] <= 0.15
        assert r["ind_rk"] / r["ind_n"] <= 0.25
    assert "000002.SZ" not in codes, "PE 100 的标的不应命中"


def test_select_candidates_threshold_is_inclusive_at_boundary():
    """门槛为 **≤**（含边界）——边界值必须进，边界外一位必须出。"""
    # 全正 PE 子总体 = [10,20]；pe=10 → grank=0.5；pe=20 → grank=1.0
    inside = [{"ts_code": "A", "industry": "X", "pe_ttm": 10.0},
              {"ts_code": "B", "industry": "X", "pe_ttm": 20.0}]
    out = lenses.select_candidates(inside, pe_grank_max=0.5, ind_rank_max=1.0)
    codes = {r["ts_code"] for r in out}
    assert "A" in codes, "pe_grank 恰为门槛值时须命中（口径为 ≤）"
    assert "B" not in codes, "pe_grank 为 1.0 超出 0.5 门槛，不应命中"


def test_select_candidates_skips_industry_gate_when_industry_missing():
    """行业缺失（—）→ 行业条件跳过并记 warning，**不静默剔除**（设计 §5）。"""
    rows = [{"ts_code": "A", "industry": "—", "pe_ttm": 5.0, "industry_missing": True},
            {"ts_code": "B", "industry": "—", "pe_ttm": 10.0, "industry_missing": True}]
    out = lenses.select_candidates(rows, pe_grank_max=1.0, ind_rank_max=0.0)
    assert {r["ts_code"] for r in out} == {"A", "B"}


def test_select_candidates_ignores_non_positive_pe():
    rows = [{"ts_code": "A", "industry": "X", "pe_ttm": -3.0},
            {"ts_code": "B", "industry": "X", "pe_ttm": 10.0}]
    out = lenses.select_candidates(rows, pe_grank_max=1.0, ind_rank_max=1.0)
    assert {r["ts_code"] for r in out} == {"B"}


# ── L3：gap 与排序 ───────────────────────────────────────────────────────

def test_implied_growth_is_inverse_pe_in_percent():
    assert lenses.implied_growth_pct(20.0) == 5.0
    assert lenses.implied_growth_pct(0) is None
    assert lenses.implied_growth_pct(None) is None


def test_gap_pct_subtracts_rf_plus_spread():
    # EY 12.5 − (1.73 + 2.0) = 8.77
    assert round(lenses.gap_pct(12.5, 1.73), 4) == 8.77
    assert lenses.gap_pct(12.5, None) is None


def test_gap_flags_both_subitems():
    flags = lenses.gap_flags(ey=12.5, rf_pct=1.73, pe_ttm=8.0, forecast_growth_max_pct=20.0)
    assert flags == [1, 1]      # 利差 8.77 > 0 且 隐含增速 12.5 < 预告上限 20
    # 利差 1.0 − 7.0 < 0 → 0；隐含增速 1.0 不低于上限 0.5 → 0
    assert lenses.gap_flags(ey=1.0, rf_pct=5.0, pe_ttm=100.0,
                            forecast_growth_max_pct=0.5) == [0, 0]


def test_gap_flags_missing_inputs_are_zero_not_crash():
    assert lenses.gap_flags(ey=None, rf_pct=None, pe_ttm=None, forecast_growth_max_pct=None) == [0, 0]
    assert lenses.gap_flags(ey=12.5, rf_pct=None, pe_ttm=8.0, forecast_growth_max_pct=None) == [0, 0]


def test_rank_candidates_is_deterministic_with_tie_breaker():
    """并列时以 ts_code 兜底——同输入两次跑必须同输出（排序确定性的前提）。"""
    rows = [{"ts_code": "B", "industry": "X", "pe_grank": 0.05, "gap_flags": [0, 0], "ey_pct": 10.0},
            {"ts_code": "A", "industry": "X", "pe_grank": 0.05, "gap_flags": [0, 0], "ey_pct": 10.0}]
    first = [r["ts_code"] for r in lenses.rank_candidates(rows, per_industry=9)]
    second = [r["ts_code"] for r in lenses.rank_candidates(list(reversed(rows)), per_industry=9)]
    assert first == second == ["A", "B"]


def test_rank_candidates_orders_by_grank_then_flags_then_ey():
    rows = [
        {"ts_code": "A", "industry": "X", "pe_grank": 0.10, "gap_flags": [1, 1], "ey_pct": 5.0},
        {"ts_code": "B", "industry": "Y", "pe_grank": 0.01, "gap_flags": [0, 0], "ey_pct": 20.0},
        {"ts_code": "C", "industry": "Z", "pe_grank": 0.10, "gap_flags": [1, 1], "ey_pct": 9.0},
    ]
    order = [r["ts_code"] for r in lenses.rank_candidates(rows, per_industry=9)]
    assert order == ["B", "C", "A"], "主键 grank 升 → 次键 flags 和降 → 三级 ey 降"


def test_rank_candidates_caps_per_industry():
    rows = [{"ts_code": f"S{i}", "industry": "X", "pe_grank": 0.01 * i,
             "gap_flags": [0, 0], "ey_pct": 10.0} for i in range(1, 6)]
    rows.append({"ts_code": "OTHER", "industry": "Y", "pe_grank": 0.9,
                 "gap_flags": [0, 0], "ey_pct": 1.0})
    out = lenses.rank_candidates(rows, per_industry=3)
    xs = [r["ts_code"] for r in out if r["industry"] == "X"]
    assert len(xs) == 3, "同行业最多 3 只"
    assert "OTHER" in {r["ts_code"] for r in out}


def test_rank_candidates_does_not_mutate_input():
    """D7：不得就地改调用方传入的 dict（清单可能来自缓存/多处引用）。"""
    rows = [{"ts_code": "A", "industry": "X", "pe_grank": 0.1, "gap_flags": [0, 0], "ey_pct": 5.0}]
    lenses.rank_candidates(rows, per_industry=3)
    assert "rank" not in rows[0]


def test_top_n_slicing():
    rows = [{"ts_code": f"S{i:02d}", "industry": f"IN{i}", "pe_grank": 0.01 * i,
             "gap_flags": [0, 0], "ey_pct": 10.0} for i in range(1, 20)]
    assert len(lenses.rank_candidates(rows, per_industry=3)[:15]) == 15
