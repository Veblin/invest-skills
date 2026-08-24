"""Release notes 提取脚本测试。"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / ".github/scripts/extract_release_notes.py"


def _current_version() -> str:
    """从 pyproject.toml 读取当前项目版本（避免硬编码版本号导致 CI 每次 bump 都报错）。"""
    pyproject = ROOT / "pyproject.toml"
    for line in pyproject.read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("version") and "=" in line:
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError("version not found in pyproject.toml")


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


def test_extract_v014_condensed_key_features():
    """v0.1.4 有 ### 小节 → 精简为引言 + 小节标题；正文细节（research 等）删除。"""
    out = subprocess.check_output(
        [sys.executable, str(SCRIPT), "v0.1.4"],
        text=True,
        cwd=ROOT,
    )
    assert "## v0.1.4" in out
    assert "模块 4" in out                 # 引言段保留
    assert "- 报告模板（P0）" in out        # ### 小节标题即主要修改清单
    assert "research" not in out          # 正文细节已精简


def test_extract_from_pyproject():
    out = subprocess.check_output(
        [sys.executable, str(SCRIPT), "--from-pyproject"],
        text=True,
        cwd=ROOT,
    )
    assert f"## v{_current_version()}" in out


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
    assert out == f"v{_current_version()}"


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


def test_full_changelog_links_to_changelog_doc_and_compare():
    """Full Changelog 行须同时链接 CHANGELOG.md（tag 锚定）与提交对比。"""
    from extract_release_notes import build_release_notes

    changelog = ROOT / "CHANGELOG.md"
    notes = build_release_notes("v0.1.4", changelog, repo="Veblin/invest-skills")
    assert notes is not None
    assert (
        "**Full Changelog**: [CHANGELOG.md]"
        "(https://github.com/Veblin/invest-skills/blob/v0.1.4/CHANGELOG.md)" in notes
    )
    assert "v0.1.3...v0.1.4" in notes
    assert "https://github.com/Veblin/invest-skills/compare/" in notes


def test_condense_section_drops_details():
    """有 ### 小节时精简为「引言段 + 小节标题清单」，无 ### 时回退全文。"""
    from extract_release_notes import condense_section

    section = (
        "版本引言：一句话总览。\n\n"
        "### 新增：功能 X\n"
        "- 细节 1（应被删除）\n"
        "- 细节 2（应被删除）\n\n"
        "### 修复：bug Y\n"
        "- 细节 3（应被删除）\n"
    )
    condensed = condense_section(section)
    assert "版本引言：一句话总览。" in condensed
    assert "- 新增：功能 X" in condensed
    assert "- 修复：bug Y" in condensed
    assert "细节 1" not in condensed

    # 无 ### 小节 → 原样返回
    plain = "只有一段文字，没有小节标题。\n"
    assert condense_section(plain) == plain


def test_condensed_current_version_notes():
    """当前版本（v0.2.7）默认输出精简正文：引言 + 主要修改清单，无深层细节。"""
    out = subprocess.check_output(
        [sys.executable, str(SCRIPT), "--from-pyproject"],
        text=True,
        cwd=ROOT,
    )
    tag = f"v{_current_version()}"
    assert f"## {tag}" in out
    assert "四标的全链路报告执行工作流评估" in out  # 引言
    assert "- 新增（2026-08-17）：WorkBuddy 零终端分发" in out  # ### 标题即主要修改
    assert "WB bundle 发布包" not in out  # 深层细节已精简
    assert "**Full Changelog**" in out
    assert "CHANGELOG.md" in out


def test_full_flag_keeps_full_section():
    """--full 显式输出章节全文（供调试/其他用途）。"""
    out = subprocess.check_output(
        [sys.executable, str(SCRIPT), "--from-pyproject", "--full"],
        text=True,
        cwd=ROOT,
    )
    assert "F0-1" in out
    assert "### " in out


def test_truncate_chars_codepoint_safe():
    """截断按字符（code point）而非字节：多字节中文边界不产生非法 UTF-8。"""
    from extract_release_notes import truncate_chars

    # 全是多字节字符：字节截断（head -c）在任意奇数位置都会切断字符
    text = "基" * 100
    out = truncate_chars(text, 10)
    assert out == "基" * 9 + "…"
    assert len(out) == 10
    out.encode("utf-8")  # 合法 UTF-8（截断字符会在此抛 UnicodeEncodeError）


def test_truncate_chars_noop_when_short():
    from extract_release_notes import truncate_chars

    text = "短文本"
    assert truncate_chars(text, 100) == text


def test_truncate_chars_emoji_kept_whole():
    """code point 切片不会切开 emoji：边界处完整保留 + 省略号。"""
    from extract_release_notes import truncate_chars

    text = "前缀🚀🚀🚀🚀"
    out = truncate_chars(text, 5)
    assert out == "前缀🚀🚀…"
    out.encode("utf-8")
    assert len(out) == 5


def test_truncate_chars_drops_lone_surrogate():
    """孤立代理项（异常输入防御）：encode('utf-8','ignore') 往返丢弃，
    输出仍为合法 UTF-8。"""
    from extract_release_notes import truncate_chars

    text = "abc\ud800def"
    out = truncate_chars(text, 5)
    assert out == "abc…"
    out.encode("utf-8")


def test_max_chars_flag_end_to_end():
    """CLI --max-chars：输出 ≤ N 字符且合法 UTF-8（镜像 commit 消息场景）。"""
    out = subprocess.check_output(
        [sys.executable, str(SCRIPT), "--from-pyproject", "--max-chars", "800"],
        text=True,
        cwd=ROOT,
    )
    assert len(out) <= 800
    out.encode("utf-8")
    assert out.endswith("…")
