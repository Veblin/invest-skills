"""Release notes 提取脚本测试。"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / ".github/scripts/extract_release_notes.py"


def test_extract_v013_contains_phases():
    out = subprocess.check_output(
        [sys.executable, str(SCRIPT), "v0.1.3"],
        text=True,
        cwd=ROOT,
    )
    assert "## v0.1.3" in out
    assert "九模块" in out
    assert "Phase 1" in out
    assert "Breaking Changes" in out
    assert "v0.1.2...v0.1.3" in out


def test_extract_from_pyproject():
    out = subprocess.check_output(
        [sys.executable, str(SCRIPT), "--from-pyproject"],
        text=True,
        cwd=ROOT,
    )
    assert "## v0.1.3" in out


def test_strict_fails_without_section():
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "v99.99.99", "--strict"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 1


def test_print_tag_from_pyproject():
    out = subprocess.check_output(
        [sys.executable, str(SCRIPT), "--from-pyproject", "--print-tag"],
        text=True,
        cwd=ROOT,
    ).strip()
    assert out == "v0.1.3"
