"""Tests for lib.catalyst — forward-looking event calendar."""

from __future__ import annotations

from datetime import date



class TestParseDate:
    def test_date_object(self):
        from lib.catalyst import _parse_date
        assert _parse_date(date(2026, 8, 15)) == date(2026, 8, 15)

    def test_datetime_object(self):
        from datetime import datetime
        from lib.catalyst import _parse_date
        assert _parse_date(datetime(2026, 8, 15, 10, 30)) == date(2026, 8, 15)

    def test_string_iso(self):
        from lib.catalyst import _parse_date
        assert _parse_date("2026-08-15") == date(2026, 8, 15)

    def test_string_chinese(self):
        from lib.catalyst import _parse_date
        assert _parse_date("2026年8月15日") == date(2026, 8, 15)

    def test_none(self):
        from lib.catalyst import _parse_date
        assert _parse_date(None) is None

    def test_empty_string(self):
        from lib.catalyst import _parse_date
        assert _parse_date("") is None

    def test_pandas_nat(self):
        import pandas as pd
        from lib.catalyst import _parse_date
        assert _parse_date(pd.NaT) is None


class TestCatalystEvent:
    def test_label(self):
        from lib.catalyst import CatalystEvent
        e = CatalystEvent("000001", date(2026, 8, 1), "dividend", "test")
        assert "分红" in e.label()

    def test_restricted_unlock_impact(self):
        from lib.catalyst import CatalystEvent
        # restricted_unlock events are created with impact="高" in the fetcher
        e = CatalystEvent("000001", date(2026, 8, 1), "restricted_unlock", "test", impact="高")
        assert e.impact == "高"


class TestFormatCalendar:
    def test_empty_events(self):
        from lib.catalyst import format_catalyst_calendar
        output = format_catalyst_calendar([], "000001")
        assert "未发现已知催化剂" in output

    def test_with_events(self):
        from lib.catalyst import CatalystEvent, format_catalyst_calendar
        events = [
            CatalystEvent("600176", date(2028, 6, 29), "restricted_unlock",
                          "限售解禁 0.10 亿股", "7 个股东, 股权激励限售股份",
                          impact="高", source="test"),
            CatalystEvent("600176", date(2026, 8, 15), "dividend",
                          "10派5元", "方案: 10派5元",
                          impact="中", source="test"),
        ]
        output = format_catalyst_calendar(events, "600176")
        assert "06-29" in output
        assert "08-15" in output
        assert "限售解禁" in output
        assert "10派5元" in output
        assert "🔓" in output
        assert "📊" in output


class TestRestrictedUnlockFetch:
    """限售解禁采集：上海时区窗口 + NaN 股东数容错。"""

    @staticmethod
    def _patch_env(monkeypatch) -> None:
        from contextlib import nullcontext
        import lib.env
        import lib.collector
        monkeypatch.setattr(lib.env, "is_akshare_available", lambda: True)
        monkeypatch.setattr(lib.collector, "akshare_direct_session", nullcontext)

    def test_window_uses_shanghai_dates_not_host_local(self, monkeypatch):
        from datetime import date, datetime, timedelta
        import pandas as pd
        from lib.catalyst import _fetch_restricted_unlock_events

        self._patch_env(monkeypatch)
        host_today = date.today()
        fake_shanghai = datetime.now() + timedelta(days=1)  # 上海已比 host 早一天
        monkeypatch.setattr("lib.catalyst._shanghai_now", lambda: fake_shanghai)

        rows = pd.DataFrame([
            # host 视角"今天" = 上海视角"昨天" → 落在上海窗口之下 → 丢弃
            {"解禁时间": host_today.isoformat(), "解禁数量": 1e8,
             "解禁股东数": 3, "限售股类型": "首发"},
            # 上海视角 30 日后 = host 视角 31 日后 → host 口径会误丢，上海口径保留
            {"解禁时间": (host_today + timedelta(days=31)).isoformat(), "解禁数量": 2e8,
             "解禁股东数": 5, "限售股类型": "定向"},
        ])
        monkeypatch.setattr("akshare.stock_restricted_release_queue_em",
                            lambda symbol: rows)

        events = _fetch_restricted_unlock_events("000001", lookahead_days=30)
        assert [e.date for e in events] == [host_today + timedelta(days=31)]

    def test_nan_holder_count_does_not_drop_batch(self, monkeypatch):
        from datetime import datetime, timedelta
        import pandas as pd
        from lib.catalyst import _fetch_restricted_unlock_events

        self._patch_env(monkeypatch)
        fake_shanghai = datetime.now() + timedelta(days=1)
        monkeypatch.setattr("lib.catalyst._shanghai_now", lambda: fake_shanghai)
        within = fake_shanghai.date() + timedelta(days=5)

        rows = pd.DataFrame([
            # 解禁股东数为 NaN → 旧实现 int(NaN) 抛 ValueError 整批丢失
            {"解禁时间": within.isoformat(), "解禁数量": 1e8,
             "解禁股东数": float("nan"), "限售股类型": "首发"},
            {"解禁时间": (within + timedelta(days=1)).isoformat(), "解禁数量": 2e8,
             "解禁股东数": 7, "限售股类型": "定向"},
        ])
        monkeypatch.setattr("akshare.stock_restricted_release_queue_em",
                            lambda symbol: rows)

        events = _fetch_restricted_unlock_events("000001", lookahead_days=30)
        assert len(events) == 2  # 单条 NaN 不整批丢失
        assert len([e for e in events if e.detail.startswith("股东数不可得")]) == 1
        assert len([e for e in events if "7 个股东" in e.detail]) == 1
