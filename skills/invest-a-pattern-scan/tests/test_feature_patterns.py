"""R-B01 三特征模板 + R-B02 左右侧双组回测 — 离线单测。

证据纪律（hypothesis-registry C11 = 待验证假设，**不得作规则层依据**）：
- 三特征**只作筛选特征**，命中一律带 `evidence_note`
- 特征③的条件分层假设出自 **Liu (2015)（中文期刊，待核验）**
- 事件研究引用须带样本期注记（S&W 上证 2001-2003 / Chen 深交所账户级）
- R-B02 **禁止预设结论**（措辞为「本池样本内右侧 X% vs 左侧 Y%，样本 N」）
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts" / "lib"))

import feature_patterns as fp  # noqa: E402


def _lin(a, b, n):
    """分段线性序列（可控构造，避免用随机序列碰运气）。"""
    return [a + (b - a) * i / (n - 1) for i in range(n)]


def _sawtooth(*, cycles=8, up=10, down=5, lo=100.0, amp=0.10):
    """「涨 up 根（放量）→ 跌 down 根（缩量）」锯齿——每轮产生一次缩量回踩候选。"""
    closes, vols = [], []
    px = lo
    for _ in range(cycles):
        for _i in range(up):
            px *= 1 + amp / up
            closes.append(px)
            vols.append(1000.0)
        for _i in range(down):
            px *= 1 - (amp * 1.1) / down
            closes.append(px)
            vols.append(300.0)
    return closes, vols


def _divergence_series():
    """价更低 + 动能更弱：陡跌→反弹→慢跌（跌破前低）→回弹。

    ⚠️ 末段**必须留回弹**：pivot 需左右各 N 根，否则最后一个低点永远无法确认
    （确认型 pivot 的固有滞后——不能用「今天的低点」做背离判定）。
    """
    return _lin(100, 70, 45) + _lin(70, 85, 25) + _lin(85, 66, 50) + _lin(66, 74, 20)


# ── ① MACD 底背离 ────────────────────────────────────────────────────────

def test_macd_divergence_detected_on_price_low_macd_higher_low():
    """构造：价格两个低点更低的低点，但下跌动能递减（MACD 抬高）。"""
    out = fp.detect_macd_divergence(_divergence_series(), pivot_window=4, max_pivots=3)
    assert out, "应检出至少一处价跌-MACD 抬高的背离"
    for h in out:
        d = h["detail"]
        assert d["price_now"] < d["price_prev"]
        assert d["dif_now"] > d["dif_prev"]
        assert "endpoint_idx" in h


def test_macd_divergence_hit_carries_evidence_note():
    for h in fp.detect_macd_divergence(_divergence_series(), pivot_window=4):
        assert "自家样本统计" in h["evidence_note"]
        assert "无顶级期刊直接检验" in h["evidence_note"]


def test_macd_divergence_short_sample_is_empty():
    assert fp.detect_macd_divergence([1.0] * 10) == []


# ── ② 缩量回踩 ───────────────────────────────────────────────────────────

def test_shrink_pullback_detected():
    closes = [100.0] + [100 + i * 0.5 for i in range(20)] + [110 - i * 0.9 for i in range(15)]
    vols = [1000.0] * 21 + [400.0] * 15        # 回踩期量能收缩
    out = fp.detect_shrink_pullback(closes, vols)
    assert out
    d = out[0]["detail"]
    assert d["pullback_pct"] >= fp.PULLBACK_MIN_PCT
    assert d["shrink_ratio"] is not None and d["shrink_ratio"] <= fp.SHRINK_MAX_RATIO


def test_shrink_pullback_rejects_volume_expansion():
    closes = [100.0] + [100 + i * 0.5 for i in range(20)] + [110 - i * 0.9 for i in range(15)]
    vols = [1000.0] * 21 + [3000.0] * 15       # 回踩放量 → 不是缩量回踩
    assert fp.detect_shrink_pullback(closes, vols) == []


def test_shrink_semantics_restricted():
    closes = [100.0] + [100 + i * 0.5 for i in range(20)] + [110 - i * 0.9 for i in range(15)]
    out = fp.detect_shrink_pullback(closes, [1000.0] * 21 + [400.0] * 15)
    sem = out[0]["detail"]["semantics"]
    assert "低活动" in sem and "信息真空" in sem
    assert "禁止" in sem, "须显式禁止映射「支撑成立/不会破位」"


def test_shrink_pullback_without_volume_still_needs_depth():
    """量能不可得时不冒充「缩量」——但仍须满足回踩幅度。"""
    closes = [100.0] + [100 + i * 0.5 for i in range(20)] + [110 - i * 0.1 for i in range(15)]
    out = fp.detect_shrink_pullback(closes, [None] * len(closes))
    assert out == [], "浅回踩不应命中（量能不可得也不放宽幅度条件）"


# ── ③ 涨停站上中期均线 ───────────────────────────────────────────────────

def test_limit_up_above_ma_detected():
    closes = [100.0] * 70                       # 均线上下平稳
    closes[-1] = closes[-2] * 1.10              # 涨停
    out = fp.detect_limit_up_above_ma(closes, ma=60)
    assert len(out) == 1
    d = out[0]["detail"]
    assert d["chg_pct"] >= 9.8
    assert d["close"] > d["ma_value"]
    assert "待核验" in d["layering_hypothesis"]


def test_limit_up_under_ma_not_hit():
    """长期下跌后的一次涨停**未能站上**中期均线 → 不命中。"""
    closes = _lin(200, 100, 80)                  # 80 根持续下跌，MA60 远在上方
    closes[-1] = closes[-2] * 1.10               # 末根涨停（+10%）
    out = fp.detect_limit_up_above_ma(closes, ma=60)
    assert out == [], "涨停但收盘仍在 MA60 下方 → 不应命中"


def test_limit_up_hit_carries_sample_period_note():
    closes = [100.0] * 70
    closes[-1] = closes[-2] * 1.10
    out = fp.detect_limit_up_above_ma(closes, ma=60)
    note = out[0]["evidence_note"]
    assert "2001-2003" in note or "样本期" in note
    assert "相反" in note, "须写明涨停方向与「确认续涨」相反"


# ── 汇总与规则宇宙键 ─────────────────────────────────────────────────────

def test_detect_all_returns_all_three_kinds_possible():
    closes = [100.0] * 70
    closes[-1] = closes[-2] * 1.10
    out = fp.detect_all(closes, [1000.0] * 70)
    assert out
    for h in out:
        assert "evidence_note" in h and h["evidence_note"]


def test_rule_keys_cover_three_features_times_horizons():
    keys = fp.rule_keys((5, 10, 20))
    assert len(keys) == 9
    for f in fp.FEATURES:
        for h in (5, 10, 20):
            assert f"{f}_+{h}" in keys


def test_features_never_claim_direction():
    closes = [100.0] * 70
    closes[-1] = closes[-2] * 1.10
    text = str(fp.detect_all(closes, [1000.0] * 70))
    for banned in ("将上涨", "将下跌", "看多", "看空", "建议买入"):
        assert banned not in text


# ── R-B02 左右侧双组回测 ─────────────────────────────────────────────────

def test_group_stats_basic():
    s = fp.group_stats([0.05, -0.02, 0.03, -0.01, 0.04])
    assert s["n"] == 5
    assert s["win_rate_pct"] == 60.0
    assert s["mean_pct"] is not None and s["max_drawdown_pct"] is not None


def test_group_stats_empty_is_three_state():
    s = fp.group_stats([])
    assert s["n"] == 0 and s["win_rate_pct"] is None


def test_left_right_backtest_reports_both_groups_and_n():
    out = fp.left_right_backtest(right_returns=[0.02, 0.03, -0.01],
                                 left_returns=[-0.02, 0.01, 0.0, 0.05], horizon=10)
    assert out["horizon"] == 10
    assert len(out["groups"]) == 2
    assert "样本 3/4" in out["wording_template"]


@pytest.mark.parametrize("banned", ["右侧更优", "右侧更稳健", "左侧更优", "说明右侧"])
def test_left_right_backtest_forbids_preset_conclusion(banned):
    """**禁止预设结论**——措辞模板固定为并列数值。"""
    out = fp.left_right_backtest(right_returns=[0.02, 0.03], left_returns=[-0.01], horizon=5)
    assert banned not in str(out)


def test_left_right_backtest_insufficient_sample_no_conclusion():
    out = fp.left_right_backtest(right_returns=[], left_returns=[0.01], horizon=5)
    assert "样本不足" in out["wording_template"]


# ── ② 缩量回踩：历史事件逐点检测（轮末评审修复 2026-09-13）───────────────────

def test_shrink_pullback_finds_historical_event_after_full_recovery():
    """回踩发生在**中段**、其后已收复并创新高 → 仍须检出。

    ⚠️ 老实现只判「当前状态」（前高取最近 `peak_window+1` 根窗口内的最高价，
    终点恒为最后一根 K）：价格收复后 pullback ≈ 0 → 恒不命中，且**终点恒为末根**
    使前向收益窗恒空，该规则在 RC 规则矩阵里恒为全 0 列（实测 24/27 条有效）。
    """
    closes = _lin(100.0, 120.0, 21) + _lin(120.0, 110.0, 10)[1:] + _lin(110.0, 130.0, 30)[1:]
    vols = [1000.0] * 21 + [400.0] * 9 + [1000.0] * (len(closes) - 30)
    out = fp.detect_shrink_pullback(closes, vols)
    assert out, "历史回踩被漏检（老实现的典型失败）"
    assert all(e["endpoint_idx"] < len(closes) - 1 for e in out), \
        "终点不得恒为最后一根 K（否则前向收益窗恒空）"
    assert any(e["detail"]["peak_idx"] == 20 for e in out), "前高须落在中段峰值（i=20）"


def test_shrink_pullback_caps_and_keeps_most_recent_events():
    """命中数按 `MAX_PIVOTS` 截断，且保留**最近**的若干次而非最早的。

    夹具：8 轮「涨 10 根 + 跌 5 根」锯齿（每轮末段构成一次缩量回踩）→ 检出数
    远超上限；截断后须落在**尾部**（保留最早的实现会让「最近一次回踩」缺席）。
    """
    closes, vols = _sawtooth(cycles=8)
    out = fp.detect_shrink_pullback(closes, vols)
    eps = [e["endpoint_idx"] for e in out]
    assert len(out) == fp.MAX_PIVOTS, f"须按 MAX_PIVOTS 截断，实得 {len(out)}"
    assert eps == sorted(eps), "事件须按时间升序"
    assert max(eps) >= len(closes) * 3 // 4, \
        f"须保留尾部（最近）事件，实得末次 idx={max(eps)} / 共 {len(closes)} 根"


def test_shrink_pullback_nan_volume_is_not_shrink():
    """量能全为 NaN 时**不得**冒充「缩量」——NaN 会穿过 `is not None` 让
    `ratio > 阈值` 恒 False，老实现把放量踩踏报成缩量回踩并落盘 NaN。"""
    closes = _lin(100.0, 120.0, 21) + _lin(120.0, 105.0, 15)[1:]
    vols = [float("nan")] * len(closes)
    out = fp.detect_shrink_pullback(closes, vols)
    assert out, "幅度达标仍应命中（只是量能不可得）"
    for e in out:
        assert e["detail"]["shrink_ratio"] is None
        assert e["detail"]["volume_available"] is False


def test_shrink_pullback_vols_shorter_than_closes_does_not_crash():
    """量能序列短于价格（截断/停牌）→ 按缺量处理，不得 IndexError。"""
    closes = _lin(100.0, 120.0, 21) + _lin(120.0, 105.0, 15)[1:]
    out = fp.detect_shrink_pullback(closes, [1000.0] * 10)
    assert isinstance(out, list)
