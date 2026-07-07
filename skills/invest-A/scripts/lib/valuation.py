"""估值分析模块。

输入: PE_TTM/PB/PS 历史序列（由 collector 采集后传入）
输出: 分位、区间标签、估值描述文本

原则:
  - 不输出买卖建议、目标价、仓位建议
  - 输出格式为"状态描述"而非"交易信号"
  - 数据不足时标注而非静默跳过
"""

from __future__ import annotations

import math
import statistics
from datetime import date
from typing import Any

from lib.financials import parse_end_date as _parse_end_date
from lib.nums import safe_float as _safe_float


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


# Backward-compatible alias; superset of old _as_float (inf/Nan handled in safe_float now)
_as_float = _safe_float


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


# ═══════════════════════════════════════════════════════════════
# V-1~V-5: DCF 估值模型（v0.1.8 Step 3）
#
# 合规红线（AGENTS.md 约束1 / CLAUDE.md LAW 6）：
#   - dcf_two_stage / dcf_sensitivity 只返回企业价值（enterprise_value）及矩阵，
#     不做每股换算，不输出任何形式的"目标价"数字。每股价值换算与多情景区间呈现
#     留给 render.py（Step 4）在调用处完成，且必须伴随情景假设说明。
#   - scenario_fcff 的默认假设字段标注 assumption_type="rule_based_proxy"，
#     即"规则代理，非分析师预测"，避免被误读为对未来的判断性预测。
#   - triangle_check 的分歧提示只做数值比较陈述，不使用"低估/高估"等形容词。
# ═══════════════════════════════════════════════════════════════


def dcf_two_stage(
    fcff_base: float,
    growth_s1: float,
    years: int,
    wacc: float,
    terminal_g: float,
) -> dict:
    """两阶段 FCFF DCF：显式预测期（growth_s1 恒定复合增速）+ 永续增长终值。

    公式：
        FCFF_t = fcff_base × (1 + growth_s1)^t，t = 1..years
        PV(FCFF_t) = FCFF_t / (1 + wacc)^t
        Terminal FCFF = FCFF_years × (1 + terminal_g)
        Terminal Value = Terminal FCFF / (wacc - terminal_g)
        Terminal PV = Terminal Value / (1 + wacc)^years
        企业价值 = ΣPV(FCFF_t) + Terminal PV

    Args:
        fcff_base: 基期（最近一期）FCFF，元
        growth_s1: 显式预测期恒定复合增速（小数，如 0.12 表示 12%）
        years: 显式预测期年数（正整数，通常 5）
        wacc: 加权平均资本成本（小数）
        terminal_g: 永续增长率（小数），须严格小于 wacc

    Returns:
        正常路径: {"explicit_pv", "terminal_value", "terminal_pv",
                   "enterprise_value", "yearly_fcff", "assumptions"}
        非法参数: {"error": "..."}

    合规: 只返回企业价值，不做每股价值换算（不输出目标价）。
    """
    if not isinstance(years, int) or years < 1:
        return {"error": "years 必须为正整数"}
    numeric_inputs = {
        "fcff_base": fcff_base,
        "growth_s1": growth_s1,
        "wacc": wacc,
        "terminal_g": terminal_g,
    }
    invalid_numeric = [
        name for name, value in numeric_inputs.items()
        if not isinstance(value, (int, float)) or not math.isfinite(value)
    ]
    if invalid_numeric:
        return {"error": f"参数必须为有限数值: {', '.join(invalid_numeric)}"}
    if wacc <= terminal_g:
        return {"error": "WACC 必须大于永续增长率"}

    yearly_fcff: list[dict[str, Any]] = []
    explicit_pv = 0.0
    fcff_final = fcff_base
    for t in range(1, years + 1):
        fcff_t = fcff_base * (1 + growth_s1) ** t
        pv = fcff_t / (1 + wacc) ** t
        explicit_pv += pv
        yearly_fcff.append({"year": t, "fcff": round(fcff_t, 2), "pv": round(pv, 2)})
        fcff_final = fcff_t

    terminal_fcff = fcff_final * (1 + terminal_g)
    terminal_value = terminal_fcff / (wacc - terminal_g)
    terminal_pv = terminal_value / (1 + wacc) ** years
    enterprise_value = explicit_pv + terminal_pv

    return {
        "explicit_pv": round(explicit_pv, 2),
        "terminal_value": round(terminal_value, 2),
        "terminal_pv": round(terminal_pv, 2),
        "enterprise_value": round(enterprise_value, 2),
        "yearly_fcff": yearly_fcff,
        "assumptions": {
            "fcff_base": fcff_base,
            "growth_s1": growth_s1,
            "years": years,
            "wacc": wacc,
            "terminal_g": terminal_g,
        },
    }


