"""report_qc — 报告文本质量门禁（v0.2.8 R-A1/R-A2）。

可读性指标组：
  - 篇幅（total_chars，正文文本字符数）
  - 长句比例（句子按中文句末符切分；长句定义见 READABILITY_LONG_SENT_CHARS）
  - 术语密度（金融术语 glossary 命中数 / 正文千字符）
  - 结论摘要要素（数据-逻辑-分歧-风险四要素，缺失即 error）
结论段证据等级（R-A2）：
  - 「主要结论/结论」段内每条断言须带证据标签（[来源: / [证据: / [证据强度:）；
    全部断言无 ≥C 级证据（[A-D] 或 四维标签）时 error。
服务层标注为引擎计算输出，禁止任何"目视估算"入口。
"""
from __future__ import annotations

import re
from dataclasses import dataclass


READABILITY_MAX_CHARS = 20_000          # 篇幅上限（字符）
READABILITY_LONG_SENT_CHARS = 45       # 长句阈值（字符）
READABILITY_LONG_RATIO_WARN = 0.30     # 长句占比告警阈值
_TERM_GLOSSARY = {
    "趋势", "动能", "资金流", "估值", "分位", "净利差", "毛利率", "净利率", "ROE",
    "ROIC", "WACC", "FCF", "FCFF", "DCF", "同比", "环比", "汇率", "PMI", "CPI",
    "PPI", "VIX", "SOX", "北向", "两融", "基差", "β", "beta", "复合增速",
    "(EP|PE|PB|PS)(TTM)?", "折溢价", "席位", "龙虎榜", "胜率", "赔率",
}

_CONCLUSION_HEAD_RE = re.compile(r"^#{2,3}\s*(主要|核心)?结论", re.M)
_SENT_SPLIT_RE = re.compile(r"[。！？!?]")
_EVIDENCE_TAG_RE = re.compile(r"\[(来源|证据|证据强度)\s*[:：]")
_FACT_MARK_RE = re.compile(r"\[事实\]")
_ANALYSIS_MARK_RE = re.compile(r"\[分析\]")
_SECTION_HEAD_RE = re.compile(r"^#{2,4}\s")   # 全量审查：#### 情景子标题亦为节边界
_FACT_LOOKBACK_LINES = 50   # 与 lint structure-analysis-without-fact 同规则

# 全量审查 #3（P0-2）：畸形字符类 [来源:|[-−]?… 修复（原内容意外跨越
# '['-'-' 码位区间——过宽）；词族与真实模板措辞对齐（含条件词「若…则」）
_SUMMARY_ELEMS = {
    "数据": re.compile(r"(?:来源|数据|数值|同比|环比)|[−-]?\d+(?:\.\d+)?(?:%|亿|万|元|倍|x|X)?"),
    "逻辑": re.compile(r"因为|由于|因此|所以|分析|意味着|表明|映射|传导|解释|佐证|支撑|推断|归因|若|如果"),
    "分歧": re.compile(r"分歧|争议|不同观点|不同解读|矛盾|相反|另类路径|不确定性来源"),
    "风险": re.compile(r"风险|不确定性|警示|关注点|留意|注意|制约|下行|回撤|假设失效|承压"),
}
# 结论段结构行（表行/引用/分隔/标题/代码围栏）不算断言（全量审查：FP 源）
_STRUCT_LINE_RE = re.compile(r"^(\||>|---|```|#{2,})")


def _evidence_ge_c(ln: str) -> bool:
    """断言证据等级 ≥C（全量审查 P0-2：死代码「tagged==0 且无 out」不可达——
    tagged==0 时 out 必有内容。改为逐行判定：来源标注（可核验）或 [证据: A/B/C]
    或四维强度 ✅ 视为 ≥C；[证据: D] / ❓ 强度为 <C）。"""
    if re.search(r"\[来源\s*[:：]", ln):
        return True
    m = re.search(r"\[证据\s*[:：]\s*([A-Da-d])", ln)
    if m:
        return m.group(1).upper() in ("A", "B", "C")
    if re.search(r"\[证据强度\s*[:：]\s*✅", ln):
        return True
    return False


