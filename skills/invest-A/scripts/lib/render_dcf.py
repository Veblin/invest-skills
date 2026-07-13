"""DCF valuation section rendering."""
from __future__ import annotations

import logging
from typing import Any

from lib.nums import safe_float as _safe_num
from lib.technical import sort_kline_asc
from lib.stats import calc_beta

from .render_utils import _compute_metric_cagr, _get_dim_data, _fmt_v2

logger = logging.getLogger(__name__)

_DCF_TERMINAL_G_DEFAULT = 0.025  # 与 D-③ 10Y 国债默认假设一致，作为长期宏观增长代理 [推测，待验证]



# --- _dcf_compute_beta ---
def _dcf_compute_beta(kline_data: list[dict] | None) -> dict:
    """从个股 K 线 + 沪深300 基准计算 Beta。

    对齐个股与沪深300 的交易日，计算日收益率序列，
    调用 valuation.calc_beta() 做回归。

    Args:
        kline_data: 个股 K 线数据（list of dict，含 trade_date/close）

    Returns:
        {"beta": float, "r_squared": float | None, "observations": int,
         "source": str, "is_default": bool}
        计算失败时返回 {"beta": 1.0, "is_default": True, ...}
    """

    stock_by_date: dict[str, float] = {}
    if kline_data and isinstance(kline_data, list):
        for r in kline_data:
            if r.get("close") is None:
                continue
            td = str(r.get("trade_date") or "").replace("-", "").replace("/", "")[:8]
            if len(td) == 8 and td.isdigit():
                stock_by_date[td] = float(r["close"])

    if len(stock_by_date) < 12:
        return {
            "beta": 1.0, "r_squared": None, "observations": len(stock_by_date),
            "source": "默认值（个股 K 线不足 12 个交易日）",
            "is_default": True,
        }

    # 获取沪深300 基准数据
    try:
        from lib.collector import _akshare_hs300_dated_closes
        bench_dated = _akshare_hs300_dated_closes(days=max(130, len(stock_by_date) + 10))
    except Exception:
        logger.warning("沪深300基准数据获取失败，Beta 使用默认值 1.0", exc_info=True)
        bench_dated = []

    if not bench_dated:
        return {
            "beta": 1.0, "r_squared": None, "observations": len(stock_by_date),
            "source": "默认值（HS300 基准数据不可得）",
            "is_default": True,
        }

    bench_by_date = dict(bench_dated)
    common = sorted(set(stock_by_date) & set(bench_by_date))
    if len(common) < 12:
        return {
            "beta": 1.0, "r_squared": None, "observations": len(common),
            "source": f"默认值（个股与HS300交集仅 {len(common)} 个交易日，< 12）",
            "is_default": True,
        }

    # 对齐收盘价后计算日收益率
    stock_closes = [stock_by_date[d] for d in common]
    bench_closes = [bench_by_date[d] for d in common]
    stock_returns = [
        (stock_closes[i] - stock_closes[i - 1]) / stock_closes[i - 1]
        for i in range(1, len(stock_closes))
    ]
    bench_returns = [
        (bench_closes[i] - bench_closes[i - 1]) / bench_closes[i - 1]
        for i in range(1, len(bench_closes))
    ]

    from lib.stats import calc_beta
    beta_result = calc_beta(stock_returns, bench_returns)

    if beta_result.get("beta") is None:
        return {
            "beta": 1.0, "r_squared": None, "observations": len(stock_returns),
            "source": f"默认值（回归失败: {beta_result.get('error', '未知')}）",
            "is_default": True,
        }

    return {
        "beta": beta_result["beta"],
        "r_squared": beta_result.get("r_squared"),
        "observations": beta_result.get("observations", len(stock_returns)),
        "source": f"个股 vs 沪深300 日收益率回归（{beta_result.get('observations', len(stock_returns))} 个对齐交易日）",
        "is_default": False,
    }


