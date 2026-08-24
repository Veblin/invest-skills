"""Markdown report rendering (v2/v3) and main render() entry."""
from __future__ import annotations

import logging
import math
from typing import Any, Callable

from lib.nums import coalesce_field, fmt_amount, safe_float as _safe_num
from lib.technical import compute, sort_kline_asc
from lib.participant_scan import (
    build_participant_behavior_section,
    moneyflow_cv_window,
    moneyflow_signal_label,
    northbound_label,
    resolve_moneyflow,
)

from ..schema import DriverFactor

from .. import render_utils as _ru
from ..financials import (  # C5 v0.2.7: 语义常量（毛利率字段优先级 / OCF 覆盖比阈值）
    GROSS_MARGIN_FIELDS,
    OCF_COVERAGE_ALERT,
    OCF_COVERAGE_EXCELLENT,
    OCF_COVERAGE_GOOD,
    OCF_COVERAGE_WEAK,
)
from ..valuation import EXTREME_HIGH_THRESHOLD, EXTREME_LOW_THRESHOLD  # C5 v0.2.7
from ..shared_dates import fmt_fetched_at  # P2-2 v0.2.7: 采集时间 UTC→北京时间
from ..render_utils import (
    sanitize_error,
    _sanitize_error,
    _index_dims,
    _get_dim_data,
    _get_dim_meta,
    _get_analysis_cards,
    _missing_section,
    _references_appendix,
    _risk_footer,
    _cv,
    _fmt_v2,
    _fmt_num,
    _fmt_end_date,
    _get_safe,
    _coalesce_fin_field,
    _coalesce_gross_margin,
    _fin_field_num,
    _wrap_details,
    _compute_metric_cagr,
    cagr_period_rows,
    _historical_pe_median,
    _evidence_conclusion_block,
    _v3_cv7_block,
    _v3_price_change,
    _v3_price_window_label,
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

    lines.extend(_render_income_driver(collection))
    lines.extend(_render_style_match(collection))
    lines.extend(_render_success_factors(collection))
    # R12g-A 两段由注册表驱动（标签与 TOC 单一来源，见 _R12G_HEADER_SECTIONS）
    for _r12g_label, _r12g_fn in _R12G_HEADER_SECTIONS:
        lines.extend(_r12g_fn(collection))
    lines.extend(_render_price_structure(collection))

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


# --- _render_income_driver (R1) ---
def _extract_industry(basic_data: Any) -> str:
    """从 basic_info 维度数据提取行业，兼容 tushare「industry」与 akshare「行业」键。

    basic_info 可来自 tushare stock_basic（键 industry）或 akshare
    stock_individual_info_em（键「行业」）——只查一个键会在另一源下静默失配
    （金融行业豁免 F0-8 / 成长分支减权 F2-1 被跳过）。F0-8/F2-1 引入的
    多处手写循环统一收敛到此。
    """
    if isinstance(basic_data, list):
        for r in basic_data:
            if isinstance(r, dict):
                v = r.get("industry") or r.get("行业")
                if v:
                    return str(v)
    elif isinstance(basic_data, dict):
        v = basic_data.get("industry") or basic_data.get("行业")
        if v:
            return str(v)
    return ""


def _render_income_driver(collection: dict[str, Any]) -> list[str]:
    """R1: 报告头部「收益驱动假设」块（研究路径分流）。

    数据来源：collection.financials 中年报期（1231）记录的 net_profit——
    R12b 后 net_profit 由 income 表兜底（fina_indicator 字段被积分过滤时）。
    纯本地计算，零网络；年度样本 <3 年或净利全缺失 → 不渲染。
    """
    dims = _index_dims(collection)
    fin = _get_dim_data(dims, "financials")
    if not isinstance(fin, list) or not fin:
        return []
    annual: list[dict] = []
    for r in fin:
        if not isinstance(r, dict):
            continue
        ed = str(r.get("end_date", ""))
        npv = r.get("net_profit")
        if ed.endswith("1231") and npv is not None:
            try:
                annual.append({"year": ed, "net_profit": float(npv)})
            except (TypeError, ValueError):
                continue
    if len(annual) < 3:
        return []
    try:
        from lib.income_driver import classify_income_driver
    except ImportError:
        return []
    # F2-1: 行业传入（金融行业成长分支减权）——双键兼容见 _extract_industry
    industry: str | None = None
    basic_dim = dims.get("basic_info") or {}
    bdata = basic_dim.get("data") if isinstance(basic_dim, dict) else None
    industry = _extract_industry(bdata) or None
    result = classify_income_driver(annual, fin, industry=industry)
    driver = result.get("driver", "")
    conf = result.get("confidence", "")
    lines = [f"**[收益驱动假设（R1）]** {driver}（置信度: {conf}）— 研究路径分流依据，决定模块权重（R12d）"]
    if result.get("counter_evidence"):
        for item in result["counter_evidence"][:2]:
            lines.append(f"  - ⚠️ 反例: {item}")
    if result.get("missing_evidence"):
        lines.append("  - 🔍 证据缺失（需 WebSearch/公告补充）: " + "、".join(result["missing_evidence"]))
    return lines


# --- _render_style_match (R10) ---
def _render_style_match(collection: dict[str, Any]) -> list[str]:
    """R10: 报告头部「风格-标的匹配」三态行 + 混搭提示（固定模板）。

    数据来源：cmd_report 装配的 collection["style_match"] =
    {"style", "driver", "journal_driver", "state", "reason", "hint"}（match_style 产出）。
    无 style_match → 不渲染。
    """
    cfg = collection.get("style_match")
    if not isinstance(cfg, dict) or not cfg.get("state"):
        return []
    state = cfg["state"]
    driver = str(cfg.get("driver") or "?")
    style = str(cfg.get("style") or "未填写")
    lines = [f"**[风格-标的匹配（R10）]** {state}：自评风格 {style} × 收益驱动 {driver}"]
    if state == "混搭风险" and cfg.get("hint"):
        lines.append(f"  - ⚠️ {cfg['hint']}")
    elif cfg.get("reason"):
        lines.append(f"  - {cfg['reason']}")
    return lines


# --- _render_success_factors (R4) ---
def _render_success_factors(collection: dict[str, Any]) -> list[str]:
    """R4: 行业成功关键因素块（先答行业关键问题，再进通用 12 题）。

    数据来源：cmd_report 装配的 collection["success_factors"] =
    {"industry": 行业名, "covered": bool, "factors": [...]}（get_success_factors 产出）。
    因子 data_fields 从 financials 最新期取值；引擎外字段输出「需 AI 补查」。
    未覆盖行业 → 输出「无行业成功因素定义」一行，回退通用 12 题。
    """
    cfg = collection.get("success_factors")
    if not isinstance(cfg, dict):
        return []
    industry = str(cfg.get("industry") or "未知行业")
    factors = cfg.get("factors") or []
    if not cfg.get("covered") or not factors:
        return [
            f"**[行业成功关键因素（R4）]** {industry}：无行业成功因素定义"
            "（未覆盖行业，回退通用 12 题）"
        ]
    dims = _index_dims(collection)
    fin = _get_dim_data(dims, "financials")
    latest: dict = {}
    if isinstance(fin, list) and fin:
        rows = [r for r in fin if isinstance(r, dict) and r.get("end_date")]
        if rows:
            latest = max(rows, key=lambda r: str(r.get("end_date", "")))
    lines = [f"**[行业成功关键因素（R4）]** {industry}"]
    for i, factor in enumerate(factors, 1):
        if not isinstance(factor, dict):
            continue
        q = str(factor.get("question", "?"))
        fields = factor.get("data_fields") or []
        vals: list[str] = []
        for f in fields:
            v = latest.get(f)
            if v is None:
                vals.append(f"{f}: 需 AI 补查")
            else:
                try:
                    vals.append(f"{f}: {float(v):.2f}")
                except (TypeError, ValueError):
                    vals.append(f"{f}: {v}")
        src = factor.get("sources") or []
        data_part = " · ".join(vals) if vals else "需 AI 补查（引擎外字段）"
        lines.append(f"- {i}. {q}")
        lines.append(f"  - 数据: {data_part} [来源: {' / '.join(src)}]")
    return lines


# --- _render_ma_system (R12g-A) ---
def _render_ma_system(collection: dict[str, Any]) -> list[str]:
    """R12g-A: 均线系统表（MA5/10/20/60 值 + 现价位置 + 排列标签）。

    复用 technical.compute 的 _ma_alignment（periods=(5,10,20,60)），纯本地计算。
    kline 样本不足 → 不渲染。
    """
    dims = _index_dims(collection)
    kline = _get_dim_data(dims, "kline")
    if not isinstance(kline, list) or len(kline) < 5:
        return []
    try:
        from lib.technical import compute
        tech = compute(kline)
        t = (tech.get("trend") or {})
    except Exception:
        return []
    closes = tech.get("latest_close")
    # 缺陷5: latest_close 可为 None/NaN（technical.latest_close）。有限性检查必须在
    # 比较之前——None 参与 >= 抛 TypeError（逃出唯一的 try/except 中止整个渲染），
    # NaN 参与比较恒 False（四根 MA 全误标「现价下方」+ 渲染 '现价 nan'）。
    if closes is not None:
        try:
            closes_finite = math.isfinite(closes)
        except TypeError:
            closes_finite = False
        if not closes_finite:
            closes = None
    ma = t.get("ma") or {}
    latest = {}
    for p in ("5", "10", "20", "60"):
        vals = ma.get(p) or []
        v = vals[-1] if vals and vals[-1] is not None else None
        if v is not None:
            try:
                v_finite = math.isfinite(v)
            except TypeError:
                v_finite = False
            if not v_finite:
                v = None
        latest[p] = v
    parts = []
    for p in ("5", "10", "20", "60"):
        v = latest.get(p)
        if v is None:
            parts.append(f"MA{p}: —")
            continue
        if closes is None:
            pos = "（现价不可得）"
        else:
            pos = "（现价上方）" if closes >= v else "（现价下方）"
        parts.append(f"MA{p}={v:.2f}{pos}")
    label = (t.get("alignment") or {}).get("trend_label", "—")
    if closes is not None:
        parts.append(f"现价 {closes:.2f}")
    lines = ["**[均线系统表（R12g）]** " + " · ".join(parts)]
    lines.append(f"  排列: {label} [来源: kline derived（technical.compute）]")
    return lines


# --- _render_limit_streak_structure (R12g-A) ---
def _limit_streak_section_active(collection: dict[str, Any]) -> bool:
    """连板结构区块是否激活（TOC 与渲染共用触发判定，batch-test P1-3）。

    触发 = 采集层已写入 zt_pool / lhb 维度（cmd_report 在 detect_limit_streaks
    判定近 5 日 ≥2 涨停后才采集）；未触发时零网络调用，TOC 也不得列出该条目。
    """
    dims = _index_dims(collection)
    zt = _get_dim_data(dims, "zt_pool")
    lhb = _get_dim_data(dims, "lhb")
    return isinstance(zt, dict) or isinstance(lhb, dict)


# R12g-A 连板结构区块标签：渲染前缀 + 注册表 + TOC 过滤三处共用单一常量
# （code-review #2：此前三处字面量手写同步，任一处漂移即 TOC 与正文脱节）
_LIMIT_STREAK_LABEL = "连板结构（R12g）"


def _render_limit_streak_structure(collection: dict[str, Any]) -> list[str]:
    """R12g-A: 连板结构六步（仅触发时渲染；数据由 lhb/zt_pool 维度提供）。

    已有数据可交付 = 情绪周期 / 梯队 / 龙虎榜席位 / 证伪条件（引擎渲染，AI 只做合成引用）；
    待数据源验证 = 筹码、题材纯度 → 强制「不可得 + attempted sources」，AI 不得补全。
    """
    if not _limit_streak_section_active(collection):
        return []
    dims = _index_dims(collection)
    zt = _get_dim_data(dims, "zt_pool")
    lhb = _get_dim_data(dims, "lhb")
    lines = [f"**[{_LIMIT_STREAK_LABEL}]**（近 5 日 ≥2 涨停触发）"]
    if isinstance(zt, dict) and zt.get("total"):
        dist = zt.get("board_dist") or {}
        dist_s = "、".join(f"{k}板{x}家" for k, x in sorted(dist.items()))
        lines.append(f"- 情绪周期: 涨停 {zt['total']} 家（{zt.get('date')}）· "
                     f"最高 {zt.get('max_board')} 板 · {dist_s} [来源: stock_zt_pool_em]")
        lines.append(f"- 梯队: 最高连板 {zt['max_board']} 板（当日连板高度分层；题材归属由 AI 合成引用）")
    else:
        lines.append("- 情绪周期: 数据不可得 [来源: stock_zt_pool_em]")
        lines.append("- 梯队: 数据不可得 [来源: stock_zt_pool_em]")
    if isinstance(lhb, dict) and (lhb.get("seats") or {}).get("has_seats"):
        seats = lhb["seats"]
        buys = "、".join(str(r.get("交易营业部名称", "?")) for r in seats.get("top_buy", [])[:3])
        lines.append(f"- 龙虎榜席位: 买入榜 {buys} [来源: stock_lhb_stock_detail_em]")
    else:
        lines.append("- 龙虎榜席位: 未上榜或席位不可得（连板 ≠ 必然上榜）——"
                     "降级用资金流三日结构替代 [来源: stock_lhb_detail_em/sina + stock_fund_flow_industry]")
    lines.append("- 证伪条件: 涨停次日不延续（连板断板/跌停）→ 情绪退潮；"
                 "席位纯游资接力无机构 → 高度有限；资金流三日转净流出 → 退潮信号")
    lines.append("- 筹码: 不可得 + attempted sources: [未定义数据源——待数据源验证后补充]")
    lines.append("- 题材纯度: 不可得 + attempted sources: [未定义数据源——待数据源验证后补充]")
    return lines


# --- R12g-A 头部区块注册表（单一来源） ---
# 均线系统表 / 连板结构在 brief/concise/full 三种模式的 engine extras 头部均渲染；
# TOC 标签（_v3._report_toc）与渲染顺序（_render_engine_extras）由此常量派生，
# 杜绝 section 列表与静态 TOC 再次漂移（code-review: R12g-A 已渲染但缺失于 TOC）。
_R12G_HEADER_SECTIONS: tuple[tuple[str, Callable[[dict], list[str]]], ...] = (
    ("均线系统表（R12g）", _render_ma_system),
    (_LIMIT_STREAK_LABEL, _render_limit_streak_structure),
)


# --- _render_price_structure (R12e) ---
def _render_price_structure(collection: dict[str, Any]) -> list[str]:
    """R12e: 近端价格结构（涨跌停/连板/极端波动）头部行。

    修复沃格光电实证缺陷：20 日窗口累计数掩盖"三跌停 → 三连板"近端结构。
    """
    dims = _index_dims(collection)
    kline = _get_dim_data(dims, "kline")
    if not isinstance(kline, list) or len(kline) < 5:
        return []
    try:
        from lib.technical import detect_limit_streaks
        symbol = str(collection.get("symbol") or "")
        st = detect_limit_streaks(kline, symbol=symbol)
    except Exception:
        return []
    if not st.get("available"):
        return []
    # window_pct 可为 None：detect_limit_streaks 在窗口首 close 为 0.0（停牌日/故障行
    # 被 0 填充）时返回 None——None 参与 :+.1f 格式化抛 TypeError，会中止整个渲染
    # （此处位于唯一 try/except 之外）。None → 占位「—」，与 _render_ma_system 同风格。
    window_pct = st.get("window_pct")
    pct_s = f"{window_pct:+.1f}%" if window_pct is not None else "—"
    parts = [f"近 {st['lookback']} 日 {pct_s}"]
    if st["recent_limit_ups"] or st["recent_limit_downs"]:
        parts.append(
            f"涨跌停 {st['recent_limit_ups']}↑/{st['recent_limit_downs']}↓"
            f"（{st['limit_threshold']:.0f}% 阈值）")
    for s in st.get("streaks") or []:
        label = "连板" if s["type"] == "up" else "连跌停"
        total = f"，累计 {s['total_pct']:+.1f}%" if s.get("total_pct") is not None else ""
        parts.append(f"{s['start_date']}~{s['end_date']} {s['days']}日{label}{total}")
    low = st.get("period_low") or {}
    if low.get("date"):
        parts.append(f"区间低点 {low['value']}（{low['date']}）")
    return [f"**[近端价格结构]** " + " · ".join(parts)]


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
