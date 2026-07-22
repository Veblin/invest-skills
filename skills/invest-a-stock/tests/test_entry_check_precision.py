"""entry_check: flow scoring uses raw 亿元 floats (Q10), not display strings."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd

_ENTRY = Path(__file__).resolve().parents[3] / "scripts" / "entry_check.py"


def _load_entry_check():
    name = "entry_check_under_test"
    sys.modules.pop(name, None)
    spec = importlib.util.spec_from_file_location(name, _ENTRY)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_flow_score_uses_raw_floats_not_rounded_display():
    """1.004亿 formats as +1.00亿; scoring must still treat smart > 1."""
    mod = _load_entry_check()
    spot = pd.DataFrame(
        [
            {
                "代码": "588000",
                "超大单净流入-净额": 1.004e8,
                "大单净流入-净额": 0.0,
                "中单净流入-净额": 0.0,
                "小单净流入-净额": 0.0,
                "换手率": 1.0,
            }
        ]
    )
    with patch.object(mod, "akshare_direct_session", MagicMock()):
        with patch.object(mod.ak, "fund_etf_spot_em", return_value=spot):
            score, info = mod._flow_score("588000")

    assert info["超大单"] == "+1.00亿"  # display rounded
    assert info["主力态度"] == "积极流入"  # raw 1.004 > 1
    assert score == 21  # 15 + 6


def test_entry_check_help_exit_0():
    import subprocess

    r = subprocess.run(
        [sys.executable, str(_ENTRY), "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert r.returncode == 0
    assert "条件评分" in r.stdout or "symbol" in r.stdout.lower()
