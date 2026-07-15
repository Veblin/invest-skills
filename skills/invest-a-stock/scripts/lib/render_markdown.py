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

from .proxy import (
    EASTMONEY_BLOCKED_KEYWORDS as _EASTMONEY_BLOCKED_KEYWORDS,
    EASTMONEY_FAILURE_PROXY_MARKER,
    EASTMONEY_FAILURE_TUN_MARKER,
)
from .schema import CrossValidation, DriverFactor, ProbabilityStructure, _CV_ICONS, _CV_LABELS, index_dimensions
from .version import get_package_version

from . import render_utils as _ru
from .render_utils import (
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
from .render_dcf import _section_dcf_valuation
from .render_risk import (
    _v3_build_risk_report,
    _v3_bull_bear_implied_growth,
    _section_bull_bear,
    _section_risk_uncertainty,
    _section_left_right_probability,
)
from .render_html import render_html

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
        from .macro import macro_signal_label
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
        from .env import PRICE_NEWS_WHITELIST
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


# --- render_compact ---
def render_compact(collection: dict[str, Any], symbol: str) -> str:
    """紧凑文本报告（已弃用，v0.1.2 起 render() 路由到 render_report_v2）。

    保留供向后兼容；新代码应直接使用 render() 或 render_report_v2()。
    """
    lines = [
        f"# {symbol} 采集报告",
        f"采集时间: {collection.get('fetched_at','')[:19]}",
        f"状态: {collection['summary']['available']}/{collection['summary']['total']} 维度有数据",
        "",
    ]
    extras = _render_engine_extras(collection)
    if extras:
        lines.extend(extras)
        lines.append("")

    for dim in collection.get("dimensions", []):
        dn, display = dim["dimension"], dim["display"]
        data, meta = dim.get("data"), dim.get("_meta", {})
        source = meta.get("source", "none")
        all_src = meta.get("all_sources")
        has_data = data is not None

        # ---- 主数据区块 ----
        if has_data:
            xv = _source_status_block(all_src)
            lines.append(f"## ✅ {display}")
            lines.append(f"**主数据来源：** {source}")
            if xv:
                lines.append("")
                lines.append("**各渠道取证状态：**")
                lines.append(xv)
                lines.append("")
            cv_line = _meta_cv_line(meta)
            if cv_line:
                lines.append(cv_line)
                lines.append("")
            _render_dimension_data(dn, data, lines)
        else:
            lines.append(f"## ⚠️ {display}")
            error = dim.get("error", "无可用数据源")
            lines.append("")
            lines.append(f"> **未获取到任何有效数据，无法判断。**")
            lines.append(f"> 原因：{_sanitize_error(error, 80)}")
            xv = _source_status_block(all_src)
            if xv:
                lines.append("")
                lines.append("**已尝试的渠道：**")
                lines.append(xv)
        lines.append("")

    # ---- 引用来源附录（类似论文 References） ----
    lines.append("---")
    lines.append("## 📚 引用来源（References）")
    lines.append("")
    lines.append("| 维度 | 渠道 | 追溯路径 | 数据状态 |")
    lines.append("|------|------|----------|---------|")
    for dim in collection.get("dimensions", []):
        display = dim["display"]
        all_src = dim.get("_meta", {}).get("all_sources")
        if not all_src:
            src_entry = dim.get("_meta", {})
            icon = "✅" if dim.get("data") is not None else "❌"
            qp = src_entry.get("query_params", "")
            src_name = src_entry.get("source", "?")
            status = "可用" if dim.get("data") is not None else "不可用"
            lines.append(f"| {display} | {src_name} | `{qp}` | {icon} {status} |")
            continue

        first = True
        for s in all_src:
            src_name = s.get("source", "?")
            avail = s.get("data_available", False)
            error = s.get("error", "")
            qp = s.get("query_params", "")
            dim_label = display if first else ""
            first = False
            if avail:
                lines.append(f"| {dim_label} | {src_name} | `{qp}` | ✅ 有数据 |")
            elif error:
                lines.append(f"| {dim_label} | {src_name} | `{qp}` | ❌ {_sanitize_error(error, 55)} |")
            else:
                lines.append(f"| {dim_label} | {src_name} | — | ⏭️ 未尝试 |")

    return "\n".join(lines)


# --- render_json ---
def render_json(collection: dict[str, Any]) -> str:
    from .json_util import dumps_json
    return dumps_json(collection)


# --- render ---
def render(collection: dict[str, Any], symbol: str, fmt: str = "compact",
           mode: str = "full", *, attach_extras: bool = True) -> str:
    """统一渲染入口。支持 compact / json / md / html 格式。

    compact  — 紧凑文本报告（v0.1.2 八段 v2 模板）
    json     — 结构化 JSON，适合程序消费
    md       — Markdown 九模块研究备忘录（v0.1.3 render_report_v3）
    html     — HTML 研究报告（v0.1.2 冻结模板）

    mode     — "full"（完整九模块）/"brief"（精简简报）/"concise"（对话场景精简）
    attach_extras — False 时跳过 market_structure / phase2 补采（离线 synthesize）
    """
    if attach_extras:
        from lib import collector
        if not collection.get("market_structure"):
            collector.attach_market_structure(collection, symbol)
        collector.attach_phase2_extras(collection, symbol)

        # Events: backfill when never attached, or [] without events_summary (failed path).
        # Standard collect→report with events_summary already ran attach_events.
        events_attached = False
        try:
            from lib.events import attach_events, needs_events_backfill
            if needs_events_backfill(collection):
                deep_mode = collection.get("_meta", {}).get("deep", False)
                event_days = 90 if deep_mode else 30
                attach_events(collection, symbol, days=event_days)
                events_attached = True
        except Exception as e:
            logger.warning("attach_events failed (non-fatal): %s", e)

        # Build analysis cards when missing or events were just attached.
        try:
            meta = collection.setdefault("_meta", {})
            if events_attached:
                meta.pop("analysis_cards", None)
            if events_attached or "analysis_cards" not in meta:
                from lib.analysis_templates import build_analysis_cards
                build_analysis_cards(collection)
        except Exception as e:
            logger.warning("build_analysis_cards failed (non-fatal): %s", e)

    if fmt == "json":
        return render_json(collection)
    if fmt == "html":
        return render_html(collection, symbol)
    if fmt == "md":
        return render_report_v3(collection, symbol, mode=mode)
    return render_report_v2(collection, symbol)


# --- render_report_v2 ---
def render_report_v2(collection: dict[str, Any], symbol: str) -> str:
    """v0.1.2 八段研究模板。

    结构: 公司画像 → 经营质量 → 估值位置 → 资金与筹码 →
          技术结构 → 事件催化 → 核心矛盾 → 引用来源
    """
    dims = _index_dims(collection)

    parts: list[str] = [
        _header_v2(collection, symbol),
        _section_profile(dims),
        _section_quality(dims),
        render_valuation_section(dims, collection),
        _section_flow(dims, collection),
        render_technical_section(dims, collection),
        _section_research_summary(collection, symbol, dims),
        _section_events_placeholder(),
        _section_thesis_placeholder(dims),
        _references_appendix(collection),
        _risk_footer(),
    ]
    return "\n\n".join(p for p in parts if p)


# --- _header_v2 ---
def _header_v2(collection: dict, symbol: str) -> str:
    name = ""
    basic = _get_dim_data(_index_dims(collection), "basic_info")
    if isinstance(basic, dict):
        name = basic.get("name", "") or basic.get("股票简称", "")
    title = f"# {symbol} {name} 研究快照"
    lines = [
        title.strip(),
        f"采集时间: {collection.get('fetched_at', '')[:19]}",
        f"维度: {collection['summary']['available']}/{collection['summary']['total']} 有数据"
        + (f"（{collection['summary']['degraded']} 降级）" if collection['summary'].get('degraded') else ""),
        "",
        "> ⚠️ **风险提示:** 本报告由自动化引擎生成，仅供研究备忘录参考，不构成任何投资建议、买卖指令或目标价预测。",
        "",
    ]
    return "\n".join(lines)


# --- _section_profile ---
def _section_profile(dims: dict[str, dict]) -> str:
    """公司画像（basic_info 事实罗列）。"""
    data = _get_dim_data(dims, "basic_info")
    if not data or not isinstance(data, dict):
        return _missing_section("公司画像", "basic_info 维度无数据")

    lines = ["## 一、公司画像", ""]
    # 关键字段映射
    key_fields = [
        ("name", "公司名称"),
        ("股票简称", "简称"),
        ("industry", "行业"),
        ("area", "地区"),
        ("market", "上市市场"),
        ("list_date", "上市日期"),
        # total_mv / pe_ratio 字段 basic_info 采集未请求，暂不渲染
    ]
    for key, label in key_fields:
        v = data.get(key)
        if v is not None:
            lines.append(f"- **{label}:** {v}")

    # 上市时长判断
    list_date = data.get("list_date", "")
    if list_date:
        try:
            from datetime import datetime
            ld = datetime.strptime(str(list_date)[:8], "%Y%m%d")
            years = (datetime.now() - ld).days / 365.25
            if years < 5:
                lines.append(f"- ⚠️ 上市约 {years:.1f} 年，属次新股，历史数据窗口较短")
        except (ValueError, TypeError):
            pass

    lines.append("")
    lines.append("🔍 **待独立验证:** 行业分类可能因数据源口径不同存在差异。上市日期以交易所公告为准。")
    return "\n".join(lines)


# --- _section_quality ---
def _section_quality(dims: dict[str, dict]) -> str:
    """经营质量（financials 表格 + 趋势句）。"""
    data = _get_dim_data(dims, "financials")
    if not data or not isinstance(data, list) or len(data) == 0:
        return _missing_section("经营质量", "financials 维度无数据")

    data = sort_kline_asc(data)  # end_date 与 trade_date 同格式可复用

    lines = ["## 二、经营质量", ""]

    # 表格（最近 8 期，升序后取末尾）
    lines.append("| 报告期 | ROE(%) | EPS | 扣非净利润 | 营收 | 净利润 |")
    lines.append("|--------|--------|-----|-----------|------|--------|")
    for r in data[-8:]:
        roe = r.get("roe", "-")
        eps = r.get("eps", "-")
        profit_dedt = _fmt_v2(r.get("profit_dedt"))
        revenue = _fmt_v2(r.get("revenue"))
        net_profit = _fmt_v2(r.get("net_profit"))
        lines.append(f"| {r.get('end_date', '?')} | {roe} | {eps} | {profit_dedt} | {revenue} | {net_profit} |")

    # 趋势句（Python 仅陈述事实）
    if len(data) >= 2:
        latest = data[-1]
        prev = data[-2]
        roe_now = latest.get("roe")
        roe_prev = prev.get("roe")
        if roe_now is not None and roe_prev is not None and isinstance(roe_now, (int, float)) and isinstance(roe_prev, (int, float)):
            direction = "上升" if roe_now > roe_prev else ("下降" if roe_now < roe_prev else "持平")
            lines.append(f"\n最近两期 ROE 趋势: {roe_prev}% → {roe_now}%（{direction}）")

    lines.append("")
    lines.append("🔍 **待独立验证:** 财务数据来自第三方数据源，应与公司年报/季报交叉核对。扣非净利润口径可能因源而异。")
    return "\n".join(lines)


# --- render_valuation_section ---
def render_valuation_section(dims: dict[str, dict], collection: dict = None) -> str:
    """估值位置（valuation 维度 + valuation.py 分位计算）。"""
    val_dim = dims.get("valuation", {})
    val_data = _get_dim_data(dims, "valuation")

    lines = ["## 三、估值位置", ""]
    if collection:
        try:
            from .render_extras import render_rigor_warnings
            strict = bool((collection.get("_meta") or {}).get("strict_rigor"))
            rigor = render_rigor_warnings(collection, strict=strict)
            if rigor:
                lines.append(rigor)
        except ImportError:
            pass

    if val_data is None:
        # 无数据 → 标注
        meta = _get_dim_meta(dims, "valuation")
        error = dims.get("valuation", {}).get("error", "估值维度无数据")
        lines.append(f"> **估值数据不可得。** 原因: {_sanitize_error(error, 80)}")
        lines.append("")
        lines.append("🔍 **待独立验证:** 确认 Tushare Token 配置后重试，或手动查询 PE/PB 当前值。")
        return "\n".join(lines)

    # 判断数据来源
    from lib.valuation import valuation_summary
    meta = _get_dim_meta(dims, "valuation")
    source = meta.get("source", "未知")

    # 处理 Tushare daily_basic 序列
    if isinstance(val_data, list) and len(val_data) > 0:
        val_sorted = sort_kline_asc(val_data)
        pe_seq = [r.get("pe_ttm") for r in val_sorted]
        pb_seq = [r.get("pb") for r in val_sorted]
        ps_seq = [r.get("ps_ttm") or r.get("ps") for r in val_sorted]
        dv = None
        for r in reversed(val_sorted):
            if r.get("dv_ratio") is not None:
                dv = r.get("dv_ratio")
                break

        # 判断窗口（A 股 ~242 交易日/年，1250 ≈ 5 年）
        from lib.valuation import valuation_window_label
        window_label = valuation_window_label(len(val_sorted))

        summary = valuation_summary(pe_seq, pb_seq, ps_seq=ps_seq,
                                   dv_ratio=dv, window_label=window_label)

        lines.append(f"**来源:** {source}（{window_label}历史序列 + 分位计算）")
        lines.append(f"**数据:** {summary['n_samples']} 个有效交易日")
        lines.append("")

        # PE（pct = 历史中严格低于当前值的比例，与 zone 标签含义一致）
        pe = summary["pe"]
        if pe["current"] is not None:
            pct_str = f"，{window_label} {pe['pct']:.1f}% 分位" if pe["pct"] is not None else ""
            median_str = f"（中位数 {pe['median']:.2f}x）" if pe.get("median") is not None else ""
            lines.append(f"**PE(TTM):** {pe['current']:.2f}x{pct_str}{median_str}，处于历史**{pe['zone']}**区间。")
        else:
            lines.append(f"**PE(TTM):** {pe.get('reason', '不可得')}")

        # PB
        pb = summary["pb"]
        if pb["current"] is not None:
            pct_str = f"，{window_label} {pb['pct']:.1f}% 分位" if pb["pct"] is not None else ""
            median_str = f"（中位数 {pb['median']:.2f}x）" if pb.get("median") is not None else ""
            lines.append(f"**PB:** {pb['current']:.2f}x{pct_str}{median_str}，处于历史**{pb['zone']}**区间。")
        else:
            lines.append(f"**PB:** {pb.get('reason', '不可得')}")

        # 股息率（Tushare daily_basic.dv_ratio 为百分比值如 0.42 表示 0.42%）
        if summary["dv_ratio"] is not None:
            lines.append(f"**股息率:** {summary['dv_ratio']:.2f}%（最近交易日 dv_ratio）")
        else:
            lines.append("**股息率:** 不可得")

        # PS
        ps = summary.get("ps", {})
        if ps.get("current") is not None:
            pct_str = f"，分位 {ps['pct']:.1f}%" if ps.get("pct") is not None else ""
            lines.append(f"**PS(TTM):** {ps['current']:.2f}x{pct_str}")

        # 警告
        for w in summary.get("warnings", []):
            lines.append(f"⚠️ {w}")

    elif isinstance(val_data, dict):
        # 腾讯快照（无历史序列）
        pe = val_data.get("pe_ttm")
        has_history = val_data.get("history_available", False)
        lines.append(f"**来源:** {source}（快照数据）")
        lines.append("")
        if pe is not None:
            lines.append(f"**PE(TTM):** {pe:.2f}x（当前快照）")
        if not has_history:
            lines.append("")
            lines.append("> ⚠️ **历史分位不可得，仅展示当前 PE/PB。** 需配置 Tushare Token 获取历史估值序列。")

    lines.append("")
    lines.append("🔍 **待独立验证:** PE 亏损期为 null 已剔除；行业相对估值 v0.1.2 未覆盖。估值分位不构成买卖判断。")
    return "\n".join(lines)


# --- _section_flow ---
def _section_flow(dims: dict[str, dict], collection: dict = None) -> str:
    """资金与筹码（shareholders + northbound + quote）。"""
    lines = ["## 四、资金与筹码", ""]

    # 行情
    quote_data = _get_dim_data(dims, "quote")
    if quote_data:
        if isinstance(quote_data, dict):
            price = coalesce_field(quote_data, "price", "close")
            change = quote_data.get("change_pct")
            turnover = quote_data.get("turnover_rate")
            if price is not None:
                change_str = f"（{change:+.2f}%）" if change is not None else ""
                lines.append(f"**最新价:** {price}{change_str}")
            if turnover is not None:
                lines.append(f"**换手率:** {turnover}%")
        elif isinstance(quote_data, list) and quote_data:
            r = quote_data[-1]
            lines.append(f"**最新收盘:** {r.get('close', '-')}（{r.get('trade_date', '-')}）")

    # 北向资金（升序后取最近 7 日）
    nb_data = _get_dim_data(dims, "northbound")
    if nb_data and isinstance(nb_data, list) and nb_data:
        nb_sorted = sort_kline_asc(nb_data)
        lines.append("")
        lines.append("**北向资金近7日:**")
        lines.append("| 日期 | 净流向 |")
        lines.append("|------|--------|")
        for r in nb_sorted[-7:]:
            lines.append(f"| {r.get('trade_date', '?')} | {_fmt_v2(r.get('net_mf_vol'))} |")

    # 十大股东
    sh_data = _get_dim_data(dims, "shareholders")
    if sh_data and isinstance(sh_data, list) and sh_data:
        lines.append("")
        lines.append("**前十大股东（最新报告期）:**")
        lines.append("| 股东 | 持股比例 |")
        lines.append("|------|---------|")
        for r in sh_data[:10]:
            lines.append(f"| {r.get('holder_name', '?')} | {_fmt_v2(r.get('hold_ratio'), '%')} |")

    if not quote_data and not nb_data and not sh_data:
        lines.append("> 资金与筹码数据暂无。")

    lines.append("")
    lines.append("🔍 **待独立验证:** 北向资金为估算值，股东数据可能有报告期滞后。")
    return "\n".join(lines)


# --- render_technical_section ---
def render_technical_section(dims: dict[str, dict], collection: dict = None) -> str:
    """技术结构（kline → technical.py 计算 + 渲染）。"""
    kline_data = _get_dim_data(dims, "kline")
    lines = ["## 五、技术结构", ""]

    if not kline_data or not isinstance(kline_data, list) or len(kline_data) == 0:
        lines.append("> K 线数据不可得，跳过技术分析。")
        lines.append("")
        lines.append("🔍 **待独立验证:** 确认日K线维度采集成功。")
        return "\n".join(lines)

    meta = _get_dim_meta(dims, "kline")
    source = meta.get("source", "未知")
    lines.append(f"[复权: 不复权 / 来源: {source}]")

    kline_data = sort_kline_asc(kline_data)
    tech = compute(kline_data)

    if "error" in tech:
        lines.append(f"> 技术指标计算失败: {sanitize_error(tech.get('message', '未知错误'), 80)}")
        return "\n".join(lines)

    # --- 趋势 ---
    trend = tech["trend"]
    lines.append("")
    lines.append("### 趋势")
    alignment = trend.get("alignment", {})
    lines.append(f"**均线排列:** {alignment.get('trend_label', '?')}")

    # MA 关键值
    ma = trend.get("ma", {})
    ma_strs = []
    for p in ("5", "10", "20", "60", "120"):
        vals = ma.get(p, [])
        if vals and vals[-1] is not None:
            ma_strs.append(f"MA{p}={vals[-1]:.2f}")
    if ma_strs:
        lines.append(f"**均线位置:** {', '.join(ma_strs)}")

    # MA250
    vals_250 = ma.get("250", [])
    if vals_250 and vals_250[-1] is not None:
        lines.append(f"**MA250:** {vals_250[-1]:.2f}")
    else:
        avail = trend.get("ma_availability", {}).get("250", "")
        if avail:
            lines.append(f"**MA250:** {avail}")

    # 均线斜率
    slopes = trend.get("slope", {})
    slope_strs = []
    for p in ("20", "60"):
        s = slopes.get(p)
        if s is not None:
            slope_strs.append(f"MA{p}斜率 {'+' if s >= 0 else ''}{s:.1f}%")
    if slope_strs:
        lines.append(f"**均线斜率:** {', '.join(slope_strs)}")

    # 趋势摘要
    sentences = trend.get("summary_sentences", [])
    for s in sentences:
        lines.append(f"- {s}")

    # --- 动量 ---
    macd = tech["momentum"]["macd"]
    lines.append("")
    lines.append("### 动量")
    if macd.get("available"):
        lines.append(f"**MACD:** DIF={macd['dif']}, DEA={macd['dea']}, 柱={macd['histogram']}")
        cross = macd.get("cross", {})
        if cross:
            lines.append(f"**DIF/DEA:** {cross.get('desc', '?')}")
        if macd.get("histogram_trend"):
            lines.append(f"**柱体:** {macd['histogram_trend']}")
    else:
        lines.append(f"MACD: {macd.get('reason', '不可得')}")

    # --- 超买超卖 ---
    rsi = tech["overbought_oversold"].get("rsi", {})
    kdj = tech["overbought_oversold"].get("kdj", {})
    lines.append("")
    lines.append("### 超买超卖")
    rsi_strs = []
    for p in ("6", "12", "24"):
        r = rsi.get(p, {})
        if r.get("available"):
            rsi_strs.append(f"RSI({p})={r['value']:.1f}（{r['zone']}）")
        elif r.get("reason"):
            rsi_strs.append(f"RSI({p}): {r['reason']}")
    lines.append("; ".join(rsi_strs) if rsi_strs else "RSI: 不可得")

    if kdj.get("available"):
        lines.append(f"**KDJ:** K={kdj['k']:.1f}, D={kdj['d']:.1f}, J={kdj['j']:.1f}")
    else:
        reason = kdj.get("reason", "不可得")
        lines.append(f"**KDJ:** {reason}")

    # --- 波动 ---
    vol = tech["volatility"]
    boll = vol.get("boll", {})
    atr = vol.get("atr", {})
    lines.append("")
    lines.append("### 波动")
    if boll.get("available"):
        pos = boll.get("position", "")
        pos_str = f"，收盘价{pos}" if pos else ""
        lines.append(f"**BOLL:** 上轨 {boll['upper']}, 中轨 {boll['mid']}, 下轨 {boll['lower']}{pos_str}")
    else:
        lines.append(f"**BOLL:** {boll.get('reason', '不可得')}")

    if atr.get("available"):
        lines.append(f"**ATR(14):** {atr['value']}")
    else:
        lines.append(f"**ATR(14):** {atr.get('reason', '不可得')}")

    # --- 成交量 ---
    vol_info = tech["volume"]
    lines.append("")
    lines.append("### 成交量")
    if vol_info.get("status"):
        lines.append(f"**量比:** {vol_info['status']}")
    lines.append(f"**近5日均量:** {vol_info.get('avg_vol_5d', '-')}")
    if vol_info.get("recent_spike_days", 0) > 0:
        lines.append(f"近20日有 {vol_info['recent_spike_days']} 日量比>1.5")

    # --- 结构 ---
    structure = tech["structure"]
    extremes = structure.get("extremes", {})
    dd = structure.get("drawdown_60d", {})
    lines.append("")
    lines.append("### 结构")

    for n in (20, 60, 120):
        ex = extremes.get(n, {})
        if ex.get("available"):
            lines.append(f"- 近{n}日最高 {ex['max']}（{ex.get('max_date', '')}），最低 {ex['min']}（{ex.get('min_date', '')}）")
            if ex.get("is_n_day_high"):
                lines.append(f"  → 当前处近{n}日新高")

    if dd.get("available"):
        lines.append(f"- 近60日最大回撤: {dd['drawdown_pct']:.1f}%（峰值 {dd['peak']} 于 {dd.get('peak_date', '')}）")

    lines.append("")
    lines.append("🔍 **待独立验证:** 技术指标基于不复权收盘价计算。均线/RSI/MACD 为描述性统计，不构成交易信号。")
    return "\n".join(lines)


# --- _section_events_placeholder ---
def _section_events_placeholder() -> str:
    """事件催化占位（v0.1.2 不实现自动分析）。"""
    return """## 六、事件催化

> 本节由分析阶段（Claude）根据公告、新闻、行业动态撰写，非引擎自动生成。
> v0.1.2 引擎仅提供数据卡片，Claude 应通过 WebSearch 补充近期事件。

（待分析阶段填写）

🔍 **待独立验证:** 事件分析依赖 WebSearch 结果，应标注每条信息的 URL 来源。"""


# --- _section_thesis_placeholder ---
def _section_thesis_placeholder(dims: dict[str, dict]) -> str:
    """核心矛盾占位（v0.1.2 引擎只填数据卡片）。"""

    # 尝试提取关键数据（统一升序后取末位）
    fin_data = _get_dim_data(dims, "financials")
    roe_str = "?"
    if fin_data and isinstance(fin_data, list) and fin_data:
        fin_sorted = sort_kline_asc(fin_data)
        latest = fin_sorted[-1]
        roe = latest.get("roe")
        if roe is not None:
            roe_str = f"{roe}%"

    pe_str = "?"
    val_data = _get_dim_data(dims, "valuation")
    if val_data and isinstance(val_data, list) and val_data:
        val_sorted = sort_kline_asc(val_data)
        pe = val_sorted[-1].get("pe_ttm")
        if pe is not None:
            pe_str = f"{pe}x"

    trend_str = "?"
    kline_data = _get_dim_data(dims, "kline")
    if kline_data and isinstance(kline_data, list) and len(kline_data) >= 20:
        tech = compute(kline_data)
        if "error" not in tech:
            trend_str = tech["trend"]["alignment"].get("trend_label", "?")

    return f"""## ⚡ 核心矛盾（当前最值得跟踪的问题）

> 本节由分析阶段（Claude）根据上下文数据卡片撰写，非引擎自动生成。
> 数据输入: 经营质量 ROE={roe_str} | 估值 PE={pe_str} | 技术趋势={trend_str}

（待分析阶段填写）"""




# --- _v3_law11_trigger_d ---
def _v3_law11_trigger_d(dims: dict[str, dict]) -> bool:
    """LAW 11 触发源 D：52 周高低区间极端，或价格贴近 MA60 盘整。"""
    kline = _get_dim_data(dims, "kline")
    if not kline or not isinstance(kline, list):
        return False

    rows = sort_kline_asc(kline)
    closes = [float(r["close"]) for r in rows if r.get("close") is not None]
    if len(closes) < 60:
        return False

    n52 = min(len(closes), 250)
    window = closes[-n52:]
    hi, lo = max(window), min(window)
    cur = closes[-1]
    if hi > lo:
        pos = (cur - lo) / (hi - lo)
        if pos >= 0.85 or pos <= 0.15:
            return True

    tech = compute(rows)
    if "error" in tech:
        return False
    ma60_vals = tech["trend"]["ma"].get("60") or []
    ma60 = ma60_vals[-1] if ma60_vals else None
    if ma60 is not None and float(ma60) > 0:
        if abs(cur - float(ma60)) / float(ma60) <= 0.03:
            return True
    return False


# --- _v3_multi_source_consistency ---
def _v3_multi_source_consistency(dims: dict[str, dict]) -> tuple[str, str]:
    """模块 1 多源一致性：🟢 多源并行 / 🟡 部分降级 / 🔴 单源或不可得。"""
    checks: list[str] = []
    for key in ("quote", "valuation", "kline", "financials"):
        meta = _get_dim_meta(dims, key)
        all_src = meta.get("all_sources") or []
        if not all_src:
            if _get_dim_data(dims, key) is not None:
                checks.append("single")
            else:
                checks.append("gap")
            continue
        avail = [s for s in all_src if s.get("data_available")]
        tried = [s for s in all_src if s.get("data_available") or s.get("error")]
        if len(avail) >= 2:
            checks.append("multi")
        elif len(avail) == 1 and len(tried) >= 2:
            checks.append("degraded")
        elif avail:
            checks.append("single")
        else:
            checks.append("gap")
    if not checks:
        return "🔴", "核心维度无可比对的并行取证记录"
    multi_n = sum(1 for c in checks if c == "multi")
    degraded_n = sum(1 for c in checks if c == "degraded")
    gap_n = sum(1 for c in checks if c in ("gap", "single"))
    if multi_n >= 2 and gap_n == 0:
        return "🟢", f"{multi_n} 个核心维度具备多源并行取证且均有数据"
    if multi_n >= 1 or degraded_n >= 1:
        return "🟡", (
            f"多源 {multi_n} / 降级 {degraded_n} / 单源或缺口 {gap_n}；"
            "极端值需对照 primary 源与附录追溯表"
        )
    return "🔴", "核心维度以单源或不可得为主，交叉验证能力受限"


# --- _v3_ms_availability_note ---
def _v3_ms_availability_note(availability: dict, key: str) -> str:
    status = (availability or {}).get(key, "")
    if status == "available":
        return ""
    if status.startswith("partial"):
        return f"（{status}）"
    if status.startswith("unavailable"):
        reason = status.split(":", 1)[-1].strip()
        return f"（不可得：{reason}）"
    return "（不可得）" if status else ""


# --- _v3_build_candidate_explanations ---
def _v3_build_candidate_explanations(
    *,
    chg: float | None,
    window_label: str,
    chg_s: str,
    dims: dict[str, dict],
    market_structure: dict,
    val_cache: dict | None = None,
) -> list[tuple[str, str, str, str]]:
    """LAW 13 候选解释，最多 5 条。返回 (标签, 文本, 证据, 强度)。"""
    explanations: list[tuple[str, str, str, str]] = []
    pe_pct, pb_pct, _ = _v3_valuation_percentiles(dims, val_cache)
    sw = market_structure.get("sw_index") or {}
    mf = market_structure.get("moneyflow") or {}
    nb = market_structure.get("northbound") or {}
    mf_net, mf_key = resolve_moneyflow(mf)

    if chg is not None:
        explanations.append((
            "A",
            f"价格{window_label}变动 {chg_s} 可能与估值/资金因子共振",
            "见下方多因子矩阵",
            "⚠️",
        ))
    else:
        explanations.append((
            "A",
            "K 线不足，价格变化幅度不可得",
            "kline 维度",
            "❓",
        ))

    if pe_pct is not None and (pe_pct >= 80 or pe_pct <= 20):
        zone = "偏高" if pe_pct >= 80 else "偏低"
        explanations.append((
            "B",
            f"估值历史分位{zone}（PE {pe_pct:.1f}%）驱动定价预期重估",
            "valuation 历史分位",
            "⚠️",
        ))
    elif pb_pct is not None and (pb_pct >= 80 or pb_pct <= 20):
        zone = "偏高" if pb_pct >= 80 else "偏低"
        explanations.append((
            "B",
            f"PB 历史分位{zone}（{pb_pct:.1f}%）或反映资产定价差异",
            "valuation 历史分位",
            "⚠️",
        ))

    rel = sw.get("relative_vs_benchmark_pct")
    svi = sw.get("stock_vs_industry_pct")
    if rel is not None and abs(rel) >= 5:
        explanations.append((
            "C",
            f"行业板块相对沪深300 {rel:+.2f}%，行业景气或拖累/支撑个股",
            sw.get("source", "sw_daily"),
            "⚠️",
        ))
    elif svi is not None and abs(svi) >= 5:
        explanations.append((
            "C",
            f"个股相对行业 {svi:+.2f}%，个股特异性因素可能主导",
            sw.get("source", "sw_daily"),
            "⚠️",
        ))

    kline = _get_dim_data(dims, "kline")
    if kline and isinstance(kline, list):
        tech = compute(sort_kline_asc(kline))
        if "error" not in tech:
            label = tech["trend"]["alignment"].get("trend_label", "")
            if label:
                explanations.append((
                    "D",
                    f"技术趋势结构（{label}）与价格动量方向一致或背离",
                    "technical.py MA 排列",
                    "⚠️",
                ))

    mf_v = mf_net
    nb_v = nb.get("net_sum_10d")
    if chg is not None and mf_v is not None:
        price_up = chg > 0
        flow_in = float(mf_v) > 0
        if price_up != flow_in:
            explanations.append((
                "E",
                f"价格{window_label}{chg_s} 与{moneyflow_cv_window(mf_key)}净额方向不一致，或存在博弈/滞后",
                mf.get("source", "moneyflow"),
                "❓",
            ))
    elif nb_v is not None and mf_v is not None:
        if float(nb_v) * float(mf_v) < 0:
            explanations.append((
                "E",
                "北向与主力资金方向相反，资金归因存在分歧",
                f"{nb.get('source', '')} vs {mf.get('source', '')}",
                "❓",
            ))

    return explanations[:5]


# --- _v3_pick_dominant_factor ---
def _v3_pick_dominant_factor(rows: list[str]) -> str:
    """从矩阵行中选取方向明确且强度最高的因子。"""
    scored: list[tuple[int, str, str, str]] = []
    for row in rows:
        if "跳过" in row or "---" in row:
            continue
        parts = [p.strip() for p in row.split("|")]
        if len(parts) < 5:
            continue
        cat, signal, direction, strength = parts[1], parts[2], parts[3], parts[4]
        if direction in ("—", "→中性"):
            continue
        weight = 2 if "⚠️" in strength else (1 if "❓" in strength else 0)
        if weight:
            scored.append((weight, cat, direction, signal))
    if not scored:
        return "数据不足，暂无法声明主导因子；可持续性：待观察"
    scored.sort(key=lambda x: (-x[0], x[1]))
    _, cat, direction, signal = scored[0]
    return f"{cat}（{signal}，{direction}）；可持续性：待观察"


# --- _executive_core_contradictions ---
def _executive_core_contradictions(
    collection: dict,
    dims: dict[str, dict],
    val_cache: dict | None = None,
) -> list[str]:
    """从已有数据卡片提炼两条核心矛盾（数据驱动，非占位）。"""
    items: list[str] = []
    pe_pct, pb_pct, _ = _v3_valuation_percentiles(dims, val_cache)

    roe = eps = None
    fin = dims.get("financials", {}).get("data")
    if isinstance(fin, list) and fin:
        latest = fin[-1]
        roe = latest.get("roe")
        eps = latest.get("eps")

    if pe_pct is not None and roe is not None:
        try:
            roe_f = float(roe)
            if pe_pct >= 70 and roe_f < 10:
                items.append(
                    f"估值历史位置偏高（PE {pe_pct:.0f}%）vs 盈利质量偏弱"
                    f"（ROE {roe_f:.1f}%）[来源: valuation+financials]"
                )
            elif pe_pct <= 30 and roe_f >= 12:
                items.append(
                    f"估值历史位置偏低（PE {pe_pct:.0f}%）vs 盈利质量尚可"
                    f"（ROE {roe_f:.1f}%）[来源: valuation+financials]"
                )
        except (TypeError, ValueError):
            pass

    ms = collection.get("market_structure") or {}
    nb = ms.get("northbound") or dims.get("northbound", {}).get("data") or {}
    net10 = nb.get("net_sum_10d") if isinstance(nb, dict) else None
    quote = dims.get("quote", {}).get("data") or {}
    chg = quote.get("change_pct") if isinstance(quote, dict) else None
    if net10 is not None and chg is not None:
        try:
            net_f, chg_f = float(net10), float(chg)
            if net_f > 0 and chg_f < -2:
                items.append(
                    f"北向近10日净流入 {net_f:+.0f} 与股价 {chg_f:+.1f}% 背离"
                    f"[来源: northbound+quote]"
                )
            elif net_f < 0 and chg_f > 2:
                items.append(
                    f"北向近10日净流出 {net_f:+.0f} 与股价 {chg_f:+.1f}% 背离"
                    f"[来源: northbound+quote]"
                )
        except (TypeError, ValueError):
            pass

    cred = collection.get("credibility") or {}
    if cred:
        low = [k for k, v in cred.items() if v < 50]
        if low:
            items.append(
                f"可信度偏低维度: {', '.join(low[:3])}"
                f"{'…' if len(low) > 3 else ''} [来源: rerank]"
            )

    while len(items) < 2:
        items.append("独立维度交叉验证不足，需补充外部信源 [推测，待验证]")
    return items[:2]


# --- _section_executive_summary ---
def _section_executive_summary(collection, symbol, dims, val_cache=None):
    """生成一屏内可读的执行摘要：一行话定位 + 两条矛盾 + 三个观察点。"""
    lines = ["## 执行摘要", ""]

    basic = dims.get("basic_info", {}).get("data", {})
    name = ""
    industry = ""
    if isinstance(basic, dict):
        name = basic.get("name", "") or basic.get("股票简称", "")
        industry = basic.get("industry", "")

    pe_pct, pb_pct, pe_zone = _v3_valuation_percentiles(dims, val_cache)

    name_str = f"{symbol} {name}".strip()
    industry_str = f"（{industry}）" if industry else ""
    summary = _v3_load_valuation_summary(dims, val_cache)
    pe_median = (summary.get("pe") or {}).get("median") if summary else None
    if pe_pct is not None:
        median_part = f"（中位数 {pe_median:.2f}x）" if pe_median is not None else ""
        pe_str = f"PE 历史位置 {pe_pct:.1f}%{median_part}"
    else:
        pe_str = "PE 不可得"
    lines.append(f"**{name_str}**{industry_str} — {pe_str}")
    lines.append("")

    lines.append("**核心矛盾：**")
    for i, item in enumerate(_executive_core_contradictions(collection, dims, val_cache), 1):
        lines.append(f"{i}. {item}")
    lines.append("")

    lines.append("**关键观察点：**")
    fin = dims.get("financials", {}).get("data")
    if fin and isinstance(fin, list) and fin:
        latest = fin[-1]
        rot = latest.get('roe', '?')
        eps = latest.get('eps', '?')
        lines.append(f"- 财务: 最近报告期 ROE={rot}%, EPS={eps}")
    else:
        lines.append("- 财务: 数据不可得")

    quote = dims.get("quote", {}).get("data", {})
    if isinstance(quote, dict):
        price = coalesce_field(quote, "close", "price")
        if price:
            lines.append(f"- 行情: 最新价 {price}")

    ms_icon, ms_detail = _v3_multi_source_consistency(dims)
    lines.append(f"- 数据质量: {ms_icon} {ms_detail}")

    return "\n".join(lines)


# --- _section_research_question ---
def _section_research_question(
    collection: dict, symbol: str, *, val_cache: dict | None = None,
) -> str:
    dims = _index_dims(collection)
    lines = ["## 0. 研究问题卡", ""]
    triggers: list[str] = []

    chg, window = _v3_price_change(dims)
    if chg is not None and window is not None and window >= 20 and abs(chg) >= 10:
        triggers.append("A")

    pe_pct, pb_pct, _ = _v3_valuation_percentiles(dims, val_cache)
    if (pe_pct is not None and (pe_pct >= 80 or pe_pct <= 20)) or (
        pb_pct is not None and (pb_pct >= 80 or pb_pct <= 20)
    ):
        triggers.append("B")

    ms = collection.get("market_structure") or {}
    sw = ms.get("sw_index") or {}
    rel = sw.get("relative_vs_benchmark_pct")
    if rel is not None and abs(rel) >= 5:
        triggers.append("C")

    if _v3_law11_trigger_d(dims):
        triggers.append("D")

    trigger_labels = {
        "A": "变化驱动（价格/财报/公告异动）",
        "B": "估值位置驱动（历史分位极端）",
        "C": "行业结构驱动（板块相对强弱）",
        "D": "趋势结构驱动（价格区间/均线结构）",
    }
    if triggers:
        lines.append("**激活的触发源:** " + "、".join(f"{t} {trigger_labels[t]}" for t in triggers))
    else:
        lines.append("**激活的触发源:** 暂无明确触发（以事实快照为主构建问题）")

    lines.extend([
        "", "```",
        f"核心问题：{symbol} 当前价格与基本面/市场结构之间，哪些驱动力尚不确定？",
        "└── 子问题 ① 近 20 日价格变化能否被财务与估值数据解释？",
        "└── 子问题 ② 资金与行业情绪信号是否指向相反方向？",
        "└── 子问题 ③ 若主导解释成立，对估值定价的传导路径是什么？",
        "",
        "为什么这是好问题：将可验证数据与未决不确定性分离，避免把相关性误读为因果。",
        "```",
    ])
    lines.append("🔍 **待独立验证:** 触发源依赖采集数据完整性；公告/政策类触发需 WebSearch 补充。")
    return "\n".join(lines)


# --- _load_report_key_diff ---
def _load_report_key_diff(symbol: str, collection: dict) -> dict | None:
    """若 store 有历史快照，返回当前采集相对上次的关键字段 diff。"""
    try:
        from lib.store import load_key_diff_vs_stored
        return load_key_diff_vs_stored(symbol, collection)
    except Exception:
        return None


# --- _snapshot_diff_block ---
def _snapshot_diff_block(key_diff: dict) -> str:
    from lib.store import format_key_diff_markdown_lines

    old_at = key_diff.get("old_at", "")[:19]
    new_at = key_diff.get("new_at", "")[:19]
    lines = ["", "### 相对上次调研变化", ""]
    if old_at and new_at:
        lines.append(f"对比区间：{old_at} → {new_at}（本次采集）")
        lines.append("")
    lines.extend(format_key_diff_markdown_lines(key_diff))
    lines.append("")
    lines.append(
        "🔍 **待独立验证:** 跨时点变化基于 store 快照字段提取，"
        "应与 `invest.py diff` 输出交叉核对。"
    )
    return "\n".join(lines)


# --- _section_snapshot ---
def _section_snapshot(
    collection: dict,
    symbol: str,
    dims: dict[str, dict],
    *,
    val_cache: dict | None = None,
    key_diff: dict | None = None,
) -> str:
    lines = ["## 1. 当前状态快照", ""]
    quote = _get_dim_data(dims, "quote")
    if isinstance(quote, dict):
        price = coalesce_field(quote, "close", "price")
        chg = quote.get("change_pct")
        if price is not None:
            chg_s = f"（{chg:+.2f}%）" if chg is not None else ""
            lines.append(f"- **最新价:** {price}{chg_s}")

    pe_pct, pb_pct, pe_zone = _v3_valuation_percentiles(dims, val_cache)
    if pe_pct is not None:
        lines.append(f"- **PE(TTM) 历史分位:** {pe_pct:.1f}%（{pe_zone or '—'}）")
    if pb_pct is not None:
        lines.append(f"- **PB 历史分位:** {pb_pct:.1f}%")

    fin = _get_dim_data(dims, "financials")
    if fin and isinstance(fin, list):
        fin = sort_kline_asc(fin)
        latest = fin[-1]
        lines.append(
            f"- **最近财报:** {latest.get('end_date', '?')} "
            f"ROE={latest.get('roe', '-')}%, 净利润={_fmt_v2(latest.get('net_profit'))}"
        )
        np_v = latest.get("net_profit")
        ocf = latest.get("ocf") if latest.get("ocf") is not None else latest.get("n_cashflow_act")
        if np_v is not None and ocf is not None:
            np_f, ocf_f = float(np_v), float(ocf)
            if np_f > 0 and ocf_f > 0:
                ratio = ocf_f / np_f
                cv_status = "convergence" if ratio >= 0.5 else "divergence"
            elif np_f < 0 and ocf_f < 0:
                cv_status = "divergence"
            elif np_f == 0 or ocf_f == 0:
                cv_status = "gap"
            else:
                cv_status = "divergence"
            cv_detail = f"净利润 {_fmt_v2(np_v)} vs 经营现金流 {_fmt_v2(ocf)}"
            lines.append("")
            lines.append(_cv(cv_status, "CV-1", "净利润 vs 经营现金流", cv_detail, "中（单期财报）"))
        else:
            lines.append("")
            lines.append(_cv(
                "gap", "CV-1", "净利润 vs 经营现金流",
                "经营现金流字段不可得，无法交叉验证利润质量", "低",
            ))

    if pe_pct is not None and pb_pct is not None:
        if (pe_pct >= 70 and pb_pct >= 70) or (pe_pct <= 30 and pb_pct <= 30):
            cv3 = "convergence"
            cv3d = f"PE 分位 {pe_pct:.1f}% 与 PB 分位 {pb_pct:.1f}% 同向"
        else:
            cv3 = "divergence"
            cv3d = f"PE 分位 {pe_pct:.1f}% 与 PB 分位 {pb_pct:.1f}% 方向不一致"
        lines.append("")
        lines.append(_cv(cv3, "CV-3", "PE 分位 vs PB 分位", cv3d, "中"))

    ms_icon, ms_detail = _v3_multi_source_consistency(dims)
    ms_strength = {"🟢": "✅", "🟡": "⚠️", "🔴": "❓"}.get(ms_icon, "⚠️")
    lines.extend([
        "",
        "### 多源一致性",
        f"{ms_icon} **并行取证状态** — {ms_detail}",
    ])

    if key_diff is None:
        key_diff = _load_report_key_diff(symbol, collection)
    if key_diff and key_diff.get("categories"):
        lines.append(_snapshot_diff_block(key_diff))

    lines.append("")
    lines.append(_evidence_conclusion_block(
        "当前快照呈现价格、估值与最近财报的并列事实",
        [
            ("✅", "行情与估值数据来自采集维度 primary 源"),
            (ms_strength, f"多源一致性：{ms_detail}"),
        ],
    ))
    lines.append("")
    lines.append("🔍 **待独立验证:** 快照数字应与财报 PDF / 交易所行情交叉核对。")

    futures_block = _render_pricing_futures_section(collection.get("industry_pricing", {}))
    if futures_block:
        lines.append("")
        lines.append(futures_block)

    return "\n".join(lines)


# --- _v3_driver_unavailable ---
def _v3_driver_unavailable(category: str) -> DriverFactor:
    return DriverFactor(category, "[数据源不可用，该因子跳过]", "—", "—", "—")


# --- _section_dynamic_drivers ---
def _section_dynamic_drivers(
    collection: dict, symbol: str, dims: dict[str, dict], market_structure: dict,
    *, val_cache: dict | None = None,
) -> str:
    lines = ["## 2. 动态驱动分析", ""]
    chg, window = _v3_price_change(dims)
    window_label = _v3_price_window_label(window)
    chg_s = f"{chg:+.2f}%" if chg is not None else "不可得"
    lines.append(f"{window_label}涨跌幅：**{chg_s}**（采集: {collection.get('fetched_at', '')[:10]}）")
    lines.append("")
    lines.append("### 候选解释（LAW 13，上限 5 条）")
    lines.append("")
    candidates = _v3_build_candidate_explanations(
        chg=chg,
        window_label=window_label,
        chg_s=chg_s,
        dims=dims,
        market_structure=market_structure,
        val_cache=val_cache,
    )
    for label, text, evidence, strength in candidates:
        lines.append(f"→ 解释 {label}：{text}")
        lines.append(f"   证据：{evidence}")
        lines.append(f"   强度：{strength}")
        lines.append("")
    if len(candidates) < 5:
        lines.append("⚠️ **尚无候选解释的部分：** 公告/政策类事件需 WebSearch 或 anns 数据补充。")
        lines.append("")
    lines.append("### 多因子驱动矩阵")
    lines.append("")
    lines.append("| 因子类别 | 具体信号 | 方向 | 强度 | 数据来源 |")
    lines.append("|---------|---------|------|------|---------|")

    factors: list[DriverFactor] = []
    fin = _get_dim_data(dims, "financials")
    np_now, np_prev = None, None
    if fin and isinstance(fin, list) and len(fin) >= 2:
        fin = sort_kline_asc(fin)
        np_now = fin[-1].get("net_profit")
        np_prev = fin[-2].get("net_profit")
        if np_now is not None and np_prev is not None:
            d = "↑正向" if float(np_now) > float(np_prev) else ("↓负向" if float(np_now) < float(np_prev) else "→中性")
            factors.append(DriverFactor("基本面", "净利润环比", d, "⚠️", "financials"))
        else:
            factors.append(DriverFactor("基本面", "净利润", "→中性", "❓", "financials"))
    else:
        factors.append(_v3_driver_unavailable("基本面"))

    sw = market_structure.get("sw_index")
    if sw and sw.get("return_20d_pct") is not None:
        r = sw["return_20d_pct"]
        d = "↑正向" if r > 0 else ("↓负向" if r < 0 else "→中性")
        factors.append(DriverFactor(
            "行业景气", f"申万板块 20 日 {r:+.2f}%", d, "⚠️", sw.get("source", "sw_daily"),
        ))
    else:
        factors.append(_v3_driver_unavailable("行业景气"))

    nb = market_structure.get("northbound")
    if nb and nb.get("net_sum_10d") is not None:
        v = nb["net_sum_10d"]
        d = "↑正向" if v > 0 else ("↓负向" if v < 0 else "→中性")
        factors.append(DriverFactor(
            "资金（北向）", _v3_northbound_signal_label(nb), d, "⚠️", nb.get("source", ""),
        ))
    else:
        factors.append(_v3_driver_unavailable("资金（北向）"))

    mf = market_structure.get("moneyflow")
    mf_net, mf_key = resolve_moneyflow(mf)
    if mf_net is not None:
        d = "↑正向" if mf_net > 0 else ("↓负向" if mf_net < 0 else "→中性")
        factors.append(DriverFactor(
            "资金（主力）", f"{moneyflow_signal_label(mf_key)} {fmt_amount(mf_net)}", d, "⚠️", mf.get("source", "") if isinstance(mf, dict) else "",
        ))
    else:
        factors.append(_v3_driver_unavailable("资金（主力）"))

    mg = market_structure.get("margin")
    if mg and mg.get("change_pct") is not None:
        v = mg["change_pct"]
        d = "↑正向" if v > 0 else ("↓负向" if v < 0 else "→中性")
        factors.append(DriverFactor(
            "情绪（融资）", f"融资余额变化 {v:+.2f}%", d, "⚠️", mg.get("source", ""),
        ))
    else:
        factors.append(_v3_driver_unavailable("情绪（融资）"))

    to = market_structure.get("turnover")
    if to and to.get("ratio_5_60") is not None:
        r = to["ratio_5_60"]
        d = "↑正向" if r > 1.1 else ("↓负向" if r < 0.9 else "→中性")
        factors.append(DriverFactor(
            "情绪（换手）", f"5日/60日换手比 {r:.2f}", d, "⚠️", to.get("source", ""),
        ))
    else:
        factors.append(_v3_driver_unavailable("情绪（换手）"))

    kline = _get_dim_data(dims, "kline")
    ma_dir = "→中性"
    ma_strength = "❓"
    fin_dir = "→中性"
    if kline and isinstance(kline, list):
        tech = compute(sort_kline_asc(kline))
        if "error" not in tech:
            label = tech["trend"]["alignment"].get("trend_label", "")
            if "多头" in label:
                ma_dir, ma_strength = "↑正向", "⚠️"
            elif "空头" in label:
                ma_dir, ma_strength = "↓负向", "⚠️"
            factors.append(DriverFactor(
                "技术趋势", label or "MA 排列", ma_dir, ma_strength, "technical.py",
            ))
        else:
            factors.append(_v3_driver_unavailable("技术趋势"))
    else:
        factors.append(_v3_driver_unavailable("技术趋势"))

    if np_now is not None and np_prev is not None:
        fin_dir = "↑正向" if float(np_now) > float(np_prev) else (
            "↓负向" if float(np_now) < float(np_prev) else "→中性")

    # 事件催化因子 — from collection["events"]
    events_list = collection.get("events") or []
    if events_list and isinstance(events_list, list) and len(events_list) > 0:
        event_count = len(events_list)
        summary = (collection.get("_meta") or {}).get("events_summary") or {}
        top_types = summary.get("top_types", [])
        top_types_str = "、".join(
            f"{t['type']}({t['count']})" for t in top_types[:3]
        ) if top_types else f"{event_count}条"
        event_label = f"近{summary.get('window_days', 30)}日 {event_count}条事件（{top_types_str}）"
        factors.append(DriverFactor(
            "事件催化", event_label, "→中性", "⚠️",
            "akshare stock_individual_notice_report",
        ))
    else:
        factors.append(DriverFactor(
            "事件催化", "事件数据暂不可用（akshare 公告接口未返回数据）", "—", "—",
            "akshare stock_individual_notice_report",
        ))
    rows = [f.to_matrix_row() for f in factors]
    lines.extend(rows)

    pos = sum(1 for r in rows if "↑正向" in r)
    neg = sum(1 for r in rows if "↓负向" in r)
    neu = len(rows) - pos - neg
    lines.extend([
        "",
        f"因子方向一致性：{pos} 正向 / {neg} 负向 / {neu} 中性或跳过",
        "",
        "### 因子交叉验证结论",
    ])
    if ma_dir == fin_dir and ma_dir != "→中性" and fin_dir != "→中性":
        lines.append(_cv(
            "convergence", "CV-6", "MA 趋势 vs 近期业绩方向",
            f"技术趋势 {ma_dir} 与净利润环比方向 {fin_dir} 一致", "中",
        ))
    elif ma_dir != "→中性" and fin_dir != "→中性" and ma_dir != fin_dir:
        lines.append(_cv(
            "divergence", "CV-6", "MA 趋势 vs 近期业绩方向",
            f"技术趋势 {ma_dir} 与净利润环比方向 {fin_dir} 不一致", "中",
        ))
    else:
        lines.append(_cv(
            "gap", "CV-6", "MA 趋势 vs 近期业绩方向",
            "技术或业绩方向数据不足", "低",
        ))

    lines.append("")
    dominant = _v3_pick_dominant_factor(rows)
    lines.append(f"→ **主导因子（声明）:** {dominant}")
    lines.append("")
    lines.append("🔍 **待独立验证:** 候选解释仅为假说列表，非因果归因。")

    news_block = _render_pricing_news_section(collection.get("industry_pricing", {}))
    if news_block:
        lines.append("")
        lines.append(news_block)

    return "\n".join(lines)


# --- _section_participant_behavior_scan ---
def _section_participant_behavior_scan(
    collection: dict,
    symbol: str,
    market_structure: dict,
    dims: dict,
) -> str:
    return build_participant_behavior_section(collection, symbol, market_structure, dims)


# --- _section_market_structure ---
def _section_market_structure(
    collection: dict, symbol: str, market_structure: dict, *, val_cache: dict | None = None,
) -> str:
    lines = ["## 3. 市场结构分析", ""]
    sw = market_structure.get("sw_index")
    if sw:
        ret = sw.get("return_20d_pct")
        ret_s = f"{ret}%" if ret is not None else "-"
        lines.append(f"- **申万行业指数:** {sw.get('index_code', '?')} 20日涨跌 {ret_s}")
        svi = sw.get("stock_vs_industry_pct")
        if svi is not None:
            lines.append(f"- **个股 vs 行业:** {svi:+.2f}%")
        stock_ret = sw.get("stock_return_20d_pct")
        ind_ret = sw.get("return_20d_pct")
        if stock_ret is not None and ind_ret is not None:
            svi_s = f"{svi:+.2f}%" if svi is not None else "-"
            if stock_ret * ind_ret > 0 or (stock_ret == 0 and ind_ret == 0):
                cv5 = "convergence"
                cv5d = (
                    f"个股 20 日 {stock_ret:+.2f}% 与行业 {ind_ret:+.2f}% 同向"
                    f"（个股相对板块 {svi_s}）"
                )
            elif stock_ret != 0 and ind_ret != 0:
                cv5 = "divergence"
                cv5d = (
                    f"个股 20 日 {stock_ret:+.2f}% 与行业 {ind_ret:+.2f}% 反向"
                    f"（个股相对板块 {svi_s}）"
                )
            else:
                cv5 = "gap"
                cv5d = f"个股或行业 20 日涨跌有一方为零（个股相对板块 {svi_s}）"
            lines.append("")
            lines.append(_cv(cv5, "CV-5", "申万板块 vs 个股相对强弱", cv5d, "中"))
        rel = sw.get("relative_vs_benchmark_pct")
        if rel is not None:
            lines.append(f"- **板块相对沪深300:** {rel:+.2f}%")
    else:
        lines.append("> 申万行业指数不可得。")

    nb = market_structure.get("northbound")
    mf = market_structure.get("moneyflow")
    mf_net, mf_key = resolve_moneyflow(mf)
    if nb or mf_net is not None:
        lines.append("")
        lines.append("### 资金态度")
        if nb:
            nb_src = nb.get("source", "northbound")
            lines.append(
                f"- 北向个股资金流（{nb_src}）{_v3_northbound_signal_label(nb)}"
            )
        if mf_net is not None:
            lines.append(
                f"- 主力（moneyflow）{moneyflow_signal_label(mf_key)}: {fmt_amount(mf_net)}"
            )
        if nb and mf_net is not None:
            n_v = float(nb.get("net_sum_10d") or 0)
            m_v = mf_net
            if n_v * m_v > 0:
                cv4 = "convergence"
                cv4d = "北向与主力净流入方向一致"
            elif n_v == 0 and m_v == 0:
                cv4 = "convergence"
                cv4d = "北向与主力净流入方向一致"
            elif n_v == 0 or m_v == 0:
                cv4 = "gap"
                cv4d = "资金数据不完整"
            else:
                cv4 = "divergence"
                cv4d = "北向与主力净流入方向相反"
            lines.append("")
            lines.append(_cv(cv4, "CV-4", "北向 vs 主力大单", cv4d, "中"))

    to = market_structure.get("turnover")
    erp = market_structure.get("erp")
    if to or erp:
        lines.append("")
        lines.append("### ERP / 换手")
        if to:
            lines.append(
                f"- 换手率: 5日均 {to.get('avg_5d', '-')}%，60日均 {to.get('avg_60d', '-')}%，"
                f"分位 {to.get('percentile_60d', '-')}%"
            )
        if erp:
            partial_note = "（样本日不足，分位仅供参考）" if erp.get("partial") else ""
            y10_src = erp.get("source", "")
            if "+" in y10_src:
                _, bond_src = y10_src.split("+", 1)
                y10_note = f"；10Y 国债来源: {bond_src}"
            else:
                y10_note = ""
            lines.append(
                f"- ERP（沪深300）: {erp.get('raw', '-')}%，5年分位 {erp.get('percentile_5y', '-')}%"
                f"{partial_note}{y10_note} [对齐样本 {erp.get('erp_days', '-')} 日]"
            )

    pe_pct, _, _ = _v3_valuation_percentiles(_index_dims(collection), val_cache)
    mf_out = mf_net
    cv7 = _v3_cv7_block(pe_pct, mf_out)
    if cv7:
        lines.append("")
        lines.append(cv7)

    avail = market_structure.get("availability") or {}
    pcr = market_structure.get("put_call_ratio")
    sm = market_structure.get("short_margin")
    nhr = market_structure.get("new_high_ratio")
    etf = market_structure.get("etf_flow")

    lines.append("")
    lines.append("### 3b. ETF 资金")
    if etf:
        parts = [f"**{etf.get('ts_code', '510300.SH')}**"]
        if etf.get("net_flow_5d") is not None:
            parts.append(f"近5日估算净流入 {_fmt_v2(etf['net_flow_5d'])}")
        if etf.get("net_flow_10d") is not None:
            parts.append(f"近10日估算净流入 {_fmt_v2(etf['net_flow_10d'])}")
        if etf.get("price_incomplete"):
            parts.append("（收盘价缺失，未估算缺失区间）")
        parts.append(f"[{etf.get('source', '')}]")
        lines.append("- " + "；".join(parts))
    else:
        lines.append(
            f"- ETF 资金流向不可得"
            f"{_v3_ms_availability_note(avail, 'etf_flow')}"
        )

    if pcr or sm or nhr or any(
        avail.get(k) for k in ("put_call_ratio", "short_margin", "new_high_ratio")
    ):
        lines.append("")
        lines.append("### 3c. 情绪指标")
        if pcr:
            partial = "（历史样本不足，分位仅供参考）" if pcr.get("partial") else ""
            pct_5y = pcr.get("percentile_5y")
            pct_60d = pcr.get("percentile_60d")
            if pct_5y is not None and pct_60d is not None and pct_5y != pct_60d:
                pct_s = f"5年分位 {pct_5y}%，60日分位 {pct_60d}%"
            else:
                pct_s = f"分位 {pct_5y if pct_5y is not None else pct_60d if pct_60d is not None else '-'}"
            lines.append(
                f"- **50ETF 认沽认购比:** {pcr.get('ratio', '-')}，"
                f"{pct_s}{partial} "
                f"[{pcr.get('source', '')}]"
            )
        else:
            lines.append(
                f"- **50ETF 认沽认购比:** 不可得"
                f"{_v3_ms_availability_note(avail, 'put_call_ratio')}"
            )
        if sm:
            sm_pct = sm.get("percentile_5y")
            scope = "交易所" if sm.get("scope") == "exchange" else "个股"
            pct_note = f"，5年分位 {sm_pct}%" if sm_pct is not None else ""
            lines.append(
                f"- **融券余额增速（{scope}）:** {sm.get('growth_pct', '-')}%"
                f"{pct_note} [{sm.get('source', '')}]"
            )
        else:
            lines.append(
                f"- **融券余额增速:** 不可得"
                f"{_v3_ms_availability_note(avail, 'short_margin')}"
            )
        if nhr:
            partial = "（采样近似）" if nhr.get("partial") else ""
            lines.append(
                f"- **创新高个股占比:** {nhr.get('ratio_pct', '-')}%"
                f"，60日分位 {nhr.get('percentile_60d', '-')}%"
                f"{partial} [样本 {nhr.get('sample_size', '-')}]"
            )
        else:
            lines.append(
                f"- **创新高个股占比:** 不可得"
                f"{_v3_ms_availability_note(avail, 'new_high_ratio')}"
            )

    ms_evidences: list[tuple[str, str]] = []
    if sw:
        ms_evidences.append((
            "⚠️",
            f"申万行业 20 日涨跌 {sw.get('return_20d_pct', '-')}%"
            f"（{sw.get('index_code', '?')}）",
        ))
    else:
        ms_evidences.append(("❓", "申万行业指数不可得"))
    if nb or mf_net is not None:
        parts = []
        if nb:
            parts.append(f"北向 {_v3_northbound_signal_label(nb)}")
        if mf_net is not None:
            parts.append(f"{moneyflow_cv_window(mf_key)} {fmt_amount(mf_net)}")
        ms_evidences.append(("⚠️", "；".join(parts)))
    else:
        ms_evidences.append(("❓", "北向/主力资金数据不完整"))
    if erp:
        erp_desc = f"ERP {erp.get('raw', '-')}%（5年分位 {erp.get('percentile_5y', '-')}%）"
        if erp.get("partial"):
            erp_desc += "，样本日不足"
        ms_evidences.append(("⚠️", erp_desc))
    elif to:
        ms_evidences.append(("❓", "ERP 不可得，仅换手数据可参考"))
    if pcr:
        pct = pcr.get("percentile_5y") or pcr.get("percentile_60d")
        ms_evidences.append((
            "⚠️",
            f"50ETF 认沽认购比 {pcr.get('ratio', '-')}（分位 {pct if pct is not None else '-'}%）",
        ))
    if sm:
        ms_evidences.append((
            "⚠️",
            f"融券余额增速 {sm.get('growth_pct', '-')}%",
        ))
    lines.append("")
    lines.append(_evidence_conclusion_block(
        "市场结构呈现行业相对强弱、资金态度与 ERP/换手并列事实",
        ms_evidences,
    ))

    # ---- A-6: 价值链位置 + 利润池分布 ----
    company_gm: float | None = None
    fin_dim = collection.get("financials") if isinstance(collection, dict) else None
    fin_rows = fin_dim.get("data") if isinstance(fin_dim, dict) else None
    if isinstance(fin_rows, list) and fin_rows:
        fin_sorted = sort_kline_asc(fin_rows)
        if fin_sorted:
            company_gm = _fin_field_num(fin_sorted[-1], "grossprofit_margin", "gross_margin")
    chain_section = _section_value_chain_position(
        collection.get("chain_context") or {},
        collection.get("industry_pricing") or {},
        company_gm,
    )
    if chain_section:
        lines.append("")
        lines.append(chain_section)

    lines.append("")
    lines.append("🔍 **待独立验证:** Tushare 积分不足时见 availability 标注（sw_daily 需 5000 分，2000 分档走 akshare 回退）。")
    return "\n".join(lines)


# --- _section_value_chain_position ---
def _section_value_chain_position(
    chain: dict, industry_pricing: dict, company_gross_margin: float | None = None,
) -> str:
    """A-6: 价值链位置 + 利润池分布 ASCII 图（v0.1.8 挂载于模块 3 市场结构末尾）。

    ``chain`` 为 ``collection["chain_context"]``（见 lib.chain.collect_chain_context /
    ``_CHAIN_MAP``），字段: industry / chain_position / upstream: list[str] / downstream: list[str]。
    ``industry_pricing`` 为 ``collection["industry_pricing"]`` legacy dict，用于补充上下游
    议价力线索（期货映射覆盖 / 涨价信号，详见 3b/3c 小节，此处不重复渲染完整表格）。
    ``company_gross_margin`` 为可选参数（非原始设计签名的一部分）：调用方可从 financials
    维度取本公司最新毛利率传入；行业上下游毛利率数据源不可得，明确标注 ⚠️，不编造行业均值。
    """
    if not isinstance(chain, dict) or not chain:
        return ""
    industry = chain.get("industry") or ""
    position = chain.get("chain_position")
    upstream = [s for s in (chain.get("upstream") or []) if s]
    downstream = [s for s in (chain.get("downstream") or []) if s]

    if not industry and not position and not upstream and not downstream:
        return ""

    lines = ["### 3f. 价值链位置 + 利润池分布", ""]

    if not position and not upstream and not downstream:
        lines.append(
            f"数据不足：行业「{industry}」在 `lib.chain._CHAIN_MAP` 中暂无产业链映射，"
            "无法渲染价值链图（仅覆盖新能源汽车/电气/汽车/医药/白酒/银行/房地产/半导体/"
            "新能源/化工/钢铁/食品/计算机/通信/电子等行业关键词）。"
        )
        return "\n".join(lines)

    up_label = " / ".join(upstream) if upstream else "⚠️ 未映射"
    down_label = " / ".join(downstream) if downstream else "⚠️ 未映射"
    company_label = f"本公司（{industry or '行业未知'}"
    if position:
        company_label += f" · {position}"
    company_label += "）"

    lines.append("```")
    lines.append(f"[上游: {up_label}]   →   [{company_label}]   →   [下游: {down_label}]")
    lines.append("```")
    lines.append("")
    lines.append(f"[来源: lib.chain.collect_chain_context / 行业分类={industry or '未知'}]")
    lines.append("")

    lines.append("**各环节毛利率对比（利润池分布代理指标）**")
    lines.append("")
    lines.append("| 环节 | 毛利率 | 说明 |")
    lines.append("|------|--------|------|")
    lines.append(f"| 上游（{up_label}） | ⚠️ 不可得 | 无上游行业毛利率数据源，不编造行业均值 |")
    if company_gross_margin is not None:
        lines.append(
            f"| 本公司 | {company_gross_margin:.2f}% | [来源: financials 维度 grossprofit_margin] |"
        )
    else:
        lines.append("| 本公司 | 数据不足 | financials 维度毛利率字段不可得 |")
    lines.append(f"| 下游（{down_label}） | ⚠️ 不可得 | 无下游行业毛利率数据源，不编造行业均值 |")
    lines.append("")
    lines.append(
        "> ⚠️ 利润池分布（各环节增加值/价值占比）需产业链数据库或行业研究报告补充，"
        "当前引擎仅能提供毛利率对比框架，不构成完整利润池分布结论。"
    )
    lines.append("")

    inner, _all_srcs = _industry_pricing_parts(industry_pricing)
    if inner:
        has_futures = inner.get("has_futures", False)
        lines.append("**议价力线索**")
        lines.append("")
        if has_futures:
            lines.append(
                f"- 该行业存在期货现货映射覆盖（详见「原材料成本速览」小节），"
                "上游原材料价格趋势可作为上游议价力变化的间接观察窗口——"
                "原材料价格上涨且本公司毛利率同步收窄，暗示成本传导能力偏弱；"
                "反之则暗示顺价能力较强，需结合实际毛利率变化验证。"
            )
        else:
            lines.append("- ⚠️ 该行业暂无期货映射，缺少上游价格趋势的量化观察窗口。")
        news_inner = None
        for src in _all_srcs:
            if src.get("source") == "akshare.stock_news_em" and src.get("data"):
                news_inner = src.get("data") or {}
                break
        if news_inner and news_inner.get("signal") and news_inner.get("signal") != "无":
            lines.append(
                f"- 公司新闻涨价信号：**{news_inner.get('signal')}**"
                f"（{news_inner.get('signal_detail', '')}，详见「涨价信号」小节），"
                "可作为下游顺价能力的定性佐证，需 WebSearch 深搜确认幅度和持续性。"
            )
        lines.append("")
    else:
        lines.append("**议价力线索**：⚠️ industry_pricing 维度数据不可得，无法补充议价力观察窗口。")
        lines.append("")

    return "\n".join(lines)


# --- _check_fast_veto ---
def _check_fast_veto(dims: dict, collection: dict) -> dict[str, list[str]]:
    """F-3: 快速否决自动化子集，返回硬触发/软触发及展示文本。

    规则分层：
      - hard_triggers: 触发后 DCF 段跳过
      - soft_triggers: 仅展示预警，不跳过 DCF

    当前自动化覆盖：
      1. FCF/OCF 累计为负（硬触发，FCFF 缺失时退化为 OCF 代理）
      2. 连续 3 期经营性现金流为负（软触发）
      3. 资产负债率 >90% 且未见改善（硬触发）
      4. 近 3 期 ROE 连续 <5%（软触发）
      5. 商誉/净资产 >50%（硬触发；字段可得时才检查）

    合规：仅陈述量化事实，不使用动作词。
    """
    result = {
        "hard_triggers": [],
        "soft_triggers": [],
        "display_lines": [],
    }

    fin_dim = (dims or {}).get("financials") or (collection or {}).get("financials") or {}
    fin_list = fin_dim.get("data") if isinstance(fin_dim, dict) else None
    if not isinstance(fin_list, list) or not fin_list:
        return result

    rows = sort_kline_asc(fin_list)

    def _append(level: str, line: str) -> None:
        result[level].append(line)
        tag = "硬触发" if level == "hard_triggers" else "软触发"
        result["display_lines"].append(f"- {tag}: {line}")

    # 1. FCF 累计为负（优先 fcff，字段不可得时退化为经营现金流）
    fcff_vals = [v for v in (_fin_field_num(r, "fcff") for r in rows) if v is not None]
    if len(fcff_vals) >= 3:
        total = sum(fcff_vals)
        if total < 0:
            _append(
                "hard_triggers",
                f"⚠️ 近 {len(fcff_vals)} 期可得 FCFF 累计为负（合计 {total:.2f}）"
                "[来源: financials.fcff]"
            )
    else:
        ocf_vals = [
            v for v in (_fin_field_num(r, "n_cashflow_act", "ocf") for r in rows) if v is not None
        ]
        if len(ocf_vals) >= 3 and sum(ocf_vals) < 0:
            _append(
                "hard_triggers",
                f"⚠️ FCFF 字段不可得，退化以经营现金流近 {len(ocf_vals)} 期累计为负"
                f"（合计 {sum(ocf_vals):.2f}）代理观察[来源: financials.n_cashflow_act]"
            )

    # 2. 连续 3 期经营性现金流为负
    ocf_series = [v for v in (_fin_field_num(r, "n_cashflow_act", "ocf") for r in rows) if v is not None]
    if len(ocf_series) >= 3:
        last3 = ocf_series[-3:]
        if all(v < 0 for v in last3):
            _append(
                "soft_triggers",
                f"⚠️ 连续 3 期经营性现金流为负（{', '.join(f'{v:.2f}' for v in last3)}）"
                "[来源: financials.n_cashflow_act]"
            )

    # 3. 资产负债率 >90% 且未见改善
    debt_ratios = []
    for row in rows:
        total_liab = _fin_field_num(row, "total_liab")
        total_assets = _fin_field_num(row, "total_assets")
        if total_liab is not None and total_assets:
            debt_ratios.append(total_liab / total_assets * 100)
    latest = rows[-1]
    total_liab = _fin_field_num(latest, "total_liab")
    total_assets = _fin_field_num(latest, "total_assets")
    if total_liab is not None and total_assets:
        ratio = total_liab / total_assets * 100
        prev_ratio = debt_ratios[-2] if len(debt_ratios) >= 2 else None
        if ratio > 90 and (prev_ratio is None or ratio >= prev_ratio):
            detail = f"⚠️ 最新报告期资产负债率 {ratio:.1f}%（>90%）"
            if prev_ratio is not None:
                detail += f"，前一期 {prev_ratio:.1f}%"
            detail += "[来源: financials.total_liab/total_assets]"
            _append(
                "hard_triggers",
                detail,
            )

    # 4. 近 3 期 ROE 连续 <5%
    roe_series = [v for v in (_fin_field_num(r, "roe") for r in rows) if v is not None]
    if len(roe_series) >= 3:
        last3 = roe_series[-3:]
        if all(v < 5 for v in last3):
            _append(
                "soft_triggers",
                f"⚠️ 近 3 期 ROE 连续低于 5%（{', '.join(f'{v:.2f}%' for v in last3)}）"
                "[来源: financials.roe]"
            )

    # 5. 商誉/净资产 >50%（字段可得时检查）
    bs_dim = (dims or {}).get("balancesheet") or (collection or {}).get("balancesheet") or {}
    bs_list = bs_dim.get("data") if isinstance(bs_dim, dict) else None
    if isinstance(bs_list, list) and bs_list:
        bs_rows = sort_kline_asc(bs_list)
        bs_latest = bs_rows[-1]
        goodwill = _fin_field_num(bs_latest, "goodwill", "good_will")
        total_equity = _fin_field_num(
            bs_latest, "total_equity", "total_hldr_eqy_inc_min_int", "total_hldr_eqy_exc_min_int",
        )
        if goodwill is not None and total_equity and total_equity > 0:
            ratio = goodwill / total_equity * 100
            if ratio > 50:
                _append(
                    "hard_triggers",
                    f"⚠️ 最新报告期商誉/净资产为 {ratio:.1f}%（>50%）"
                    "[来源: balancesheet.goodwill/total_equity]"
                )

    return result


# --- _six_gate_row ---
def _six_gate_row(name: str, score: float | None, label: str, note: str) -> str:
    score_s = f"{score:.0f}/100" if score is not None else "—"
    return f"| {name} | {label}（{score_s}） | {note} |"


# --- _section_six_gates_scorecard ---
def _section_six_gates_scorecard(
    dims: dict, collection: dict, val_cache: dict,
) -> str:
    """F-4: 六关评分速览（生意/护城河/管理层/财务/估值/风险）。

    来源: 借鉴报告 §6.1 investment-checklist、§8.5。

    合规红线（CLAUDE.md 六关评分规则，最容易违规的一条）：**无通过/不通过二元判决，
    无仓位动作映射**——每关仅用分数或描述性档位（较强/中等/较弱等）呈现，末尾必须附加
    合规声明。
    """
    from lib.scoring import customer_lockin_score, management_ability_proxy, revenue_quality_score

    fin_dim = dims.get("financials") or {}
    fin_list = fin_dim.get("data") if isinstance(fin_dim.get("data"), list) else []
    holder_changes = dims.get("holder_changes") or {}
    market_structure = collection.get("market_structure") or {}

    def grade(score: float | None) -> str:
        if score is None:
            return "数据不足"
        if score >= 70:
            return "较强"
        if score >= 40:
            return "中等"
        return "较弱"

    def avg(*vals: float | None) -> float | None:
        v = [x for x in vals if x is not None]
        return round(sum(v) / len(v), 1) if v else None

    # 生意：复用 A-4 商业模式画布中规模效应/增长驱动/周期性评分均值
    scale_score, _n1, _s1 = _canvas_scale_effect(fin_list)
    growth_score, _n2, _s2 = _canvas_growth_driver(fin_list)
    cyc_score, _n3, _s3 = _canvas_cyclicality(fin_list)
    business_score = avg(scale_score, growth_score, cyc_score)

    # 护城河：复用 A-4 客户锁定/收入模式评分
    rq = revenue_quality_score(fin_list)
    lockin = customer_lockin_score(fin_list)
    moat_score = avg(rq.get("score"), lockin.get("score"))

    # 管理层：复用 management_ability_proxy()，附带其"置信度中等"说明
    mgmt = management_ability_proxy(fin_list, holder_changes)
    mgmt_score = mgmt.get("score")
    mgmt_note = mgmt.get("note", "")

    # 财务：近期毛利率稳定性 + OCF/净利润覆盖评分综合（复用 revenue_quality_score 子信号）
    ocf_detail = (rq.get("detail") or {}).get("ocf_coverage") or {}
    margin_detail = (rq.get("detail") or {}).get("margin_stability") or {}
    fin_score = avg(ocf_detail.get("score"), margin_detail.get("score"))

    from lib.risk_scanner import ocf_np_divergence_flag, revenue_acceleration_flag

    accel = revenue_acceleration_flag(fin_list)
    ocf_div = ocf_np_divergence_flag(fin_list)
    soft_notes: list[str] = []
    for label, flag in (("营收加速度", accel), ("OCF/净利背离", ocf_div)):
        # Only render computed results; skip degrade-path details without accel_pp/ratio
        if "accel_pp" not in flag and "ratio" not in flag:
            continue
        detail = flag.get("detail", "")
        if not detail:
            continue
        prefix = "软信号⚠️ " if flag.get("triggered") else "软信号 "
        soft_notes.append(f"{prefix}{label}: {detail}")
    fin_note = (
        "毛利率稳定性 + OCF/净利润覆盖评分均值 "
        "[来源: lib.scoring.revenue_quality_score 子信号]"
    )
    if soft_notes:
        fin_note += "；" + "；".join(soft_notes)

    # 估值：复用 PE/PB 历史位置（呈现位置，非贵贱判断，不与"强弱"混用）
    pe_pct, pb_pct, pe_zone = _v3_valuation_percentiles(dims, val_cache)
    if pe_pct is not None:
        val_desc = f"PE 历史位置 {pe_pct:.1f}%（{pe_zone or '—'}）"
        if pb_pct is not None:
            val_desc += f"，PB 历史位置 {pb_pct:.1f}%"
        val_desc += " [来源: valuation 历史位置]"
        if pe_pct >= 70:
            val_grade = "历史高位"
        elif pe_pct <= 30:
            val_grade = "历史低位"
        else:
            val_grade = "历史中位"
    else:
        val_desc, val_grade = "PE/PB 历史位置不可得", "数据不足"

    # 风险：复用 risk_data 触发数量
    risk_data = _v3_build_risk_report(collection, dims, market_structure, val_cache=val_cache)
    triggered = risk_data.get("triggered_count", 0) or 0
    coverage = risk_data.get("coverage") or {}
    total_signals = sum(v for v in coverage.values() if isinstance(v, (int, float))) if isinstance(coverage, dict) else 0
    if total_signals:
        risk_desc = f"触发 {triggered}/{total_signals} 项定量风险信号 [来源: lib.risk_scanner.risk_report]"
    else:
        risk_desc = f"触发 {triggered} 项定量风险信号 [来源: lib.risk_scanner.risk_report]"
    if triggered == 0:
        risk_grade = "较少触发"
    elif triggered <= 2:
        risk_grade = "中等触发"
    else:
        risk_grade = "较多触发"

    lines = ["### F-4 六关评分速览", ""]
    lines.append(
        "> 巴菲特六关框架（生意/护城河/管理层/财务/估值/风险）的多维度事实与量化评分汇总呈现，"
        "档位为描述性分档，不做二元判定，不含任何仓位或操作动作映射。"
    )
    lines.append("")
    lines.append("| 关口 | 档位 | 依据 |")
    lines.append("|------|:---:|------|")
    lines.append(_six_gate_row(
        "生意", business_score, grade(business_score),
        "规模效应/增长驱动/周期性评分均值 [来源: A-4 商业模式画布规则推断]",
    ))
    lines.append(_six_gate_row(
        "护城河", moat_score, grade(moat_score),
        "收入模式质量 + 客户锁定评分均值 "
        "[来源: lib.scoring.revenue_quality_score / customer_lockin_score]",
    ))
    lines.append(_six_gate_row(
        "管理层", mgmt_score, grade(mgmt_score),
        f"管理层能力代理评分（{mgmt_note}） [来源: lib.scoring.management_ability_proxy]",
    ))
    lines.append(_six_gate_row(
        "财务", fin_score, grade(fin_score),
        fin_note,
    ))
    lines.append(f"| 估值 | {val_grade} | {val_desc} |")
    lines.append(f"| 风险 | {risk_grade} | {risk_desc} |")
    lines.append("")
    lines.append(
        "> 本速览为多维度事实与量化评分的汇总呈现，不构成投资建议，"
        "不代表买卖或持有的行动判断。"
    )
    lines.append("")
    return "\n".join(lines)


# --- _section_events_timeline ---
def _section_events_timeline(collection: dict) -> str:
    """事件时间线（模块 3-3b 过渡段）。

    渲染 events 列表为时间降序表格，并附加 Template B 事件分类摘要。
    """
    events_all = collection.get("events") or []
    if not events_all or not isinstance(events_all, list) or len(events_all) == 0:
        return ""

    lines = ["## 3a. 事件时间线", ""]

    # 按 date 降序排列
    sorted_events = sorted(
        events_all,
        key=lambda e: str(e.get("date", "")),
        reverse=True,
    )
    shown = sorted_events[:15]

    lines.append("| 日期 | 类型 | 公告标题 | 影响维度 | 持续性质 |")
    lines.append("|------|------|---------|---------|---------|")
    for ev in shown:
        date = str(ev.get("date", ""))
        etype = str(ev.get("type", "other"))
        title = str(ev.get("title", ""))
        impact = str(ev.get("impact_dimension", ""))
        duration = str(ev.get("duration", ""))
        # Trim long titles for table display
        if len(title) > 50:
            title = title[:47] + "..."
        # Escape pipe chars
        title = title.replace("|", "/")
        lines.append(f"| {date} | {etype} | {title} | {impact} | {duration} |")

    hide_count = max(0, len(sorted_events) - 15)
    if hide_count > 0:
        lines.append(f"| ... | ... | （另有 {hide_count} 条事件未展示） | ... | ... |")

    lines.append("")
    lines.append(f"[来源: akshare stock_individual_notice_report / {len(sorted_events)} 条事件]")
    lines.append("")

    # ---- Template B classification cards ----
    cards = _get_analysis_cards(collection)
    event_classifications = cards.get("event_classifications") or []
    if event_classifications and isinstance(event_classifications, list):
        lines.append("**事件分类摘要**（规则推断，待 Claude 验证）:")
        lines.append("")
        for ec in event_classifications:
            ev_type = ec.get("event_label", ec.get("event_type", "其他"))
            ev_count = len(ec.get("events", []))
            direction_hint = ec.get("direction_hint", "")
            duration = ec.get("default_duration_hint", "")
            direction_note = ec.get("direction_note", "")
            summary_parts = [f"**{ev_type}** ({ev_count}条)"]
            if direction_hint:
                summary_parts.append(f"方向: {direction_hint}")
                if direction_note:
                    summary_parts.append(f"{direction_note}")
            else:
                summary_parts.append("[参考: 事件类型分类规则，不构成投资建议]")
            lines.append("  - " + " ".join(summary_parts))
        lines.append("")

    # Industry / market event placeholders
    ind_note = collection.get("_meta", {}).get("industry_events_note")
    mkt_note = collection.get("_meta", {}).get("market_events_note")
    if ind_note:
        lines.append(f"⏭️ **行业事件**: {ind_note}")
    if mkt_note:
        lines.append(f"⏭️ **市场事件**: {mkt_note}")
    if ind_note or mkt_note:
        lines.append("")

    return "\n".join(lines)


# --- _fmt_peer_metric ---
def _fmt_peer_metric(v: Any, *, signed: bool = False) -> str:
    if v is None:
        return "-"
    return f"{float(v):+.2f}" if signed else f"{float(v):.2f}"


# --- _competitive_position_label ---
def _competitive_position_label(pct: float | None) -> str | None:
    """营收增速同行分位 → 龙头/挑战者/追赶者（分位越高增速越快）。"""
    if pct is None:
        return None
    if pct >= 75:
        return "龙头"
    if pct >= 40:
        return "挑战者"
    return "追赶者"


# --- _section_holder_changes ---
def _section_holder_changes(data: dict, events: list | None = None) -> str:
    """股东增减持动向（P0-2 holder_changes 渲染 + v0.1.8 A-1 信号聚合/言行对照）。"""
    if not data or not isinstance(data, dict):
        return ""
    records = data.get("data") or []
    if not records or not isinstance(records, list) or len(records) == 0:
        return ""

    lines = ["## 3d. 股东增减持动向", ""]

    # 近期重要变动表格
    lines.append("### 近期重要变动（近 2 年）")
    lines.append("")
    lines.append("| 公告日期 | 股东名称 | 方向 | 变动数量(万) | 变动比例(%) | 均价 | 来源 | 交叉验证 |")
    lines.append("|----------|---------|------|-------------|------------|------|------|---------|")
    for r in records[:20]:
        date = str(r.get("ann_date", ""))
        if len(date) == 8:
            date = f"{date[:4]}-{date[4:6]}-{date[6:8]}"
        name = str(r.get("holder_name", ""))[:16]
        direction = str(r.get("direction", ""))
        vol = r.get("change_vol")
        vol_str = ""
        if vol is not None:
            v = _safe_num(vol)
            if v is not None:
                if abs(v) >= 10000:
                    vol_str = f"{v / 10000:.0f}万"
                else:
                    vol_str = f"{v:.0f}"
            else:
                vol_str = str(r.get("change_vol_raw") or vol)[:12]
        ratio = _safe_num(r.get("change_ratio"))
        ratio_str = f"{ratio:.2f}" if ratio is not None else ""
        price = _safe_num(r.get("avg_price"))
        price_str = f"{price:.2f}" if price is not None else "—"
        source = str(r.get("source", ""))
        cc = r.get("cross_check", 1)
        cc_str = f"{cc}源一致" if cc >= 2 else ""
        lines.append(
            f"| {date} | {name} | {direction} | {vol_str} | {ratio_str} | "
            f"{price_str} | {source} | {cc_str} |"
        )
    lines.append("")

    # 信号分析
    lines.append("### 信号分析")
    lines.append("")

    # 净增/减持方向
    buy_count = sum(1 for r in records if "增" in str(r.get("direction", "")))
    sell_count = sum(1 for r in records if "减" in str(r.get("direction", "")))
    lines.append(f"- **净增/减持方向**: 近 2 年 增持 {buy_count} 笔，减持 {sell_count} 笔")

    # 关键主体（按出现次数排序）
    from collections import Counter
    name_counter = Counter(
        str(r.get("holder_name", ""))[:12] for r in records
    )
    top_names = name_counter.most_common(3)
    if top_names:
        lines.append(f"- **关键主体**: {', '.join(f'{n}({c}次)' for n, c in top_names)}")

    # 内部人一致性
    if buy_count >= 3 and sell_count == 0:
        lines.append("- **内部人一致性**: 多主体同向增持 → 信号增强")
    elif sell_count >= 3 and buy_count == 0:
        lines.append("- **内部人一致性**: 多主体同向减持 → 信号增强（负面）")
    else:
        lines.append("- **内部人一致性**: 增减持方向分歧，信号混杂")

    # ---- v0.1.8 A-1: 信号聚合 ----
    from lib.scoring import insider_signal

    signal = insider_signal(data)
    _SIGNAL_HINTS = {
        "强正向": "近 12 月内 ≥3 名股东增持，0 笔减持，且交叉验证 ≥2 源",
        "正向": "近 12 月内增持笔数明显多于减持笔数",
        "分歧": "近 12 月内增减持方向不明确或数据不足以判断趋势",
        "负向": "近 12 月内减持笔数明显多于增持笔数",
        "强负向": "近 12 月内 ≥3 名股东减持，0 笔增持，且交叉验证 ≥2 源",
        "数据不足": "公告日期或增减持记录不足，无法生成聚合信号",
    }
    lines.append("")
    lines.append("### 信号聚合")
    lines.append("")
    lines.append(
        f"- **内部人买卖一致性信号**: **{signal}**（{_SIGNAL_HINTS.get(signal, '')}）"
        f" [来源: lib.scoring.insider_signal / holder_changes]"
    )
    lines.append("⚠️ 以上仅为行为事实的量化聚合，不构成任何投资建议或买卖指令。")

    # ---- v0.1.8 A-1: 言行对照 ----
    lines.append("")
    lines.append("### 言行对照")
    lines.append("")
    sell_records = [r for r in records if "减" in str(r.get("direction", ""))]
    commitment_events = []
    if isinstance(events, list):
        for e in events:
            if not isinstance(e, dict):
                continue
            title = str(e.get("title", ""))
            if any(kw in title for kw in _COMMITMENT_KEYWORDS):
                commitment_events.append(e)
    if not commitment_events:
        lines.append("未检索到相关承诺公告，言行对照暂缺。")
    elif not sell_records:
        lines.append(
            f"检索到 {len(commitment_events)} 条承诺/不减持相关公告，同期 holder_changes "
            "记录中无匹配的减持行为。"
        )
        for e in commitment_events[:5]:
            edate = str(e.get("date", ""))
            etitle = str(e.get("title", ""))[:60]
            lines.append(f"- {edate} {etitle} [来源: events]")
    else:
        lines.append(
            f"检索到 {len(commitment_events)} 条承诺/不减持相关公告，"
            f"同期 holder_changes 记录中存在 {len(sell_records)} 笔减持，时间线对照如下（仅陈述行为事实，"
            "不判断是否违反承诺，具体条款需人工核实公告原文）："
        )
        lines.append("")
        lines.append("| 日期 | 类型 | 内容 |")
        lines.append("|------|------|------|")
        timeline: list[tuple[str, str, str]] = []
        for e in commitment_events:
            edate = str(e.get("date", ""))
            etitle = str(e.get("title", ""))[:60]
            timeline.append((edate, "承诺公告", etitle))
        for r in sell_records:
            rdate = str(r.get("ann_date", ""))
            if len(rdate) == 8:
                rdate = f"{rdate[:4]}-{rdate[4:6]}-{rdate[6:8]}"
            rname = str(r.get("holder_name", ""))[:16]
            timeline.append((rdate, "减持记录", rname))
        for edate, etype, content in sorted(timeline, key=lambda t: t[0])[:15]:
            lines.append(f"| {edate} | {etype} | {content} |")
        lines.append("")
        lines.append("🔍 **待独立验证:** 承诺公告的具体条款（承诺期限/主体范围）需与公告原文核对。")

    return "\n".join(lines)


# --- _industry_pricing_parts ---
def _industry_pricing_parts(data: dict) -> tuple[dict, list]:
    """解析 industry_pricing legacy dict → (inner, all_sources)。"""
    if not data or not isinstance(data, dict):
        return {}, []
    inner = data.get("data") or {}
    if not isinstance(inner, dict):
        return {}, []
    all_srcs = data.get("_meta", {}).get("all_sources", [])
    return inner, all_srcs


# --- _render_pricing_futures_section ---
def _render_pricing_futures_section(data: dict) -> str:
    """模块 1：原材料成本速览（期货现货）。"""
    inner, all_srcs = _industry_pricing_parts(data)
    if not inner:
        return ""

    has_futures = inner.get("has_futures", False)
    lines: list[str] = []

    if has_futures:
        lines.append("### 原材料成本速览")
        lines.append("")
        futures_data: dict = {}
        for src in all_srcs:
            if src.get("source") == "akshare.futures_spot_price" and src.get("data"):
                futures_data = src.get("data") or {}
                break
        if not futures_data:
            for k, v in inner.items():
                if isinstance(v, dict) and "code" in v:
                    futures_data[k] = v

        if futures_data:
            lines.append("| 品种 | 代码 | 现货价 | 主力合约 | 主力基差率 | 近30日趋势 |")
            lines.append("|------|------|--------|---------|-----------|-----------|")
            for name, info in futures_data.items():
                if not isinstance(info, dict):
                    continue
                code = info.get("code", "")
                spot_n = _safe_num(info.get("spot_price"))
                spot_s = f"{spot_n:,.0f}" if spot_n is not None else "—"
                dom_n = _safe_num(info.get("dom_price"))
                dom_s = f"{dom_n:,.0f}" if dom_n is not None else "—"
                basis_n = _safe_num(info.get("dom_basis_rate"))
                basis_s = f"{basis_n:.2f}%" if basis_n is not None else "—"
                trend = info.get("trend_30d", "—")
                lines.append(f"| {name} | {code} | {spot_s} | {dom_s} | {basis_s} | {trend} |")
            lines.append("")

    industry = inner.get("industry", "")
    note = f"> 行业: {industry} | 数据来源: akshare 期货现货" if industry else ""
    if not has_futures:
        note = (note + " | ⚠️ 该行业无期货映射，仅靠新闻源") if note else \
            "> ⚠️ 该行业无期货映射，仅靠新闻源"
    if note:
        lines.append(note)

    return "\n".join(lines) if lines else ""


# --- _render_pricing_news_section ---
def _render_pricing_news_section(data: dict) -> str:
    """模块 2：涨价信号（公司新闻）。"""
    inner, all_srcs = _industry_pricing_parts(data)
    if not inner:
        return ""

    news_data: dict = {}
    for src in all_srcs:
        if src.get("source") == "akshare.stock_news_em" and src.get("data"):
            news_data = src.get("data") or {}
            break

    if not news_data:
        return ""

    signal = news_data.get("signal", "无")
    detail = news_data.get("signal_detail", "")
    lines = [
        "### 涨价信号",
        "",
        f"**状态: {'涨价趋势确认' if signal == '确认' else '单条涨价新闻' if signal == '单条' else '无涨价信号'}**（{detail}）",
        "",
    ]

    matches = news_data.get("matches") or []
    if matches:
        lines.append("| 日期 | 标题 |")
        lines.append("|------|------|")
        for m in matches[:10]:
            date = str(m.get("date", ""))[:10]
            title = str(m.get("title", ""))[:50]
            lines.append(f"| {date} | {title} |")
        lines.append("")
        lines.append("> 🔍 待验证: WebSearch 深搜确认涨价幅度和持续性")

    return "\n".join(lines)


# --- _v3_trigger_c_active ---
def _v3_trigger_c_active(market_structure: dict) -> bool:
    sw = market_structure.get("sw_index") or {}
    rel = sw.get("relative_vs_benchmark_pct")
    return rel is not None and abs(rel) >= 5


# --- _conclude_profit_structure ---
def _conclude_profit_structure(
    roe: float | None, gm: float | None, debt_ratio: float | None = None,
) -> str:
    """盈利结构结论：一句话判断。"""
    parts = []
    if roe is not None:
        if roe >= 15:
            parts.append("盈利能力较强，ROE 处于较优区间")
        elif roe >= 10:
            parts.append("盈利能力中等，ROE 处于中等水平")
        elif roe >= 6:
            parts.append("盈利能力一般，ROE 偏低")
        else:
            parts.append("盈利能力薄弱，ROE 显著偏低")
    if gm is not None:
        if gm >= 40:
            parts.append("毛利率较高，产品或服务具有较强定价权")
        elif gm >= 20:
            parts.append("毛利率处于中等水平，定价权一般")
        else:
            parts.append("毛利率偏低，产品或服务差异化不足")
    if roe is None and gm is None:
        return "盈利结构数据不足，无法形成有效判断"
    if debt_ratio is not None and debt_ratio > 70 and roe is not None and roe > 15:
        parts.append("需注意高杠杆对 ROE 的虚增效应")
    return "；".join(parts)


# --- _conclude_cash_flow_quality ---
def _conclude_cash_flow_quality(
    cf_ratio: float | None, ar_growth: float | None,
    rev_growth: float | None, ocf: float | None,
    *,
    np_v: float | None = None,
) -> str:
    """现金流质量结论：一句话判断。"""
    if np_v is not None and np_v <= 0:
        if ocf is not None and ocf > 0:
            return (
                "利润为负但经营现金流为正，覆盖比不适用；"
                "需结合亏损原因判断现金流质量"
            )
        if ocf is not None:
            return "利润为负，经营现金流/净利润覆盖比不适用"
        return "利润为负，现金流质量数据不足"
    if cf_ratio is not None:
        if cf_ratio < 0:
            return "经营现金流/净利润覆盖比为负，比值不适用（利润与现金流方向不一致）"
        base = ""
        if cf_ratio >= 1.0:
            base = "现金流质量优秀，经营现金流充分覆盖净利润"
        elif cf_ratio >= 0.8:
            base = "现金流质量良好，经营现金流基本覆盖净利润"
        elif cf_ratio >= 0.5:
            base = "现金流质量偏弱，经营现金流与净利润存在一定背离"
        else:
            base = "现金流质量较差，经营现金流与净利润严重背离"
        if ar_growth is not None and rev_growth is not None:
            if ar_growth > rev_growth * 1.5:
                return base + "；应收增速远超营收，回款质量存疑"
            if ar_growth > rev_growth:
                return base + "；应收增速略高于营收，需关注回款节奏"
        return base
    if ocf is not None:
        return "经营现金流数据可用，但缺少净利润项，无法计算覆盖比"
    return "现金流质量数据不足，无法形成有效判断"


# --- _conclude_asset_liability ---
def _conclude_asset_liability(
    debt_ratio: float | None, em: float | None,
    ar_cur: float | None, inv_cur: float | None,
    rev_cur: float | None,
) -> str:
    """资产负债与扩产路径结论：一句话判断。"""
    parts = []
    if debt_ratio is not None:
        if debt_ratio >= 70:
            parts.append("资产负债率较高（>70%），财务杠杆偏大")
        elif debt_ratio >= 50:
            parts.append("资产负债率适中（50%-70%），杠杆水平合理")
        else:
            parts.append("资产负债率较低（<50%），财务结构稳健")
    if em is not None:
        if em > 3:
            parts.append("权益乘数偏高，扩产依赖外部融资")
        elif em < 1.5:
            parts.append("权益乘数偏低，扩产空间充足")
    if ar_cur is not None and inv_cur is not None and rev_cur is not None and rev_cur > 0:
        working_ratio = (ar_cur + inv_cur) / rev_cur
        if working_ratio > 0.5:
            parts.append("运营资金占用较大，应收+存货占营收比例偏高")
        else:
            parts.append("运营资金管理效率良好")
    if not parts:
        return "资产负债数据不足，无法形成有效判断"
    return "；".join(parts)


# --- _evidence_strength_label ---
def _evidence_strength_label(data_available: list[bool]) -> str:
    """根据可用数据项占比判断证据强度。"""
    from .render_icons import (
        ICON_EVIDENCE_INSUFFICIENT,
        ICON_EVIDENCE_MEDIUM,
        ICON_EVIDENCE_STRONG,
        ICON_EVIDENCE_WEAK,
    )
    if not data_available:
        return ICON_EVIDENCE_INSUFFICIENT
    if not any(data_available):
        return ICON_EVIDENCE_WEAK
    ratio = sum(data_available) / len(data_available)
    if ratio >= 0.8:
        return ICON_EVIDENCE_STRONG
    if ratio >= 0.5:
        return ICON_EVIDENCE_MEDIUM
    return ICON_EVIDENCE_WEAK


# --- _financial_panorama_table ---
def _financial_panorama_table(fin_list: list[dict]) -> list[str]:
    """模块4 业绩全景表（P1b：含 EPS 列）。"""
    if not fin_list:
        return []
    rows = sort_kline_asc(fin_list)[-8:]
    lines = [
        "### 业绩全景（近8期）",
        "",
        "| 报告期 | ROE(%) | EPS | 营收 | 净利润 | 毛利率 | 净利率 |",
        "|--------|--------|-----|------|--------|--------|--------|",
    ]
    for r in rows:
        roe = r.get("roe", "-")
        eps = r.get("eps") if r.get("eps") is not None else r.get("basic_eps", "-")
        rev = _fmt_v2(r.get("revenue"))
        np_ = _fmt_v2(r.get("net_profit"))
        gm = r.get("grossprofit_margin") if r.get("grossprofit_margin") is not None else r.get("gross_margin")
        gm_s = f"{gm:.2f}" if isinstance(gm, (int, float)) else "-"
        npm = r.get("netprofit_margin") or r.get("np_margin")
        if npm is None:
            rev_n = _safe_num(r.get("revenue"))
            np_n = _safe_num(r.get("net_profit"))
            npm = (np_n / rev_n * 100) if rev_n and np_n and rev_n > 0 else None
        npm_s = f"{npm:.2f}" if isinstance(npm, (int, float)) else "-"
        lines.append(
            f"| {r.get('end_date', '?')} | {roe} | {eps} | {rev} | {np_} | {gm_s} | {npm_s} |"
        )
    lines.append("")
    lines.append("> EPS 来源: financials 维度 / fina_indicator.basic_eps 或 eps 字段")
    lines.append("")
    return lines


# --- _stars ---
def _stars(n: int) -> str:
    n = max(1, min(5, int(round(n))))
    return "★" * n


# --- _score_to_stars ---
def _score_to_stars(score: float | None) -> int | None:
    """0-100 分制评分 → 1-5 星（数据不足时返回 None，不得裸给星级）。"""
    if score is None:
        return None
    if score >= 80:
        return 5
    if score >= 60:
        return 4
    if score >= 40:
        return 3
    if score >= 20:
        return 2
    return 1


# --- _canvas_scale_effect ---
def _canvas_scale_effect(fin_list: list[dict]) -> tuple[float | None, str, list[str]]:
    """规模效应：近 3-5 期营收增速 vs 毛利率变化关系推断。"""
    if not fin_list:
        return None, "数据不足：缺少财务数据，无法判断规模效应", []
    rows = sort_kline_asc(fin_list)[-5:]
    pairs = [
        (_fin_field_num(r, "revenue"), _fin_field_num(r, "grossprofit_margin", "gross_margin"))
        for r in rows
    ]
    valid = [(rev, gm) for rev, gm in pairs if rev is not None and gm is not None]
    if len(valid) < 3:
        return None, "数据不足：营收/毛利率至少需 3 期同时可得数据才能判断规模效应", []
    rev0, gm0 = valid[0]
    rev1, gm1 = valid[-1]
    if not rev0:
        return None, "数据不足：起始期营收为 0，无法计算增速", []
    rev_growth = (rev1 - rev0) / abs(rev0) * 100
    margin_change = gm1 - gm0
    if rev_growth > 0 and margin_change >= -1:
        score = 80.0
        note = (
            f"近 {len(valid)} 期营收增长 {rev_growth:+.1f}%，同期毛利率变化 {margin_change:+.2f}pp"
            "（未随规模扩大而下降），呈现规模效应特征"
        )
    elif rev_growth > 0:
        score = 40.0
        note = (
            f"近 {len(valid)} 期营收增长 {rev_growth:+.1f}%，但毛利率下降 {margin_change:+.2f}pp，"
            "规模效应证据较弱（可能被价格竞争或成本上升抵消）"
        )
    else:
        score = 20.0
        note = f"近 {len(valid)} 期营收未见增长（{rev_growth:+.1f}%），规模效应无法验证"
    return score, note, ["revenue", "grossprofit_margin"]


# --- _canvas_cyclicality ---
def _canvas_cyclicality(fin_list: list[dict]) -> tuple[float | None, str, list[str]]:
    """周期性：历史 ROE 波动率推断（波动越大周期性越强，星级越低）。"""
    if not fin_list:
        return None, "数据不足：缺少财务数据，无法判断周期性", []
    import statistics
    rows = sort_kline_asc(fin_list)[-8:]
    roes = [v for v in (_fin_field_num(r, "roe") for r in rows) if v is not None]
    if len(roes) < 3:
        return None, "数据不足：ROE 至少需 3 期数据评估波动性", []
    mean_roe = statistics.mean(roes)
    if abs(mean_roe) <= 1e-9:
        return None, "数据不足：ROE 均值接近 0，变异系数不适用", []
    cv = statistics.pstdev(roes) / abs(mean_roe)
    if cv < 0.15:
        score, note = 90.0, f"近 {len(roes)} 期 ROE 变异系数 {cv:.2f}（<0.15），波动小，周期性特征弱"
    elif cv < 0.35:
        score, note = 55.0, f"近 {len(roes)} 期 ROE 变异系数 {cv:.2f}（0.15-0.35），中等波动"
    else:
        score, note = 20.0, f"近 {len(roes)} 期 ROE 变异系数 {cv:.2f}（≥0.35），波动大，周期性特征明显"
    return score, note, ["roe"]


# --- _canvas_growth_driver ---
def _canvas_growth_driver(fin_list: list[dict]) -> tuple[float | None, str, list[str]]:
    """增长驱动：数据不支持精细量/价拆分，用营收增速绝对水平粗略映射。"""
    if not fin_list:
        return None, "数据不足：缺少财务数据，无法判断增长驱动", []
    rows = sort_kline_asc(fin_list)[-5:]
    revs = [_fin_field_num(r, "revenue") for r in rows]
    revs = [v for v in revs if v is not None]
    if len(revs) < 2:
        return None, "数据不足：营收至少需 2 期数据", []
    if not revs[0]:
        return None, "数据不足：起始期营收为 0，无法计算增速", []
    growth = (revs[-1] - revs[0]) / abs(revs[0]) * 100
    note_suffix = "（数据不支持量/价精细拆分，以营收增速绝对水平粗略映射）"
    if growth >= 30:
        score, note = 85.0, f"近 {len(revs)} 期营收累计增长 {growth:+.1f}%，增长动能强{note_suffix}"
    elif growth >= 10:
        score, note = 55.0, f"近 {len(revs)} 期营收累计增长 {growth:+.1f}%，增长动能中等{note_suffix}"
    elif growth >= 0:
        score, note = 30.0, f"近 {len(revs)} 期营收累计增长 {growth:+.1f}%，增长动能偏弱{note_suffix}"
    else:
        score, note = 10.0, f"近 {len(revs)} 期营收累计下滑 {growth:+.1f}%{note_suffix}"
    return score, note, ["revenue"]


# --- _canvas_capital_intensity ---
def _canvas_capital_intensity(fin_list: list[dict]) -> tuple[float | None, str, list[str]]:
    """资本密集度：固定资产/总资产比例（评分越低代表资本密集度越高）。

    v0.1.7/v0.1.8 collector.py 未采集 fix_assets/fixed_assets 字段，本函数在字段
    可得时才计算，当前实际运行路径下恒定标注数据不足（不得凭空编造比例）。
    """
    if not fin_list:
        return None, "数据不足：缺少财务数据，无法判断资本密集度", []
    latest = fin_list[-1]
    fix_assets = _fin_field_num(latest, "fix_assets", "fixed_assets")
    total_assets = _fin_field_num(latest, "total_assets")
    if fix_assets is None or not total_assets:
        return (
            None,
            "数据不足：固定资产字段（fix_assets/fixed_assets）当前引擎未采集，无法计算资本密集度比例",
            [],
        )
    ratio = fix_assets / total_assets * 100
    if ratio >= 50:
        score, note = 15.0, f"固定资产/总资产 = {ratio:.1f}%（≥50%），资本密集度高"
    elif ratio >= 25:
        score, note = 50.0, f"固定资产/总资产 = {ratio:.1f}%（25%-50%），资本密集度中等"
    else:
        score, note = 85.0, f"固定资产/总资产 = {ratio:.1f}%（<25%），资本密集度低"
    return score, note, ["fix_assets", "total_assets"]


# --- _canvas_detail_notes ---
def _canvas_detail_notes(result: dict) -> str:
    """从 scoring.py 返回结构中提取各子信号 note，拼接为一句可读依据文本。"""
    detail = result.get("detail") or {}
    notes = [
        d.get("note") for d in detail.values()
        if isinstance(d, dict) and d.get("note") and d.get("score") is not None
    ]
    base = "；".join(notes) if notes else "数据不足，各子信号均无法计算"
    insufficient = result.get("insufficient_data") or []
    if insufficient:
        base += f"（未计入子信号: {'; '.join(insufficient)}）"
    return base


# --- _canvas_row ---
def _canvas_row(name: str, score: float | None, note: str, sources: list[str] | None = None) -> str:
    if score is None:
        return f"| {name} | 数据不足 | {note} |"
    stars = _score_to_stars(score)
    src_note = f" [来源: {', '.join(sorted(set(sources)))}]" if sources else ""
    return f"| {name} | {_stars(stars)}（{score:.0f}/100） | {note}{src_note} |"


# --- _section_business_model_canvas ---
def _section_business_model_canvas(
    fin_list: list[dict], holder_changes: dict, chain: dict,
) -> str:
    """A-4: 7 维度商业模式画布。

    5/7 维度可计算（收入模式/客户锁定复用 scoring.py 量化引擎，规模效应/周期性/增长驱动
    为本函数基于 fin_list 的规则推断）；技术壁垒（需研发占比/专利数据）与资本密集度
    （固定资产字段当前未采集）2 维度标注数据不足，不得用行业常识编造分数。
    ``holder_changes``/``chain`` 参数保留用于未来扩展上下文（如按行业调整周期性阈值），
    当前版本评分逻辑仅依赖 fin_list。
    """
    from lib.scoring import customer_lockin_score, revenue_quality_score

    lines = ["#### 商业模式画布（A-4，7 维度）", ""]
    industry = (chain or {}).get("industry")
    if industry:
        lines.append(f"> 所属行业: {industry}（来源: lib.chain.collect_chain_context）")
        lines.append("")

    rq = revenue_quality_score(fin_list)
    lockin = customer_lockin_score(fin_list)
    scale_score, scale_note, scale_src = _canvas_scale_effect(fin_list)
    cyc_score, cyc_note, cyc_src = _canvas_cyclicality(fin_list)
    growth_score, growth_note, growth_src = _canvas_growth_driver(fin_list)
    capital_score, capital_note, capital_src = _canvas_capital_intensity(fin_list)

    rows: list[tuple[str, float | None, str, list[str]]] = [
        ("收入模式", rq.get("score"), _canvas_detail_notes(rq), rq.get("sources") or []),
        ("客户锁定", lockin.get("score"), _canvas_detail_notes(lockin), lockin.get("sources") or []),
        ("规模效应", scale_score, scale_note, scale_src),
        (
            "技术壁垒",
            None,
            "数据不足，定性推断，置信度低：需研发投入占比/专利数量数据，当前引擎未采集相关字段",
            [],
        ),
        ("周期性", cyc_score, cyc_note, cyc_src),
        ("增长驱动", growth_score, growth_note, growth_src),
        ("资本密集度", capital_score, capital_note, capital_src),
    ]

    lines.append("| 维度 | 评分 | 依据 |")
    lines.append("|------|:---:|------|")
    for name, score, note, src in rows:
        lines.append(_canvas_row(name, score, note, src))
    lines.append("")

    scored = [(name, score) for name, score, _note, _src in rows if score is not None]
    if len(scored) >= 2:
        hi_name, hi_score = max(scored, key=lambda x: x[1])
        lo_name, lo_score = min(scored, key=lambda x: x[1])
        if hi_name != lo_name:
            lines.append(
                f"> **核心矛盾**：「{hi_name}」评分最高"
                f"（{_stars(_score_to_stars(hi_score))}，{hi_score:.0f}/100），"
                f"「{lo_name}」评分最低（{_stars(_score_to_stars(lo_score))}，{lo_score:.0f}/100）——"
                "两者形成对比，需结合护城河来源（见 B-①）与行业位置（见 4a）综合判断商业模式的一致性。"
            )
        else:
            lines.append("> **核心矛盾**：仅 1 个维度可计算评分，暂无法生成维度间对比。")
    else:
        lines.append(f"> **核心矛盾**：可计算维度不足 2 个（当前 {len(scored)} 个），暂无法生成评分对比。")
    lines.append("")
    return "\n".join(lines)


# --- _mgmt_categorize_event ---
def _mgmt_categorize_event(title: str) -> str | None:
    """按标题关键词分类事件；未命中关键词的记录不纳入时间线，不强行归类。"""
    for keyword, category in _MGMT_EVENT_KEYWORDS:
        if keyword in title:
            return category
    return None


# --- _section_management_assessment ---
def _section_management_assessment(
    events: list | None, holder_changes: dict, fin_list: list[dict],
) -> str:
    """A-5: 管理层完整评估。

    合规: 仅陈述公开记录事实（决策日期/公告内容/行为统计），不推断管理层主观动机，
    不给"信赖/不信赖"二元结论。软维度（组织能力/企业文化/接班人风险）固定标注
    "[Claude report 阶段定性填充]"占位。
    """
    from lib.schema import ManagementTimelineEntry
    from lib.scoring import insider_signal, management_ability_proxy

    lines = ["#### 管理层完整评估（A-5）", ""]

    # ---- 决策时间线 ----
    lines.append("**关键决策时间线**（按标题关键词分类，未命中关键词的公告不纳入）")
    lines.append("")
    timeline: list[ManagementTimelineEntry] = []
    for ev in (events or []):
        if not isinstance(ev, dict):
            continue
        title = str(ev.get("title", "")).strip()
        if not title:
            continue
        category = _mgmt_categorize_event(title)
        if category is None:
            continue
        timeline.append(ManagementTimelineEntry(
            date=str(ev.get("date", "")),
            event=title,
            category=category,  # type: ignore[arg-type]
            source="akshare stock_individual_notice_report",
            rating=None,
        ))
    if timeline:
        timeline.sort(key=lambda e: e.date, reverse=True)
        lines.append("| 日期 | 决策类别 | 事件 | 评级(1-5) |")
        lines.append("|------|---------|------|:---:|")
        for e in timeline[:20]:
            title_s = e.event.replace("|", "/")
            if len(title_s) > 50:
                title_s = title_s[:47] + "..."
            lines.append(
                f"| {e.date} | {_MGMT_CATEGORY_LABELS.get(e.category, e.category)} | {title_s} "
                f"| [待 Claude report 阶段填充] |"
            )
        hide = max(0, len(timeline) - 20)
        if hide:
            lines.append(f"| ... | ... | （另有 {hide} 条决策相关公告未展示） | ... |")
        lines.append("")
        lines.append(
            f"[来源: akshare stock_individual_notice_report / 共 {len(timeline)} 条决策相关公告；"
            "评级由 Claude 在 report 阶段依据公告内容与后续实际影响填充，本引擎不预设评分]"
        )
    else:
        lines.append(
            "数据不足：events 中未检索到可按「回购/并购/收购/增发/定增/IPO/资本开支/扩产」"
            "关键词分类的决策记录。"
        )
    lines.append("")

    # ---- 资本配置能力 ----
    lines.append("**资本配置能力**（5 维度，1 维度可量化）")
    lines.append("")
    mgmt = management_ability_proxy(fin_list, holder_changes)
    capex_detail = (mgmt.get("detail") or {}).get("capex_efficiency") or {}
    capex_score25 = capex_detail.get("score")
    capex_note = capex_detail.get("note", "数据不足，跳过")
    lines.append("| 维度 | 评分 | 依据 |")
    lines.append("|------|:---:|------|")
    if capex_score25 is not None:
        score100 = capex_score25 / 25.0 * 100
        lines.append(_canvas_row(
            "研发回报（代理: ΔRevenue/CAPEX）", score100, capex_note,
            ["revenue", "cap_ex"],
        ))
    else:
        lines.append(f"| 研发回报（代理: ΔRevenue/CAPEX） | 数据不足 | {capex_note} |")
    for dim_name, reason in (
        ("并购", "需并购标的估值倍数/协同效应实现情况，当前引擎未采集"),
        ("回购", "需回购价格区间/实际执行率数据，当前引擎未采集"),
        ("IPO 时机", "需发行定价/募资投向执行情况数据，当前引擎未采集"),
        ("库存管理", "需细分库存周转/呆滞库存数据，当前引擎未采集"),
    ):
        lines.append(f"| {dim_name} | 数据不足 | {reason}，需人工定性判断 |")
    lines.append("")
    lines.append(f"[来源: lib.scoring.management_ability_proxy / {mgmt.get('note', '')}]")
    lines.append("")

    # ---- 股东利益一致性 ----
    lines.append("**股东利益一致性**（复用 A-1 内部人信号，避免重复计算逻辑）")
    lines.append("")
    signal = insider_signal(holder_changes)
    if signal == "数据不足":
        lines.append("数据不足：holder_changes 缺失或无可解析公告日期，无法生成内部人一致性信号。")
    else:
        lines.append(
            f"内部人买卖一致性信号：**{signal}**（近 12 个月窗口，基于增减持公告聚合，"
            "详见「3d. 股东增减持动向」信号聚合小节，此处不重复渲染）。"
        )
    lines.append("[来源: lib.scoring.insider_signal / holder_changes]")
    lines.append("")

    # ---- 组织能力 / 企业文化 / 接班人风险 ----
    lines.append("**组织能力 / 企业文化 / 接班人风险**")
    lines.append("")
    lines.append("- 组织能力: [Claude report 阶段定性填充——需结合管理层背景、组织架构变化、核心团队稳定性等公开信息]")
    lines.append("- 企业文化: [Claude report 阶段定性填充——需结合公司治理公告、员工持股计划、历史危机应对记录等公开信息]")
    lines.append("- 接班人风险: [Claude report 阶段定性填充——需结合高管年龄结构、董事会变更公告、控制权结构等公开信息]")
    lines.append("")
    lines.append(
        "> ⚠️ 合规声明：以上时间线与评分仅陈述公开记录事实（决策日期、公告内容、行为统计），"
        "不推断管理层主观动机，不构成对管理层的信赖/不信赖二元结论。"
    )
    lines.append("")
    return "\n".join(lines)


# --- _section_fundamentals_layered ---
def _section_fundamentals_layered(
    dims: dict[str, dict], collection: dict, symbol: str, *, val_cache: dict | None = None,
) -> str:
    """v0.1.3 Phase 2：分层激活基本面 12 题 + LAW 10/14/15 完整框架。

    P0-3 升级：新增「核心判断摘要」与「12题回答状态表」。
    """
    lines = ["## 4. 静态基本面分析", ""]

    # ---- Template A: MD&A 快速扫描 ----
    cards = _get_analysis_cards(collection)
    mda_card = cards.get("mda_narrative")
    if mda_card and isinstance(mda_card, dict):
        gen_at = mda_card.get("generated_at", "")[:10] if mda_card.get("generated_at") else ""
        lines.append("> **MD&A 快速扫描** (自动计算) | 生成时间: " + gen_at)
        rg = mda_card.get("revenue_growth_yoy")
        pg = mda_card.get("profit_growth_yoy")
        gm = mda_card.get("gross_margin")
        gmc = mda_card.get("gross_margin_change")
        nm = mda_card.get("net_margin")
        nmc = mda_card.get("net_margin_change")
        ocf = mda_card.get("operating_cashflow")
        np = mda_card.get("net_profit")
        cq = mda_card.get("cashflow_quality_hint", "")
        ratio_str = ""
        if ocf is not None and np is not None and abs(np) > 1e-9:
            ratio_str = f"{ocf/np:.2f}"
        roe = mda_card.get("roe")
        dr = mda_card.get("debt_ratio")
        _fmt_pct = lambda v: f"{v:.2f}%" if v is not None else "—"
        _fmt_pp = lambda v: f"{v:+.1f}pp" if v is not None else "—"
        rg_s = f"{rg:.2f}%" if rg is not None else "—"
        pg_s = f"{pg:.2f}%" if pg is not None else "—"
        lines.append(f"> - 营收增速: {rg_s} | 净利润增速: {pg_s}")
        lines.append(f"> - 毛利率: {_fmt_pct(gm)} ({_fmt_pp(gmc)}) | 净利率: {_fmt_pct(nm)} ({_fmt_pp(nmc)})")
        if ratio_str:
            lines.append(f"> - 经营现金流/净利润: {ratio_str} → 利润含金量: {cq}")
        if roe is not None:
            dr_label = f"{dr:.2f}%" if dr is not None else "—"
            lines.append(f"> - ROE: {roe:.2f}% | 负债率: {dr_label}")
        ns = mda_card.get("narrative_slot", "")
        if ns:
            lines.append(f"> - 叙事解读: {ns}")
        lines.append("")

    fin = _get_dim_data(dims, "financials")
    fin_list: list[dict] = []
    if fin and isinstance(fin, list):
        fin_list = sort_kline_asc(fin)

    latest_fin = fin_list[-1] if fin_list else {}
    prev_fin = fin_list[-2] if len(fin_list) >= 2 else {}
    first_fin = fin_list[0] if fin_list else {}

    # =================================================================
    # 核心判断摘要（P0-3 升级）
    # =================================================================
    roe_val = _get_safe(fin_list, "roe")
    gm_val = _coalesce_fin_field(fin_list, "grossprofit_margin", "gross_margin")
    np_v = _safe_num(latest_fin.get("net_profit"))
    profit_dedt = _safe_num(latest_fin.get("profit_dedt"))
    debt_ratio = _coalesce_fin_field(fin_list, "debt_ratio", "debt_to_assets")
    em_val = _coalesce_fin_field(fin_list, "equity_multiplier", "em")
    ocf_val = _coalesce_fin_field(fin_list, "ocf", "n_cashflow_act")
    cf_ratio_val: float | None = None
    if ocf_val is not None and np_v is not None and np_v > 0:
        cf_ratio_val = ocf_val / np_v
    rev_cur = _safe_num(latest_fin.get("revenue"))
    rev_prev = _safe_num(prev_fin.get("revenue"))
    rev_yoy: float | None = None
    if rev_cur is not None and rev_prev is not None and rev_prev > 0:
        rev_yoy = (rev_cur - rev_prev) / rev_prev * 100
    ar_cur = _safe_num(latest_fin.get("accounts_receiv") or latest_fin.get("ar"))
    ar_prev = _safe_num(prev_fin.get("accounts_receiv") or prev_fin.get("ar"))
    ar_growth: float | None = None
    if ar_cur is not None and ar_prev is not None and ar_prev > 0:
        ar_growth = (ar_cur - ar_prev) / ar_prev * 100
    inv_cur = _safe_num(latest_fin.get("inventory") or latest_fin.get("inventories"))
    cagr, cagr_years_span = _compute_metric_cagr(fin_list, "revenue")
    np_cagr, np_cagr_years_span = _compute_metric_cagr(fin_list, "net_profit")
    fin_rev_list = [r for r in fin_list if _safe_num(r.get("revenue")) is not None]
    np_cur = np_v
    np_prev = _safe_num(prev_fin.get("net_profit"))
    ocf = ocf_val
    cf_ratio = cf_ratio_val

    lines.append("\n### 核心判断摘要\n")

    # 判断1: 盈利结构
    lines.append("#### 盈利结构")
    lines.append(f"[结论] {_conclude_profit_structure(roe_val, gm_val, debt_ratio)}")
    lines.append("")
    lines.append("[事实]")
    if roe_val is not None:
        lines.append(f"- ROE(TTM) = {_fmt_num(roe_val)}%")
    if gm_val is not None:
        lines.append(f"- 毛利率 = {_fmt_num(gm_val)}%")
    if debt_ratio is not None:
        lines.append(f"- 资产负债率 = {_fmt_num(debt_ratio)}%")
    if profit_dedt is not None and np_v is not None and np_v > 0:
        c4_ratio = profit_dedt / np_v
        lines.append(f"- 扣非/净利润 = {c4_ratio:.2f}")
    lines.append("")
    lines.append("[分析]")
    analysis_parts = []
    if roe_val is not None and gm_val is not None:
        if roe_val >= 15 and gm_val >= 40:
            analysis_parts.append("高 ROE × 高毛利率组合，盈利模式具备结构优势")
        elif roe_val >= 15 and gm_val < 20:
            analysis_parts.append("ROE 虽然较高但毛利率偏低，盈利依赖高周转或高杠杆驱动，需警惕可持续性")
        elif roe_val < 10 and gm_val >= 40:
            analysis_parts.append("高毛利率但低 ROE，可能费用率偏高或资产周转效率不足")
        else:
            analysis_parts.append(f"ROE={roe_val:.1f}%、毛利率={gm_val:.1f}%，盈利模式处于行业常见区间，需持续跟踪变化趋势")
    if debt_ratio is not None and debt_ratio > 70:
        analysis_parts.append("资产负债率偏高，需关注偿债风险与财务费用对利润的侵蚀")
    if profit_dedt is not None and np_v is not None and np_v > 0:
        c4_check = profit_dedt / np_v
        if c4_check < 0.7:
            analysis_parts.append("非经常性损益占比过大，净利润质量存疑")
    if not analysis_parts:
        analysis_parts.append("数据有限，无法进行充分的分析推理")
    lines.append("；".join(analysis_parts))
    lines.append("")
    e1_items = [roe_val is not None, gm_val is not None, debt_ratio is not None,
                profit_dedt is not None and np_v is not None and np_v > 0]
    lines.append(f"[证据强度: {_evidence_strength_label(e1_items)}]")
    lines.append("")

    # 判断2: 现金流质量
    lines.append("#### 现金流质量")
    lines.append(f"[结论] {_conclude_cash_flow_quality(cf_ratio_val, ar_growth, rev_yoy, ocf_val, np_v=np_v)}")
    lines.append("")
    lines.append("[事实]")
    if ocf_val is not None:
        lines.append(f"- 经营现金流 = {_fmt_v2(ocf_val)}")
    if np_v is not None:
        lines.append(f"- 净利润 = {_fmt_v2(np_v)}")
    if cf_ratio_val is not None:
        lines.append(f"- 经营现金流/净利润 = {cf_ratio_val:.2f}")
    if ar_growth is not None and rev_yoy is not None:
        lines.append(f"- 应收增速 vs 营收增速：{ar_growth:+.2f}% vs {rev_yoy:+.2f}%")
    lines.append("")
    lines.append("[分析]")
    cf_analysis = []
    if cf_ratio_val is not None:
        if cf_ratio_val >= 1.0:
            cf_analysis.append("经营现金流充分覆盖净利润，利润含金量高")
        elif cf_ratio_val >= 0.8:
            cf_analysis.append("经营现金流基本覆盖净利润，利润质量良好")
        else:
            cf_analysis.append("经营现金流覆盖不足，利润质量存疑，需关注应收与存货变化")
    if ar_growth is not None and rev_yoy is not None:
        if ar_growth > rev_yoy * 1.5:
            cf_analysis.append(f"应收增速远超营收增速，存在赊销膨胀或回款恶化的风险")
        elif ar_growth > rev_yoy:
            cf_analysis.append("应收增速略高于营收增速，需关注回款节奏变化")
        else:
            cf_analysis.append("应收增速低于营收增速，收入增长质量较高")
    if not cf_analysis:
        cf_analysis.append("数据有限，无法进行充分的现金流分析")
    lines.append("；".join(cf_analysis))
    lines.append("")
    e2_items = [ocf_val is not None, np_v is not None,
                ar_growth is not None and rev_yoy is not None]
    lines.append(f"[证据强度: {_evidence_strength_label(e2_items)}]")
    lines.append("")

    # 判断3: 资产负债与扩产路径
    lines.append("#### 资产负债与扩产路径")
    conclusion3 = _conclude_asset_liability(debt_ratio, em_val, ar_cur, inv_cur, rev_cur)
    lines.append(f"[结论] {conclusion3}")
    lines.append("")
    lines.append("[事实]")
    if debt_ratio is not None:
        lines.append(f"- 资产负债率 = {debt_ratio:.2f}%")
    if em_val is not None:
        lines.append(f"- 权益乘数 = {em_val:.2f}")
    if ar_cur is not None and inv_cur is not None and rev_cur is not None and rev_cur > 0:
        wc_ratio = (ar_cur + inv_cur) / rev_cur * 100
        lines.append(f"- (应收+存货)/营收 = {wc_ratio:.1f}%")
    lines.append("")
    lines.append("[分析]")
    al_analysis = []
    if debt_ratio is not None:
        if debt_ratio >= 70:
            al_analysis.append("资产负债率偏高，扩产主要依赖负债融资，财务风险较大")
        elif debt_ratio >= 50:
            al_analysis.append("资产负债率适中，扩产可在负债与权益间灵活选择")
        else:
            al_analysis.append("资产负债率较低，扩产空间充足，可使用合理杠杆加速发展")
    if em_val is not None:
        if em_val > 3:
            al_analysis.append("权益乘数较高，扩产路径可能受限于融资能力")
        elif em_val < 1.5:
            al_analysis.append("权益乘数偏低，扩产路径可适度加杠杆")
    if ar_cur is not None and inv_cur is not None and rev_cur is not None and rev_cur > 0:
        wc_ratio_val = (ar_cur + inv_cur) / rev_cur
        if wc_ratio_val > 0.5:
            al_analysis.append("运营资金占用偏高，扩产时需关注现金流压力")
        else:
            al_analysis.append("运营资金占用较低，扩产的现金流压力较小")
    if not al_analysis:
        al_analysis.append("数据有限，无法进行充分的资产负债分析")
    lines.append("；".join(al_analysis))
    lines.append("")
    e3_items = [debt_ratio is not None, em_val is not None,
                ar_cur is not None and rev_cur is not None]
    lines.append(f"[证据强度: {_evidence_strength_label(e3_items)}]")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.extend(_financial_panorama_table(fin_list))

    val_data = _get_dim_data(dims, "valuation")
    pe_seq: list[float] = []
    pb_seq: list[float] = []
    ps_seq: list[float] = []
    val_sorted: list[dict] = []
    dv_ratio: float | None = None
    val_window_label = "历史"
    current_pe: float | None = None
    current_pb: float | None = None
    if val_data and isinstance(val_data, list):
        from lib.valuation import valuation_window_label

        val_sorted = sort_kline_asc(val_data)
        pe_seq = [r.get("pe_ttm") for r in val_sorted if r.get("pe_ttm") is not None]
        pb_seq = [r.get("pb") for r in val_sorted if r.get("pb") is not None]
        ps_seq = [
            r.get("ps_ttm") or r.get("ps")
            for r in val_sorted
            if (r.get("ps_ttm") is not None or r.get("ps") is not None)
        ]
        for r in reversed(val_sorted):
            if r.get("dv_ratio") is not None:
                dv_ratio = _safe_num(r.get("dv_ratio"))
                break
        val_window_label = valuation_window_label(len(val_sorted))
        if pe_seq:
            current_pe = pe_seq[-1]
        if pb_seq:
            current_pb = pb_seq[-1]

    # 行业同行数据
    industry_peers = collection.get("industry_peers") or {}
    ms = collection.get("market_structure") or {}
    sw = ms.get("sw_index") or {}
    pmi_data = ms.get("pmi") or {}
    pe_pct, pb_pct_ext, _ = _v3_valuation_percentiles(dims, val_cache)
    trigger_c = _v3_trigger_c_active(ms)

    # =================================================================
    # 4a. 行业位置（3 题）
    # =================================================================
    lines.append("### 4a. 行业位置")
    lines.append("")

    # A-① 行业景气度
    lines.append("#### A-① 行业景气度")
    if sw and sw.get("return_20d_pct") is not None:
        lines.append(
            f"申万行业指数 {sw.get('index_code', '?')}（{sw.get('industry', '?')}）"
            f"近 20 日涨跌：**{sw['return_20d_pct']:+.2f}%**。"
        )
        rel = sw.get("relative_vs_benchmark_pct")
        if rel is not None:
            direction = "跑赢" if rel > 0 else "跑输"
            lines.append(f"相对沪深 300：{rel:+.2f}%（{direction}大盘）。")
        svi = sw.get("stock_vs_industry_pct")
        if svi is not None:
            direction = "跑赢" if svi > 0 else "跑输"
            lines.append(f"个股相对行业：{svi:+.2f}%（{direction}行业板块）。")
    else:
        lines.append("数据不足：[申万行业指数不可得，无法判断行业景气度]")
    if pmi_data.get("manufacturing_pmi") is not None:
        lines.append(
            f"制造业 PMI：**{pmi_data['manufacturing_pmi']:.1f}**（{pmi_data.get('month', '?')}，"
            f"{pmi_data.get('signal', '?')}）[来源: {pmi_data.get('source', 'akshare')}]"
        )
    else:
        lines.append("PMI/产量：数据不足：[宏观 PMI 数据源不可得；产量分项需行业数据库或 WebSearch 补充]")
    lines.append("")
    sw_ret = sw.get("return_20d_pct")
    a1_pitfall = (
        f"本次申万板块近 20 日涨跌为 {sw_ret:+.2f}%，若直接等同于公司经营改善，"
        f"可能忽略板块内部分化——需核对本公司营收增速是否与板块同向。"
        if sw_ret is not None else
        "申万行业指数本次不可得，若仅凭个股涨跌判断行业景气，可能把公司特异性波动误判为行业趋势。"
    )
    lines.append(_law10_hint(
        "行业景气度决定个股定价的贝塔部分，板块同向运动时个股 Alpha 的置信度更高。",
        a1_pitfall,
        [
            "对比板块内市值相近公司涨跌幅离散度",
            "查近期行业政策/供需公告（WebSearch）验证板块方向持续性",
            "观察板块成交量是否放大（放量趋势 vs 缩量反弹）",
        ],
    ))
    if trigger_c:
        lines.append("")
        lines.append("**[扩展激活 · 触发源 C]** 行业结构驱动：建议补充竞争格局变化分析——"
                     "板块相对大盘偏离显著时，需区分行业景气 vs 估值重估 vs 政策预期。")
        lines.append("**[扩展激活 · 行业政策]** 近 30 日行业政策/监管事件需 WebSearch 补充，"
                     "并追溯政策传导至公司收入/成本的具体路径。")
    lines.append("")

    # A-② 竞争位置
    lines.append("#### A-② 竞争位置")
    basic = _get_dim_data(dims, "basic_info")
    industry_name = ""
    if isinstance(basic, dict):
        industry_name = basic.get("industry", "") or basic.get("行业", "") or ""
    rev = _safe_num(latest_fin.get("revenue"))
    ry_pct = industry_peers.get("rankings", {}).get("revenue_yoy_pct")
    ry_rank = industry_peers.get("rankings", {}).get("revenue_yoy_rank")
    ry_total = industry_peers.get("rankings", {}).get("revenue_yoy_total")
    target_ry = (industry_peers.get("target") or {}).get("revenue_yoy")
    peer_source = industry_peers.get("peer_source")
    if peer_source == "stock_basic_fallback":
        warn = industry_peers.get("warning") or "非申万 L3 成分股"
        lines.append(f"⚠️ {warn}")
    if rev is not None and industry_peers.get("sufficient"):
        lines.append(f"所属行业：{industry_name or industry_peers.get('industry_name') or '未知'}（申万分类）。")
        if ry_pct is not None and ry_rank and ry_total:
            pos = _competitive_position_label(ry_pct)
            ry_s = f"{target_ry:+.2f}%" if target_ry is not None else "—"
            lines.append(
                f"竞争位置参考：**{pos}**（营收增速 {ry_s}，同行排名 {ry_rank}/{ry_total}，分位 {ry_pct:.1f}%）。"
            )
        else:
            lines.append("数据不足：[同行营收增速排名字段不完整]")
    else:
        if industry_name:
            lines.append(f"所属行业：{industry_name}。")
        if peer_source == "stock_basic_fallback":
            lines.append("数据不足：[同行池非申万 L3 成分，分位排名已降级]")
        else:
            err = industry_peers.get("error", "缺少同行营收对比数据")
            lines.append(f"数据不足：[{err}]")
    lines.append("")
    a2_pitfall = (
        f"本次营收增速同行分位为 {ry_pct:.1f}%（排名 {ry_rank}/{ry_total}），"
        f"若据此直接认定竞争壁垒，可能忽略毛利率与 ROE 的转化效率。"
        if ry_pct is not None and ry_rank and ry_total else
        f"本次仅有行业名「{industry_name or '?'}」、缺少同行增速对比，"
        "不宜仅凭营收规模推断龙头地位。"
    )
    lines.append(_law10_hint(
        "竞争位置决定定价溢价/折价的合理性——龙头享有流动性溢价，追赶者需证明成长性。",
        a2_pitfall,
        [
            "对比毛利率与行业均值差异（见 A-③）",
            "查公司市占率数据（年报/行业报告）",
            "关注近 3 年竞争位置是上升还是下降趋势",
        ],
    ))
    lines.append("")

    # A-③ 毛利率 vs 行业中位数
    lines.append("#### A-③ 毛利率 vs 行业中位数")
    gross_margin = _fin_field_num(latest_fin, "gross_margin", "grossprofit_margin")
    if gross_margin is None:
        for r in reversed(fin_list):
            gross_margin = _fin_field_num(r, "gross_margin", "grossprofit_margin")
            if gross_margin is not None:
                break
    if gross_margin is not None:
        lines.append(f"最新报告期毛利率：**{gross_margin:.2f}%**。")
        peer_gms = [
            _fin_field_num(p, "gross_margin", "grossprofit_margin")
            for p in industry_peers.get("peers", [])
        ]
        peer_gms = [g for g in peer_gms if g is not None]
        if len(peer_gms) >= 3:
            from lib.valuation import median_of
            ind_med = median_of(peer_gms)
            diff = gross_margin - ind_med
            lines.append(f"同行毛利率中位数：**{ind_med:.2f}%**（样本 {len(peer_gms)} 家），差异 **{diff:+.2f}pp**。")
        else:
            lines.append("数据不足：[同行毛利率样本不足 3 家，需 income 表批量采集]")
    else:
        lines.append("数据不足：[毛利率字段不可得；需 fina_indicator.grossprofit_margin 或 income 表]")
    lines.append("")
    a3_pitfall = (
        f"本次毛利率 {gross_margin:.2f}%，若仅因高于行业均值就认定定价权，"
        "可能忽略销售费用率是否同步偏高（高毛利低净利模式）。"
        if gross_margin is not None else
        "本次毛利率不可得，不宜用 ROE 或营收增速间接替代毛利率做定价权判断。"
    )
    lines.append(_law10_hint(
        "毛利率是定价权的第一道防线——高毛利率意味着客户对价格不敏感或产品有差异化壁垒。",
        a3_pitfall,
        [
            "对比同行业公司毛利率离散度（若可得）",
            "观察毛利率近 3 年趋势：下降可能暗示竞争加剧或成本上升",
            "结合应收/现金流验证收入质量（见 C-③）",
        ],
    ))
    lines.append("")

    # =================================================================
    # 4b. 商业质量（3 题）
    # =================================================================
    lines.append("### 4b. 商业质量")
    lines.append("")

    # B-① 护城河来源
    lines.append("#### B-① 护城河来源")
    roe_now = _safe_num(latest_fin.get("roe"))
    roe_first = _safe_num(first_fin.get("roe"))
    if roe_now is not None:
        lines.append(f"当前 ROE：**{roe_now:.2f}%**。")
        if roe_first is not None and len(fin_list) >= 4:
            trend = "强化" if roe_now > roe_first + 2 else (
                "侵蚀" if roe_now < roe_first - 2 else "稳定")
            lines.append(f"近 {len(fin_list)} 期 ROE 趋势：{roe_first:.2f}% → {roe_now:.2f}%（{trend}）。")
        lines.append("")
        lines.append("护城河定性判断需结合以下维度（数据引擎提供定量基础，AI 做定性综合）：")
        lines.append(f"- **利润转化效率：** ROE={roe_now:.2f}%、扣非/净利润比例见 C-④")
        lines.append("- **现金流健康度：** 经营现金流/净利润覆盖比见 C-③")
        lines.append("- **收入可持续性：** 近 3 年营收 CAGR 见 C-①")
        lines.append("- **资产回报效率：** 杜邦拆解见 C-②")
    else:
        lines.append("数据不足：[缺少 ROE 数据，无法评估护城河]")
    lines.append("")
    lines.append(_law10_hint(
        "护城河是长期估值的锚——没有护城河的高增长公司，估值收缩速度可能快于预期（待补案例）。",
        (
            f"本次 ROE {roe_now:.2f}%"
            + (f"（{roe_first:.2f}% → {roe_now:.2f}%）" if roe_first is not None else "")
            + "，若直接等同于强护城河，可能忽略高杠杆或周期高点的一次性贡献（见 C-② 杜邦）。"
            if roe_now is not None else
            "本次 ROE 不可得，不宜用营收增速或 PE 分位间接替代护城河判断。"
        ),
        [
            "杜邦拆解 ROE 来源（见 C-②）",
            "对比同行 ROE 中位数（见可比公司表）",
            "查公司年报中「核心竞争力」部分与实际财务数据是否一致",
        ],
    ))
    lines.append("")

    # A-4: 商业模式画布（v0.1.8 Step 6，紧接 B-① 护城河来源之后）
    lines.append(_section_business_model_canvas(
        fin_list,
        dims.get("holder_changes") or {},
        collection.get("chain_context") or {},
    ))

    # A-5: 管理层完整评估（v0.1.8 Step 6，与商业模式画布并列在 4b 商业质量段落）
    lines.append(_section_management_assessment(
        collection.get("events"),
        dims.get("holder_changes") or {},
        fin_list,
    ))

    # B-② 增长驱动力
    lines.append("#### B-② 增长驱动力")
    if rev_cur is not None and rev_prev is not None and rev_prev > 0:
        lines.append(f"最近一期营收同比：**{rev_yoy:+.2f}%**。")
    if np_cur is not None and np_prev is not None and np_prev > 0:
        np_yoy = (np_cur - np_prev) / np_prev * 100
        lines.append(f"最近一期净利润同比：**{np_yoy:+.2f}%**。")
    if not (rev_cur and rev_prev) and not (np_cur and np_prev):
        lines.append("数据不足：[缺少两期以上可比营收/净利润数据]")
    elif cagr is not None and cagr_years_span is not None:
        lines.append(
            f"近 {cagr_years_span:.1f} 年营收 CAGR：**{cagr:+.2f}%**（多年增长趋势锚点）。"
        )
        if rev_yoy is not None:
            if rev_yoy > cagr + 3:
                sustain = "加速，驱动力仍在强化"
            elif rev_yoy < cagr - 3:
                sustain = "减速，需关注驱动力是否切换"
            else:
                sustain = "与多年趋势基本一致，驱动力仍在持续"
            lines.append(
                f"驱动力持续性：**{sustain}**（最近同比 {rev_yoy:+.2f}% vs CAGR {cagr:+.2f}%）。"
            )
        gm_first = _fin_field_num(first_fin, "gross_margin", "grossprofit_margin")
        gm_latest = gross_margin
        if gm_latest is None:
            gm_latest = _fin_field_num(latest_fin, "gross_margin", "grossprofit_margin")
        if gm_first is not None and gm_latest is not None:
            gm_chg = gm_latest - gm_first
            if gm_chg > 1 and (cagr or 0) > 0:
                lines.append(
                    f"价驱动信号：毛利率 {gm_first:.2f}% → {gm_latest:.2f}%（{gm_chg:+.2f}pp），"
                    "收入增长可能含定价/结构升级贡献。"
                )
            elif abs(gm_chg) <= 1 and (cagr or 0) > 0:
                lines.append(
                    f"量驱动信号：毛利率基本稳定（{gm_first:.2f}% → {gm_latest:.2f}%），"
                    "增长更多来自规模扩张或份额提升。"
                )
        roe_first_v = _safe_num(first_fin.get("roe"))
        roe_latest_v = _safe_num(latest_fin.get("roe"))
        if roe_first_v is not None and roe_latest_v is not None:
            if roe_latest_v > roe_first_v + 3 and (cagr or 0) > 0:
                lines.append(
                    f"杠杆驱动警示：ROE {roe_first_v:.2f}% → {roe_latest_v:.2f}% 升幅较大，"
                    "需结合 C-② 杜邦验证是否来自权益乘数。"
                )
    lines.append("")
    lines.append("增长驱动力来源需结合以下判断：")
    lines.append("- **量驱动：** 收入增速 > 行业均值 → 份额扩张（收入/应收见 C-③）")
    lines.append("- **价驱动：** 毛利率扩张 + 收入增长 → 定价权提升（毛利率见 A-③）")
    lines.append("- **杠杆驱动：** ROE 提升来自权益乘数 → 不可持续（杜邦见 C-②）")
    lines.append("")
    b2_pitfall = (
        f"本次营收同比 {rev_yoy:+.2f}%，若等同于价值创造，可能忽略资本开支/ROIC——"
        "低回报扩张反而摧毁股东价值。"
        if rev_yoy is not None else
        "本次缺少两期可比营收，不宜用单季利润波动推断增长驱动力类型。"
    )
    lines.append(_law10_hint(
        "增长驱动力类型决定估值倍数——量价齐升（质量最高）vs 纯杠杆扩张（质量最低）。",
        b2_pitfall,
        [
            "对比营收增速与行业均值（见可比公司表）",
            "观察毛利率与营收增速方向是否一致（量价关系）",
            "关注业绩预告/管理层指引中的增长驱动力表述",
        ],
    ))
    lines.append("")
    lines.append("**[扩展激活 · 业绩预告]** 数据不足：[业绩预告数据源未接入，需 Tushare forecast / WebSearch 补充]；"
                 "若后续获取预告，应对比 B-② 驱动力是否发生转换。")
    lines.append("")

    # B-③ 现金流模式
    lines.append("#### B-③ 现金流模式")
    if ocf is not None and np_v is not None and np_v > 0:
        cf_ratio = ocf / np_v
        quality = "健康" if cf_ratio >= 0.8 else (
            "偏弱" if cf_ratio >= 0.5 else "严重背离")
        lines.append(f"经营现金流/净利润覆盖比：**{cf_ratio:.2f}**（{quality}）。")
        if cf_ratio < 0.8:
            lines.append("⚠️ 现金流覆盖比 < 0.8，建议扩展分析：收入确认质量、应收/存货变动（见 C-③ 交叉验证）。")
    elif ocf is None:
        lines.append("数据不足：[经营现金流字段不可得]")
    elif np_v is None or np_v <= 0:
        lines.append("数据不足：[净利润非正，无法计算覆盖比]")
    lines.append("")
    lines.append(_law10_hint(
        "现金流是利润的「含金量」检验——利润好看但现金流持续弱于利润，"
        "可能意味着应收膨胀、存货积压或收入确认激进（待补案例）。",
        (
            f"本次经营现金流/净利润 = {cf_ratio:.2f}，若仅看单期就认定利润质量差，"
            "可能忽略季节性备货——应对比连续 4 期趋势。"
            if cf_ratio is not None else
            "本次现金流覆盖比不可得，不宜用净利润同比单独判断利润含金量。"
        ),
        [
            "对比应收增速 vs 营收增速（CV-2）",
            "对比存货增速 vs 营收增速",
            "查看连续 4 期现金流覆盖比趋势方向",
        ],
    ))
    if cf_ratio is not None and cf_ratio < 0.8:
        lines.append("")
        lines.append("**[扩展激活 · 现金流覆盖 < 0.8]** 建议深度扫描收入确认质量："
                     "核对应收账龄、收入确认政策变更、大客户集中度变化。")
    lines.append("")

    # =================================================================
    # 4c. 财务质量（4 题）
    # =================================================================
    lines.append("### 4c. 财务质量")
    lines.append("")

    # C-① 近 3 年营收 CAGR
    lines.append("#### C-① 近 3 年营收 CAGR")
    if len(fin_rev_list) >= 2 and cagr is not None and cagr_years_span is not None:
        d0 = _fmt_end_date(fin_rev_list[0].get("end_date")) or "首期"
        d1 = _fmt_end_date(fin_rev_list[-1].get("end_date")) or "末期"
        lines.append(f"近 {cagr_years_span:.1f} 年营收 CAGR：**{cagr:+.2f}%**（{d0} → {d1}）。")
        if rev_yoy is not None:
            recent_trend = "加速" if rev_yoy > cagr + 3 else (
                "减速" if rev_yoy < cagr - 3 else "持平")
            lines.append(f"近一年趋势：{recent_trend}（最近一期同比 {rev_yoy:+.2f}% vs CAGR {cagr:+.2f}%）。")
    elif len(fin_rev_list) >= 2:
        lines.append("数据不足：[营收数据异常（首期或末期为零/负）]")
    else:
        lines.append("数据不足：[财务数据少于 2 期]")
    lines.append("")
    lines.append(_law10_hint(
        "营收 CAGR 是估值模型中增长率假设的锚——CAGR 的稳定性直接影响 DCF/g 值的置信度。",
        (
            f"本次 CAGR {cagr:+.2f}%、最近一期同比 {rev_yoy:+.2f}%，"
            "若机械外推历史 CAGR 至未来，可能在增速已放缓时高估。"
            if cagr is not None and rev_yoy is not None else
            "本次 CAGR 或近一年同比不可得，不宜用单季利润波动外推长期增长率。"
        ),
        [
            "对比净利润 CAGR 与营收 CAGR 是否同步（利润增速 > 收入增速 = 利润率扩张）",
            "结合行业景气度判断增长是行业性还是公司特异性",
            "关注近两季趋势：加速/减速背后的原因是什么",
        ],
    ))
    lines.append("")

    # C-② 杜邦拆解 ROE
    lines.append("#### C-② 杜邦拆解 ROE")
    roe_v = _safe_num(latest_fin.get("roe"))
    npm = _safe_num(latest_fin.get("netprofit_margin") or latest_fin.get("np_margin"))
    tat = _safe_num(latest_fin.get("asset_turnover") or latest_fin.get("assets_turn"))
    em = _safe_num(latest_fin.get("equity_multiplier") or latest_fin.get("em"))
    # 若杜邦字段不可得，尝试从已有数据计算
    if npm is None and np_cur is not None and rev_cur is not None and rev_cur > 0:
        npm = np_cur / rev_cur * 100
    dupont_available = npm is not None and tat is not None and em is not None
    if roe_v is not None:
        lines.append(f"ROE：**{roe_v:.2f}%**。")
        if dupont_available:
            lines.append(f"- **净利润率：** {npm:.2f}%（{'高利润率模式' if npm > 15 else '低利润率/高周转模式' if npm < 5 else '中等利润率'}）")
            lines.append(f"- **资产周转率：** {tat:.4f}（{'重资产' if tat < 0.5 else '轻资产/高周转' if tat > 1.5 else '中等周转'}）")
            lines.append(f"- **权益乘数：** {em:.2f}（{'高杠杆' if em > 3 else '低杠杆' if em < 1.5 else '中等杠杆'}）")
            dupont_roe = npm / 100 * tat * em * 100
            lines.append(f"- 杜邦 ROE 校验：{dupont_roe:.2f}%（{'与 ROE 一致' if abs(dupont_roe - roe_v) < 0.5 else '与 ROE 存在差异，可能存在口径问题'}）")
        else:
            lines.append("数据不足：[杜邦拆解字段不可得；fina_indicator 不含净利率/周转率/权益乘数，需 income + balance 表]")
        # ROE 变化超 ±5pp → 提示完整杜邦
        roe_prev_v = _safe_num(prev_fin.get("roe"))
        if roe_prev_v is not None and abs(roe_v - roe_prev_v) > 5:
            lines.append(f"⚠️ ROE 近两期变化超 ±5pp（{roe_prev_v:.2f}% → {roe_v:.2f}%），建议完整杜邦拆解。")
    else:
        lines.append("数据不足：[ROE 字段不可得]")
    lines.append("")
    lines.append(_law10_hint(
        "杜邦拆解回答「ROE 从哪来」——高净利率（品牌/技术壁垒）> 高周转（运营效率）> 高杠杆（财务风险）。",
        (
            f"本次 ROE {roe_v:.2f}%"
            + (f"、净利润率 {npm:.2f}%、周转 {tat:.4f}、权益乘数 {em:.2f}"
               if dupont_available else "")
            + "，若只看 ROE 绝对值不看结构，可能把高杠杆驱动的 ROE 误判为经营优秀。"
            if roe_v is not None else
            "本次 ROE 不可得，不宜用 PE 或营收增速间接替代 ROE 结构分析。"
        ),
        [
            "若 ROE 变化 > 5pp，追溯三大驱动因子的各自贡献变化",
            "对比同行 ROE 结构（高杠杆在加息周期更脆弱）",
            "关注权益乘数的负债结构（有息负债 vs 经营负债）",
        ],
    ))
    lines.append("")

    # C-③ 经营现金流/净利润 + 应收/存货增速对比 + CV-2
    lines.append("#### C-③ 现金流覆盖 + 应收/存货交叉验证")
    if ocf is not None and np_v is not None and np_v > 0:
        cf_ratio = ocf / np_v
        lines.append(f"- 经营现金流/净利润：**{cf_ratio:.2f}**")
    else:
        lines.append("数据不足：[经营现金流或净利润字段不可得，无法计算覆盖比]")
    # 应收增速 vs 营收增速 (CV-2)
    ar_cur = _safe_num(latest_fin.get("accounts_receiv") or latest_fin.get("ar"))
    ar_prev = _safe_num(prev_fin.get("accounts_receiv") or prev_fin.get("ar"))
    inv_cur = _safe_num(latest_fin.get("inventory") or latest_fin.get("inventories"))
    inv_prev = _safe_num(prev_fin.get("inventory") or prev_fin.get("inventories"))
    rev_growth: float | None = rev_yoy
    ar_growth: float | None = None
    inv_growth: float | None = None
    if rev_growth is None and rev_cur is not None and rev_prev is not None and rev_prev > 0:
        rev_growth = (rev_cur - rev_prev) / rev_prev * 100
    if ar_cur is not None and ar_prev is not None and ar_prev > 0:
        ar_growth = (ar_cur - ar_prev) / ar_prev * 100
        lines.append(f"- 应收账款增速：**{ar_growth:+.2f}%**")
        if rev_cur is not None and rev_prev is not None and rev_prev > 0:
            rev_growth = (rev_cur - rev_prev) / rev_prev * 100
            lines.append(f"- 营收增速：**{rev_growth:+.2f}%**")
            if ar_growth > rev_growth * 1.5:
                cv2_status = "divergence"
                cv2_detail = (f"应收增速 {ar_growth:+.2f}% 远超营收增速 {rev_growth:+.2f}%，"
                              "可能存在收入确认激进或回款恶化")
            elif ar_growth > rev_growth:
                cv2_status = "divergence"
                cv2_detail = (f"应收增速 {ar_growth:+.2f}% 略高于营收增速 {rev_growth:+.2f}%，"
                              "关注回款节奏，暂未触发 1.5× 预警")
            else:
                cv2_status = "convergence"
                cv2_detail = (f"应收增速 {ar_growth:+.2f}% 低于营收增速 {rev_growth:+.2f}%，"
                              "收入增长质量较高")
            lines.append("")
            lines.append(_cv(cv2_status, "CV-2", "营收增长 vs 应收账款增长", cv2_detail, "中"))
    else:
        lines.append("数据不足：[缺少资产负债表应收/存货字段；需 balancesheet 或 akshare 财务摘要]")
    # 存货增速
    if inv_cur is not None and inv_prev is not None and inv_prev > 0:
        inv_growth = (inv_cur - inv_prev) / inv_prev * 100
        lines.append(f"- 存货增速：**{inv_growth:+.2f}%**")
        if rev_growth is not None and inv_growth > rev_growth * 1.5:
            lines.append("⚠️ 存货增速超营收增速 1.5×，关注存货积压风险。")
            lines.append("**[扩展激活 · 报表风险]** 存货扩张异常：建议核对产品滞销、渠道压货或会计政策变更。")
    lines.append("")
    c3_pitfall = (
        f"本次应收增速 {ar_growth:+.2f}% vs 营收增速 {rev_growth:+.2f}%，"
        "若仅因应收上升就认定收入造假，可能忽略大客户账期正常延长——需结合账龄结构验证。"
        if ar_growth is not None and rev_growth is not None else
        (
            f"本次现金流/净利润 = {cf_ratio:.2f}，若忽视应收/存货字段缺失，"
            "可能漏掉利润质量交叉验证。"
            if cf_ratio is not None else
            "本次缺少应收/存货与现金流数据，不宜单独用净利润同比判断收入质量。"
        )
    )
    lines.append(_law10_hint(
        "应收增速 > 营收增速是经典的利润质量预警信号——激进赊销可以短期推高营收，"
        "但最终会以坏账或回款恶化暴露。存货积压则可能意味着产品滞销。",
        c3_pitfall,
        [
            "查看连续 4 期以上应收/营收增速对比趋势",
            "查账龄结构（1 年以内应收占比，需财报附注）",
            "结合经营现金流方向确认（利润增长+现金流恶化=红色信号）",
        ],
    ))
    lines.append("")

    # C-④ 扣非/净利润
    lines.append("#### C-④ 扣非/净利润")
    profit_dedt = _safe_num(latest_fin.get("profit_dedt"))
    ratio_c4: float | None = None
    if profit_dedt is not None and np_v is not None and np_v > 0:
        ratio_c4 = profit_dedt / np_v
        quality_label = "健康" if ratio_c4 >= 0.9 else (
            "存在非经常性损益" if ratio_c4 >= 0.7 else "非经常性损益扭曲严重")
        lines.append(f"扣非净利润/净利润：**{ratio_c4:.2f}**（{quality_label}）。")
        if ratio_c4 < 0.7:
            lines.append("⚠️ 扣非/净利润 < 0.7，净利润被非经常性损益显著抬高。请查阅最新财报附注中「非经常性损益」明细。")
        lines.append(f"扣非净利润：{_fmt_v2(profit_dedt)}；净利润：{_fmt_v2(np_v)}。")
    elif np_v is not None and np_v <= 0:
        lines.append("数据不足：[净利润非正，扣非/净利润比值无意义]")
    else:
        lines.append("数据不足：[扣非净利润或净利润字段不可得]")
    lines.append("")
    lines.append(_law10_hint(
        "扣非/净利润比值反映利润的「可持续性」——卖资产、政府补贴、投资收益等非经常性损益"
        "不具有重复性，以此为基础的 PE 估值会产生误导。",
        (
            f"本次扣非/净利润 = {ratio_c4:.2f}（扣非 {_fmt_v2(profit_dedt)} / 净利 {_fmt_v2(np_v)}），"
            "若仅因单期扣非偏低就认定利润质量差，可能忽略一次性资产处置的偶发性。"
            if ratio_c4 is not None else
            "本次扣非或净利润不可得，不宜用 PE 分位单独判断盈利可持续性。"
        ),
        [
            "查看连续 4 期扣非/净利润比值趋势",
            "查阅财报「非经常性损益」明细（政府补贴/资产处置/投资收益占比）",
            "对比同行扣非/净利润比值（行业特征如地产/金融需特殊处理）",
        ],
    ))
    if ratio_c4 is not None and ratio_c4 < 0.7:
        lines.append("")
        lines.append("**[扩展激活 · 报表风险]** 扣非/净利润 < 0.7：建议查阅非经常性损益明细并对比连续 4 期趋势。")
    lines.append("")

    # =================================================================
    # 4d. 估值与预期（3 题 + LAW 15）
    # =================================================================
    lines.append("### 4d. 估值与预期")
    lines.append("")

    # D-① PE/PB 5 年历史位置
    lines.append("#### D-① PE/PB 历史位置")
    if pe_seq and current_pe is not None:
        vs = _v3_load_valuation_summary(dims, val_cache)
        if not vs:
            lines.append("数据不足：[估值历史序列不可得，建议配置 Tushare Token 获取 daily_basic]")
        else:
            pe_info = vs["pe"]
            pb_info = vs["pb"]
            ps_info = vs.get("ps", {})
            wl = vs.get("window_label", val_window_label)
            if pe_info.get("current") is not None:
                pct_str = f"，{wl} {pe_info['pct']:.1f}% 历史位置" if pe_info.get("pct") is not None else ""
                lines.append(f"- PE(TTM)：**{pe_info['current']:.2f}x**{pct_str}，处于历史**{pe_info.get('zone', '未知')}**区间。")
            else:
                lines.append(f"- PE(TTM)：{pe_info.get('reason', '不可得')}")
            if pb_info.get("current") is not None:
                pct_str = f"，{wl} {pb_info['pct']:.1f}% 历史位置" if pb_info.get("pct") is not None else ""
                lines.append(f"- PB：**{pb_info['current']:.2f}x**{pct_str}，处于历史**{pb_info.get('zone', '未知')}**区间。")
            else:
                lines.append(f"- PB：{pb_info.get('reason', '不可得')}")
            if ps_info.get("current") is not None:
                pct_str = f"，{wl} {ps_info['pct']:.1f}% 历史位置" if ps_info.get("pct") is not None else ""
                lines.append(f"- PS(TTM)：**{ps_info['current']:.2f}x**{pct_str}。")
            else:
                lines.append("数据不足：[估值序列无 ps/ps_ttm 字段]")
            if vs.get("dv_ratio") is not None:
                lines.append(f"- 股息率：**{vs['dv_ratio']:.2f}%**（最近交易日 dv_ratio）")
            else:
                lines.append("数据不足：[daily_basic 无 dv_ratio 股息率字段]")
            for w in vs.get("warnings", []):
                lines.append(f"⚠️ {w}")
    else:
        lines.append("数据不足：[估值历史序列不可得，建议配置 Tushare Token 获取 daily_basic]")
    pe_extreme = pe_pct is not None and (pe_pct >= 80 or pe_pct <= 20)
    pb_extreme = pb_pct_ext is not None and (pb_pct_ext >= 80 or pb_pct_ext <= 20)
    if pe_extreme:
        zone = "偏高（≥80% 分位）" if pe_pct >= 80 else "偏低（≤20% 分位）"
        lines.append(f"⚠️ PE 处于历史 {zone}，建议触发完整预期差分析（见 D-③）。")
    if pb_extreme:
        zone = "偏高（≥80% 分位）" if pb_pct_ext >= 80 else "偏低（≤20% 分位）"
        lines.append(f"⚠️ PB 处于历史 {zone}，建议结合 D-③ 与资产质量验证预期差。")
    if pe_extreme or pb_extreme:
        lines.append("**[扩展激活 · 估值极端]** 完整预期差分析：① 隐含 g vs 历史 CAGR；② 一致预期（若可得）；③ 增长拐点催化剂。")
    lines.append("")
    d1_pitfall = (
        f"本次 PE 历史分位 {pe_pct:.1f}%、PB {pb_pct_ext:.1f}%，"
        "若把低分位直接等同于「便宜」，可能忽略盈利下修导致的「低 PE 陷阱」。"
        if pe_pct is not None and pb_pct_ext is not None else
        (
            f"本次 PE 历史分位 {pe_pct:.1f}%，需结合 PB 与盈利趋势判断是否为价值陷阱。"
            if pe_pct is not None else
            "本次估值分位不可得，不宜用当前 PE 绝对值替代历史分位判断。"
        )
    )
    lines.append(_law10_hint(
        "历史分位回答「当前估值在自身历史中处于什么位置」——极端分位不直接等于买卖信号，"
        "但意味着市场定价中包含了某种极端预期，需要验证这种预期是否合理。",
        d1_pitfall,
        [
            "PE 与 PB 分位是否一致（CV-3 已在模块 1 落地）",
            "对比行业中位数 PE（见 D-②）",
            "极低分位时检查是否有大额非经常性损益压低 PE",
        ],
    ))
    lines.append("")

    # D-② PE vs 行业中位数
    lines.append("#### D-② PE vs 行业中位数")
    premium: float | None = None
    ind_median: float | None = None
    if current_pe is not None and industry_peers.get("sufficient"):
        peers = industry_peers.get("peers", [])
        peer_pes = [
            float(p.get("pe_ttm")) for p in peers
            if p.get("pe_ttm") is not None and float(p.get("pe_ttm")) > 0
        ]
        if peer_pes:
            from lib.valuation import median_of
            ind_median = median_of([float(x) for x in peer_pes])
            premium = (current_pe - ind_median) / ind_median * 100
            lines.append(f"- 公司 PE(TTM)：**{current_pe:.2f}x**")
            lines.append(f"- 行业中位数 PE：**{ind_median:.2f}x**（{len(peer_pes)} 家同行）")
            lines.append(f"- 溢价/折价：**{premium:+.1f}%**（{'溢价' if premium > 0 else '折价'}）")
            if premium > 30:
                lines.append("公司 PE 显著高于行业，需验证：是否具备远超同行的盈利增长或护城河。")
            elif premium < -30:
                lines.append("公司 PE 显著低于行业，需验证：是否存在未被市场定价的负面因素。")
        else:
            lines.append("数据不足：[同行 PE 数据不可得]")
    elif current_pe is not None:
        lines.append(f"公司 PE(TTM)：**{current_pe:.2f}x**。")
        lines.append("数据不足：[同行数量不足 3 家或行业数据不可得，无法计算行业中位 PE]")
    else:
        lines.append("数据不足：[当前 PE 不可得]")
    lines.append("")
    d2_pitfall = (
        f"本次 PE {current_pe:.2f}x vs 行业中位 {ind_median:.2f}x（溢价 {premium:+.1f}%），"
        "若把溢价直接等同于「高估应回避」，可能忽略龙头合理溢价与成长性差异。"
        if premium is not None and current_pe is not None and ind_median is not None else
        (
            f"本次 PE {current_pe:.2f}x 但缺少 ≥3 家同行中位数，"
            "不宜用绝对 PE 水平判断行业相对贵贱。"
            if current_pe is not None else
            "本次 PE 不可得，不宜用 PB 分位替代行业相对估值判断。"
        )
    )
    lines.append(_law10_hint(
        "行业相对估值回答「市场给公司的定价是否比同行更高」——溢价可能来自"
        "更强的护城河/更高的增长预期，也可能只是市场情绪/流动性的暂时结果。",
        d2_pitfall,
        [
            "结合 A-② 竞争位置判断溢价合理性",
            "对比 ROE 行业中位（高质量公司值得高估值）",
            "观察溢价变化方向：扩大或收窄？",
        ],
    ))
    lines.append("")

    # D-③ LAW 15 隐性预期差
    lines.append("#### D-③ 隐性预期差（LAW 15）")
    ig: dict[str, Any] = {}
    if current_pe is not None and current_pe > 0:
        erp_data = ms.get("erp") or {}
        risk_free_raw = erp_data.get("dgs10")
        y10_source = ""
        if erp_data.get("source") and "+" in str(erp_data.get("source", "")):
            _, y10_source = str(erp_data["source"]).split("+", 1)
        risk_free_is_default = risk_free_raw is None
        if risk_free_is_default:
            risk_free = 0.025
            y10_source = "默认值（FRED/akshare 国债数据不可得）"
        else:
            risk_free = risk_free_raw / 100.0
        from lib.valuation import implied_growth
        ig = implied_growth(current_pe, risk_free, erp=0.06)
        if ig.get("g_implied") is not None:
            lines.append(f"- 当前 PE(TTM)：**{ig['pe']}x**")
            rf_label = (
                f"{ig['risk_free_rate'] * 100:.2f}% [推测，待验证：{y10_source}]"
                if risk_free_is_default else
                f"{ig['risk_free_rate'] * 100:.2f}%（{'FRED.DGS10 +' + y10_source if y10_source else y10_source}）"
            )
            lines.append(f"- 10Y 国债收益率：**{rf_label}**")
            lines.append(f"- ERP 假设：**6%**（保守基准）")
            lines.append(f"- 折现率 r：**{ig['r'] * 100:.2f}%**" if ig.get("r") else "- 折现率：不可得")
            lines.append(f"- **市场隐含增长率 g_implied：约 {ig['g_implied'] * 100:.2f}%**")
            lines.append("")
            cagr_text = f"{cagr:+.2f}%" if cagr is not None else "不可得"
            cagr_years_label = f"{cagr_years_span:.1f}" if cagr_years_span is not None else "?"
            np_cagr_text = f"{np_cagr:+.2f}%" if np_cagr is not None else "不可得"
            np_cagr_years_label = f"{np_cagr_years_span:.1f}" if np_cagr_years_span is not None else "?"
            lines.append(f"- 实际近 {cagr_years_label} 年营收 CAGR：{cagr_text}")
            lines.append(f"- 实际近 {np_cagr_years_label} 年净利润 CAGR：{np_cagr_text}")
            lines.append("- 一致预期：无可靠数据，跳过")
            lines.append("")
            g_implied_pct = ig["g_implied"] * 100
            ref_cagr = cagr if cagr is not None else np_cagr
            ref_label = "营收" if cagr is not None else ("净利润" if np_cagr is not None else None)
            ref_text = cagr_text if cagr is not None else np_cagr_text
            if risk_free_is_default:
                lines.append(
                    "**解读：** 10Y 国债使用默认假设 2.5% [推测，待验证]，"
                    "g_implied 与 CAGR 的方向性对比仅供参考，须先获取真实无风险利率。"
                )
            elif ref_cagr is not None and abs(g_implied_pct - ref_cagr) > 5:
                if g_implied_pct > ref_cagr:
                    lines.append(
                        f"**解读：** 市场隐含增长（{g_implied_pct:.2f}%）> 实际{ref_label} CAGR（{ref_text}），"
                        "市场定价偏乐观，需验证增长加速依据。"
                    )
                else:
                    lines.append(
                        f"**解读：** 市场隐含增长（{g_implied_pct:.2f}%）< 实际{ref_label} CAGR（{ref_text}），"
                        "市场定价偏悲观，可能存在低估（需结合风险评估）。"
                    )
                if cagr is not None and np_cagr is not None and abs(cagr - np_cagr) > 5:
                    lines.append(
                        f"补充：营收 CAGR（{cagr_text}）与净利润 CAGR（{np_cagr_text}）分化较大，"
                        "解读时优先核对利润率变化与非经常性损益。"
                    )
            elif ref_cagr is not None:
                lines.append(
                    f"**解读：** 市场隐含增长 ≈ 实际{ref_label} CAGR，定价基本反映历史增长，关注增长率拐点。"
                )
            else:
                lines.append("**解读：** 缺少实际 CAGR 对比，仅呈现隐含增长率供参考。")
            if ig.get("warning"):
                lines.append(f"\n⚠️ {ig['warning']}")
            if pe_extreme or pb_extreme:
                lines.append("")
                lines.append("**[扩展激活 · 完整预期差]** 估值处于历史极端区间："
                             "请逐项验证 g_implied 假设、盈利增速拐点、以及行业相对估值（D-②）是否一致。")
        else:
            lines.append(f"数据不足：[{ig.get('error', '隐含增长率计算失败')}]")
    else:
        lines.append("数据不足：[PE 非正或不可得，无法计算隐含增长率]")
    lines.append("")
    g_implied = ig.get("g_implied")
    d3_pitfall = (
        f"本次 PE {current_pe:.2f}x → g_implied 约 {g_implied * 100:.2f}%，"
        f"营收 CAGR {cagr:+.2f}%"
        + (f"、净利润 CAGR {np_cagr:+.2f}%" if np_cagr is not None else "")
        + "；若把两者差距直接等同于「高估/低估」，"
        "可能忽略 ERP 假设（6%）与永续增长简化模型的局限。"
        if current_pe and g_implied is not None and cagr is not None else
        (
            f"本次 g_implied 约 {g_implied * 100:.2f}%，但缺少可比 CAGR，"
            "不宜单独用隐含增长率做方向性结论。"
            if current_pe and g_implied is not None else
            "本次 PE 或 g_implied 不可得，戈登反推不适用。"
        )
    )
    lines.append(_law10_hint(
        "g_implied 回答「当前 PE 隐含了多高的永续增长预期」——是预期差分析的定量锚点。",
        d3_pitfall,
        [
            "核对 10Y 国债与 ERP 假设是否匹配当前宏观环境",
            "对比 g_implied 与近 3 年营收/净利润 CAGR、管理层指引增速",
            "PE>50 或国债为默认值时，仅作方向性参考，不作精确估值结论",
        ],
    ))
    lines.append("")

    # =================================================================
    # 同行可比公司表
    # =================================================================
    if industry_peers.get("sufficient"):
        lines.append("### 同行可比公司")
        lines.append("")
        target = industry_peers.get("target") or {}
        rankings = industry_peers.get("rankings") or {}
        lines.append(f"行业：{industry_peers.get('industry_name', '?')}（{len(industry_peers.get('peers', []))} 家同行）")
        lines.append("")
        lines.append("| 公司 | PE(TTM) | PB | ROE(%) | 营收增速(%) |")
        lines.append("|------|---------|-----|--------|------------|")
        # 目标公司行
        target_pe = _fmt_v2(target.get("pe_ttm"), "x") if target.get("pe_ttm") is not None else "-"
        target_pb = _fmt_v2(target.get("pb"), "x") if target.get("pb") is not None else "-"
        target_roe = _fmt_peer_metric(target.get("roe"))
        target_ry = _fmt_peer_metric(target.get("revenue_yoy"), signed=True)
        lines.append(f"| **本公司** | **{target_pe}** | **{target_pb}** | **{target_roe}** | **{target_ry}** |")
        for p in industry_peers.get("peers", [])[:10]:
            p_pe = _fmt_v2(p.get("pe_ttm"), "x") if p.get("pe_ttm") is not None else "-"
            p_pb = _fmt_v2(p.get("pb"), "x") if p.get("pb") is not None else "-"
            p_roe = _fmt_peer_metric(p.get("roe"))
            p_ry = _fmt_peer_metric(p.get("revenue_yoy"), signed=True)
            name = p.get("name", "") or p.get("symbol", "?")
            lines.append(f"| {name} | {p_pe} | {p_pb} | {p_roe} | {p_ry} |")
        lines.append("")
        # 分位排名
        rk_lines = []
        for metric, label in [("pe_ttm", "PE"), ("pb", "PB"), ("roe", "ROE"), ("revenue_yoy", "营收增速")]:
            pct_key = f"{metric}_pct"
            rk_key = f"{metric}_rank"
            tot_key = f"{metric}_total"
            pct_v = rankings.get(pct_key)
            rk_v = rankings.get(rk_key)
            tot_v = rankings.get(tot_key)
            if pct_v is not None:
                rk_lines.append(f"- {label}：分位 **{pct_v}%**（排名 {rk_v}/{tot_v}）")
        if rk_lines:
            lines.append("**分位排名（在同行中的位置）：**")
            lines.extend(rk_lines)
            lines.append("")
        lines.append("> 分位排名越高，表示在同行中数值越高。PE/PB 分位高 = 估值高于多数同行。ROE/营收增速分位高 = 盈利能力或增长优于同行。")

    # =================================================================
    # 12题回答状态表（P0-3 升级）
    # =================================================================
    lines.append("\n### 12题回答状态\n")
    lines.append("| # | 问题 | 状态 | 回答摘要 |")
    lines.append("|----|------|------|---------|")

    # Track question data availability at end of function
    # A-① 行业景气度
    _a1_ok = bool(sw and sw.get("return_20d_pct") is not None)
    _a1_s = f"申万板块近20日{sw['return_20d_pct']:+.2f}%" if _a1_ok else "数据不足"
    lines.append(f"| A-① | 行业景气度 | {'✅' if _a1_ok else '❌'} | {_a1_s} |")

    # A-② 竞争位置
    _a2_ok = bool(latest_fin.get("revenue") is not None and industry_peers.get("sufficient")
                  and industry_peers.get("rankings", {}).get("revenue_yoy_pct") is not None)
    _a2_ry_pct = industry_peers.get("rankings", {}).get("revenue_yoy_pct")
    _a2_s = f"营收增速分位{_a2_ry_pct:.1f}%" if _a2_ok else "数据不足"
    lines.append(f"| A-② | 竞争位置 | {'✅' if _a2_ok else '❌'} | {_a2_s} |")

    # A-③ 毛利率 vs 行业中位数
    _a3_gm = _fin_field_num(latest_fin, "gross_margin", "grossprofit_margin")
    _a3_ok = _a3_gm is not None
    _a3_s = f"毛利率{_a3_gm:.2f}%" if _a3_ok else "数据不足"
    lines.append(f"| A-③ | 毛利率 vs 行业中位数 | {'✅' if _a3_ok else '❌'} | {_a3_s} |")

    # B-① 护城河来源
    _b1_roe = _safe_num(latest_fin.get("roe"))
    _b1_ok = _b1_roe is not None
    _b1_s = f"ROE={_b1_roe:.2f}%" if _b1_ok else "数据不足"
    lines.append(f"| B-① | 护城河来源 | {'✅' if _b1_ok else '❌'} | {_b1_s} |")

    # B-② 增长驱动力
    _b2_ok = rev_cur is not None and rev_prev is not None and rev_prev > 0
    _b2_s = f"营收同比{rev_yoy:+.2f}%" if _b2_ok else "数据不足"
    lines.append(f"| B-② | 增长驱动力 | {'✅' if _b2_ok else '❌'} | {_b2_s} |")

    # B-③ 现金流模式（与核心判断摘要一致：回溯 ocf_val）
    _b3_ok = ocf_val is not None and np_v is not None and np_v > 0
    _b3_r = ocf_val / np_v if _b3_ok else None
    _b3_s = f"OCF/净利={_b3_r:.2f}" if _b3_ok else "数据不足"
    lines.append(f"| B-③ | 现金流模式 | {'✅' if _b3_ok else '❌'} | {_b3_s} |")

    # C-① 近 3 年营收 CAGR
    _c1_ok = len(fin_rev_list) >= 2 and cagr is not None
    _c1_s = f"CAGR={cagr:+.2f}%" if _c1_ok else "数据不足"
    lines.append(f"| C-① | 近3年营收CAGR | {'✅' if _c1_ok else '❌'} | {_c1_s} |")

    # C-② 杜邦拆解 ROE（须净利率+周转+权益乘数）
    _c2_roe = _safe_num(latest_fin.get("roe"))
    _c2_npm = _safe_num(latest_fin.get("netprofit_margin") or latest_fin.get("np_margin"))
    _c2_tat = _safe_num(latest_fin.get("asset_turnover") or latest_fin.get("assets_turn"))
    _c2_em = _safe_num(latest_fin.get("equity_multiplier") or latest_fin.get("em"))
    _c2_ok = (
        _c2_roe is not None and _c2_npm is not None
        and _c2_tat is not None and _c2_em is not None
    )
    _c2_s = (
        f"ROE={_c2_roe:.2f}%，npm×tat×em"
        if _c2_ok else "数据不足"
    )
    lines.append(f"| C-② | 杜邦拆解ROE | {'✅' if _c2_ok else '❌'} | {_c2_s} |")

    # C-③ 现金流覆盖 + 应收/存货交叉验证
    _c3_ar = _safe_num(latest_fin.get("accounts_receiv") or latest_fin.get("ar"))
    _c3_inv = _safe_num(latest_fin.get("inventory") or latest_fin.get("inventories"))
    _c3_ok = (
        ocf_val is not None and np_v is not None and np_v > 0
        and _c3_ar is not None and _c3_inv is not None
    )
    _c3_s = f"OCF/净利={_b3_r:.2f}，含应收+存货" if _c3_ok else "数据不足"
    lines.append(f"| C-③ | 现金流覆盖+应收/存货 | {'✅' if _c3_ok else '❌'} | {_c3_s} |")

    # C-④ 扣非/净利润
    _c4_pd = _safe_num(latest_fin.get("profit_dedt"))
    _c4_ok = _c4_pd is not None and np_v is not None and np_v > 0
    _c4_r = _c4_pd / np_v if _c4_ok else None
    _c4_s = f"扣非/净利={_c4_r:.2f}" if _c4_ok else "数据不足"
    lines.append(f"| C-④ | 扣非/净利润 | {'✅' if _c4_ok else '❌'} | {_c4_s} |")

    # D-① PE/PB 历史位置（须含历史位置百分比与中位数）
    _d1_pe_median = _historical_pe_median(val_cache, dims)
    _d1_ok = bool(pe_seq) and current_pe is not None and pe_pct is not None
    if _d1_ok and _d1_pe_median is not None:
        _d1_s = f"PE={current_pe:.2f}x，历史位置{pe_pct:.1f}%（中位数 {_d1_pe_median:.2f}x）"
    elif _d1_ok:
        _d1_s = f"PE={current_pe:.2f}x，历史位置{pe_pct:.1f}%"
    else:
        _d1_s = "数据不足"
    lines.append(f"| D-① | PE/PB历史分位 | {'✅' if _d1_ok else '❌'} | {_d1_s} |")

    # D-② PE vs 行业中位数
    _d2_ok = current_pe is not None and industry_peers.get("sufficient")
    _d2_s = f"PE={current_pe:.2f}x" if _d2_ok else "数据不足"
    lines.append(f"| D-② | PE vs行业中位数 | {'✅' if _d2_ok else '❌'} | {_d2_s} |")

    # D-③ 隐性预期差
    _d3_implied = None
    try:
        _d3_implied = ig.get("g_implied")  # type: ignore[union-attr]
    except (NameError, AttributeError):
        pass
    _d3_ok = current_pe is not None and current_pe > 0 and _d3_implied is not None
    _d3_s = f"g_implied={_d3_implied * 100:.2f}%" if _d3_ok else "数据不足"
    lines.append(f"| D-③ | 隐性预期差(LAW15) | {'✅' if _d3_ok else '❌'} | {_d3_s} |")

    lines.append("")
    lines.append("> ✅ = 有可用数据，❌ = 数据不足。状态反映数据完整性，不反映结论正误。")

    lines.append("")
    lines.append("🔍 **待独立验证:** 基本面分析基于第三方数据源（Tushare/akshare），应逐项与公司年报/季报原始数据交叉核对。行业分类可能因数据源口径不同存在差异。估值分位/隐含增长率不构成买卖判断。")
    return "\n".join(lines)


# --- _law10_hint ---
def _law10_hint(why: str, pitfall: str, next_steps: list[str]) -> str:
    """LAW 10 分析提示块（每题末尾固定格式）。"""
    lines = [
        "> [分析提示]",
        f"> - **为什么重要：** {why}",
        f"> - **常见分析误区：** {pitfall}",
    ]
    for i, step in enumerate(next_steps, 1):
        lines.append(f"> - **下一步交叉验证 {i}：** {step}")
    return "\n".join(lines)


# --- _section_static_fundamentals ---
def _section_static_fundamentals(
    dims: dict[str, dict], collection: dict, *, val_cache: dict | None = None,
) -> str:
    # 委托给 Phase 2 分层基本面
    symbol = collection.get("symbol", "")
    return _section_fundamentals_layered(dims, collection, symbol, val_cache=val_cache)


# --- _section_technical_brief ---
def _section_technical_brief(
    dims: dict[str, dict], *, val_cache: dict | None = None,
) -> str:
    lines = ["## 8. 附录", ""]
    pe_table = _pe_band_markdown_table(dims, val_cache)
    if pe_table:
        lines.extend([pe_table, ""])
    lines.extend(["### 技术分析精简", ""])
    kline = _get_dim_data(dims, "kline")
    if not kline or not isinstance(kline, list):
        lines.append("- 趋势：K 线不可得")
        lines.append("- 量：—")
        lines.append("- 支撑阻力：—")
        return "\n".join(lines)
    tech = compute(sort_kline_asc(kline))
    if "error" in tech:
        lines.append(f"- 趋势：{tech.get('message', '计算失败')}")
        lines.append("- 量：—")
        lines.append("- 支撑阻力：—")
        return "\n".join(lines)
    trend = tech["trend"]["alignment"].get("trend_label", "—")
    sentences = tech["trend"].get("summary_sentences", [])
    vol_s = sentences[1] if len(sentences) > 1 else "量价关系见完整 K 线"
    sup = tech.get("support_resistance", {})
    sr = sup.get("summary", "—") if isinstance(sup, dict) else "—"
    lines.append(f"- **趋势:** {trend}")
    lines.append(f"- **量:** {vol_s}")
    lines.append(f"- **支撑阻力:** {sr}")
    return "\n".join(lines)


# --- _report_toc ---
def _report_toc() -> str:
    entries = [
        ("研究问题卡", "0-研究问题卡"),
        ("当前状态快照", "1-当前状态快照"),
        ("动态驱动", "2-动态驱动分析"),
        ("市场结构", "3-市场结构分析"),
        ("静态基本面（12题）", "4-静态基本面分析"),
        ("Bull/Bear 情景", "5-市场分歧"),
        ("左/右概率", "6-左侧右侧概率判断"),
        ("风险与不确定性", "7-风险与不确定性"),
        ("技术简报", "8-附录"),
        ("PE Band（5年轨道）", "pe-band5年轨道"),
        ("引用来源", "引用来源references"),
    ]
    lines = ["## 目录", ""]
    lines.extend(f"- [{label}](#{anchor})" for label, anchor in entries)
    return "\n".join(lines)


# --- _pe_band_markdown_table ---
def _pe_band_markdown_table(
    dims: dict[str, dict], val_cache: dict | None = None,
) -> str:
    if val_cache is not None and "pe_band" in val_cache:
        band = val_cache["pe_band"]
    else:
        val_data = _get_dim_data(dims, "valuation")
        if not val_data or not isinstance(val_data, list):
            return ""
        from lib.valuation import pe_band_series
        band = pe_band_series(val_data)
        if val_cache is not None:
            val_cache["pe_band"] = band
    if not band.get("n_samples"):
        return ""
    years = band.get("years", 5)

    def _cell(v: Any) -> str:
        return str(v) if v is not None else "—"

    lines = [
        f"### PE Band（{years}年轨道）",
        "",
        "| 指标 | 数值 |",
        "|------|------|",
        f"| 样本数 | {_cell(band.get('n_samples'))} |",
        f"| 均值 (μ) | {_cell(band.get('mean'))} |",
        f"| +1σ | {_cell(band.get('upper_1σ'))} |",
        f"| -1σ | {_cell(band.get('lower_1σ'))} |",
        f"| +2σ | {_cell(band.get('upper_2σ'))} |",
        f"| -2σ | {_cell(band.get('lower_2σ'))} |",
        f"| 当前 PE | {_cell(band.get('current_pe'))} |",
        f"| 当前位置 | {_cell(band.get('current_position'))} |",
    ]
    return "\n".join(lines)


# --- _classify_sellside_rating ---
def _classify_sellside_rating(rating: str) -> str:
    """卖方评级归类（LAW 6：输出侧避免「买入」「目标价」字面）。"""
    s = str(rating)
    if "卖" in s or "减持" in s:
        return "看空"
    if "中性" in s:
        return "中性"
    if "增持" in s or "持有" in s:
        return "温和看多"
    if "买" in s:
        return "偏多"
    return "其他"


# --- _section_research_summary ---
def _section_research_summary(
    collection: dict[str, Any], symbol: str, dims: dict,
) -> str:
    """机构研报与盈利预测展示段。

    数据来自 collect_research() → dims["research"] → research_summary。
    三层权限降级展示：
      1️⃣ 有评级+卖方预期价位（Tushare 10000+积分 / report_rc）
      2️⃣ 仅业绩预告（Tushare 2000+积分 / forecast）
      3️⃣ 全部不可得 → 无展示
    """
    # collection, symbol unused in v2 legacy; kept for signature consistency with v3 sections
    research_dim = dims.get("research", {})
    summary = research_dim.get("research_summary") or {}
    status = summary.get("status", "no_data")

    if status == "no_data":
        return ""

    lines: list[str] = []
    body: list[str] = []

    if status == "ok":
        ratings = summary.get("latest_ratings") or []
        if ratings:
            buckets: dict[str, int] = {}
            for r in ratings:
                label = _classify_sellside_rating(r.get("rating", ""))
                buckets[label] = buckets.get(label, 0) + 1
            parts = [f"{k} {v}" for k, v in buckets.items() if v]
            body.append(
                f"- **机构覆盖:** 近半年 {len(ratings)} 条评级（{' / '.join(parts)}）"
            )

        tp = summary.get("target_price_range")
        if tp:
            upper_note = ""
            if tp.get("avg_upper") is not None:
                upper_note = f"（卖方上限均值 {tp['avg_upper']} 元）"
            body.append(
                f"- **卖方预期价位:** {tp['min']} – {tp['max']} 元{upper_note}"
            )

        eps_forecasts = summary.get("eps_forecasts", [])
        if eps_forecasts:
            eps_rows = " | ".join(
                f"{e['quarter']}: {e['avg_eps']}（{e['n_analysts']}家）"
                for e in eps_forecasts[:4]
            )
            body.append(f"- **EPS预测（均值）:** {eps_rows}")

        if not body:
            return ""

    elif status == "ok_guidance_only" and summary.get("company_guidance"):
        g = summary["company_guidance"]
        pct_min = g.get("pct_change_min")
        pct_max = g.get("pct_change_max")
        profit_min = g.get("profit_min_100m")
        profit_max = g.get("profit_max_100m")
        guide_type = g.get("type", "")

        body.append(f"- **公司业绩预告:** {guide_type}")
        _pct_min = f"{pct_min}" if pct_min is not None else "?"
        _pct_max = f"{pct_max}" if pct_max is not None else "?"
        if profit_min is not None:
            body.append(
                f"  - 预计归母净利 **{profit_min}–{profit_max} 亿元**"
                f"（同比 {_pct_min}%–{_pct_max}%）"
            )
        else:
            body.append(
                f"  - 同比变动 {_pct_min}%–{_pct_max}%（利润率变动未披露）"
            )

    elif status == "ok_limited":
        body.append(f"- {summary.get('summary_text', '东方财富研报记录（无结构化评级摘要）')}")

    else:
        return ""

    lines.append("## 机构观点与盈利预测\n")
    lines.extend(body)

    # Template C: SentimentCard note
    sentiment_card = _get_analysis_cards(collection).get("sentiment")
    if sentiment_card and isinstance(sentiment_card, dict):
        eps_mean = sentiment_card.get("eps_forecast_mean")
        eps_high = sentiment_card.get("eps_forecast_high")
        eps_low = sentiment_card.get("eps_forecast_low")
        eps_count = sentiment_card.get("eps_forecast_count", 0)
        if eps_mean is not None:
            eps_range = ""
            if eps_low is not None and eps_high is not None:
                eps_range = f", range [{eps_low}-{eps_high}]"
            lines.append(
                f"\n> **研报情绪:** EPS一致预期 {eps_mean} (n={eps_count}){eps_range}"
            )
        slot_text = sentiment_card.get("sentiment_slot", "")
        if slot_text:
            lines.append(f"> *{slot_text}*")

    from datetime import datetime
    source_label = {
        "ok": "Tushare report_rc（10000+积分/特色大数据）",
        "ok_guidance_only": "Tushare forecast（2000+积分）",
        "ok_limited": "akshare（东方财富研报摘要，免注册）",
    }.get(status, "")
    if source_label:
        lines.append(
            f"\n> **数据来源:** {source_label} | 获取日期: {datetime.now().strftime('%Y-%m-%d')}"
        )

    lines.append(
        "\n🔍 **待独立验证:** 机构评级存在利益冲突，卖方预期价位不代表股价必然到达。"
        "业绩预告为公司单方披露，未经审计。"
    )
    return "\n".join(lines)


# --- _section_core_tension ---
def _section_core_tension(
    collection: dict,
    symbol: str,
    dims: dict[str, dict],
    market_structure: dict,
    *,
    val_cache: dict | None = None,
) -> str:
    """模块 4–5 之间的核心矛盾小结（P2a，数据驱动填空）。"""
    pe_pct, _, pe_zone = _v3_valuation_percentiles(dims, val_cache)
    ig, cagr, np_cagr = _v3_bull_bear_implied_growth(dims, market_structure)
    ref_cagr = cagr if cagr is not None else np_cagr
    ref_label = "营收" if cagr is not None else ("净利润" if np_cagr is not None else None)
    variables: list[str] = []
    if pe_pct is not None:
        variables.append(
            f"估值历史区间位置（当前 {pe_pct:.1f}%，{pe_zone or '—'}）能否维持"
        )
    if ig.get("g_implied") is not None and ref_cagr is not None and ref_label:
        g_pct = ig["g_implied"] * 100
        variables.append(
            f"隐含增长 g_implied {g_pct:.1f}% 与实际{ref_label} CAGR {ref_cagr:+.1f}% 的缺口"
        )
    sw = market_structure.get("sw_index") or {}
    if sw.get("stock_vs_industry_pct") is not None:
        variables.append(
            f"个股相对行业超额 {sw['stock_vs_industry_pct']:+.2f}% 的可持续性"
        )
    if len(variables) < 2:
        return ""
    name = collection.get("name") or symbol
    lines = [
        f"> **核心矛盾小结** — 围绕 {name}（{symbol}）当前市场分歧，实质上集中在：",
    ]
    for i, var in enumerate(variables[:3], 1):
        lines.append(f"> {i}. {var}；")
    lines.append(
        "> 其他估值、资金和情绪的争议，本质上都在围绕上述变量摇摆。"
    )
    lines.append("")
    return "\n".join(lines)


# --- ReportEnhancer ---
class ReportEnhancer:
    """Report 阶段增强触发器统一管理。

    所有增强逻辑通过 register / apply 机制调用，
    避免在 render_report_v3() 中散落 if-else。
    """

    def __init__(self, data: dict):
        self.data = data
        self._enhancers: list[tuple[str, callable, callable]] = []

    def register(self, name: str, condition, enhancer_fn):
        """注册增强器：条件满足时自动调用。"""
        self._enhancers.append((name, condition, enhancer_fn))

    def apply(self) -> dict:
        """执行所有满足条件的增强器，返回增强结果。"""
        results = {}
        for name, condition, fn in self._enhancers:
            try:
                if condition(self.data):
                    results[name] = fn(self.data)
            except Exception as e:
                results[name] = {"error": str(e)}
        return results


# --- _has_price_signal ---
def _has_price_signal(data: dict) -> bool:
    """检查是否触发涨价信号。"""
    ip = data.get("industry_pricing")
    if not isinstance(ip, dict):
        return False
    for src in ip.get("_meta", {}).get("all_sources", []):
        if not isinstance(src, dict):
            continue
        nd = src.get("data")
        if isinstance(nd, dict) and nd.get("signal") == "确认":
            return True
    return False


# --- _is_valuation_extreme ---
def _is_valuation_extreme(data: dict, percentile: float = 80) -> bool:
    """检查估值分位是否超过阈值（从 dimensions 读取，与报告其他模块一致）。"""
    dims = _index_dims(data)
    pe_pct, _, _ = _v3_valuation_percentiles(dims, {})
    return pe_pct is not None and pe_pct >= percentile


# --- setup_default_enhancers ---
def setup_default_enhancers(data: dict) -> ReportEnhancer:
    """配置默认增强器集合。"""
    enhancer = ReportEnhancer(data)

    enhancer.register(
        "price_shock_websearch",
        _has_price_signal,
        lambda d: {"triggered": True, "reason": "涨价信号确认，建议 WebSearch 深搜"},
    )

    enhancer.register(
        "valuation_high_alert",
        lambda d: _is_valuation_extreme(d, percentile=80),
        lambda d: {"triggered": True, "reason": "PE 历史位置≥80%，建议 B 类增强"},
    )

    enhancer.register(
        "price_shock_detect",
        lambda d: bool((d.get("price_shock") or {}).get("has_shock")),
        lambda d: d.get("price_shock"),
    )

    return enhancer


# --- _render_extras_block (shared by brief & full paths) ---
def _render_extras_block(collection: dict, *, strict: bool) -> list[str]:
    """Collect rigor warnings + exogenous shock + AH detection for report body."""
    try:
        from .render_extras import render_rigor_warnings, section_exogenous_shock, render_ah_detection_note
    except ImportError:
        return []
    parts: list[str] = []
    for text in (render_rigor_warnings(collection, strict=strict),
                 section_exogenous_shock(collection),
                 render_ah_detection_note(collection)):
        if text and text.strip():
            parts.append(text)
    return parts


# --- concise helpers (v0.2.0: Hermes/OpenClaw 对话场景) ---
def _concise_positioning(collection, symbol, dims, val_cache=None):
    """定位句：symbol + name + industry + PE 历史位置 + 定性。"""
    basic = dims.get("basic_info", {}).get("data", {})
    name = ""
    industry = ""
    if isinstance(basic, dict):
        name = basic.get("name", "") or basic.get("股票简称", "")
        industry = basic.get("industry", "")

    pe_pct, pb_pct, pe_zone = _v3_valuation_percentiles(dims, val_cache)
    summary = _v3_load_valuation_summary(dims, val_cache)
    pe_median = (summary.get("pe") or {}).get("median") if summary else None
    pe_current = (summary.get("pe") or {}).get("latest") if summary else None

    name_str = f"{symbol} {name}".strip()
    industry_str = f"（{industry}）" if industry else ""

    if pe_current is not None and pe_pct is not None:
        median_part = f"中位数 {pe_median:.2f}x" if pe_median is not None else ""
        position = f"PE {pe_current:.2f}x，历史位置 {pe_pct:.1f}%（{median_part}）"
    elif pe_pct is not None:
        median_part = f"（中位数 {pe_median:.2f}x）" if pe_median is not None else ""
        position = f"PE 历史位置 {pe_pct:.1f}%{median_part}"
    else:
        position = "PE 数据不可得"

    qualitative = ""
    if pe_pct is not None and pe_zone:
        qualitative_map = {"偏贵区": "估值偏高", "合理区": "估值合理", "偏低区": "估值偏低"}
        qualitative = f" — {qualitative_map.get(pe_zone, '')}"
    elif pe_pct is not None:
        if pe_pct >= 80:
            qualitative = " — 估值偏高"
        elif pe_pct <= 20:
            qualitative = " — 估值偏低"

    return f"**{name_str}**{industry_str} — {position}{qualitative}"


def _concise_contradictions(collection, dims, val_cache=None):
    """核心矛盾 1-2 条，复用 _executive_core_contradictions。"""
    items = _executive_core_contradictions(collection, dims, val_cache)
    if not items:
        return "**核心矛盾**：数据不足，无法判断。"
    lines = ["**核心矛盾**："]
    for item in items:
        lines.append(f"- {item}")
    return "\n".join(lines)


def _concise_bull(collection, symbol, dims, market_structure, val_cache=None):
    """Bull Case 1 段：关键假设 + 支撑数值。"""
    pe_pct, pb_pct, pe_zone = _v3_valuation_percentiles(dims, val_cache)
    fin = _get_dim_data(dims, "financials")
    roe = None
    if fin and isinstance(fin, list):
        latest = sort_kline_asc(fin)[-1]
        roe = latest.get("roe")

    summary = _v3_load_valuation_summary(dims, val_cache)
    pe_latest = (summary.get("pe") or {}).get("latest") if summary else None
    pe_median = (summary.get("pe") or {}).get("median") if summary else None
    eps_cagr = (summary.get("earnings") or {}).get("cagr_3y") if summary else None

    points = []
    if pe_pct is not None and pe_pct <= 30:
        median_part = f" vs 中位数 {pe_median:.2f}x" if pe_median is not None else ""
        points.append(f"PE 处于历史偏低位置（{pe_pct:.1f}% 分位{median_part}），存在均值回归空间")
    if roe is not None and float(roe) >= 12:
        points.append(f"ROE {float(roe):.1f}%，盈利质量支撑估值修复")
    if eps_cagr is not None and eps_cagr > 0:
        points.append(f"近 3 年 EPS CAGR {eps_cagr:+.1f}%，盈利趋势向好")
    if pe_latest is not None and pe_pct is not None and pe_pct <= 30:
        sw = market_structure.get("sw_index") or {}
        svi = sw.get("stock_vs_industry_pct")
        if svi is not None:
            points.append(f"个股相对行业指数 {svi:+.1f}%")

    if not points:
        ms = collection.get("market_structure") or {}
        nb = ms.get("northbound") or {}
        net10 = nb.get("net_sum_10d")
        if net10 is not None and float(net10) > 0:
            points.append(f"北向近 10 日净流入 {float(net10):+.0f}，资金面偏向积极")
        if not points:
            points.append("当前缺乏明确的 Bull Case 数据支撑 [推测，待验证]")

    return "**Bull Case 主导逻辑**：\n" + "\n".join(f"- {p}" for p in points)


def _concise_bear(collection, symbol, dims, market_structure, risk_data, val_cache=None):
    """Bear Case 1 段：主要风险 + 触发条件。"""
    pe_pct, pb_pct, pe_zone = _v3_valuation_percentiles(dims, val_cache)
    fin = _get_dim_data(dims, "financials")
    ocf_divergence = False
    gross_margin_declining = False

    if fin and isinstance(fin, list):
        fin_sorted = sort_kline_asc(fin)
        latest = fin_sorted[-1]
        np_v = latest.get("net_profit")
        ocf = latest.get("ocf") if latest.get("ocf") is not None else latest.get("n_cashflow_act")
        if np_v is not None and ocf is not None:
            try:
                if float(np_v) > 0 and float(ocf) / float(np_v) < 0.6:
                    ocf_divergence = True
            except (TypeError, ValueError, ZeroDivisionError):
                pass
        # 毛利率趋势
        if len(fin_sorted) >= 2:
            gm_curr = latest.get("gross_profit_margin") if latest.get("gross_profit_margin") is not None else latest.get("grossprofit_margin")
            gm_prev = fin_sorted[-2].get("gross_profit_margin") if fin_sorted[-2].get("gross_profit_margin") is not None else fin_sorted[-2].get("grossprofit_margin")
            if gm_curr is not None and gm_prev is not None:
                try:
                    if float(gm_curr) < float(gm_prev) - 1:
                        gross_margin_declining = True
                except (TypeError, ValueError):
                    pass

    points = []
    if pe_pct is not None and pe_pct >= 70:
        summary = _v3_load_valuation_summary(dims, val_cache)
        pe_median = (summary.get("pe") or {}).get("median") if summary else None
        if pe_median is not None:
            points.append(f"PE 处于历史偏高位置（{pe_pct:.1f}% 分位 vs 中位数 {pe_median:.2f}x），存在估值收缩风险")
        else:
            points.append(f"PE 处于历史偏高位置（{pe_pct:.1f}% 分位），存在估值收缩风险")

    if ocf_divergence:
        points.append("经营现金流/净利润 < 0.6，利润质量需关注")

    if gross_margin_declining:
        points.append("毛利率连续下滑，竞争压力或成本上升")

    # 从 risk_data 提取关键风险信号
    for sig in (risk_data.get("signals") or [])[:3]:
        if sig.get("triggered") and sig.get("severity") in ("高", "中"):
            detail = sig.get("detail", "")
            if detail and detail not in points:
                points.append(detail)

    if not points:
        ms = collection.get("market_structure") or {}
        nb = ms.get("northbound") or {}
        net10 = nb.get("net_sum_10d")
        if net10 is not None and float(net10) < 0:
            points.append(f"北向近 10 日净流出 {float(net10):+.0f}，资金面偏谨慎")
        if not points:
            points.append("当前缺乏明确的 Bear Case 触发信号 [推测，待验证]")

    return "**Bear Case 主要风险**：\n" + "\n".join(f"- {p}" for p in points)


def _concise_catalyst(collection, dims):
    """催化剂与观察节点（可选），浓缩 _section_events_timeline 关键事件。"""
    events = collection.get("events")
    if not events:
        return ""
    if isinstance(events, dict):
        timeline = events.get("timeline") or events.get("items") or []
    elif isinstance(events, list):
        timeline = events
    else:
        return ""

    if not timeline:
        return ""

    key_events = []
    for ev in timeline[:5]:
        if isinstance(ev, dict):
            date = ev.get("date") or ev.get("event_date") or ""
            title = ev.get("title") or ev.get("event") or ev.get("summary", "")
            if title:
                key_events.append(f"- {date} {title}" if date else f"- {title}")

    if not key_events:
        return ""

    return "**催化剂与观察节点**：\n" + "\n".join(key_events)


def _concise_financial_snapshot(dims, val_cache=None):
    """财务速览表（ROE/EPS/毛利率/OCF 比率，4-6 行）。"""
    fin = _get_dim_data(dims, "financials")
    if not fin or not isinstance(fin, list):
        return ""

    fin_sorted = sort_kline_asc(fin)
    latest = fin_sorted[-1]
    end_date = latest.get("end_date", "?")
    roe = latest.get("roe")
    eps = latest.get("eps")
    gross_margin = latest.get("gross_profit_margin") if latest.get("gross_profit_margin") is not None else latest.get("grossprofit_margin")
    np_v = latest.get("net_profit")
    ocf = latest.get("ocf") or latest.get("n_cashflow_act")

    lines = [
        f"| 指标 | 报告期 {end_date} |",
        "|------|------|",
    ]
    if roe is not None:
        lines.append(f"| ROE | {float(roe):.2f}% |")
    if eps is not None:
        lines.append(f"| EPS | {float(eps):.4f} |")
    if gross_margin is not None:
        lines.append(f"| 毛利率 | {float(gross_margin):.2f}% |")
    if np_v is not None and ocf is not None:
        try:
            ratio = float(ocf) / float(np_v) if float(np_v) != 0 else None
            if ratio is not None:
                lines.append(f"| OCF/净利润 | {ratio:.2f} |")
        except (TypeError, ValueError, ZeroDivisionError):
            pass

    if len(lines) <= 2:
        return ""
    return "\n".join(lines)


def _concise_valuation_snapshot(dims, val_cache=None):
    """估值位置表（PE/PB/PS + 分位 + 中位数）。"""
    summary = _v3_load_valuation_summary(dims, val_cache)
    if not summary:
        return ""

    pe_pct, pb_pct, _ = _v3_valuation_percentiles(dims, val_cache)

    lines = [
        "| 指标 | 当前值 | 历史分位 | 中位数 |",
        "|------|-------|---------|-------|",
    ]
    pe = summary.get("pe") or {}
    if pe.get("latest") is not None and pe_pct is not None:
        lines.append(
            f"| PE | {pe['latest']:.2f}x | {pe_pct:.1f}% | "
            f"{pe['median']:.2f}x |" if pe.get("median") is not None
            else f"| PE | {pe['latest']:.2f}x | {pe_pct:.1f}% | — |"
        )

    pb = summary.get("pb") or {}
    if pb.get("latest") is not None and pb_pct is not None:
        lines.append(
            f"| PB | {pb['latest']:.2f}x | {pb_pct:.1f}% | "
            f"{pb['median']:.2f}x |" if pb.get("median") is not None
            else f"| PB | {pb['latest']:.2f}x | {pb_pct:.1f}% | — |"
        )

    ps = summary.get("ps") or {}
    if ps.get("latest") is not None:
        lines.append(
            f"| PS | {ps['latest']:.2f}x | — | "
            f"{ps['median']:.2f}x |" if ps.get("median") is not None
            else f"| PS | {ps['latest']:.2f}x | — | — |"
        )

    if len(lines) <= 2:
        return ""
    return "\n".join(lines)


def _concise_capital_flow(dims, collection):
    """资金行为摘要（北向、股东户数、内部人交易）。"""
    points = []
    market_structure = collection.get("market_structure") or {}
    nb = market_structure.get("northbound") or {}
    net10 = nb.get("net_sum_10d")
    if net10 is not None:
        try:
            direction = "净流入" if float(net10) > 0 else ("持平" if float(net10) == 0 else "净流出")
            points.append(f"- 北向近 10 日{direction} {abs(float(net10)):.0f}")
        except (TypeError, ValueError):
            pass

    holder = dims.get("holder_changes", {}).get("data")
    if isinstance(holder, dict):
        holder_change = holder.get("change_pct") or holder.get("change")
        if holder_change is not None:
            try:
                chg = float(holder_change)
                direction = "增加" if chg > 0 else "减少" if chg < 0 else "持平"
                points.append(f"- 股东户数{direction} {abs(chg):.1f}%")
            except (TypeError, ValueError):
                pass

    events = collection.get("events")
    insider = ""
    if isinstance(events, dict):
        insider = events.get("insider_signal", "") or events.get("insider_trading", "")
    if insider:
        points.append(f"- 内部人信号: {insider}")

    if not points:
        return ""
    return "\n".join(points)


# --- render_report_v3 ---
def render_report_v3(collection: dict[str, Any], symbol: str, mode: str = "full") -> str:
    """v0.2.0 九模块研究备忘录。mode="brief" 仅输出精简简报, mode="concise" 输出对话场景精简。"""
    dims = _index_dims(collection)
    market_structure = collection.get("market_structure") or {}

    # P3-1: 统一增强触发器
    enhancer = setup_default_enhancers(collection)
    collection["_enhancements"] = enhancer.apply()

    val_cache: dict = {}
    risk_data = _v3_build_risk_report(
        collection, dims, market_structure, val_cache=val_cache,
    )
    strict = bool((collection.get("_meta") or {}).get("strict_rigor"))

    if mode == "brief":
        parts: list[str] = [
            _header_v2(collection, symbol),
        ]
        extras = _render_engine_extras(collection)
        if extras:
            parts.append("\n".join(extras))
        _extras = _render_extras_block(collection, strict=strict)
        if _extras:
            parts.append("\n\n".join(_extras))
        parts.extend([
            _section_executive_summary(collection, symbol, dims, val_cache=val_cache),
            _section_research_question(collection, symbol, val_cache=val_cache),
            _section_snapshot(collection, symbol, dims, val_cache=val_cache),
            _section_dynamic_drivers(
                collection, symbol, dims, market_structure, val_cache=val_cache,
            ),
            _section_holder_changes(dims.get("holder_changes", {}), collection.get("events")),
            _section_bull_bear(
                collection, symbol, dims, market_structure, risk_data, val_cache=val_cache,
            ),
            _wrap_details(
                "展开：风险与不确定性",
                _section_risk_uncertainty(collection, symbol, dims, market_structure, risk_data),
            ),
            _references_appendix(collection),
            _risk_footer(),
        ])
    elif mode == "concise":
        # === Hermes/OpenClaw 对话场景精简模式 ===
        # 结论速览（3-5 段）+ 关键数据展开块（<details>）
        parts: list[str] = [
            _header_v2(collection, symbol),
        ]
        extras = _render_engine_extras(collection)
        if extras:
            parts.append("\n".join(extras))
        _extras = _render_extras_block(collection, strict=strict)
        if _extras:
            parts.append("\n\n".join(_extras))
        parts.extend([
            _concise_positioning(collection, symbol, dims, val_cache=val_cache),
            _concise_contradictions(collection, dims, val_cache=val_cache),
            _concise_bull(collection, symbol, dims, market_structure, val_cache=val_cache),
            _concise_bear(collection, symbol, dims, market_structure, risk_data, val_cache=val_cache),
        ])
        # 可选第 5 段：催化剂
        catalyst = _concise_catalyst(collection, dims)
        if catalyst:
            parts.append(catalyst)
        # 关键数据展开块
        fin_block = _concise_financial_snapshot(dims, val_cache)
        if fin_block:
            parts.append(_wrap_details("展开：财务速览", fin_block))
        val_block = _concise_valuation_snapshot(dims, val_cache)
        if val_block:
            parts.append(_wrap_details("展开：估值位置", val_block))
        cap_block = _concise_capital_flow(dims, collection)
        if cap_block:
            parts.append(_wrap_details("展开：资金行为", cap_block))
        parts.append(_wrap_details("展开：参考资料", _references_appendix(collection)))
        parts.append(_risk_footer())
    else:
        # F-3: 快速否决检测需在 D 段之前算出，供 veto_triggered 联动 + 展示触发条目
        _fast_veto = _check_fast_veto(dims, collection)
        parts: list[str] = [
            _header_v2(collection, symbol),
        ]
        extras = _render_engine_extras(collection)
        if extras:
            parts.append("\n".join(extras))
        _extras = _render_extras_block(collection, strict=strict)
        if _extras:
            parts.append("\n\n".join(_extras))
        parts.extend([
            _report_toc(),
            _section_research_question(collection, symbol, val_cache=val_cache),
            _section_snapshot(collection, symbol, dims, val_cache=val_cache),
            _section_dynamic_drivers(
                collection, symbol, dims, market_structure, val_cache=val_cache,
            ),
            _section_market_structure(
                collection, symbol, market_structure, val_cache=val_cache,
            ),
            _section_participant_behavior_scan(
                collection, symbol, market_structure, dims,
            ),
            _section_events_timeline(collection),
            _section_holder_changes(dims.get("holder_changes", {}), collection.get("events")),
            _section_research_summary(collection, symbol, dims),
            _wrap_details(
                "展开：静态基本面（12题）",
                _section_static_fundamentals(dims, collection, val_cache=val_cache),
            ),
            "\n".join(
                ["### 快速否决检测（F-3）", ""] + _fast_veto["display_lines"]
            ) if _fast_veto["display_lines"] else "",
            _section_dcf_valuation(
                dims, collection, symbol, veto_triggered=bool(_fast_veto["hard_triggers"]),
            ),
            _section_core_tension(
                collection, symbol, dims, market_structure, val_cache=val_cache,
            ),
            _section_bull_bear(
                collection, symbol, dims, market_structure, risk_data, val_cache=val_cache,
            ),
            _section_left_right_probability(
                collection, symbol, dims, market_structure, val_cache=val_cache,
            ),
            _wrap_details(
                "展开：风险与不确定性",
                _section_risk_uncertainty(collection, symbol, dims, market_structure, risk_data),
            ),
            _section_technical_brief(dims, val_cache=val_cache),
            _section_six_gates_scorecard(dims, collection, val_cache),
            _references_appendix(collection),
            _risk_footer(),
        ])
    return "\n\n".join(p for p in parts if p)