@dataclass
class QcFinding:
    line: int
    rule_id: str
    severity: str   # error / warning / info
    message: str
    context: str = ""


def _body_lines(md: str) -> list[str]:
    """去掉命令/引用外的纯正文行（标题也算正文）。"""
    return [ln for ln in md.splitlines() if ln.strip() and not ln.lstrip().startswith(("#", ">", "|", "```"))]


def readability_metrics(md: str) -> dict:
    """可读性指标组（全 Python 引擎计算）。"""
    body = "\n".join(_body_lines(md))
    total_chars = len(body)

    sentences = [s for s in _SENT_SPLIT_RE.split(body) if s.strip()]
    if not sentences:
        sentences = [body]
    long_ratio = sum(1 for s in sentences if len(s) > READABILITY_LONG_SENT_CHARS) / len(sentences)

    term_hits = 0
    for pat in _TERM_GLOSSARY:
        term_hits += len(re.findall(pat, body, re.IGNORECASE))
    term_density = round(term_hits / total_chars * 1000, 2) if total_chars else 0.0

    # 结论摘要要素：在「主要/核心结论」段内查找；无结论段标题 → 不判缺
    # （全量审查：旧实现无标题也报缺要素——对前置引擎输出假阳性）
    summary_elements = {k: False for k in _SUMMARY_ELEMS}
    m = _CONCLUSION_HEAD_RE.search(md)
    if m:
        tail = md[m.end():]
        next_head = re.search(r"^#{2,4}\s", tail, re.M)
        seg = tail if not next_head else tail[: next_head.start()]
        for k, pat in _SUMMARY_ELEMS.items():
            summary_elements[k] = bool(pat.search(seg))
    else:
        summary_elements = {k: None for k in _SUMMARY_ELEMS}  # 无结论段 → 未知

    return {
        "total_chars": total_chars,
        "sentences": len(sentences),
        "long_sentence_ratio": round(long_ratio, 4),
        "term_density_permille": term_density,
        "summary_elements": summary_elements,
    }


def conclusion_evidence_findings(md: str) -> list[QcFinding]:
    """R-A2：结论段逐条断言须带证据标签；<C 级证据的断言不得进入结论段。

    全量审查 P0-2 修复：
    - 标题支持「核心结论」（真实模板 `## 核心结论`——旧 regex 只匹配主要/结论
      → 210/210 真实报告未检到结论段）
    - 死代码移除：旧「tagged==0 且 not out」不可达（tagged==0 → out 必有行）——
      改为逐行 _evidence_ge_c 判定，D 级/未达标行报 level error
    - 结构行（| 表行/> 引用/---/#### 标题/```）排除——旧实现把表行/引用/
      情景子标题当断言（FP 源）
    - 段边界含 ####（乐观/悲观情景子标题内容不再误扫）
    """
    out: list[QcFinding] = []
    m = _CONCLUSION_HEAD_RE.search(md)
    if not m:
        return out
    tail = md[m.end():]
    nxt = re.search(r"^#{2,4}\s", tail, re.M)
    seg = tail if not nxt else tail[: nxt.start()]
    line_base = md[: m.end()].count("\n") + 1
    lines = seg.splitlines()
    weak_lines: list[tuple[int, str]] = []
    total = 0
    for i, ln in enumerate(lines):
        stripped = ln.strip()
        if not stripped or _STRUCT_LINE_RE.match(stripped):
            continue
        total += 1
        has = bool(_EVIDENCE_TAG_RE.search(ln))
        if has:
            if not _evidence_ge_c(ln):
                weak_lines.append((line_base + i, stripped[:80]))
        else:
            out.append(QcFinding(
                line=line_base + i,
                rule_id="wording-conclusion-evidence",
                severity="error",
                message="结论段断言缺少证据标签（[来源: / [证据: / [证据强度:）——无 ≥C 级证据的断言不得进入结论段（R-A2）",
                context=stripped[:80],
            ))
    if weak_lines:
        lines_txt = "；".join(
            f"L{ln}: {ctx}" for ln, ctx in weak_lines[:3])
        out.append(QcFinding(
            line=weak_lines[0][0],
            rule_id="wording-conclusion-evidence-level",
            severity="error",
            message=("结论段存在 <C 级证据断言（D 级/未标等级）——不满足"
                     "「无 ≥C 级证据不入结论段」，标注「证据弱，仅作观察」（R-A2）"
                     f"：{lines_txt}"),
            context=seg[:80],
        ))
    return out


