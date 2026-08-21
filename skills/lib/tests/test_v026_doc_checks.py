"""v0.2.6 文档级断言 — 无网络、无引擎导入，纯文件读取 + 正则验证。

覆盖：
- A 类点位红线：report-conventions §2.4 存在 + compliance_rules.yaml 6 条 wording-level-* 规则
- 红线规则双向行为：断言式命中 / 事实句（已回补/未回补/回补非必然/位置描述）不误伤
- H5 裁决落点：journal SKILL.md 日历效应建议节存在
- 执行计划文档存在（host-docs/v0.2.6/execution-plan.md；CI checkout 无 host-docs 时跳过）
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
# 判定语义 = lint._lint_line_scope：pattern 命中 且 skip_if 不豁免 → 记为违规。
# 「不误伤」样本含两类：pattern 不命中；pattern 命中但 skip_if 豁免（如否定式事实句）。
_REDLINE_CASES = [
    (
        "wording-level-gap-fill",
        ["沪指将回补缺口 4000", "缺口必然回补，需先补掉", "4000 缺口必将回补"],
        [
            "缺口带 3983~4015 未回补已 3 日（记录性事实；回补非必然）",
            "该缺口 7 月已回补",
            "后续是否回补需观察",
            "缺口带不必然回补（记录性事实）",
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
        ["50% 位 3960.26", "斐波回撤位无预测性证据（L3 习俗）", "4050 ≈ 0.382 回撤位 4054.30（L3 习俗）",
         "斐波位无支撑（事实陈述）", "38.2% 回撤位没有压力"],
    ),
    (
        "wording-level-round-number",
        ["整数关口 4000 必然受阻", "4000 整数关口必定突破", "整数关口将受阻", "4000 关口将突破"],
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

    def _lint_flags(text: str) -> bool:
        """与 lint._lint_line_scope 同语义：pattern 命中且 skip_if 不豁免。"""
        return bool(regex.search(text)) and not (skip and skip.search(text))

    for text in should_hit:
        assert _lint_flags(text), f"应命中 {rule_id}: {text!r}"
    for text in should_not_hit:
        assert not _lint_flags(text), f"不应命中 {rule_id}: {text!r}"


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


# ---------------------------------------------------------------- v0.2.7 E5 宏观外推红线

_V027_MACRO_RULE_IDS = (
    "wording-macro-all-time-high",
    "wording-macro-no-top",
    "wording-macro-fundamentals",
    "wording-macro-distance-no-criterion",
    "wording-macro-chain-evidence",
    "wording-staleness-monthly-overseas",
)


def test_v027_macro_redline_rules_registered():
    """E5 六条新禁止的 lint 规则必须存在且为 error 级拦截（验收硬要求）。"""
    rules = _rules()
    for rule_id in _V027_MACRO_RULE_IDS:
        assert rule_id in rules, f"规则缺失: {rule_id}"
        assert rules[rule_id]["severity"] == "error", f"{rule_id} 应为 error 级拦截"
        assert rules[rule_id]["scope"] == "line"


def test_conventions_32_numbering_continuous():
    """§3.2 已知违规模式编号须从 1 连续到最新上限（11 旧 + 6 新 = 17）。"""
    conv = _read(_CONVENTIONS)
    sec = _section(conv, "### 3.2 已知违规模式")
    numbers = [int(m) for m in re.findall(r"^(\d+)\. ", sec, flags=re.M)]
    assert numbers == list(range(1, len(numbers) + 1)), f"§3.2 编号不连续: {numbers}"
    assert len(numbers) >= 17, f"§3.2 应至少 17 条（11 旧 + 6 新），当前 {len(numbers)}"


# (规则, 命中样本, 不误伤样本) — 与 _REDLINE_CASES 同语义
_MACRO_CASES = [
    (
        "wording-macro-all-time-high",
        ["美英德法日 10Y/30Y 收益率全线创出近 18-20 年新高", "四国整体创新高", "全部主权债收益率创新高"],
        ["4 项中 1 项创 2006 年来新高（JP 2.67；GB/DE/FR 均低于 2007-2008 峰值）",
         "并非全线创新高", "日本 10Y 创出 2006 年来新高"],
    ),
    (
        "wording-macro-no-top",
        ["黄金长期上涨方向不变，理论上无价格顶部", "长期方向不变", "理论上无顶部"],
        ["央行结构性购金提供长期需求支撑，但金价高位后均值回归显著（Erb & Harvey 2013）",
         "不代表长期方向不变", "不存在'无顶部'特例"],
    ),
    (
        "wording-macro-fundamentals",
        ["板块同涨同跌，不纠结个股短期基本面", "个股 α 被 β 淹没", "短期基本面不重要", "基本面意义有限"],
        ["在高同步性板块+短窗口+高波动市态下收益方差由板块成分主导；基本面在中长期恢复意义",
         "卖方基于订单而非股价定价 — 基本面解释"],
    ),
    (
        "wording-macro-distance-no-criterion",
        ["中国 30 年国债距前高仅 3-5%", "中国 30 年国债收益率距前高仅 3-5%", "距历史高点 2.4%", "较前高 -10.07%"],
        ["距前高 -10.07%（收益率口径，2025-11 至 2026-06 窗口）",
         "距前高 -0.15%（价格口径，806 日窗口）",
         "距离区间高点 8%（截至 2026-06 窗口）"],
    ),
    (
        "wording-macro-chain-evidence",
        ["中东 → 美债信用 → AI 融资环境 → 资产价格", "美债利率上行 → 高久期股承压 → 资金再配置"],
        ["中东→美债：A 级（Weber 2018 JFE）；美债→AI 融资：C/D 级（无同行评审证据）；最弱环节不作核心论证",
         "AI 融资渠道仅作边际补充（证据等级更低）"],
    ),
    (
        "wording-staleness-monthly-overseas",
        ["日本 10Y 收益率 2.67%", "英德法日 10Y 收益率升至 2.67%"],
        ["英德法日 10Y 收益率（截至 2026-06，滞后约 2.5 个月）：JP 2.67% 为 2006 年来最高",
         "美债 10Y 收益率 4.2%（日频，最新交易日）"],
    ),
]


@pytest.mark.parametrize("rule_id,should_hit,should_not_hit", _MACRO_CASES)
def test_v027_macro_rules_hits_assertions_not_facts(rule_id, should_hit, should_not_hit):
    rule = _rules()[rule_id]
    regex = re.compile(rule["pattern"])
    skip = re.compile(rule["skip_if_pattern"]) if rule.get("skip_if_pattern") else None

    def _lint_flags(text: str) -> bool:
        return bool(regex.search(text)) and not (skip and skip.search(text))

    for text in should_hit:
        assert _lint_flags(text), f"应命中 {rule_id}: {text!r}"
    for text in should_not_hit:
        assert not _lint_flags(text), f"不应命中 {rule_id}: {text!r}"


# ---------------------------------------------------------------- H5 裁决落点

def test_journal_calendar_section():
    journal = _read(_JOURNAL)
    sec = _section(journal, "## 日历效应建议（H5 回测裁决）")
    assert "降级为建议" in sec
    assert "不显著" in sec
    assert "非硬约束" in sec
    assert "scripts/archive/backtest_calendar.py" in sec
    # 负断言：原设计的硬约束不得落地
    assert "窗口内新开仓额外理由" not in journal or "不设置" in sec


def test_exec_plan_exists():
    if not (_REPO_ROOT / _EXEC_PLAN).exists():
        # host-docs 为独立嵌套 git 仓库，不入主仓库 checkout（git ls-files 为空）；
        # 执行计划存在性仅在本地验证，CI 跳过
        pytest.skip("host-docs/v0.2.6/execution-plan.md 不在 CI checkout 中（嵌套仓库）")
    plan = _read(_EXEC_PLAN)
    assert "# v0.2.6 执行计划" in plan
    for key in ("H5 日历效应回测", "D 类引擎字段", "点位红线", "Windows", "预案库"):
        assert key in plan


# ---------------------------------------------------------------- 版本头（防漂移预留）

def test_v026_version_headers_pending_bump():
    """v0.2.6 内容已落（pyproject 已 bump 至 0.2.6）；版本一致性由
    test_v025_doc_checks.py::test_doc_versions_match_pyproject 统一守护。"""
    conv = _read(_CONVENTIONS)
    assert "v0.2.6 新增" in conv


# ---------------------------------------------------------------- Windows ps1 静态自查

def test_ps1_covers_all_23_links():
    """ps1 链接表须覆盖仓库全部 23 条技能链接（17 junction + 6 hardlink）。

    17 junction = .workbuddy\\skills 6 + .claude\\skills 5 + .agents\\skills 6（DSH）；
    6 hardlink = .claude\\commands 6（v0.2.6 补 pattern-scan、v0.2.7 补 .agents 后
    Python 复算，2026-08-21）。
    macOS 无法执行 PowerShell——以链接名清单对照作静态验收（T1-T5 真机验收后置）。
    """
    ps1 = _read("scripts/setup_workbuddy_windows.ps1")
    expected_dirs = [
        ".workbuddy\\skills\\invest-a-stock", ".workbuddy\\skills\\invest-a-etf",
        ".workbuddy\\skills\\invest-a-journal", ".workbuddy\\skills\\invest-a-pulse",
        ".workbuddy\\skills\\invest-a-gap-scan", ".workbuddy\\skills\\invest-a-pattern-scan",
        ".claude\\skills\\invest-a-stock", ".claude\\skills\\invest-a-etf",
        ".claude\\skills\\invest-a-journal", ".claude\\skills\\invest-a-gap-scan",
        ".claude\\skills\\invest-a-pattern-scan",
        ".agents\\skills\\invest-a-stock", ".agents\\skills\\invest-a-etf",
        ".agents\\skills\\invest-a-journal", ".agents\\skills\\invest-a-pulse",
        ".agents\\skills\\invest-a-gap-scan", ".agents\\skills\\invest-a-pattern-scan",
    ]
    expected_files = [
        ".claude\\commands\\invest-a-stock.md", ".claude\\commands\\invest-a-etf.md",
        ".claude\\commands\\invest-a-journal.md", ".claude\\commands\\invest-a-pulse.md",
        ".claude\\commands\\invest-a-gap-scan.md", ".claude\\commands\\invest-a-pattern-scan.md",
    ]
    for name in expected_dirs + expected_files:
        assert name in ps1, f"ps1 缺少链接: {name}"
    # 数量断言：链接表条目恰为 23（17 + 6），无遗漏/重复
    names = re.findall(r'Name = "([^"]+)"', ps1)
    assert len(names) == 23, f"ps1 链接表应为 23 条，实际 {len(names)}"
    assert len(set(names)) == 23, "ps1 链接表存在重复条目"
    # junction 用于目录、hardlink 用于文件（junction 不支持文件）
    assert "New-Item -ItemType Junction" in ps1
    assert "New-Item -ItemType HardLink" in ps1
    # 幂等性：已存在 junction/hardlink 跳过
    assert "LinkType -eq \"Junction\"" in ps1
    assert "LinkType -eq \"HardLink\"" in ps1


def test_readme_windows_rebuild_section():
    readme = _read("README.md")
    assert "setup_workbuddy_windows.ps1" in readme
    assert "23 条技能链接" in readme
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


# ---------------------------------------------------------------- M6 journal 结构化字段

def test_journal_v026_structured_fields():
    journal = _read(_JOURNAL)
    # DB 列与 evaluation_json 键均文档化
    for key in ("stop_price", "expected_loss_pct", "proceeds_destination",
                "stop_moved_count", "extracted_amount", "falsifiable_conditions",
                "trigger_source", "emotion_level", "commitment_level"):
        assert key in journal, f"journal SKILL.md 缺字段 {key}"
    # LAW 6 协调措辞：字段完整性要求 ≠ 交易指令
    assert "字段完整性要求" in journal
    assert "不输出止损位建议数字" in journal


def test_evaluation_criteria_four_dimensions():
    criteria = _read("skills/invest-a-journal/references/evaluation-criteria.md")
    assert "卖出四维评估细则" in criteria
    assert "参考点独立性核对（Reference-Point Check）" in criteria
    assert "机会成本（Opportunity Cost）" in criteria
    # 顺序：参考点独立性在机会成本之前
    assert criteria.index("参考点独立性核对") < criteria.index("机会成本（Opportunity Cost）")
    # 旧"三维"表述清除
    assert "卖出三维" not in criteria
    assert "其他三维" not in criteria
