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

    tc._clear_trade_cal_cache()  # 隔离模块级日历缓存（code-review #3 memoization）
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

        tc._clear_trade_cal_cache()  # 隔离模块级日历缓存（code-review #3 memoization）
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


class TestTradeCalCache:
    """code-review #3：日历缓存——重叠窗口二次查询零网络请求（R11b 事件对齐场景）。"""

    def test_overlapping_windows_serve_from_cache(self, monkeypatch):
        import trade_cal as tc

        calls: list[str] = []

        class _CountingClient:
            def query(self, api, **params):
                calls.append(f"{params['start_date']}~{params['end_date']}")
                cal = ["20260701", "20260702", "20260703", "20260704",
                       "20260706", "20260707", "20260708", "20260709",
                       "20260710"]
                in_range = [c for c in cal
                            if params["start_date"] <= c <= params["end_date"]]
                return pd.DataFrame({"cal_date": in_range})

        tc._clear_trade_cal_cache()
        monkeypatch.setattr(tc, "_CLIENT", _CountingClient())
        # 第一次：请求范围月度对齐 → 实际请求 [20260601, 20260731] 并缓存
        d1, _ = tc.fetch_trade_cal("20260601", "20260710")
        assert d1[0] == "20260701" and d1[-1] == "20260710"
        # 第二次：完全落在缓存段内 → 零新请求
        d2, _ = tc.fetch_trade_cal("20260702", "20260709")
        assert d2 == ["20260702", "20260703", "20260704",
                      "20260706", "20260707", "20260708", "20260709"]
        # 第三次：同月内 end 漂移 → 缓存命中（月度对齐吞掉日级扩展）
        d3, _ = tc.fetch_trade_cal("20260708", "20260720")
        assert d3 == ["20260708", "20260709", "20260710"]
        assert len(calls) == 1  # 对齐后第三次不再触发扩展
        # 第四次：跨月（end 进入 8 月）→ 扩展请求一次，合并后全量命中
        d4, _ = tc.fetch_trade_cal("20260725", "20260810")
        assert d4 == []
        assert len(calls) == 2
        # 第五次：新扩展范围内命中 → 零请求
        d5, _ = tc.fetch_trade_cal("20260801", "20260805")
        assert d5 == []
        assert len(calls) == 2

    def test_prev_next_share_cached_calendar(self, monkeypatch):
        """事件对齐场景：N 个日期的 prev/next 只在首次各请求一次。"""
        import trade_cal as tc

        calls: list[str] = []

        class _CountingClient:
            def query(self, api, **params):
                calls.append(f"{params['start_date']}~{params['end_date']}")
                cal = ["20260701", "20260702", "20260703",
                       "20260706", "20260707", "20260708",
                       "20260709", "20260710", "20260713"]
                in_range = [c for c in cal
                            if params["start_date"] <= c <= params["end_date"]]
                return pd.DataFrame({"cal_date": in_range})

        tc._clear_trade_cal_cache()
        monkeypatch.setattr(tc, "_CLIENT", _CountingClient())
        for d in (datetime.date(2026, 7, 6), datetime.date(2026, 7, 7),
                  datetime.date(2026, 7, 8)):
            _ = tc.prev_trading_day(d)
            _ = tc.next_trading_day(d)
        # 3 个日期 × prev/next：缓存扩展后仅 2 次请求（首 prev + 首 next 范围
        # 合并），后续 4 次调用全部命中——修复前为 6 次顺序请求
        assert len(calls) == 2