# --- _dcf_try_wacc ---
def _dcf_try_wacc(
    financials: dict, market_structure: dict,
    kline_data: list[dict] | None = None,
) -> tuple[dict | None, list[str]]:
    """尝试计算 WACC（CAPM）。

    risk_free_rate / erp 沿用 D-③ 已建立的降级惯例（10Y 国债不可得时用 2.5%
    默认值 + "[推测，待验证]" 标注，ERP 固定 6% 保守基准）。

    beta 优先级：
    1. financials / market_structure 中预存的 beta（未来版本直接接入）
    2. 从 kline_data + HS300 基准实时计算（_dcf_compute_beta）
    3. 默认值 1.0（标注 [推测，待验证]）

    Returns:
        (wacc_result, missing_reasons)。wacc_result 为 None 时 missing_reasons 非空。
    """
    missing: list[str] = []

    beta = financials.get("beta")
    if beta is None:
        beta = market_structure.get("beta")

    beta_meta: dict = {}
    if beta is None:
        beta_meta = _dcf_compute_beta(kline_data)
        beta = beta_meta["beta"]

    erp_data = market_structure.get("erp") or {}
    risk_free_raw = erp_data.get("dgs10")
    risk_free_is_default = risk_free_raw is None
    risk_free = 0.025 if risk_free_is_default else risk_free_raw / 100.0

    if missing:
        return None, missing

    from lib.valuation import calc_wacc

    wacc_result = calc_wacc(beta=beta, risk_free_rate=risk_free, erp=0.06, cost_of_debt=None)
    wacc_result["risk_free_is_default"] = risk_free_is_default
    wacc_result["beta"] = beta
    wacc_result["beta_is_default"] = beta_meta.get("is_default", False)
    wacc_result["beta_r_squared"] = beta_meta.get("r_squared")
    wacc_result["beta_observations"] = beta_meta.get("observations")
    wacc_result["beta_source"] = beta_meta.get("source", "")
    return wacc_result, []


# --- _dcf_extract_shares ---
def _dcf_extract_shares(dims: dict) -> tuple[float | None, str]:
    """从 basic_info 或 total_mv/price 提取总股本（股）。

    Returns:
        (shares: float | None, source_description: str)
    """
    # Source 1: basic_info 中 akshare 的 "总股本" 字段
    basic_dim = dims.get("basic_info") or {}
    basic_data = basic_dim.get("data")
    if isinstance(basic_data, dict):
        raw = basic_data.get("总股本") or basic_data.get("total_share") or basic_data.get("float_share")
        if raw is not None:
            try:
                shares = float(str(raw).strip().replace(",", ""))
                if shares > 0:
                    return shares, "akshare stock_individual_info_em \"总股本\""
            except (TypeError, ValueError):
                pass
    # 也搜索 all_sources（akshare 可能在二级源中）
    for sd in (basic_dim.get("_meta") or {}).get("all_sources") or []:
        if isinstance(sd, dict) and sd.get("success"):
            sd_data = sd.get("data") or {}
            if isinstance(sd_data, dict):
                for k in ("总股本", "total_share", "float_share"):
                    v = sd_data.get(k)
                    if v is not None:
                        try:
                            shares = float(str(v).strip().replace(",", ""))
                            if shares > 0:
                                return shares, f"basic_info all_sources \"{k}\""
                        except (TypeError, ValueError):
                            continue

    # Source 2: total_mv / price 推导
    val_dim = dims.get("valuation") or {}
    val_data = val_dim.get("data")
    if isinstance(val_data, list) and val_data:
        val_sorted = sort_kline_asc(val_data)
        latest_mv = val_sorted[-1].get("total_mv") if val_sorted else None
        if latest_mv is not None and _safe_num(latest_mv) and _safe_num(latest_mv) > 0:
            latest_mv = float(latest_mv)
            kline_dim = _get_dim_data(dims, "kline")
            if isinstance(kline_dim, list) and kline_dim:
                k_sorted = sort_kline_asc(kline_dim)
                price = k_sorted[-1].get("close") if k_sorted else None
                if price is not None and _safe_num(price) and float(price) > 0:
                    shares = latest_mv / float(price)
                    return shares, "total_mv / 当前股价 推导"

    return None, "不可得"


# --- _dcf_extract_net_debt ---
def _dcf_extract_net_debt(financials: dict) -> tuple[float, str]:
    """从 financials dcf_preprocess 提取净债务。

    Returns:
        (net_debt: float, source_description: str)
    """
    dcf_pre = financials.get("dcf_preprocess") or {}
    net_debt_info = dcf_pre.get("net_debt") or {}
    nd = net_debt_info.get("net_debt")
    if nd is not None and isinstance(nd, (int, float)):
        return float(nd), "valuation.calc_net_debt（最新财报）"
    # 降级：自己从 financials 行计算
    from lib.valuation import extract_financial_rows, _latest_financial_row
    rows = extract_financial_rows(financials)
    latest = _latest_financial_row(rows)
    if latest:
        debt = _safe_num(latest.get("total_liab") or latest.get("debt_total"))
        cash = _safe_num(latest.get("money_cap") or latest.get("cash"))
        if debt is not None and cash is not None:
            nd = debt - cash
            return nd, "总负债 - 货币资金（最新财报，近似）"
    return 0.0, "默认值 0（净债务数据不可得）"


