"""Concise mode + ReportEnhancer + V3 main entry point."""
from __future__ import annotations
# Import ALL names (including _-prefixed) from _base
from . import _base as __base_ref
for __base_n in dir(__base_ref):
    if not __base_n.startswith("__"):
        globals()[__base_n] = getattr(__base_ref, __base_n)
del __base_ref, __base_n


# Import ALL names (including _-prefixed) from _v2
from . import _v2 as __v2_ref
for __v2_n in dir(__v2_ref):
    if not __v2_n.startswith("__"):
        globals()[__v2_n] = getattr(__v2_ref, __v2_n)
del __v2_ref, __v2_n


# Import ALL names (including _-prefixed) from _v3
from . import _v3 as __v3_ref
for __v3_n in dir(__v3_ref):
    if not __v3_n.startswith("__"):
        globals()[__v3_n] = getattr(__v3_ref, __v3_n)
del __v3_ref, __v3_n


logger = logging.getLogger(__name__)

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
def _is_valuation_extreme(
    data: dict, percentile: float = 80, val_cache: dict | None = None,
) -> bool:
    """检查估值分位是否超过阈值（从 dimensions 读取，与报告其他模块一致）。

    val_cache 与 render_report_v3 的缓存共享：增强器条件与风险报告使用同一份
    val_cache，5 年 PE/PB/PS 分位序列只全量计算一次（此前传临时 dict 永不命中
    备忘录，full 报告每次渲染全量重算两次）。
    """
    dims = _index_dims(data)
    pe_pct, _, _ = _v3_valuation_percentiles(dims, val_cache)
    return pe_pct is not None and pe_pct >= percentile


