"""三层风险扫描：报表 / 商业 / 市场共 17 个定量信号。

纯函数，无 API 调用；不输出买卖建议。
"""

from __future__ import annotations

from typing import Any

from lib.nums import safe_float
from lib.technical import compute, rsi_series, sort_kline_asc


def _fin(rows: list[dict]) -> list[dict]:
    """财务行按报告期升序；与 sort_kline_asc 一致并增加 ann_date 稳定次序。"""
    return sorted(
        sort_kline_asc(rows),
        key=lambda r: (
            str(r.get("trade_date") or r.get("end_date", "")),
            str(r.get("ann_date", "")),
        ),
    )


def _ocf(row: dict) -> float | None:
    return safe_float(row.get("n_cashflow_act") or row.get("ocf"))


def _gross_margin(row: dict) -> float | None:
    return safe_float(row.get("grossprofit_margin") or row.get("gross_margin"))


def _signal(
    id: str,
    name: str,
    category: str,
    *,
    triggered: bool,
    severity: str | None,
    detail: str,
    auto: bool,
    status: str | None = None,
) -> dict[str, Any]:
    if status is None:
        status = "triggered" if triggered else "clear"
    return {
        "id": id,
        "name": name,
        "category": category,
        "triggered": triggered,
        "severity": severity if triggered else None,
        "detail": detail,
        "auto": auto,
        "status": status,
    }


