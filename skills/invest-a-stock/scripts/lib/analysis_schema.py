"""analysis.json schema + 校验器（v0.2.8 R-B1 分析协议）。

段结构: [{module, title, facts_md, analysis_md, evidence_tag, position}]
校验：必填字段、长度、evidence_tag 模式（A-D 等级或 四维标签起始）、
markdown 子集（复用 lib.md_subset 的 fail-loud 判定，不支持语法即 error）。
与 md 产物同目录并存：reports/{symbol}-{name}/{ts}.analysis.json
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from lib.md_subset import MarkdownSubsetError, render_markdown

REQUIRED_FIELDS = ("module", "title", "facts_md", "analysis_md", "evidence_tag", "position")
MAX_LEN = {"module": 64, "title": 128, "facts_md": 20_000, "analysis_md": 40_000, "evidence_tag": 32, "position": 64}
POSITION_ALLOWED = {"overview", "valuation", "financials", "technicals", "northbound",
                    "holders", "events", "refs", "research", "conclusion", "analysis"}
_EVIDENCE_RE = re.compile(r"^([A-Da-d]{1,2}|[Ll][1-4])")


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
        for k in ("facts_md", "analysis_md"):
            try:
                render_markdown(sec[k])
            except MarkdownSubsetError as exc:
                errs.append(f"markdown:{k}:{exc}")
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
