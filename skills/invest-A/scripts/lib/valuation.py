"""估值分析模块。

输入: PE_TTM/PB/PS 历史序列（由 collector 采集后传入）
输出: 分位、区间标签、估值描述文本

原则:
  - 不输出买卖建议、目标价、仓位建议
  - 输出格式为"状态描述"而非"交易信号"
  - 数据不足时标注而非静默跳过
"""

from __future__ import annotations

from typing import Any


def percentile_rank(seq: list[float], current: float) -> float | None:
    """计算 current 在 seq 中的百分位（严格低于 current 的比例 × 100）。

    percentile = count_(v < current) / total × 100
    即：值越低，百分位越小 → "低于历史 X% 的时间"

    使用严格小于（不包含等于），避免当前值等于历史极值时
    分位被推向极端（最小值→0%，最大值→100%），使 zone 判断更稳健。

    Args:
        seq: 历史估值序列（正数）
        current: 当前值

    Returns:
        百分位 [0, 100]，数据不足时返回 None
    """
    valid = [v for v in seq if v is not None and v > 0]
    if not valid:
        return None
    below = sum(1 for v in valid if v < current)
    return (below / len(valid)) * 100


# 与 CV-7 / 左-右概率分位边界一致（严格小于/大于，不含端点）
ZONE_LOW_THRESHOLD = 30.0
ZONE_HIGH_THRESHOLD = 70.0


def zone_label(
    pct: float,
    low_threshold: float = ZONE_LOW_THRESHOLD,
    high_threshold: float = ZONE_HIGH_THRESHOLD,
) -> str:
    """根据百分位返回估值区间标签。

    pct < 30  → "偏低"（当前值低于历史 70% 的时间）
    pct 30-70 → "适中"
    pct > 70  → "偏高"（当前值高于历史 70% 的时间）

    阈值与 render._v3_cv7_assessment 保持一致。
    """
    if pct < low_threshold:
        return "偏低"
    elif pct > high_threshold:
        return "偏高"
    return "适中"


