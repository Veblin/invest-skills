"""v0.1.9 report rendering extras — isolated from render.py to reduce merge conflicts.

All functions are pure: read collection dict, return markdown strings.
"""

from __future__ import annotations

from .financial_rigor import FAIL_THRESHOLD_PCT, WARN_THRESHOLD_PCT, cross_validate
from .schema import index_dimensions


def render_rigor_warnings(collection: dict, *, strict: bool = False) -> str:
    """v0.1.9: >5% cross-source hard block annotation."""
    reports = cross_validate(collection)
    fails = [r for r in reports if r.deviation_pct > FAIL_THRESHOLD_PCT]
    warns = [r for r in reports if WARN_THRESHOLD_PCT < r.deviation_pct <= FAIL_THRESHOLD_PCT]

    if not fails and not warns:
        return ""

    lines = ["### 数据验算警示（financial_rigor）", ""]
    for r in fails:
        lines.append(f"- ❌ **{r.field}**: {r.detail}（偏差 {r.deviation_pct:.1f}%）")
    for r in warns:
        lines.append(f"- ⚠️ **{r.field}**: {r.detail}（偏差 {r.deviation_pct:.1f}%）")

    if strict and fails:
        lines.append("")
        lines.append(
            "> **严格验算模式（--strict-rigor）**：跨源差异 >5%，"
            "后续估值段仅供参考，须独立核验原始数据源。"
        )
    lines.append("")
    return "\n".join(lines)


def render_ah_detection_note(collection: dict) -> str:
    """v0.1.9: A+H detection only — no HK financials."""
    dims = index_dimensions(collection)
    basic = dims.get("basic_info", {}).get("data") or {}
    if not isinstance(basic, dict):
        return ""

    hk_code = (
        basic.get("hk_code") or basic.get("h_code")
        or basic.get("港股代码") or basic.get("B股代码")
    )
    name = str(basic.get("name") or basic.get("股票简称") or "")
    is_ah = bool(hk_code) or "H" in name.upper().split()

    if not is_ah:
        return ""

    code_str = f"（港股代码: {hk_code}）" if hk_code else ""
    return (
        f"> **A+H 标的{code_str}**：检测到两地上市标记。"
        "跨准则完整财务对比（毛利率/净利率/ROE 多年度）待 **v0.2.0** 港股采集链交付。"
        "当前报告仅基于 A 股数据源。\n"
    )


def section_exogenous_shock(collection: dict) -> str:
    """v0.1.9: Exogenous shock hypothesis ⑥ from news cards."""
    news = collection.get("news") or {}
    cards = news.get("cards") or []
    if not cards:
        return ""

    lines = [
        "### 外生冲击假说⑥（新闻/公告归因）",
        "",
        "| 日期 | 方向 | 可信度 | 标题 | 来源 |",
        "|------|------|--------|------|------|",
    ]
    for c in cards[:10]:
        if not isinstance(c, dict):
            continue
        lines.append(
            f"| {c.get('date', '—')} | {c.get('direction', 'neutral')} "
            f"| {c.get('credibility', '—')} ({c.get('credibility_score', '—')}) "
            f"| {c.get('title', '—')[:40]} | {c.get('source', '—')} |"
        )
    lines.extend([
        "",
        "**[分析]** 上述条目反映市场可能正在定价的外生叙事；"
        "可信度低的消息仍可能通过情绪渠道影响价格，与最终事实认定无关。",
        "",
        "🔍 **待独立验证项**：高影响条目须回溯原始 URL/公告全文。",
        "",
    ])
    return "\n".join(lines)

