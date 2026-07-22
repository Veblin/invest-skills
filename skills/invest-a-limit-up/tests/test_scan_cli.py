"""Offline CLI smoke for limit-up scan.py --help."""

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
