"""Markdown report rendering (v2/v3) and main render() entry."""
from __future__ import annotations

import html as _html_mod
import json
import logging
import re
from pathlib import Path
from typing import Any

from lib.nums import coalesce_field, fmt_amount, safe_float as _safe_num
from lib.technical import compute, sort_kline_asc
from lib.participant_scan import (
    build_participant_behavior_section,
    moneyflow_cv_window,
    moneyflow_signal_label,
    northbound_label,
    resolve_moneyflow,
)

from ..proxy import (
    EASTMONEY_BLOCKED_KEYWORDS as _EASTMONEY_BLOCKED_KEYWORDS,
    EASTMONEY_FAILURE_PROXY_MARKER,
    EASTMONEY_FAILURE_TUN_MARKER,
)
from ..schema import CrossValidation, DriverFactor, ProbabilityStructure, _CV_ICONS, _CV_LABELS, index_dimensions
from ..version import get_package_version

from .. import render_utils as _ru
from ..render_utils import (
    ENGINE_VERSION,
    sanitize_error,
    _sanitize_error,
    _index_dims,
    _get_dim_data,
    _get_dim_meta,
    _get_analysis_cards,
    _missing_section,
    _references_appendix,
    _risk_footer,
    _meta_cv_line,
    _cv,
    _fmt,
    _fmt_v2,
    _fmt_num,
    _fmt_end_date,
    _get_safe,
    _coalesce_fin_field,
    _fin_field_num,
    _wrap_details,
    _source_status_block,
    _compute_metric_cagr,
    _periods_per_year,
    _historical_pe_median,
    _bull_bear_valuation_divergence_text,
    _evidence_conclusion_block,
    _v3_cv7_assessment,
    _v3_cv7_block,
    _v3_cv8_assessment,
    _v3_cv8_block,
    _v3_trend_stage_hints,
    _v3_price_change,
    _v3_price_window_label,
    _data_fields,
)
from ..render_dcf import _section_dcf_valuation
from ..render_risk import (
    _v3_build_risk_report,
    _v3_bull_bear_implied_growth,
    _section_bull_bear,
    _section_risk_uncertainty,
    _section_left_right_probability,
)
from ..render_html import render_html

logger = logging.getLogger(__name__)

def _v3_valuation_percentiles(dims, val_cache=None):
    """Facade-aware：``monkeypatch`` ``lib.render._v3_valuation_percentiles`` 对本模块生效。"""
    from lib import render as facade

    current = facade.__dict__.get("_v3_valuation_percentiles")
    if current is not None and current is not _v3_valuation_percentiles:
        return current(dims, val_cache)
    return _ru._v3_valuation_percentiles(dims, val_cache)


def _v3_load_valuation_summary(dims, val_cache=None):
    """Facade-aware：``monkeypatch`` ``lib.render._v3_load_valuation_summary`` 对本模块生效。"""
    from lib import render as facade

    current = facade.__dict__.get("_v3_load_valuation_summary")
    if current is not None and current is not _v3_load_valuation_summary:
        return current(dims, val_cache)
    return _ru._v3_load_valuation_summary(dims, val_cache)

_COMMITMENT_KEYWORDS = ("承诺", "不减持")

_MGMT_EVENT_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("回购", "buyback"),
    ("并购", "ma"),
    ("收购", "ma"),
    ("增发", "capital_allocation"),
    ("定增", "capital_allocation"),
    ("IPO", "capital_allocation"),
    ("资本开支", "capex"),
    ("扩产", "capex"),
)

_MGMT_CATEGORY_LABELS = {
    "capital_allocation": "资本配置",
    "capex": "资本开支",
    "buyback": "回购",
    "ma": "并购",
    "personnel": "人事",
}

_v3_northbound_signal_label = northbound_label



# --- _render_engine_extras ---
def _render_engine_extras(collection: dict[str, Any]) -> list[str]:
    """渲染引擎层产出：宏观、融合、可信度、产业链。"""
    lines: list[str] = []

    macro = collection.get("macro_context") or {}
    if macro.get("status") == "ok":
        from ..macro import macro_signal_label
        lines.append(f"**[宏观情景]** {macro_signal_label(macro)}")

    chain = collection.get("chain_context") or {}
    if chain.get("status") == "ok" and chain.get("industry"):
        pos = chain.get("chain_position") or "—"
        lines.append(f"**[产业链]** {chain['industry']} · {pos}")

    fusion = collection.get("fusion") or {}
    if fusion:
        lines.append("**[多源融合]**")
        for dim, fp in sorted(fusion.items()):
            if isinstance(fp, dict):
                fv = fp.get("fused_value")
                consensus = fp.get("consensus", "?")
                diff = fp.get("max_diff_pct", 0)
                lines.append(f"  - {dim}: 融合值={fv} · {consensus} · 最大差异={diff}%")

    cred = collection.get("credibility") or {}
    if cred:
        top = sorted(cred.items(), key=lambda x: -x[1])[:5]
        cred_s = ", ".join(f"{k}={v:.0f}" for k, v in top)
        lines.append(f"**[证据可信度]** {cred_s}")

    lines.extend(_render_enhancement_hints(collection))

    return lines