# --- setup_default_enhancers ---
def setup_default_enhancers(data: dict, val_cache: dict | None = None) -> ReportEnhancer:
    """配置默认增强器集合。

    val_cache 由 render_report_v3 传入（先于增强器执行创建），
    保证增强器条件与后续风险报告/各 section 共用同一份估值分位缓存。
    """
    enhancer = ReportEnhancer(data)

    enhancer.register(
        "price_shock_websearch",
        _has_price_signal,
        lambda d: {"triggered": True, "reason": "涨价信号确认，建议 WebSearch 深搜"},
    )

    enhancer.register(
        "valuation_high_alert",
        lambda d: _is_valuation_extreme(d, percentile=80, val_cache=val_cache),
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
        from ..render_extras import render_rigor_warnings, section_exogenous_shock, render_ah_detection_note
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
        if pe_pct >= EXTREME_HIGH_THRESHOLD:
            qualitative = " — 估值偏高"
        elif pe_pct <= EXTREME_LOW_THRESHOLD:
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
        nb_days = int(nb.get("days") or 10)
        if net10 is not None and float(net10) > 0:
            points.append(f"北向近 {nb_days} 日净流入 {float(net10):+.0f}，资金面偏向积极")
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
                if float(np_v) > 0 and float(ocf) / float(np_v) < OCF_COVERAGE_ALERT:
                    ocf_divergence = True
            except (TypeError, ValueError, ZeroDivisionError):
                pass
        # 毛利率趋势（字段优先级同 _concise_financial_snapshot）
        if len(fin_sorted) >= 2:
            gm_curr = _coalesce_fin_field(
                [latest], *GROSS_MARGIN_FIELDS)
            gm_prev = _coalesce_fin_field(
                [fin_sorted[-2]], *GROSS_MARGIN_FIELDS)
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
        points.append(f"经营现金流/净利润 < {OCF_COVERAGE_ALERT}，利润质量需关注")

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
        nb_days = int(nb.get("days") or 10)
        if net10 is not None and float(net10) < 0:
            points.append(f"北向近 {nb_days} 日净流出 {float(net10):+.0f}，资金面偏谨慎")
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
    # 字段优先级同 render_utils._coalesce_fin_field（_v3 同源）：grossprofit_margin
    # （tushare 真名）→ gross_margin → gross_profit_margin（拼错旧键，兜底兼容老快照）
    gross_margin = _coalesce_fin_field(
        [latest], *GROSS_MARGIN_FIELDS)
    np_v = latest.get("net_profit")
    ocf = latest.get("ocf") if latest.get("ocf") is not None else latest.get("n_cashflow_act")

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
            # np > 0 守卫：亏损期不渲染负比率（对齐 _concise_bear 与 _v3 口径）
            ratio = float(ocf) / float(np_v) if float(np_v) > 0 else None
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
            nb_days = int(nb.get("days") or 10)
            points.append(f"- 北向近 {nb_days} 日{direction} {abs(float(net10)):.0f}")
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
def render_report_v3(collection: dict[str, Any], symbol: str, mode: str = "full",
                     analysis: list[dict] | None = None,
                     profile: dict[str, Any] | None = None) -> str:
    """v0.2.0 九模块数据底稿。mode="brief" 输出精简简报, mode="concise" 输出对话场景精简。

    analysis（R-B1）: analysis.json 段列表，渲染期替换 "[待 Claude report 阶段填充]" 占位。
    profile（P0-5）: ResearchProfile 研究档案；仅 full 模式在报告说明块内展示，
    不做字段过滤——偏好只改阅读顺序与补证优先级。
    """
    dims = _index_dims(collection)
    market_structure = collection.get("market_structure") or {}

    # 就地槽位消费登记：每轮渲染从零开始（同一 collection 二次渲染不得残留
    # 上一轮的登记，否则本次未渲染的段会被误剔除）。
    from lib.analysis_schema import reset_inline_consumed
    reset_inline_consumed(collection)

    # val_cache 先建：增强器条件 / 风险报告 / 各 section 共用同一缓存，
    # 5 年 PE/PB/PS 分位序列只全量计算一次（code-review: 临时 dict 永不命中备忘录）
    val_cache: dict = {}
    # P3-1: 统一增强触发器
    enhancer = setup_default_enhancers(collection, val_cache)
    collection["_enhancements"] = enhancer.apply()

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
        sections = [
            _section_executive_summary(collection, symbol, dims, val_cache=val_cache),
            _section_research_question(collection, symbol, val_cache=val_cache),
            _section_snapshot(collection, symbol, dims, val_cache=val_cache),
            _section_dynamic_drivers(
                collection, symbol, dims, market_structure, val_cache=val_cache,
            ),
            _section_holder_changes(dims.get("holder_changes", {}), collection.get("events")),
            _section_bull_bear(
                collection, symbol, dims, market_structure, risk_data,
                val_cache=val_cache, analysis=analysis,
            ),
            _wrap_details(
                "展开：风险与不确定性",
                _section_risk_uncertainty(
                    collection, symbol, dims, market_structure, risk_data,
                    val_cache=val_cache,
                ),
            ),
            _references_appendix(collection),
            _risk_footer(),
        ]
        parts.extend([
            # v0.3.0 fix②：brief 此前完全忽略 analysis payload（`--analysis` 传了
            # 也不生效），6.4 分钟的精简版因此一个分析字都没有。现前置 overview
            # 判断区、尾部附其余分析段，与 full 共用同一组槽位语义。
            # 顺序敏感：附录依赖宿主「消费登记」，故 sections 先求值（上面的
            # list 已完成宿主调用），再与 overview/附录一起入列。
            _render_analysis_overview(analysis, collection),
            _render_analysis_appendix(analysis, collection),
            *sections,
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
        sections = [
            _concise_positioning(collection, symbol, dims, val_cache=val_cache),
            _concise_contradictions(collection, dims, val_cache=val_cache),
            _concise_bull(collection, symbol, dims, market_structure, val_cache=val_cache),
            _concise_bear(collection, symbol, dims, market_structure, risk_data, val_cache=val_cache),
        ]
        # v0.3.0 fix②（concise 侧补齐）：此前 `--analysis` 在本模式下被解析、校验后
        # 丢弃（exit 0），一句话不落。现与 brief 同形——overview 前置（对话场景
        # 本就「结论先行」），其余段进展开块，既不静默丢内容也不撑长输出。
        # 顺序敏感：附录依赖宿主「消费登记」，故 sections 先求值再入列。
        parts.extend([
            _render_analysis_overview(analysis, collection),
            *sections,
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
        appendix = _render_analysis_appendix(analysis, collection)
        if appendix:
            parts.append(_wrap_details("展开：分析详情", appendix))
        parts.append(_wrap_details("展开：参考资料", _references_appendix(collection)))
        parts.append(_risk_footer())
    else:
        # F-3: 快速否决检测需在 D 段之前算出，供 veto_triggered 联动 + 展示触发条目
        _fast_veto = _check_fast_veto(dims, collection)
        parts: list[str] = [
            _header_v2(collection, symbol),
            _render_judgment_index(analysis),
        ]
        extras = _render_engine_extras(collection)
        if extras:
            parts.append("\n".join(extras))
        _extras = _render_extras_block(collection, strict=strict)
        if _extras:
            parts.append("\n\n".join(_extras))
        sections = [
            _report_toc(collection),
            _section_research_question(collection, symbol, val_cache=val_cache),
            _section_snapshot(collection, symbol, dims, val_cache=val_cache),
            _section_dynamic_drivers(
                collection, symbol, dims, market_structure, val_cache=val_cache,
            ),
            _section_market_structure(
                collection, symbol, market_structure, val_cache=val_cache,
            ),
            _section_participant_behavior_scan(
                collection, symbol, market_structure, dims, analysis=analysis,
            ),
            _section_events_timeline(collection, analysis=analysis),
            _section_holder_changes(dims.get("holder_changes", {}), collection.get("events")),
            _section_research_summary(collection, symbol, dims),
            _wrap_details(
                "展开：静态基本面（12题）",
                _section_static_fundamentals(dims, collection, val_cache=val_cache, analysis=analysis),
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
                collection, symbol, dims, market_structure, risk_data,
                val_cache=val_cache, analysis=analysis,
            ),
            _section_left_right_probability(
                collection, symbol, dims, market_structure, val_cache=val_cache,
            ),
            _wrap_details(
                "展开：风险与不确定性",
                _section_risk_uncertainty(
                    collection, symbol, dims, market_structure, risk_data,
                    val_cache=val_cache,
                ),
            ),
            _section_technical_brief(dims, val_cache=val_cache, collection=collection),
            _section_six_gates_scorecard(dims, collection, val_cache),
            _render_engine_selfcheck_appendix(collection),
            _references_appendix(collection),
            _risk_footer(),
        ]
        # 方案 A（v0.3.0，四层阅读结构）——
        #   ① 判断索引（首屏索引，见 _render_judgment_index；在 parts 头部）
        #   ② 报告说明（底稿身份 + 研究档案）
        #   ③ 重要发现（5 分钟阅读区，overview 槽位正文）→ 分析详情（其余分析段）
        #   ④ 九模块数据底稿（以下各 section）
        # 均置于目录之前，使「结论先行」不受导航块干扰；无 analysis
        # 时各层均返回空串（基线零 diff 保持）。
        # 顺序敏感：附录依赖宿主「消费登记」，故 sections 先求值，再入列。
        parts.extend([
            _full_mode_identity_status(symbol, analysis, profile),
            _render_analysis_overview(analysis, collection),
            _render_analysis_appendix(analysis, collection),
            *sections,
        ])
    return "\n\n".join(p for p in parts if p)


def _full_mode_identity_status(symbol: str, analysis: list[dict] | None,
                               profile: dict[str, Any] | None = None) -> str:
    """full 是可审计底稿；不得在缺少分析合成时伪装成研究成品。

    profile（P0-5）：研究档案仅在提供时追加到同一「报告说明」块内，
    不改变底稿身份判定，也不影响 brief/concise（该块本就不渲染）。
    """
    from lib.analysis_status import (ANALYSIS_OK, ANALYSIS_UNAVAILABLE,
                                     analysis_payload_status)
    from lib.research_profile import format_profile_markdown_lines

    status = analysis_payload_status(analysis)
    if status == ANALYSIS_OK:
        lines = [
            "## 报告说明",
            "",
            "> **产物定位：审计/证据数据底稿（分析合成已注入）。**",
            "> 本模式保留完整数据、来源与计算过程以供追溯；分析段已附在文末，"
            "但数据底稿本身不替代面向阅读的研究结论。",
        ]
    elif status == ANALYSIS_UNAVAILABLE:
        # 工具故障 ≠ 内容缺失：不得断言「分析合成未完成」（review C2）。
        lines = [
            "## 报告说明",
            "",
            "> **产物定位：数据底稿（分析合成状态无法校验）。**",
            "> 分析校验组件本次不可用，无法确认分析段是否已注入——这是工具故障，"
            "**不是内容缺失的证据**。",
            "> 本文件仅用于核验采集数据、来源和计算过程，不能视为完成的研究报告。",
        ]
    else:
        lines = [
            "## 报告说明",
            "",
            "> **产物定位：数据底稿（分析合成未完成）。**",
            "> 本文件仅用于核验采集数据、来源和计算过程，不能视为完成的研究报告。",
            "> 完成方式：准备通过校验的 `analysis.json` 后重渲："
            f"`uv run python skills/invest-a-stock/scripts/invest.py report {symbol} --mode full --analysis <analysis.json>`。",
        ]
    return "\n".join(lines + format_profile_markdown_lines(profile))


def _render_analysis_overview(analysis: list[dict] | None,
                              collection: dict | None = None) -> str:
    """方案 A：把 overview 槽位的分析段前置为「重要发现（5 分钟阅读区）」。

    动机：full 底稿把最有价值的判断层放在文末，读者需读完全文才看到结论。
    前置后形成「5 分钟判断区 → 九模块数据底稿 → 其余分析注记」三层。

    渲染体例与尾部注记同构（[事实]/[分析]/证据等级）——既满足 SOP-QC 的
    「先事实后分析」结构要求，也让前置段可独立追溯。长度由 analysis.json
    的写作控制，渲染器不做截断（截断会静默丢来源标注）。

    无 overview 段 → 返回空串，brief/full 基线零 diff 保持。
    """
    from lib.analysis_schema import mark_inline_consumed, split_overview
    ov, _ = split_overview(analysis)
    if not ov:
        return ""
    n = len(ov)
    lines = [
        "## 重要发现（5 分钟阅读区）",
        "",
        f"> 结论先行区：以下 {n} 段是本次分析的核心判断，数据底稿与其余分析注记见文末。",
    ]
    for sec in ov:
        mark_inline_consumed(collection, sec)
        title = str(sec.get("title") or "").strip()
        facts = str(sec.get("facts_md") or "").strip()
        amd = str(sec.get("analysis_md") or "").strip()
        ev = str(sec.get("evidence_tag") or "").strip()
        lines.append("")
        if title:
            lines += [f"### {title}", ""]
        if facts:
            lines += ["**[事实]**", "", facts, ""]
        if amd:
            lines += ["**[分析]**", "", amd, ""]
        if ev:
            lines.append(f"**证据等级：** {ev}")
            lines.append("")
    return "\n".join(lines).rstrip()


def _render_judgment_index(analysis: list[dict] | None) -> str:
    """在完整底稿首屏列出本次分析的判断索引（分类标签 + 标题）。

    分析段标题由研究阶段生成，已覆盖事件归因、经营质量、估值、行业/竞争、
    资金与风险等判断。这里只做可追溯索引，不重新概括或改写分析结论；完整
    事实、来源和证据强度仍以稍后的分析详情为准。

    成员判据与标签**不在本层决定**——由 `analysis_schema.index_entries` 单点
    给出（与 html 侧 `_html_judgment_index` 共用），本层只负责 md 排布。
    无条目 → 空串（无 analysis 与「全部段都被排除」两种情况都不会留下空标题）。
    """
    from lib.analysis_schema import index_entries

    entries = index_entries(analysis)
    if not entries:
        return ""
    lines = [
        "## 判断索引",
        "",
        "> 以下是本次研究最值得先看的判断索引；数字、事实来源和证据强度请展开对应分析段核验。",
        "",
    ]
    lines.extend(
        f"- **{label}**：{title}（详见下方对应分析段）" for label, title in entries
    )
    return "\n".join(lines).rstrip()


def _render_analysis_appendix(analysis: list[dict] | None,
                              collection: dict | None = None) -> str:
    """全量审查 P0-3：md 与 html 同源消费 analysis 段。

    旧实现 md 侧只在 A-5 时间线消费 events 段首行——非 events 的
    position（valuation/conclusion 等）在 html 渲染为完整卡、在 md 中
    **静默消失**（「同源」协议仅对 events 成立）。此处将全部段渲染为
    尾部注记节（facts/analysis/evidence 与 html 卡同构）；无 analysis →
    返回空串（基线 md 零 diff 保持）。

    方案 A / fix③：已被就地渲染的段必须剔除，否则同一段在 md 中出现两次。
    v0.3.0 修复：剔除条件从 `is_inline_slotted`（静态：命中槽位）改为
    `is_consumed_inline`（动态：本次确实渲染了）。静态版会误删宿主未渲染的
    段——条件宿主（无扫描行/无事件卡/无 MD&A 卡）与 brief 模式下，这些段
    既无正文落点又被剔除，`--analysis` 内容零落点丢失；同槽位多段也只有
    首个被 find_section 消费，其余同样丢失。

    **调用顺序敏感**：须在全部宿主 section 渲染之后调用（见 render_report_v3）。
    """
    from lib.analysis_schema import is_consumed_inline
    rest = [s for s in (analysis or []) if not is_consumed_inline(collection, s)]
    if not rest:
        return ""
    lines = ["## 分析详情（analysis.json 注入）", ""]
    for i, sec in enumerate(rest):
        if not isinstance(sec, dict):
            continue
        mod = str(sec.get("module") or sec.get("position") or f"section-{i}")
        title = str(sec.get("title") or mod).strip()
        facts = str(sec.get("facts_md") or "").strip()
        amd = str(sec.get("analysis_md") or "").strip()
        ev = str(sec.get("evidence_tag") or "").strip()
        lines.append(f"### {title}（{mod}）")
        lines.append("")
        if facts:
            lines += [f"**[事实]**", "", facts, ""]
        if amd:
            lines += [f"**[分析]**", "", amd, ""]
        if ev:
            lines.append(f"**证据等级：** {ev}")
            lines.append("")
    return "\n".join(lines)
