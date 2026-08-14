"""v0.2.5 D1-D8 文档级断言 — 无网络、无引擎导入，纯文件读取。

对照 host-docs/v0.2.5/execution-plan.md §2 每项需求的测试清单：
- D1 统一参考输出层（report-conventions §8 + pulse/journal 模板）
- D2 journal 参考点独立性核对
- D4 trade-structure 3 段区间（悲观锚区/中性-悲观区/中性锚区）
- D5 离场理由参考规范（Odean 1998 浮盈目标提示）
- D6 A 股主线资金流确认（Chui et al. 2022）
- D7 止损定位与话术规范（Kaminski & Lo 2014）
- D8 观念修正内置（C3/C5 表述）

版本一致性断言随 pyproject [project].version 动态比对（仿 test_lib_version.py），
避免版本号硬编码。
"""

from __future__ import annotations

import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]

_CONVENTIONS = "skills/lib/references/report-conventions.md"
_JOURNAL = "skills/invest-a-journal/SKILL.md"
_PULSE = "skills/invest-a-pulse/SKILL.md"
_STOCK = "skills/invest-a-stock/SKILL.md"
_TRADE_STRUCTURE = "skills/invest-a-stock/references/trade-structure.md"

# 动作词扫描：无动作词（买/卖/加/减/止）紧跟在"建议"之后
_ACTION_RE = re.compile(r"建议(?:买|卖|加|减|止)")


def _read(rel: str) -> str:
    return (_REPO_ROOT / rel).read_text(encoding="utf-8")


def _section(text: str, header: str) -> str:
    """从 header 行到下一个同级别（或更高级别）标题的文本。

    仅按行首 '#' 前缀判定级别，不解析代码块——调用方对嵌套了二级
    标题的模板节须改用显式边界切片（见 test_d1_journal_template_no_action_words）。
    """
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


def _action_matches(text: str) -> list[str]:
    return _ACTION_RE.findall(text)


# ---------------------------------------------------------------- D1 参考输出层

def test_d1_conventions_section8_exists():
    conv = _read(_CONVENTIONS)
    sec8 = _section(conv, "## 8. 参考输出层")
    assert "### 8.1 四类参考定义" in sec8
    assert "### 8.2 判定标准" in sec8
    assert "### 8.3 术语规范" in sec8
    assert "### 8.4 Self-Check 新增" in sec8
    # 四类参考定义表
    for kind in ("趋势参考", "区间参考", "状态参考", "核对参考"):
        assert kind in sec8
    # 判定标准（LAW 6a 同构）
    assert "skill 产出只描述「市场是什么样」与「你的决策理由质量如何」，不描述「你应该做什么」" in sec8
    # §7.1 与 §8.4 各含同一检查项
    assert conv.count("输出是否只含四类参考、无任何动作词") >= 2


def test_d1_conventions_section8_no_action_words():
    """§8 节内动作词仅允许"禁用"自指命中（L286 术语规范：
    "禁用「建议止损 / 建议止盈」" 命中 2 次，同在一行且含禁用）。"""
    conv = _read(_CONVENTIONS)
    sec8 = _section(conv, "## 8. 参考输出层")  # L268 → EOF，无嵌套二级标题
    matches = _action_matches(sec8)
    assert len(matches) == 2, f"§8 节动作词命中数应为 2（建议止损/建议止盈），实际 {len(matches)}: {matches}"
    hit_lines = [l for l in sec8.split("\n") if _ACTION_RE.search(l)]
    assert len(hit_lines) == 1, f"两处命中应在同一行，实际 {len(hit_lines)} 行"
    assert "禁用" in hit_lines[0], f"命中行应为禁用自指，实际: {hit_lines[0]}"


def test_d1_pulse_reference_layer():
    pulse = _read(_PULSE)
    sec = _section(pulse, "## 📌 参考输出层")  # L260 → L274（## 分析纪律）
    assert "## 📌 参考输出层" in pulse
    for kind in ("趋势参考", "区间参考", "状态参考", "核对参考"):
        assert kind in sec
    assert "只描述市场客观状态，不含任何动作建议；执行由你依据自身纪律决定。" in sec


