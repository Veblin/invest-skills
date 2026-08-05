"""Tests for skills/lib/dates.py — pure helpers, no network."""

from __future__ import annotations

import sys
from pathlib import Path

_SKILLS_LIB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SKILLS_LIB))  # 无条件插 0：防其他 skill 目录先行入 path 遮蔽同名模块

from dates import (  # noqa: E402
    normalize_end_date,
    shanghai_days_ago,
    shanghai_today,
    yyyymmdd_to_iso,
)


def test_yyyymmdd_to_iso_basic():
    assert yyyymmdd_to_iso("20260713") == "2026-07-13"
    assert yyyymmdd_to_iso("20260101") == "2026-01-01"


def test_yyyymmdd_to_iso_passthrough():
    assert yyyymmdd_to_iso("abc") == "abc"
    assert yyyymmdd_to_iso("abcdefgh") == "abcdefgh"
    assert yyyymmdd_to_iso("2026-07-13") == "2026-07-13"


def test_yyyymmdd_to_iso_strips_whitespace():
    assert yyyymmdd_to_iso(" 20260101 ") == "2026-01-01"


def test_shanghai_today_format():
    today = shanghai_today()
    assert len(today) == 8
    assert today.isdigit()


def test_shanghai_days_ago_zero_matches_today():
    assert shanghai_days_ago(0) == shanghai_today()


def test_shanghai_days_ago_is_earlier():
    assert shanghai_days_ago(3) < shanghai_today()


class TestNormalizeEndDate:
    def test_already_yyyymmdd(self):
        assert normalize_end_date("20251231") == "20251231"

    def test_dash_format(self):
        assert normalize_end_date("2025-12-31") == "20251231"

    def test_dot_format(self):
        assert normalize_end_date("2025.7.1") == "20250701"

    def test_interval_format(self):
        assert normalize_end_date("2015.07.23-2015.07.23") == "20150723"

    def test_digit_prefix_fallback(self):
        assert normalize_end_date("20260101abc") == "20260101"

    def test_unparseable_returns_empty(self):
        assert normalize_end_date("无日期") == ""
        assert normalize_end_date("") == ""
