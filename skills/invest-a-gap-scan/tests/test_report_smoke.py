"""Offline smoke: report_formatter / kline_cache pure helpers + scan --help."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pandas as pd

import kline_cache
from report_formatter import _fmt_amount, _fmt_pct, _fmt_price, _parse_universe_indices

_SCAN_PY = Path(__file__).resolve().parent.parent / "scripts" / "scan.py"


def test_fmt_helpers():
    assert _fmt_amount(2.5e8) == "2.50亿"
    assert _fmt_amount(5e4) == "5万"
    assert _fmt_pct(1.25) == "+1.25%"
    assert _fmt_pct(-0.5) == "-0.50%"
    assert _fmt_pct(float("nan")) == "N/A"
    assert _fmt_price(12.345) == "12.345"


def test_parse_universe_indices_default():
    labels = _parse_universe_indices({})
    assert ("沪深300", 300) in labels
    assert ("中证A500", 500) in labels


def test_kline_cache_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(kline_cache.env, "STORE_DIR", tmp_path)
    df = pd.DataFrame({"close": [1.0, 2.0]})
    kline_cache.save("000001.SZ", df, date_str="20260722", source_name="test")
    loaded = kline_cache.load("000001.SZ", date_str="20260722", source_name="test")
    assert loaded is not None
    assert list(loaded["close"]) == [1.0, 2.0]


def test_scan_cli_help_exit_0():
    r = subprocess.run(
        [sys.executable, str(_SCAN_PY), "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert r.returncode == 0
    assert "gap" in r.stdout.lower() or "universe" in r.stdout.lower()
