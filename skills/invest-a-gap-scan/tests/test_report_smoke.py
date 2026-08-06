"""Offline smoke: report_formatter / kline_cache pure helpers + scan --help."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pandas as pd

from _invest_path import ensure_invest_a_scripts_on_path

ensure_invest_a_scripts_on_path()

from kline_cache import KlineTTLCache  # noqa: E402（canonical: skills/lib）
from report_formatter import _fmt_amount, _fmt_pct, _fmt_price, _parse_universe_indices  # noqa: E402

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


def test_kline_cache_roundtrip(tmp_path):
    """canonical KlineTTLCache 键布局兼容旧 gap 布局：{root}/{date}/{source}/{code}.pkl。"""
    cache = KlineTTLCache(lambda: tmp_path / "gap_scan_cache", 3 * 86400)
    df = pd.DataFrame({"close": [1.0, 2.0]})
    cache.save("20260722", ("test", "000001.SZ"), df)
    loaded = cache.load("20260722", ("test", "000001.SZ"))
    assert loaded is not None
    assert list(loaded["close"]) == [1.0, 2.0]
    assert (tmp_path / "gap_scan_cache" / "20260722" / "test" / "000001.SZ.pkl").is_file()


def test_scan_cli_help_exit_0():
    r = subprocess.run(
        [sys.executable, str(_SCAN_PY), "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert r.returncode == 0
    assert "gap" in r.stdout.lower() or "universe" in r.stdout.lower()


def _load_scan_module():
    name = "gap_scan_scan_under_test"
    sys.modules.pop(name, None)
    spec = importlib.util.spec_from_file_location(name, str(_SCAN_PY))
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_report_path_has_seconds_timestamp():
    """report-conventions.md §1.2：详文档路径带时分秒，同日二次运行不覆盖（缺陷 5）。

    修复前写入 reports/gap-scan/{YYYYMMDD}.md（无时分秒）→ 同日二次运行覆盖历史。
    """
    from datetime import datetime
    from zoneinfo import ZoneInfo

    mod = _load_scan_module()
    tz = ZoneInfo("Asia/Shanghai")
    p1 = mod._report_path_for_now(datetime(2026, 8, 6, 9, 30, 5, tzinfo=tz))
    p2 = mod._report_path_for_now(datetime(2026, 8, 6, 9, 30, 6, tzinfo=tz))
    assert p1.name == "2026-08-06-09-30-05.md"
    assert p1.parent.name == "gap-scan"
    assert p1 != p2  # 秒级区分：同日二次运行不覆盖历史