def fact_analysis_pair_findings(md: str) -> list[QcFinding]:
    """R-A6：[分析] 节段内须有前置 [事实] 块（对偶强制）。

    与 lint `structure-analysis-without-fact` 同规则：50 行回溯、遇
    ##/### 节段边界停止（跨节段的 [事实] 不满足本节的 [分析]）。
    """
    out: list[QcFinding] = []
    lines = md.splitlines()
    for i, ln in enumerate(lines):
        if not _ANALYSIS_MARK_RE.search(ln):
            continue
        found = False
        for j in range(i - 1, max(i - 1 - _FACT_LOOKBACK_LINES, -1), -1):
            if _SECTION_HEAD_RE.match(lines[j]):
                break
            if _FACT_MARK_RE.search(lines[j]):
                found = True
                break
        if not found:
            out.append(QcFinding(
                line=i + 1,
                rule_id="structure-fact-analysis-pair",
                severity="error",
                message=f"[分析] 节段内缺少前置 [事实] 块（{_FACT_LOOKBACK_LINES} 行回溯）——[事实]→[分析] 对偶强制（R-A6）",
                context=ln.strip()[:80],
            ))
    return out


def readability_findings(md: str) -> list[QcFinding]:
    met = readability_metrics(md)
    out: list[QcFinding] = []
    if met["total_chars"] > READABILITY_MAX_CHARS:
        out.append(QcFinding(0, "readability-length", "error",
                             f"正文篇幅 {met['total_chars']} 字符超限（>{READABILITY_MAX_CHARS}）"))
    if met["long_sentence_ratio"] > READABILITY_LONG_RATIO_WARN:
        out.append(QcFinding(0, "readability-long-sentence", "warning",
                             f"长句占比 {met['long_sentence_ratio']:.1%}（阈值 {READABILITY_LONG_RATIO_WARN:.0%}）"))
    missing = [k for k, v in met["summary_elements"].items() if v is False]
    if missing:
        # 全量审查：真实模板措辞（如「…分歧…若…则…」条件结构）已纳入词族——
        # 仍缺 1 项降 warning（可能为措辞风格而非结构缺失），缺 ≥2 项 error
        sev = "warning" if len(missing) == 1 else "error"
        out.append(QcFinding(0, "readability-summary-elements", sev,
                             f"主要结论段缺少要素：{('、'.join(missing))}——要求'数据-逻辑-分歧-风险'四要素齐全"))
    return out


def run_report_qc(md: str) -> list[QcFinding]:
    return readability_findings(md) + conclusion_evidence_findings(md) + fact_analysis_pair_findings(md)


def format_report_qc(findings: list[QcFinding]) -> str:
    if not findings:
        return "✅ report_qc 通过（可读性 + 结论证据等级）"
    lines = [f"❌ report_qc 发现 {len(findings)} 项:"]
    for f in findings:
        loc = f"L{f.line}" if f.line else ""
        lines.append(f"- {loc} [{f.rule_id}] {f.message}")
    return "\n".join(lines)
