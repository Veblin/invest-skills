"""
compliance/lint 模块 — invest-A 研究报告合规扫描器。

用法:
    from lib.lint import lint_file, lint_directory, format_results
    findings = lint_file(Path("report.md"))
    print(format_results("report.md", findings))

规则来源：CLAUDE.md 措辞规范、已知违规模式、估值分位规则、分析标记规范。
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore[assignment]


# ── 数据模型 ──────────────────────────────────────────────────────────────


@dataclass
class LintFinding:
    """单条合规发现。"""
    line: int
    rule_id: str
    severity: str       # error / warning / info
    message: str
    context: str        # 匹配行内容（截断后）
    law_ref: str = ""


# ── 规则加载 ──────────────────────────────────────────────────────────────


_RULES_CACHE: Optional[list[dict]] = None


def _rules_path() -> Path:
    """返回 compliance_rules.yaml 的绝对路径。"""
    return (
        Path(__file__).resolve().parent.parent
        / "references"
        / "compliance_rules.yaml"
    )


def load_rules() -> list[dict]:
    """加载合规规则列表。

    与 invest.py 同目录下的 references/compliance_rules.yaml 是规则数据源。
    缓存避免重复解析。
    """
    global _RULES_CACHE
    if _RULES_CACHE is not None:
        return _RULES_CACHE

    path = _rules_path()
    if not path.exists():
        print(f"❌ 规则文件不存在: {path}", file=sys.stderr)
        _RULES_CACHE = []
        return _RULES_CACHE

    if yaml is None:
        print("❌ pyyaml 未安装，无法加载规则", file=sys.stderr)
        _RULES_CACHE = []
        return _RULES_CACHE

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        _RULES_CACHE = data.get("rules", [])
        return _RULES_CACHE
    except Exception as exc:
        print(f"❌ 规则文件解析失败: {exc}", file=sys.stderr)
        _RULES_CACHE = []
        return _RULES_CACHE


# ── 规则过滤 ──────────────────────────────────────────────────────────────


def _should_skip_by_profile(rule: dict, profile: str) -> bool:
    """根据 profile 判断规则是否跳过。

    claude (默认): 全部规则
    engine: 仅措辞 + 文件名规则，跳过结构/证据检查
    """
    if profile == "claude":
        return False  # 全部启用

    # engine profile: 只保留 wording 类和 filename 类规则
    rule_id: str = rule.get("id", "")
    scope: str = rule.get("scope", "line")

    # 文件名规则
    if scope == "filename":
        return False

    # 措辞规则（wording- 前缀）
    if rule_id.startswith("wording-"):
        return False

    # LAW3 软提醒（往往/通常）— 引擎有固定模板，结构类提醒不适用
    # 必须在通用 law 检查之前，因为 law3- 也匹配 law 前缀
    if rule_id.startswith("law3"):
        return True

    # 已知违规模式
    if rule_id.startswith("known-violation"):
        return False

    # LAW 规则（除了 structure- 前缀）
    if rule_id.startswith("law"):
        return False

    # 跳过结构类规则（structure-）和估值分位规则（percentile-）
    if rule_id.startswith("structure-") or rule_id.startswith("percentile"):
        return True

    # 默认跳过（engine 模式下只保留上面显式保留的规则）
    return True


# ── 核心扫描 ──────────────────────────────────────────────────────────────


def _truncate(text: str, max_len: int = 120) -> str:
    """截断文本为可显示长度。"""
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."


def _compile_regex(pattern: str) -> Optional[re.Pattern]:
    """安全编译正则，失败时返回 None 并打印警告。"""
    try:
        return re.compile(pattern)
    except re.error as exc:
        print(
            f"⚠️ 规则正则编译失败: {pattern!r} — {exc}",
            file=sys.stderr,
        )
        return None


def _lint_line_scope(
    lines: list[str],
    rule: dict,
) -> list[LintFinding]:
    """对行级规则（scope=line）逐行匹配。"""
    findings: list[LintFinding] = []
    pattern_str = rule.get("pattern", "")
    regex = _compile_regex(pattern_str)
    if regex is None:
        return findings

    for i, line in enumerate(lines, start=1):
        if regex.search(line):
            findings.append(
                LintFinding(
                    line=i,
                    rule_id=rule["id"],
                    severity=rule.get("severity", "info"),
                    message=rule["message"],
                    context=_truncate(line.strip()),
                    law_ref=rule.get("law_ref", ""),
                )
            )
    return findings


def _lint_file_scope(
    text: str,
    rule: dict,
) -> list[LintFinding]:
    """对文件级规则（scope=file）检查模式是否存在。"""
    findings: list[LintFinding] = []
    pattern_str = rule.get("pattern", "")
    regex = _compile_regex(pattern_str)
    if regex is None:
        return findings

    if not regex.search(text):
        # 未找到必备结构 → 警告
        findings.append(
            LintFinding(
                line=0,
                rule_id=rule["id"],
                severity=rule.get("severity", "info"),
                message=rule["message"],
                context="（全文未匹配）",
                law_ref=rule.get("law_ref", ""),
            )
        )
    return findings


def _lint_paragraph_scope(
    lines: list[str],
    rule: dict,
) -> list[LintFinding]:
    """对段级规则（scope=paragraph）按空行分隔段落匹配。

    分段规则：空行或连续空行分隔不同段落。
    """
    findings: list[LintFinding] = []

    # 将 lines 分组为段落
    paragraphs: list[tuple[int, list[str]]] = []
    current_start = 1
    current_lines: list[str] = []

    for i, line in enumerate(lines, start=1):
        stripped = line.strip()
        if stripped == "" and current_lines:
            # 空行结束当前段落
            paragraphs.append((current_start, current_lines))
            current_lines = []
            current_start = i + 1
        elif stripped != "":
            current_lines.append(stripped)

    # 最后一个段落
    if current_lines:
        paragraphs.append((current_start, current_lines))

    pattern_str = rule.get("pattern", "")
    regex = _compile_regex(pattern_str)
    if regex is None:
        return findings

    for para_start, para_lines in paragraphs:
        para_text = " ".join(para_lines)
        if regex.search(para_text):
            # 匹配到段落 → flag 段内所有包含 "分位" 的行
            for j, pline in enumerate(para_lines):
                if "分位" in pline and "%" in pline:
                    findings.append(
                        LintFinding(
                            line=para_start + j,
                            rule_id=rule["id"],
                            severity=rule.get("severity", "info"),
                            message=rule["message"],
                            context=_truncate(pline),
                            law_ref=rule.get("law_ref", ""),
                        )
                    )
    return findings


def _lint_filename_scope(
    filepath: Path,
    rule: dict,
) -> list[LintFinding]:
    """对文件名级规则（scope=filename）检查文件名格式。"""
    findings: list[LintFinding] = []
    pattern_str = rule.get("pattern", "")
    regex = _compile_regex(pattern_str)
    if regex is None:
        return findings

    fname = filepath.name
    if not regex.match(fname):
        findings.append(
            LintFinding(
                line=0,
                rule_id=rule["id"],
                severity=rule.get("severity", "info"),
                message=rule["message"],
                context=f"文件名: {fname}",
                law_ref=rule.get("law_ref", ""),
            )
        )
    return findings


# ── 公开 API ──────────────────────────────────────────────────────────────


def lint_file(filepath: Path, profile: str = "claude") -> list[LintFinding]:
    """扫描单个报告文件，返回所有合规发现。

    Args:
        filepath: Markdown 报告路径。
        profile: claude（全部规则）| engine（措辞+文件名）。

    Returns:
        按行号排序的 LintFinding 列表。
    """
    if not filepath.exists():
        print(f"❌ 文件不存在: {filepath}", file=sys.stderr)
        return []

    try:
        text = filepath.read_text(encoding="utf-8")
    except Exception as exc:
        print(f"❌ 无法读取文件 {filepath}: {exc}", file=sys.stderr)
        return []

    lines = text.split("\n")
    findings: list[LintFinding] = []
    rules = load_rules()

    for rule in rules:
        if _should_skip_by_profile(rule, profile):
            continue

        scope = rule.get("scope", "line")

        if scope == "line":
            findings.extend(_lint_line_scope(lines, rule))
        elif scope == "file":
            findings.extend(_lint_file_scope(text, rule))
        elif scope == "paragraph":
            findings.extend(_lint_paragraph_scope(lines, rule))
        elif scope == "filename":
            findings.extend(_lint_filename_scope(filepath, rule))

    # 按行号排序
    findings.sort(key=lambda f: (f.line if f.line > 0 else 999999, f.rule_id))
    return findings


def lint_directory(
    directory: Path,
    profile: str = "claude",
) -> dict[str, list[LintFinding]]:
    """扫描目录下所有 .md 文件。

    Args:
        directory: 目标目录。
        profile: claude（全部规则）| engine（措辞+文件名）。

    Returns:
        {文件名: [LintFinding, ...]}
    """
    if not directory.is_dir():
        print(f"❌ 目录不存在: {directory}", file=sys.stderr)
        return {}

    results: dict[str, list[LintFinding]] = {}
    md_files = sorted(directory.glob("*.md"))

    if not md_files:
        print(f"ℹ️ 目录中无 .md 文件: {directory}", file=sys.stderr)
        return {}

    for fpath in md_files:
        findings = lint_file(fpath, profile=profile)
        results[fpath.name] = findings

    return results


# ── 输出格式化 ──────────────────────────────────────────────────────────────


def format_results(
    filename: str,
    findings: list[LintFinding],
) -> str:
    """格式化为人类可读的扫描报告。

    按 severity 分组输出。
    """
    errors = [f for f in findings if f.severity == "error"]
    warnings = [f for f in findings if f.severity == "warning"]
    infos = [f for f in findings if f.severity == "info"]

    lines: list[str] = [
        f"## 合规扫描: {filename}",
        "",
    ]

    def _format_group(items: list[LintFinding], icon: str, label: str) -> None:
        if not items:
            return
        lines.append(f"### {icon} {label} ({len(items)})")
        for item in items:
            loc = f"L{item.line}" if item.line > 0 else "文件名"
            law = f" [{item.law_ref}]" if item.law_ref else ""
            lines.append(f"- **{loc}**: {item.message} [{item.rule_id}]{law}")
            lines.append(f"  > {item.context}")
        lines.append("")

    _format_group(errors, "❌", "错误")
    _format_group(warnings, "⚠️", "警告")
    _format_group(infos, "ℹ️", "信息")

    # 汇总
    total = len(findings)
    err_count = len(errors)
    warn_count = len(warnings)
    info_count = len(infos)
    lines.append(
        f"通过: {total - err_count - warn_count}"
        f" | 错误: {err_count}"
        f" | 警告: {warn_count}"
        f" | 信息: {info_count}"
    )
    lines.append("")

    return "\n".join(lines)


def print_results(
    filename: str,
    findings: list[LintFinding],
    file=sys.stdout,
) -> int:
    """打印扫描结果并返回退出码。

    Returns:
        0 = 无错误，1 = 存在错误
    """
    output = format_results(filename, findings)
    print(output, file=file)

    errors = [f for f in findings if f.severity == "error"]
    return 1 if errors else 0