# --- _aggregate_scenario_dcf ---
def _aggregate_scenario_dcf(
    yearly_fcff: list[dict], wacc: float, terminal_g: float,
) -> dict | None:
    """将 scenario_fcff() 的显式期 FCFF 序列直接折现聚合为企业价值。

    设计决策：不复用 dcf_two_stage() 的"恒定复合增速"假设重新生成 FCFF 序列，
    而是直接对 scenario_fcff 已经按年计算好的 FCFF（其中已经反映了毛利率/EBIT
    利润率随情景偏移、折旧按营收等比例缩放等非恒定增速的动态）逐年折现——
    这比"先算出一个平均增速，再喂回 dcf_two_stage 重新按恒定增速展开"更贴近
    scenario_fcff 本身的建模假设，避免信息损失。终值仍沿用 Gordon 永续增长法，
    以显式期最后一年 FCFF 为基数。

    Returns:
        {"explicit_pv", "terminal_pv", "enterprise_value"}，
        非法参数（wacc<=terminal_g 或序列为空）返回 None。
    """
    if not yearly_fcff or wacc <= terminal_g:
        return None

    explicit_pv = 0.0
    for row in yearly_fcff:
        t = row["year"]
        fcff_t = row["fcff"]
        explicit_pv += fcff_t / (1 + wacc) ** t

    years = yearly_fcff[-1]["year"]
    fcff_final = yearly_fcff[-1]["fcff"]
    terminal_fcff = fcff_final * (1 + terminal_g)
    terminal_value = terminal_fcff / (wacc - terminal_g)
    terminal_pv = terminal_value / (1 + wacc) ** years
    enterprise_value = explicit_pv + terminal_pv

    return {
        "explicit_pv": round(explicit_pv, 2),
        "terminal_pv": round(terminal_pv, 2),
        "enterprise_value": round(enterprise_value, 2),
    }


# --- _consensus_growth_from_forecasts ---
def _consensus_growth_from_forecasts(research_summary: dict | None) -> float | None:
    """从机构净利润预测（profit_forecasts，按 quarter 聚合的 avg_np_100m）
    尝试推导一致预期年化增速。

    保守策略：只接受 quarter 字段可清洗出恰好 4 位数字（视为年度，如 "2026"、
    "2026年" 等）的记录，取最早与最晚两个年份的均值净利润计算年化复合增速。
    quarter 字段不是清晰的年度标签（如混杂的季度报告期）时返回 None——
    不为了凑数据而把季度数字误当年度使用。
    """
    if not research_summary:
        return None
    forecasts = research_summary.get("profit_forecasts") or []
    by_year: dict[int, float] = {}
    for f in forecasts:
        val = f.get("avg_np_100m")
        if val is None:
            continue
        digits = "".join(ch for ch in str(f.get("quarter", "")) if ch.isdigit())
        if len(digits) == 4:
            year = int(digits)
            by_year.setdefault(year, val)
    years_sorted = sorted(by_year.items())
    if len(years_sorted) < 2:
        return None
    (y0, v0), (y1, v1) = years_sorted[0], years_sorted[-1]
    if v0 is None or v1 is None or v0 <= 0 or y1 <= y0:
        return None
    return (v1 / v0) ** (1 / (y1 - y0)) - 1


