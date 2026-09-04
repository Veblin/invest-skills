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