def valuation_summary(
    pe_ttm_seq: list[float | None],
    pb_seq: list[float | None],
    current_pe: float | None = None,
    current_pb: float | None = None,
    current_ps: float | None = None,
    ps_seq: list[float | None] | None = None,
    dv_ratio: float | None = None,
    *,
    window_label: str = "近5年",
) -> dict[str, Any]:
    """生成估值状态结构化描述。

    Args:
        pe_ttm_seq: 历史 PE(TTM) 序列（升序，旧→新）
        pb_seq: 历史 PB 序列
        current_pe: 当前 PE(TTM)，None 时取序列最后一值
        current_pb: 当前 PB，None 时取序列最后一值
        current_ps: 当前 PS(TTM)，可选
        ps_seq: 历史 PS 序列，可选
        dv_ratio: 股息率（最近交易日），可选
        window_label: 分位窗口描述，如"近5年"、"上市以来"

    Returns:
        dict 含 pe/pb/ps 分位、zone、median、样本数等
    """
    pe_seq_clean = [v for v in pe_ttm_seq if v is not None and v > 0]
    pb_seq_clean = [v for v in pb_seq if v is not None and v > 0]

    if current_pe is None and pe_seq_clean:
        current_pe = pe_seq_clean[-1]
    if current_pb is None and pb_seq_clean:
        current_pb = pb_seq_clean[-1]

    result: dict[str, Any] = {
        "window_label": window_label,
        "n_samples": len(pe_seq_clean),
        "sufficient": len(pe_seq_clean) >= 30,
    }

    # PE
    if pe_seq_clean and current_pe is not None:
        pe_pct = percentile_rank(pe_seq_clean, current_pe)
        pe_median = _median(pe_seq_clean)
        result["pe"] = {
            "current": round(current_pe, 2),
            "pct": round(pe_pct, 2) if pe_pct is not None else None,
            "median": round(pe_median, 2) if pe_median is not None else None,
            "zone": zone_label(pe_pct) if pe_pct is not None else "未知",
            "n_valid": len(pe_seq_clean),
        }
    else:
        result["pe"] = {"current": None, "pct": None, "median": None,
                        "zone": "未知", "n_valid": 0,
                        "reason": "PE 数据为空或无正值"}

    # PB
    if pb_seq_clean and current_pb is not None:
        pb_pct = percentile_rank(pb_seq_clean, current_pb)
        pb_median = _median(pb_seq_clean)
        result["pb"] = {
            "current": round(current_pb, 2),
            "pct": round(pb_pct, 2) if pb_pct is not None else None,
            "median": round(pb_median, 2) if pb_median is not None else None,
            "zone": zone_label(pb_pct) if pb_pct is not None else "未知",
            "n_valid": len(pb_seq_clean),
        }
    else:
        result["pb"] = {"current": None, "pct": None, "median": None,
                        "zone": "未知", "n_valid": 0,
                        "reason": "PB 数据为空或无正值"}

    # PS（可选）
    if ps_seq is not None:
        ps_seq_clean = [v for v in ps_seq if v is not None and v > 0]
        if current_ps is None and ps_seq_clean:
            current_ps = ps_seq_clean[-1]
        if ps_seq_clean and current_ps is not None:
            ps_pct = percentile_rank(ps_seq_clean, current_ps)
            ps_median = _median(ps_seq_clean)
            result["ps"] = {
                "current": round(current_ps, 2),
                "pct": round(ps_pct, 2) if ps_pct is not None else None,
                "median": round(ps_median, 2) if ps_median is not None else None,
                "zone": zone_label(ps_pct) if ps_pct is not None else "未知",
                "n_valid": len(ps_seq_clean),
            }
        else:
            result["ps"] = {"current": None, "pct": None, "median": None,
                           "zone": "未知", "n_valid": 0,
                           "reason": "PS 数据不可得"}
    else:
        result["ps"] = {"available": False, "reason": "PS 序列未传入"}

    # 股息率
    result["dv_ratio"] = round(dv_ratio, 4) if dv_ratio is not None else None

    # 样本不足警告
    result["warnings"] = []
    if not result["sufficient"]:
        result["warnings"].append("样本不足30个交易日，分位计算结果仅供参考")

    # 检查亏损期（负 PE/PB）是否被过滤
    pe_total = len([v for v in pe_ttm_seq if v is not None])
    pe_pos = len(pe_seq_clean)
    if pe_total > pe_pos:
        result["warnings"].append(
            f"PE 历史序列中有 {pe_total - pe_pos} 个交易日为亏损期（负值），"
            f"已从历史样本中排除，分位计算可能偏高")

    # 摘要文本（渲染用）
    result["summary_text"] = _build_summary_text(result)

    return result


def valuation_window_label(n_trading_days: int) -> str:
    """估值分位窗口描述（A 股约 242 交易日/年）。"""
    if n_trading_days >= 1250:
        return "近5年"
    if n_trading_days >= 250:
        return f"近{n_trading_days // 250}年"
    return "上市以来（数据有限）"


