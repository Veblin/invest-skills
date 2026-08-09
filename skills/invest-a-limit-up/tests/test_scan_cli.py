"""Offline CLI smoke for limit-up scan.py --help + #8 全市场行落库回归."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_SCAN_PY = Path(__file__).resolve().parent.parent / "scripts" / "scan.py"


def test_scan_cli_help_exit_0():
    r = subprocess.run(
        [sys.executable, str(_SCAN_PY), "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert r.returncode == 0
    assert "days" in r.stdout.lower() or "质量" in r.stdout


def _mk_stock(symbol: str) -> dict:
    return {
        "symbol": symbol,
        "name": "测试",
        "sector": "银行",
        "market": "主板",
        "max_consecutive": 1,
        "total_appearances": 1,
        "first_date": "20260807",
        "last_date": "20260807",
        "is_st": False,
        "flags": {"sealed": True},
        "appearances": [{"close": 10.0, "change_pct": 10.0, "date": "20260807"}],
        "float_mkt_cap": 1e10,
        "market_cap": 2e10,
    }


def test_filtered_run_persists_full_market_row(tmp_path, monkeypatch):
    """#8 回归：带过滤运行（--sector）时全市场 'all' 行自动落库。

    get_scan 的 'all' 优先契约与 get_breadth_trend 的 WHERE filter_key='all'
    依赖该日 'all' 行；仅落过滤子集会留下市场广度空洞。
    """
    import scan as scan_mod
    import limit_up_store as lus_mod
    import lib.store as store_mod

    db = tmp_path / "research.db"
    monkeypatch.setattr(store_mod, "_db_override", db)
    monkeypatch.setattr(lus_mod, "_db_override", None)

    full = {
        "scan_date": "20260807",
        "trading_days_scanned": 1,
        "market_breadth": {"total_unique_stocks": 2},
        "enrichment": {"tushare": True},
        "errors": [],
        "stocks": [_mk_stock("600000"), _mk_stock("600001")],
    }

    def _fake_scan_market(days=None):
        return dict(full)

    def _fake_quality_filter(result, **kwargs):
        out = dict(result)
        out["stocks"] = [s for s in result["stocks"] if s["symbol"] == "600000"]
        return out

    monkeypatch.setattr(scan_mod, "scan_market", _fake_scan_market)
    monkeypatch.setattr(scan_mod, "quality_filter", _fake_quality_filter)
    monkeypatch.setattr(
        sys, "argv", ["scan.py", "--sector", "银行", "--days", "1"],
    )

    scan_mod.main()

    # 'all' 行已落库：get_scan(scan_date) 优先返回全市场行（2 只）
    row = lus_mod.get_scan(scan_date="20260807")
    assert row is not None
    assert row["filter_key"] == "all"
    assert {s["symbol"] for s in row["stocks"]} == {"600000", "600001"}

    # 过滤子集并存（1 只）
    scans = lus_mod.list_scans(limit=10)
    filt = [s for s in scans if s["filter_key"] != "all"]
    assert len(filt) == 1
    filt_row = lus_mod.get_scan(scan_id=filt[0]["id"])
    assert {s["symbol"] for s in filt_row["stocks"]} == {"600000"}
