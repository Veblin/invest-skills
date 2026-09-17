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


def _current_section_expectations() -> tuple[str, list[str], list[str]]:
    """从 CHANGELOG 的**当前版本段**推导期望值：``(引言块, [### 标题...], [正文细节行...])``。

    期望值全部由 CHANGELOG 现文推导，**不锚具体版本的正文短语**。

    历史缺陷（两次复发）：本文件原先硬编码当前版本的正文短语（v0.2.9 期锚
    「拍卖机制观」/「Frydman & Wang 2020」），每次 bump 后必然失配。因本文件不在
    `pyproject.toml` 的 testpaths 内，本机 `pytest` 恒不执行它 → 红灯只能在 CI
    可见，而 CI 仅在水 PR/main 时触发 → 潜伏至合并才爆。
    （同步记录：commit `3b4feeb` 曾同步过一次锚点；v0.3.0 再度失配。）
    """
    from extract_release_notes import extract_changelog_section

    tag = f"v{_current_version()}"
    changelog_text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    section = extract_changelog_section(changelog_text, tag)
    assert section, f"CHANGELOG.md 缺 {tag} 段——发布链前提不成立"

    lines = section.splitlines()
    headers = [ln.strip()[4:].strip() for ln in lines if ln.strip().startswith("### ")]

    intro_lines: list[str] = []
    for ln in lines:
        if ln.strip().startswith("### "):
            break
        intro_lines.append(ln)
    while intro_lines and not intro_lines[-1].strip():
        intro_lines.pop()
    intro = "\n".join(intro_lines).strip()

    # 小节正文细节行：位于 ### 之后、以 `- ` 开头、且足够长（避免通用短语误命中）
    details: list[str] = []
    in_body = False
    for ln in lines:
        s = ln.strip()
        if s.startswith("### "):
            in_body = True
            continue
        if in_body and s.startswith("- ") and len(s) > 40:
            details.append(s[2:].strip())

    return intro, headers, details


def test_condensed_current_version_notes():
    """当前版本默认输出精简正文：引言块 + 「### 标题」清单，且不含小节正文细节。

    期望值由 `_current_section_expectations()` 从 CHANGELOG 现文推导，bump 版本不失配。
    """
    out = subprocess.check_output(
        [sys.executable, str(SCRIPT), "--from-pyproject"],
        text=True,
        cwd=ROOT,
    )
    tag = f"v{_current_version()}"
    intro, headers, details = _current_section_expectations()

    assert f"## {tag}" in out
    assert intro and intro in out, "引言块应原样保留"
    assert headers, "当前版本段无 ### 小节——本测试前提不成立（应改用旧版本回退断言）"
    for h in headers:
        assert f"- {h}" in out, f"### 标题应以「- {h}」进入主要修改清单"
    assert details, "当前版本段无可验证的长正文细节行——「细节已精简」无从断言"
    for d in details:
        assert d not in out, f"小节正文细节应被精简，却出现：{d[:30]}…"
    assert "**Full Changelog**" in out
    assert "CHANGELOG.md" in out


def test_full_flag_keeps_full_section():
    """--full 显式输出章节全文（供调试/其他用途）：小节正文细节**保留**。

    与本文件上一条构成对照——同一条推导出的细节行，精简模式必须缺、全文模式必须在。
    """
    out = subprocess.check_output(
        [sys.executable, str(SCRIPT), "--from-pyproject", "--full"],
        text=True,
        cwd=ROOT,
    )
    _, headers, details = _current_section_expectations()

    assert "### " in out
    for h in headers:
        assert h in out, f"小节标题应在全文模式保留：{h}"
    assert details, "当前版本段无可验证的长正文细节行——「细节保留」无从断言"
    for d in details:
        assert d in out, f"小节正文细节应在全文模式保留，却缺失：{d[:30]}…"


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
    """CLI --max-chars：输出 ≤ N 字符且合法 UTF-8（镜像 commit 消息场景）。

    截断窗口取当前版本浓缩输出实际长度 -10，不硬编码数值（随 CHANGELOG 篇幅漂移不再假绿）。
    """
    base = subprocess.check_output(
        [sys.executable, str(SCRIPT), "--from-pyproject"],
        text=True,
        cwd=ROOT,
    )
    limit = len(base) - 10
    assert limit > 10  # 防御：浓缩输出本身过短时测试前提失效
    out = subprocess.check_output(
        [sys.executable, str(SCRIPT), "--from-pyproject", "--max-chars", str(limit)],
        text=True,
        cwd=ROOT,
    )
    assert len(out) <= limit
    out.encode("utf-8")
    assert out.endswith("…")