def default_wacc_range(wacc_center: float) -> list[float]:
    """生成以 wacc_center 为中心的 5 档 WACC 敏感性区间。

    步长取 design.md 规定的 ±1%/±2%（绝对百分点，非相对比例）：
    [wacc-2%, wacc-1%, wacc, wacc+1%, wacc+2%]
    """
    return [round(wacc_center + d, 6) for d in (-0.02, -0.01, 0.0, 0.01, 0.02)]


def default_terminal_g_range(terminal_g_center: float) -> list[float]:
    """生成以 terminal_g_center 为中心的 5 档永续增长率敏感性区间。

    步长取 design.md 规定的 ±0.5%/±1%（绝对百分点）：
    [g-1%, g-0.5%, g, g+0.5%, g+1%]
    """
    return [round(terminal_g_center + d, 6) for d in (-0.01, -0.005, 0.0, 0.005, 0.01)]


def dcf_sensitivity(
    fcff_base: float,
    growth_s1: float,
    years: int,
    wacc_range: list[float],
    terminal_g_range: list[float],
) -> dict:
    """WACC × 永续增长率敏感性矩阵，每格调用 dcf_two_stage。

    默认区间生成规则（供调用方参考，见 default_wacc_range / default_terminal_g_range）：
      - wacc_range: 以基准 WACC 为中心，±1%/±2% 共 5 档
      - terminal_g_range: 以基准永续增长率为中心，±0.5%/±1% 共 5 档
    本函数不强制区间长度为 5（调用方可传入任意长度的等值区间），矩阵形状为
    len(wacc_range) × len(terminal_g_range)。

    非法组合（wacc <= terminal_g）不报错、不中断，单元格标注 "N/A"，其余格子照常计算。

    Returns:
        {"matrix": [[...] x len(wacc_range)], "wacc_labels": [...],
         "terminal_g_labels": [...], "note": "..."}
        wacc_range/terminal_g_range 为空时返回 {"error": "..."}
    """
    if not wacc_range or not terminal_g_range:
        return {"error": "wacc_range 与 terminal_g_range 均不能为空"}

    matrix: list[list[Any]] = []
    for wacc in wacc_range:
        row: list[Any] = []
        for terminal_g in terminal_g_range:
            cell = dcf_two_stage(fcff_base, growth_s1, years, wacc, terminal_g)
            row.append("N/A" if "error" in cell else cell["enterprise_value"])
        matrix.append(row)

    return {
        "matrix": matrix,
        "wacc_labels": [f"{w * 100:.2f}%" for w in wacc_range],
        "terminal_g_labels": [f"{g * 100:.2f}%" for g in terminal_g_range],
        "note": (
            "矩阵单元为企业价值（enterprise_value），非每股价值，不换算为单一价格结论；"
            "wacc<=terminal_g 的非法组合标注 N/A"
        ),
    }


# _parse_end_date imported above from lib.financials (canonical body lives there)


