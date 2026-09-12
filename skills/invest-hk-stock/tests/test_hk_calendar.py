"""港股交易日历变体测试（T11-2）——纯离线：注入日历集，不联网。

为什么需要独立变体：港股与 A 股假日不同（中秋翌日、国庆、佛诞、耶稣受难等），
复用 A 股日历（`dates._trade_days`）会给出**错误的最近交易日与交易日数**
——报告里的「距上次数据 N 个交易日」会系统性偏小/偏大。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts" / "lib"))

import hk_calendar as hkc  # noqa: E402

# 构造能体现两市场差异的窗口（2026-09-28 ~ 2026-10-08）：
# 港股 —— 09-28/29 开市，**09-30 休（中秋翌日）**，10-01 休（国庆），10-02 开，10-05~08 开。
# 对照：A 股 09-30 是开市日（节前最后交易日），故两者在此窗内必然不同。
_HK_DAYS = ["20260928", "20260929", "20261002", "20261005", "20261006", "20261007", "20261008"]


def _inject(monkeypatch, days, calls=None):
    def fake():
        if calls is not None:
            calls.append(1)
        return days

    monkeypatch.setattr(hkc, "_fetch_from_tushare", fake)
    hkc._reset_cache()


# ── 交易日集本身 ─────────────────────────────────────────────────────────

def test_session_date_uses_hk_calendar_not_a_share(monkeypatch):
    """09-30 港股休市（A 股开市）→ 最近交易日须是 09-29，而非 09-30。"""
    _inject(monkeypatch, _HK_DAYS)
    assert hkc.hk_session_date("20260930") == "20260929"
    assert hkc.hk_session_date("20261002") == "20261002"


def test_session_date_defaults_to_beijing_today(monkeypatch):
    """不传 today 时用**北京日期**（不得用宿主本地日期——仓库已知的时间炸弹类缺陷）。

    判别方式：把北京日期钉成一个**与宿主今天不同**的日期，故若代码取宿主日期，
    就会落到日历覆盖之外 → 走周末近似 → 断言失败。
    """
    import dates

    monkeypatch.setattr(dates, "shanghai_today", lambda: "20261225")
    _inject(monkeypatch, ["20261220", "20261224"])
    assert hkc.hk_session_date() == "20261224", "须按北京日期取 ≤ today 的最近交易日"


def test_trading_day_lag_counts_hk_days_only(monkeypatch):
    """(as_of, session] 内只数**港股**交易日：09-28→10-05 应为 3（09-29/10-02/10-05）。"""
    _inject(monkeypatch, _HK_DAYS)
    assert hkc.hk_trading_day_lag("20260928", "20261005") == (3, False)


def test_lag_unparseable_input_returns_none(monkeypatch):
    _inject(monkeypatch, _HK_DAYS)
    assert hkc.hk_trading_day_lag("bad-date", "20261005") == (None, True)


# ── 降级链（日历不可用 ≠ 没有交易日）────────────────────────────────────

def test_fetch_failure_degrades_to_weekday_approx(monkeypatch):
    _inject(monkeypatch, None)
    assert hkc.hk_session_date("20260926") == "20260925"   # 周六 → 回退到周五
    assert hkc.hk_session_date("20260920") == "20260918"   # 周日 → 回退到周五
    assert hkc.hk_session_date("20260924") == "20260924"   # 周四（周中）→ 原样
    assert hkc.hk_calendar_degraded() is True


def test_empty_calendar_is_degraded_not_no_trading_days(monkeypatch):
    """空日历是「不可得」，**不是**「港股没有交易日」——不得静默当成全休市。"""
    _inject(monkeypatch, [])
    assert hkc.hk_trade_days() is None
    assert hkc.hk_calendar_degraded() is True


def test_lag_without_calendar_falls_back_to_natural_days(monkeypatch):
    _inject(monkeypatch, None)
    n, degraded = hkc.hk_trading_day_lag("20260928", "20261005")
    assert (n, degraded) == (7, True), "日历不可用时须退回自然日差并标记 degraded"


# ── 缓存 ────────────────────────────────────────────────────────────────

def test_calendar_cached_across_calls(monkeypatch):
    calls = []
    _inject(monkeypatch, _HK_DAYS, calls=calls)
    assert hkc.hk_trade_days() == _HK_DAYS
    hkc.hk_session_date("20261002")
    assert len(calls) == 1, f"TTL 内应复用缓存，实际取数 {len(calls)} 次"