def scan_financial_risks(
    financials: list[dict],
    *,
    industry_median_debt: float | None = None,
) -> list[dict]:
    """报表风险 7 信号。"""
    rows = _fin(financials or [])
    signals: list[dict] = []

    # 1 现金流持续为负
    if len(rows) >= 2:
        q1, q2 = rows[-2], rows[-1]
        o1, o2 = _ocf(q1), _ocf(q2)
        trig = o1 is not None and o2 is not None and o1 < 0 and o2 < 0
        detail = (
            f"近两期经营现金流均为负（{q1.get('end_date')}: {o1:.0f}，"
            f"{q2.get('end_date')}: {o2:.0f}）"
            if trig
            else "近两期经营现金流未连续为负"
        )
    else:
        trig, detail = False, "财务期数不足 2 期，无法判断连续季度现金流"
    signals.append(_signal("cashflow_negative", "现金流持续为负", "financial",
                             triggered=trig, severity="高", detail=detail, auto=True))

    # 2 利润质量低（连续 2 年 OCF/净利润 < 0.6）
    by_year: dict[str, dict] = {}
    for r in rows:
        y = str(r.get("end_date", ""))[:4]
        if y:
            by_year[y] = r
    years = sorted(by_year)[-2:]
    qual_trig = False
    qual_parts: list[str] = []
    low_flags: list[bool] = []
    if len(years) == 2:
        for y in years:
            r = by_year[y]
            ocf = _ocf(r)
            np_v = safe_float(r.get("net_profit"))
            if ocf is not None and np_v is not None and np_v > 0:
                ratio = ocf / np_v
                qual_parts.append(f"{y}: OCF/净利润={ratio:.2f}")
                low_flags.append(ratio < 0.6)
        qual_trig = len(low_flags) == 2 and all(low_flags)
        detail = "；".join(qual_parts) if qual_parts else "缺少 OCF/净利润字段"
        if not qual_trig and qual_parts:
            detail += "（未连续 2 年均低于 0.6）"
    else:
        detail = "年度财务数据不足 2 年"
    signals.append(_signal("profit_quality_low", "利润质量低", "financial",
                             triggered=qual_trig, severity="中", detail=detail, auto=True))

    # 3–5 应收 / 存货 / 扣非（基于最近两期）
    latest = rows[-1] if rows else {}
    prev = rows[-2] if len(rows) >= 2 else {}
    rev_cur = safe_float(latest.get("revenue"))
    rev_prev = safe_float(prev.get("revenue"))
    rev_g = ((rev_cur - rev_prev) / rev_prev * 100) if rev_cur and rev_prev and rev_prev > 0 else None

    ar_cur = safe_float(latest.get("accounts_receiv") or latest.get("ar"))
    ar_prev = safe_float(prev.get("accounts_receiv") or prev.get("ar"))
    if ar_cur is not None and ar_prev is not None and ar_prev > 0 and rev_g is not None:
        ar_g = (ar_cur - ar_prev) / ar_prev * 100
        ar_trig = ar_g > rev_g * 1.5
        ar_detail = f"应收增速 {ar_g:+.1f}% vs 营收增速 {rev_g:+.1f}%"
    else:
        ar_trig, ar_detail = False, "应收或营收字段不足，无法计算增速对比"
    signals.append(_signal("receivable_expansion", "应收扩张异常", "financial",
                             triggered=ar_trig, severity="中", detail=ar_detail, auto=True))

    inv_cur = safe_float(latest.get("inventories") or latest.get("inventory"))
    inv_prev = safe_float(prev.get("inventories") or prev.get("inventory"))
    if inv_cur is not None and inv_prev is not None and inv_prev > 0 and rev_g is not None:
        inv_g = (inv_cur - inv_prev) / inv_prev * 100
        inv_trig = inv_g > rev_g * 1.5
        inv_detail = f"存货增速 {inv_g:+.1f}% vs 营收增速 {rev_g:+.1f}%"
    else:
        inv_trig, inv_detail = False, "存货或营收字段不足，无法计算增速对比"
    signals.append(_signal("inventory_expansion", "存货扩张异常", "financial",
                             triggered=inv_trig, severity="中", detail=inv_detail, auto=True))

    pd_v = safe_float(latest.get("profit_dedt"))
    np_v = safe_float(latest.get("net_profit"))
    if pd_v is not None and np_v is not None and np_v > 0:
        ratio = pd_v / np_v
        ded_trig = ratio < 0.7
        ded_detail = f"扣非/净利润 = {ratio:.2f}（{latest.get('end_date')}）"
    else:
        ded_trig, ded_detail = False, "扣非或净利润字段不足"
    signals.append(_signal("deducted_profit_divergence", "扣非大幅背离", "financial",
                             triggered=ded_trig, severity="中", detail=ded_detail, auto=True))

    # 6 负债率升高（连续 2 年上升且超行业中位数 +10pp）
    debt_years = [(y, safe_float(by_year[y].get("debt_to_assets"))) for y in sorted(by_year)]
    debt_years = [(y, d) for y, d in debt_years if d is not None]
    debt_trig = False
    if len(debt_years) >= 2:
        (y0, d0), (y1, d1) = debt_years[-2], debt_years[-1]
        rising = d0 < d1
        above_ind = industry_median_debt is not None and d1 > industry_median_debt + 10
        debt_trig = rising and above_ind
        debt_detail = (
            f"资产负债率 {y0}→{y1}: {d0:.1f}%→{d1:.1f}%"
            f"{'' if industry_median_debt is None else f'；行业中位数 {industry_median_debt:.1f}%+10pp'}"
        )
        if rising and not above_ind and industry_median_debt is not None:
            debt_detail += f"（未超行业中位数+10pp，当前 {d1:.1f}%）"
        elif rising and industry_median_debt is None:
            debt_detail += "（行业中位数不可得，无法判定是否超阈值）"
    else:
        debt_detail = "负债率年度数据不足 2 年"
    signals.append(_signal("debt_ratio_rising", "负债率升高", "financial",
                             triggered=debt_trig, severity="中", detail=debt_detail, auto=True))

    # 7 利息保障弱
    ebit = safe_float(latest.get("ebit"))
    int_exp = safe_float(latest.get("int_exp") or latest.get("interest_expense"))
    if ebit is not None and int_exp is not None and int_exp > 0:
        cov = ebit / int_exp
        int_trig = cov < 2
        int_detail = f"利息保障倍数 = {cov:.2f}（EBIT {ebit:.0f} / 利息 {int_exp:.0f}）"
    elif int_exp is not None and int_exp <= 0:
        int_trig, int_detail = False, "利息支出为零或缺失，利息保障倍数不适用"
    else:
        int_trig, int_detail = False, "EBIT 或利息支出字段不足"
    signals.append(_signal("interest_coverage_weak", "利息保障弱", "financial",
                             triggered=int_trig, severity="高", detail=int_detail, auto=True))

    return signals