def _historical_revenue_cagr(rows: list[dict]) -> tuple[float | None, dict]:
    """估算历史营收复合增长率（优先使用年报 1231 记录，覆盖窗口尽量接近 3 年）。

    退化策略：
      1. 优先取全部年报（end_date 以 1231 结尾）记录，取最早与最新一期年报计算跨期 CAGR。
      2. 年报记录不足 2 期时，退回全部记录中最早与最新一期（可能为季报），按实际
         跨越的自然年数（days/365.25）折算年化增长率，并在 meta 中标注真实跨度，
         不假装是严格的"3年"CAGR。
      3. 数据不足 2 期或跨度过短（<0.5 年）时返回 (None, meta)。
    """
    annual = sorted(
        (
            r
            for r in rows
            if str(r.get("end_date", "")).strip().endswith("1231")
            and _as_float(r.get("revenue")) is not None
        ),
        key=lambda r: str(r.get("end_date", "")),
    )
    pool = annual if len(annual) >= 2 else [
        r for r in rows if _as_float(r.get("revenue")) is not None
    ]
    if len(pool) < 2:
        return None, {"note": "revenue 历史数据不足 2 期，无法计算 CAGR"}

    first, last = pool[0], pool[-1]
    rev_first = _as_float(first.get("revenue"))
    rev_last = _as_float(last.get("revenue"))
    d_first = _parse_end_date(first.get("end_date"))
    d_last = _parse_end_date(last.get("end_date"))
    if rev_first is None or rev_last is None or rev_first <= 0 or d_first is None or d_last is None:
        return None, {"note": "revenue 或 end_date 无法解析，无法计算 CAGR"}

    span_years = (d_last - d_first).days / 365.25
    if span_years < 0.5:
        return None, {"note": "时间跨度过短（<0.5年），无法计算年化增长率"}

    cagr = (rev_last / rev_first) ** (1 / span_years) - 1
    return cagr, {
        "start_period": first.get("end_date"),
        "end_period": last.get("end_date"),
        "span_years": round(span_years, 2),
        "cagr": round(cagr, 4),
        "basis": "年报(1231)" if len(annual) >= 2 else "季报退化(跨度非精确3年)",
        "note": f"基于 {first.get('end_date')}→{last.get('end_date')}（约 {span_years:.1f} 年）营收复合增长率",
    }


_SCENARIOS = ("bear", "base", "bull")
_SCENARIO_GROWTH_MULTIPLIER = {"bear": 0.5, "base": 1.0, "bull": 1.5}
_SCENARIO_MARGIN_DELTA_PP = {"bear": -3.0, "base": 0.0, "bull": 2.0}