def test_d1_pulse_reference_layer_no_action_words():
    pulse = _read(_PULSE)
    sec = _section(pulse, "## 📌 参考输出层")
    matches = _action_matches(sec)
    assert matches == [], f"pulse 📌 参考输出层节动作词应为空，实际: {matches}"


def test_d1_journal_template_header():
    journal = _read(_JOURNAL)
    assert "本评估的卖出路径含四类参考之核对参考（report-conventions §8）。" in journal


def test_d1_journal_template_no_action_words():
    """评估输出模板节含嵌套 `## 参考点独立性核对`（模板代码块内），
    用显式边界切片避免行扫描截断。"""
    journal = _read(_JOURNAL)
    start = journal.index("## 评估输出模板")
    end = journal.index("## 复盘归因模板")
    tpl = journal[start:end]
    matches = _action_matches(tpl)
    assert matches == [], f"评估输出模板节动作词应为空，实际: {matches}"


# ---------------------------------------------------------------- D2 参考点独立性

def test_d2_journal_law4_four_dimensions():
    journal = _read(_JOURNAL)
    assert "卖出四维度（一致性/情绪检测/参考点独立性/机会成本）" in journal


def test_d2_reference_point_in_sell_section():
    journal = _read(_JOURNAL)
    sec = _section(journal, "## 卖出评估维度（4 维）")
    assert "### 3. 参考点独立性核对（Reference-Point Check）" in sec
    assert "### 4. 机会成本（Opportunity Cost）" in sec
    assert sec.index("### 3. 参考点独立性核对") < sec.index("### 4. 机会成本")
    # 四问 + 关键问题 + 独立依据
    assert "浮盈目标 / 回本心理 / 亏损不甘 / 成本价锚定" in sec
    assert '关键问题："如果这笔交易不是你的持仓，你还会做这个决定吗？"' in sec
    assert "决策独立依据：{逻辑失效 / 估值触发 / 信号反转 / 资金面变化 / 其他}" in sec


def test_d2_self_check_and_output_template():
    journal = _read(_JOURNAL)
    assert "9. ✅ 检查 D2：卖出评估包含参考点独立性核对（四问 + 关键问题 + 独立依据）" in journal
    assert "## 参考点独立性核对（卖出路径必填；买入路径跳过）" in journal
    assert "卖出路径：情绪化检测后执行参考点独立性核对" in journal


def test_d2_evaluation_json_field():
    journal = _read(_JOURNAL)
    assert "reference_point_check" in journal
    assert "anchored_to" in journal
    assert "independent_basis" in journal


# ---------------------------------------------------------------- D4 trade-structure 3 段

def test_d4_trade_structure_three_segments():
    ts = _read(_TRADE_STRUCTURE)
    assert "为 3 段参考，不设触发条件/比例" in ts
    assert "3 段：悲观锚区/中性-悲观区/中性锚区" in ts
    assert "进入该区间意味着什么（状态含义）" in ts
    # 模板表头含状态含义列
    assert "| 情景锚定 | 价格区间 | 对应估值 | 假设前提 | 进入该区间意味着什么（状态含义） |" in ts
    # 负断言：D4 删除乐观溢价区行与盈亏比列
    assert "乐观溢价区" not in ts
    assert "盈亏比" not in ts


def test_d4_conventions_62_three_segments():
    conv = _read(_CONVENTIONS)
    sec = _section(conv, "### 6.2 交易结构分析")
    for key in ("悲观锚区", "中性-悲观区", "中性锚区", "状态含义", "不设触发条件/比例"):
        assert key in sec


# ---------------------------------------------------------------- D5 离场理由规范

def test_d5_q3_has_valuation_trigger():
    """方式 B 已落地：Q3 错误条件含估值触发选项。"""
    journal = _read(_JOURNAL)
    assert "| B | 估值触发 |" in journal