def scan_business_risks(
    financials: list[dict],
    industry: dict | None,
) -> list[dict]:
    """商业风险 4 信号（部分需 Agent 补充）。"""
    rows = _fin(financials or [])
    industry = industry or {}
    signals: list[dict] = []

    # 8 毛利率下降
    margins = [(str(r.get("end_date", ""))[:4], _gross_margin(r)) for r in rows]
    margins = [(y, m) for y, m in margins if y and m is not None]
    seen: dict[str, float] = {}
    for y, m in margins:
        seen[y] = m
    annual = sorted(seen.items())
    gm_trig = False
    if len(annual) >= 2:
        (_, m0), (_, m1) = annual[-2], annual[-1]
        gm_trig = m0 > m1 and (m0 - m1) > 3
        gm_detail = f"毛利率 {annual[-2][0]}→{annual[-1][0]}: {m0:.2f}%→{m1:.2f}%"
    else:
        gm_detail = "毛利率年度数据不足 2 年"
    signals.append(_signal("gross_margin_decline", "毛利率下降", "business",
                             triggered=gm_trig, severity="中", detail=gm_detail, auto=True))

    # 9 竞争加剧（同行毛利率普降，需 industry_peers 趋势字段）
    peers = industry.get("peers") or []
    trends = [p.get("gross_margin_trend") for p in peers if p.get("gross_margin_trend")]
    if len(trends) >= 3:
        down_n = sum(1 for t in trends if str(t).lower() in ("down", "decline", "falling"))
        comp_trig = down_n >= max(2, int(len(trends) * 0.6))
        comp_detail = f"同行毛利率下行 {down_n}/{len(trends)} 家"
        comp_status = "triggered" if comp_trig else "clear"
    else:
        comp_trig, comp_detail, comp_status = False, "同行毛利率趋势数据不足", "insufficient_data"
    signals.append(_signal("competition_intense", "竞争加剧", "business",
                             triggered=comp_trig, severity="中", detail=comp_detail, auto=True,
                             status=comp_status))

    # 10 技术替代（引擎不判定，交 Agent）
    signals.append(_signal("tech_substitution", "技术替代", "business",
                             triggered=False, severity=None,
                             detail="需 WebSearch 验证技术路线变化与替代时间维度",
                             auto=False, status="pending_agent"))

    # 11 客户集中
    conc = None
    for src in (rows[-1] if rows else {}, industry.get("target") or {}, industry):
        conc = safe_float(
            src.get("top5_customer_ratio")
            or src.get("top5_cust_sale_pct")
            or src.get("customer_concentration")
        )
        if conc is not None:
            break
    if conc is not None:
        cust_trig = conc > 50
        cust_detail = f"前五大客户营收占比 {conc:.1f}%"
        cust_status = "triggered" if cust_trig else "clear"
    else:
        cust_trig, cust_detail, cust_status = False, "前五大客户占比数据不可得", "insufficient_data"
    signals.append(_signal("customer_concentration", "客户集中", "business",
                             triggered=cust_trig, severity="中", detail=cust_detail, auto=True,
                             status=cust_status))

    return signals


def _pe_percentile(valuation: dict | None) -> float | None:
    if not valuation:
        return None
    v = safe_float(valuation.get("pe_percentile"))
    if v is not None:
        return v
    pe = valuation.get("pe") or {}
    return safe_float(pe.get("pct"))


def _technical_breakdown(technical: dict | None) -> tuple[bool, str]:
    if not technical or technical.get("error"):
        return False, "技术指标数据不足"
    ma = (technical.get("trend") or {}).get("ma") or {}
    close = safe_float(technical.get("latest_close"))

    def _latest(period: int) -> float | None:
        vals = ma.get(str(period), [])
        return safe_float(vals[-1]) if vals else None

    m5, m20, m60, m120 = _latest(5), _latest(20), _latest(60), _latest(120)
    if None in (close, m5, m20, m60, m120):
        return False, "MA120 或收盘价数据不足"
    bearish = m5 < m20 < m60 < m120
    below = close < m120
    trig = bearish and below
    detail = (
        f"收盘 {close:.2f} < MA120 {m120:.2f}，且 MA5<MA20<MA60<MA120"
        if trig else f"收盘 {close:.2f}，均线排列未满足破位条件"
    )
    return trig, detail


def _rsi14_streak(technical: dict | None, *, high: bool) -> tuple[bool, str]:
    series = (technical or {}).get("rsi14")
    if not series:
        return False, "RSI(14) 序列不可用"
    tail = [safe_float(v) for v in series[-3:]]
    if len(tail) < 3 or any(v is None for v in tail):
        return False, "RSI(14) 近 3 日数据不足"
    if high:
        trig = all(v > 80 for v in tail)
        label = ">80"
    else:
        trig = all(v < 20 for v in tail)
        label = "<20"
    vals = ", ".join(f"{v:.1f}" for v in tail)
    return trig, f"近 3 日 RSI(14) {vals}（连续 {label}）"


