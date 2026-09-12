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

import pytest
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


def test_conventions_52_table_rows_wellformed():
    """§5.2 四维标注表每行须为 3 列（曾因编辑丢单元格致整行损坏、单元格文字外溢）。"""
    conv = _read(_CONVENTIONS)
    sec = _section(conv, "### 5.2 四维标注")
    rows = [ln for ln in sec.splitlines() if ln.strip().startswith("|")]
    assert rows, "§5.2 表格缺失"
    for ln in rows:
        cells = ln.strip().strip("|").split("|")
        assert len(cells) == 3, f"§5.2 行非 3 列（{len(cells)} 列）: {ln!r}"


def test_etf_skill_selfcheck_synced():
    """ETF SKILL Self-Check 含 v0.3.0 三项（F1/F2+F3/F4）且引用强制行为 7。"""
    etf_skill = _read("skills/invest-a-etf/SKILL.md")
    assert "证据标签未覆盖无来源前提" in etf_skill
    assert "派生数字（倍数/比例/百分点/点位差）带 `[来源: Python calc: formula]`" in etf_skill
    assert "正文 §N 交叉引用指向节含被引内容" in etf_skill
    assert "强制行为 5-7" in etf_skill


# ---------------------------------------------------------------- A1 登记义务（T11-6）

_SMOKE_SCRIPT = "scripts/smoke_interfaces.py"
_INTERFACE_MAP = "skills/lib/references/data-interface-map.md"


def _smoke_l1_names() -> list[str]:
    """冒烟 L1 清单的接口名字面量（纯文本解析，不导入模块——零依赖）。

    锚定**赋值语句**而非裸 `AK_INTERFACES`：docstring 里也提到该名字，
    按首次出现切分会切到注释区导致解析为空（实测踩坑）。
    """
    text = _read(_SMOKE_SCRIPT)
    marker = "AK_INTERFACES: list[str] = ["
    assert marker in text, "L1 清单赋值语句形态变化，请更新本解析"
    block = text.split(marker, 1)[1].split("]", 1)[0]
    return re.findall(r'"([a-z_][a-z0-9_]+)"', block)


def test_smoke_l1_list_parses():
    """前置自检：解析失败须显式红，不得因「解析到 0 项」而静默通过。"""
    names = _smoke_l1_names()
    assert len(names) >= 60, f"L1 清单解析异常，仅得 {len(names)} 项"
    assert len(set(names)) == len(names), "L1 清单存在重复项"


def test_data_interface_map_covers_smoke_l1():
    """A1 义务：L1 清单每个接口名都必须在地图内有登记行（防地图与清单脱同步）。"""
    text = _read(_INTERFACE_MAP)
    missing = sorted(n for n in _smoke_l1_names() if f"`{n}`" not in text)
    assert not missing, f"data-interface-map 缺登记（A1 义务）：{missing}"


def test_new_sources_registered_with_caliber_notes():
    """新增源须登记且**高危口径注记在位**——缺注记即回归为静默陷阱。"""
    text = _read(_INTERFACE_MAP)
    required = {
        "stock_hsgt_fund_flow_summary_em": ["语义未核"],
        "currency_boc_sina": ["每 100 港元"],
        "moneyflow_hsgt": ["累计口径", "差分"],
        "hk_tradecal": ["港股交易日历"],
        "stock_hsgt_hist_em": ["港股通沪", "恒 NaN"],
    }
    for name, tokens in required.items():
        assert f"`{name}`" in text, f"地图缺 {name} 登记行"
        row = next(ln for ln in text.splitlines()
                   if ln.startswith("|") and f"`{name}`" in ln)
        for tok in tokens:
            assert tok in row, f"{name} 登记行缺口径注记「{tok}」"


# ---------------------------------------------------------------- R-E02 斐波/波浪否定注记

_FIB_RULE = "wording-fib-no-negation"
_WAVE_RULE = "wording-wave-no-negation"
_TEMPLATE_KEYWORDS = ("市场习俗", "无样本外盈利证据", "Tsinaslanidis", "随机位点")


def test_r_e02_rules_registered():
    rules = _rules()
    for rid in (_FIB_RULE, _WAVE_RULE):
        rule = rules.get(rid)
        assert rule, f"compliance_rules.yaml 缺 {rid}"
        assert rule["severity"] == "error", f"{rid} 应为 error（引用习俗点位不给否定注记＝以实证面貌出现）"
        assert rule["scope"] == "line"
        assert rule.get("skip_if_pattern"), f"{rid} 须有豁免（否定式说明/免责行不应被拦）"
        assert rule.get("law_ref")


def test_r_e02_fib_template_present_in_conventions():
    conv = _read(_CONVENTIONS)
    assert "2.4.1" in conv and "斐波/波浪否定注记模板" in conv
    for kw in _TEMPLATE_KEYWORDS:
        assert kw in conv, f"§2.4.1 模板缺关键词：{kw}"
    assert "仅作展示参照" in conv
    assert "点位版本化" in conv, "模板须带快照日期 + 失效追踪（与 scenario-plans 对接）"


