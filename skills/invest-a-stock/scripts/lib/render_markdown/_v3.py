"""V3 core sections — analysis, snapshot, drivers, market structure, financials."""
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

from ..shared_dates import (  # noqa: E402
    fmt_fetched_at,
    normalize_end_date as _norm_ed,
    yyyymmdd_to_iso as _to_iso_date,
)
from ..financials import dedupe_by_end_date, find_yoy_row, resolve_rf  # noqa: E402


logger = logging.getLogger(__name__)


def _pe_loss_flag(val_cache: dict | None) -> str:
    """R12c: PE 亏损期占比 >30% 时标题层强制标注（P0-2 规则）。

    仅在标题（模块 0/1）附加「PE分位失真·仅作位置参考」，提示分位不反映估值贵贱。
    """
    if not val_cache:
        return ""
    summary = val_cache.get("val_summary") or {}
    pe = summary.get("pe") or {}
    if (pe.get("loss_ratio") or 0) > 0.3:
        return "PE分位失真·仅作位置参考"
    return ""


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
    pe_med, pb_med = _pct_medians(val_cache, dims)
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

    if pe_pct is not None and (pe_pct >= EXTREME_HIGH_THRESHOLD or pe_pct <= EXTREME_LOW_THRESHOLD):
        zone = "偏高" if pe_pct >= EXTREME_HIGH_THRESHOLD else "偏低"
        explanations.append((
            "B",
            f"估值历史分位{zone}（PE {pe_pct:.1f}%{_pct_median_inline(pe_med)}）驱动定价预期重估",
            "valuation 历史分位",
            "⚠️",
        ))
    elif pb_pct is not None and (pb_pct >= EXTREME_HIGH_THRESHOLD or pb_pct <= EXTREME_LOW_THRESHOLD):
        zone = "偏高" if pb_pct >= EXTREME_HIGH_THRESHOLD else "偏低"
        explanations.append((
            "B",
            f"PB 历史分位{zone}（{pb_pct:.1f}%{_pct_median_inline(pb_med)}）或反映资产定价差异",
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
    elif flow_direction_relation(nb_v, mf_v) == "divergence":
        explanations.append((
            "E",
            "北向与全档资金方向相反，资金归因存在分歧",
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
    # 同 _section_executive_summary：financials 维度为降序，裸取 [-1] 会锚定
    # 最旧一期（曾致核心矛盾行显示 2022Q3 的 ROE）。先按 end_date 升序。
    fin = dims.get("financials", {}).get("data")
    fin_rows = [r for r in fin if isinstance(r, dict)] if isinstance(fin, list) else []
    if fin_rows:
        latest = sort_kline_asc(fin_rows)[-1]
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
    nb_days = int(nb.get("days") or 10) if isinstance(nb, dict) else 10
    quote = dims.get("quote", {}).get("data") or {}
    chg = quote.get("change_pct") if isinstance(quote, dict) else None
    if net10 is not None and chg is not None:
        try:
            net_f, chg_f = float(net10), float(chg)
            if net_f > 0 and chg_f < -2:
                items.append(
                    f"北向近{nb_days}日净流入 {net_f:+.0f} 与股价 {chg_f:+.1f}% 背离"
                    f"[来源: northbound+quote]"
                )
            elif net_f < 0 and chg_f > 2:
                items.append(
                    f"北向近{nb_days}日净流出 {net_f:+.0f} 与股价 {chg_f:+.1f}% 背离"
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
    industry = _extract_industry(basic)

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
    # 报告期锚定最新一期：financials 维度 Tushare 源为**降序**（新→旧），
    # 裸取 fin[-1] 会拿到最旧一期——曾致执行摘要显示 2022Q3 的 ROE/EPS
    # （与 F0-4「序列方向假设错误」同类）。与 scoring._sorted_rows、
    # render_risk 等消费者一致，先委托 sort_kline_asc 按 end_date 升序。
    fin = dims.get("financials", {}).get("data")
    fin_rows = [r for r in fin if isinstance(r, dict)] if isinstance(fin, list) else []
    if fin_rows:
        latest = sort_kline_asc(fin_rows)[-1]
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


# --- _core_variables ---
def _core_variables(dims: dict[str, dict], collection: dict, *,
                    val_cache: dict | None = None) -> list[str]:
    """本次分歧所围绕的核心变量（0–3 条）。

    v0.3.1 A4：原 `_concise._section_core_tension` 的「核心矛盾小结」是一段无
    `##` 宿主的结论复述（挂在 DCF 之后，与 §0/§1/§3 讲同一件事），改为在
    `## 0.` 节内就地给出这三个变量——同一判断只出现一次。

    不足 2 条时返回空表（沿用原判据：单变量不构成「矛盾」，不硬凑）。
    """
    pe_pct, _, pe_zone = _v3_valuation_percentiles(dims, val_cache)
    market_structure = collection.get("market_structure") or {}
    ig, cagr, np_cagr = _v3_bull_bear_implied_growth(
        dims, market_structure, val_cache=val_cache,
    )
    ref_cagr, ref_label = _growth_reference(cagr, np_cagr)
    variables: list[str] = []
    if pe_pct is not None:
        variables.append(
            f"估值历史区间位置（当前 {pe_pct:.1f}%，{pe_zone or '—'}）能否维持"
        )
    # R1（2026-10-04 独立复检）：原判据只看 rf_is_default，漏掉美元口径与
    # 来源/币种未确认——同一报告因此同时出现「g_implied 缺口」与「暂停方向
    # 解读」（A06 跨节冲突）。准入收敛为 rf_usable（仅确认 CNY）。
    if (ig.get("g_implied") is not None and ig.get("rf_usable")
            and ref_cagr is not None and ref_label):
        # REV-04 同消费链（2026-10-07 主线收尾）：原「g_implied 与实际 CAGR 的
        # 缺口」把两个不同变量/时间假设的读数读作可解释的增长缺口——改为两个
        # 分别核查的读数（条件模型读数 vs 历史事实），并明示不可直接比较。
        g_pct = ig["g_implied"] * 100
        variables.append(
            f"条件模型读数（隐含增长 g_implied {g_pct:.1f}%，取 r 与 PE 假设）与"
            f"历史事实（实际{ref_label} CAGR {ref_cagr:+.1f}%，有限历史区间）"
            "须分别核查——两值变量与时间假设不同，不可直接比较"
        )
    elif ig.get("g_implied") is not None and ref_cagr is not None:
        if ig.get("rf_is_default"):
            if ig.get("rf_note"):
                variables.append(
                    f"无风险利率不可用（{ig['rf_note']}），隐含增长与实际增长的比较暂缓")
            else:
                variables.append("无风险利率尚待核验，隐含增长与实际增长的比较暂缓")
        elif ig.get("rf_is_wrong_currency"):
            variables.append("无风险利率币种与 A 股不一致，隐含增长与实际增长的比较暂缓")
        elif ig.get("rf_is_currency_unconfirmed"):
            variables.append("无风险利率来源/币种未确认，隐含增长与实际增长的比较暂缓")
    sw = market_structure.get("sw_index") or {}
    if sw.get("stock_vs_industry_pct") is not None:
        variables.append(
            f"个股相对行业超额 {sw['stock_vs_industry_pct']:+.2f}% 的可持续性"
        )
    return variables if len(variables) >= 2 else []


# --- _section_research_question ---
def _section_research_question(
    collection: dict, symbol: str, *, val_cache: dict | None = None,
) -> str:
    dims = _index_dims(collection)
    triggers: list[str] = []

    chg, window = _v3_price_change(dims)
    if chg is not None and window is not None and window >= 20 and abs(chg) >= 10:
        triggers.append("A")

    pe_pct, pb_pct, _ = _v3_valuation_percentiles(dims, val_cache)
    pe_med, _pb_med = _pct_medians(val_cache, dims)

    # LAW 17: 构建含触发源数据的标题 + 段首主旨句
    chg_s = f"{chg:+.2f}%" if chg is not None else ""
    pe_s = f"PE {pe_pct:.1f}% 分位{_pct_median_suffix(pe_med)}" if pe_pct is not None else ""
    loss_flag = _pe_loss_flag(val_cache)
    if pe_s and loss_flag:
        pe_s += f"（{loss_flag}）"
    title_parts = [p for p in [chg_s, pe_s] if p]
    title_suffix = " · ".join(title_parts) if title_parts else "核心问题"
    judgment = f"当前{title_suffix}，以下为激活的研究问题与触发源。"
    lines = [f"## 0. {title_suffix}", ""]
    lines.append(f"**结论：** {judgment}")
    lines.append("")
    if (pe_pct is not None and (pe_pct >= EXTREME_HIGH_THRESHOLD or pe_pct <= EXTREME_LOW_THRESHOLD)) or (
        pb_pct is not None and (pb_pct >= EXTREME_HIGH_THRESHOLD or pb_pct <= EXTREME_LOW_THRESHOLD)
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

    # v0.3.1 A4：本次分歧围绕的核心变量——与触发源同段（都在回答「本节要盯什么」），
    # 取代原先挂在 DCF 之后的「核心矛盾小结」结论复述。
    core_vars = _core_variables(dims, collection, val_cache=val_cache)
    if core_vars:
        lines.append("")
        lines.append("**核心变量：** " + "；".join(core_vars) + "。")

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

    old_at = key_diff.get("old_at", "")
    new_at = key_diff.get("new_at", "")
    lines = ["", "### 相对上次调研变化", ""]
    if old_at and new_at:
        # P2-2 收尾（code-review 第四轮）：同报告不得混时区——头部已是
        # 北京时间+(北京时间)，此处原样打印 UTC ISO 会让对比区间偏移 8h
        lines.append(f"对比区间：{fmt_fetched_at(old_at)} → {fmt_fetched_at(new_at)}（本次采集）")
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
    quote = _get_dim_data(dims, "quote")
    price = None
    chg = None
    if isinstance(quote, dict):
        price = coalesce_field(quote, "close", "price")
        chg = quote.get("change_pct")

    pe_pct, pb_pct, pe_zone = _v3_valuation_percentiles(dims, val_cache)
    pe_med, pb_med = _pct_medians(val_cache, dims)

    # LAW 17: 构建含数据的标题 + 段首主旨句
    price_s = f"{price}" if price is not None else ""
    pe_s = f"PE {pe_pct:.1f}% 分位{_pct_median_suffix(pe_med)}" if pe_pct is not None else ""
    loss_flag = _pe_loss_flag(val_cache)
    if pe_s and loss_flag:
        pe_s += f"（{loss_flag}）"
    pb_s = f"PB {pb_pct:.1f}% 分位{_pct_median_suffix(pb_med)}" if pb_pct is not None else ""
    title_parts = [p for p in [price_s, pe_s, pb_s] if p]
    title_suffix = " · ".join(title_parts) if title_parts else "当前状态快照"
    judgment_parts = [s for s in [f"最新价 {price_s}" if price_s else "", pe_s, pb_s] if s]
    judgment = "，".join(judgment_parts) + "，关键数据快照如下。" if judgment_parts else "当前状态快照，关键数据见下方。"

    lines = [f"## 1. {title_suffix}", ""]
    lines.append(f"**结论：** {judgment}")
    lines.append("")

    if isinstance(quote, dict) and price is not None:
        chg_s = f"（{chg:+.2f}%）" if chg is not None else ""
        quote_meta = _get_dim_meta(dims, "quote")
        price_source = quote_meta.get("price_source") or quote_meta.get("source") or "来源未封存"
        price_at = fmt_fetched_at(quote_meta.get("price_fetched_at") or quote_meta.get("fetched_at"))
        time_note = f"；取数 {price_at}" if price_at else ""
        kline = quote.get("kline") or _get_dim_data(dims, "kline") or []
        bar_dates = [str(row.get("trade_date")) for row in kline
                     if isinstance(row, dict) and row.get("trade_date")]
        bar_note = f"；日线截至 {_to_iso_date(max(bar_dates))}" if bar_dates else ""
        lines.append(f"- **最新价:** {price}{chg_s}（{price_source}{time_note}{bar_note}）")
    if pe_pct is not None:
        lines.append(f"- **PE(TTM) 历史分位:** {pe_pct:.1f}%（{pe_zone or '—'}{_pct_median_inline(pe_med)}）")
    if pb_pct is not None:
        lines.append(f"- **PB 历史分位:** {pb_pct:.1f}%{_pct_median_suffix(pb_med)}")

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
                cv_status = "convergence" if ratio >= OCF_COVERAGE_WEAK else "divergence"
            elif np_f < 0 and ocf_f < 0:
                cv_status = "divergence"
            elif np_f == 0 or ocf_f == 0:
                cv_status = "gap"
            else:
                cv_status = "divergence"
            cv_detail = f"净利润 {_fmt_v2(np_v)} vs 经营现金流 {_fmt_v2(ocf)}（覆盖关系）"
            lines.append("")
            lines.append(_cv(cv_status, "CV-1", "净利润 vs 经营现金流", cv_detail, "中（单期财报）"))
        else:
            lines.append("")
            lines.append(_cv(
                "gap", "CV-1", "净利润 vs 经营现金流",
                "经营现金流字段不可得，无法核对覆盖关系", "低",
            ))

    if pe_pct is not None and pb_pct is not None:
        if (pe_pct >= 70 and pb_pct >= 70) or (pe_pct <= 30 and pb_pct <= 30):
            cv3 = "convergence"
            cv3d = (f"PE 分位 {pe_pct:.1f}%{_pct_median_suffix(pe_med)}"
                    f" 与 PB 分位 {pb_pct:.1f}%{_pct_median_suffix(pb_med)} 同向")
        else:
            cv3 = "divergence"
            cv3d = (f"PE 分位 {pe_pct:.1f}%{_pct_median_suffix(pe_med)}"
                    f" 与 PB 分位 {pb_pct:.1f}%{_pct_median_suffix(pb_med)} 方向不一致")
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
    if key_diff:  # 有历史快照即显示对比块（无显著变化时显示状态行，diff_key_snapshots 恒返回 old_at/new_at）
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


# --- 事件采集可得性（渲染层判据的唯一入口） ---
def _events_leg_state(collection: dict) -> str:
    """事件采集的可得性状态，供文案分支使用（**不得由 events 是否为空反推**）。

    - ``ok``：事件非空，且公告腿未明确失败
    - ``notice_failed``：事件非空，但公告腿失败（数据来自分红/股东变动腿）
    - ``no_rows``：确认窗口内无公告事件（**是事实，不是采集缺陷**）
    - ``unavailable``：未挂载或来源腿全失败（采集缺陷）
    """
    events = collection.get("events")
    meta = collection.get("_meta") or {}
    legs = meta.get("events_legs")
    legs = legs if isinstance(legs, dict) else {}

    if events:
        return "notice_failed" if legs.get("notice") == "failed" else "ok"
    if events is None:
        return "unavailable"
    # events == []：「失败」与「确实无」的判据复用 needs_events_backfill，保持单一源
    from ..events import needs_events_backfill

    return "unavailable" if needs_events_backfill(collection) else "no_rows"


def _events_factor_label(events: list, summary: dict) -> str:
    """事件催化行的计数文案（计数**现场聚合**，见 summarize_event_types）。"""
    from ..analysis_templates import event_type_label
    from ..events import summarize_event_types

    agg = summarize_event_types(events)
    parts: list[str] = []
    if agg["substantive"]:
        named = "、".join(
            f"{event_type_label(t)}({c})" for t, c in agg["substantive"][:3]
        )
        if len(agg["substantive"]) > 3:
            named += "…"
        parts.append(f"实质 {agg['substantive_count']}：{named}")
    # 两个低信号桶分列，不合并、不互相代称
    low_parts = []
    if agg["procedural_count"]:
        low_parts.append(f"程序性公告 {agg['procedural_count']} 条")
    if agg["unclassified_count"]:
        low_parts.append(f"未分类公告 {agg['unclassified_count']} 条")
    if low_parts:
        parts.append("、".join(low_parts))

    window = summary.get("window_days", 30)
    return f"近{window}日 {agg['total']}条公告（{'；'.join(parts)}）"


# 同一财年相邻累计期的前一报告期 MMDD（单季拆分用）
_SQ_PREV_MMDD = {"0630": "0331", "0930": "0630", "1231": "0930"}
_SQ_QUARTER = {"0331": "Q1", "0630": "Q2", "0930": "Q3", "1231": "Q4"}


def _v3_single_quarter_row(rows: list[dict], idx: int, field: str) -> float | None:
    """累计行拆分单季值（仅同财年相邻期齐全时）；缺期/跨年 → None。

    日期先经 ``_norm_ed`` 归一（2026-10-07 复检修复）：ISO（2026-06-30）与
    compact（20260630）混合输入下的拆分结果须与全 compact 一致。"""
    if idx < 0 or idx >= len(rows):
        return None
    ed = _norm_ed(str(rows[idx].get("end_date") or ""))
    cur = _safe_num(rows[idx].get(field))
    if cur is None or len(ed) != 8 or not ed.isdigit():
        return None
    mmdd = ed[4:]
    if mmdd == "0331":
        return cur  # Q1 累计即单季
    prev_mmdd = _SQ_PREV_MMDD.get(mmdd)
    if prev_mmdd is None:
        return None
    prev_ed = ed[:4] + prev_mmdd
    for r in rows:
        if _norm_ed(str(r.get("end_date") or "")) == prev_ed:
            base = _safe_num(r.get(field))
            return None if base is None else cur - base
    return None


def _v3_single_quarter_of(rows: list[dict], end_date: str, field: str) -> float | None:
    """指定报告期（按 end_date 查找）的单季值；行缺失/缺中间期 → None。

    查找同样按 ``_norm_ed`` 归一后比较（ISO/compact 等价）。"""
    target = _norm_ed(str(end_date or ""))
    if len(target) != 8 or not target.isdigit():
        return None
    for i, r in enumerate(rows):
        if _norm_ed(str(r.get("end_date") or "")) == target:
            return _v3_single_quarter_row(rows, i, field)
    return None


def _v3_period_label(end_date: str) -> str:
    """20260630 / 2026-06-30 → 「2026Q2」；无法识别时原样返回。"""
    raw = str(end_date or "")
    ed = _norm_ed(raw)
    if len(ed) == 8 and ed.isdigit() and ed[4:] in _SQ_QUARTER:
        return f"{ed[:4]}{_SQ_QUARTER[ed[4:]]}"
    return raw


def _v3_previous_quarter_end(end_date: str) -> str:
    """日历上紧邻的前一报告期末：2026Q1 → 2025-12-31；其余 → 同年上一 MMDD。

    MC-01（2026-10-07 独立探针）：环比比较的两端必须是**相邻日历季度**——
    两个 Q1（相隔一年）不是环比，跨年缺口也要停笔。日期先经 ``_norm_ed``
    归一（ISO/compact 等价，2026-10-07 复检修复）。"""
    ed = _norm_ed(str(end_date or ""))
    if len(ed) != 8 or not ed.isdigit():
        return ""
    mmdd = ed[4:]
    if mmdd == "0331":
        return f"{int(ed[:4]) - 1}1231"
    prev_mmdd = _SQ_PREV_MMDD.get(mmdd)
    return ed[:4] + prev_mmdd if prev_mmdd else ""


def _v3_recent_profit_direction(fin_list: list[dict]) -> tuple[str, str, str]:
    """近期净利润方向（REV-01）：只做同口径比较，禁止跨累计期直接相减。

    返回 ``(direction, label, basis)``，方向不可得时三者均为空串：

    1. **单季环比**（优先）：最新期可拆单季、且**日历前一个季度**也可拆
       （同财年相邻累计期差，或跨年 Q1 由上年 Q4 差 = FY−9M 得到）时，
       比较两个相邻单季，label=「净利润环比」，basis 注明两端（如
       「2026Q2 vs 2026Q1」；跨年 Q4→Q1 为「2026Q1 vs 2025Q4」）。
    2. **同报告期同比**：单季环比不可得（缺中间期/跨年缺基期/两个 Q1 相隔
       一年等）时，找上年同 MMDD 行比较累计值，label=「净利润同比」，basis
       注明两端报告期。
    3. 两者皆缺 → 方向不可得（中性 + 缺口标注），不得用相邻行（含同报告期
       修订重复行）硬比。

    原反例（REV-01，600519 collection 175）：2026 半年累计 445.17 亿与一季度
    累计 272.43 亿直接相减得「↑正向」；按单季拆分二季度为 172.74 亿，环比
    -36.59%（集合含一季度）。MC-01 追加反例：仅凭「两个值各可拆单季」不能
    标环比——20250331 vs 20260331（相隔一年）须退回同报告期同比，年份缺口
    （20240331 vs 20260331）缺基期须停笔。修订重复行（同 end_date）先按
    ann_date 去重，否则相邻比较会退化成同一行自比（600036 collection 172
    实测「→中性」）。

    2026-10-07 复检修复：行日期先经 ``_norm_ed`` 归一（保留不可解析行原样），
    排序/最新期/同比 basis 在 ISO 与 compact 混合输入下与全 compact 一致；
    去重与缺期/跨年停笔纪律保持不变。
    """
    normed: list[dict] = []
    for r in fin_list or []:
        ed = _norm_ed(str(r.get("end_date") or ""))
        cur_ed = str(r.get("end_date") or "")
        normed.append({**r, "end_date": ed} if ed and ed != cur_ed else r)
    rows = dedupe_by_end_date(sort_kline_asc(normed))
    if not rows:
        return "", "", ""
    latest = rows[-1]
    ed_latest = str(latest.get("end_date") or "")
    s_now = _v3_single_quarter_row(rows, len(rows) - 1, "net_profit")
    prev_end = _v3_previous_quarter_end(ed_latest)
    s_prev = _v3_single_quarter_of(rows, prev_end, "net_profit") if prev_end else None
    if s_now is not None and s_prev is not None:
        d = ("↑正向" if s_now > s_prev else "↓负向" if s_now < s_prev else "→中性")
        basis = (
            f"{_v3_period_label(ed_latest)} vs {_v3_period_label(prev_end)} 单季"
        )
        return d, "净利润环比", basis
    yoy = find_yoy_row(rows, latest)
    if yoy is not None:
        cur = _safe_num(latest.get("net_profit"))
        base = _safe_num(yoy.get("net_profit"))
        if cur is not None and base is not None:
            d = ("↑正向" if cur > base else "↓负向" if cur < base else "→中性")
            basis = (
                f"{str(latest.get('end_date'))} vs {str(yoy.get('end_date'))} 同报告期"
            )
            return d, "净利润同比", basis
    return "", "", ""


# --- _section_dynamic_drivers ---
def _section_dynamic_drivers(
    collection: dict, symbol: str, dims: dict[str, dict], market_structure: dict,
    *, val_cache: dict | None = None,
) -> str:
    # LAW 17: 构建含价格变化数据的标题
    chg, window = _v3_price_change(dims)
    window_label = _v3_price_window_label(window)
    chg_pct_s = f"{chg:+.2f}%" if chg is not None else "不可得"
    chg_title_s = f"{window_label} {chg_pct_s}" if chg is not None else ""
    title_suffix = f"动态驱动 · {chg_title_s}" if chg_title_s else "动态驱动分析"
    judgment = f"{chg_title_s} 价格变化，以下为候选驱动因子分析。" if chg_title_s else "以下为动态驱动的候选解释。"

    lines = [f"## 2. {title_suffix}", ""]
    lines.append(f"**结论：** {judgment}")
    lines.append("")
    lines.append(f"{window_label}涨跌幅：**{chg_pct_s}**（采集: {fmt_fetched_at(collection.get('fetched_at', ''))[:10]}）")
    lines.append("")
    lines.append("### 候选解释（上限 5 条）")
    lines.append("")
    candidates = _v3_build_candidate_explanations(
        chg=chg,
        window_label=window_label,
        chg_s=chg_pct_s,
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
        # 此前写「需 WebSearch 或 anns 数据补充」——`anns` 维度并不存在（采集器与
        # 默认维度表里都没有），属悬空引用。改写后须**与本节其它行保持一致**：
        # 公告腿未取到、或窗口内确实无公告时，都不得断言「公告已采集并分类」。
        _state = _events_leg_state(collection)
        lines.append(
            "⚠️ **尚无候选解释的部分：** 候选解释由引擎因子生成"
            + {
                "ok": "，尚未消费公告正文（公告已采集并分类，见「事件催化」行）",
                "notice_failed": "，尚未消费公告正文（公告接口未取到，现有事件来自"
                                 "分红/股东变动来源，见「事件催化」行）",
                "no_rows": "；本次窗口内无公告事件（见「事件催化」行）",
                "unavailable": "；本次公告事件未取到（见「事件催化」行的不可得说明）",
            }[_state]
            + "；需外部检索补充的政策/传闻类事件应标注为待核验，不在此处臆造解释。"
        )
        lines.append("")
    lines.append("### 多因子驱动矩阵")
    lines.append("")
    lines.append("| 因子类别 | 具体信号 | 方向 | 强度 | 数据来源 |")
    lines.append("|---------|---------|------|------|---------|")

    factors: list[DriverFactor] = []
    fin = _get_dim_data(dims, "financials")
    fin_dir, fin_label, fin_basis = "→中性", "", ""
    if fin and isinstance(fin, list) and len(fin) >= 2:
        # REV-01：只做同口径比较（单季拆分环比 → 同报告期同比），
        # 不得把相邻累计行直接相减（原反例：半年累计 vs 一季度累计）。
        _d, _label, _basis = _v3_recent_profit_direction(fin)
        if _d:
            fin_dir, fin_label, fin_basis = _d, _label, _basis
            factors.append(DriverFactor("基本面", fin_label, fin_dir, "⚠️", "financials"))
        else:
            factors.append(DriverFactor("基本面", "净利润（可比期不足）", "→中性", "❓", "financials"))
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
        # P0-1：源停更陈旧（staleness_note）时输出原因，区分「数据不足」与「停更」
        stale_note = nb.get("staleness_note") if isinstance(nb, dict) else None
        factors.append(_v3_driver_unavailable(
            f"资金（北向）{('：' + stale_note) if stale_note else ''}"
        ))

    mf = market_structure.get("moneyflow")
    mf_net, mf_key = resolve_moneyflow(mf)
    if mf_net is not None:
        d = "↑正向" if mf_net > 0 else ("↓负向" if mf_net < 0 else "→中性")
        factors.append(DriverFactor(
            "资金（全档）", f"{moneyflow_signal_label(mf_key)} {fmt_amount(mf_net)}", d, "⚠️", mf.get("source", "") if isinstance(mf, dict) else "",
        ))
    else:
        factors.append(_v3_driver_unavailable("资金（全档）"))

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
    # fin_dir/fin_label 在因子矩阵处已按同口径比较赋值（REV-01），不在此重置。
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

    # fin_dir/fin_label 已在因子矩阵处按同口径比较得出（REV-01），此处不再重算。

    # 事件催化因子 — from collection["events"]（计数**现场聚合**，不读快照 top_types：
    # 后者只存信号榜前 5 且旧快照仍是历史口径，会让括号内数字与总数对不上）
    events_list = collection.get("events") or []
    if events_list and isinstance(events_list, list) and len(events_list) > 0:
        summary = (collection.get("_meta") or {}).get("events_summary") or {}
        factors.append(DriverFactor(
            "事件催化", _events_factor_label(events_list, summary), "→中性", "⚠️",
            "akshare stock_individual_notice_report",
        ))
    else:
        # 空 events 有两种相反含义，不得混同：采集缺陷 vs 窗口内确实无公告
        no_rows = _events_leg_state(collection) == "no_rows"
        factors.append(DriverFactor(
            "事件催化",
            "窗口内无公告事件（非采集失败）" if no_rows
            else "事件数据暂不可用（akshare 公告接口未返回数据）",
            "—", "—",
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
            f"技术趋势 {ma_dir} 与{fin_label}方向 {fin_dir} 一致"
            f"（{fin_basis}；财报与价格窗口不同，仅作方向对照）", "中",
        ))
    elif ma_dir != "→中性" and fin_dir != "→中性" and ma_dir != fin_dir:
        lines.append(_cv(
            "divergence", "CV-6", "MA 趋势 vs 近期业绩方向",
            f"技术趋势 {ma_dir} 与{fin_label}方向 {fin_dir} 不一致"
            f"（{fin_basis}；财报与价格窗口不同，仅作方向对照）", "中",
        ))
    else:
        lines.append(_cv(
            "gap", "CV-6", "MA 趋势 vs 近期业绩方向",
            "技术或业绩方向数据不足", "低",
        ))

    lines.append("")
    dominant = _v3_pick_dominant_factor(rows)
    # R15 round-8 全文补齐（Codex supplement L290）：该行由阈值规则（强度权重）
    # 筛出，不是因果归因——标签补「候选线索」限定。
    lines.append(f"→ **主导因子（候选线索，阈值规则筛出，非因果归因）:** {dominant}")
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
    analysis: list[dict] | None = None,
) -> str:
    return build_participant_behavior_section(
        collection, symbol, market_structure, dims, analysis=analysis,
    )


def _v3_rf_series_key(src: str) -> str:
    """10Y 来源串归一（小写、去掉「(CN10Y)」等参数后缀），用于判断 ERP 对齐
    序列与 cn10y 现值是否同源；空串/仅后缀时不构成同源。"""
    return src.strip().lower().split("(", 1)[0].strip()


# --- _section_market_structure ---
def _section_market_structure(
    collection: dict, symbol: str, market_structure: dict, *, val_cache: dict | None = None,
) -> str:
    # LAW 17: 构建含行业数据的标题
    sw = market_structure.get("sw_index") or {}
    sw_ret = sw.get("return_20d_pct")
    ret_s = f"行业 20 日 {sw_ret:+.2f}%" if sw_ret is not None else ""
    title_suffix = f"市场结构 · {ret_s}" if ret_s else "市场结构分析"
    judgment = f"申万行业近 20 日涨跌幅 {ret_s}，市场结构与参与者行为见下方。" if ret_s else "市场结构、产业链位置与参与者行为分析。"

    lines = [f"## 3. {title_suffix}", ""]
    lines.append(f"**结论：** {judgment}")
    lines.append("")
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
    # 模块 3 即使只有单侧资金源，也须展示可得值与不可得原因。
    if nb or mf_net is not None:
        lines.append("")
        lines.append("### 资金态度")
        if nb:
            nb_src = nb.get("source", "northbound")
            lines.append(f"- 北向个股资金流（{nb_src}）{northbound_label(nb)}")
        else:
            lines.append("- 北向个股资金流：不可得，无法交叉验证 [来源: northbound]")
        if mf_net is not None:
            lines.append(
                f"- 全档资金（moneyflow）{moneyflow_signal_label(mf_key)}: {fmt_amount(mf_net)}"
            )
        else:
            lines.append("- 全档资金（moneyflow）：不可得，无法交叉验证 [来源: moneyflow]")
        # 口径：mf 侧取自 resolve_moneyflow 默认键（net_sum_5d/10d/
        # net_mf_amount），全部是**全档**净额，故称「全档资金」；
        # 「主力」（大单+特大单）是另一口径，见 participant_scan._MF_LABELS。
        relation = flow_direction_relation(
            nb.get("net_sum_10d") if isinstance(nb, dict) else None, mf_net,
        )
        cv4d = {
            "convergence": "北向与全档资金净流入方向一致",
            "divergence": "北向与全档资金净流入方向相反",
            "gap": "资金数据不完整",
            "unavailable": "北向或全档资金数据不可用，无法交叉验证",
        }[relation]
        lines.append(_cv(relation if relation != "unavailable" else "gap",
                         "CV-4", "北向 vs 全档资金", cv4d, "中"))

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
            # REV-02（2026-10-07 主线收尾）：标签必须绑定**实际参与 ERP 运算的
            # 整条 10Y 序列**（erp.y10_source/rf_currency），而不是现值闸门
            # （resolve_rf）按 cn10y 优先挑的现值——两者可并存且币种不同。
            # 反例（封存 175）：ERP 2.27% / 分位 1.4% 由 FRED.DGS10（USD）序列
            # 算出，同时存在独立 cn10y 现值 1.682%；旧实现把该行标成「中国
            # 10Y 国债」口径。旧封存快照缺 y10_source/rf_currency 时按来源
            # 字符串推断（FRED=USD，bond_zh/CN10Y=CNY）。人民币现值的参与
            # 说明按证据三分（2026-10-07 复检修复）：USD 序列且来源已证明
            # → 明示「未参与」；CNY 且与 ERP 序列同源（_v3_rf_series_key 归一
            # 比较）→「同源人民币序列，按交易日对齐」（现值是否参与取决于
            # 对齐，不断言）；来源/关系未确认 → 只披露现值，不断言参与与否。
            combined = str(erp.get("source") or "")
            series_src = str(erp.get("y10_source") or "")
            if not series_src and "+" in combined:
                series_src = combined.split("+", 1)[1].strip()
            cur = str(erp.get("rf_currency") or "")
            if not cur and series_src:
                up = series_src.upper()
                if "FRED" in up:
                    cur = "USD"
                elif "CN10Y" in up or "BOND_ZH" in up:
                    cur = "CNY"
            y10_note = f"；10Y 国债来源: {series_src}" if series_src else ""
            if series_src and cur == "USD":
                y10_note += "（美元口径——ERP 对齐序列与 A 股口径不一致，读数仅供参考、不作方向解读）"
            elif series_src and cur != "CNY":
                y10_note += "（来源/币种未确认——无法核对与 A 股口径一致性，仅供参考）"
            if erp.get("cn10y") is not None:
                cn_src = str(erp.get("cn10y_source") or "") or "来源未标注"
                same_series = bool(series_src) and _v3_rf_series_key(
                    str(erp.get("cn10y_source") or "")) == _v3_rf_series_key(series_src)
                if cur == "USD" and series_src and not same_series:
                    # ERP 对齐序列已证明为 USD（USD 分支的 cn10y 必为独立 CNY
                    # 序列）：现值未参与本 ERP 计算。
                    tail = "未参与本 ERP 计算"
                elif cur == "CNY" and same_series:
                    # 同源人民币序列：现值是否参与取决于交易日对齐，不作断言。
                    tail = "同源人民币序列，按交易日对齐"
                else:
                    # 来源/关系未确认：不断言参与与否。
                    tail = "与 ERP 序列关系未确认"
                y10_note += f"；人民币 10Y 现值 {erp.get('cn10y')}%（{cn_src}，{tail}）"
            lines.append(
                f"- ERP（沪深300）: {erp.get('raw', '-')}%，5年分位 {erp.get('percentile_5y', '-')}%"
                f"{partial_note}{y10_note} [对齐样本 {erp.get('erp_days', '-')} 日]"
            )

    pe_pct, _, _ = _v3_valuation_percentiles(_index_dims(collection), val_cache)
    _pe_med7, _ = _pct_medians(val_cache, _index_dims(collection))
    mf_out = mf_net
    cv7 = _v3_cv7_block(pe_pct, mf_out, _pe_med7)
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
            partial = "（采样不完整，仅展示可用窗口分位）" if pcr.get("partial") else ""
            pcr_date = _to_iso_date(str(pcr.get("current_date") or "")) or "日期未封存"
            stale_note = ("；未纳入当期交叉验证"
                          if not _ru.pcr_is_current_for_snapshot(pcr, collection) else "")
            pct_5y = pcr.get("percentile_5y")
            pct_60d = pcr.get("percentile_60d")
            if pct_5y is not None and pct_60d is not None and pct_5y != pct_60d:
                pct_s = f"5年分位 {pct_5y}%，60日分位 {pct_60d}%"
            else:
                pct_s = f"分位 {pct_5y if pct_5y is not None else pct_60d if pct_60d is not None else '-'}"
            sample_note = (
                f"；五年均匀样本 {pcr.get('history_days', '-')}"
                f"/{pcr.get('history_sample_target', '-')} 点"
                f"；近期窗口 {pcr.get('recent_observed_days', '-')}"
                f"/{pcr.get('recent_days', '-')} 个交易日"
                if pcr.get("history_sample_target") is not None else ""
            )
            lines.append(
                f"- **50ETF 认沽认购比:** {pcr.get('ratio', '-')}，"
                f"{pct_s}{partial}（截至 {pcr_date}{sample_note}{stale_note}） "
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
            partial = bool(nhr.get("partial"))
            sample = f"{nhr.get('sample_size', '-')}/{nhr.get('sample_target', 30)}"
            pct = nhr.get("percentile_60d") if not partial else None
            note = "；样本不完整，不作市场广度判断" if partial else ""
            lines.append(
                f"- **创新高个股占比:** {nhr.get('ratio_pct', '-')}%"
                f"，60日分位 {f'{pct}%' if pct is not None else '—'}"
                f" [样本 {sample}{note}] [来源: {nhr.get('source', 'tushare.daily')}]"
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
        ms_evidences.append(("❓", "北向/全档资金数据不完整"))
    if erp:
        erp_desc = f"ERP {erp.get('raw', '-')}%（5年分位 {erp.get('percentile_5y', '-')}%）"
        if erp.get("partial"):
            erp_desc += "，样本日不足"
        ms_evidences.append(("⚠️", erp_desc))
    elif to:
        ms_evidences.append(("❓", "ERP 不可得，仅换手数据可参考"))
    if pcr:
        pct = pcr.get("percentile_5y") or pcr.get("percentile_60d")
        pcr_date = _to_iso_date(str(pcr.get("current_date") or "")) or "日期未封存"
        stale_note = ("；不作当期判断"
                      if not _ru.pcr_is_current_for_snapshot(pcr, collection) else "")
        ms_evidences.append((
            "⚠️",
            f"50ETF 认沽认购比 {pcr.get('ratio', '-')}（分位 {pct if pct is not None else '-'}%；"
            f"截至 {pcr_date}{stale_note}）",
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
    # A-6（code-review 第五轮）：financials 不在 collection 顶层——须经 dims
    # 索引（dimensions → _index_dims，同 _section_dynamic_drivers 511 行模式）。
    # 顶层键从未被 _assemble_result 设置，此前恒 None → 「本公司数据不足」
    # 永久污名（即便财务数据齐备）。
    fin_rows = None
    if isinstance(collection, dict):
        fin_dim = _get_dim_data(_index_dims(collection), "financials")
        fin_rows = fin_dim if isinstance(fin_dim, list) else None
    if fin_rows:
        fin_sorted = sort_kline_asc(fin_rows)
        if fin_sorted:
            company_gm = _fin_field_num(fin_sorted[-1], *GROSS_MARGIN_FIELDS)
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
    # 展示命中的关键词（chain_matched_on，如「锂电池」）；raw_industry 为采集侧
    # 传入的原始名（Tushare 粗分类，如「电气设备」），仅用于来源标注与降级提示。
    raw_industry = chain.get("industry") or ""
    industry = chain.get("chain_matched_on") or raw_industry
    position = chain.get("chain_position")
    upstream = [s for s in (chain.get("upstream") or []) if s]
    downstream = [s for s in (chain.get("downstream") or []) if s]

    if not industry and not position and not upstream and not downstream:
        return ""

    lines = ["### 3f. 价值链位置 + 利润池分布", ""]

    if not position and not upstream and not downstream:
        lines.append(
            f"数据不足：行业「{raw_industry or industry}」在 `lib.chain._CHAIN_MAP` 中暂无产业链映射，"
            "无法渲染价值链图（仅覆盖新能源汽车/电气/锂电池/电池/电力设备/光伏设备/汽车/"
            "医药/白酒/银行/房地产/半导体/新能源/化工/钢铁/食品/计算机/通信/电子等行业关键词）。"
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

    # F0-8 修复：金融行业豁免——银行/非银的 90%+ 资产负债率与单季累计
    # ROE 是经营模式常态，不是否决信号。行业键双兼容见 _extract_industry。
    basic_dim = (dims or {}).get("basic_info") or {}
    basic_data = basic_dim.get("data") if isinstance(basic_dim, dict) else None
    industry = _extract_industry(basic_data)
    financial_industry = industry in ("银行", "非银金融", "保险", "证券", "多元金融")

    fin_dim = (dims or {}).get("financials") or (collection or {}).get("financials") or {}
    fin_list = fin_dim.get("data") if isinstance(fin_dim, dict) else fin_dim
    if not isinstance(fin_list, list) or not fin_list:
        return result

    rows = sort_kline_asc(fin_list)
    if financial_industry:
        result["display_lines"].append(
            "- ℹ️ 金融行业豁免：资产负债率 >90% 与近 3 期 ROE <5% 两条阈值"
            "对银行/非银金融不适用（高杠杆与单季累计 ROE 为经营常态），已跳过。"
        )

    def _append(level: str, line: str) -> None:
        result[level].append(line)
        tag = "硬触发" if level == "hard_triggers" else "软触发"
        result["display_lines"].append(f"- {tag}: {line}")

    # 1. FCF 累计为负（优先 fcff，字段不可得时退化为经营现金流）
    # 口径（code-review 第四轮）：fina_indicator 的 fcff / n_cashflow_act 为
    # 报告期累计（Q1→H1→3Q→FY 逐期叠加同一财年）——全量求和重复计数且季度
    # 单期为负可误否决；对齐 quality_check._metric_fcf_5y：仅取年报（1231）行。
    annual_rows = [
        r for r in rows if str(_norm_ed(str(r.get("end_date") or ""))).endswith("1231")
    ]
    fcff_vals = [
        v for v in (_fin_field_num(r, "fcff") for r in annual_rows) if v is not None
    ]
    if len(fcff_vals) >= 3:
        total = sum(fcff_vals)
        if total < 0:
            _append(
                "hard_triggers",
                f"⚠️ 近 {len(fcff_vals)} 个年报期 FCFF 累计为负（合计 {total:.2f}）"
                "[来源: financials.fcff]"
            )
    else:
        ocf_vals = [
            v for v in (_fin_field_num(r, "n_cashflow_act", "ocf") for r in annual_rows)
            if v is not None
        ]
        if len(ocf_vals) >= 3 and sum(ocf_vals) < 0:
            _append(
                "hard_triggers",
                f"⚠️ FCFF 字段不可得，退化以经营现金流近 {len(ocf_vals)} 个年报期"
                f"累计为负（合计 {sum(ocf_vals):.2f}）代理观察[来源: financials.n_cashflow_act]"
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

    # 3. 资产负债率 >90% 且未见改善（金融行业豁免）
    if not financial_industry:
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

    # 4. 近 3 期 ROE 连续 <5%（金融行业豁免——单季累计 ROE 早期季度天然 <5%）
    if not financial_industry:
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

    合规红线（references/financials.md F-4 六关评分速览规范，最容易违规的一条）：**无通过/不通过二元判决，
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
    # F0-6 修复：coverage = {"auto": N, "total": 17}，旧实现 sum() 得 16+17=33
    # 与 §7 的「自动判定覆盖 16/17」口径矛盾；统一为 auto/total 两数字。
    risk_data = _v3_build_risk_report(collection, dims, market_structure, val_cache=val_cache)
    triggered = risk_data.get("triggered_count", 0) or 0
    coverage = risk_data.get("coverage") or {}
    if isinstance(coverage, dict) and coverage.get("total"):
        total_signals = coverage["total"]
        auto_covered = coverage.get("auto", total_signals)
        risk_desc = (
            f"触发 {triggered} 项定量风险信号（自动判定覆盖 {auto_covered}/{total_signals}）"
            " [来源: lib.risk_scanner.risk_report]"
        )
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
        "不代表买卖或持仓的行动判断。"
    )
    lines.append("")
    return "\n".join(lines)


# --- _section_events_timeline ---
def _section_events_timeline(
    collection: dict, analysis: list[dict] | None = None,
) -> str:
    """事件时间线（模块 3-3b 过渡段）。

    渲染 events 列表为时间降序表格，并附加 Template B 事件分类摘要。

    analysis（v0.3.0 fix③）：命中 event_classification 槽位的段替换
    「待 Claude 验证」表头占位（QC completion-template-placeholder 的
    error 级命中项），并在分类列表后附 [分析]。
    """
    events_all = collection.get("events") or []
    if not events_all or not isinstance(events_all, list) or len(events_all) == 0:
        return ""

    from ..analysis_templates import event_type_label
    from ..events import event_table_fingerprint

    lines = ["## 3a. 事件时间线", ""]

    # 按 date 降序排列
    sorted_events = sorted(
        events_all,
        key=lambda e: str(e.get("date", "")),
        reverse=True,
    )
    shown = sorted_events[:15]

    lines.append("| 日期 | 类型 | 公告标题 | 涉及维度（类型默认） |")
    lines.append("|------|------|---------|---------|")
    row_cells: list[list[str]] = []
    for ev in shown:
        date = str(ev.get("date", ""))
        # 中文标签经 taxonomy 单一源（与因子矩阵同一函数），不再打印英文类型键
        etype = event_type_label(str(ev.get("type", "other")))
        title = str(ev.get("title", ""))
        # R13（2026-10-05）：影响维度/持续性质原按事件类型默认值直出，读作
        # 已核影响结论（反例：银行「短期扰动」与未读原文的治理事件）。现只
        # 呈现分类线索：维度列注明「类型默认」（新采集字段 dimension_hint，
        # 封存旧字段 impact_dimension 兼容读取）；持续性质列撤下——影响方向
        # 与持续性须以公告原文核验后写入事件分析段，不在表内断言。
        impact = str(ev.get("dimension_hint") or ev.get("impact_dimension") or "")
        # Trim long titles for table display
        if len(title) > 50:
            title = title[:47] + "..."
        # Escape pipe chars
        title = title.replace("|", "/")
        row_cells.append([date, etype, title, impact])

    hide_count = max(0, len(sorted_events) - 15)
    if hide_count > 0:
        row_cells.append(
            ["...", "...", f"（另有 {hide_count} 条事件未展示）", "...", "..."])
    for cells in row_cells:
        lines.append("| " + " | ".join(cells) + " |")

    lines.append("")
    # R14（2026-10-07 主线收尾）：尾注附**行指纹**——检查器从报告实际行重算
    # 并比对，把行级结构豁免收紧为「行集合与该尾注自洽」（完整性校验）；仅
    # 复制表头/尾注/条数的外形不再放行（Codex round-8 event_title_assertion 的
    # footer 变体）。指纹**不证明来源身份**（算法公开、可对自造行重算）：
    # 来源身份的关闭在 analysis_schema 入口（禁止自由分析伪造引擎事件表元数据）
    # 与 report_qc 块位规则（只豁免事件段首个引擎表块，人工分析区不豁免）。
    fp = event_table_fingerprint(row_cells)
    lines.append(
        f"[来源: akshare stock_individual_notice_report / {len(sorted_events)} 条事件"
        f"；行指纹 sha256:{fp}]")
    lines.append("")

    # ---- Template B classification cards ----
    cards = _get_analysis_cards(collection)
    event_classifications = cards.get("event_classifications") or []
    if event_classifications and isinstance(event_classifications, list):
        from lib.analysis_schema import (
            EVENTS_HOST_KEYS,
            find_section,
            mark_inline_consumed,
        )
        _ec = find_section(analysis, EVENTS_HOST_KEYS)
        # 有分析段 → 表头改为「已复核」，并声明列表本身即 [事实] 块
        # （QC structure-analysis-without-fact 要求 [分析] 前有 [事实]）；
        # 无分析段 → 保持原占位串，完成度门禁照常拦截未填报告。
        lines.append(
            "**事件分类摘要**（规则分类，已由本次分析复核条目；"
            "标题仅作原文检索线索，不推断影响方向）[来源: akshare "
            "stock_individual_notice_report 事件分类规则]:"
            if _ec else
            "**事件分类摘要**（规则推断，待 Claude 验证）:"
        )
        lines.append("")
        if _ec:
            # 窗口取自实际采集参数（--deep 为 90 日），不得硬编码 30——
            # 与 740 行「事件催化」因子同源（events.py 落 `events_window_days`
            # 与 `events_summary.window_days`）。
            _emeta = collection.get("_meta") or {}
            _edays = (_emeta.get("events_window_days")
                      or (_emeta.get("events_summary") or {}).get("window_days")
                      or 30)
            lines.append(f"**[事实]** 近 {_edays} 日公告按类型归类如下：")
            lines.append("")
        for idx, ec in enumerate(event_classifications):
            ev_type = ec.get("event_label") or ec.get("event_type") or "其他"
            ev_count = len(ec.get("events") or ())
            label_field = "event_label" if ec.get("event_label") else "event_type"
            lines.append(
                f"  - **{ev_type}** ({ev_count}条) "
                f"[来源: _meta.analysis_cards.event_classifications.{idx}.{label_field}] "
                "[来源: Python calc: len(ec.get('events') or ())]"
            )
        lines.append("")
        _ec_amd = str((_ec or {}).get("analysis_md") or "").strip()
        if _ec_amd:
            mark_inline_consumed(collection, _ec)
            lines.append("**[分析]**")
            lines.append("")
            lines.append(_ec_amd)
            lines.append("")

    # Industry / market event placeholders
    # C3：封存 _meta 携带旧文案（「待补来源」）——渲染层做术语映射，避免
    # lint placeholder-tofill 把封存旧词当未填占位；新采集产出新词，映射恒等。
    def _note_display(note: str) -> str:
        return str(note).replace("待补来源", "来源缺口")
    ind_note = collection.get("_meta", {}).get("industry_events_note")
    mkt_note = collection.get("_meta", {}).get("market_events_note")
    if ind_note:
        lines.append(f"⏭️ **行业事件**: {_note_display(ind_note)}")
    if mkt_note:
        lines.append(f"⏭️ **市场事件**: {_note_display(mkt_note)}")
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
        date = _to_iso_date(str(r.get("ann_date", "")))
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
            rdate = _to_iso_date(str(r.get("ann_date", "")))
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
                # akshare dom_basis_rate 为小数（-0.063 = -6.3%），×100 渲染
                basis_s = f"{basis_n * 100:.2f}%" if basis_n is not None else "—"
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
    *, financial_industry: bool = False,
) -> str:
    """盈利结构结论：一句话判断。金融行业豁免高杠杆虚增条款（R4 同链）。

    R14/C3（2026-10-05）：毛利率→定价权、毛利率低→差异化不足属代理→归因
    表述——降级为阈值读数 + 须另核验，数值原料（阈值与字段）保留。
    """
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
            parts.append("毛利率较高（≥40% 阈值读数）——定价权与竞争格局须结合行业证据，本卡不单独裁决")
        elif gm >= 20:
            parts.append("毛利率处于中等水平（20%–40% 阈值读数）")
        else:
            parts.append("毛利率偏低（<20% 阈值读数）——差异化程度须结合行业证据")
    if roe is None and gm is None:
        return "盈利结构数据不足，无法形成有效判断"
    if (not financial_industry and debt_ratio is not None and debt_ratio > 70
            and roe is not None and roe > 15):
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
        if cf_ratio >= OCF_COVERAGE_EXCELLENT:
            base = "经营现金流对净利润覆盖充分（≥1.0；覆盖关系指标，不单独构成利润质量或持续性结论）"
        elif cf_ratio >= OCF_COVERAGE_GOOD:
            base = "经营现金流对净利润基本覆盖（0.8-1.0；覆盖关系指标，不单独构成利润质量结论）"
        elif cf_ratio >= OCF_COVERAGE_WEAK:
            base = "经营现金流对净利润覆盖偏低（0.5-0.8），需结合现金流量表构成复核"
        else:
            base = "经营现金流对净利润覆盖不足（<0.5），需结合现金流量表构成复核"
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
    *, financial_industry: bool = False,
) -> str:
    """资产负债与扩产路径结论：一句话判断。

    R4 同链（2026-10-04）：金融行业（银行/非银）高负债率与高权益乘数是
    负债经营模式特征，工商企业阈值判断不适用（否则与同报告口径说明冲突）。
    """
    parts = []
    if debt_ratio is not None:
        if financial_industry:
            parts.append("资产负债率为金融行业负债经营特征（不适用工商企业杠杆阈值）")
        elif debt_ratio >= 70:
            parts.append("资产负债率较高（>70%），财务杠杆读数偏大")
        elif debt_ratio >= 50:
            parts.append("资产负债率适中（50%-70%），杠杆水平读数合理")
        else:
            parts.append("资产负债率较低（<50% 阈值读数），财务结构稳健")
    if em is not None and not financial_industry:
        if em > 3:
            parts.append("权益乘数偏高，扩产依赖外部融资")
        elif em < 1.5:
            parts.append("权益乘数偏低（读数）")
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
    from ..render_icons import (
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
    # F0-9/C1-a：同报告期去重（多源/重复行、修订行）——统一走 lib.financials
    # 的 dedupe_by_end_date（ann_date 最大者优先；ann_date 缺失时等价于原
    # 「保留先出现行」规则）。
    rows = dedupe_by_end_date(sort_kline_asc(fin_list))[-8:]
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


# --- 同报告期序列（C1-a）---
_PERIOD_CALIBER = {"0331": "一季报", "0630": "中报", "0930": "三季报", "1231": "年报"}


def _same_period_fin_rows(rows: list[dict]) -> tuple[list[dict], str]:
    """按「同报告期类型」构造可比序列（C1-a），返回 (rows, 口径标签)。

    年报 ≥3 期 → 用年报（波动/规模判断惯用窗口）；否则取与最新报告期同 MMDD
    的行（≥3 期时可用）；再否则返回空列表——调用方输出数据不足，**禁止混期
    计算**（混算会把年报 ROE 与半年/季累计 ROE 混在一起，系统性放大方差，
    600519 反例：混算 CV=0.48）。rows 需已去重且按 end_date 升序。
    """
    annual = [r for r in rows if _norm_ed(str(r.get("end_date") or "")).endswith("1231")]
    if len(annual) >= 3:
        return annual, "年报"
    if not rows:
        return [], ""
    mmdd = _norm_ed(str(rows[-1].get("end_date") or ""))[4:]
    if mmdd:
        same = [r for r in rows if _norm_ed(str(r.get("end_date") or ""))[4:] == mmdd]
        if len(same) >= 3:
            return same, _PERIOD_CALIBER.get(mmdd, "同报告期")
    return [], ""


# --- _canvas_scale_effect ---
def _canvas_scale_effect(fin_list: list[dict]) -> tuple[float | None, str, list[str]]:
    """规模效应：同报告期序列的营收增长 vs 毛利率变化关系推断。

    C1-a：序列先去除同报告期修订重复行，再用同报告期类型序列（年报优先）——
    禁止跨期混比（旧实现取混期首尾，年报营收对半年累计会算出跨期伪"下滑"）。
    """
    if not fin_list:
        return None, "数据不足：缺少财务数据，无法判断规模效应", []
    rows = dedupe_by_end_date(sort_kline_asc(fin_list))
    series, caliber = _same_period_fin_rows(rows)
    if len(series) < 3:
        return None, (
            "数据不足：同报告期序列不足 3 期（年报/同报告期各需 ≥3 期），"
            "无法判断规模效应"
        ), []
    pairs = [
        (_fin_field_num(r, "revenue"), _fin_field_num(r, *GROSS_MARGIN_FIELDS))
        for r in series
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
            f"近 {len(valid)} 期{caliber}营收增长 {rev_growth:+.1f}%，同期毛利率变化 {margin_change:+.2f}pp"
            f"（口径：{caliber}；未随规模扩大而下降），呈现规模效应特征"
        )
    elif rev_growth > 0:
        score = 40.0
        note = (
            f"近 {len(valid)} 期{caliber}营收增长 {rev_growth:+.1f}%，但毛利率下降 {margin_change:+.2f}pp"
            f"（口径：{caliber}），规模效应证据较弱（可能被价格竞争或成本上升抵消）"
        )
    else:
        score = 20.0
        note = (
            f"近 {len(valid)} 期{caliber}营收未见增长（{rev_growth:+.1f}%，口径：{caliber}），"
            "规模效应无法验证"
        )
    return score, note, ["revenue", "grossprofit_margin"]


# --- _canvas_cyclicality ---
def _canvas_cyclicality(fin_list: list[dict]) -> tuple[float | None, str, list[str]]:
    """周期性：同报告期 ROE 波动率推断（波动越大周期性越强，星级越低）。

    C1-a：只用同报告期且去重的序列——年报 ≥3 期用年报，否则与最新报告期同
    MMDD 的行；都不足 3 期则输出数据不足，**不做混期 CV**。波动描述必须带
    口径标签，且不单独构成周期性结论（阈值 0.15/0.35 数值未变，仅输入序列
    与文案口径修正）。
    """
    if not fin_list:
        return None, "数据不足：缺少财务数据，无法判断周期性", []
    import statistics
    rows = dedupe_by_end_date(sort_kline_asc(fin_list))
    series, caliber = _same_period_fin_rows(rows)
    if len(series) < 3:
        return None, (
            "数据不足：同报告期 ROE 序列不足 3 期（年报/同报告期各需 ≥3 期），"
            "不做波动或周期性判断"
        ), []
    roes = [v for v in (_fin_field_num(r, "roe") for r in series) if v is not None]
    if len(roes) < 3:
        return None, "数据不足：ROE 至少需 3 期同报告期数据评估波动性", []
    mean_roe = statistics.mean(roes)
    if abs(mean_roe) <= 1e-9:
        return None, "数据不足：ROE 均值接近 0，变异系数不适用", []
    cv = statistics.pstdev(roes) / abs(mean_roe)
    if cv < 0.15:
        score, note = 90.0, f"近 {len(roes)} 期{caliber} ROE 变异系数 {cv:.2f}（<0.15），波动小（口径：{caliber}）"
    elif cv < 0.35:
        score, note = 55.0, f"近 {len(roes)} 期{caliber} ROE 变异系数 {cv:.2f}（0.15-0.35），中等波动（口径：{caliber}）"
    else:
        score, note = 20.0, (
            f"近 {len(roes)} 期{caliber} ROE 变异系数 {cv:.2f}（≥0.35），波动大，"
            f"呈周期性波动特征（口径：{caliber}；不单独构成周期性结论）"
        )
    return score, note, ["roe"]


# --- _canvas_growth_driver ---
def _canvas_growth_driver(fin_list: list[dict]) -> tuple[float | None, str, list[str]]:
    """增长驱动：数据不支持精细量/价拆分，用营收增速绝对水平粗略映射。

    F0-8 修复：改用「最近报告期 vs 同报告期上年」同比，禁止跨期混比
    （旧实现取近 5 期首尾差，Q1 累计 vs 全年会算出 -74% 的荒谬"下滑"）。
    """
    if not fin_list:
        return None, "数据不足：缺少财务数据，无法判断增长驱动", []
    rows = sort_kline_asc(fin_list)
    latest = rows[-1]
    prev = _prior_year_row(rows, latest)
    rev_cur = _fin_field_num(latest, "revenue")
    rev_prev = _fin_field_num(prev, "revenue") if prev else None
    if rev_cur is None or rev_prev is None or not rev_prev:
        return None, "数据不足：缺少同报告期上年基期，营收同比不可比", []
    growth = (rev_cur - rev_prev) / abs(rev_prev) * 100
    note_suffix = "（数据不支持量/价精细拆分，以营收同比绝对水平粗略映射）"
    if growth >= 30:
        score, note = 85.0, f"最近报告期营收同比 {growth:+.1f}%，增长动能强{note_suffix}"
    elif growth >= 10:
        score, note = 55.0, f"最近报告期营收同比 {growth:+.1f}%，增长动能中等{note_suffix}"
    elif growth >= 0:
        score, note = 30.0, f"最近报告期营收同比 {growth:+.1f}%，增长动能偏弱{note_suffix}"
    else:
        score, note = 10.0, f"最近报告期营收同比 {growth:+.1f}%，增长动能弱{note_suffix}"
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
    industry = (chain or {}).get("chain_matched_on") or (chain or {}).get("industry")
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
    # R14/C3（2026-10-05 全量审查）：各维度是引擎代理模型（如毛利率水平/稳定性
    # 代理客户锁定），行内「特征/粘性」等表述为代理读数——加一行口径披露，
    # 不删除评分与依据字段。
    lines.append(
        "> 评分口径：各维度为引擎代理模型读数（字段依据见「依据」列）——"
        "代理读数不单独构成客户行为、竞争壁垒或规模效应的直接结论。")
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
    analysis: list[dict] | None = None,
) -> str:
    """A-5: 管理层完整评估。

    合规: 仅陈述公开记录事实（决策日期/公告内容/行为统计），不推断管理层主观动机，
    不给"信赖/不信赖"二元结论。软维度（组织能力/企业文化/接班人风险）固定标注
    "[Claude report 阶段定性填充]"占位。

    analysis（R-B1）: 命中事件槽位（EVENTS_HOST_KEYS：「events」或
    「event_classification」）的段时，以 analysis_md 首行摘要替换决策时间线占位；
    无匹配段 → 保持 "[待 Claude report 阶段填充]"（F0-3 qc 拦截未填占位）。
    """
    from lib.analysis_schema import EVENTS_HOST_KEYS, find_section
    from lib.schema import ManagementTimelineEntry
    from lib.scoring import insider_signal, management_ability_proxy

    # 判据与 _section_events_timeline(:1600) / render_html(:1526) 同源：
    # 此前这里手写 module/position == "events"，于是 `module: "event_classification"`
    # （文档里的写法）能过预检却命不中此处，A-5 单元格留 error 级占位。
    _sec = find_section(analysis, EVENTS_HOST_KEYS)
    ev_summary = ""
    if _sec is not None:
        first_line = str(_sec.get("analysis_md") or "").splitlines()
        ev_summary = (first_line[0].strip() if first_line else "")
        # 全量审查 P2：analysis_md 首行入 A-5 表格单元格——须 | 转义 +
        # 长度截断（仿事件标题处理；旧实现裸插，含 | 的摘要会拆裂表格列）
        ev_summary = ev_summary.replace("|", "｜")[:47]
    ev_cell = (f'<span data-module="events">**{ev_summary}**</span>'
               if ev_summary else "[待 Claude report 阶段填充]")

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
                f"| {ev_cell} |"
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
            "营收增量/CAPEX（同报告期代理读数）", score100, capex_note,
            ["revenue", "cap_ex"],
        ))
    else:
        lines.append(f"| 营收增量/CAPEX（同报告期代理读数） | 数据不足 | {capex_note} |")
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
    # 措辞避开 `placeholder-engine-slot` 词规（\[\待 Claude|\[\待填充|Claude report 阶段|
    # Claude 填写）：这三行**没有注入机制**（写死直出，analysis.json 的任一槽位都不
    # 读它），却按 error 级占位被拦截 → 任何 full 报告都无法通过第 0 层门禁。
    # 与报告内其他缺口一致，改按「引擎未采集 + 补证路径」如实陈述。
    lines.append("- 组织能力: 引擎未采集——需结合管理层背景、组织架构变化、核心团队稳定性等公开信息人工补充")
    lines.append("- 企业文化: 引擎未采集——需结合公司治理公告、员工持股计划、历史危机应对记录等公开信息人工补充")
    lines.append("- 接班人风险: 引擎未采集——需结合高管年龄结构、董事会变更公告、控制权结构等公开信息人工补充")
    lines.append("")
    lines.append(
        "> ⚠️ 合规声明：以上时间线与评分仅陈述公开记录事实（决策日期、公告内容、行为统计），"
        "不推断管理层主观动机，不构成对管理层的信赖/不信赖二元结论。"
    )
    lines.append("")
    return "\n".join(lines)


# --- _section_fundamentals_layered ---
def _prior_year_row(fin_list: list[dict], latest: dict) -> dict | None:
    """找同报告期上年行（同比基期）。

    F0-2 修复：财务序列混合季度累计行与年报行，直接取前一行会把
    「Q1 累计 vs 上年全年」或「半年累计环比」误标为同比。
    同比基期必须是同月日的上年报告期（如 20260630 → 20250630）；
    找不到 → None（同比不可比）。
    """
    ed = _norm_ed(str(latest.get("end_date") or ""))
    if len(ed) != 8:
        return None
    target = f"{int(ed[:4]) - 1}{ed[4:]}"
    for r in fin_list:
        if _norm_ed(str(r.get("end_date") or "")) == target:
            return r
    return None


def _roe_trend_anchors(
    fin_list: list[dict], latest_fin: dict,
) -> tuple[float | None, float | None, int]:
    """护城河 ROE 趋势锚点：(起点 ROE, 末年报 ROE, 年报行数)。

    年报 ≥2 期 → 首尾年报 ROE；否则与最新行同 MMDD 的最老行做起点
    （避免「年报 ROE vs 季累计 ROE」跨期混比出伪"侵蚀"）。
    end_date 不可解析 → 无锚点（review 二轮：normalize 返回空串时
    ""[4:]=="" 会把所有不可解析行归入同组，静默混比）。
    """
    annual_rows = [
        r for r in fin_list
        if _norm_ed(str(r.get("end_date") or "")).endswith("1231")
    ]
    n_annual = len(annual_rows)
    if n_annual >= 2:
        return (
            _safe_num(annual_rows[0].get("roe")),
            _safe_num(annual_rows[-1].get("roe")),
            n_annual,
        )
    latest_mmdd = _norm_ed(str(latest_fin.get("end_date") or ""))[4:]
    if not latest_mmdd:
        return None, None, n_annual
    same_period = [
        r for r in fin_list
        if _norm_ed(str(r.get("end_date") or ""))[4:] == latest_mmdd
    ]
    if len(same_period) >= 2:
        return _safe_num(same_period[0].get("roe")), None, n_annual
    return None, None, n_annual


# --- _FundamentalsContext ---
class _FundamentalsContext:
    """C4 v0.2.7：_section_fundamentals_layered 块②数据预取 + C6 估值派生的封装。

    构造入参 (dims, collection, val_cache)，一次性预取 12 题分层所需的全部
    财务/估值/同行/宏观派生值，供 ③-⑩ 各块共享引用。消除无意义重命名
    （np_cur→np_v 式）与块⑩ 对 _a3_gm/杜邦三字段/_b3_r/cagr 的重复计算；
    gross_margin 走 _coalesce_gross_margin 单点 walk-back，A-③ 正文与
    状态行天然同源（清单任务 4 的 A-③ 状态行 bug 由此修复）。
    """

    def __init__(
        self,
        dims: dict[str, dict],
        collection: dict,
        val_cache: dict | None = None,
    ):
        # --- 财务序列（原块②）---
        fin = _get_dim_data(dims, "financials")
        self.fin_list: list[dict] = []
        if fin and isinstance(fin, list):
            # C1-a：同报告期修订行先去重（ann_date 最大者），再升序。
            self.fin_list = dedupe_by_end_date(sort_kline_asc(fin))
        self.latest_fin: dict = self.fin_list[-1] if self.fin_list else {}
        # F0-2: 同比基期取同报告期上年行；无基期 → 同比不可比（禁止跨期混比）。
        self.prev_fin: dict = _prior_year_row(self.fin_list, self.latest_fin) or {}
        self.first_fin: dict = self.fin_list[0] if self.fin_list else {}

        self.roe_val = _get_safe(self.fin_list, "roe")
        self.gm_val = _coalesce_fin_field(self.fin_list, "grossprofit_margin", "gross_margin")
        self.np_v = _safe_num(self.latest_fin.get("net_profit"))
        self.profit_dedt = _safe_num(self.latest_fin.get("profit_dedt"))
        self.debt_ratio = _coalesce_fin_field(self.fin_list, "debt_ratio", "debt_to_assets")
        self.em_val = _coalesce_fin_field(self.fin_list, "equity_multiplier", "em")
        self.ocf_val = _coalesce_fin_field(self.fin_list, "ocf", "n_cashflow_act")
        self.cf_ratio_val: float | None = None
        if self.ocf_val is not None and self.np_v is not None and self.np_v > 0:
            self.cf_ratio_val = self.ocf_val / self.np_v
        self.rev_cur = _safe_num(self.latest_fin.get("revenue"))
        self.rev_prev = _safe_num(self.prev_fin.get("revenue"))
        self.rev_yoy: float | None = None
        if self.rev_cur is not None and self.rev_prev is not None and self.rev_prev > 0:
            self.rev_yoy = (self.rev_cur - self.rev_prev) / self.rev_prev * 100
        self.ar_cur = _fin_field_num(self.latest_fin, "accounts_receiv", "ar")
        self.ar_prev = _fin_field_num(self.prev_fin, "accounts_receiv", "ar")
        self.ar_growth: float | None = None
        if self.ar_cur is not None and self.ar_prev is not None and self.ar_prev > 0:
            self.ar_growth = (self.ar_cur - self.ar_prev) / self.ar_prev * 100
        self.inv_cur = _fin_field_num(self.latest_fin, "inventory", "inventories")
        self.inv_prev = _fin_field_num(self.prev_fin, "inventory", "inventories")
        self.cagr, self.cagr_years_span = _compute_metric_cagr(self.fin_list, "revenue")
        self.np_cagr, self.np_cagr_years_span = _compute_metric_cagr(self.fin_list, "net_profit")
        self.fin_rev_list = [
            r for r in self.fin_list if _safe_num(r.get("revenue")) is not None
        ]
        # 最新行原始值（B-① 护城河 / C-② 杜邦正文与状态行共用；C-② 正文对
        # npm 另有 np/rev 派生回退，状态行不派生，数据完整性判定口径不变）
        self.roe_latest = _safe_num(self.latest_fin.get("roe"))
        self.npm_latest = _fin_field_num(self.latest_fin, "netprofit_margin", "np_margin")
        self.tat_latest = _fin_field_num(self.latest_fin, "asset_turnover", "assets_turn")
        self.em_latest = _fin_field_num(self.latest_fin, "equity_multiplier", "em")
        # 毛利率 walk-back 单点（A-③ 正文与状态行同源）
        self.gross_margin = _coalesce_gross_margin(self.fin_list)

        # --- 估值派生（原块④，C6 v0.2.7 已收敛到 canonical）---
        # canonical _v3_load_valuation_summary 在 valuation_summary 内滤
        # None+≤0 并补亏损期警告；边界变化：current_pe 从「最后非 None」变为
        # 「最后正值」——全亏损窗口股票由渲染负 PE 变为「数据不足」。
        self.vs = _v3_load_valuation_summary(dims, val_cache)
        val_rows = _get_dim_data(dims, "valuation")
        self.pe_avail = (
            bool(val_rows)
            and isinstance(val_rows, list)
            and any(r.get("pe_ttm") is not None for r in val_rows)
        )
        self.current_pe = (self.vs.get("pe") or {}).get("current") if self.vs else None
        self.val_window_label = self.vs.get("window_label", "历史") if self.vs else "历史"
        self.pe_pct, self.pb_pct_ext, _ = _v3_valuation_percentiles(dims, val_cache)
        self.hist_pe_median = _historical_pe_median(val_cache, dims)
        # 分位须伴随中位数（report-conventions.md §9.2 估值分位规则 3）：D-① 预警与误区句同源取用
        _pe_med_any, self.hist_pb_median = _pct_medians(val_cache, dims)

        # --- 行业同行 / 市场结构（原块④余量）---
        self.industry_peers = collection.get("industry_peers") or {}
        # R4 同链最小修复（2026-10-04 复读）：金融行业标记——核心判断摘要的
        # 杠杆阈值判断（>70% 偿债风险/权益乘数扩产受限）对银行/非银不适用，
        # 与 `_check_fast_veto` 的 F0-8 豁免同一判据（600036 实测：摘要称
        # 「资产负债率偏高，需关注偿债风险」而同报告口径说明称不构成
        # 工商企业同口径杠杆信号——同报告冲突）。
        self.industry = _extract_industry(_get_dim_data(dims, "basic_info"))
        self.financial_industry = self.industry in (
            "银行", "非银金融", "保险", "证券", "多元金融")
        industry_data = _get_dim_data(dims, "industry")
        self.industry_data = industry_data if isinstance(industry_data, dict) else {}
        ms = collection.get("market_structure") or {}
        self.ms = ms
        self.sw = ms.get("sw_index") or {}
        self.pmi_data = ms.get("pmi") or {}
        self.trigger_c = _v3_trigger_c_active(ms)




# --- _core_judgment_summary ---
# R14（2026-10-05）：本卡片段（`### 核心判断摘要` 的 H4 子段）现已纳入
# 结论段逐行证据扫描（`report_qc._scan_segment_assertions`）——引擎自动生成
# 的断言行须带证据绑定：字段直读 → `financials 维度字段（封存快照）`；
# 派生比值 → `Python calc: <formula>`。同时按 C3 全量审查降级代理→因果
# 表述（定价权/结构优势/杠杆建议等无依据成分），保留全部数值原料。
_CARD_SRC_FIN = "[来源: financials 维度字段（封存快照）]"
_CARD_SRC_RULE = "[来源: financials 维度字段（封存快照）；引擎阈值判读]"


def _core_judgment_summary(ctx: _FundamentalsContext) -> list[str]:
    """块③ 核心判断摘要（P0-3 升级；尾部业绩全景表随迁，test_v014 断言依赖）。"""
    lines: list[str] = ["\n### 核心判断摘要\n"]

    # 判断1: 盈利结构
    # F0-8 修复：最新报告期非年报期时（如 Q1 累计 ROE 2.96%），判断改用工
    # 最近年报 ROE（银行等季节性行业单季累计 ROE 不可与 TTM 门槛直接比较）；
    # 展示仍用报告期原始值并标注口径。
    roe_judge = ctx.roe_val
    roe_label = "ROE(TTM)"
    latest_ed = _norm_ed(str(ctx.latest_fin.get("end_date") or ""))
    if latest_ed and not latest_ed.endswith("1231"):
        roe_label = f"ROE（{latest_ed} 报告期累计）"
        annual_rows = [r for r in ctx.fin_list if _norm_ed(str(r.get("end_date") or "")).endswith("1231")]
        if annual_rows:
            ann_roe = _safe_num(annual_rows[-1].get("roe"))
            if ann_roe is not None:
                roe_judge = ann_roe
    lines.append("#### 盈利结构")
    lines.append(f"[结论] {_conclude_profit_structure(roe_judge, ctx.gm_val, ctx.debt_ratio, financial_industry=ctx.financial_industry)}{_CARD_SRC_RULE}")
    lines.append("")
    lines.append("[事实]")
    if ctx.roe_val is not None:
        label_line = f"- {roe_label} = {_fmt_num(ctx.roe_val)}%"
        if roe_judge is not None and roe_judge != ctx.roe_val:
            label_line += f"（判断用最近年报 ROE {roe_judge:.2f}%）"
        lines.append(label_line + _CARD_SRC_FIN)
    if ctx.gm_val is not None:
        lines.append(f"- 毛利率 = {_fmt_num(ctx.gm_val)}%{_CARD_SRC_FIN}")
    if ctx.debt_ratio is not None:
        lines.append(f"- 资产负债率 = {_fmt_num(ctx.debt_ratio)}%{_CARD_SRC_FIN}")
    if ctx.profit_dedt is not None and ctx.np_v is not None and ctx.np_v > 0:
        c4_ratio = ctx.profit_dedt / ctx.np_v
        lines.append(f"- 扣非/净利润 = {c4_ratio:.2f}[来源: Python calc: profit_dedt / np_v]")
    lines.append("")
    lines.append("[分析]")
    analysis_parts = []
    if ctx.roe_val is not None and ctx.gm_val is not None:
        if roe_judge >= 15 and ctx.gm_val >= 40:
            analysis_parts.append(
                "ROE 与毛利率均处高区间（阈值读数并列）；"
                "对盈利模式优势的解释须结合行业与产品证据，本卡不单独裁决")
        elif roe_judge >= 15 and ctx.gm_val < 20:
            analysis_parts.append(
                "ROE 较高而毛利率偏低——高周转/高杠杆归因须结合杜邦拆解与行业数据，"
                "可持续性待核验")
        elif roe_judge < 10 and ctx.gm_val >= 40:
            analysis_parts.append("高毛利率但低 ROE，可能费用率偏高或资产周转效率不足")
        else:
            analysis_parts.append(f"ROE={roe_judge:.1f}%（判断口径）、毛利率={ctx.gm_val:.1f}%，盈利模式处于中间区间，需持续跟踪变化趋势")
    if ctx.debt_ratio is not None and ctx.debt_ratio > 70:
        if ctx.financial_industry:
            analysis_parts.append(
                "资产负债率为金融行业负债经营特征，不作为工商企业口径的偿债风险信号"
                "（资产质量指标不在本报告数据面内）")
        else:
            analysis_parts.append("资产负债率偏高，需关注偿债风险与财务费用对利润的侵蚀")
    if ctx.profit_dedt is not None and ctx.np_v is not None and ctx.np_v > 0:
        c4_check = ctx.profit_dedt / ctx.np_v
        if c4_check < 0.7:
            analysis_parts.append("非经常性损益占比过大，净利润质量存疑")
    if not analysis_parts:
        analysis_parts.append("数据有限，无法进行充分的分析推理")
    lines.append("；".join(analysis_parts) + _CARD_SRC_RULE)
    lines.append("")
    e1_items = [ctx.roe_val is not None, ctx.gm_val is not None, ctx.debt_ratio is not None,
                ctx.profit_dedt is not None and ctx.np_v is not None and ctx.np_v > 0]
    lines.append(f"**证据强度：{_evidence_strength_label(e1_items)}**")
    lines.append("")

    # 判断2: 现金流质量
    lines.append("#### 现金流质量")
    lines.append(f"[结论] {_conclude_cash_flow_quality(ctx.cf_ratio_val, ctx.ar_growth, ctx.rev_yoy, ctx.ocf_val, np_v=ctx.np_v)}{_CARD_SRC_RULE}")
    lines.append("")
    lines.append("[事实]")
    if ctx.ocf_val is not None:
        lines.append(f"- 经营现金流 = {_fmt_v2(ctx.ocf_val)}{_CARD_SRC_FIN}")
    if ctx.np_v is not None:
        lines.append(f"- 净利润 = {_fmt_v2(ctx.np_v)}{_CARD_SRC_FIN}")
    if ctx.cf_ratio_val is not None:
        lines.append(f"- 经营现金流/净利润 = {ctx.cf_ratio_val:.2f}[来源: Python calc: ocf / np_v]")
    if ctx.ar_growth is not None and ctx.rev_yoy is not None:
        lines.append(f"- 应收增速 vs 营收增速：{ctx.ar_growth:+.2f}% vs {ctx.rev_yoy:+.2f}%[来源: Python calc: (ar_cur - ar_prev) / ar_prev 与 (rev_cur - rev_prev) / rev_prev]")
    lines.append("")
    lines.append("[分析]")
    cf_analysis = []
    if ctx.cf_ratio_val is not None:
        if ctx.cf_ratio_val >= OCF_COVERAGE_EXCELLENT:
            cf_analysis.append("经营现金流对净利润覆盖充分（覆盖关系指标，不单独构成利润质量或持续性结论）")
        elif ctx.cf_ratio_val >= OCF_COVERAGE_GOOD:
            cf_analysis.append("经营现金流对净利润基本覆盖（覆盖关系指标，不单独构成利润质量结论）")
        else:
            cf_analysis.append("经营现金流对净利润覆盖不足，需结合现金流量表构成与应收/存货变化复核")
    if ctx.ar_growth is not None and ctx.rev_yoy is not None:
        if ctx.ar_growth > ctx.rev_yoy * 1.5:
            cf_analysis.append(f"应收增速远超营收增速，存在赊销膨胀或回款恶化的风险")
        elif ctx.ar_growth > ctx.rev_yoy:
            cf_analysis.append("应收增速略高于营收增速，需关注回款节奏变化")
        else:
            cf_analysis.append("应收增速低于营收增速，收入增长质量较高")
    if not cf_analysis:
        cf_analysis.append("数据有限，无法进行充分的现金流分析")
    lines.append("；".join(cf_analysis) + _CARD_SRC_RULE)
    lines.append("")
    e2_items = [ctx.ocf_val is not None, ctx.np_v is not None,
                ctx.ar_growth is not None and ctx.rev_yoy is not None]
    lines.append(f"**证据强度：{_evidence_strength_label(e2_items)}**")
    lines.append("")

    # 判断3: 资产负债与扩产路径
    lines.append("#### 资产负债与扩产路径")
    conclusion3 = _conclude_asset_liability(
        ctx.debt_ratio, ctx.em_val, ctx.ar_cur, ctx.inv_cur, ctx.rev_cur,
        financial_industry=ctx.financial_industry)
    lines.append(f"[结论] {conclusion3}{_CARD_SRC_RULE}")
    lines.append("")
    lines.append("[事实]")
    if ctx.debt_ratio is not None:
        lines.append(f"- 资产负债率 = {ctx.debt_ratio:.2f}%{_CARD_SRC_FIN}")
    if ctx.em_val is not None:
        lines.append(f"- 权益乘数 = {ctx.em_val:.2f}{_CARD_SRC_FIN}")
    if ctx.ar_cur is not None and ctx.inv_cur is not None and ctx.rev_cur is not None and ctx.rev_cur > 0:
        wc_ratio = (ctx.ar_cur + ctx.inv_cur) / ctx.rev_cur * 100
        lines.append(f"- (应收+存货)/营收 = {wc_ratio:.1f}%[来源: Python calc: (ar_cur + inv_cur) / rev_cur]")
    lines.append("")
    lines.append("[分析]")
    al_analysis = []
    if ctx.debt_ratio is not None:
        if ctx.financial_industry:
            al_analysis.append(
                "金融行业：高负债率与高权益乘数为负债经营模式特征，"
                "不构成工商企业口径的财务风险判断（关注资产质量与资本充足，"
                "指标不在本报告数据面内）")
        elif ctx.debt_ratio >= 70:
            al_analysis.append("资产负债率偏高（>70% 阈值读数），财务杠杆读数偏大")
        elif ctx.debt_ratio >= 50:
            al_analysis.append("资产负债率适中（50%–70% 阈值读数）")
        else:
            al_analysis.append("资产负债率较低（<50% 阈值读数）——扩产空间评估须结合行业与经营计划")
    if ctx.em_val is not None and not ctx.financial_industry:
        if ctx.em_val > 3:
            al_analysis.append("权益乘数较高，扩产路径可能受限于融资能力")
        elif ctx.em_val < 1.5:
            al_analysis.append("权益乘数偏低（读数）")
    if ctx.ar_cur is not None and ctx.inv_cur is not None and ctx.rev_cur is not None and ctx.rev_cur > 0:
        wc_ratio_val = (ctx.ar_cur + ctx.inv_cur) / ctx.rev_cur
        if wc_ratio_val > 0.5:
            al_analysis.append("运营资金占用偏高，扩产时需关注现金流压力")
        else:
            al_analysis.append("运营资金占用较低，扩产的现金流压力较小")
    if not al_analysis:
        al_analysis.append("数据有限，无法进行充分的资产负债分析")
    lines.append("；".join(al_analysis) + _CARD_SRC_RULE)
    lines.append("")
    e3_items = [ctx.debt_ratio is not None, ctx.em_val is not None,
                ctx.ar_cur is not None and ctx.rev_cur is not None]
    lines.append(f"**证据强度：{_evidence_strength_label(e3_items)}**")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.extend(_financial_panorama_table(ctx.fin_list))
    return lines


# --- _section_4a_industry_position ---
_SW_LEVEL_CN = {"L1": "一级", "L2": "二级", "L3": "三级"}


def _section_4a_industry_position(
    dims: dict[str, dict], ctx: _FundamentalsContext,
    status_rows: list[tuple[str, str, bool, str]],
) -> list[str]:
    """4a. 行业位置（A-① 行业景气度 / A-② 竞争位置 / A-③ 毛利率 vs 行业中位数）。"""
    lines: list[str] = ["### 4a. 行业位置", ""]

    # A-① 行业景气度
    lines.append("#### A-① 行业景气度")
    if ctx.sw and ctx.sw.get("return_20d_pct") is not None:
        lines.append(
            f"申万行业指数 {ctx.sw.get('index_code', '?')}（{ctx.sw.get('industry', '?')}）"
            f"近 20 日涨跌：**{ctx.sw['return_20d_pct']:+.2f}%**。"
        )
        rel = ctx.sw.get("relative_vs_benchmark_pct")
        if rel is not None:
            direction = "跑赢" if rel > 0 else "跑输"
            lines.append(f"相对沪深 300：{rel:+.2f}%（{direction}大盘）。")
        svi = ctx.sw.get("stock_vs_industry_pct")
        if svi is not None:
            direction = "跑赢" if svi > 0 else "跑输"
            lines.append(f"个股相对行业：{svi:+.2f}%（{direction}行业板块）。")
    else:
        lines.append("数据不足：[申万行业指数不可得，无法判断行业景气度]")
    if ctx.pmi_data.get("manufacturing_pmi") is not None:
        lines.append(
            f"制造业 PMI：**{ctx.pmi_data['manufacturing_pmi']:.1f}**（{ctx.pmi_data.get('month', '?')}，"
            f"{ctx.pmi_data.get('signal', '?')}）[来源: {ctx.pmi_data.get('source', 'akshare')}]"
        )
    else:
        lines.append("PMI/产量：数据不足：[宏观 PMI 数据源不可得；产量分项需行业数据库或 WebSearch 补充]")
    lines.append("")
    sw_ret = ctx.sw.get("return_20d_pct")
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
    if ctx.trigger_c:
        lines.append("")
        lines.append("**[扩展激活 · 触发源 C]** 行业结构驱动：建议补充竞争格局变化分析——"
                     "板块相对大盘偏离显著时，需区分行业景气 vs 估值重估 vs 政策预期。")
        lines.append("**[扩展激活 · 行业政策]** 近 30 日行业政策/监管事件需 WebSearch 补充，"
                     "并追溯政策传导至公司收入/成本的具体路径。")
    lines.append("")

    # A-① 行业景气度（状态行同源）
    _a1_ok = bool(ctx.sw and ctx.sw.get("return_20d_pct") is not None)
    _a1_s = f"申万板块近20日{ctx.sw['return_20d_pct']:+.2f}%" if _a1_ok else "数据不足"
    status_rows.append(("A-①", "行业景气度", _a1_ok, _a1_s))

    # A-② 竞争位置
    lines.append("#### A-② 竞争位置")
    basic = _get_dim_data(dims, "basic_info")
    industry_name = ""
    if isinstance(basic, dict):
        industry_name = basic.get("industry", "") or basic.get("行业", "") or ""
    rev = _safe_num(ctx.latest_fin.get("revenue"))
    ry_pct = ctx.industry_peers.get("rankings", {}).get("revenue_yoy_pct")
    ry_rank = ctx.industry_peers.get("rankings", {}).get("revenue_yoy_rank")
    ry_total = ctx.industry_peers.get("rankings", {}).get("revenue_yoy_total")
    target_ry = (ctx.industry_peers.get("target") or {}).get("revenue_yoy")
    peer_source = ctx.industry_peers.get("peer_source")
    if peer_source == "stock_basic_fallback":
        warn = ctx.industry_peers.get("warning") or "非申万 L3 成分股"
        lines.append(f"⚠️ {warn}")
    if rev is not None and ctx.industry_peers.get("sufficient"):
        sw_name = ctx.industry_peers.get("industry_name") or ""
        shown = sw_name or industry_name or "未知"
        if peer_source == "sw_index_member" and sw_name:
            # 申万成分池：行业名以申万名（L3/L2）为准。Tushare 粗分类名与之常不
            # 一致（粗名「电气设备」vs 申万「锂电池」），差异显式标注——否则模块 ④
            # 与模块 ⑨ 会出现两个行业名，读者无从判断哪个是申万口径。
            # basic_info 无 industry 字段时不得渲染成「Tushare 粗分类为「」」
            if not industry_name or sw_name == industry_name:
                tag = "（申万分类）"
            else:
                tag = f"（申万分类；Tushare 粗分类为「{industry_name}」）"
            # 池升层披露：L3 过薄时同行池升到 L2/L1，展示名仍是 L3，读者按所写
            # 行业重算会得到不同的 N。其他降级路径都有披露（粗分类回落有
            # warning、名称不匹配有上面的 tag），唯独这一条此前无任何提示。
            _name_lvl = ctx.industry_peers.get("industry_level")
            _pool_lvl = ctx.industry_peers.get("peer_level")
            if _name_lvl and _pool_lvl and _name_lvl != _pool_lvl:
                _pool_shown = ctx.industry_peers.get("pool_name") or shown
                tag += (
                    f"；同行排名与分位按申万{_SW_LEVEL_CN.get(_pool_lvl, _pool_lvl)}"
                    f"行业「{_pool_shown}」成分池计算，"
                    f"非按申万{_SW_LEVEL_CN.get(_name_lvl, _name_lvl)}行业「{shown}」"
                )
            lines.append(f"所属行业：{shown}{tag}。")
        else:
            # 降级池不得断言「申万分类」
            lines.append(f"所属行业：{shown}。")
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
            err = ctx.industry_peers.get("error", "缺少同行营收对比数据")
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
        "竞争位置常被用作定价溢价/折价的一种解释（经验框架，非本次数据结论）——"
        "龙头常伴流动性溢价、追赶者需以成长兑现支撑估值，均须以同行样本验证。",
        a2_pitfall,
        [
            "对比毛利率与行业均值差异（见 A-③）",
            "查公司市占率数据（年报/行业报告）",
            "关注近 3 年竞争位置是上升还是下降趋势",
        ],
    ))
    lines.append("")

    # A-② 竞争位置（状态行同源）
    _a2_ok = bool(ctx.latest_fin.get("revenue") is not None and ctx.industry_peers.get("sufficient")
                  and ctx.industry_peers.get("rankings", {}).get("revenue_yoy_pct") is not None)
    _a2_ry_pct = ctx.industry_peers.get("rankings", {}).get("revenue_yoy_pct")
    _a2_s = f"营收增速分位{_a2_ry_pct:.1f}%" if _a2_ok else "数据不足"
    status_rows.append(("A-②", "竞争位置", _a2_ok, _a2_s))

    # A-③ 毛利率 vs 行业中位数
    lines.append("#### A-③ 毛利率 vs 行业中位数")
    # C5 v0.2.7: 字段优先级统一 GROSS_MARGIN_FIELDS（grossprofit_margin 真名优先）；
    # C4 v0.2.7: 取值收敛到 ctx.gross_margin（_coalesce_gross_margin walk-back 单点，
    # 与 A-③ 状态行同源）
    gross_margin = ctx.gross_margin
    if gross_margin is not None:
        lines.append(f"最新报告期毛利率：**{gross_margin:.2f}%**。")
        peer_gms = [
            _coalesce_gross_margin([p])
            for p in ctx.industry_peers.get("peers", [])
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
        # R15 round-8 全文补齐（Codex supplement L538）：高毛利率与定价权/壁垒
        # 不是等号——改候选解释 + 边界（成本结构/核算口径/产品结构）。
        "毛利率是定价权分析的第一道线索——高毛利率**可能**反映客户价格不敏感"
        "或产品差异化壁垒（候选解释），同时受成本结构、核算口径与产品结构影响。",
        a3_pitfall,
        [
            "对比同行业公司毛利率离散度（若可得）",
            "观察毛利率近 3 年趋势：下降可能暗示竞争加剧或成本上升",
            "结合应收/现金流验证收入质量（见 C-③）",
        ],
    ))
    lines.append("")

    # A-③ 毛利率 vs 行业中位数（状态行同源：ctx.gross_margin walk-back）
    _a3_gm = ctx.gross_margin
    _a3_ok = _a3_gm is not None
    _a3_s = f"毛利率{_a3_gm:.2f}%" if _a3_ok else "数据不足"
    status_rows.append(("A-③", "毛利率 vs 行业中位数", _a3_ok, _a3_s))

    return lines


# --- _section_4b_business_quality ---
def _section_4b_business_quality(
    dims: dict[str, dict], collection: dict, ctx: _FundamentalsContext,
    status_rows: list[tuple[str, str, bool, str]],
    analysis: list[dict] | None = None,
) -> list[str]:
    """4b. 商业质量（B-① 护城河 / 商业模式画布 / 管理层评估 / B-② 增长 / B-③ 现金流）。"""
    lines: list[str] = ["### 4b. 商业质量", ""]
    # C4 v0.2.7: np_cur/np_prev 原 wrapper 别名，此处仅 np_prev 需要本地化
    np_prev = _safe_num(ctx.prev_fin.get("net_profit"))

    # B-① 护城河来源
    lines.append("#### B-① 护城河来源")
    roe_now = _safe_num(ctx.latest_fin.get("roe"))
    # F0-8 修复：ROE 趋势用年报行（1231）对比，避免季度累计 ROE 混比
    # （招行 12.03% 全年 vs 2.96% Q1 被误判为"侵蚀"）。锚点计算收敛到
    # _roe_trend_anchors（review 二轮补：可解析守卫 + 可单测）。
    roe_first, roe_ann_last, n_annual_rows = _roe_trend_anchors(ctx.fin_list, ctx.latest_fin)
    if roe_now is not None:
        lines.append(f"当前 ROE：**{roe_now:.2f}%**（报告期累计口径，最新年报 {roe_ann_last:.2f}%）" if roe_ann_last is not None else f"当前 ROE：**{roe_now:.2f}%**。")
        # R9（2026-10-04 独立复检）：不以首末点净变化概括「趋势强化/侵蚀」——
        # 端点关系不是持续趋势，更不能直接支撑护城河强化。改为明示**同口径
        # 完整年度序列（至多近 5 期）与最近一期变化**；可解析年报值 <2 个时
        # 退到同报告期（MMDD）序列并注明口径，两者皆缺则停笔（不再用季累计
        # 全序列出趋势判词）。
        observations = [
            (_norm_ed(str(r.get("end_date") or "")), _safe_num(r.get("roe")))
            for r in ctx.fin_list
        ]
        observations = [(date, value) for date, value in observations
                        if len(date) == 8 and date.isdigit() and value is not None]
        annual = [(date, value) for date, value in observations if date.endswith("1231")]
        mmdd = _norm_ed(str(ctx.latest_fin.get("end_date") or ""))[4:]
        comparable = annual if len(annual) >= 2 else [
            (date, value) for date, value in observations if mmdd and date[4:] == mmdd]
        if len(comparable) >= 2:
            show = comparable[-5:]
            seq = " → ".join(f"{date[:4]}-{date[4:6]}-{date[6:]}: {value:.2f}%"
                             for date, value in show)
            prev_date, prev_value = comparable[-2]
            date, value = comparable[-1]
            change = value - prev_value
            if int(date[:4]) - int(prev_date[:4]) == 1:
                label = "较上年" if len(annual) >= 2 else "较上年同期"
                comparison = f"最新有效一期{label} {change:+.2f}pp。"
            else:
                comparison = (f"{date[:4]} 年相对 {prev_date[:4]} 年变化 {change:+.2f}pp"
                              "（非同比；上年可比 ROE 不可得）。")
            prefix = (f"近 {len(show)} 个有效年报 ROE（年报口径，按报告期）"
                      if len(annual) >= 2 else
                      f"同报告期（{mmdd[:2]}-{mmdd[2:]}）ROE 序列（近 {len(show)} 个有效期，非年报口径）")
            lines.append(f"{prefix}：{seq}；{comparison}")
        lines.append("")
        lines.append("护城河定性判断需结合以下维度（数据引擎提供定量基础，AI 做定性综合）：")
        lines.append(f"- **利润转化效率：** ROE={roe_now:.2f}%、扣非/净利润比例见 C-④")
        lines.append("- **现金流健康度：** 经营现金流/净利润覆盖比见 C-③")
        lines.append("- **收入可持续性：** 近 3 年营收 CAGR 见 C-①")
        lines.append("- **资产回报效率：** 杜邦拆解见 C-②")
    else:
        lines.append("数据不足：[缺少 ROE 数据，无法评估护城河]")
    lines.append("")
    _hint_first = roe_first if roe_ann_last is not None else None
    _hint_now = roe_ann_last if roe_ann_last is not None else roe_now
    lines.append(_law10_hint(
        "护城河是长期估值的锚——没有护城河的高增长公司，估值收缩速度可能快于预期。",
        (
            f"本次 ROE {_hint_now:.2f}%"
            + (f"（{_hint_first:.2f}% → {_hint_now:.2f}%，年报口径）" if _hint_first is not None else "")
            + "，若直接等同于强护城河，可能忽略高杠杆或周期高点的一次性贡献（见 C-② 杜邦）。"
            if _hint_now is not None else
            "本次 ROE 不可得，不宜用营收增速或 PE 分位间接替代护城河判断。"
        ),
        [
            "杜邦拆解 ROE 来源（见 C-②）",
            "对比同行 ROE 中位数（见可比公司表）",
            "查公司年报中「核心竞争力」部分与实际财务数据是否一致",
        ],
    ))
    lines.append("")

    # B-① 护城河来源（状态行同源：ctx.roe_latest 最新行 ROE）
    _b1_roe = ctx.roe_latest
    _b1_ok = _b1_roe is not None
    _b1_s = f"ROE={_b1_roe:.2f}%" if _b1_ok else "数据不足"
    status_rows.append(("B-①", "护城河来源", _b1_ok, _b1_s))

    # A-4: 商业模式画布（v0.1.8 Step 6，紧接 B-① 护城河来源之后）
    lines.append(_section_business_model_canvas(
        ctx.fin_list,
        dims.get("holder_changes") or {},
        collection.get("chain_context") or {},
    ))

    # A-5: 管理层完整评估（v0.1.8 Step 6，与商业模式画布并列在 4b 商业质量段落）
    lines.append(_section_management_assessment(
        collection.get("events"),
        dims.get("holder_changes") or {},
        ctx.fin_list,
        analysis=analysis,
    ))

    # B-② 增长驱动力
    lines.append("#### B-② 增长驱动力")
    if ctx.rev_cur is not None and ctx.rev_prev is not None and ctx.rev_prev > 0:
        lines.append(f"最近一期营收同比：**{ctx.rev_yoy:+.2f}%**。")
    elif ctx.rev_cur is not None:
        # F0-2: 无同报告期上年基期 → 同比不可比，禁止跨期混比
        lines.append("最近一期营收同比：**不可比**（无同报告期上年基期，跨期混比已禁用）。")
    if ctx.np_v is not None and np_prev is not None and np_prev > 0:
        np_yoy = (ctx.np_v - np_prev) / np_prev * 100
        lines.append(f"最近一期净利润同比：**{np_yoy:+.2f}%**。")
    elif ctx.np_v is not None:
        lines.append("最近一期净利润同比：**不可比**（无同报告期上年基期，跨期混比已禁用）。")
    if not (ctx.rev_cur and ctx.rev_prev) and not (ctx.np_v and np_prev):
        lines.append("数据不足：[缺少两期以上可比营收/净利润数据]")
    elif ctx.cagr is not None and ctx.cagr_years_span is not None:
        lines.append(
            f"近 {ctx.cagr_years_span:.0f} 年同报告期营收 CAGR：**{ctx.cagr:+.2f}%**（多年增长趋势锚点）。"
        )
        if ctx.rev_yoy is not None:
            # R14/C3（2026-10-05 全量审查）：原「加速/减速/驱动力仍在强化」
            # 将「单期同比 vs 同报告期多年 CAGR」两个口径的读数差自动读作
            # 驱动力持续性/因果（差值还含基数与期间效应）——降级为读数比较，
            # 持续性/切换须另核验；两个数值原料保留。
            gap = ctx.rev_yoy - ctx.cagr
            if gap > 3:
                sustain = f"最近一期同比高于同报告期多年 CAGR {gap:+.2f}pp"
            elif gap < -3:
                sustain = f"最近一期同比低于同报告期多年 CAGR {gap:+.2f}pp"
            else:
                sustain = f"最近一期同比与同报告期多年 CAGR 接近（{gap:+.2f}pp）"
            lines.append(
                f"驱动力读数比较：**{sustain}**（最近同比 {ctx.rev_yoy:+.2f}% vs CAGR {ctx.cagr:+.2f}%）；"
                "两口径读数差可能含基数与期间效应，不单独构成「加速/减速」或"
                "驱动力切换的结论[来源: Python calc: rev_yoy - cagr]。"
            )
        gm_first = _coalesce_gross_margin([ctx.first_fin])
        gm_latest = ctx.gross_margin
        if gm_latest is None:
            gm_latest = _coalesce_gross_margin([ctx.latest_fin])
        if gm_first is not None and gm_latest is not None:
            gm_chg = gm_latest - gm_first
            if gm_chg > 1 and (ctx.cagr or 0) > 0:
                lines.append(
                    f"毛利率读数：{gm_first:.2f}% → {gm_latest:.2f}%（{gm_chg:+.2f}pp）——"
                    "价/量结构归因须结合行业价格与销量数据（不在本快照内），"
                    "本段不单独归因[来源: Python calc: gm_latest - gm_first]。"
                )
            elif abs(gm_chg) <= 1 and (ctx.cagr or 0) > 0:
                lines.append(
                    f"毛利率读数：基本稳定（{gm_first:.2f}% → {gm_latest:.2f}%）——"
                    "增长来源（价格/规模/份额）不由此单独判定"
                    "[来源: Python calc: gm_latest - gm_first]。"
                )
        roe_first_v = _safe_num(ctx.first_fin.get("roe"))
        roe_latest_v = _safe_num(ctx.latest_fin.get("roe"))
        if roe_first_v is not None and roe_latest_v is not None:
            if roe_latest_v > roe_first_v + 3 and (ctx.cagr or 0) > 0:
                lines.append(
                    f"杠杆驱动警示：ROE {roe_first_v:.2f}% → {roe_latest_v:.2f}% 升幅较大，"
                    "需结合 C-② 杜邦验证是否来自权益乘数。"
                )
    lines.append("")
    lines.append("增长驱动力来源需结合以下判断（候选解释，均须核验后成立）：")
    # R15 round-8 全文补齐（Codex supplement L619–621）：三条「→ 结论」的
    # 等号式推断改为候选解释 + 核验需求，不直接断言份额扩张/定价权/不可持续。
    lines.append("- **量驱动（候选解释）：** 收入增速 > 行业均值 → 可能对应份额扩张"
                 "（待核验：收入与应收拆分，见 C-③）")
    lines.append("- **价驱动（候选解释）：** 毛利率扩张 + 收入增长 → 可能对应定价权提升"
                 "（待核验：量价拆分与成本口径，见 A-③）")
    lines.append("- **杠杆驱动（候选解释）：** ROE 提升若来自权益乘数 → 可持续性存疑"
                 "（待核验：杜邦分解与融资/分红记录，见 C-②）")
    lines.append("")
    b2_pitfall = (
        f"本次营收同比 {ctx.rev_yoy:+.2f}%，若等同于价值创造，可能忽略资本开支/ROIC——"
        "低回报扩张反而摧毁股东价值。"
        if ctx.rev_yoy is not None else
        "本次缺少两期可比营收，不宜用单季利润波动推断增长驱动力类型。"
    )
    lines.append(_law10_hint(
        # R15 round-8 全文补齐（Codex supplement L624）：无「类型决定倍数」的
        # 普遍确定关系——改为经验框架 + 质量谱系两端 + 验证要求。
        "增长驱动力的质量谱系（经验框架，非确定关系）：量价齐升常被视为质量较高"
        "的一端、纯杠杆扩张为较低的一端——与估值倍数的映射没有普遍确定式，"
        "须结合 ROIC/资本开支与行业样本单独验证。",
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

    # B-② 增长驱动力（状态行同源）
    _b2_ok = ctx.rev_cur is not None and ctx.rev_prev is not None and ctx.rev_prev > 0
    _b2_s = f"营收同比{ctx.rev_yoy:+.2f}%" if _b2_ok else "数据不足"
    status_rows.append(("B-②", "增长驱动力", _b2_ok, _b2_s))

    # B-③ 现金流模式
    lines.append("#### B-③ 现金流模式")
    if ctx.ocf_val is not None and ctx.np_v is not None and ctx.np_v > 0:
        # C4: 覆盖比重推消除——ctx.cf_ratio_val 在 ctx 构造时已按同一公式计算
        # C1-b：覆盖关系措辞（原「健康/偏弱/严重背离」含质量定性）
        quality = "覆盖充分" if ctx.cf_ratio_val >= OCF_COVERAGE_GOOD else (
            "覆盖偏低" if ctx.cf_ratio_val >= OCF_COVERAGE_WEAK else "覆盖不足")
        lines.append(f"经营现金流/净利润覆盖比：**{ctx.cf_ratio_val:.2f}**（{quality}）。")
        if ctx.cf_ratio_val < OCF_COVERAGE_GOOD:
            lines.append(f"⚠️ 现金流覆盖比 < {OCF_COVERAGE_GOOD}，建议扩展分析：收入确认质量、应收/存货变动（见 C-③ 交叉验证）。")
    elif ctx.ocf_val is None:
        lines.append("数据不足：[经营现金流字段不可得]")
    elif ctx.np_v is None or ctx.np_v <= 0:
        lines.append("数据不足：[净利润非正，无法计算覆盖比]")
    lines.append("")
    lines.append(_law10_hint(
        "经营现金流对净利润的覆盖比是覆盖关系指标——利润与现金流持续背离，"
        "可能意味着应收膨胀、存货积压或收入确认激进（案例尚缺）。",
        (
            f"本次经营现金流/净利润覆盖比 = {ctx.cf_ratio_val:.2f}，单期读数不构成利润质量结论，"
            "应对比连续 4 期同口径趋势。"
            if ctx.cf_ratio_val is not None else
            "本次现金流覆盖比不可得，不宜用净利润同比单独推断覆盖关系。"
        ),
        [
            "对比应收增速 vs 营收增速（CV-2）",
            "对比存货增速 vs 营收增速",
            "查看连续 4 期现金流覆盖比趋势方向",
        ],
    ))
    if ctx.cf_ratio_val is not None and ctx.cf_ratio_val < OCF_COVERAGE_GOOD:
        lines.append("")
        lines.append("**[扩展激活 · 现金流覆盖 < 0.8]** 建议深度扫描收入确认质量："
                     "核对应收账龄、收入确认政策变更、大客户集中度变化。")
    lines.append("")

    # B-③ 现金流模式（状态行同源：ctx.cf_ratio_val）
    _b3_ok = ctx.ocf_val is not None and ctx.np_v is not None and ctx.np_v > 0
    _b3_s = f"OCF/净利={ctx.cf_ratio_val:.2f}" if _b3_ok else "数据不足"
    status_rows.append(("B-③", "现金流模式", _b3_ok, _b3_s))

    return lines




# --- _section_4_header_mda ---
def _section_4_header_mda(
    collection: dict, analysis: list[dict] | None = None,
) -> list[str]:
    """块① MD&A 快速扫描（Template A；collection 无 mda_narrative 卡片时零输出）。

    analysis（v0.3.0 fix③）：命中 mda_narrative 槽位的段替换
    「[待 Claude 填充管理层论述解读]」占位（QC completion-template-placeholder
    的 error 级命中项）。无匹配段 → 保持原占位，完成度门禁照常拦截。
    """
    lines: list[str] = []
    cards = _get_analysis_cards(collection)
    mda_card = cards.get("mda_narrative")
    if mda_card and isinstance(mda_card, dict):
        # generated_at 由 analysis_templates 以 UTC 生成，渲染侧转北京时间
        gen_at = fmt_fetched_at(mda_card.get("generated_at", ""))[:10] if mda_card.get("generated_at") else ""
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
        # C1-b：封存快照中的卡片值是旧质量词（良好/一般/需关注），渲染层做
        # 术语映射——快照内容不改（完整性契约），显示与新覆盖口径一致；
        # 新采集快照产出新词，映射恒等。
        cq = {"良好": "覆盖充分", "一般": "基本覆盖", "需关注": "覆盖偏低"}.get(cq, cq)
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
            lines.append(f"> - 经营现金流/净利润覆盖: {ratio_str} → {cq}（仅覆盖关系）")
        if roe is not None:
            dr_label = f"{dr:.2f}%" if dr is not None else "—"
            lines.append(f"> - ROE: {roe:.2f}% | 负债率: {dr_label}")
        ns = mda_card.get("narrative_slot", "")
        from lib.analysis_schema import (
            MDA_NARRATIVE_KEYS,
            find_section,
            mark_inline_consumed,
        )
        _mda = find_section(analysis, MDA_NARRATIVE_KEYS)
        _mda_amd = str((_mda or {}).get("analysis_md") or "").strip()
        if _mda_amd:
            mark_inline_consumed(collection, _mda)
            ns = _mda_amd
        if ns:
            lines.append(f"> - 叙事解读: {ns}")
        lines.append("")
    return lines


# --- _section_4c_financial_quality ---
def _section_4c_financial_quality(
    ctx: _FundamentalsContext,
    status_rows: list[tuple[str, str, bool, str]],
) -> list[str]:
    """4c. 财务质量（C-① 营收 CAGR / C-② 杜邦拆解 / C-③ 现金流+应收存货 / C-④ 扣非）。"""
    lines: list[str] = ["### 4c. 财务质量", ""]

    # C-① 近 3 年营收 CAGR
    lines.append("#### C-① 近 3 年营收 CAGR")
    if len(ctx.fin_rev_list) >= 2 and ctx.cagr is not None and ctx.cagr_years_span is not None:
        # F0-8 配套：日期范围标注 CAGR 实际采用的同报告期行组，
        # 而非全序列首尾（避免"2022-09-30 → 2026-06-30"误导）。
        cagr_rows = cagr_period_rows(ctx.fin_list, "revenue")
        if cagr_rows:
            d0 = _fmt_end_date(cagr_rows[0].get("end_date")) or "首期"
            d1 = _fmt_end_date(cagr_rows[-1].get("end_date")) or "末期"
        else:
            d0 = _fmt_end_date(ctx.fin_rev_list[0].get("end_date")) or "首期"
            d1 = _fmt_end_date(ctx.fin_rev_list[-1].get("end_date")) or "末期"
        lines.append(f"近 {ctx.cagr_years_span:.0f} 年营收 CAGR：**{ctx.cagr:+.2f}%**（{d0} → {d1}，同报告期口径）。")
        if ctx.rev_yoy is not None:
            # R14/C3（2026-10-05 全量审查）：与 B-② 同族——「加速/减速」把
            # 单期同比 vs 多年同报告期 CAGR 的两口径读数差自动读作趋势结论；
            # 降级为读数比较（数值原料保留）。
            _gap_c1 = ctx.rev_yoy - ctx.cagr
            if _gap_c1 > 3:
                recent_trend = f"最近一期同比高于同报告期多年 CAGR {_gap_c1:+.2f}pp"
            elif _gap_c1 < -3:
                recent_trend = f"最近一期同比低于同报告期多年 CAGR {_gap_c1:+.2f}pp"
            else:
                recent_trend = f"最近一期同比与同报告期多年 CAGR 接近（{_gap_c1:+.2f}pp）"
            lines.append(
                f"近一年读数比较：{recent_trend}"
                f"（最近一期同比 {ctx.rev_yoy:+.2f}% vs CAGR {ctx.cagr:+.2f}%）；"
                "两口径读数差可能含基数与期间效应，不单独构成趋势结论"
                "[来源: Python calc: rev_yoy - cagr]。")
    elif len(ctx.fin_rev_list) >= 2:
        lines.append("数据不足：[营收数据异常（首期或末期为零/负）]")
    else:
        lines.append("数据不足：[财务数据少于 2 期]")
    lines.append("")
    lines.append(_law10_hint(
        "营收 CAGR 是估值模型中增长率假设的锚——CAGR 的稳定性直接影响 DCF/g 值的置信度。",
        (
            f"本次 CAGR {ctx.cagr:+.2f}%、最近一期同比 {ctx.rev_yoy:+.2f}%，"
            "若机械外推历史 CAGR 至未来，可能在增速已放缓时高估。"
            if ctx.cagr is not None and ctx.rev_yoy is not None else
            "本次 CAGR 或近一年同比不可得，不宜用单季利润波动外推长期增长率。"
        ),
        [
            "对比净利润 CAGR 与营收 CAGR 是否同步（利润增速 > 收入增速 = 利润率扩张）",
            "结合行业景气度判断增长是行业性还是公司特异性",
            "关注近两季趋势：加速/减速背后的原因是什么",
        ],
    ))
    lines.append("")

    # C-① 近 3 年营收 CAGR（状态行同源）
    _c1_ok = len(ctx.fin_rev_list) >= 2 and ctx.cagr is not None
    _c1_s = f"CAGR={ctx.cagr:+.2f}%" if _c1_ok else "数据不足"
    status_rows.append(("C-①", "近3年营收CAGR", _c1_ok, _c1_s))

    # C-② 杜邦拆解 ROE
    lines.append("#### C-② 杜邦拆解 ROE")
    roe_v = _safe_num(ctx.latest_fin.get("roe"))
    npm = _fin_field_num(ctx.latest_fin, "netprofit_margin", "np_margin")
    tat = _fin_field_num(ctx.latest_fin, "asset_turnover", "assets_turn")
    em = _fin_field_num(ctx.latest_fin, "equity_multiplier", "em")
    # 若杜邦字段不可得，尝试从已有数据计算
    if npm is None and ctx.np_v is not None and ctx.rev_cur is not None and ctx.rev_cur > 0:
        npm = ctx.np_v / ctx.rev_cur * 100
    dupont_available = npm is not None and tat is not None and em is not None
    if roe_v is not None:
        lines.append(f"ROE：**{roe_v:.2f}%**。")
        if dupont_available:
            lines.append(f"- **净利润率：** {npm:.2f}%（{'高利润率模式' if npm > 15 else '低利润率/高周转模式' if npm < 5 else '中等利润率'}）")
            lines.append(f"- **资产周转率：** {tat:.4f}（{'重资产' if tat < 0.5 else '轻资产/高周转' if tat > 1.5 else '中等周转'}）")
            _em_label = ('高杠杆' if em > 3 else '低杠杆' if em < 1.5 else '中等杠杆')
            if ctx.financial_industry and em > 3:
                _em_label += '；金融行业负债经营模式'
            lines.append(f"- **权益乘数：** {em:.2f}（{_em_label}）")
            dupont_roe = npm / 100 * tat * em * 100
            lines.append(f"- 杜邦 ROE 校验：{dupont_roe:.2f}%（{'与 ROE 一致' if abs(dupont_roe - roe_v) < 0.5 else '与 ROE 存在差异，可能存在口径问题'}）")
        else:
            lines.append("数据不足：[杜邦拆解字段不可得；fina_indicator 不含净利率/周转率/权益乘数，需 income + balance 表]")
        # ROE 变化超 ±5pp → 提示完整杜邦
        roe_prev_v = _safe_num(ctx.prev_fin.get("roe"))
        if roe_prev_v is not None and abs(roe_v - roe_prev_v) > 5:
            lines.append(f"⚠️ ROE 近两期变化超 ±5pp（{roe_prev_v:.2f}% → {roe_v:.2f}%），建议完整杜邦拆解。")
    else:
        lines.append("数据不足：[ROE 字段不可得]")
    lines.append("")
    # R4 同链（2026-10-04 二轮）：金融行业的权益乘数/杠杆为负债经营模式特征，
    # 通用「高杠杆（财务风险）」提示不得赋予工商企业风险含义（Codex 有限补查）。
    if ctx.financial_industry:
        _dupont_why = ("杜邦拆解回答「ROE 从哪来」：银行/非银的权益乘数为负债经营模式"
                       "特征（监管资本约束下运行），三大因子的经营含义与工商企业不同。")
        _dupont_pitfall = (
            f"本次 ROE {roe_v:.2f}%"
            + (f"、净利润率 {npm:.2f}%、周转 {tat:.4f}、权益乘数 {em:.2f}"
               if dupont_available else "")
            + "，若只看 ROE 绝对值不看结构，可能忽略息差、拨备与资本占用对 ROE 的影响"
            "（金融行业不适用工商企业的高杠杆风险口径）。"
            if roe_v is not None else
            "本次 ROE 不可得，不宜用 PE 或营收增速间接替代 ROE 结构分析。"
        )
        _dupont_next = [
            "若 ROE 变化 > 5pp，追溯息差/拨备/资本占用对三大因子的贡献变化",
            "对比同行 ROE 结构（金融行业杠杆为经营常态，比较重点在息差与资产质量）",
            "关注资本充足与资产质量指标（本报告数据面外的需取财报原文）",
        ]
    else:
        _dupont_why = ("杜邦拆解回答「ROE 从哪来」——高净利率（品牌/技术壁垒）> "
                       "高周转（运营效率）> 高杠杆（财务风险）。")
        _dupont_pitfall = (
            f"本次 ROE {roe_v:.2f}%"
            + (f"、净利润率 {npm:.2f}%、周转 {tat:.4f}、权益乘数 {em:.2f}"
               if dupont_available else "")
            + "，若只看 ROE 绝对值不看结构，可能把高杠杆驱动的 ROE 误判为经营优秀。"
            if roe_v is not None else
            "本次 ROE 不可得，不宜用 PE 或营收增速间接替代 ROE 结构分析。"
        )
        _dupont_next = [
            "若 ROE 变化 > 5pp，追溯三大驱动因子的各自贡献变化",
            "对比同行 ROE 结构（高杠杆在加息周期更脆弱）",
            "关注权益乘数的负债结构（有息负债 vs 经营负债）",
        ]
    lines.append(_law10_hint(_dupont_why, _dupont_pitfall, _dupont_next))
    lines.append("")

    # C-② 杜邦拆解 ROE（状态行同源：最新行原始值，不派生）
    _c2_roe = ctx.roe_latest
    _c2_npm = ctx.npm_latest
    _c2_tat = ctx.tat_latest
    _c2_em = ctx.em_latest
    _c2_ok = (
        _c2_roe is not None and _c2_npm is not None
        and _c2_tat is not None and _c2_em is not None
    )
    _c2_s = (
        f"ROE={_c2_roe:.2f}%，npm×tat×em"
        if _c2_ok else "数据不足"
    )
    status_rows.append(("C-②", "杜邦拆解ROE", _c2_ok, _c2_s))

    # C-③ 经营现金流/净利润 + 应收/存货增速对比 + CV-2
    lines.append("#### C-③ 现金流覆盖 + 应收/存货交叉验证")
    if ctx.ocf_val is not None and ctx.np_v is not None and ctx.np_v > 0:
        lines.append(f"- 经营现金流/净利润：**{ctx.cf_ratio_val:.2f}**")
    else:
        lines.append("数据不足：[经营现金流或净利润字段不可得，无法计算覆盖比]")
    # 应收增速 vs 营收增速 (CV-2)
    rev_growth: float | None = ctx.rev_yoy
    ar_growth: float | None = None
    inv_growth: float | None = None
    if rev_growth is None and ctx.rev_cur is not None and ctx.rev_prev is not None and ctx.rev_prev > 0:
        rev_growth = (ctx.rev_cur - ctx.rev_prev) / ctx.rev_prev * 100
    if ctx.ar_cur is not None and ctx.ar_prev is not None and ctx.ar_prev > 0:
        ar_growth = (ctx.ar_cur - ctx.ar_prev) / ctx.ar_prev * 100
        lines.append(f"- 应收账款增速：**{ar_growth:+.2f}%**")
        if ctx.rev_cur is not None and ctx.rev_prev is not None and ctx.rev_prev > 0:
            rev_growth = (ctx.rev_cur - ctx.rev_prev) / ctx.rev_prev * 100
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
    if ctx.inv_cur is not None and ctx.inv_prev is not None and ctx.inv_prev > 0:
        inv_growth = (ctx.inv_cur - ctx.inv_prev) / ctx.inv_prev * 100
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
            f"本次现金流/净利润 = {ctx.cf_ratio_val:.2f}，若忽视应收/存货字段缺失，"
            "可能漏掉利润质量交叉验证。"
            if ctx.cf_ratio_val is not None else
            "本次缺少应收/存货与现金流数据，不宜单独用净利润同比判断收入质量。"
        )
    )
    lines.append(_law10_hint(
        "应收增速 > 营收增速是经典的利润质量预警信号——激进赊销可以短期推高营收，"
        "若赊销未能转化为回款，会以坏账或回款恶化暴露（条件式路径）。存货积压则可能意味着产品滞销。",
        c3_pitfall,
        [
            "查看连续 4 期以上应收/营收增速对比趋势",
            "查账龄结构（1 年以内应收占比，需财报附注）",
            "结合经营现金流方向确认（利润增长+现金流恶化=红色信号）",
        ],
    ))
    lines.append("")

    # C-③ 现金流覆盖 + 应收/存货（状态行同源：ctx 的 ar/inv/cf_ratio）
    _c3_ar = ctx.ar_cur
    _c3_inv = ctx.inv_cur
    _c3_ok = (
        ctx.ocf_val is not None and ctx.np_v is not None and ctx.np_v > 0
        and _c3_ar is not None and _c3_inv is not None
    )
    _c3_s = f"OCF/净利={ctx.cf_ratio_val:.2f}，含应收+存货" if _c3_ok else "数据不足"
    status_rows.append(("C-③", "现金流覆盖+应收/存货", _c3_ok, _c3_s))

    # C-④ 扣非/净利润
    lines.append("#### C-④ 扣非/净利润")
    ratio_c4: float | None = None
    if ctx.profit_dedt is not None and ctx.np_v is not None and ctx.np_v > 0:
        ratio_c4 = ctx.profit_dedt / ctx.np_v
        quality_label = "健康" if ratio_c4 >= 0.9 else (
            "存在非经常性损益" if ratio_c4 >= 0.7 else "非经常性损益扭曲严重")
        lines.append(f"扣非净利润/净利润：**{ratio_c4:.2f}**（{quality_label}）。")
        if ratio_c4 < 0.7:
            lines.append("⚠️ 扣非/净利润 < 0.7，净利润被非经常性损益显著抬高。请查阅最新财报附注中「非经常性损益」明细。")
        lines.append(f"扣非净利润：{_fmt_v2(ctx.profit_dedt)}；净利润：{_fmt_v2(ctx.np_v)}。")
    elif ctx.np_v is not None and ctx.np_v <= 0:
        lines.append("数据不足：[净利润非正，扣非/净利润比值无意义]")
    else:
        lines.append("数据不足：[扣非净利润或净利润字段不可得]")
    lines.append("")
    lines.append(_law10_hint(
        "扣非/净利润比值反映利润的「可持续性」——卖资产、政府补贴、投资收益等非经常性损益"
        "不具有重复性，以此为基础的 PE 估值会产生误导。",
        (
            f"本次扣非/净利润 = {ratio_c4:.2f}（扣非 {_fmt_v2(ctx.profit_dedt)} / 净利 {_fmt_v2(ctx.np_v)}），"
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
    # C-④ 扣非/净利润（状态行同源：ctx.profit_dedt）
    _c4_pd = ctx.profit_dedt
    _c4_ok = _c4_pd is not None and ctx.np_v is not None and ctx.np_v > 0
    _c4_r = _c4_pd / ctx.np_v if _c4_ok else None
    _c4_s = f"扣非/净利={_c4_r:.2f}" if _c4_ok else "数据不足"
    status_rows.append(("C-④", "扣非/净利润", _c4_ok, _c4_s))

    # 4d. 估值与预期（3 题 + LAW 15）
    # =================================================================
    return lines


# --- _section_4d_valuation_expectation ---

# D-③ 派生值（g_implied 族）的来源标签。措辞与 render_risk.py:685 的 5d 对照块一致，
# 避免同一数值在模块 4 与模块 5 出现两套来源措辞。本函数此前零 [来源:] 标签，
# 导致 `约 X%` 形态的行被 report_qc 的 F2 规则判为「派生表述缺来源」（warn）。
_D3_SOURCE_LABEL = "[来源: lib.valuation.implied_growth / 模块 4 D-③]"


def _section_4d_valuation_expectation(
    ctx: _FundamentalsContext,
    status_rows: list[tuple[str, str, bool, str]],
) -> list[str]:
    """4d. 估值与预期（D-① PE/PB 历史位置 / D-② PE vs 行业中位 / D-③ LAW 15 预期差）。"""
    lines: list[str] = ["### 4d. 估值与预期", ""]

    # D-① PE/PB 5 年历史位置
    lines.append("#### D-① PE/PB 历史位置")
    if ctx.vs is not None and ctx.pe_avail:
        if not ctx.vs:
            lines.append("数据不足：[估值历史序列不可得，建议配置 Tushare Token 获取 daily_basic]")
        else:
            pe_info = ctx.vs["pe"]
            pb_info = ctx.vs["pb"]
            ps_info = ctx.vs.get("ps", {})
            wl = ctx.vs.get("window_label", ctx.val_window_label)
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
            if ctx.vs.get("dv_ratio") is not None:
                lines.append(f"- 股息率：**{ctx.vs['dv_ratio']:.2f}%**（最近交易日 dv_ratio）")
            else:
                lines.append("数据不足：[daily_basic 无 dv_ratio 股息率字段]")
            for w in ctx.vs.get("warnings", []):
                lines.append(f"⚠️ {w}")
    else:
        lines.append("数据不足：[估值历史序列不可得，建议配置 Tushare Token 获取 daily_basic]")
    pe_extreme = ctx.pe_pct is not None and (ctx.pe_pct >= EXTREME_HIGH_THRESHOLD or ctx.pe_pct <= EXTREME_LOW_THRESHOLD)
    pb_extreme = ctx.pb_pct_ext is not None and (ctx.pb_pct_ext >= EXTREME_HIGH_THRESHOLD or ctx.pb_pct_ext <= EXTREME_LOW_THRESHOLD)
    if pe_extreme:
        zone = "偏高（≥80% 分位）" if ctx.pe_pct >= EXTREME_HIGH_THRESHOLD else "偏低（≤20% 分位）"
        lines.append(f"⚠️ PE 处于历史 {zone}{_pct_median_inline(ctx.hist_pe_median)}，建议触发完整预期差分析（见 D-③）。")
    if pb_extreme:
        zone = "偏高（≥80% 分位）" if ctx.pb_pct_ext >= EXTREME_HIGH_THRESHOLD else "偏低（≤20% 分位）"
        lines.append(f"⚠️ PB 处于历史 {zone}{_pct_median_inline(ctx.hist_pb_median)}，建议结合 D-③ 与资产质量验证预期差。")
    if pe_extreme or pb_extreme:
        lines.append("**[扩展激活 · 估值极端]** 完整预期差分析：① 隐含 g vs 历史 CAGR；② 一致预期（若可得）；③ 增长拐点催化剂。")
    lines.append("")
    d1_pitfall = (
        f"本次 PE 历史分位 {ctx.pe_pct:.1f}%{_pct_median_inline(ctx.hist_pe_median)}、"
        f"PB {ctx.pb_pct_ext:.1f}%{_pct_median_inline(ctx.hist_pb_median)}，"
        "若把低分位直接等同于「便宜」，可能忽略「低 PE 陷阱」的成立条件——"
        "PE=P/E，正盈利下修本身会抬高 PE；低分位陷阱须以盈利位置（是否处高点、"
        "将随周期回落）与价格路径（调整是否更大/更快）判别，不能由盈利下修直接推出。"
        if ctx.pe_pct is not None and ctx.pb_pct_ext is not None else
        (
            f"本次 PE 历史分位 {ctx.pe_pct:.1f}%{_pct_median_inline(ctx.hist_pe_median)}，"
            "需结合 PB 与盈利趋势判断是否为价值陷阱。"
            if ctx.pe_pct is not None else
            "本次估值分位不可得，不宜用当前 PE 绝对值替代历史分位判断。"
        )
    )
    lines.append(_law10_hint(
        # R15 round-8 全文补齐（Codex supplement L700）：不能由历史位置唯一反推
        # 市场预期——改「须另行核验隐含假设」的表述。
        "历史分位回答「当前估值在自身历史中处于什么位置」——极端分位不直接等于买卖信号，"
        "也不能由历史位置唯一反推市场预期；须另行核验当前定价隐含了何种假设"
        "（盈利路径/贴现率），以及该假设是否成立。",
        d1_pitfall,
        [
            "PE 与 PB 分位是否一致（CV-3 已在模块 1 落地）",
            "对比行业中位数 PE（见 D-②）",
            "极低分位时检查是否有大额非经常性损益压低 PE",
        ],
    ))
    lines.append("")

    # D-① PE/PB 历史位置（状态行同源：ctx 估值派生）
    _d1_pe_median = ctx.hist_pe_median
    _d1_ok = ctx.pe_avail and ctx.current_pe is not None and ctx.pe_pct is not None
    if _d1_ok and _d1_pe_median is not None:
        _d1_s = f"PE={ctx.current_pe:.2f}x，历史位置{ctx.pe_pct:.1f}%（中位数 {_d1_pe_median:.2f}x）"
    elif _d1_ok:
        _d1_s = f"PE={ctx.current_pe:.2f}x，历史位置{ctx.pe_pct:.1f}%"
    else:
        _d1_s = "数据不足"
    status_rows.append(("D-①", "PE/PB历史分位", _d1_ok, _d1_s))

    # D-② PE vs 行业中位数
    lines.append("#### D-② PE vs 行业中位数")
    premium: float | None = None
    ind_median: float | None = None
    if ctx.current_pe is not None and ctx.industry_peers.get("sufficient"):
        peers = ctx.industry_peers.get("peers", [])
        peer_pes = [
            float(p.get("pe_ttm")) for p in peers
            if p.get("pe_ttm") is not None and float(p.get("pe_ttm")) > 0
        ]
        if peer_pes:
            from lib.valuation import median_of
            ind_median = median_of([float(x) for x in peer_pes])
            premium = (ctx.current_pe - ind_median) / ind_median * 100
            lines.append(f"- 公司 PE(TTM)：**{ctx.current_pe:.2f}x**")
            # C2-d：家数必须带口径——本行为「有有效 PE 的同行数」，与同行池
            # 总数（另有标注）及各指标排名分母不同（600519 反例：8/10/9 三值
            # 无口径并列）。
            lines.append(f"- 行业中位数 PE：**{ind_median:.2f}x**（{len(peer_pes)} 家，PE 有效）")
            lines.append(f"- 溢价/折价：**{premium:+.1f}%**（{'溢价' if premium > 0 else '折价'}）")
            if premium > 30:
                lines.append("公司 PE 显著高于行业，需验证：是否具备远超同行的盈利增长或护城河。")
            elif premium < -30:
                lines.append("公司 PE 显著低于行业，需验证：是否存在未被市场定价的负面因素。")
        else:
            lines.append("数据不足：[同行 PE 数据不可得]")
    elif ctx.current_pe is not None:
        lines.append(f"公司 PE(TTM)：**{ctx.current_pe:.2f}x**。")
        lines.append("数据不足：[同行数量不足 3 家或行业数据不可得，无法计算行业中位 PE]")
    else:
        lines.append("数据不足：[当前 PE 不可得]")
    if ctx.industry_data.get("industry_pe_status") == "unavailable":
        note = str(ctx.industry_data.get("industry_pe_note") or "行业 PE 不可得")
        lines.append(f"⚠️ 巨潮行业 PE 不可得：{_sanitize_error(note, 120)}")
    lines.append("")
    d2_pitfall = (
        f"本次 PE {ctx.current_pe:.2f}x vs 行业中位 {ind_median:.2f}x（溢价 {premium:+.1f}%），"
        "若把溢价直接等同于「高估应回避」，可能忽略龙头合理溢价与成长性差异。"
        if premium is not None and ctx.current_pe is not None and ind_median is not None else
        (
            f"本次 PE {ctx.current_pe:.2f}x 但缺少 ≥3 家同行中位数，"
            "不宜用绝对 PE 水平判断行业相对贵贱。"
            if ctx.current_pe is not None else
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

    # D-② PE vs 行业中位数（状态行同源）
    _d2_ok = ctx.current_pe is not None and ctx.industry_peers.get("sufficient")
    _d2_s = f"PE={ctx.current_pe:.2f}x" if _d2_ok else "数据不足"
    status_rows.append(("D-②", "PE vs行业中位数", _d2_ok, _d2_s))

    # D-③ 隐性预期差
    lines.append("#### D-③ 隐性预期差")
    ig: dict[str, Any] = {}
    # C2-a：rf/_ig_attempted 预置中性值——PE 不可得分支与下方 d3_pitfall/
    # 状态行也引用（原实现在块外预置 risk_free_is_default=False，同款写法）。
    rf: dict[str, Any] = {"rate_pct": None, "source": "", "currency": "",
                          "is_default": False, "is_wrong_currency": False,
                          "is_currency_unconfirmed": False, "rf_usable": False,
                          "label": ""}
    _ig_attempted = False
    if ctx.current_pe is not None and ctx.current_pe > 0:
        erp_data = ctx.ms.get("erp") or {}
        # C2-a：优先人民币口径（cn10y）；仅美元口径时显式标注币种并暂停
        # 方向解读（与 DCF/风险行同一降级通道，report-conventions §9.3）。
        rf = resolve_rf(erp_data)
        if rf["is_default"]:
            _rf_invalid = (f"（{rf['degraded_note']}）" if rf.get("degraded_note") else "")
            lines.append(
                f"- 无风险利率不可得{_rf_invalid}，暂停隐含增长率计算、与 CAGR 比较及方向判断；"
                "须补同估值时点的实际利率。[来源: market_structure.erp.cn10y/dgs10]"
            )
        elif rf["is_wrong_currency"]:
            lines.append(
                f"- ⚠️ 无风险利率仅有美元口径（{rf['rate_pct']:.2f}%，{rf['label']}），"
                "与 A 股折现率币种不一致——暂停隐含增长率与方向解读；"
                "须补同币种人民币利率。[来源: market_structure.erp.cn10y 不可得]"
            )
        elif rf["is_currency_unconfirmed"]:
            # R1：来源/币种未知不是可用状态——同美元口径一样暂停方向解读
            lines.append(
                f"- ⚠️ 无风险利率来源/币种未确认（{rf['rate_pct']:.2f}%，{rf['label']}）——"
                "无法确认与 A 股折现率同币种，暂停隐含增长率与方向解读；"
                "须补带来源标注的人民币利率。[来源: market_structure.erp.y10_source 缺口]"
            )
        else:
            _ig_attempted = True
            risk_free = rf["rate_pct"] / 100.0
            from lib.valuation import implied_growth
            ig = implied_growth(ctx.current_pe, risk_free, erp=0.06, sensitivity=True)
        if ig.get("g_implied") is not None:
            lines.append(f"- 当前 PE(TTM)：**{ig['pe']}x**")
            rf_label = f"{ig['risk_free_rate'] * 100:.2f}%（{rf['label']}）"
            lines.append(f"- 10Y 国债收益率：**{rf_label}**")
            lines.append(f"- ERP 假设：**6%**（保守基准）")
            lines.append(f"- 折现率 r：**{ig['r'] * 100:.2f}%**" if ig.get("r") else "- 折现率：不可得")
            lines.append(
                f"- **市场隐含增长率 g_implied：约 {ig['g_implied'] * 100:.2f}%** "
                f"{_D3_SOURCE_LABEL}"
            )
            lines.append(
                f"- g_implied 敏感性带（r±1pp）：{ig['g_band_down'] * 100:.2f}% ~ "
                f"{ig['g_band_up'] * 100:.2f}%（对应 r={ig['r'] * 100:.2f}% ±1pp，"
                f"r 口径 = 10Y {rf_label} + ERP {ig['erp'] * 100:.0f}%；与模块 4 D-③ 同源）"
            )
            lines.append("")
            cagr_text = f"{ctx.cagr:+.2f}%" if ctx.cagr is not None else "不可得"
            cagr_years_label = f"{ctx.cagr_years_span:.1f}" if ctx.cagr_years_span is not None else "?"
            np_cagr_text = f"{ctx.np_cagr:+.2f}%" if ctx.np_cagr is not None else "不可得"
            np_cagr_years_label = f"{ctx.np_cagr_years_span:.1f}" if ctx.np_cagr_years_span is not None else "?"
            lines.append(f"- 实际近 {cagr_years_label} 年营收 CAGR：{cagr_text} [来源: financials]")
            lines.append(
                f"- 实际近 {np_cagr_years_label} 年净利润 CAGR：{np_cagr_text} [来源: financials]"
            )
            lines.append("- 一致预期：无可靠数据，跳过")
            lines.append("")
            g_implied_pct = ig["g_implied"] * 100
            ref_cagr, ref_label = _growth_reference(ctx.cagr, ctx.np_cagr)
            ref_text = cagr_text if ctx.cagr is not None else np_cagr_text
            # REV-04 全文补充（2026-10-07 主线收尾）：原「解读」按差值大小给
            # 「偏乐观/偏悲观、可能存在低估」或「接近=定价基本反映历史增长」——
            # 均为把条件模型读数与历史事实的差值读作定价裁决；上一稿仍输出
            # 「相差 X pp（相对 Y%）」。现统一为：分别列两个读数 + 不可直接比较
            # 的原因——**不输出差值/相对差**，不作方向裁决（与 5c/5d/核心变量同口径）。
            if ref_cagr is not None:
                lines.append(
                    f"**解读：** 市场隐含增长（{g_implied_pct:.2f}%）是**条件模型读数**"
                    "（永续口径，取 r 与 PE 假设）；实际"
                    f"{ref_label} CAGR（{ref_text}）是**有限历史区间的已发生增速**——"
                    "两者变量与时间假设不同，不可直接相减或换算为「高估/低估」裁决；"
                    "须分别核查模型假设（收益分配、风险溢价、盈利与可分配现金流差异）"
                    "与增长可持续性证据。"
                )
                if ctx.cagr is not None and ctx.np_cagr is not None and abs(ctx.cagr - ctx.np_cagr) > 5:
                    lines.append(
                        f"补充：营收 CAGR（{cagr_text}）与净利润 CAGR（{np_cagr_text}）分化较大，"
                        "解读时优先核对利润率变化与非经常性损益。"
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
            if _ig_attempted:
                lines.append(f"数据不足：[{ig.get('error', '隐含增长率计算失败')}]")
    else:
        lines.append("数据不足：[PE 非正或不可得，无法计算隐含增长率]")
    lines.append("")
    g_implied = ig.get("g_implied")
    # 前两个分支末尾各自内联来源标签：本行经 _law10_hint 渲染为独立 blockquote
    # （「> - **常见分析误区：** …」），上方 3 行内没有任何 [来源:] 标签，F2 的
    # 3 行窗口覆盖不到，必须在自身行内标注。第三分支无派生数字，不加标签。
    d3_pitfall = (
        f"本次 PE {ctx.current_pe:.2f}x → g_implied 约 {g_implied * 100:.2f}%，"
        f"营收 CAGR {ctx.cagr:+.2f}%"
        + (f"、净利润 CAGR {ctx.np_cagr:+.2f}%" if ctx.np_cagr is not None else "")
        + "；若把两者差距直接等同于「高估/低估」，"
        "可能忽略 ERP 假设（6%）与永续增长简化模型的局限。"
        f" {_D3_SOURCE_LABEL}"
        if ctx.current_pe and g_implied is not None and ctx.cagr is not None else
        (
            f"本次 g_implied 约 {g_implied * 100:.2f}%，但缺少可比 CAGR，"
            "不宜单独用隐含增长率做方向性结论。"
            f" {_D3_SOURCE_LABEL}"
            if ctx.current_pe and g_implied is not None else
            ("本次无风险利率不可得、币种不一致或来源未确认（详见 D-③），"
             "隐含增长率计算已暂停。"
             if ctx.current_pe is not None and ctx.current_pe > 0 and not rf["rf_usable"]
             else "本次 PE 或 g_implied 不可得，戈登反推不适用。")
        )
    )
    d3_next_steps = ["核对 10Y 国债与 ERP 假设是否匹配当前宏观环境"]
    if ctx.current_pe is not None and ctx.current_pe > 0 and not rf["rf_usable"]:
        d3_next_steps.append("先取得同估值时点的实际利率，再复核隐含增长与 CAGR")
    else:
        d3_next_steps.extend([
            "对比 g_implied 与近 3 年营收/净利润 CAGR、管理层指引增速",
            "PE>50 时仅作方向性参考，不作精确估值结论",
        ])
    lines.append(_law10_hint(
        "g_implied 回答「当前 PE 隐含了多高的永续增长预期」——是预期差分析的定量锚点。",
        d3_pitfall,
        d3_next_steps,
    ))
    lines.append("")

    # D-③ 隐性预期差（状态行同源：ig 为本函数 D-③ 块局部）
    _d3_implied = None
    try:
        _d3_implied = ig.get("g_implied")  # type: ignore[union-attr]
    except (NameError, AttributeError):
        pass
    _d3_ok = ctx.current_pe is not None and ctx.current_pe > 0 and _d3_implied is not None
    _d3_s = (f"g_implied={_d3_implied * 100:.2f}%" if _d3_ok else
             "无风险利率缺口，计算暂停" if ctx.current_pe is not None and ctx.current_pe > 0 and not rf["rf_usable"]
             else "数据不足")
    status_rows.append(("D-③", "隐性预期差", _d3_ok, _d3_s))

    return lines


# --- _peer_comparison_table ---
def _peer_comparison_table(industry_peers: dict) -> list[str]:
    """⑨ 同行可比公司表（PE/PB/ROE/营收增速 + 同行分位排名；insufficient 时零输出）。"""
    lines: list[str] = []
    # 同行可比公司表
    if industry_peers.get("sufficient"):
        lines.append("### 同行可比公司")
        lines.append("")
        target = industry_peers.get("target") or {}
        rankings = industry_peers.get("rankings") or {}
        lines.append(f"行业：{industry_peers.get('industry_name', '?')}（同行池 {len(industry_peers.get('peers', []))} 家）")
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
        # 分位排名（分位须伴随中位数——此处取同行组中位数，report-conventions.md §9.2 估值分位规则 3）
        from lib.valuation import median_of
        peer_vals: dict[str, list[float]] = {}
        for _p in industry_peers.get("peers", []):
            for _m in ("pe_ttm", "pb", "roe", "revenue_yoy"):
                _v = _p.get(_m)
                if isinstance(_v, (int, float)) and not isinstance(_v, bool):
                    peer_vals.setdefault(_m, []).append(float(_v))
        rk_lines = []
        for metric, label in [("pe_ttm", "PE"), ("pb", "PB"), ("roe", "ROE"), ("revenue_yoy", "营收增速")]:
            pct_key = f"{metric}_pct"
            rk_key = f"{metric}_rank"
            tot_key = f"{metric}_total"
            pct_v = rankings.get(pct_key)
            rk_v = rankings.get(rk_key)
            tot_v = rankings.get(tot_key)
            if pct_v is not None:
                med_v = median_of(peer_vals.get(metric) or [])
                unit = "x" if metric in ("pe_ttm", "pb") else ""
                med_s = f"，同行中位 {med_v:.2f}{unit}" if med_v is not None else ""
                rk_lines.append(f"- {label}：分位 **{pct_v}%**（排名 {rk_v}/{tot_v}{med_s}）")
        if rk_lines:
            lines.append("**分位排名（在同行中的位置）：**")
            lines.extend(rk_lines)
            lines.append("")
        lines.append("> 分位排名越高，表示在同行中数值越高。PE/PB 分位高 = 估值高于多数同行。ROE/营收增速分位高 = 盈利能力或增长优于同行。")
        # C2-d：排名分母 = 各指标**有效样本数**（缺字段的同行不入该指标池），
        # 与「同行池」总数、中位数样本数互不相同——显式说明，防口径混读。
        lines.append("> 备注：排名分母为对应指标的有效样本数（各指标口径不同），与同行池总数及中位数样本数可能不一致。")
    return lines


def _section_fundamentals_layered(
    dims: dict[str, dict], collection: dict, symbol: str, *, val_cache: dict | None = None,
    analysis: list[dict] | None = None,
) -> str:
    """v0.1.3 Phase 2：分层激活基本面 12 题 + LAW 10/14/15 完整框架。

    P0-3 升级：新增「核心判断摘要」与「12题回答状态表」。
    """
    # LAW 17: 标题含判断性描述
    lines = ["## 4. 12 题分层激活 · 静态基本面深度检验", ""]
    lines.append("**结论：** 以下按 12 道核心题分层激活，覆盖生意/护城河/管理层/财务/估值/风险六大维度。")
    lines.append("")

    lines.extend(_section_4_header_mda(collection, analysis=analysis))

    # C4 v0.2.7：块②全部预取与 C6 估值派生封装进 _FundamentalsContext；
    # ③-⑩ 各块经 ctx 共享；status_rows 由各题渲染处就地 append。
    ctx = _FundamentalsContext(dims, collection, val_cache)
    status_rows: list[tuple[str, str, bool, str]] = []

    # =================================================================
    # 核心判断摘要（P0-3 升级）
    # =================================================================
    lines.extend(_core_judgment_summary(ctx))

    # =================================================================
    # 4a. 行业位置（3 题）
    # =================================================================
    lines.extend(_section_4a_industry_position(dims, ctx, status_rows))
    lines.extend(_section_4b_business_quality(dims, collection, ctx, status_rows, analysis=analysis))
    lines.extend(_section_4c_financial_quality(ctx, status_rows))
    lines.extend(_section_4d_valuation_expectation(ctx, status_rows))
    lines.extend(_peer_comparison_table(ctx.industry_peers))

    # =================================================================
    # 12题回答状态表（P0-3 升级）
    # =================================================================
    # C4-1/2/3：状态行已随 4a/4b/4c/4d 各题渲染处就地 append；数值全部
    # 引用 ctx/共享值，A-③ 为随正文的 ctx.gross_margin walk-back
    # （清单任务 4 bug）。本表纯消费 status_rows，不重推任何数值。
    lines.extend(_section_12_question_table(status_rows))
    lines.append("")
    lines.append("🔍 **待独立验证:** 基本面分析基于第三方数据源（Tushare/akshare），应逐项与公司年报/季报原始数据交叉核对。行业分类可能因数据源口径不同存在差异。估值分位/隐含增长率不构成买卖判断。")
    return "\n".join(lines)


def _section_12_question_table(
    status_rows: list[tuple[str, str, bool, str]],
) -> list[str]:
    """12 题回答状态表（P0-3 升级）：纯消费 status_rows，不重推任何数值。

    行序由各题渲染处（4a/4b/4c/4d）就地 append 保证；本函数只做
    表头 + 行 + 脚注渲染。
    """
    lines = ["\n### 12题回答状态\n"]
    lines.append("| # | 问题 | 状态 | 回答摘要 |")
    lines.append("|----|------|------|---------|")
    for qid, qtext, ok, summary in status_rows:
        lines.append(f"| {qid} | {qtext} | {'✅' if ok else '❌'} | {summary} |")
    lines.append("")
    lines.append("> ✅ = 有可用数据，❌ = 数据不足。状态反映数据完整性，不反映结论正误。")
    return lines


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
    analysis: list[dict] | None = None,
) -> str:
    # 委托给 Phase 2 分层基本面
    symbol = collection.get("symbol", "")
    return _section_fundamentals_layered(dims, collection, symbol, val_cache=val_cache, analysis=analysis)


# --- _section_technical_brief ---
def _section_technical_brief(
    dims: dict[str, dict], *, val_cache: dict | None = None,
    collection: dict[str, Any] | None = None,
) -> str:
    """§8 技术指标附录。

    collection（可选）：传入时在末尾追加均线系统表（R12g）与近端价格结构（R12e）。
    这两段原渲染在报告头部，与本节既有的「趋势」「20/60/120 日高低」行重复——
    2026-09-16 用户审阅指出头部放的是无结论数据罗列，遂下沉到本节（技术指标
    与技术指标同处）。渲染器本体复用，不重写其 None/NaN 守卫。
    """
    text = _technical_brief_core(dims, val_cache=val_cache)
    if not collection:
        return text
    # 不在此吞异常：两个渲染器内部各自已有边界守卫（None/NaN 现价、样本不足→[]），
    # 逃到这一层的异常是真缺陷；静默丢弃会让下沉内容无声消失，且读者与 QC 都无感
    # （与项目消除静默降级的既有纪律一致）。
    extras = _render_ma_system(collection) + _render_price_structure(collection)
    if not extras:
        return text
    return text + "\n\n" + "\n".join(extras)


def _technical_brief_core(
    dims: dict[str, dict], *, val_cache: dict | None = None,
) -> str:
    lines = ["## 8. 技术指标附录 · 均线/动量/波动率简报", ""]
    lines.append("**结论：** 以下为技术指标摘要，供交叉参考，不构成交易信号。")
    # R6 学术纪律固定提示行（引擎/模板层，非 AI 撰写——与 SKILL.md:544 文本一致）
    lines.append(
        "> 技术指标仅用于描述市场状态（价格与均线位置关系、MACD 方向）与交叉验证"
        "其他证据，不单独构成结论，也不构成任何操作依据。学术检验"
        "（Chen, Zhou & Wang 2018, *Physica A*）：沪深 300 期指 279 个技术策略"
        "计入交易成本后利润被完全消除。"
    )
    lines.append("")
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
    # 量行：引擎真实成交量状态（technical._volume_ratio 产出「量比 x.xx（…）」；
    # 原绑 summary_sentences[1] 实为 MA60 句，错绑修复）
    vol_s = (tech.get("volume", {}) or {}).get("status") or "—"
    # 支撑阻力行：structure.extremes 的 20/60/120 日最高/最低收盘价+日期
    # （technical._n_day_extremes 产出；原 support_resistance 键全仓无产出方，
    # 恒 "—" 死行）。数值全部直引引擎字段。
    ext = (tech.get("structure", {}) or {}).get("extremes", {})
    parts = []
    for n in (20, 60, 120):
        e = ext.get(n) or {}
        if e.get("available") and e.get("max") is not None and e.get("min") is not None:
            parts.append(
                f"{n}日高/低: {e['max']:.2f}@{e['max_date']} / {e['min']:.2f}@{e['min_date']}")
        elif e.get("available"):
            parts.append(f"{n}日: 极值不可用")
        else:
            parts.append(f"{n}日: {e.get('reason', '—')}")
    sr = "；".join(parts) or "—"
    lines.append(f"- **趋势:** {trend}")
    lines.append(f"- **量:** {vol_s}")
    lines.append(f"- **支撑阻力:** {sr}")
    return "\n".join(lines)


# --- _report_toc ---
def _report_toc(collection: dict[str, Any] | None = None) -> str:
    # LAW 17: 标题动态包含数据，Markdown 锚点不可预测，TOC 仅作视觉目录
    entries = [
        "0. 核心问题与触发源",
        "1. 当前状态快照（含估值/价格）",
        "2. 动态驱动分析",
        "3. 市场结构分析",
        "4. 静态基本面（12题）",
        "5. Bull/Bear 情景",
        "6. 左/右概率判断",
        "7. 风险与不确定性",
        "8. 技术指标附录",
        "PE Band（5年轨道）",
    ]
    # R12g-A 头部区块（brief/concise/full 三模式 engine extras 均渲染）——
    # 标签与渲染顺序单一来源 _base._R12G_HEADER_SECTIONS，禁止在此手写新增条目。
    # 「连板结构」为条件渲染（近 5 日 ≥2 涨停触发，见 _limit_streak_section_active），
    # 未触发时 TOC 不得列出不存在的章节（batch-test P1-3）。
    collection = collection or {}
    for label, _fn in _R12G_HEADER_SECTIONS:
        if label == _LIMIT_STREAK_LABEL and not _limit_streak_section_active(collection):
            continue
        entries.append(label)
    # 附录「数据质量与引擎自检」：R12g 注册表之外无注册机制，故为静态条目。
    # 仅在正文确实渲染该节时列出——空节不入目录（失败/缺数据时 _render_engine_selfcheck_appendix
    # 返回空串），避免 TOC 指向不存在的章节。
    if _render_engine_selfcheck_appendix(collection):
        entries.append(_ENGINE_SELFCHECK_LABEL)
    entries.append("引用来源")
    lines = ["## 目录", ""]
    lines.extend(f"- {label}" for label in entries)
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
