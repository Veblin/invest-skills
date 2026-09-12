"""R-A03 量价观察特征 + R-A04 条件性反转观测窗 — 离线单测。

规格（源文档 §3 R-A03 / R-A04）：
- R-A03 是**自定义观察特征**——「不声称复现任何论文机制，须自家样本后验后才考虑规则化」
- LMSW b2 符号**仅作统计方向描述**；「投机/知情 vs 风险分担」身份解释
  **仅限原论文美国样本语境**，A 股输出**不得**用作交易者身份/资金行为分类标签
- 缩量字段语义限定「**低活动/信息真空态**」，**禁止**映射「支撑成立/不会破位」
- R-A04 是**事后统计**（非事前信号），须含样本量与基线对照，且**并列输出趋势延续子模型**
- 每字段带 available / 样本量 / 口径说明
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import volume_price as vp  # noqa: E402


def _rows(n=300, *, base=10.0, vol=1_000_000, trend=0.0005):
    """构造行情行（trade_date/close/high/low/vol）；趋势 + 波动可调。"""
    out = []
    px = base
    for i in range(n):
        px *= (1 + trend + 0.01 * math.sin(i / 7.0))
        out.append({
            "trade_date": f"2025-{(i // 28) + 1:02d}-{(i % 28) + 1:02d}",
            "close": round(px, 4),
            "high": round(px * 1.01, 4),
            "low": round(px * 0.99, 4),
            "vol": vol * (1 + 0.5 * math.sin(i / 5.0)),
        })
    return out


# ── R-A03 ① 量分位 ────────────────────────────────────────────────────────

def test_volume_percentile_classifies_high_and_low():
    rows = _rows()
    rows[-1]["vol"] = 5_000_000          # 显著放量
    out = vp.volume_percentile(rows, window=250)
    assert out["available"] is True
    assert out["vol_pctile"] is not None
    assert out["state"] == "放量日"
    assert out["n"] > 0

    rows2 = _rows()
    rows2[-1]["vol"] = 1.0               # 显著缩量
    out2 = vp.volume_percentile(rows2, window=250)
    assert out2["state"] == "缩量日"


def test_low_volume_state_semantics_are_restricted():
    """缩量字段语义限定「低活动/信息真空态」——**禁止**映射「支撑成立」。"""
    rows = _rows()
    rows[-1]["vol"] = 1.0
    out = vp.volume_percentile(rows, window=250)
    assert "低活动" in out["semantics"] and "信息真空" in out["semantics"]
    assert "支撑" in out["semantics"], "须显式声明「不得映射支撑成立」"
    assert "禁止" in out["semantics"] or "不得" in out["semantics"]


# ── R-A03 ② LMSW b2 ──────────────────────────────────────────────────────

def test_lmsm_b2_returns_coef_ci_and_n():
    out = vp.lmsm_b2(_rows(400), window=120)
    assert out["available"] is True
    for k in ("b2", "ci95_low", "ci95_high", "t_stat", "n", "window"):
        assert k in out, f"缺 {k}"
    assert out["n"] > 0
    assert out["ci95_low"] <= out["b2"] <= out["ci95_high"]


def test_lmsm_b2_sign_is_statistical_only():
    """符号**仅作统计方向描述**，不得读作交易者身份。"""
    out = vp.lmsm_b2(_rows(400), window=120)
    assert "统计方向" in out["sign_note"]
    assert "身份" in out["identity_note"]
    assert "仅限" in out["identity_note"]


def test_lmsm_b2_unavailable_on_short_sample():
    out = vp.lmsm_b2(_rows(5), window=120)
    assert out["available"] is False
    assert out["b2"] is None and out["reason"]


# ── R-A03 ③ 风险态 ───────────────────────────────────────────────────────

def test_risk_state_flags_volume_break_ma():
    rows = _rows(300)
    rows[-1]["vol"] = 9_000_000
    rows[-1]["close"] = min(r["close"] for r in rows) * 0.8    # 跌破均线
    out = vp.risk_state(rows, ma=60)
    assert out["available"] is True
    assert out["is_risk_state"] is True
    assert out["components"]["high_volume"] is True
    assert out["components"]["below_ma"] is True


def test_risk_state_false_when_normal():
    out = vp.risk_state(_rows(300), ma=60)
    assert out["is_risk_state"] is False


# ── R-A03 ④ 退潮特征 ─────────────────────────────────────────────────────

def test_fade_feature_is_labelled_not_a_paper_replication():
    out = vp.fade_feature(_rows(300))
    assert "非任何论文机制的复现" in out["disclaimer"] or "非论文复现" in out["disclaimer"]
    assert "available" in out and "n" in out


def test_fade_feature_counts_volume_day_then_momentum_decay():
    rows = _rows(300)
    rows[-3]["vol"] = 9_000_000          # 放量日
    rows[-2]["close"] = rows[-3]["close"] * 0.99   # 次日下跌（动量衰减）
    out = vp.fade_feature(rows)
    assert out["available"] is True
    assert out["n_volume_days"] >= 1
    assert out["n_faded"] >= 1


# ── R-A03 ⑤ 汇总 ─────────────────────────────────────────────────────────

def test_features_summary_carries_available_n_and_caliber():
    out = vp.volume_price_features(_rows(400))
    for key in ("volume_percentile", "lmsm_b2", "risk_state", "fade"):
        sub = out[key]
        assert "available" in sub, f"{key} 缺 available"
    assert out["caliber"], "须带口径说明"
    assert out["n_rows"] == 400


def test_features_never_claim_direction():
    """观察特征：输出不得含方向/预测语义。"""
    text = str(vp.volume_price_features(_rows(400)))
    for banned in ("将上涨", "将下跌", "看多", "看空", "买入信号", "卖出信号"):
        assert banned not in text


# ── R-A04 条件性反转观测窗 ───────────────────────────────────────────────

def _bench(rows, daily=0.0002):
    return {r["trade_date"]: daily for r in rows}


def test_panic_days_requires_both_drop_and_volume():
    rows = _rows(400)
    # 造一个「大跌 + 放量」日
    i = 380
    rows[i]["close"] = rows[i - 1]["close"] * 0.90
    rows[i]["vol"] = 9_000_000
    days = vp.panic_selloff_days(rows, drop_pctile=15, vol_pctile=85)
    assert any(d["date"] == rows[i]["trade_date"] for d in days)
    for d in days:
        assert "drop_pctl" in d and "vol_pctl" in d


def test_conditional_table_has_horizons_baseline_and_n():
    rows = _rows(500)
    for i in (400, 410, 420):
        rows[i]["close"] = rows[i - 1]["close"] * 0.92
        rows[i]["vol"] = 8_000_000
    out = vp.conditional_reversal_table(rows, benchmark_returns=_bench(rows),
                                        horizons=(5, 10, 20))
    assert out["available"] is True
    for h in ("5", "10", "20"):
        row = out["horizons"][h]
        for k in ("n", "mean_excess_pct", "median_excess_pct", "win_rate_pct",
                  "baseline_excess_pct", "continuation_share_pct"):
            assert k in row, f"horizon {h} 缺 {k}"


def test_conditional_table_notes_it_is_hindsight_not_signal():
    rows = _rows(500)
    for i in (400, 410, 420):
        rows[i]["close"] = rows[i - 1]["close"] * 0.92
        rows[i]["vol"] = 8_000_000
    out = vp.conditional_reversal_table(rows, benchmark_returns=_bench(rows))
    assert "事后" in out["note"] and "非预测" in out["note"]
    assert "单点事件信号" in out["note"] or "无单点" in out["note"]


def test_conditional_table_reports_continuation_submodel():
    """须**并列输出趋势延续子模型**（Hurst 反证：两股力量并存）。"""
    rows = _rows(500)
    for i in (400, 410, 420):
        rows[i]["close"] = rows[i - 1]["close"] * 0.92
        rows[i]["vol"] = 8_000_000
    out = vp.conditional_reversal_table(rows, benchmark_returns=_bench(rows))
    assert "continuation" in out, "缺趋势延续子模型"
    assert "Hurst" in out["continuation"]["note"]


def test_conditional_table_insufficient_sample():
    out = vp.conditional_reversal_table(_rows(30), benchmark_returns={})
    assert out["available"] is False and out["reason"]
