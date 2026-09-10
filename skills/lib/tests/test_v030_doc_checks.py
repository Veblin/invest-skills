"""v0.3.0 文档级断言 — 无网络、无引擎导入，纯文件读取 + 正则验证。

覆盖（F1-F5，源自 516160 报告 LAW 合规审查 2026-09-08）：
- F3：compliance_rules.yaml 存在 `wording-unrun-source-label` 规则（error/line 级），
  5 个未实跑字样逐词命中、合法来源标注不误伤
- F1：report-conventions §2.3 强制行为 7（[分析] 事实性前提来源标注）存在
- F3 同步：report-conventions 强制 5 与 CLAUDE.md 含全部 5 个禁用字样
- F4：report-conventions §3.2 新增模式 #18（§N 交叉引用错位）存在；
  §3.2 编号 1 起连续且 >=19 条
- F5：§3.2 模式 #15（距前高口径）含标准表述模板

D1=A（2026-09-08 用户裁决）：lint 只做 F3 token 级，F2 靠条文 + 自检清单。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO_ROOT))  # yaml 读取用相对路径（保持与 v026 同形态）

_CONVENTIONS = "skills/lib/references/report-conventions.md"
_RULES_YAML = "skills/invest-a-stock/scripts/references/compliance_rules.yaml"
_CLAUDE_MD = "CLAUDE.md"

_UNRUN_TOKENS = ("Python calc 视角", "口径源自引擎", "聚合验证", "Python 复算一致", "自洽校验")
_RULE_ID = "wording-unrun-source-label"


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


# ---------------------------------------------------------------- F3 规则存在性


def test_f3_rule_registered():
    rules = _rules()
    assert _RULE_ID in rules, f"规则缺失: {_RULE_ID}"
    rule = rules[_RULE_ID]
    assert rule["severity"] == "error", "F3 应为 error 级拦截"
    assert rule["scope"] == "line"
    for token in _UNRUN_TOKENS:
        assert token in rule["pattern"], f"规则 pattern 缺字样: {token}"


# ---------------------------------------------------------------- F3 双向行为

# 判定语义 = lint._lint_line_scope：pattern 命中 且 skip_if 不豁免 -> 记为违规。
_HIT_SAMPLES = [
    "历史分位经 Python 复算一致（口径见上表）",
    "聚合计与 top10_sum 自洽校验一致",
    "该偏离经聚合验证为 -21%",
    "Python calc 视角 -21%",
    "口径源自引擎 summary 字段",
]

_CLEAN_SAMPLES = [
    "NAV vs MA20 偏离 +1.2% [来源: kline.derived.nav_vs_ma20_pct]",
    "前十大占 58.97% [来源: Python calc: 22.81+14.48+11.77+9.91]",
    "距 6/25 顶部 -27.03% [来源: history.stats.current_vs_high_pct]",
    "流出量是同行 2.5 倍 [来源: Python calc: 2.33/0.93]",
    "复算一致（仅描述复核动作，不含禁用字样完整形态）",
    "该差异来自两条拉取路径口径之差，未裁决（并列报告）",
]


def test_f3_hits_unrun_labels_not_clean_labels():
    rule = _rules()[_RULE_ID]
    regex = re.compile(rule["pattern"])
    skip = re.compile(rule["skip_if_pattern"]) if rule.get("skip_if_pattern") else None

    def _lint_flags(text: str) -> bool:
        return bool(regex.search(text)) and not (skip and skip.search(text))

    for text in _HIT_SAMPLES:
        assert _lint_flags(text), f"应命中 {_RULE_ID}: {text!r}"
    for text in _CLEAN_SAMPLES:
        assert not _lint_flags(text), f"不应命中 {_RULE_ID}: {text!r}"


# ---------------------------------------------------------------- 规范/CLAUDE 同步


def test_conventions_f1_premise_source_mandate():
    """§2.3 强制行为 7（[分析] 事实性前提来源标注）存在且含关键约束。"""
    conv = _read(_CONVENTIONS)
    assert "**[分析] 块内事实性前提必须带来源" in conv, "§2.3 强制 7 缺失"
    assert "无标注的事实性前提不得作为推演基础" in conv
    assert "证据强度标签只对其实际覆盖的数据依据负责" in conv


def test_unrun_tokens_synced_in_conventions_and_claude_md():
    """5 个禁用字样须同时出现在 report-conventions §2.3 强制 5 与 CLAUDE.md P0 表。"""
    conv = _read(_CONVENTIONS)
    claude = _read(_CLAUDE_MD)
    for token in _UNRUN_TOKENS:
        assert token in conv, f"report-conventions 缺禁用字样: {token}"
        assert token in claude, f"CLAUDE.md 缺禁用字样: {token}"


def test_conventions_32_numbering_19_continuous():
    """§3.2 编号从 1 连续且 >=19 条（11 旧 + 6 E5 + 2 新 #18/#19）。"""
    conv = _read(_CONVENTIONS)
    sec = _section(conv, "### 3.2 已知违规模式")
    numbers = [int(m) for m in re.findall(r"^(\d+)\. ", sec, flags=re.M)]
    assert numbers == list(range(1, len(numbers) + 1)), f"§3.2 编号不连续: {numbers}"
    assert len(numbers) >= 19, (
        f"§3.2 应 >=19 条（v0.3.0 新增 #18 交叉引用/#19 前提背书），当前 {len(numbers)}"
    )


def test_conventions_new_patterns_18_19_and_c10_template():
    """#18（§N 交叉引用错位）/#19（前提被标签背书）与 #15 C10 标准模板存在。"""
    conv = _read(_CONVENTIONS)
    sec = _section(conv, "### 3.2 已知违规模式")
    assert "§N 交叉引用" in sec, "#18 缺失"
    assert "被块尾证据强度标签背书" in sec, "#19 缺失"
    assert "价格口径（NAV 前复权），窗口 2025-08-25 起 252 交易日" in sec, "#15 C10 标准模板缺失"


def test_etf_skill_selfcheck_synced():
    """ETF SKILL Self-Check 含 v0.3.0 三项（F1/F2+F3/F4）且引用强制行为 7。"""
    etf_skill = _read("skills/invest-a-etf/SKILL.md")
    assert "证据标签未覆盖无来源前提" in etf_skill
    assert "派生数字（倍数/比例/百分点/点位差）带 `[来源: Python calc: formula]`" in etf_skill
    assert "正文 §N 交叉引用指向节含被引内容" in etf_skill
    assert "强制行为 5-7" in etf_skill