def scenario_fcff(
    financials: dict,
    scenario: str = "base",
    probabilities: dict[str, float] | None = None,
) -> dict:
    """基于历史财务数据生成 Bear/Base/Bull 情景下的 5 年 FCFF 预测序列。

    默认假设生成规则（无外部输入时的规则代理，非分析师预测）：
      - base: 营收增速 = 历史营收 CAGR（见 _historical_revenue_cagr，优先取年报，
        退化到可得的最早/最新两期折算年化）；毛利率 = 近 4 期 grossprofit_margin 均值；
        capex 强度 = 历史全部可得期数 cap_ex/revenue 均值（三情景共用同一历史均值，
        设计上不对 capex 强度做情景化调整——scope.md 仅对营收增速与毛利率做情景区分）
      - bear: 营收增速 = base 营收增速 × 0.5；毛利率 = base 毛利率 - 3pp
      - bull: 营收增速 = base 营收增速 × 1.5；毛利率 = base 毛利率 + 2pp

    毛利率情景差（pp）同步施加到 EBIT 利润率上（EBIT margin 以最新一期 ebit/revenue
    为基准，加上同等 pp 偏移），作为"利润率随经营杠杆同向变化"的保守简化——
    未对毛利率与 EBIT 利润率的传导系数做进一步精细建模，[推测，待验证]。
    折旧按营收同比例缩放（depr_t = depr_latest × revenue_t/revenue_latest），
    ΔNWC 简化为 0（保守假设，未单独建模营运资本变化）。

    已知局限：若历史营收 CAGR 本身为负，bear 情景（×0.5）产出的降幅小于 base，
    数值上"更乐观"，这是规则代理公式的已知局限而非解读错误，返回值中会标注
    `growth_sign_caveat` 字段提示调用方。

    Args:
        financials: financials 维度 legacy dict（含 "data" 或 "_meta.all_sources"，
            与 extract_financial_rows 消费格式一致），也兼容直接传入 list[dict] 财报行。
        scenario: "bear" | "base" | "bull"
        probabilities: 三情景主观概率权重（V-5），如 {"bear":0.3,"base":0.4,"bull":0.3}；
            未提供的情景键补齐为均等权重的默认值 1/3；不做归一化强制校验（调用方自行保证
            概率合理性），仅按需分渲染标注"概率为主观假设，不构成估值结论"。

    Returns:
        正常: {"scenario", "assumption_type": "rule_based_proxy", "assumptions": {...},
               "sources": [...], "growth_basis": {...}, "yearly_fcff": [...],
               "probability": float}
        数据不足: {"scenario", "assumption_type": "rule_based_proxy",
                   "error": "数据不足，无法生成 FCFF 预测", "insufficient_data": [...]}
    """
    if scenario not in _SCENARIOS:
        return {"error": f"未知情景: {scenario!r}，仅支持 {_SCENARIOS}"}

    default_probs = {"bear": 1 / 3, "base": 1 / 3, "bull": 1 / 3}
    probs = dict(default_probs)
    if probabilities:
        probs.update({k: v for k, v in probabilities.items() if k in default_probs})

    if isinstance(financials, dict):
        rows = extract_financial_rows(financials)
    elif isinstance(financials, list):
        rows = [r for r in financials if isinstance(r, dict)]
    else:
        rows = []
    rows = sorted(
        (r for r in rows if isinstance(r, dict) and r.get("end_date")),
        key=lambda r: str(r.get("end_date", "")),
    )

    latest = _latest_financial_row(rows)
    insufficient: list[str] = []

    revenue_latest = _as_float(latest.get("revenue")) if latest else None
    ebit_latest = _as_float(latest.get("ebit")) if latest else None
    base_growth, growth_meta = _historical_revenue_cagr(rows)
    margins = [
        v for v in (_as_float(r.get("grossprofit_margin")) for r in rows[-4:])
        if v is not None
    ]
    base_margin = statistics.mean(margins) if margins else None
    intensities = []
    for r in rows:
        rev = _as_float(r.get("revenue"))
        capex_raw = _as_float(r.get("cap_ex"))
        if rev is not None and rev > 0 and capex_raw is not None:
            intensities.append(abs(capex_raw) / rev)
    base_capex_intensity = statistics.mean(intensities) if intensities else None

    if latest is None:
        insufficient.append("financials 无有效历史财报记录（end_date 缺失或格式不可解析）")
    if revenue_latest is None:
        insufficient.append("revenue（最新期）不可得")
    if ebit_latest is None:
        insufficient.append("ebit（最新期）不可得")
    if base_growth is None:
        insufficient.append(f"营收历史 CAGR 不可得（{growth_meta.get('note')}）")
    if base_margin is None:
        insufficient.append("grossprofit_margin（近4期均值）不可得")
    if base_capex_intensity is None:
        insufficient.append("cap_ex/revenue 历史强度不可得")

    if insufficient:
        return {
            "scenario": scenario,
            "assumption_type": "rule_based_proxy",
            "error": "数据不足，无法生成 FCFF 预测",
            "insufficient_data": insufficient,
            "probability": round(probs.get(scenario, 1 / 3), 4),
        }

    tax_rate = _infer_tax_rate(latest)
    depr_latest = _as_float(latest.get("depr_amort")) or 0.0
    ebit_margin_latest = ebit_latest / revenue_latest if revenue_latest else 0.0

    growth = base_growth * _SCENARIO_GROWTH_MULTIPLIER[scenario]
    margin_delta_pp = _SCENARIO_MARGIN_DELTA_PP[scenario]
    scenario_gross_margin = base_margin + margin_delta_pp
    scenario_ebit_margin = max(0.0, ebit_margin_latest + margin_delta_pp / 100.0)

    yearly_fcff: list[dict[str, Any]] = []
    revenue_t = revenue_latest
    for t in range(1, 6):
        revenue_t = revenue_t * (1 + growth)
        ebit_t = revenue_t * scenario_ebit_margin
        capex_t = revenue_t * base_capex_intensity
        depr_t = depr_latest * (revenue_t / revenue_latest) if revenue_latest else depr_latest
        fcff_calc = calc_fcff(ebit=ebit_t, tax_rate=tax_rate, depr=depr_t, cap_ex=capex_t, delta_nwc=0)
        yearly_fcff.append({
            "year": t,
            "revenue": round(revenue_t, 2),
            "ebit": round(ebit_t, 2),
            "fcff": fcff_calc["fcff"],
        })

    result: dict[str, Any] = {
        "scenario": scenario,
        "assumption_type": "rule_based_proxy",
        "assumptions": {
            "revenue_growth": round(growth, 4),
            "base_revenue_growth_cagr": round(base_growth, 4),
            "gross_margin_assumption": round(scenario_gross_margin, 2),
            "base_gross_margin": round(base_margin, 2),
            "ebit_margin_assumption": round(scenario_ebit_margin, 4),
            "capex_intensity": round(base_capex_intensity, 4),
            "tax_rate": round(tax_rate, 4),
            "note": (
                "默认假设，非分析师预测；由历史统计规则外推生成（规则代理），"
                "不代表对公司未来经营的判断性预测"
            ),
        },
        "sources": [
            "revenue", "ebit", "grossprofit_margin", "cap_ex", "depr_amort",
            "income_tax/total_profit（用于税率推断）",
        ],
        "growth_basis": growth_meta,
        "yearly_fcff": yearly_fcff,
        "probability": round(probs.get(scenario, 1 / 3), 4),
    }
    if base_growth < 0:
        result["growth_sign_caveat"] = (
            "历史营收 CAGR 为负，bear 情景（×0.5）降幅小于 base 情景，数值上呈现"
            "'更乐观'的反直觉结果，属规则代理公式已知局限，非解读错误"
        )
    return result


