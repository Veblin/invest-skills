"""证据表生成模块。从采集结果生成结构化 {dimension, channel, value, confidence} 表。

在 synthesis 之前输出独立的结构化证据表，供前端审查。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class EvidenceRow:
    """证据表单行。"""
    dimension: str
    channel: str
    value_summary: str
    confidence: str         # "high" | "medium" | "low"
    source_count: int
    cross_validation: str   # "✅ 一致" | "⚠️ 差异 X%" | "— 单源"


def _format_value(data) -> str:
    """从数据中提取可读的值摘要。"""
    if data is None:
        return "无数据"
    if isinstance(data, (int, float)):
        return f"{data:.2f}" if isinstance(data, float) else str(data)
    if isinstance(data, dict):
        key_fields = ["pe_ttm", "pb", "close", "price", "roe", "eps",
                       "name", "industry", "net_mf_vol"]
        parts = []
        for k in key_fields:
            v = data.get(k)
            if v is not None:
                if isinstance(v, float):
                    parts.append(f"{k}={v:.2f}")
                else:
                    parts.append(f"{k}={v}")
        return ", ".join(parts[:3]) if parts else "有数据"
    if isinstance(data, list):
        return f"{len(data)}条记录"
    return "有数据"


def _format_source_value(source_entry: dict) -> str:
    """按渠道格式化值摘要（仅用该源自身数据，不回退到维度主源）。"""
    sv = source_entry.get("scalar_value")
    if sv is not None:
        return f"scalar={float(sv):.4g}"
    src_data = source_entry.get("data")
    if src_data is not None:
        return _format_value(src_data)
    return "无标量摘要"


def _confidence_label(confidence: str) -> str:
    """置信度映射为可读标签。"""
    return {"high": "🟢 高", "medium": "🟡 中", "low": "🔴 低"}.get(confidence, confidence)


def _cv_label(meta: dict, all_src: list) -> str:
    """从 _meta 生成交叉验证标签（保留 detail）。"""
    cv_status = meta.get("cross_validation")
    cv_detail = meta.get("cross_validation_detail") or ""
    if cv_status == "convergence":
        return f"✅ 一致" + (f" ({cv_detail})" if cv_detail else "")
    if cv_status:
        return f"⚠️ {cv_detail}" if cv_detail else "⚠️ 差异"
    return "— 单源" if len(all_src) <= 1 else "—"


def build_evidence_table(dimensions: list[dict]) -> list[EvidenceRow]:
    """从采集结果构建证据表。"""
    rows: list[EvidenceRow] = []
    for dim in dimensions:
        dn = dim.get("dimension", "")
        display = dim.get("display", dn)
        meta = dim.get("_meta", {})
        all_src = meta.get("all_sources", [])
        cv_label = _cv_label(meta, all_src)

        if all_src:
            for s in all_src:
                src_name = s.get("source", "?")
                avail = s.get("data_available", False)
                confidence = s.get("confidence", "low")
                if avail:
                    rows.append(EvidenceRow(
                        dimension=display,
                        channel=src_name,
                        value_summary=_format_source_value(s),
                        confidence=_confidence_label(confidence),
                        source_count=len(all_src),
                        cross_validation=cv_label,
                    ))
                elif s.get("error"):
                    rows.append(EvidenceRow(
                        dimension=display,
                        channel=src_name,
                        value_summary=f"❌ {s.get('error', '失败')[:40]}",
                        confidence="—",
                        source_count=len(all_src),
                        cross_validation=cv_label,
                    ))
        else:
            primary_src = meta.get("source", "none")
            rows.append(EvidenceRow(
                dimension=display,
                channel=primary_src,
                value_summary=_format_value(dim.get("data")),
                confidence=_confidence_label(meta.get("confidence", "low")),
                source_count=1,
                cross_validation="— 单源",
            ))
    return rows


def render_evidence_table(rows: list[EvidenceRow], fmt: str = "md") -> str:
    """渲染证据表为 Markdown 或 JSON。"""
    if fmt == "json":
        import json as _json
        return _json.dumps(
            [{
                "dimension": r.dimension,
                "channel": r.channel,
                "value_summary": r.value_summary,
                "confidence": r.confidence,
                "source_count": r.source_count,
                "cross_validation": r.cross_validation,
            } for r in rows],
            ensure_ascii=False, indent=2,
        )

    lines = [
        "## 证据表（Evidence Table）",
        "",
        "| 维度 | 渠道 | 值摘要 | 置信度 | 源数量 | 交叉验证 |",
        "|------|------|--------|--------|--------|---------|",
    ]
    for r in rows:
        lines.append(
            f"| {r.dimension} | {r.channel} | {r.value_summary} | "
            f"{r.confidence} | {r.source_count} | {r.cross_validation} |"
        )
    return "\n".join(lines)
