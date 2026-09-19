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
    _pct_medians,
    _pct_median_suffix,
    _pct_median_inline,
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
    """渲染报告头部：只放**带结论/判定**的引擎行。

    头部是全报告最高价值的位置。本函数被 brief/concise/full 三模式共用
    （_concise.render_report_v3），因此本函数里增删任何内容都会同时影响三种
    交付形态——下沉前须确认每种模式都有承接方。

    保留：宏观情景（国内/海外各带结论）、产业链位置、收益驱动假设（R1）、
    风格匹配（R10）、行业成功关键因素（R4，含未覆盖行业的覆盖范围披露）、
    连板结构（R12g，触发时）、报告增强触发（_render_enhancement_hints）。

    已下沉（**仅 full 有承接方**——brief/concise 因此不再包含这些内容，
    属删除而非搬迁，是有意的取舍：两模式定位为精简/对话产物）：
      · 多源融合 / 证据可信度  → full 附录「数据质量与引擎自检」
        （_render_engine_selfcheck_appendix）——引擎自检数据，读者无法据此判断
      · 均线系统表（R12g）      → full §8 技术指标附录（_v3._section_technical_brief）
      · 近端价格结构（R12e）    → full §8；二者与 §8 既有「趋势」「20/60/120 日高低」
        本就重复，属放错位置而非多余内容

    R4 的「未覆盖行业」披露**不在此列**：它是覆盖范围说明而非数据罗列，且
    brief/concise 无附录区承接，摘掉即等于删除。

    证据：用户审阅 2026-09-16 full 报告指出头部 4 项为无结论数据罗列。
    """
    lines: list[str] = []

    macro = collection.get("macro_context") or {}
    if macro.get("status") == "ok":
        from ..macro import macro_signal_label
        lines.append(f"**[宏观情景]** {macro_signal_label(macro)}")

    chain = collection.get("chain_context") or {}
    if chain.get("status") == "ok" and chain.get("industry"):
        pos = chain.get("chain_position") or "—"
        # 展示命中的申万名（chain_matched_on，如「锂电池」）而非 Tushare 粗分类名
        # （如「电气设备」）——粗名会误导读者把电池厂读成输配电企业。
        label = chain.get("chain_matched_on") or chain["industry"]
        lines.append(f"**[产业链]** {label} · {pos}")

    lines.extend(_render_income_driver(collection))
    lines.extend(_render_style_match(collection))
    lines.extend(_render_success_factors(collection))
    # R12g-A 注册表驱动（标签与 TOC 单一来源，见 _R12G_HEADER_SECTIONS）
    for _r12g_label, _r12g_fn in _R12G_HEADER_SECTIONS:
        lines.extend(_r12g_fn(collection))

    lines.extend(_render_enhancement_hints(collection))

    return lines


# --- _render_engine_selfcheck_appendix (附录 D) ---
_ENGINE_SELFCHECK_LABEL = "附录：数据质量与引擎自检"

# consensus → 读者可用的措辞。fusion._consensus_from_diff 的 "weak" 有**两种来源**：
#   (1) 单源分支（fusion.py:85-94，len(valid)==1，max_diff_pct 恒 0.0）
#       → 语义是「无第二源可比对」；
#   (2) 多源分歧（两源及以上返回数据但差异 >5%）
#       → 语义是「源之间冲突」。
# 只按 consensus 单值推断会把 (2) 误报成「只有一个源」，同时抹掉唯一能区分的
# max_diff_pct——12% 的跨源冲突被读成单源，违反「数据冲突并列不裁决」。
_CONSENSUS_LABELS = {
    "strong": "双源一致（≤1%）",
    "moderate": "双源接近（≤5%）",
}
_WEAK_SINGLE_LABEL = "单源，未做交叉验证"
_WEAK_MULTI_LABEL = "多源分歧（>5%）"