def test_r_e02_rules_behave_bidirectionally():
    """双向：引用习俗点位无注记 → 命中；带注记/否定式说明 → 不误伤。"""
    rules = _rules()

    def _flags(rid: str, text: str) -> bool:
        rule = rules[rid]
        if not re.search(rule["pattern"], text):
            return False
        skip = rule.get("skip_if_pattern")
        return not (skip and re.search(skip, text))

    for text in ("黄金分割 0.618 压力位在 3514", "38.2% 回撤位 3514", "斐波那契回撤 0.5"):
        assert _flags(_FIB_RULE, text), f"应命中：{text}"
    assert _flags(_WAVE_RULE, "三浪将止于 3514")
    assert _flags(_WAVE_RULE, "当前处于第 3 浪")
    assert _flags(_WAVE_RULE, "A浪结束")

    clean = ("该点位属市场习俗（L3），无样本外盈利证据（Tsinaslanidis 2022 与随机位点无差异；"
             "波浪不可证伪），仅作展示参照")
    assert not _flags(_FIB_RULE, clean), "模板全句不得误伤"
    assert not _flags(_WAVE_RULE, "波浪不可证伪，仅作展示参照")
    for ok in ("本报告不使用斐波/波浪工具", "新浪财经报道", "海浪发电概念",
               "收盘价 3514.20，成交额 5 亿 [来源: engine]"):
        assert not _flags(_FIB_RULE, ok) and not _flags(_WAVE_RULE, ok), f"误伤：{ok}"


# ---------------------------------------------------------------- R-E01/E03/E04（v0.3.0 R5）

_CONVENTION_RULES = ("wording-practitioner-convention",
                     "structure-convention-in-fact-block",
                     "wording-message-no-tristate")


def test_r_e03_e04_rules_registered():
    rules = _rules()
    for rid in _CONVENTION_RULES:
        rule = rules.get(rid)
        assert rule, f"compliance_rules.yaml 缺 {rid}"
        assert rule["severity"] in ("error", "warning")
        assert rule.get("skip_if_pattern"), f"{rid} 须有豁免（标注/免责行不应被拦）"
        assert rule.get("law_ref")


def test_r_e04_fact_block_rule_is_error_and_paragraph_scoped():
    """`[事实]` 块内出现惯例表述是**块级**问题——须 paragraph scope + error。"""
    rule = _rules()["structure-convention-in-fact-block"]
    assert rule["severity"] == "error", "惯例进 [事实] 块属事实性错误，须 error"
    assert rule["scope"] == "paragraph", "块级拦截不能用 line scope（跨行场景会漏）"


def test_r_e03_convention_template_documented():
    conv = _read(_CONVENTIONS)
    assert "3.5「从业者惯例」标注系统化" in conv or "### 3.5 「从业者惯例」标注系统化" in conv
    assert "[从业者惯例，非学术验证：" in conv, "模板须逐字在位"
    for token in ("不得置于 `[事实]` 块", "不得与学术证据同权重", "出处不可考"):
        assert token in conv, f"§3.5 缺约束：{token}"


def test_r_e01_message_tristate_documented():
    conv = _read(_CONVENTIONS)
    assert "消息三态与类型标注" in conv
    for token in ("事实", "传言", "证实", "注意力情绪型", "基本面型"):
        assert token in conv, f"强制 8 缺要素：{token}"
    assert "不预设" in conv, "须显式禁止预设方向语义"
    assert "热度见顶 ≠ 证实" in conv


def test_r_e04_selfcheck_has_new_items():
    conv = _read(_CONVENTIONS)
    sec = _section(conv, "### 7.1 通用检查项")
    for token in ("从业者惯例", "structure-convention-in-fact-block", "消息/传闻引用"):
        assert token in sec, f"§7.1 缺检查项：{token}"


def test_r_e01_prereg_registered():
    """R-E01 的「分类器验证方案文档化」= 预注册文件就位（B1+B2 的验证方案注册）。"""
    f = _REPO_ROOT / "skills/lib/references/backtest_prereg/C8_预注册.md"
    assert f.exists(), "缺 C8 预注册"
    text = f.read_text(encoding="utf-8")
    for token in ("H0", "H1", "滚动", "禁止全样本一次性结论", "显著性判据", "稳健性"):
        assert token in text, f"C8 预注册缺：{token}"
    assert "不产出结论" in text, "须声明本轮不产出验证结论（B1+B2）"


# ---------------------------------------------------------------- R5 预注册清单（B1+B2 验收）

_PREREG_DIR = "skills/lib/references/backtest_prereg"
# C 号 → 负责的档 B/C 条目。
# ⚠️ 本表**随各批次落地追加**（本轮共 7 份：C3/C7/C8/C10/C11/C12/C17）——
# 未落地前不入表，保证每个提交都是绿的；轮末门再核 7/7 齐备。
_PREREG_REQUIRED = {
    "C3": "R-A04 条件性反转观测窗",       # B5
    "C7": "R-A03 量价观察特征",           # B5
    "C8": "R-E01 消息三态分类器",         # B2
    "C12": "R-D03 可跟踪度",              # B3
    "C10": "R-B03 多概念拥挤度",          # B6
    "C11": "R-B01/B02 三特征 + 左右侧双组回测",   # B6
    # B8 追加：C17（R-F02 配置方法回测）
}


@pytest.mark.parametrize("cid,item", sorted(_PREREG_REQUIRED.items()))
def test_r5_prereg_registered(cid, item):
    """每条涉及规则/阈值的档 B/C 条目须有**冻结的预注册文件**（B1+B2 的「验证方案注册」）。"""
    f = _REPO_ROOT / f"{_PREREG_DIR}/{cid}_预注册.md"
    assert f.exists(), f"缺 {cid} 预注册（对应 {item}）"
    text = f.read_text(encoding="utf-8")
    for token in ("H0", "显著性判据", "稳健性"):
        assert token in text, f"{cid} 预注册缺「{token}」"
    assert "不产出结论" in text, f"{cid} 须声明本轮不产出验证结论（B1+B2）"


def test_r5_prereg_rows_backfilled_in_registry():
    """预注册落地后须回写假设注册表状态（否则注册表与实物脱同步）。"""
    reg = _read("skills/lib/references/hypothesis-registry.md")
    for cid in _PREREG_REQUIRED:
        assert f"backtest_prereg/{cid}_预注册.md" in reg, f"注册表 {cid} 行未回写预注册路径"
