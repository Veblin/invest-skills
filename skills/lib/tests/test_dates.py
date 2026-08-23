"""Tests for skills/lib/dates.py — pure helpers, no network."""

from __future__ import annotations

import sys
from pathlib import Path

_SKILLS_LIB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SKILLS_LIB))  # 无条件插 0：防其他 skill 目录先行入 path 遮蔽同名模块

from dates import (  # noqa: E402
    fmt_fetched_at,
    normalize_end_date,
    parse_utc_iso,
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


# --- v0.2.7 P2-2: parse_utc_iso / fmt_fetched_at（UTC → 北京时间渲染） ---

def test_parse_utc_iso_plus_offset():
    dt = parse_utc_iso("2026-08-22T17:10:41+00:00")
    assert dt is not None
    assert dt.isoformat().startswith("2026-08-22T17:10:41")
    assert dt.utcoffset() is not None


def test_parse_utc_iso_z_suffix():
    dt = parse_utc_iso("2026-08-22T17:10:41Z")
    assert dt is not None
    assert dt.isoformat().startswith("2026-08-22T17:10:41")


def test_parse_utc_iso_naive_assumed_utc():
    """naive 按 UTC 假定（存量数据全由 _assemble_result 以 UTC 生成）。"""
    dt = parse_utc_iso("2026-08-22T17:10:41")
    assert dt is not None
    assert dt.isoformat().startswith("2026-08-22T17:10:41+00:00")


def test_parse_utc_iso_invalid():
    assert parse_utc_iso(None) is None
    assert parse_utc_iso("") is None
    assert parse_utc_iso("not-a-date") is None
    assert parse_utc_iso(12345) is None


def test_fmt_fetched_at_shanghai_cross_day():
    """UTC 2026-08-22 17:10 → 北京 2026-08-23 01:10（跨日 +8h）。"""
    assert fmt_fetched_at("2026-08-22T17:10:41+00:00") == "2026-08-23 01:10 (北京时间)"
    assert fmt_fetched_at("2026-08-22T17:10:41Z") == "2026-08-23 01:10 (北京时间)"
    assert fmt_fetched_at("2026-08-22T17:10:41") == "2026-08-23 01:10 (北京时间)"


def test_fmt_fetched_at_unparseable_fallback():
    """解析失败回退原串截 16 字符。"""
    assert fmt_fetched_at("") == ""
    assert fmt_fetched_at(None) == ""