def _fusion_source_count(fp: dict) -> int:
    """参与融合的源数量。

    优先读 fusion 落库的 `source_values`（fusion.py 三个分支都会输出该键）；
    键缺失的旧数据/夹具按 max_diff_pct 推断：weak 且无差异值 ⇒ 单源分支
    （该分支 max_diff_pct 恒 0.0），否则视为多源。
    """
    sv = fp.get("source_values")
    if isinstance(sv, dict):
        return len(sv)
    return 2 if fp.get("max_diff_pct") else 1


def _fusion_consensus_label(fp: dict, raw: str) -> str:
    """consensus 文案：weak 须按**源数量**分档，不得一律写「单源」。"""
    if raw != "weak":
        return _CONSENSUS_LABELS.get(raw, raw or "—")
    return _WEAK_SINGLE_LABEL if _fusion_source_count(fp) <= 1 else _WEAK_MULTI_LABEL


def _format_fused_value(value: Any) -> str:
    """融合值按量级格式化。

    未 round 的来源是 fusion.weighted_rrf_for_dimension 的**单源分支**（原样
    透传取值；多源分支早已 round(...,4)）。该分支已补 round（review C7），但
    存量 collection 里仍带旧值（曾渲染出「融合值=14637.250837439999」），故渲染
    层继续自行格式化，不依赖上游 round 是否到位，也不依赖数据是否已重采。
    """
    if value is None:
        return "—"
    try:
        num = float(value)
    except (TypeError, ValueError):
        return str(value)
    if not math.isfinite(num):
        return "—"
    if abs(num) >= 1000:
        return f"{num:,.2f}"
    if abs(num) >= 1:
        return f"{num:.2f}"
    return f"{num:.4f}"


def _render_engine_selfcheck_appendix(collection: dict[str, Any]) -> str:
    """附录 D：多源核验 + 证据可信度 + 宏观指标明细。

    scope: 仅 full 模式装配（brief/concise 无附录区）。数据全部来自
    collection 的引擎自检字段，不做任何重算。
    """
    lines: list[str] = [f"## {_ENGINE_SELFCHECK_LABEL}", ""]

    fusion = collection.get("fusion") or {}
    rows = [
        (dim, fp) for dim, fp in sorted(fusion.items()) if isinstance(fp, dict)
    ]
    if rows:
        lines += [
            "### 多源核验",
            "",
            "| 维度 | 融合值 | 交叉验证 | 最大差异 |",
            "|------|--------|---------|---------|",
        ]
        for dim, fp in rows:
            raw_consensus = str(fp.get("consensus") or "")
            consensus = _fusion_consensus_label(fp, raw_consensus)
            raw_diff = fp.get("max_diff_pct")
            # 单源分支的 max_diff_pct 恒为 0.0（fusion.py 单源早返回），与「无第二源
            # 可比对」的语义矛盾，故显示「—」而非 0.0%；多源则一律照显差异值，
            # 含 weak 分歧（清空等于把跨源冲突这一事实抹掉）。
            diff = ("—" if _fusion_source_count(fp) <= 1 or raw_diff is None
                    else f"{raw_diff}%")
            lines.append(
                f"| {dim} | {_format_fused_value(fp.get('fused_value'))} "
                f"| {consensus} | {diff} |")
        lines += [
            "",
            "> 「单源」= 该维度仅一个数据源返回数据，无法交叉验证，**不等于数据有误**。",
            "> 「多源分歧」= 两个及以上源返回数据但差异 >5%，各源数值并列呈现，"
            "不判定何者正确。",
            "",
        ]

    cred = collection.get("credibility") or {}
    if cred:
        top = sorted(cred.items(), key=lambda x: -x[1])[:5]
        cred_s = " / ".join(f"{k} {v:.0f}" for k, v in top)
        lines += [
            "### 证据可信度",
            "",
            f"{cred_s}",
            "",
            "> 引擎内部评分（0-100），衡量该维度证据的可得性与一致性，**非投资含义**。",
            "",
        ]

    macro = collection.get("macro_context") or {}
    detail = _render_macro_detail_lines(macro)
    if detail:
        lines += ["### 宏观指标明细", ""] + detail + [""]

    if len(lines) <= 2:
        return ""
    return "\n".join(lines).rstrip()


