"""Tests for lib.catalyst — forward-looking event calendar."""

from __future__ import annotations

from datetime import date

import pytest


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
