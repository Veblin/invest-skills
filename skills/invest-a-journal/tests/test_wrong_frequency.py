"""R-C01 错频计数 — 离线单测（store 聚合 + 个人自证数据）。

纪律（R-C01 规格）：
- 计数一律 Python（`actual_result < 0` **严格小于**）；`None` 行**剔除不计**
  （既不当盈利也不当亏损——不伪造结果）
- 阈值默认 2-3 **可配置**，标注「纪律惯例（直播建议值），非实证阈值」
- 触发动作 = **强制结构化复盘**，不是倒计时、**不得**出现「必须停止交易」类指令
- 自证数据用**用户自己的**历史：「亏损后立即再交易 N 次、其中 M 次续亏」
"""
from __future__ import annotations

import pytest

import db


def _row(result, *, date="2026-01-01", symbol="600176"):
    return {"actual_result": result, "entry_date": date, "created_at": f"{date} 09:00:00",
            "symbol": symbol}


# ── consecutive_loss_streak ──────────────────────────────────────────────

def test_streak_counts_trailing_losses():
    rows = [_row(5.0, date="2026-01-01"), _row(-2.0, date="2026-01-02"),
            _row(-3.0, date="2026-01-03")]
    out = db.consecutive_loss_streak(rows=rows)
    assert out["streak"] == 2
    assert out["last_loss_at"] == "2026-01-03"


def test_streak_zero_when_last_trade_is_profit():
    rows = [_row(-1.0, date="2026-01-01"), _row(4.0, date="2026-01-02")]
    assert db.consecutive_loss_streak(rows=rows)["streak"] == 0


def test_zero_result_is_not_a_loss():
    """0 是合法结果（保本）——**严格小于 0** 才算亏损（D1：0 不得被 falsy 吞掉）。"""
    rows = [_row(-1.0, date="2026-01-01"), _row(0.0, date="2026-01-02")]
    assert db.consecutive_loss_streak(rows=rows)["streak"] == 0


def test_none_result_is_excluded_not_counted_as_zero():
    """None（未平仓/未记录结果）剔除不计——不当作 0（那会伪造一个「保本」结果）。"""
    rows = [_row(-1.0, date="2026-01-01"), _row(None, date="2026-01-02"),
            _row(-2.0, date="2026-01-03")]
    out = db.consecutive_loss_streak(rows=rows)
    assert out["streak"] == 2, "None 行不打断连续亏损（它不构成一个结果）"
    assert out["window_n"] == 2


def test_threshold_is_configurable_and_labelled_as_convention():
    rows = [_row(-1.0, date="2026-01-01"), _row(-1.0, date="2026-01-02")]
    two = db.consecutive_loss_streak(rows=rows, threshold=2)
    assert two["triggered"] is True and two["threshold"] == 2
    three = db.consecutive_loss_streak(rows=rows, threshold=3)
    assert three["triggered"] is False and three["threshold"] == 3
    assert "非实证阈值" in two["threshold_label"], "阈值须标注为纪律惯例"
    assert "惯例" in two["threshold_label"]


def test_streak_orders_by_time_not_by_input_order():
    rows = [_row(-9.0, date="2026-03-01"), _row(-1.0, date="2026-01-01"),
            _row(-1.0, date="2026-02-01")]
    assert db.consecutive_loss_streak(rows=rows)["streak"] == 3


def test_streak_handles_empty_store():
    out = db.consecutive_loss_streak(rows=[])
    assert out["streak"] == 0 and out["triggered"] is False and out["window_n"] == 0


# ── loss_after_loss_evidence（个人自证数据）───────────────────────────────

def test_evidence_counts_continuation_after_loss():
    rows = [
        _row(-1.0, date="2026-01-01"),
        _row(-2.0, date="2026-01-02"),   # 亏损后立即再交易 → 续亏
        _row(-1.0, date="2026-01-03"),
        _row(3.0, date="2026-01-04"),    # 亏损后立即再交易 → 未续亏
        _row(-1.0, date="2026-01-05"),   # 最后一笔之后没有交易 → 不计入分母
    ]
    out = db.loss_after_loss_evidence(rows=rows)
    assert out["n_after_loss"] == 3, "分母 = 亏损后仍有下一笔的次数"
    assert out["n_continued_loss"] == 2
    assert out["rate_pct"] == pytest.approx(66.7, abs=0.1)
    assert out["available"] is True


def test_evidence_unavailable_when_no_samples():
    """样本为 0 → available=False，**不得**拿 0/0 造一个比率。"""
    out = db.loss_after_loss_evidence(rows=[_row(1.0, date="2026-01-01")])
    assert out["n_after_loss"] == 0
    assert out["rate_pct"] is None
    assert out["available"] is False
    assert out["note"]


def test_evidence_median_gap_days():
    rows = [_row(-1.0, date="2026-01-01"), _row(-1.0, date="2026-01-03"),
            _row(-1.0, date="2026-01-04"), _row(-1.0, date="2026-01-05")]
    out = db.loss_after_loss_evidence(rows=rows)
    assert out["median_gap_days"] == pytest.approx(1.0)


def test_evidence_ignores_none_results():
    rows = [_row(-1.0, date="2026-01-01"), _row(None, date="2026-01-02"),
            _row(-1.0, date="2026-01-03")]
    out = db.loss_after_loss_evidence(rows=rows)
    assert out["n_after_loss"] == 1, "None 行不构成「再交易」"


# ── 输出文案（合规硬约束）────────────────────────────────────────────────

def test_wrong_frequency_report_has_self_evidence_and_formula_label():
    rows = [_row(-1.0, date="2026-01-01"), _row(-2.0, date="2026-01-02"),
            _row(-1.0, date="2026-01-03")]
    text = db.render_wrong_frequency(rows=rows)
    assert "错频" in text
    assert "Python calc" in text, "P0：自证比率须带公式标签"
    assert "非实证阈值" in text


@pytest.mark.parametrize("banned", ["必须停止", "禁止交易", "停止交易", "不得再交易"])
def test_no_directive_language(banned):
    """冷却 = 启动结构化复盘流程，**不是**禁止交易的指令。"""
    rows = [_row(-1.0, date="2026-01-01"), _row(-2.0, date="2026-01-02"),
            _row(-1.0, date="2026-01-03")]
    text = db.render_wrong_frequency(rows=rows)
    assert banned not in text


def test_below_threshold_renders_nothing_alarming():
    rows = [_row(2.0, date="2026-01-01")]
    text = db.render_wrong_frequency(rows=rows)
    assert "错频提示" not in text