# --- _render_enhancement_hints ---
def _render_enhancement_hints(collection: dict[str, Any]) -> list[str]:
    """渲染 ReportEnhancer 触发的可操作建议。"""
    enhancements = collection.get("_enhancements") or {}
    if not enhancements:
        return []

    lines: list[str] = ["**[报告增强触发]**"]

    price_ws = enhancements.get("price_shock_websearch")
    if isinstance(price_ws, dict) and price_ws.get("triggered"):
        from ..env import PRICE_NEWS_WHITELIST
        sites = " OR ".join(f"site:{d}" for d in PRICE_NEWS_WHITELIST[:4])
        lines.append(f"- 涨价信号确认 → 建议 WebSearch 深搜（{sites} ...）")

    val_alert = enhancements.get("valuation_high_alert")
    if isinstance(val_alert, dict) and val_alert.get("triggered"):
        lines.append("- PE 历史位置≥80% → 建议触发源 B 类增强（估值区间驱动）")

    shock = enhancements.get("price_shock_detect")
    if isinstance(shock, dict) and shock.get("has_shock"):
        dates = shock.get("shock_dates") or []
        shock_type = shock.get("shock_type") or "异常波动"
        date_parts = []
        for s in dates[:5]:
            if s.get("date") is None:
                continue
            pct = _safe_num(s.get("pct_chg"))
            pct_s = f"{pct:+.1f}%" if pct is not None else "—"
            date_parts.append(f"{s.get('date')}({pct_s})")
        date_s = ", ".join(date_parts)
        lines.append(f"- 近 60 日价格异常（{shock_type}）: {date_s or '—'}")

    return lines if len(lines) > 1 else []


# --- _render_dimension_data ---
def _render_dimension_data(dn: str, data: Any, lines: list[str]) -> None:
    """渲染维度主数据内容（不含来源标注）。"""
    if dn == "basic_info" and isinstance(data, dict):
        for k, v in data.items():
            lines.append(f"- {k}: {v}")
    elif dn == "financials" and isinstance(data, list):
        lines.append("| 期间 | ROE | EPS | 扣非净利润 |\n|------|-----|-----|-----------|")
        for r in data[:5]:
            lines.append(f"| {r.get('end_date','?')} | {_fmt(r.get('roe'),'%')} | {_fmt(r.get('eps'))} | {_fmt(r.get('profit_dedt'))} |")
    elif dn == "quote":
        if isinstance(data, dict):
            for k, v in data.items():
                lines.append(f"- {k}: {v}")
        elif isinstance(data, list) and data:
            # Tushare/akshare 日线数据：取最新一条展示
            r = data[-1]
            lines.append(f"- 日期: {r.get('trade_date', '?')}")
            lines.append(f"- 开盘: {_fmt(r.get('open'))}")
            lines.append(f"- 最高: {_fmt(r.get('high'))}")
            lines.append(f"- 最低: {_fmt(r.get('low'))}")
            lines.append(f"- 收盘: {_fmt(r.get('close'))}")
            lines.append(f"- 成交量: {_fmt(r.get('vol'))}")
    elif dn == "shareholders" and isinstance(data, list):
        lines.append("| 股东 | 持股比例 |\n|------|---------|")
        for r in data[:10]:
            lines.append(f"| {r.get('holder_name','?')} | {_fmt(r.get('hold_ratio'),'%')} |")
    elif dn == "northbound" and isinstance(data, list):
        lines.append("| 日期 | 净流向 |\n|------|-------|")
        for r in data[:7]:
            lines.append(f"| {r.get('trade_date','?')} | {_fmt(r.get('net_mf_vol'))} |")
    elif dn == "kline" and isinstance(data, list):
        lines.append("| 日期 | 开盘 | 最高 | 最低 | 收盘 | 成交量 |\n|------|------|------|------|------|--------|")
        for r in data[-10:]:
            lines.append(f"| {r.get('trade_date','?')} | {_fmt(r.get('open'))} | {_fmt(r.get('high'))} | {_fmt(r.get('low'))} | {_fmt(r.get('close'))} | {_fmt(r.get('vol'))} |")


