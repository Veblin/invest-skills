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
    # Full Changelog 行依赖 git tag，浅克隆 CI 可能缺失，不硬断言具体 tag 对


def test_extract_v014_contains_key_features():
    out = subprocess.check_output(
        [sys.executable, str(SCRIPT), "v0.1.4"],
        text=True,
        cwd=ROOT,
    )
    assert "## v0.1.4" in out
    assert "模块 4" in out
    assert "research" in out


def test_extract_from_pyproject():
    out = subprocess.check_output(
        [sys.executable, str(SCRIPT), "--from-pyproject"],
        text=True,
        cwd=ROOT,
    )
    assert "## v0.1.6" in out


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
    assert out == "v0.1.6"


def test_build_release_notes_includes_compare_when_previous_tag_known():
    from extract_release_notes import build_release_notes, changelog_has_section

    changelog = ROOT / "CHANGELOG.md"
    assert changelog_has_section("0.1.4", changelog)

    notes = build_release_notes("v0.1.4", changelog)
    assert notes is not None
    assert "## v0.1.4" in notes
    assert "模块 4" in notes
    # 有 git 时应有 compare 尾注；无 git 时仍应有正文
    if "**Full Changelog**" in notes:
        assert "v0.1.4" in notes