def _render_macro_detail_lines(macro: dict[str, Any]) -> list[str]:
    """宏观指标明细：头部标签只保留规定格式的结论行，被移出的指标在此列全值。

    仅列**数值与来源**，不挂「高位」「平坦」这类无阈值说明的定性单词——
    定性判断在头部标签由确定性规则统一给出（macro._global_conclusion）。
    """
    indicators = macro.get("indicators") or {}
    if not isinstance(indicators, dict) or not indicators:
        return []
    specs = (
        ("money_supply", "M2 同比", "pct"),
        ("loan", "新增信贷", "loan"),
        ("dgs10", "美10Y", "pct"),
        ("dgs30", "美30Y", "pct"),
        ("dfii10", "美实际利率(10Y)", "pct"),
        ("t10y2y", "美10Y-2Y 期限利差", "pct"),
        ("t5yie", "美5Y 盈亏平衡通胀", "pct"),
        ("dtwexbgs", "美元指数(广义)", "num"),
        ("dcoilbrenteu", "布伦特原油", "num"),
        ("dexchus", "USDCNY", "num"),
        ("acm_tp10", "ACM 10Y 期限溢价", "num"),
    )
    out: list[str] = []
    for key, disp, fmt in specs:
        ind = indicators.get(key)
        if not isinstance(ind, dict):
            continue
        val = ind.get("value")
        if val is None:
            continue
        try:
            num = float(val)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(num):
            continue
        if fmt == "pct":
            shown = f"{num:.2f}%"
        elif fmt == "loan":
            # 引擎原值 + 单位声明：渲染层**不做换算**（P0——本附录存在的意义就是
            # 供第 1 层复检与原始 JSON 对值对单位）。此前按本地阈值把 3000（亿元）
            # 写成「0.3万亿」、12000 写成「12000亿」，同一字段两种单位且仍挂引擎
            # 来源标签，复检无法对账。
            shown = f"{num:,.2f}亿元"
        else:
            shown = f"{num:,.2f}"
        src = ind.get("source") or "—"
        out.append(f"- {disp}: {shown} [来源: {src}]")
    return out


