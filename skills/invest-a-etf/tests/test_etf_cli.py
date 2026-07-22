"""Offline CLI smoke for invest-a-etf (no live network)."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

from etf_data import _spot_row_to_quote

_ETF_PY = Path(__file__).resolve().parent.parent / "scripts" / "etf.py"


def _load_etf_main():
    """Load etf.py by path so scripts/ is not put on sys.path (avoids lib shadow)."""
    name = "etf_cli_under_test"
    sys.modules.pop(name, None)
    spec = importlib.util.spec_from_file_location(name, _ETF_PY)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.main


def test_cli_help_exit_0():
    r = subprocess.run(
        [sys.executable, str(_ETF_PY), "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert r.returncode == 0
    assert "report" in r.stdout


def test_invalid_symbol_exit_2():
    main = _load_etf_main()
    assert main(["report", "abc"]) == 2
    assert main(["report", "12345"]) == 2


def test_diagnose_ok():
    main = _load_etf_main()
    assert main(["diagnose"]) == 0


def test_spot_row_to_quote_maps_fields():
    row = pd.Series(
        {
            "最新价": 1.23,
            "涨跌幅": -0.5,
            "成交量": 1000,
            "成交额": 12300,
            "基金折价率": 0.2,  # EM discount → normalized premium_discount = -0.2
        }
    )
    q = _spot_row_to_quote("510300", row)
    assert q["symbol"] == "510300"
    assert q["price"] == pytest.approx(1.23)
    assert q["change_pct"] == pytest.approx(-0.5)
    assert q["premium_discount"] == pytest.approx(-0.2)
    assert q["status"] == "available"