def triangle_check(
    dcf_growth: float | None,
    consensus_growth: float | None,
    hist_growth: float | None,
) -> dict:
    """估值三角对照表：自算 DCF 隐含增速 / 机构一致预期增速 / 历史营收 CAGR。

    任一输入为 None → 对应行标注"不可得"，不跳过整个函数（其余行照常输出）。
    仅当三者都存在且 (max-min) > 3pp 时生成 divergence_note，措辞使用数值比较，
    禁止"低估/高估"等形容词（CLAUDE.md 禁止词表）。

    Args:
        dcf_growth: 自算 DCF 隐含增速（小数，如 0.12），通常来自 scenario_fcff/
            dcf_two_stage 反推的显式期复合增速
        consensus_growth: 机构一致预期增速（小数），来自 research 维度
        hist_growth: 历史营收 CAGR（小数），来自 financials 维度

    Returns:
        {"rows": [{"label", "value", "display", "source"}, ...],
         "divergence_note": str | None}
    """
    specs = [
        ("自算DCF隐含增速", dcf_growth, "valuation.dcf_two_stage / scenario_fcff 反推"),
        ("机构一致预期增速", consensus_growth, "research 维度机构盈利预测"),
        ("历史营收CAGR", hist_growth, "financials 维度历史财报数据"),
    ]

    rows: list[dict[str, Any]] = []
    for label, value, source in specs:
        if value is None:
            rows.append({"label": label, "value": None, "display": "不可得", "source": source})
        else:
            rows.append({
                "label": label,
                "value": round(value, 4),
                "display": f"{value * 100:.1f}%",
                "source": source,
            })

    divergence_note = None
    values = [dcf_growth, consensus_growth, hist_growth]
    if all(v is not None for v in values):
        labeled = list(zip((s[0] for s in specs), values))
        hi_label, hi_val = max(labeled, key=lambda x: x[1])
        lo_label, lo_val = min(labeled, key=lambda x: x[1])
        spread_pp = (hi_val - lo_val) * 100
        if spread_pp > 3.0:
            divergence_note = (
                f"{hi_label}（{hi_val * 100:.1f}%）vs {lo_label}（{lo_val * 100:.1f}%），"
                f"差值 {spread_pp:.1f}pp，三者对增长路径的定价假设存在数值差异，"
                "具体解读需结合行业周期与订单可见度综合判断，仅供参考，不构成估值结论。"
            )

    return {"rows": rows, "divergence_note": divergence_note}