# --- _render_income_driver (R1) ---
# income_driver.classify_income_driver 的 missing_evidence 键 → 读者可读的缺口说明。
# 括号内是「缺了它会影响哪个判断」，不是装饰：R1 的置信度与该判断直接相关。
_MISSING_EVIDENCE_LABELS = {
    "dividend": "分红记录（影响分红连续性与股息回报的判断）",
    "refi": "近 5 年再融资记录（影响增长是否依赖外部融资稀释的判断）",
}


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
    try:
        from lib.income_driver import classify_income_driver, extract_annual_rows
    except ImportError:
        return []
    # 装配收敛到唯一实现（R2/T9-2）：与 style_match 侧共用，防两处漂移
    annual = extract_annual_rows(fin)
    if len(annual) < 3:
        return []
    # F2-1: 行业传入（金融行业成长分支减权）——双键兼容见 _extract_industry
    industry: str | None = None
    basic_dim = dims.get("basic_info") or {}
    bdata = basic_dim.get("data") if isinstance(basic_dim, dict) else None
    industry = _extract_industry(bdata) or None
    result = classify_income_driver(annual, fin, industry=industry)
    driver = result.get("driver", "")
    conf = result.get("confidence", "")
    lines = [f"**[收益驱动假设]** {driver}（置信度: {conf}）— 研究路径分流依据，决定模块权重"]
    if result.get("counter_evidence"):
        for item in result["counter_evidence"][:2]:
            lines.append(f"  - ⚠️ 反例: {item}")
    if result.get("missing_evidence"):
        # income_driver.classify_income_driver 的 missing_evidence 返回**内部键名**
        # （"dividend" / "refi"），直接出口读者只会看到两个英文单词。此处映射为
        # 中文语义并带上「缺了它会影响什么判断」——否则读者无从判断该不该去补。
        # 映射放渲染层：income_driver.py:217 的过滤逻辑与测试依赖原键名。
        items = [
            _MISSING_EVIDENCE_LABELS.get(str(key), str(key))
            for key in result["missing_evidence"]
        ]
        lines.append("  - 🔍 证据缺口（需 WebSearch/公告补充）: " + "、".join(items))
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
    lines = [f"**[风格匹配]** {state}：自评风格 {style} × 收益驱动 {driver}"]
    if state == "混搭风险" and cfg.get("hint"):
        lines.append(f"  - ⚠️ {cfg['hint']}")
    elif cfg.get("reason"):
        # 「匹配」态的 reason 由 style_match 置为 f"{style} × {driver}{note}"，与主行重复。
        # 只保留有信息量的尾注（如趋势/事件驱动的信息深度提示），避免子行复读主行。
        note = str(cfg["reason"]).replace(f"{style} × {driver}", "").strip()
        if note:
            lines.append(f"  - {note}")
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
        # 保留在头部（三模式共用）：这是「本报告的 12 题是通用兜底而非行业定制」的
        # 覆盖范围披露，不是数据罗列。brief/concise 无附录区可承接，摘掉即等于删除，
        # 读者将无从判断结论的适用范围。
        return [
            f"**[行业成功关键因素]** {industry}：无行业成功因素定义"
            "（未覆盖行业，回退通用 12 题）"
        ]
    dims = _index_dims(collection)
    fin = _get_dim_data(dims, "financials")
    latest: dict = {}
    if isinstance(fin, list) and fin:
        rows = [r for r in fin if isinstance(r, dict) and r.get("end_date")]
        if rows:
            latest = max(rows, key=lambda r: str(r.get("end_date", "")))
    lines = [f"**[行业成功关键因素]** {industry}"]
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
            pos = "（收盘价不可得）"
        else:
            pos = "（收盘价上方）" if closes >= v else "（收盘价下方）"
        parts.append(f"MA{p}={v:.2f}{pos}")
    label = (t.get("alignment") or {}).get("trend_label", "—")
    # 口径标注（review P1）：本表比较用的是**日线收盘价**，与模块 1 的实时价常
    # 不同（300750 实测：实时 305.48 vs 09-15 收盘 316.36）。两处都写「现价」
    # 会让读者把技术段口径当成实时价，故此处写明口径与日期。
    # 日期必须与 latest_close 同源（code-review P2）：compute 已剔除 close 为
    # None/NaN 的行（停牌残留 bar），并据此产出 last_date —— 对**原始**列表取
    # max(trade_date) 会把被剔除行的日期配到前一有效交易日的收盘价上（实测
    # 收盘 17.97（2026-03-01），17.97 实为 02-28 的价）。末行无 trade_date 时
    # last_date 为空串 → 不显示日期（宁缺勿错配）。
    _kd = str(tech.get("last_date") or "")
    if closes is not None:
        parts.append(f"收盘 {closes:.2f}" + (f"（{_kd}）" if _kd else ""))
    lines = ["**[均线系统表]** " + " · ".join(parts)]
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
_LIMIT_STREAK_LABEL = "连板结构"


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
# 连板结构在 brief/concise/full 三种模式的 engine extras 头部渲染（触发时）；
# TOC 标签（_v3._report_toc）与渲染顺序（_render_engine_extras）由此常量派生，
# 杜绝 section 列表与静态 TOC 再次漂移（code-review: R12g-A 已渲染但缺失于 TOC）。
#
# 均线系统表（R12g）原在此表内、随头部渲染并占一条 TOC；已下沉至 §8 技术指标附录
# （_v3._section_technical_brief），TOC 条目随之自动消失——本表是唯一来源，
# 不要在 _v3 的静态 TOC 列表里手动增删它。
_R12G_HEADER_SECTIONS: tuple[tuple[str, Callable[[dict], list[str]]], ...] = (
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
