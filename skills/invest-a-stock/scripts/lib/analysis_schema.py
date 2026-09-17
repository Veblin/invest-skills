"""analysis.json schema + 校验器（v0.2.8 R-B1 分析协议）。

段结构: [{module, title, facts_md, analysis_md, evidence_tag, position}]
校验：必填字段、长度、evidence_tag 模式（A-D 等级或 四维标签起始）、
markdown 子集（复用 lib.md_subset 的 fail-loud 判定，不支持语法即 error）。
与 md 产物同目录并存：reports/{symbol}-{name}/{ts}.analysis.json
"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any

from lib.md_subset import MarkdownSubsetError, render_markdown

REQUIRED_FIELDS = ("module", "title", "facts_md", "analysis_md", "evidence_tag", "position")
MAX_LEN = {"module": 64, "title": 128, "facts_md": 20_000, "analysis_md": 40_000, "evidence_tag": 32, "position": 64}
POSITION_ALLOWED = {"overview", "valuation", "financials", "technicals", "northbound",
                    "holders", "events", "refs", "research", "conclusion", "analysis"}
_EVIDENCE_RE = re.compile(r"^([A-Da-d]{1,2}|[Ll][1-4])")

# --- 事实绑定（v0.3.1 #4）------------------------------------------------------
# 缺陷记录：此前 analysis.json **无 fact_id 字段**，段内数字未经任何来源校验
# （SKILL.md 自认「可审计性止于『来源 + 同代绑定 + 段级证据等级』」）——Claude 写的
# 任意数字可直接进入最终 MD/HTML，P0（一切数字经 Python）在协议层无技术保证。
#
# 本版引入**可选** `facts` 数组（向后兼容：不带 facts 的段照常通过），有 facts 时强制：
#   1. id 唯一非空；value 必为数值
#   2. formula 若给出 → **必须能被安全求值，且结果等于 value**
#      （直接拦截「标了公式但公式算不出这个数」= §2.3 强制 5 的「未实跑标注」）
#   3. facts_md/analysis_md 中的 `[事实: F1]` 引用必须在本段 facts 内存在（防悬空引用）
#   4. facts_md/analysis_md 中出现的数字**必须**能对上某个 fact 的 value，
#      或属于豁免集（年份/日期/期数/评级等结构性数字）
#
# 未覆盖的剩余缺口（如实记录，不假装已闭环）：facts **不校验** `[来源: 引擎字段]`
# 标签指向的字段是否真实存在于当次采集——那需要 report 期把 collection 注入校验
# （见 v0.3.1 requirements AH 后续项）。
#
# 2026-09-18 review #3：本组闸门此前**在生产路径恒不触发**——schema 文档（SKILL.md）
# 没有 facts 键、四个 agent prompt 教的是 `[事实: F{n}]` 而这里只认 `[[F{n}]]`、
# 且没有任何生产者写 analysis.json 的 facts。三处不一致已统一为 prompt 的书写形态
# `[事实: F1]`（报告中自解释；`[[F1]]` 对读者是不可读的）。改动须三处同步。
_FACT_REF_RE = re.compile(r"\[事实\s*[:：]\s*(F\d+)\]")
_FACT_TOL = 1e-6
_MAX_FACTS = 200

# 正文数字豁免：这些形态不是「加工出来的数值」，不该要求绑定 fact。
# 年/月/日/期数/序号、百分比符号后的单位词等结构性数字。
# 千分位书写（`12,345`）算**一个** token：它是实质量值，须照常绑定 fact——
# 切碎成 "12"/"345" 会让两侧都匹配不上（v0.3.0 D2 误报根因）。
_NUM_TOKEN_RE = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?")

# 结构性形态的**整段**掩码（v0.3.0 A1）：逐 token 判断无法把 `2026-09-17` 的
# 首/尾片段与 `9-10 倍` 这类数值区间区分开，故先把这些形态整体掩掉再逐 token
# 校验。三个分支都要求「不可能与普通数值混淆」的形状：4 位年 + 月/日、带 v 的
# 版本号、两段以上点号的版本号——`12.5` 只有一段点号，不受影响，仍须绑定。
_MASKED_STRUCTURAL_RE = re.compile(
    # 年份区间须排在 ISO 之前：`2020-2024` 会被 ISO 分支吃成 `2020-20`，
    # 残留 `24` 报未绑定（review #4 实测）。区间要求两侧各 4 位，故
    # `9-10 倍` 这类数值区间不受影响。
    r"\d{4}\s*[-–~]\s*\d{4}"     # 年份区间：2020-2024
    r"|\d{4}-\d{2}(?:-\d{2})?"   # ISO 日期：2026-09-17 / 2026-09
    r"|\d{4}年\d{1,2}月(?:\d{1,2}日)?"   # 中文日期：2026年9月17日 / 2026年9月
    r"|\d{4}[Hh]\d"              # 报告期：2026H1
    r"|\d{1,2}:\d{2}"            # 时刻：09:30
    r"|v\d+(?:\.\d+)+"           # v0.3.1
    r"|\d+(?:\.\d+){2,}"         # 0.3.1（无 v 前缀）
)

# URL 内的数字不要求绑定 fact——但**只豁免 URL 自身跨度内的 token**。
# 早期实现写的是「该行出现过 http 即豁免整行」（review #10 实测：
# `来源 https://x.com/p/123 该季增长 999%` 的 999 被整行放过，与本函数
# docstring「不能豁免整行」自相矛盾，且放过的恰是 P0 要拦的编造数字）。
_URL_RE = re.compile(r"https?://\S+")


class _FormulaError(ValueError):
    pass


def _safe_eval_formula(expr: str) -> float:
    """仅允许 数字 + ``+ - * / ** ()`` 的算术表达式求值。

    用 ``ast`` 白名单而非 ``eval``（禁属性访问/调用/下标/名字查找）。
    越界 → ``_FormulaError``（fail-loud，由调用方转成校验错误）。
    """
    import ast as _ast

    src = str(expr or "").strip()
    if not src:
        raise _FormulaError("公式为空")
    try:
        tree = _ast.parse(src, mode="eval")
    except SyntaxError as exc:
        raise _FormulaError(f"公式不可解析: {exc.msg}") from exc

    def _finite(value: float) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise _FormulaError("公式结果非实数")
        if not math.isfinite(value):
            raise _FormulaError("公式结果非有限数")
        return float(value)

    def _ev(node):
        if isinstance(node, _ast.Expression):
            return _ev(node.body)
        if isinstance(node, _ast.Constant):
            if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
                raise _FormulaError("公式含非数值常量")
            # review #13：超大整数字面量（`"9"*400`）在 float() 处抛 OverflowError，
            # 而本分支原本不在 BinOp 的 except 覆盖内 → 异常穿透 _FormulaError，
            # 调用方只捕 _FormulaError/AnalysisSchemaError → traceback 或
            # error 级 completion-analysis-sidecar-invalid。同函数 Pow 分支有兜底，
            # 此处漏了。
            try:
                return _finite(float(node.value))
            except OverflowError as exc:
                raise _FormulaError("公式常量超出浮点范围") from exc
        if isinstance(node, _ast.BinOp):
            a, b = _ev(node.left), _ev(node.right)
            op = node.op
            try:
                if isinstance(op, _ast.Add):
                    return _finite(a + b)
                if isinstance(op, _ast.Sub):
                    return _finite(a - b)
                if isinstance(op, _ast.Mult):
                    return _finite(a * b)
                if isinstance(op, _ast.Div):
                    if b == 0:
                        raise _FormulaError("公式除零")
                    return _finite(a / b)
                if isinstance(op, _ast.Pow):
                    return _finite(a ** b)
            except OverflowError as exc:
                raise _FormulaError("公式算术溢出") from exc
            except ArithmeticError as exc:
                raise _FormulaError("公式算术错误") from exc
            raise _FormulaError(f"不允许的运算符: {type(op).__name__}")
        if isinstance(node, _ast.UnaryOp) and isinstance(node.op, (_ast.UAdd, _ast.USub)):
            v = _ev(node.operand)
            return v if isinstance(node.op, _ast.UAdd) else -v
        raise _FormulaError(f"不允许的语法节点: {type(node).__name__}")

    return _ev(tree)


def _as_number(v):
    """→ float；非数值 → None（不做 str→float 的宽松解析，防把 'F1' 当数字）。"""
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    return None


def _written_decimals(value) -> int | None:
    """value 的**书写小数位数**（用于按精度判定公式复算是否一致）。

    报告里的数字是四舍五入后写出的（``-36.7`` 背后是 ``-36.7193…``），故要求
    公式结果与 value 在「书写精度」上相等，而不是要求逐位相等——后者会把所有
    正常四舍五入的数字判成错误。科学计数法（1e-07）无处谈精度 → None（回退到
    相对容差）。

    **整数书写（JSON int）→ 0 位，含义是「精确」**（review #14）：原实现用
    ``repr(float(18))`` == ``'18.0'`` 推精度，得到 1 位小数 → 取整窗口 ±0.05，
    把「公式必须算出该数」放松成「公式落在该数附近」——`value=18` 配
    `formula="18.04"` 会被判为复算一致，编造公式可过闸。
    """
    if isinstance(value, int) and not isinstance(value, bool):
        return 0
    txt = repr(float(value))
    if "e" in txt or "E" in txt or "." not in txt:
        return None
    return len(txt.split(".")[1])


def _formula_matches(value, got: float) -> bool:
    """公式结果是否与 value 一致（按书写精度，回退相对容差）。

    ``value`` 取 facts 里的**原始** JSON 值（不是 float 化后的），否则整数书写
    的精度信息已丢失。
    """
    num = _as_number(value)
    if num is None:
        return False
    d = _written_decimals(value)
    if d == 0:
        # 整数书写 → 要求精确（相对容差），**不得**用 round(got, 0) 的 ±0.5 窗口
        scale = max(abs(num), 1e-9)
        return abs(got - num) / scale <= _FACT_TOL
    if d is not None:
        return round(got, d) == round(num, d)
    scale = max(abs(num), 1e-9)
    return abs(got - num) / scale <= _FACT_TOL


def _validate_facts(sec: dict) -> tuple[list[str], set[str]]:
    """可选 ``facts`` 校验。无 facts → 无错（向后兼容）。

    返回 ``(错误, 本段已声明 fact id 集合)``——**不写回 sec**（D7：不修改传入对象；
    段字典来自 ``load_analysis_json`` 的共享结构）。
    """
    facts = sec.get("facts")
    if facts is None:
        return ([], set())
    if not isinstance(facts, list):
        return (["facts:必须为数组"], set())
    if len(facts) > _MAX_FACTS:
        return ([f"facts:条数 {len(facts)} 超过上限 {_MAX_FACTS}"], set())

    errs: list[str] = []
    seen: set[str] = set()
    for i, fa in enumerate(facts):
        if not isinstance(fa, dict):
            errs.append(f"facts[{i}]:必须是对象")
            continue
        fid = str(fa.get("id") or "").strip()
        if not fid:
            errs.append(f"facts[{i}]:缺 id")
        elif fid in seen:
            errs.append(f"facts[{i}]:id 重复 {fid}")
        else:
            seen.add(fid)

        raw = fa.get("value")
        val = _as_number(raw)
        if val is None:
            errs.append(f"facts[{i}]({fid or '?'}):value 必为数值（收到 {fa.get('value')!r}）")
        formula = fa.get("formula")
        if formula is not None:
            try:
                got = _safe_eval_formula(str(formula))
            except _FormulaError as exc:
                errs.append(f"facts[{i}]({fid or '?'}):formula 不可求值（{exc}）")
            else:
                # 传**原始**值：整数书写（JSON int）的精度语义在 float() 后丢失（#14）
                if val is not None and not _formula_matches(raw, got):
                    errs.append(
                        f"facts[{i}]({fid or '?'}):formula 复算 {got!r} ≠ value {val!r}"
                        f"（公式 {formula!r} 算不出该数——禁止未实跑标注）")
    return (errs, seen)


def _validate_fact_refs(sec: dict, known: set[str]) -> list[str]:
    """``[事实: F1]`` 引用完整性：必须在本段 facts 内存在。

    ``known`` 为空集（段未声明 facts）时**不校验**——正文里的 `[事实: F1]` 可能是
    普通文本，向后兼容优先。
    """
    if not known:
        return []
    errs: list[str] = []
    for field in ("facts_md", "analysis_md"):
        for m in _FACT_REF_RE.finditer(str(sec.get(field) or "")):
            if m.group(1) not in known:
                errs.append(f"{field}:引用 [事实: {m.group(1)}] 不存在于本段 facts")
    return errs


def _is_exempt_num(text: str, match: re.Match[str]) -> bool:
    """结构性数字不要求事实绑定；仅豁免该 token，不能豁免整行。

    特别地，不能因一行里有「2026 年」或「来源」就放过该行的其它数字：那会让
    ``2026 年营收增长 999%`` 再次绕过事实校验。
    """
    token = match.group(0)
    start, end = match.span()
    before, after = text[:start], text[end:]
    prev = before[-1:]
    nxt = after[:1]

    # 日期的年/月/日片段，以及 URL、版本号中的数字。
    if (prev in {"-", "/", "."} and nxt in {"-", "/", "."}) or (
        prev in {"-", "/", "."} and nxt.isdigit()
    ) or (prev.isdigit() and nxt in {"-", "/", "."}):
        return True
    # `token.isdigit()` 先导：`_NUM_TOKEN_RE` 含小数，len==4 的 `12.5`/`36.7`
    # 曾直接进 int() 抛未捕获 ValueError —— invest.py 只捕 AnalysisSchemaError
    # → traceback；共享 report_qc 的 except Exception 把它转成 error 级
    # completion-analysis-sidecar-invalid（合格报告 exit 2 不得交付）
    # （v0.3.0 A1）。
    if (len(token) == 4 and token.isdigit()
            and 1900 <= int(token) <= 2200 and after.lstrip().startswith("年")):
        return True
    # URL 内跨度：只豁免**落在 URL 里**的 token，不是「本行出现过 URL」（review #10）。
    line_start = text.rfind("\n", 0, start) + 1
    line_end = text.find("\n", end)
    line_end = len(text) if line_end == -1 else line_end
    line = text[line_start:line_end]
    for u in _URL_RE.finditer(line):
        if u.start() <= start - line_start and end - line_start <= u.end():
            return True

    # 报告结构：F1、L1、Q1、H1、v0.3、第 3 项、近 8 期、[1]。
    if prev in {"F", "f", "L", "l", "Q", "q", "H", "h", "v", "V"}:
        return True
    # 序号/窗口量词：第 3 季度、第 3 项、近 12 个月、近 8 期（review #4：
    # 原表缺 季/个/年/周 → 「第 3 季度」「近 12 个月」被判未绑定数字）。
    if re.search(r"第\s*$|近\s*$", before) and re.match(
            r"\s*(期|项|条|章|节|次|名|日|季|个|年|周|月)", after):
        return True
    if prev == "[" and nxt == "]":
        return True
    # 标的代码（6 位）——仅在**上下文指明是代码**时豁免，避免放过真实量值。
    if (len(token) == 6 and token.isdigit()
            and re.search(r"(标的|代码|股票|证券|简称|\(|（)\s*$", before)):
        return True
    return False


def _fact_value_matches(token: str, facts: object) -> bool:
    """正文 token 是否为本段一个数值事实的展示值。

    精确 token 比较避免把 ``9`` 误认成 ``999``；浮点比较允许 ``-36.7`` 在正文中
    写作 ``36.7%``（符号和百分号是文字格式，不改变该数的量级）。
    """
    try:
        written = float(token.replace(",", ""))   # 千分位书写（D2）
    except ValueError:
        return False
    for fact in facts if isinstance(facts, list) else ():
        if not isinstance(fact, dict):
            continue
        value = _as_number(fact.get("value"))
        if value is not None and math.isclose(abs(written), abs(value), rel_tol=_FACT_TOL,
                                              abs_tol=_FACT_TOL):
            return True
    return False


def _validate_fact_numbers(sec: dict) -> list[str]:
    """有 ``facts`` 的段，其正文每一个非结构性数字都必须绑定一个事实值。"""
    facts = sec.get("facts")
    if not isinstance(facts, list):
        return []
    errs: list[str] = []
    for field in ("facts_md", "analysis_md"):
        raw = str(sec.get(field) or "")
        # 等长空格替换 → 各 token 的 span 与原文一致，_is_exempt_num 的
        # 前后文判断语义不变
        text = _MASKED_STRUCTURAL_RE.sub(lambda m: " " * len(m.group(0)), raw)
        for m in _NUM_TOKEN_RE.finditer(text):
            if _is_exempt_num(text, m) or _fact_value_matches(m.group(0), facts):
                continue
            errs.append(f"{field}:数字 {m.group(0)!r} 未绑定本段 facts")
    return errs


class AnalysisSchemaError(ValueError):
    pass


def _validate_one(sec: dict) -> list[str]:
    errs: list[str] = []
    if not isinstance(sec, dict):
        return ["段必须是对象"]
    for k in REQUIRED_FIELDS:
        v = sec.get(k)
        if v is None:
            errs.append(f"missing:{k}")
        elif not isinstance(v, str):
            errs.append(f"type:{k}")
        elif not v.strip():
            errs.append(f"empty:{k}")
        elif len(v) > MAX_LEN[k]:
            errs.append(f"len:{k}")
    if errs:
        return errs
    if not _EVIDENCE_RE.match(sec["evidence_tag"]):
        errs.append("evidence_tag 须为证据等级（A/B/C/D 或 L1-L4）或四维标签首字标记")
    if sec["position"] not in POSITION_ALLOWED:
        errs.append(f"position 不在允许集合: {sec['position']}")
    if not errs:
        fact_errs, known_ids = _validate_facts(sec)
        errs.extend(fact_errs)
        for k in ("facts_md", "analysis_md"):
            try:
                render_markdown(sec[k])
            except MarkdownSubsetError as exc:
                errs.append(f"markdown:{k}:{exc}")
        errs.extend(_validate_fact_refs(sec, known_ids))
        errs.extend(_validate_fact_numbers(sec))
    return errs


def load_analysis_json(path: Path) -> list[dict]:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AnalysisSchemaError(f"analysis.json 读取/解析失败: {exc}") from exc
    if not isinstance(raw, list):
        raise AnalysisSchemaError("analysis.json 顶层必须为数组")
    return raw


def validate_sections(raw: list[dict]) -> list[str]:
    out: list[str] = []
    for i, sec in enumerate(raw):
        for e in _validate_one(sec):
            out.append(f"[{i}] {e}")
    return out


# --- 槽位路由（v0.3.0）--------------------------------------------------------
# analysis 段按 module/position 命中渲染器的「命名槽位」。槽位名大小写不敏感，
# 未命中任何槽位的段仍走尾部注记（零回归）。
OVERVIEW_KEYS = frozenset({"overview", "executive_summary"})
BEAR_CHAIN_KEYS = frozenset({"bear_chain"})
MDA_NARRATIVE_KEYS = frozenset({"mda_narrative"})
EVENT_CLASSIFICATION_KEYS = frozenset({"event_classification"})
PARTICIPANT_SCAN_KEYS = frozenset({"participant_scan"})

# 「事件分析归属」的 canonical 判定：md 槽位键 + 历史 html 键的并集。
# v0.3.0 把 md 的 events 语义迁到槽位键 `event_classification`，而 html 侧谓词
# 仍按 `module == "events"` 手写判定、两侧各持一份 → 按新键撰写时 md 替换占位、
# html 仍显示静态块；按旧键撰写时 md 保留 error 级 `completion-template-placeholder`
# 占位、html 反而正常。md/html 同源是这条路径的既有契约，故判定上收到此常量，
# 两个渲染器共用（见 render_html.has_events_analysis 与 _v3._section_events_timeline）。
EVENTS_HOST_KEYS = EVENT_CLASSIFICATION_KEYS | {"events"}


def _keys_of(sec: dict) -> set[str]:
    """段的 module/position 归一化小写集合（非 dict → 空集）。"""
    if not isinstance(sec, dict):
        return set()
    return {
        str(sec.get(k) or "").strip().lower()
        for k in ("module", "position")
    } - {""}


def find_section(analysis: list[dict] | None, keys: frozenset[str]) -> dict | None:
    """返回首个命中槽位的段；无命中 → None。

    首个命中即返回（不合并多段）：槽位语义是「一个位置一份内容」，
    合并多段会让渲染结果依赖段序。
    """
    for sec in (analysis or []):
        if _keys_of(sec) & keys:
            return sec
    return None


def split_overview(analysis: list[dict] | None) -> tuple[list[dict], list[dict]]:
    """切出 overview 槽位的段，返回 ``(overview 段, 其余段)``。

    前置渲染的段必须从尾部注记中剔除，否则同一段在 md 中出现两次。
    """
    ov: list[dict] = []
    rest: list[dict] = []
    for sec in (analysis or []):
        (ov if _keys_of(sec) & OVERVIEW_KEYS else rest).append(sec)
    return ov, rest


# 就地渲染的槽位（段内容嵌进渲染器的对应位置，而非尾部注记）。
# 尾部注记必须排除这些段，否则同一内容出现两次。
INLINE_SLOT_KEYS = (
    OVERVIEW_KEYS
    | BEAR_CHAIN_KEYS
    | MDA_NARRATIVE_KEYS
    | EVENT_CLASSIFICATION_KEYS
    | PARTICIPANT_SCAN_KEYS
)


def is_inline_slotted(sec: dict) -> bool:
    """段是否命中任一就地渲染槽位。"""
    return bool(_keys_of(sec) & INLINE_SLOT_KEYS)


# --- 就地消费登记（渲染期状态）--------------------------------------------------
# is_inline_slotted 只说明「命中槽位则就地渲染」，回答不了「本次是否真的渲染了」：
# 三个宿主都是条件渲染（participant_scan 无扫描行 / event_classification 无事件卡 /
# mda_narrative 无 MD&A 卡时整体不输出），brief 模式更是不调用它们。尾部注记若按
# 静态谓词剔除，这些段既无正文落点、又被剔除 → 内容零落点丢失。
# 故由宿主在真正渲染时就地登记，尾部注记只剔除已登记的段。
# 登记随 collection 传递（与 collection["_enhancements"] 同为渲染期状态）。
CONSUMED_IDS_KEY = "_inline_consumed_ids"


def reset_inline_consumed(collection: dict | None) -> None:
    """渲染开始时清空登记（同一 collection 二次渲染不得残留上一轮结果）。"""
    if isinstance(collection, dict):
        collection[CONSUMED_IDS_KEY] = set()


def mark_inline_consumed(collection: dict | None, sec: dict | None) -> None:
    """宿主渲染了该段内容后就地登记。

    按**对象身份**（id）登记而非取值：analysis 段列表在整轮渲染中保持同一批
    对象存活，故 id 稳定，且两份内容相同的段不会互相误判为已渲染。
    """
    if not isinstance(collection, dict) or not isinstance(sec, dict):
        return
    ids = collection.get(CONSUMED_IDS_KEY)
    if not isinstance(ids, set):
        ids = set()
        collection[CONSUMED_IDS_KEY] = ids
    ids.add(id(sec))


def is_consumed_inline(collection: dict | None, sec: dict) -> bool:
    """该段本次已被某个宿主就地渲染（尾部注记须剔除，避免重复出现）。"""
    ids = collection.get(CONSUMED_IDS_KEY) if isinstance(collection, dict) else None
    return isinstance(ids, set) and id(sec) in ids


def strip_render_state(collection: dict[str, Any] | None) -> dict[str, Any]:
    """返回剔除渲染期登记键的浅拷贝，供**持久化前**使用。

    id() 是 CPython 堆地址：登记键随 collection 落进 collections.raw_json 后，
    字节相同的采集数据每跑一次都写出不同的整数列表（本地实证：report 行
    116-119 各带一组互不相同的地址）。键本身在渲染内语义正确（见
    mark_inline_consumed），故修法是在持久化边界剥离，而非改成取值键——
    取值键会让「与已消费段同 module/position/title 的第二段」被误判为已渲染，
    从主机位与尾部注记双双漏掉（v0.3.0 已修过该「零落点丢失」，见 _concise
    的尾部注记构造注释）。
    """
    if not isinstance(collection, dict):
        return {}
    return {k: v for k, v in collection.items() if k != CONSUMED_IDS_KEY}
