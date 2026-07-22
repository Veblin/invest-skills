"""Ultra-thin stock CLI smoke — invest.py --help (offline)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_INVEST = Path(__file__).resolve().parent.parent / "scripts" / "invest.py"


def test_invest_cli_help_exit_0():
    r = subprocess.run(
        [sys.executable, str(_INVEST), "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert r.returncode == 0
    assert "collect" in r.stdout or "report" in r.stdout
