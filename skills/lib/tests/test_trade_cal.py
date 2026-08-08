"""交易日历模块测试（C8 收敛：trade_cal 共享实现 + prev/next 交易日，#12）。"""

from __future__ import annotations

import datetime
import sys
from pathlib import Path

import pandas as pd

_SKILLS_LIB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SKILLS_LIB))  # 无条件插 0：防其他 skill 目录先行入 path 遮蔽同名模块


def _no_client(monkeypatch):
    import trade_cal as tc

    monkeypatch.setattr(tc, "env", None)
    monkeypatch.setattr(tc, "_CLIENT", None)
    return tc


class TestEstimatePath:
    """无 token（零网络）：prev/next 周末近似，与旧 etf_timeline 语义逐位一致。"""

    def test_prev_skips_weekend(self, monkeypatch):
        tc = _no_client(monkeypatch)
        assert tc.prev_trading_day(datetime.date(2026, 8, 8)) == datetime.date(2026, 8, 7)  # 周六→周五
        assert tc.prev_trading_day(datetime.date(2026, 8, 10)) == datetime.date(2026, 8, 7)  # 周一→周五
        assert tc.prev_trading_day(datetime.date(2026, 8, 7)) == datetime.date(2026, 8, 6)   # 周五→周四

    def test_next_skips_weekend(self, monkeypatch):
        tc = _no_client(monkeypatch)
        assert tc.next_trading_day(datetime.date(2026, 8, 7)) == datetime.date(2026, 8, 10)  # 周五→周一
        assert tc.next_trading_day(datetime.date(2026, 8, 9)) == datetime.date(2026, 8, 10)  # 周日→周一
        assert tc.next_trading_day(datetime.date(2026, 8, 10)) == datetime.date(2026, 8, 11)  # 周一→周二

    def test_fetch_trade_cal_estimated_flag(self, monkeypatch):
        tc = _no_client(monkeypatch)
        dates, est = tc.fetch_trade_cal("20260803", "20260807")
        assert est is True
        assert dates == ["20260803", "20260804", "20260805", "20260806", "20260807"]

    def test_last_trade_dates_weekday_only(self, monkeypatch):
        tc = _no_client(monkeypatch)
        out = tc.last_trade_dates(3)
        assert len(out) == 3
        assert all(datetime.datetime.strptime(d, "%Y%m%d").weekday() < 5 for d in out)
        assert out == sorted(out, reverse=True)


class _FakeClient:
    """Fake TushareClient：trade_cal 返回固定日历（含调休日 2026-05-09 周六上班）。"""

    def query(self, api, **params):
        assert api == "trade_cal"
        cal = [
            "20260507", "20260508", "20260509",  # 周六调休上班
            "20260511", "20260512", "20260513",
        ]
        in_range = [c for c in cal
                    if params["start_date"] <= c <= params["end_date"]]
        return pd.DataFrame({"cal_date": in_range})


class TestRealCalendarPath:
    """有 token：SSE 真实日历——调休日（周六上班）为交易日（周末近似会错判）。"""

    def _with_client(self, monkeypatch):
        import trade_cal as tc

        monkeypatch.setattr(tc, "_CLIENT", _FakeClient())
        return tc

    def test_prev_uses_real_calendar_makeup_day(self, monkeypatch):
        tc = self._with_client(monkeypatch)
        # 周一 2026-05-11 的前一交易日：真实日历为周六调休 2026-05-09
        assert tc.prev_trading_day(datetime.date(2026, 5, 11)) == datetime.date(2026, 5, 9)

    def test_next_uses_real_calendar_makeup_day(self, monkeypatch):
        tc = self._with_client(monkeypatch)
        # 周五 2026-05-08 的后一交易日：调休日 05-09（周末近似会错判为 05-11）
        assert tc.next_trading_day(datetime.date(2026, 5, 8)) == datetime.date(2026, 5, 9)

    def test_fetch_trade_cal_uses_client(self, monkeypatch):
        tc = self._with_client(monkeypatch)
        dates, est = tc.fetch_trade_cal("20260508", "20260512")
        assert est is False
        assert dates == ["20260508", "20260509", "20260511", "20260512"]
