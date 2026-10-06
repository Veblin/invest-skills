"""evidence_tags.py — SOP-EV 证据标签的 canonical 解析规则（共享单一来源）。

R12 round-5（2026-10-04 第五次独立复检后收紧）：round-4 的四维语法把来源/时效/
交叉维度设为可省、注解词表在各图标间复用，导致六类边界仍被放过（缺维度纯标签、
图标/注解矛盾、中文括号/键空格包装内的嵌套来源或等级）。本版规则：

- **方括号 SOP 四维标签**（`[证据强度: …]`，ASCII 方括号）必须
  **四维齐全、固定顺序、图标与注解一一配对**：
    强度（必）✅强 / ⚠中 / ❓弱
    来源（必）🌐多源 / 📡单源 / 🔮推测
    时效（必）🕐近30日 / 📅近季度 或 📅报告期已注明 / 🗄️滞后>1年
    交叉（必）✓✓（一致）/ ✓✗（有差异）/ —（无验证），可带对应注解
  缺维度、重复维度、图标与注解矛盾（如 `🌐单源`、`🕐滞后 >1 年`）一律**不合法**
  ——不享受掩码/结构行豁免（fail-closed：其中的数字照常要求绑定；作为断言行扫描）。
- **单维引擎元数据**不用方括号标签表达（`[证据强度: ⚠️ 中]` 不是合法四维标签），
  改走既有渲染器体例 `**证据强度：⚠️ 中**`（`_is_render_strength`，另属一档）。
- **命名空间识别**（证据判定剥离用）：ASCII `[`、中文 `【`、全角 `［` 开括号，
  键与冒号前后允许空白，冒号允许 `:`/`：`；配平（含嵌套）截到对应闭括号，未闭合
  截到行尾。非法标签/包装内部的 `[来源:]`/`[证据:]`/`[事实:]` 不是正文证据；
  合法标签之外的真实来源/事实绑定照常通过。

消费方：`analysis_schema`（数字绑定掩码）、`report_qc`（结构行豁免 / 证据判定 /
渲染器行语法）。禁止各自复制正则（防漂移）。
"""
from __future__ import annotations

import re

# 行内空白（不得跨行——标签一律单行）
_WS = r"[^\S\n]*"
_VS16 = r"️?"

# ── 四个维度：图标与注解一一配对 ──────────────────────────────────────────
# 强度：✅强 / ⚠中 / ❓弱
STRENGTH_DIM_PATTERN = (
    r"(?:✅" + _VS16 + _WS + r"强|⚠" + _VS16 + _WS + r"中|❓" + _VS16 + _WS + r"弱)"
)
# 来源：🌐多源 / 📡单源 / 🔮推测
SOURCE_DIM_PATTERN = (
    r"(?:🌐" + _VS16 + _WS + r"多源|📡" + _VS16 + _WS + r"单源|🔮" + _VS16 + _WS + r"推测)"
)
# 时效：🕐近30日 / 📅近季度|报告期已注明 / 🗄️滞后>1年
TIMING_DIM_PATTERN = (
    r"(?:🕐" + _VS16 + _WS + r"近" + _WS + r"30" + _WS + r"日"
    r"|📅" + _VS16 + _WS + r"(?:近季度|报告期已注明)"
    r"|🗄" + _VS16 + _WS + r"滞后" + _WS + r">?" + _WS + r"1" + _WS + r"年)"
)
# 交叉：✓✓（一致）/ ✓✗（有差异）/ —（无验证），注解可省但须配对
CROSS_DIM_PATTERN = (
    r"(?:✓✓" + _WS + r"(?:跨源一致|多源一致)?"
    r"|✓✗" + _WS + r"(?:源间有差异)?"
    r"|—" + _VS16 + _WS + r"(?:单源无验证)?)"
)

# 完整四维序列（无括号；供渲染器行尾 tail 复用）
FOUR_DIM_SEQUENCE_PATTERN = (
    STRENGTH_DIM_PATTERN + _WS + SOURCE_DIM_PATTERN + _WS
    + TIMING_DIM_PATTERN + _WS + CROSS_DIM_PATTERN
)

# analysis.evidence_tag 与渲染等级行共用：裸等级或等级+完整四维序列。
EVIDENCE_LABEL_PATTERN = (
    r"(?:[A-Da-d]{1,2}|[Ll][1-4])(?:" + _WS + FOUR_DIM_SEQUENCE_PATTERN + r")?"
)
EVIDENCE_LABEL_RE = re.compile(EVIDENCE_LABEL_PATTERN)

