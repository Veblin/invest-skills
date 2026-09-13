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
    """样本不足 → 理由须点名「**样本**」。

    ⚠️ 该用例原先传 `benchmark_returns={}`，在新增基准覆盖闸门后**恒因「基准不可用」
    而通过**——与用例本意（样本不足）脱节。故改为传**完整基准**，直取 `min_events` 分支。
    （`_rows(30)` 实测 0 个杀跌日，正是样本不足形态。）
    """
    rows = _rows(30)
    out = vp.conditional_reversal_table(rows, benchmark_returns=_bench(rows))
    assert out["available"] is False
    assert "样本" in out["reason"], f"须归因到样本不足，实得：{out['reason']}"


def test_conditional_table_empty_benchmark_reason_names_benchmark():
    """空基准 → 理由须点名「**基准**」（与样本不足区分开）。"""
    out = vp.conditional_reversal_table(_rows(500), benchmark_returns={})
    assert out["available"] is False
    assert "基准" in out["reason"], out["reason"]


def test_conditional_table_refuses_when_baseline_dates_uncovered():
    """⚠️ 覆盖校验须**含无条件基线**所需日期，不能只核事件窗口。

    基线遍历全样本前向窗（`volume_price.py` 基线循环），同样用
    `benchmark_returns.get(d, 0.0)` 填 0。只核事件窗口时：基准仅覆盖事件后日期
    → 事件窗无缺口（闸门放行）→ 基线相对全量基准系统性偏移却照发。
    """
    rows = _rows(500)
    for i in (400, 410, 420):
        rows[i]["close"] = rows[i - 1]["close"] * 0.92
        rows[i]["vol"] = 8_000_000
    # 基准**恰好覆盖全部事件窗口**（老闸门的 need），其余日期全缺 → 老实现放行、
    # 基线大面积填 0；新实现须拒绝。（夹具由公开 API 反推事件窗口，非循环论证）
    dates = [r["trade_date"] for r in rows]
    idx = {d: i for i, d in enumerate(dates)}
    covered: set[str] = set()
    for ev in vp.panic_selloff_days(rows):
        i = idx[ev["date"]]
        covered.update(dates[i + 1: i + 21])
    assert covered, "夹具须至少含一个事件窗"
    out = vp.conditional_reversal_table(
        rows, benchmark_returns={d: 0.0002 for d in covered})
    assert out["available"] is False, "基线日期大面积缺基准时不得发布统计"
    assert "覆盖缺口" in out["reason"] and out["benchmark_missing"]


def test_conditional_table_tolerates_small_benchmark_gap():
    """少量缺口（≤10%）不阻断——否则交易日历轻微不齐就永不发布。"""
    rows = _rows(500)
    for i in (400, 410, 420):
        rows[i]["close"] = rows[i - 1]["close"] * 0.92
        rows[i]["vol"] = 8_000_000
    bench = _bench(rows)
    for r in rows[:3]:
        bench.pop(r["trade_date"])
    out = vp.conditional_reversal_table(rows, benchmark_returns=bench)
    assert out["available"] is True
    assert out["benchmark_gap_pct"] <= 10.0


def test_conditional_table_matches_events_by_index_without_trade_date():
    """行缺 `trade_date` 时，事件仍须计入统计（按**下标**匹配）。

    ⚠️ 原实现两处键不一致：事件侧 `str(rows[i].get("trade_date") or i)`（**退回整数下标**），
    统计侧 `str(r.get("trade_date") or "")` —— 键永不匹配 → 所有事件被静默丢弃，
    而输出仍是 `available=True` + 覆盖缺口 0%（覆盖闸门把 "" 从 need 里滤掉，
    等于宣称「基准完整」却发布空事件统计，`min_events` 也拦不住——它在窗口循环之前）。
    """
    rows = _rows(500)
    for i in (400, 410, 420):
        rows[i]["close"] = rows[i - 1]["close"] * 0.92
        rows[i]["vol"] = 8_000_000
    for r in rows:
        r.pop("trade_date", None)          # 行本身没有日期
    out = vp.conditional_reversal_table(rows, benchmark_returns={"": 0.0002})
    assert out["n_events"] > 0, "夹具须至少检出杀跌日"
    assert out["horizons"]["20"]["n"] > 0, \
        "事件未被计入任何期限（键错配导致静默丢弃）"
    assert out["horizons"]["20"]["mean_excess_pct"] is not None


def test_conditional_table_rejects_nonfinite_benchmark_values():
    """基准值须是**有限数**：NaN/Inf/None 与缺失同视。

    ⚠️ 只查「键存在」会让 NaN 通过：NaN 参与求和把整条统计污染成 NaN，
    且 `ex < 0` / `e > 0` 对 NaN 恒 False → 胜率被报成**硬 0%**（把「未知」说成
    「0% 胜率」）；传 `None` 还会在 `sum()` 里直接 TypeError。
    """
    rows = _rows(500)
    for i in (400, 410, 420):
        rows[i]["close"] = rows[i - 1]["close"] * 0.92
        rows[i]["vol"] = 8_000_000
    finite = _bench(rows)
    for bad in (float("nan"), float("inf"), None):
        out = vp.conditional_reversal_table(
            rows, benchmark_returns={d: bad for d in finite})
        assert out["available"] is False, f"{bad!r} 基准不得发布统计"
        assert "有限数" in out["reason"] or "覆盖缺口" in out["reason"]
    # 混合：仅个别日期非有限 → 计入缺口，缺口小则照发且不含 NaN
    mixed = dict(finite)
    for d in list(mixed)[:5]:
        mixed[d] = float("nan")
    out2 = vp.conditional_reversal_table(rows, benchmark_returns=mixed)
    assert out2["available"] is True
    for h in ("5", "10", "20"):
        row = out2["horizons"][h]
        assert all(v is None or v == v for v in row.values()), f"horizon {h} 含 NaN"