def scan_market_risks(
    valuation: dict | None,
    northbound: dict | None,
    technical: dict | None,
) -> list[dict]:
    """市场风险 6 信号。"""
    signals: list[dict] = []

    pe_pct = _pe_percentile(valuation)
    if pe_pct is not None:
        high_trig = pe_pct >= 90
        signals.append(_signal("valuation_extreme_high", "估值极端高", "market",
                                 triggered=high_trig, severity="中",
                                 detail=f"PE 历史分位 {pe_pct:.1f}%（≥90%）" if high_trig
                                 else f"PE 历史分位 {pe_pct:.1f}%", auto=True))
        low_trig = pe_pct <= 10
        signals.append(_signal("valuation_extreme_low", "估值极端低", "market",
                                 triggered=low_trig, severity="参考",
                                 detail=f"PE 历史分位 {pe_pct:.1f}%（≤10%）" if low_trig
                                 else f"PE 历史分位 {pe_pct:.1f}%", auto=True))
    else:
        for sid, name in (("valuation_extreme_high", "估值极端高"),
                          ("valuation_extreme_low", "估值极端低")):
            signals.append(_signal(sid, name, "market", triggered=False, severity=None,
                                     detail="PE 历史分位数据不足", auto=True,
                                     status="insufficient_data"))

    nb = northbound or {}
    nb_v = safe_float(nb.get("net_sum_10d"))
    if nb_v is not None:
        nb_trig = nb_v < -500_000_000
        signals.append(_signal("northbound_outflow", "北向持续流出", "market",
                                 triggered=nb_trig, severity="低",
                                 detail=f"近 10 日北向净额 {nb_v / 1e8:.2f} 亿元"
                                 + ("（净流出超 5 亿）" if nb_trig else ""), auto=True))
    else:
        signals.append(_signal("northbound_outflow", "北向持续流出", "market",
                                 triggered=False, severity=None, detail="北向资金数据不足",
                                 auto=True, status="insufficient_data"))

    br_trig, br_detail = _technical_breakdown(technical)
    br_status = "clear" if technical and not technical.get("error") else "insufficient_data"
    signals.append(_signal("technical_breakdown", "技术面破位", "market",
                             triggered=br_trig, severity="低", detail=br_detail, auto=True,
                             status="triggered" if br_trig else br_status))

    os_trig, os_detail = _rsi14_streak(technical, high=False)
    rsi_ok = bool((technical or {}).get("rsi14"))
    os_status = "triggered" if os_trig else ("clear" if rsi_ok else "insufficient_data")
    signals.append(_signal("momentum_oversold", "动量超卖", "market",
                             triggered=os_trig, severity="参考", detail=os_detail, auto=True,
                             status=os_status))

    ob_trig, ob_detail = _rsi14_streak(technical, high=True)
    ob_status = "triggered" if ob_trig else ("clear" if rsi_ok else "insufficient_data")
    signals.append(_signal("momentum_overbought", "动量超买", "market",
                             triggered=ob_trig, severity="参考", detail=ob_detail, auto=True,
                             status=ob_status))

    return signals


_AGENT_UNKNOWNS: dict[str, str] = {
    "tech_substitution": "技术替代：需 WebSearch 验证技术路线变化与时间维度",
    "competition_intense": "竞争加剧：同行毛利率下行，需 Agent 补充价格战持续时间",
    "gross_margin_decline": "毛利率下降：需区分行业性下行与公司特异性",
    "customer_concentration": "客户集中：需评估大客户议价权与合同稳定性",
}


def risk_report(
    financials: list[dict],
    industry_peers: dict | None = None,
    valuation: dict | None = None,
    northbound: dict | None = None,
    kline: list[dict] | None = None,
    industry_median_debt: float | None = None,
) -> dict[str, Any]:
    """汇总 17 个风险信号 + Known Unknowns。"""
    technical: dict | None = None
    if kline:
        sorted_k = sort_kline_asc(kline)
        technical = compute(sorted_k)
        if technical and "error" not in technical:
            closes = [safe_float(r.get("close")) or 0.0 for r in sorted_k]
            technical = {**technical, "rsi14": rsi_series(closes, 14)}

    fin_signals = scan_financial_risks(financials, industry_median_debt=industry_median_debt)
    biz_signals = scan_business_risks(financials, industry_peers)
    mkt_signals = scan_market_risks(valuation, northbound, technical)
    signals = fin_signals + biz_signals + mkt_signals

    known_unknowns: list[str] = []
    for s in signals:
        sid = s["id"]
        if s["status"] == "pending_agent":
            known_unknowns.append(_AGENT_UNKNOWNS[sid])
        elif s["status"] == "insufficient_data" and sid in ("customer_concentration", "competition_intense"):
            known_unknowns.append(f"{s['name']}：{s['detail']}")
        elif s["triggered"] and sid in _AGENT_UNKNOWNS:
            known_unknowns.append(_AGENT_UNKNOWNS[sid])

    auto_count = sum(
        1 for s in signals
        if s["auto"] and s["status"] in ("triggered", "clear", "insufficient_data")
    )

    return {
        "signals": signals,
        "triggered_count": sum(1 for s in signals if s["triggered"]),
        "known_unknowns": known_unknowns,
        "coverage": {"auto": auto_count, "total": 17},
    }
