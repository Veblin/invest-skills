"""v0.2.6 文档级断言 — 无网络、无引擎导入，纯文件读取 + 正则验证。

覆盖：
- A 类点位红线：report-conventions §2.4 存在 + compliance_rules.yaml 6 条 wording-level-* 规则
- 红线规则双向行为：断言式命中 / 事实句（已回补/未回补/回补非必然/位置描述）不误伤
- H5 裁决落点：journal SKILL.md 日历效应建议节存在
- 执行计划文档存在（host-docs/v0.2.6/execution-plan.md）
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest
import yaml

_REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO_ROOT))  # yaml 读取用相对路径

_CONVENTIONS = "skills/lib/references/report-conventions.md"
_JOURNAL = "skills/invest-a-journal/SKILL.md"
_RULES_YAML = "skills/invest-a-stock/scripts/references/compliance_rules.yaml"
_EXEC_PLAN = "host-docs/v0.2.6/execution-plan.md"


def _read(rel: str) -> str:
    return (_REPO_ROOT / rel).read_text(encoding="utf-8")


def _section(text: str, header: str) -> str:
    """从 header 行到下一个同级别标题的文本。"""
    start = text.index(header)
    level = len(header) - len(header.lstrip("#"))
    marker = "#" * level + " "
    lines = text[start:].split("\n")
    out = []
    for i, line in enumerate(lines):
        if i > 0 and line.startswith(marker):
            break
        out.append(line)
    return "\n".join(out)


def _rules() -> dict[str, dict]:
    data = yaml.safe_load(_read(_RULES_YAML))
    return {r["id"]: r for r in data["rules"]}


# ---------------------------------------------------------------- A 类 §2.4 章节

def test_conventions_24_exists():
    conv = _read(_CONVENTIONS)
    sec = _section(conv, "### 2.4 点位证据等级与红线清单")
    for grade in ("L1", "L2", "L3", "L4"):
        assert grade in sec
    for key in ("整数关口", "斐波", "缺口带", "Tsinaslanidis 2022", "JBEF 2020", "STW 1999"):
        assert key in sec
    # 禁止断言表 + 允许表述形式
    assert "禁止断言表" in sec
    assert "允许的表述形式" in sec
    assert "记录性事实；回补非必然" in sec


# ---------------------------------------------------------------- lint 规则存在性

def test_redline_rules_registered():
    rules = _rules()
    for rule_id in (
        "wording-level-gap-fill",
        "wording-level-ma-retest",
        "wording-level-boll-rebound",
        "wording-level-fib-support",
        "wording-level-round-number",
        "wording-level-wave-stop",
    ):
        assert rule_id in rules, f"规则缺失: {rule_id}"
        assert rules[rule_id]["severity"] == "error", f"{rule_id} 应为 error 级拦截"
        assert rules[rule_id]["scope"] == "line"


# ---------------------------------------------------------------- 红线双向行为

# (规则, 命中样本, 不误伤样本)
_REDLINE_CASES = [
    (
        "wording-level-gap-fill",
        ["沪指将回补缺口 4000", "缺口必然回补，需先补掉", "4000 缺口必将回补"],
        [
            "缺口带 3983~4015 未回补已 3 日（记录性事实；回补非必然）",
            "该缺口 7 月已回补",
            "后续是否回补需观察",
        ],
    ),
    (
        "wording-level-ma-retest",
        ["价格将回踩 MA20 后企稳", "沪指必回踩 20 日线"],
        ["若回踩 20 日线则重新评估预案", "收盘站稳 20 日线", "当前价 vs MA20 偏离 -3.2%"],
    ),
    (
        "wording-level-boll-rebound",
        ["触及 BOLL 上轨将回调", "BOLL 上轨必然回调"],
        ["BOLL 位置 85%（近上轨）", "上轨 3783.33"],
    ),
    (
        "wording-level-fib-support",
        ["38.2% 回撤位有支撑", "斐波位 0.618 构成压力"],
        ["50% 位 3960.26", "斐波回撤位无预测性证据（L3 习俗）", "4050 ≈ 0.382 回撤位 4054.30（L3 习俗）"],
    ),
    (
        "wording-level-round-number",
        ["整数关口 4000 必然受阻", "4000 整数关口必定突破"],
        ["整数关口 4000 为市场关注位（中国实证：熊市效应最强）[证据: L1-2]"],
    ),
    (
        "wording-level-wave-stop",
        ["三浪回撤将止于 3514", "二浪调整将止于一浪 61.8%"],
        ["三浪目标 3514-3519 仅为 1.618 倍外推参考", "波浪划分为经验性计数，本身主观"],
    ),
]


@pytest.mark.parametrize("rule_id,should_hit,should_not_hit", _REDLINE_CASES)
def test_redline_hits_assertions_not_facts(rule_id, should_hit, should_not_hit):
    rule = _rules()[rule_id]
    regex = re.compile(rule["pattern"])
    skip = re.compile(rule["skip_if_pattern"]) if rule.get("skip_if_pattern") else None
    for text in should_hit:
        assert regex.search(text), f"应命中 {rule_id}: {text!r}"
        if skip and skip.search(text):
            pytest.fail(f"命中样本不应被 skip_if 放过: {text!r}")
    for text in should_not_hit:
        assert not regex.search(text), f"不应命中 {rule_id}: {text!r}"


def test_redline_not_fire_on_abcd_doc_allowed_rows():
    """允许表述形式样例（§2.4 表内）不得命中任何红线规则。"""
    conv = _read(_CONVENTIONS)
    sec = _section(conv, "### 2.4 点位证据等级与红线清单")
    # 允许表述形式行的关键样例
    samples = [
        "当前价 X vs MA20 Y，偏离 +Z% [来源: engine]",
        "整数关口 4000 为市场关注位（中国实证：熊市效应最强）[证据: L1-2]",
        "缺口带 3983~4015 未回补已 N 日（记录性事实；回补非必然）[证据: L2]",
    ]
    assert all(s in sec for s in samples), "§2.4 允许表述形式样例缺失"
    rules = _rules()
    for rule_id, rule in rules.items():
        if not rule_id.startswith("wording-level-"):
            continue
        regex = re.compile(rule["pattern"])
        for s in samples:
            assert not regex.search(s), f"允许表述不应命中 {rule_id}: {s!r}"


# ---------------------------------------------------------------- H5 裁决落点

def test_journal_calendar_section():
    journal = _read(_JOURNAL)
    sec = _section(journal, "## 日历效应建议（H5 回测裁决）")
    assert "降级为建议" in sec
    assert "不显著" in sec
    assert "非硬约束" in sec
    assert "scripts/backtest_calendar.py" in sec
    # 负断言：原设计的硬约束不得落地
    assert "窗口内新开仓额外理由" not in journal or "不设置" in sec


def test_exec_plan_exists():
    plan = _read(_EXEC_PLAN)
    assert "# v0.2.6 执行计划" in plan
    for key in ("H5 日历效应回测", "D 类引擎字段", "点位红线", "Windows", "预案库"):
        assert key in plan


# ---------------------------------------------------------------- 版本头（防漂移预留）

def test_v026_version_headers_pending_bump():
    """v0.2.6 内容已落、版本号待 bump：当前 pyproject 为 0.2.5 时此测试只断言
    文档内容存在性（版本一致性由 test_v025_doc_checks.py::test_doc_versions_match_pyproject 统一守护）。"""
    conv = _read(_CONVENTIONS)
    assert "v0.2.6 新增" in conv


# ---------------------------------------------------------------- Windows ps1 静态自查

def test_ps1_covers_all_14_links():
    """ps1 链接表须覆盖仓库全部 14 条技能链接（9 junction + 5 hardlink）。

    macOS 无法执行 PowerShell——以链接名清单对照作静态验收（T1-T5 真机验收后置）。
    """
    ps1 = _read("scripts/setup_workbuddy_windows.ps1")
    expected_dirs = [
        ".workbuddy\\skills\\invest-a-stock", ".workbuddy\\skills\\invest-a-etf",
        ".workbuddy\\skills\\invest-a-journal", ".workbuddy\\skills\\invest-a-pulse",
        ".workbuddy\\skills\\invest-a-gap-scan",
        ".claude\\skills\\invest-a-stock", ".claude\\skills\\invest-a-etf",
        ".claude\\skills\\invest-a-journal", ".claude\\skills\\invest-a-gap-scan",
    ]
    expected_files = [
        ".claude\\commands\\invest-a-stock.md", ".claude\\commands\\invest-a-etf.md",
        ".claude\\commands\\invest-a-journal.md", ".claude\\commands\\invest-a-pulse.md",
        ".claude\\commands\\invest-a-gap-scan.md",
    ]
    for name in expected_dirs + expected_files:
        assert name in ps1, f"ps1 缺少链接: {name}"
    # junction 用于目录、hardlink 用于文件（junction 不支持文件）
    assert "New-Item -ItemType Junction" in ps1
    assert "New-Item -ItemType HardLink" in ps1
    # 幂等性：已存在 junction/hardlink 跳过
    assert "LinkType -eq \"Junction\"" in ps1
    assert "LinkType -eq \"HardLink\"" in ps1


def test_readme_windows_rebuild_section():
    readme = _read("README.md")
    assert "setup_workbuddy_windows.ps1" in readme
    assert "14 条技能链接" in readme
    assert "cmd /c dir .workbuddy\\skills" in readme or "cmd /c dir .workbuddy/skills" in readme


# ---------------------------------------------------------------- 预案库

def test_scenario_plans_reference_exists():
    plan = _read("skills/lib/references/scenario-plans.md")
    # 模板 + 已激活 E-001 + 候选 + 闭环 + 边界
    for key in ("预案 #E-001", "E-002", "E-007", "触发条件（科学计算）", "失效条件",
                "季度聚合", "命中率 < 50%", "非交易指令", "632 交易日", "Gollwitzer"):
        assert key in plan, f"scenario-plans.md 缺少: {key}"
    # E-001 基线数字（Python 复跑确认 2026-08-14）
    assert "13 次收盘入带" in plan
    assert "38%" in plan
    assert "55.18%" in plan
    assert "+0.26%" in plan
    assert "-1.33pp" in plan
    # 样本限定保留
    assert "样本量小，仅作参考" in plan


def test_journal_scenario_closed_loop():
    journal = _read(_JOURNAL)
    sec = _section(journal, "## 情景预案闭环（scenario-plans）")
    assert "触发即记录" in sec
    assert "研究流程规则，非交易指令" in sec
    assert "命中率 < 50%" in sec
    assert "禁止凭空造预案" in sec
    assert "scenario-plans.md" in sec