# --- _section_dcf_valuation ---
def _section_dcf_valuation(
    dims: dict, collection: dict, symbol: str,
    veto_triggered: bool = False,
) -> str:
    """D-④/D-⑤/D-⑥：DCF 三情景估值区间 + 三角对照表 + WACC×终值敏感性矩阵。

    若 veto_triggered=True，只返回一行"研究终止条件触发，估值段落已跳过"，
    不渲染任何 DCF 数值（Step 7/8 快速否决检测触发时会传入 True）。

    合规红线（AGENTS.md 约束1 / CLAUDE.md LAW 6）：不输出单一"目标价"，
    只输出企业价值区间 + 三情景假设 + 概率权重，并注明仅供参考。
    """
    header = "## D. DCF 估值区间与三角对照"
    if veto_triggered:
        return "\n".join([header, "", "**研究终止条件触发，估值段落已跳过。**"])

    lines: list[str] = [
        header, "",
        "> 本节为规则驱动的估值区间估算（非分析师预测、非单一价格结论），"
        "全部假设标注来源，仅供研究参考，不构成投资建议。",
        "",
    ]

    financials = dims.get("financials") or {}
    market_structure = collection.get("market_structure") or {}
    kline_data = _get_dim_data(dims, "kline")
    if isinstance(kline_data, list) and kline_data:
        kline_data = sort_kline_asc(kline_data)

    lines.append("#### D-④ DCF 三情景估值区间")
    lines.append("")

    wacc_result, wacc_missing = _dcf_try_wacc(financials, market_structure, kline_data=kline_data)
    if wacc_result is None:
        lines.append("数据不足，WACC 无法计算，DCF 段落跳过。缺失项：")
        for m in wacc_missing:
            lines.append(f"- {m}")
        lines.append("")
        lines.append("[来源: valuation.calc_wacc 所需参数缺失]")
        return "\n".join(lines)

    wacc = wacc_result["wacc"]
    terminal_g = _DCF_TERMINAL_G_DEFAULT

    if wacc <= terminal_g:
        lines.append(
            f"数据不足，WACC（{wacc*100:.2f}%）不高于永续增长率假设"
            f"（{terminal_g*100:.2f}%），无法计算终值，DCF 段落跳过。"
        )
        lines.append("")
        lines.append("[来源: valuation.calc_wacc / dcf_two_stage 参数约束]")
        return "\n".join(lines)

    from lib.valuation import (
        dcf_sensitivity,
        default_terminal_g_range,
        default_wacc_range,
        scenario_fcff,
        triangle_check,
    )

    scenario_results = {sc: scenario_fcff(financials, scenario=sc) for sc in ("bear", "base", "bull")}
    insufficient_items: list[str] = []
    for sc, res in scenario_results.items():
        if "error" in res:
            insufficient_items.extend(f"[{sc}] {item}" for item in res.get("insufficient_data", []))
    if insufficient_items:
        lines.append("数据不足，三情景 FCFF 预测无法生成，DCF 段落跳过。缺失项：")
        for item in sorted(set(insufficient_items)):
            lines.append(f"- {item}")
        lines.append("")
        lines.append("[来源: valuation.scenario_fcff 所需财务字段缺失]")
        return "\n".join(lines)

    scenario_ev: dict[str, dict] = {}
    for sc, res in scenario_results.items():
        ev = _aggregate_scenario_dcf(res["yearly_fcff"], wacc, terminal_g)
        if ev is None:
            lines.append(f"数据不足，{sc} 情景企业价值计算失败（WACC/终值增长率参数非法）。")
            lines.append("")
            return "\n".join(lines)
        scenario_ev[sc] = ev

    wacc_label = f"{wacc*100:.2f}%"
    rf_note = (
        "10Y 国债使用默认值 2.5% [推测，待验证]" if wacc_result.get("risk_free_is_default")
        else "10Y 国债取自 FRED/akshare"
    )
    # Beta 来源说明
    beta_val = wacc_result.get("beta")
    beta_source = wacc_result.get("beta_source", "")
    beta_r2 = wacc_result.get("beta_r_squared")
    beta_note_parts = [f"β={beta_val:.3f}"]
    if beta_r2 is not None:
        beta_note_parts.append(f"R²={beta_r2:.3f}")
    if wacc_result.get("beta_is_default"):
        beta_note_parts.append(f"[推测，待验证: {beta_source}]")
    else:
        beta_note_parts.append(f"[{beta_source}]")
    beta_note = "，".join(beta_note_parts)

    lines.append(
        f"- WACC：**{wacc_label}**（cost_of_equity 近似，因债务成本/权重数据不可得；"
        f"{rf_note}；ERP 假设 6%；{beta_note}）"
    )
    lines.append(f"- 永续增长率假设：**{terminal_g*100:.2f}%**[推测，待验证：长期宏观增长代理，与 D-③ 一致]")
    lines.append("")

    _sc_label = {"bear": "悲观情景", "base": "中性情景", "bull": "乐观情景"}

    # 提取总股本和净债务（用于每股换算）
    shares, shares_source = _dcf_extract_shares(dims)
    net_debt, nd_source = _dcf_extract_net_debt(financials)

    # 当前股价（用于安全边际计算）
    current_price: float | None = None
    if kline_data and len(kline_data) >= 1:
        cp = kline_data[-1].get("close")
        if cp is not None:
            current_price = float(cp)

    if shares is not None:
        lines.append("| 情景 | 概率权重 | 核心假设（营收增速 / 毛利率） | 企业价值（元） | 每股参考价（元） |")
        lines.append("|---|---|---|---|---|")
    else:
        lines.append("| 情景 | 概率权重 | 核心假设（营收增速 / 毛利率） | 企业价值（元） |")
        lines.append("|---|---|---|---|")

    from lib.valuation import calc_ev_to_equity

    per_share_results: dict[str, dict] = {}
    for sc in ("bull", "base", "bear"):
        res = scenario_results[sc]
        assump = res["assumptions"]
        ev = scenario_ev[sc]
        if shares is not None:
            eq = calc_ev_to_equity(ev["enterprise_value"], net_debt, int(shares))
            per_share_results[sc] = eq
            ps_str = f"{eq['per_share']:.2f}" if eq.get("per_share") is not None else "N/A"
            lines.append(
                f"| {_sc_label[sc]} | {res['probability']*100:.0f}% | "
                f"营收增速 {assump['revenue_growth']*100:+.1f}%，毛利率 {assump['gross_margin_assumption']:.1f}% | "
                f"{ev['enterprise_value']:,.0f} | "
                f"{ps_str} |"
            )
        else:
            lines.append(
                f"| {_sc_label[sc]} | {res['probability']*100:.0f}% | "
                f"营收增速 {assump['revenue_growth']*100:+.1f}%，毛利率 {assump['gross_margin_assumption']:.1f}% | "
                f"{ev['enterprise_value']:,.0f} |"
            )
    lines.append("")

    def _assump_text(sc: str) -> str:
        a = scenario_results[sc]["assumptions"]
        return f"营收增速{a['revenue_growth']*100:+.1f}%、毛利率{a['gross_margin_assumption']:.1f}%"

    # 每股参考价 + 安全边际 段落
    if shares is not None and per_share_results:
        lines.append("**每股估值参考价**（总股本 "
                     f"{shares:,.0f} 股 [来源: {shares_source}]，"
                     f"净债务 {net_debt:,.0f} 元 [来源: {nd_source}]）：")
        lines.append("")
        for sc in ("bull", "base", "bear"):
            eq = per_share_results[sc]
            ps = eq.get("per_share")
            ev = scenario_ev[sc]
            ps_display = f"每股 ~{ps:.2f} 元" if ps is not None else "每股 N/A"
            margin_display = ""
            if ps is not None and current_price is not None and current_price > 0:
                margin = (ps - current_price) / current_price * 100
                margin_display = f"（安全边际 {margin:+.1f}%）"
            lines.append(
                f"- {_sc_label[sc]}（假设{_assump_text(sc)}，"
                f"概率 {scenario_results[sc]['probability']*100:.0f}%）："
                f"企业价值 {ev['enterprise_value']:,.0f} 元，{ps_display}{margin_display}"
            )
        lines.append("")
        # 净债务为负（净现金）时提示
        if net_debt < 0:
            lines.append(
                "> 净债务为负（净现金状态），企业价值换算为权益价值时做加法，"
                "每股参考价相应提高。"
            )
            lines.append("")
        lines.append(
            "> 以上为基于 DCF 模型的多情景估值参考，假设前提与概率权重如上表所示，"
            "仅供参考，不构成投资建议。"
        )
        lines.append("")
    else:
        lines.append(
            f"乐观情景（假设{_assump_text('bull')}，概率 {scenario_results['bull']['probability']*100:.0f}%）："
            f"企业价值 {scenario_ev['bull']['enterprise_value']:,.0f} 元；"
            f"中性情景（假设{_assump_text('base')}，概率 {scenario_results['base']['probability']*100:.0f}%）："
            f"企业价值 {scenario_ev['base']['enterprise_value']:,.0f} 元；"
            f"悲观情景（假设{_assump_text('bear')}，概率 {scenario_results['bear']['probability']*100:.0f}%）："
            f"企业价值 {scenario_ev['bear']['enterprise_value']:,.0f} 元。"
            "仅供参考，不构成投资建议。"
        )
        lines.append("")
        lines.append(
            "股本/每股换算数据不可得（" + shares_source + "），"
            "以上仅为企业价值区间，不换算为每股价值或单一价格结论。"
        )
    for sc in ("bear", "base", "bull"):
        if scenario_results[sc].get("growth_sign_caveat"):
            lines.append(f"⚠️ [{sc}] {scenario_results[sc]['growth_sign_caveat']}")
    lines.append("")
    lines.append(
        "[来源: valuation.scenario_fcff（三情景假设）+ 本函数对显式期 FCFF 逐年折现"
        "并叠加 Gordon 永续增长终值（等价于 valuation.dcf_two_stage 的终值公式）]"
    )
    lines.append("")

    # =================================================================
    # D-⑤ 估值三角对照表
    # =================================================================
    lines.append("#### D-⑤ 估值三角对照")
    lines.append("")
    dcf_growth = scenario_results["base"]["assumptions"]["revenue_growth"]

    fin_raw = _get_dim_data(dims, "financials")
    fin_list: list[dict] = []
    if fin_raw and isinstance(fin_raw, list):
        fin_list = sort_kline_asc(fin_raw)
    base_assumptions = scenario_results["base"].get("assumptions", {})
    hist_growth = base_assumptions.get("base_revenue_growth_cagr")
    if hist_growth is None:
        hist_cagr_pct, _ = _compute_metric_cagr(fin_list, "revenue")
        hist_growth = hist_cagr_pct / 100.0 if hist_cagr_pct is not None else None

    research_dim = dims.get("research", {})
    research_summary = research_dim.get("research_summary") or {}
    consensus_growth = _consensus_growth_from_forecasts(research_summary)

    triangle = triangle_check(dcf_growth, consensus_growth, hist_growth)
    lines.append("| 对照维度 | 数值 | 来源 |")
    lines.append("|---|---|---|")
    for row in triangle["rows"]:
        lines.append(f"| {row['label']} | {row['display']} | {row['source']} |")
    lines.append("")
    if triangle.get("divergence_note"):
        lines.append(f"→ {triangle['divergence_note']}")
    else:
        lines.append("三者数值接近，或存在不可得项，未生成分歧提示。")
    lines.append("")
    lines.append("[来源: valuation.triangle_check；机构一致预期增速取自 research 维度 profit_forecasts"
                 "（需至少 2 个可解析年度的净利润预测均值），历史营收 CAGR 取自 financials 维度]")
    lines.append("")

    # =================================================================
    # D-⑥ WACC × 永续增长率敏感性矩阵
    # =================================================================
    lines.append("#### D-⑥ WACC × 永续增长率敏感性矩阵")
    lines.append("")
    fcff_base = ((financials.get("dcf_preprocess") or {}).get("fcff") or {}).get("fcff")
    if fcff_base is None:
        fcff_base = scenario_results["base"]["yearly_fcff"][0]["fcff"]
    growth_s1 = scenario_results["base"]["assumptions"]["revenue_growth"]

    wacc_range = default_wacc_range(wacc)
    terminal_g_range = default_terminal_g_range(terminal_g)
    sensitivity = dcf_sensitivity(fcff_base, growth_s1, 5, wacc_range, terminal_g_range)

    if "error" in sensitivity:
        lines.append(f"数据不足：[{sensitivity['error']}]")
    else:
        header_row = "| WACC ＼ 永续增长率 | " + " | ".join(sensitivity["terminal_g_labels"]) + " |"
        lines.append(header_row)
        lines.append("|" + "---|" * (len(sensitivity["terminal_g_labels"]) + 1))
        for wl, row in zip(sensitivity["wacc_labels"], sensitivity["matrix"]):
            cells = " | ".join(
                f"{c:,.0f}" if isinstance(c, (int, float)) else str(c) for c in row
            )
            lines.append(f"| {wl} | {cells} |")
        lines.append("")
        lines.append(
            f"基准：FCFF={fcff_base:,.0f} 元（{'最新一期实际值' if ((financials.get('dcf_preprocess') or {}).get('fcff') or {}).get('fcff') is not None else '基情景首年预测值'}），"
            f"显式期增速={growth_s1*100:.1f}%（基情景），预测年数=5。{sensitivity['note']}"
        )
        lines.append("")
        lines.append("[来源: valuation.dcf_sensitivity / default_wacc_range / default_terminal_g_range]")
    lines.append("")

    return "\n".join(lines)