# 方括号 SOP 四维标签（ASCII 方括号；键与冒号间可空白）
FOUR_DIM_TAG_PATTERN = (
    r"\[证据强度[:：]" + _WS + FOUR_DIM_SEQUENCE_PATTERN + _WS + r"\]"
)
FOUR_DIM_TAG_RE = re.compile(FOUR_DIM_TAG_PATTERN)
# 整行恰为四维标签（结论段结构行豁免用）
FOUR_DIM_TAG_LINE_RE = re.compile(r"^" + _WS + FOUR_DIM_TAG_PATTERN + _WS + r"$")

# 单维强度（渲染器 `**证据强度：⚠️ 中**` 头）与引擎「数据不足」哨兵
RENDER_STRENGTH_TOKEN_PATTERN = (
    r"(?:" + STRENGTH_DIM_PATTERN + r"|" + _WS + r"(?:强|中|弱)|数据不足)"
)

# 「像标签」的 ASCII 跨度（掩码函数只对其中完全合语法者生效——不合语法者保留
# 原文，其数字继续受绑定校验，fail-closed）。中文/全角括号形态不在合法语法内，
# 故也永不掩码；证据判定侧的剥离另由 strip_strength_tag_spans 覆盖全部括号形态。
_TAG_SPAN_RE = re.compile(r"\[证据强度[:：][^\[\]\n]*\]")

# 命名空间起点：覆盖 ASCII/中文/全角开括号、键与冒号前后空白
_NS_START_RE = re.compile(r"(?:\[|【|［)" + _WS + r"证据强度" + _WS + r"[:：]")
_OPENERS = ("[", "【", "［")
_CLOSERS = ("]", "】", "］")


def is_four_dim_tag(text: str) -> bool:
    """整行是否恰为合法四维标签（四维齐全、固定顺序、图标-注解配对）。"""
    return bool(FOUR_DIM_TAG_LINE_RE.match(text))


def mask_four_dim_tags(text: str) -> str:
    """把**完全合法**的四维标签跨度替换为等长空格（span 保持不变）。

    仅完整匹配四维语法的 ASCII 标签被掩码；夹带正文/数字/缺重复维度/图标注解
    矛盾的 `[证据强度: …]` 原样保留——其中的数字须照常通过事实绑定。
    """
    def _sub(m: re.Match[str]) -> str:
        if FOUR_DIM_TAG_RE.fullmatch(m.group(0)):
            return " " * len(m.group(0))
        return m.group(0)

    return _TAG_SPAN_RE.sub(_sub, text)


def strip_strength_tag_spans(line: str) -> str:
    """去掉行内全部 `证据强度` 命名空间（含非法/嵌套/中英文括号形态），等长空格。

    证据判定用：命名空间内部的 `[来源:]`/`[证据:]`/`[事实:]` 只是标签内文字，
    不构成正文证据（R12 复检反例：伪造来源/等级包入标签、中文括号包装、键空格
    变体）。按括号配平截到对应闭括号（支持嵌套与混合括号，含 `【…】`/`［…］`）；
    未闭合的截到行尾（fail-closed）。
    """
    out: list[str] = []
    i, n = 0, len(line)
    while i < n:
        m = _NS_START_RE.search(line, i)
        if not m:
            out.append(line[i:])
            break
        out.append(line[i:m.start()])
        j, depth = m.start(), 0
        while j < n:
            ch = line[j]
            if ch == "\n":
                break
            if ch in _OPENERS:
                depth += 1
            elif ch in _CLOSERS:
                depth -= 1
                if depth <= 0:
                    j += 1
                    break
            j += 1
        out.append(" " * (j - m.start()))
        i = j
    return "".join(out)


__all__ = [
    "CROSS_DIM_PATTERN",
    "FOUR_DIM_SEQUENCE_PATTERN",
    "FOUR_DIM_TAG_LINE_RE",
    "FOUR_DIM_TAG_PATTERN",
    "FOUR_DIM_TAG_RE",
    "RENDER_STRENGTH_TOKEN_PATTERN",
    "SOURCE_DIM_PATTERN",
    "STRENGTH_DIM_PATTERN",
    "TIMING_DIM_PATTERN",
    "is_four_dim_tag",
    "mask_four_dim_tags",
    "strip_strength_tag_spans",
]