def median_of(seq: list[float]) -> float | None:
    """中位数（偶数样本取两中值平均）。"""
    if not seq:
        return None
    s = sorted(seq)
    n = len(s)
    if n % 2 == 1:
        return s[n // 2]
    return (s[n // 2 - 1] + s[n // 2]) / 2


def _median(seq: list[float]) -> float | None:
    return median_of(seq)


def implied_growth(
    pe_ttm: float,
    risk_free_rate: float,
    erp: float = 0.06,
) -> dict[str, Any]:
    """LAW 15：戈登模型反推市场隐含增长率。

    g_implied ≈ r - 1/PE
    r = risk_free_rate + ERP（A 股默认 6%）

    Args:
        pe_ttm: 当前 PE(TTM)
        risk_free_rate: 无风险利率（如 10Y 国债收益率），小数形式（如 0.03 表示 3%）
        erp: 股权风险溢价，默认 0.06（6%）

    Returns:
        dict 含 pe, risk_free_rate, erp, r, g_implied, 及可选 warning
    """
    if pe_ttm <= 0:
        return {
            "pe": pe_ttm,
            "risk_free_rate": risk_free_rate,
            "erp": erp,
            "r": None,
            "g_implied": None,
            "error": "PE 非正，无法计算隐含增长率",
        }

    r = risk_free_rate + erp
    earnings_yield = 1.0 / pe_ttm
    g_implied = r - earnings_yield

    result: dict[str, Any] = {
        "pe": round(pe_ttm, 2),
        "risk_free_rate": round(risk_free_rate, 4),
        "erp": erp,
        "r": round(r, 4),
        "g_implied": round(g_implied, 4),
    }

    if pe_ttm > 50:
        result["warning"] = (
            "PE > 50，简化戈登模型对高成长/周期类公司参考价值有限。"
            "g_implied 基于永续稳态增长假设，未考虑成长阶段切换、"
            "再投资率变化及风险溢价时变，结果仅供方向性参考，不构成估值结论。"
        )

    return result


def pe_band_series(
    daily_basic_rows: list[dict],
    years: int = 5,
) -> dict[str, Any]:
    """PE Band 数据层：计算各日 PE 及 ±1σ/±2σ 轨道。

    本阶段仅实现数据层，Markdown 文本表渲染留 Phase 4。

    Args:
        daily_basic_rows: Tushare daily_basic 序列（含 trade_date, pe_ttm）
        years: 窗口年数（默认 5）

    Returns:
        dict 含：
          - dates: 交易日列表
          - pe_values: PE 序列
          - mean: 均值
          - sigma: 标准差
          - upper_1σ, lower_1σ: ±1σ 轨道
          - upper_2σ, lower_2σ: ±2σ 轨道
          - n_samples: 有效样本数
          - current_pe: 当前 PE
          - current_position: 当前 PE 在 band 中的位置描述
    """
    from .technical import sort_kline_asc
    from datetime import datetime, timedelta

    rows = sort_kline_asc(daily_basic_rows)
    cutoff = (datetime.now() - timedelta(days=max(1, years) * 365)).strftime("%Y%m%d")
    pe_pairs = [
        (str(r.get("trade_date", "")), float(r.get("pe_ttm")))
        for r in rows
        if r.get("pe_ttm") is not None
        and float(r.get("pe_ttm")) > 0
        and str(r.get("trade_date", "")) >= cutoff
    ]

    if not pe_pairs:
        return _pe_band_empty(years)

    dates = [p[0] for p in pe_pairs]
    pe_values = [p[1] for p in pe_pairs]
    n = len(pe_values)
    mean = sum(pe_values) / n
    variance = sum((v - mean) ** 2 for v in pe_values) / n
    sigma = variance ** 0.5

    current_pe = pe_values[-1]

    if sigma is not None and sigma > 0:
        if current_pe >= mean + 2 * sigma:
            position = "远高于均值（+2σ 以上）"
        elif current_pe >= mean + sigma:
            position = "高于均值（+1σ ~ +2σ）"
        elif current_pe >= mean:
            position = "略高于均值（均值 ~ +1σ）"
        elif current_pe >= mean - sigma:
            position = "略低于均值（均值 ~ -1σ）"
        elif current_pe >= mean - 2 * sigma:
            position = "低于均值（-2σ ~ -1σ）"
        else:
            position = "远低于均值（-2σ 以下）"
    else:
        position = "σ=0，无法判断"

    return {
        "dates": dates,
        "pe_values": [round(v, 2) for v in pe_values],
        "mean": round(mean, 2),
        "sigma": round(sigma, 2) if sigma is not None else None,
        "upper_1σ": round(mean + sigma, 2) if sigma is not None else None,
        "lower_1σ": round(mean - sigma, 2) if sigma is not None else None,
        "upper_2σ": round(mean + 2 * sigma, 2) if sigma is not None else None,
        "lower_2σ": round(mean - 2 * sigma, 2) if sigma is not None else None,
        "n_samples": n,
        "current_pe": round(current_pe, 2),
        "current_position": position,
        "years": years,
    }


def _pe_band_empty(years: int = 5) -> dict[str, Any]:
    """PE Band 空结果（与有数据路径字段一致，供 Phase 4 消费）。"""
    return {
        "dates": [],
        "pe_values": [],
        "mean": None,
        "sigma": None,
        "upper_1σ": None,
        "lower_1σ": None,
        "upper_2σ": None,
        "lower_2σ": None,
        "n_samples": 0,
        "current_pe": None,
        "current_position": "数据不足",
        "years": years,
    }


def _build_summary_text(result: dict[str, Any]) -> str:
    """从结构化结果生成估值摘要文本。"""
    lines: list[str] = []

    pe = result.get("pe", {})
    if pe.get("current") is not None:
        pct = pe.get("pct")
        pct_str = f"{result['window_label']} {pct:.1f}% 分位" if pct is not None else "分位不可得"
        median_str = f"（中位数 {pe['median']:.2f}x）" if pe.get("median") is not None else ""
        lines.append(f"PE(TTM): {pe['current']:.2f}x，{pct_str}{median_str}，处于历史{pe.get('zone', '未知')}区间。")
    else:
        lines.append(f"PE(TTM): {pe.get('reason', '不可得')}。")

    pb = result.get("pb", {})
    if pb.get("current") is not None:
        pct = pb.get("pct")
        pct_str = f"{result['window_label']} {pct:.1f}% 分位" if pct is not None else "分位不可得"
        median_str = f"（中位数 {pb['median']:.2f}x）" if pb.get("median") is not None else ""
        lines.append(f"PB: {pb['current']:.2f}x，{pct_str}{median_str}，处于历史{pb.get('zone', '未知')}区间。")
    else:
        lines.append(f"PB: {pb.get('reason', '不可得')}。")

    dv = result.get("dv_ratio")
    if dv is not None:
        # Tushare daily_basic.dv_ratio 为百分比值（如 0.42 表示 0.42%）
        lines.append(f"股息率: {dv:.2f}%（最近交易日）。")
    else:
        lines.append("股息率: 不可得。")

    ps = result.get("ps", {})
    if ps.get("current") is not None:
        pct = ps.get("pct")
        pct_str = f"分位 {pct:.1f}%" if pct is not None else "分位不可得"
        lines.append(f"PS(TTM): {ps['current']:.2f}x，{result['window_label']} {pct_str}。")
    elif ps.get("available") is not False:
        lines.append(f"PS(TTM): {ps.get('reason', '不可得')}。")

    for w in result.get("warnings", []):
        lines.append(f"⚠️ {w}")

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════
# P2: DCF 估值预处理函数（v0.1.7，供 v0.1.8 DCF 模型使用）
# ═══════════════════════════════════════════════════════════════


def calc_wacc(
    beta: float,
    risk_free_rate: float,
    erp: float,
    cost_of_debt: float | None = None,
    tax_rate: float = 0.25,
    debt_weight: float | None = None,
) -> dict:
    """CAPM WACC 计算。

    Args:
        beta: 5Y 月度收益率 vs 沪深300 回归
        risk_free_rate: 中国 10Y 国债收益率（小数，如 0.0265）
        erp: 权益风险溢价（小数，如 0.058）
        cost_of_debt: 税前债务成本。若为 None，仅返回 cost_of_equity
        tax_rate: 有效税率
        debt_weight: 债务权重 D/(D+E)。若为 None 且 cost_of_debt 有值，自动计算
    """
    cost_of_equity = risk_free_rate + beta * erp

    if cost_of_debt is None:
        return {
            "cost_of_equity": round(cost_of_equity, 6),
            "wacc": round(cost_of_equity, 6),
            "components": {
                "risk_free_rate": risk_free_rate,
                "beta": beta,
                "erp": erp,
            },
        }

    cost_of_debt_after_tax = cost_of_debt * (1 - tax_rate)

    if debt_weight is None:
        return {
            "cost_of_equity": round(cost_of_equity, 6),
            "cost_of_debt_pre_tax": round(cost_of_debt, 6),
            "cost_of_debt_after_tax": round(cost_of_debt_after_tax, 6),
            "wacc": round(cost_of_equity, 6),
            "warning": "cost_of_debt 已提供但 debt_weight 缺失，WACC 退化为 cost_of_equity",
            "components": {
                "risk_free_rate": risk_free_rate,
                "beta": beta,
                "erp": erp,
                "tax_rate": tax_rate,
                "cost_of_debt_pre_tax": round(cost_of_debt, 6),
            },
        }

    equity_weight = 1 - debt_weight
    wacc = cost_of_equity * equity_weight + cost_of_debt_after_tax * debt_weight

    return {
        "cost_of_equity": round(cost_of_equity, 6),
        "cost_of_debt_pre_tax": round(cost_of_debt, 6),
        "cost_of_debt_after_tax": round(cost_of_debt_after_tax, 6),
        "wacc": round(wacc, 6),
        "components": {
            "risk_free_rate": risk_free_rate,
            "beta": beta,
            "erp": erp,
            "tax_rate": tax_rate,
            "debt_weight": debt_weight,
            "equity_weight": equity_weight,
        },
    }


def calc_fcff(
    ebit: float,
    tax_rate: float,
    depr: float,
    cap_ex: float,
    delta_nwc: float = 0,
) -> dict:
    """FCFF 计算（单期）。

    FCFF = EBIT × (1 - t) + Depr - CapEx - ΔNWC
    所有金额单位为元。
    """
    nopat = ebit * (1 - tax_rate)
    fcff = nopat + depr - cap_ex - delta_nwc

    return {
        "ebit": ebit,
        "tax_rate": tax_rate,
        "nopat": round(nopat, 2),
        "depr": depr,
        "cap_ex": cap_ex,
        "delta_nwc": delta_nwc,
        "fcff": round(fcff, 2),
        "fcff_margin": round(fcff / ebit, 4) if ebit else None,
    }


def calc_net_debt(debt_total: float, money_cap: float) -> dict:
    """净债务 = 带息债务 - 货币资金。

    若为负值 → 净现金（企业价值计算时做加法）。
    """
    net = debt_total - money_cap
    return {
        "debt_total": debt_total,
        "cash": money_cap,
        "net_debt": net,
        "is_net_cash": net < 0,
    }


def calc_ev_to_equity(
    enterprise_value: float,
    net_debt: float,
    shares_outstanding: int,
) -> dict:
    """企业价值 → 每股权益价值。

    Args:
        enterprise_value: ΣPV(FCF) + PV(Terminal)
        net_debt: 带息债务 - 货币资金（可为负=净现金）
        shares_outstanding: 总股本（股）
    """
    equity_value = enterprise_value - net_debt
    per_share = equity_value / shares_outstanding if shares_outstanding else None

    return {
        "enterprise_value": enterprise_value,
        "net_debt": net_debt,
        "equity_value": equity_value,
        "shares_outstanding": shares_outstanding,
        "per_share": round(per_share, 2) if per_share else None,
    }


def calc_beta(
    stock_returns: list[float],
    market_returns: list[float],
) -> dict:
    """从收益率序列计算 Beta。

    Args:
        stock_returns: 个股月度/周度收益率
        market_returns: 基准（沪深300）同期收益率

    Returns:
        {"beta": float, "r_squared": float, "observations": int}
    """
    import statistics

    n = min(len(stock_returns), len(market_returns))
    if n < 12:
        return {"beta": None, "error": f"数据点不足: {n} < 12"}

    mean_s = statistics.mean(stock_returns[:n])
    mean_m = statistics.mean(market_returns[:n])

    cov = sum(
        (s - mean_s) * (m - mean_m)
        for s, m in zip(stock_returns[:n], market_returns[:n])
    ) / (n - 1)
    var_m = sum((m - mean_m) ** 2 for m in market_returns[:n]) / (n - 1)

    if abs(var_m) < 1e-12:
        return {"beta": None, "error": "市场方差为零"}

    beta = cov / var_m

    # R²
    ss_res = sum(
        (s - (mean_s + beta * (m - mean_m))) ** 2
        for s, m in zip(stock_returns[:n], market_returns[:n])
    )
    ss_tot = sum((s - mean_s) ** 2 for s in stock_returns[:n])
    r_squared = 1 - ss_res / ss_tot if ss_tot != 0 else 0

    return {
        "beta": round(beta, 4),
        "r_squared": round(r_squared, 4),
        "observations": n,
    }


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        f = float(value)
        if f != f or f in (float("inf"), float("-inf")):
            return None
        return f
    except (TypeError, ValueError):
        return None


def extract_financial_rows(financials: dict) -> list[dict]:
    """从 financials legacy dict 提取最佳财报行列表（优先 Tushare 源）。"""
    data = financials.get("data")
    if isinstance(data, list) and data:
        return [r for r in data if isinstance(r, dict)]

    all_sources = financials.get("_meta", {}).get("all_sources", [])
    tushare_rows: list[dict] = []
    fallback_rows: list[dict] = []
    for src in all_sources:
        if not isinstance(src, dict):
            continue
        rows = src.get("data")
        if not isinstance(rows, list) or not rows:
            continue
        dict_rows = [r for r in rows if isinstance(r, dict)]
        if not dict_rows:
            continue
        if "tushare" in str(src.get("source", "")):
            tushare_rows = dict_rows
        elif not fallback_rows:
            fallback_rows = dict_rows
    return tushare_rows or fallback_rows


def _latest_financial_row(rows: list[dict]) -> dict | None:
    if not rows:
        return None
    return max(rows, key=lambda r: str(r.get("end_date", "")))


def _infer_tax_rate(row: dict) -> float:
    tax = _as_float(row.get("income_tax") or row.get("tax"))
    profit = _as_float(row.get("total_profit") or row.get("ebit"))
    if tax is not None and profit is not None and profit > 0:
        return max(0.0, min(0.35, tax / profit))
    return 0.25


def build_dcf_preprocess(financials: dict) -> dict | None:
    """从 financials 维度最新一期数据生成 DCF 预处理块（供 v0.1.8 消费）。"""
    row = _latest_financial_row(extract_financial_rows(financials))
    if not row:
        return None

    out: dict[str, Any] = {
        "end_date": row.get("end_date"),
        "status": "partial",
    }
    computed: list[str] = []

    ebit = _as_float(row.get("ebit"))
    if ebit is not None:
        depr = _as_float(row.get("depr_amort")) or 0.0
        cap_ex_raw = _as_float(row.get("cap_ex"))
        cap_ex = abs(cap_ex_raw) if cap_ex_raw is not None else 0.0
        tax_rate = _infer_tax_rate(row)
        out["fcff"] = calc_fcff(
            ebit=ebit,
            tax_rate=tax_rate,
            depr=depr,
            cap_ex=cap_ex,
        )
        computed.append("fcff")

    debt_total = _as_float(row.get("total_liab"))
    money_cap = _as_float(row.get("money_cap"))
    if debt_total is not None and money_cap is not None:
        out["net_debt"] = calc_net_debt(debt_total, money_cap)
        computed.append("net_debt")

    if not computed:
        return None

    out["computed"] = computed
    out["status"] = "available" if len(computed) >= 2 else "partial"
    return out


def attach_dcf_preprocess(financials_legacy: dict) -> None:
    """将 dcf_preprocess 块写入 financials legacy dict（原地）。"""
    block = build_dcf_preprocess(financials_legacy)
    if block:
        financials_legacy["dcf_preprocess"] = block
