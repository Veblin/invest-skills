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


def test_report_help_exposes_research_profile_flags():
    """P0-5：研究档案的显式输入入口须在 help 中可发现。"""
    r = subprocess.run(
        [sys.executable, str(_INVEST), "report", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert r.returncode == 0
    for flag in ("--horizon", "--focus", "--goal", "--style", "--already-knows-price"):
        assert flag in r.stdout, flag
    assert "insight(研究要点)" in r.stdout