def test_d5_float_profit_warning_template():
    journal = _read(_JOURNAL)
    assert journal.count("锚定浮盈目标可能复制过早卖盈偏误") == 2  # Q3 提示 + 卖出维度节头
    for snippet in (
        "Odean 1998：卖出的盈利股次年跑赢持有的亏损股 3.4%",
        "整体可显著降低处置效应",
        "请改述为逻辑失效条件：什么情况下你原来的买入假设不成立了？",
    ):
        assert journal.count(snippet) == 2, f"snippet 应出现 2 次: {snippet}"


def test_d5_no_overdecomposed_wording():
    """负断言：不得包含把止盈单机制过度拆解的错误表述。"""
    journal = _read(_JOURNAL)
    assert "无显著改善" not in journal
    assert "止盈单对处置效应" not in journal
    conv = _read(_CONVENTIONS)
    assert '"涨到 X% 就卖"类浮盈目标卖出理由 — 提示按 Odean 1998 改述为逻辑失效条件（卖出评估场景）' in conv


# ---------------------------------------------------------------- D6 主线资金流确认

def test_d6_stock_skill_violation_row9():
    stock = _read(_STOCK)
    conv = _read(_CONVENTIONS)
    # 2026-08-14 上下文精简：stock SKILL.md 违规表仅留 stock 特有项（3a/7/8），
    # 通用项（含第 9 行 K 线断言）指针化至 report-conventions §3.2
    assert "通用违规模式（左侧/右侧、买卖建议、目标价、往往/通常、PE 分位、极度高估、K 线断言主线等）见 [report-conventions.md §3.2]" in stock
    assert "单用 K 线形态（连续上涨/突破均线）断言主线" in conv


def test_d6_pulse_and_conventions():
    pulse = _read(_PULSE)
    assert "主线确认：资金流/拥挤度为主证据" in pulse
    assert "Chui et al. 2022" in pulse
    assert '单用"连续上涨/突破均线"断言主线' in pulse
    conv = _read(_CONVENTIONS)
    assert "单用 K 线形态（连续上涨/突破均线）断言主线 — 违反主线资金流确认规则（A 股无动量，Chui et al. 2022）" in conv


# ---------------------------------------------------------------- D7 止损话术

def test_d7_conventions_replace_table():
    conv = _read(_CONVENTIONS)
    assert '"止损提高收益"/"止损提高胜率"' in conv
    assert "预设止损降低尾部风险与波动（Kaminski & Lo 2014），但可能牺牲期望收益；其价值在对抗'过久持亏'（行为矫正，Fischbacher et al. 2017）" in conv
    assert '"止损是最重要的纪律"（暗示收益增强）' in conv


def test_d7_violation_and_journal_self_check():
    conv = _read(_CONVENTIONS)
    assert '宣称"止损提高收益/胜率" — 无实证支持（Kaminski & Lo 2014：随机游走下止损必然降低期望收益）' in conv
    journal = _read(_JOURNAL)
    assert '"止损提高收益"' in journal


# ---------------------------------------------------------------- D8 观念修正内置

def test_d8_fact_boundary_item6():
    conv = _read(_CONVENTIONS)
    assert '禁止断言"无低估价值股/价值投资已失效"' in conv
    assert "识别成本高于趋势投资的成本决策" in conv


def test_d8_violation_item11_and_pulse_confirmation():
    conv = _read(_CONVENTIONS)
    assert '"无低估价值股/价值投资已失效"类断言（C3）' in conv
    pulse = _read(_PULSE)
    assert "企稳确认：{confirmation — True / False / None}" in pulse
    assert "筹码出清度输出含企稳确认字段（confirmation：True/False/None，缺失时标注原因）" in pulse


# ---------------------------------------------------------------- 版本一致性（动态防漂移）

def test_doc_versions_match_pyproject():
    """v0.2.5 版本头同步：report-conventions 版本头 / journal badge / pulse 声明。"""
    pyproject = (_REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    m = re.search(r'version = "([^"]+)"', pyproject)
    assert m, "pyproject.toml 缺少 version 字段"
    ver = m.group(1)

    conv = _read(_CONVENTIONS)
    assert f"版本：v{ver}" in conv
    journal = _read(_JOURNAL)
    assert f"invest-a-journal v{ver}" in journal
    pulse = _read(_PULSE)
    assert f"invest-a-pulse v{ver}" in pulse
