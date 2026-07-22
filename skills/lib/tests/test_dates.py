"""Tests for skills/lib/dates.py — pure helpers, no network."""

from __future__ import annotations

import sys
from pathlib import Path

_SKILLS_LIB = Path(__file__).resolve().parents[1]
if str(_SKILLS_LIB) not in sys.path:
    sys.path.insert(0, str(_SKILLS_LIB))

from dates import yyyymmdd_to_iso  # noqa: E402


def test_yyyymmdd_to_iso_basic():
    assert yyyymmdd_to_iso("20260713") == "2026-07-13"
    assert yyyymmdd_to_iso("20260101") == "2026-01-01"


def test_yyyymmdd_to_iso_passthrough():
    assert yyyymmdd_to_iso("abc") == "abc"
    assert yyyymmdd_to_iso("abcdefgh") == "abcdefgh"
    assert yyyymmdd_to_iso("2026-07-13") == "2026-07-13"


def test_yyyymmdd_to_iso_strips_whitespace():
    assert yyyymmdd_to_iso(" 20260101 ") == "2026-01-01"
