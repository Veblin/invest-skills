"""v0.2.8 W1: snapshot() 数据新鲜度审计字段（date=实际交易日 + collected_at/data_note）。

2026-09-02 误报事故根因：开盘前采集的盘面字段（涨停池/涨跌比）实为上一交易日
收盘数据，却被按日历日标为当日（83→49 涨停）。本测试锁定修复后行为：
date 取 shanghai_session_date()；开盘前场景 data_note 说明口径。
"""

from __future__ import annotations

from market_microstructure import snapshot


def _silence_fetchers(monkeypatch) -> None:
    """把 snapshot() 的全部网络采集与落库替换为 no-op（纯字段逻辑测试）。"""
    import market_microstructure as mm

    for name in (
        "_fetch_margin", "_fetch_ad_ratio", "_fetch_limit_pools",
        "_fetch_turnover", "_fetch_erp", "_fetch_pcr",
        "_fetch_below_book_pct", "_fetch_northbound", "_fetch_futures",
    ):
        monkeypatch.setattr(mm, name, lambda result: None)
    monkeypatch.setattr(mm, "_compute_labels", lambda result: None)
    monkeypatch.setattr(mm, "_auto_persist", lambda snap: None)


def test_snapshot_has_collected_at_and_no_data_note_in_session(
        monkeypatch):
    """盘中快照：date=会话日、collected_at 存在、data_note 为 None。"""
    import market_microstructure as mm

    _silence_fetchers(monkeypatch)
    monkeypatch.setattr(mm, "shanghai_session_date", lambda: "20260902")
    monkeypatch.setattr(mm, "shanghai_today", lambda: "20260902")
    monkeypatch.setattr(mm, "shanghai_now", lambda: _now("2026-09-02T10:00:00"))
    snap = snapshot()
    assert snap["date"] == "20260902"
    assert snap["collected_at"].startswith("2026-09-02")
    assert snap.get("data_note") is None


def test_snapshot_pre_open_rolls_back_date_with_data_note(monkeypatch):
    """开盘前快照：date 回拨上一交易日 + data_note 说明盘面字段口径。"""
    import market_microstructure as mm

    _silence_fetchers(monkeypatch)
    monkeypatch.setattr(mm, "shanghai_session_date", lambda: "20260901")
    monkeypatch.setattr(mm, "shanghai_today", lambda: "20260902")
    monkeypatch.setattr(mm, "shanghai_now", lambda: _now("2026-09-02T08:01:00"))
    snap = snapshot()
    assert snap["date"] == "20260901"
    assert snap["collected_at"].startswith("2026-09-02")
    note = snap.get("data_note") or ""
    assert "20260901" in note and "盘面" in note


def _now(iso: str):
    import datetime as _dt

    return _dt.datetime.fromisoformat(iso).replace(
        tzinfo=_dt.timezone(_dt.timedelta(hours=8)))
