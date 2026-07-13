"""v0.1.9 report rendering extras — isolated from render.py to reduce merge conflicts.

All functions are pure: read collection dict, return markdown strings.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any

from .financial_rigor import FAIL_THRESHOLD_PCT, WARN_THRESHOLD_PCT, cross_validate
from .schema import index_dimensions

logger = logging.getLogger(__name__)


def render_rigor_warnings(collection: dict, *, strict: bool = False) -> str:
    """v0.1.9: >5% cross-source hard block annotation."""
    reports = cross_validate(collection)
    fails = [r for r in reports if r.deviation_pct > FAIL_THRESHOLD_PCT]
    warns = [r for r in reports if WARN_THRESHOLD_PCT < r.deviation_pct <= FAIL_THRESHOLD_PCT]

    if not fails and not warns:
        return ""

    lines = ["### 数据验算警示（v0.1.9 financial_rigor）", ""]
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


def classify_info_richness(collection: dict) -> str:
    """A/B/C information richness for AI bias declaration."""
    dims = index_dimensions(collection)
    basic = dims.get("basic_info", {}).get("data") or {}
    if not isinstance(basic, dict):
        basic = {}
    list_date = basic.get("list_date") or basic.get("上市时间") or ""
    listed_years = 0.0
    if list_date:
        s = str(list_date).replace("-", "")[:8]
        try:
            ld = datetime.strptime(s, "%Y%m%d").date()
            listed_years = (date.today() - ld).days / 365.25
        except ValueError:
            pass

    research = dims.get("research", {}).get("data")
    analyst_count = 0
    if isinstance(research, list):
        analyst_count = len(research)
    elif isinstance(research, dict):
        analyst_count = len(research.get("reports") or [])

    if listed_years >= 5 and analyst_count >= 5:
        return "A"
    if listed_years >= 1:
        return "B"
    return "C"


def render_ai_bias_declaration(collection: dict) -> str:
    """v0.1.9: AI bias A/B/C declaration at report header."""
    level = classify_info_richness(collection)
    labels = {
        "A": "信息丰富（上市≥5年且研报≥5篇）— AI 训练偏见风险相对较低，但仍须独立验证",
        "B": "信息中等 — 部分维度可能依赖 LLM 先验，关键数字须追溯数据源",
        "C": "信息稀疏（新股/冷门）— LLM 幻觉与过时知识风险较高，分析性段落置信度下调",
    }
    return (
        f"**[AI 偏见声明]** 信息丰富度 **{level}**：{labels.get(level, labels['C'])}\n"
    )


def render_contrarian_section(collection: dict) -> str:
    """v0.1.9: Contrarian thinking — failure paths + bear arguments."""
    symbol = collection.get("symbol", "")
    lines = [
        "### 逆向思考（失败路径预演）",
        "",
        "| 假设前提 | 若失效则… | 可观测信号 |",
        "|---------|----------|-----------|",
        "| 盈利增速可持续 | 估值收缩 + 盈利下修双杀 | 毛利率连续下滑、应收恶化 |",
        "| 行业景气维持 | 周期下行拖累业绩 | 产品价格/开工率走弱 |",
        "| 估值溢价合理 | 向行业中位数回归 | PE 分位回落、同业折价扩大 |",
        "",
        "**空方论点（待独立验证）**：",
        f"- 当前叙事是否过度依赖单一增长驱动？",
        f"- 管理层指引 vs 现金流是否一致？",
        f"- {symbol} 是否存在未充分定价的尾部风险（监管/诉讼/客户集中）？",
        "",
        "🔍 **待独立验证项**：上述路径为框架性推演，须结合最新财报与公告逐项核验。",
        "",
    ]
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


def render_extended_technical(collection: dict, dims: dict) -> str:
    """v0.1.9: Ichimoku / volatility cone / RS / rolling beta sections."""
    from .collector import _akshare_hs300_dated_closes
    from .technical import compute, rolling_beta, sort_kline_asc, volatility_cone, relative_strength

    kline_data = dims.get("kline", {}).get("data")
    if not kline_data or not isinstance(kline_data, list):
        return ""

    kline = sort_kline_asc(kline_data)
    tech = compute(kline)
    lines = ["### 扩展技术指标（v0.1.9）", ""]

    ich = tech.get("ichimoku") or {}
    if ich and not ich.get("error"):
        lines.append("**一目均衡表（Ichimoku）**")
        lines.append(
            f"- 転換線: {ich.get('tenkan_latest', '—')} · "
            f"基準線: {ich.get('kijun_latest', '—')}"
        )
        lines.append(
            f"- 云带（先行スパン A/B）为 26 期前计算值的前瞻放置，属标准 Ichimoku 行为"
        )
        pos = ich.get("price_vs_cloud", "—")
        lines.append(f"- 现价相对云带: {pos}")
        lines.append("")

    cone = tech.get("volatility_cone") or volatility_cone(
        [r.get("close") for r in kline if r.get("close") is not None]
    )
    if cone and not cone.get("error"):
        lines.append("**波动率锥（年化 HV）**")
        cur = cone.get("current_hv")
        pct = cone.get("percentile")
        if cur is not None:
            lines.append(f"- 当前 HV: {cur:.1f}%")
        if pct is not None:
            lines.append(f"- 历史分位: {pct:.0f}%（近 {cone.get('window', 252)} 日窗口）")
        lines.append("")

    stock_by_date: dict[str, float] = {}
    for r in kline:
        if r.get("close") is None:
            continue
        td = str(r.get("trade_date") or "").replace("-", "").replace("/", "")[:8]
        if len(td) == 8 and td.isdigit():
            stock_by_date[td] = float(r["close"])

    try:
        bench_dated = _akshare_hs300_dated_closes(days=max(130, len(stock_by_date) + 10))
    except Exception:
        logger.warning("沪深300基准数据获取失败，RS/Beta 段跳过", exc_info=True)
        bench_dated = []

    if stock_by_date and bench_dated:
        bench_by_date = dict(bench_dated)
        common = sorted(set(stock_by_date) & set(bench_by_date))
        if len(common) < 20:
            lines.append("**相对强度 / Beta**：基准对齐不足（交集交易日 < 20），跳过")
            lines.append("")
        else:
            stock_closes = [stock_by_date[d] for d in common]
            bench_closes = [bench_by_date[d] for d in common]
            rs = relative_strength(stock_closes, bench_closes)
            if rs.get("rs_latest") is not None:
                lines.append("**相对强度 RS（vs 沪深300）**")
                lines.append(f"- RS = {rs['rs_latest']:.1f}（基期=100，对齐 {len(common)} 日）")
                lines.append("")

            beta = rolling_beta(stock_closes, bench_closes)
            if beta.get("windows"):
                lines.append("**滚动 Beta（vs 沪深300）**")
                for w, info in beta["windows"].items():
                    b = info.get("beta")
                    if b is not None:
                        lines.append(f"- {w}日 Beta: {b:.3f}（R²={info.get('r_squared', '—')}）")
                lines.append("")

    if len(lines) <= 2:
        return ""

    lines.append("> 以上技术指标仅描述当前市场状态，不构成投资建议。")
    lines.append("")
    return "\n".join(lines)


def render_all_extras(collection: dict, dims: dict | None = None) -> list[str]:
    """Collect all v0.1.9 extra sections for render_report_v3."""
    if dims is None:
        dims = index_dimensions(collection)
    strict = bool((collection.get("_meta") or {}).get("strict_rigor"))
    parts: list[str] = []
    for text in (
        render_ai_bias_declaration(collection),
        render_ah_detection_note(collection),
        render_rigor_warnings(collection, strict=strict),
        section_exogenous_shock(collection),
        render_contrarian_section(collection),
        render_extended_technical(collection, dims),
    ):
        if text and text.strip():
            parts.append(text)
    return parts
