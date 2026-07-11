"""量化评分与置信度矩阵（v0.1.8 S-1）。

学术依据（详见 host-docs/v0.1.8/补充资料.md）：
  - Zha Giedt (2018), *Abacus* 54(4), 457-484 — 收入应计三组件模型（收入质量公开数据代理）
  - Shy (2002), *Int'l J. of Industrial Organization* 20(3), 367-393 — 转换成本代数估算（客户锁定代理）
  - Demerjian, Lev & McVay (2012), *Management Science* 58(7), 1229-1248 — DEA+Tobit 管理层能力量化（简化代理）

原则（AGENTS.md 约束 1/2/3, CLAUDE.md 措辞规范）：
  - 只输出行为事实的量化聚合，不输出"买入/卖出/建仓/目标价"等操作建议或分数到操作的映射。
  - 数据不足时明确标注 `insufficient_data`，不得用默认值掩盖缺失后继续计算并呈现为正常分数。
  - 每个函数返回 dict 均附带 `sources` 字段，列出用到的原始字段名，供 render.py 渲染 `[来源: ...]`。
"""

from __future__ import annotations

import statistics
from datetime import date
from typing import Any

from lib.financials import find_yoy_row, parse_end_date
from lib.nums import coalesce_field, safe_float
from lib.technical import sort_kline_asc
from lib.valuation import _infer_tax_rate

# Backward-compatible aliases — callers use these module-private names
_as_float = safe_float
_field = coalesce_field

# ---- 通用工具 ----

_RECENT_MARGIN_PERIODS = 8
_RECENT_OCF_PERIODS = 4
_RECENT_ROIC_PERIODS = 6


def _sorted_rows(financials: list[dict] | None) -> list[dict]:
    """按 end_date 升序排序（旧→新），过滤非法记录。

    财务行通常仅有 end_date（报告期），无 trade_date，因此委托 sort_kline_asc
    后实际按 end_date 排序（trade_date 不存在时回退到 end_date）。
    """
    if not isinstance(financials, list):
        return []
    rows = [r for r in financials if isinstance(r, dict) and r.get("end_date")]
    return sort_kline_asc(rows)


# _parse_date replaced by lib.financials.parse_end_date (imported above)
# _field replaced by lib.nums.coalesce_field (aliased above)


def _find_yoy_row(rows: list[dict], latest: dict) -> dict | None:
    """定位与 latest 同季/同月、年份 -1 的记录（同比配对）。"""
    return find_yoy_row(rows, latest)


def _coerce_score(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, value))


# ---- 收入模式质量评分 ----

def revenue_quality_score(financials: list[dict]) -> dict:
    """收入模式质量评分（0-100），收入应计三组件模型的公开数据代理。

    来源: Zha Giedt (2018), *Abacus* 54(4), 457-484。

    三个子信号（等权重合成，缺失子信号从分母中剔除并标注）：
      - receivables_signal: ΔAR / ΔRevenue（应收增速快于营收增速 → 激进确认风险）
      - margin_stability: 1 - CV(近 8 期 grossprofit_margin)（定价权/收入模式一致性）
      - ocf_coverage: OCF / 净利润 近 4 期均值（经营现金流对账面利润的覆盖，现金收入模式验证）

    递延收入（deferred_rev，v0.1.7 未采集合同负债/预收款项字段）恒定标注为数据不足，
    不参与计算，总分永远标注 `partial: True`（补充资料 1.2）。
    """
    rows = _sorted_rows(financials)
    sources: list[str] = []
    insufficient: list[str] = [
        "deferred_rev（v0.1.7 未采集合同负债/预收款项字段，递延收入信号无法计算）",
    ]
    detail: dict[str, Any] = {}
    points: list[float] = []

    # -- receivables_signal --
    receivables_detail: dict[str, Any] = {}
    if len(rows) >= 2:
        latest, prev = rows[-1], rows[-2]
        ar_latest = _field(latest, "accounts_receiv", "ar")
        ar_prev = _field(prev, "accounts_receiv", "ar")
        rev_latest = _field(latest, "revenue")
        rev_prev = _field(prev, "revenue")
        if None not in (ar_latest, ar_prev, rev_latest, rev_prev) and ar_prev != 0 and rev_prev != 0:
            ar_growth = (ar_latest - ar_prev) / abs(ar_prev)
            rev_growth = (rev_latest - rev_prev) / abs(rev_prev)
            if abs(rev_growth) > 1e-9:
                ratio = ar_growth / rev_growth
                sources.extend(["accounts_receiv", "revenue"])
                if ratio < 0:
                    score = 20.0
                    note = "应收账款与营收变动方向相反，需关注异常"
                elif ratio <= 0.8:
                    score = 90.0
                    note = f"ΔAR/ΔRevenue={ratio:.2f}（<0.8），非激进确认特征"
                elif ratio <= 1.2:
                    score = 55.0
                    note = f"ΔAR/ΔRevenue={ratio:.2f}，应收与营收增速大体同步"
                else:
                    score = 20.0
                    note = f"ΔAR/ΔRevenue={ratio:.2f}（>1.2），应收增速快于营收增速，需关注激进确认风险"
                points.append(score)
                receivables_detail = {"ratio": round(ratio, 3), "score": score, "note": note}
    if not receivables_detail:
        insufficient.append("receivables_signal（accounts_receiv/revenue 至少需 2 期连续数据）")
        receivables_detail = {"score": None, "note": "数据不足，跳过"}
    detail["receivables_signal"] = receivables_detail

    # -- margin_stability --
    margin_detail: dict[str, Any] = {}
    margins = [
        v for v in (_field(r, "grossprofit_margin", "gross_margin") for r in rows[-_RECENT_MARGIN_PERIODS:])
        if v is not None
    ]
    if len(margins) >= 3:
        mean_m = statistics.mean(margins)
        if abs(mean_m) > 1e-9:
            cv = statistics.pstdev(margins) / abs(mean_m)
            score = _coerce_score((1 - cv) * 100)
            points.append(score)
            sources.append("grossprofit_margin")
            margin_detail = {
                "periods": len(margins), "cv": round(cv, 3), "score": round(score, 1),
                "note": f"近 {len(margins)} 期毛利率变异系数 {cv:.3f}",
            }
    if not margin_detail:
        insufficient.append("margin_stability（grossprofit_margin 至少需 3 期数据）")
        margin_detail = {"score": None, "note": "数据不足，跳过"}
    detail["margin_stability"] = margin_detail

    # -- ocf_coverage --
    ocf_detail: dict[str, Any] = {}
    ratios: list[float] = []
    for r in rows[-_RECENT_OCF_PERIODS:]:
        ocf = _field(r, "n_cashflow_act", "ocf")
        net_profit = _field(r, "net_profit")
        if ocf is not None and net_profit is not None and abs(net_profit) > 1e-9:
            ratios.append(ocf / net_profit)
    if ratios:
        avg_ratio = statistics.mean(ratios)
        if avg_ratio >= 1.0:
            score, note = 90.0, f"近 {len(ratios)} 期 OCF/净利润均值 {avg_ratio:.2f}（≥1.0），现金收入模式特征明显"
        elif avg_ratio >= 0.5:
            score, note = 60.0, f"近 {len(ratios)} 期 OCF/净利润均值 {avg_ratio:.2f}"
        elif avg_ratio >= 0:
            score, note = 30.0, f"近 {len(ratios)} 期 OCF/净利润均值 {avg_ratio:.2f}（偏低）"
        else:
            score, note = 10.0, f"近 {len(ratios)} 期 OCF/净利润均值 {avg_ratio:.2f}（为负，现金收入模式证据弱）"
        points.append(score)
        sources.extend(["n_cashflow_act", "net_profit"])
        ocf_detail = {"periods": len(ratios), "avg_ratio": round(avg_ratio, 3), "score": score, "note": note}
    if not ocf_detail:
        insufficient.append("ocf_coverage（n_cashflow_act/ocf 与 net_profit 数据不足）")
        ocf_detail = {"score": None, "note": "数据不足，跳过"}
    detail["ocf_coverage"] = ocf_detail

    score = round(statistics.mean(points), 1) if points else None
    return {
        "score": score,
        "partial": True,  # 递延收入信号恒定缺失，总分永远为部分子信号加权
        "insufficient_data": insufficient,
        "sources": sorted(set(sources)),
        "detail": detail,
    }


# ---- 客户锁定评分 ----

def customer_lockin_score(financials: list[dict]) -> dict:
    """客户锁定评分（0-100），转换成本代理（公开财务数据间接推断）。

    来源: Shy (2002), *Int'l J. of Industrial Organization* 20(3), 367-393
        + CFA Institute (2026) 护城河框架（转换成本为四类护城河来源之一）。

    三个子信号（等权重合成）：
      - gross_margin_level: 最新期毛利率水平（高 → 客户对价格不敏感）
      - gross_margin_stability: 近 8 期毛利率标准差（低 → 客户不议价，粘性高）
      - ar_turnover: 应收账款周转率 revenue/avg(AR)（高 → 客户付款及时）
    """
    rows = _sorted_rows(financials)
    sources: list[str] = []
    insufficient: list[str] = []
    detail: dict[str, Any] = {}
    points: list[float] = []

    # -- gross_margin_level --
    level_detail: dict[str, Any] = {}
    gm_latest = _field(rows[-1], "grossprofit_margin", "gross_margin") if rows else None
    if gm_latest is not None:
        if gm_latest > 50:
            score, note = 90.0, f"最新毛利率 {gm_latest:.1f}%（>50%），强客户锁定特征"
        elif gm_latest >= 25:
            score, note = 60.0, f"最新毛利率 {gm_latest:.1f}%（25%-50%），中等客户锁定特征"
        else:
            score, note = 25.0, f"最新毛利率 {gm_latest:.1f}%（<25%），客户锁定证据弱"
        points.append(score)
        sources.append("grossprofit_margin")
        level_detail = {"value": gm_latest, "score": score, "note": note}
    else:
        insufficient.append("gross_margin_level（grossprofit_margin 不可得）")
        level_detail = {"score": None, "note": "数据不足，跳过"}
    detail["gross_margin_level"] = level_detail

    # -- gross_margin_stability --
    stability_detail: dict[str, Any] = {}
    margins = [
        v for v in (_field(r, "grossprofit_margin", "gross_margin") for r in rows[-_RECENT_MARGIN_PERIODS:])
        if v is not None
    ]
    if len(margins) >= 3:
        std = statistics.pstdev(margins)
        if std < 2:
            score, note = 90.0, f"近 {len(margins)} 期毛利率标准差 {std:.2f}pp（<2pp），客户粘性高"
        elif std <= 5:
            score, note = 60.0, f"近 {len(margins)} 期毛利率标准差 {std:.2f}pp（2-5pp）"
        else:
            score, note = 25.0, f"近 {len(margins)} 期毛利率标准差 {std:.2f}pp（>5pp），客户议价能力较强"
        points.append(score)
        sources.append("grossprofit_margin")
        stability_detail = {"periods": len(margins), "std": round(std, 2), "score": score, "note": note}
    else:
        insufficient.append("gross_margin_stability（grossprofit_margin 至少需 3 期数据）")
        stability_detail = {"score": None, "note": "数据不足，跳过"}
    detail["gross_margin_stability"] = stability_detail

    # -- ar_turnover --
    turnover_detail: dict[str, Any] = {}
    if rows:
        latest = rows[-1]
        rev_latest = _field(latest, "revenue")
        ar_latest = _field(latest, "accounts_receiv", "ar")
        ar_prev = _field(rows[-2], "accounts_receiv", "ar") if len(rows) >= 2 else None
        if rev_latest is not None and ar_latest is not None:
            avg_ar = (ar_latest + ar_prev) / 2 if ar_prev is not None else ar_latest
            if avg_ar and abs(avg_ar) > 1e-9:
                turnover = rev_latest / avg_ar
                if turnover > 12:
                    score, note = 90.0, f"应收账款周转率 {turnover:.1f}x（>12x），客户付款及时"
                elif turnover >= 6:
                    score, note = 60.0, f"应收账款周转率 {turnover:.1f}x（6-12x）"
                else:
                    score, note = 25.0, f"应收账款周转率 {turnover:.1f}x（<6x），回款周期较长"
                points.append(score)
                sources.extend(["revenue", "accounts_receiv"])
                turnover_detail = {"turnover": round(turnover, 2), "score": score, "note": note}
    if not turnover_detail:
        insufficient.append("ar_turnover（revenue/accounts_receiv 不可得）")
        turnover_detail = {"score": None, "note": "数据不足，跳过"}
    detail["ar_turnover"] = turnover_detail

    score = round(statistics.mean(points), 1) if points else None
    return {
        "score": score,
        "partial": len(insufficient) > 0,
        "insufficient_data": insufficient,
        "sources": sorted(set(sources)),
        "detail": detail,
    }


# ---- 内部人买卖一致性信号 ----

_INSIDER_LABELS = ("强正向", "正向", "分歧", "负向", "强负向")


def insider_signal(holder_changes: dict) -> str:
    """内部人买卖一致性信号: "强正向"|"正向"|"分歧"|"负向"|"强负向"|"数据不足"。

    规则（补充资料 3.3，窗口锚定为记录中最新公告日往前 12 个月）：
      - "强正向": ≥3 名股东（按 holder_name 去重）增持 + 0 笔减持 + 交叉验证 ≥2 源
      - "正向": 增持笔数 > 减持笔数 × 2（且减持为 0 或满足比例）
      - "分歧": 增减持方向不明确
      - "负向": 减持笔数 > 增持笔数 × 2
      - "强负向": ≥3 名股东减持 + 0 笔增持 + 交叉验证 ≥2 源
      - "数据不足": holder_changes 缺失或无可解析的公告日期

    ⚠️ 合规：仅陈述行为事实（笔数/主体数统计），不给买卖建议。
    复用 render.py::_section_holder_changes 现有的 direction/holder_name/ann_date 字段解析规则。
    """
    data = (holder_changes or {}).get("data") if isinstance(holder_changes, dict) else None
    if not isinstance(data, list) or not data:
        return "数据不足"

    dated = [(r, parse_end_date(r.get("ann_date"))) for r in data if isinstance(r, dict)]
    dated = [(r, d) for r, d in dated if d is not None]
    if not dated:
        return "数据不足"

    anchor = max(d for _, d in dated)
    try:
        window_start = date(anchor.year - 1, anchor.month, anchor.day)
    except ValueError:
        # 处理 2/29 等非法日期回退
        window_start = date(anchor.year - 1, anchor.month, min(anchor.day, 28))

    recent = [r for r, d in dated if window_start <= d <= anchor]
    buy_records = [r for r in recent if "增" in str(r.get("direction", ""))]
    sell_records = [r for r in recent if "减" in str(r.get("direction", ""))]
    buy_entities = {str(r.get("holder_name", "")) for r in buy_records if r.get("holder_name")}
    sell_entities = {str(r.get("holder_name", "")) for r in sell_records if r.get("holder_name")}
    cross_sources = len({str(r.get("source")) for r in recent if r.get("source")})

    if len(buy_entities) >= 3 and len(sell_records) == 0 and cross_sources >= 2:
        return "强正向"
    if len(sell_entities) >= 3 and len(buy_records) == 0 and cross_sources >= 2:
        return "强负向"
    if not buy_records and not sell_records:
        return "分歧"
    if sell_records and not buy_records:
        return "负向"
    if buy_records and not sell_records:
        return "正向"
    if len(buy_records) >= 2 * len(sell_records):
        return "正向"
    if len(sell_records) >= 2 * len(buy_records):
        return "负向"
    return "分歧"


_INSIDER_SIGNAL_POINTS = {
    "强正向": 25.0, "正向": 18.0, "分歧": 10.0, "负向": 3.0, "强负向": 0.0,
}


# ---- 管理层能力代理评分 ----

def management_ability_proxy(financials: list[dict], holder_changes: dict) -> dict:
    """管理层能力代理评分（0-100，4 个子信号各占 25 分，缺失子信号从分母剔除）。

    来源: Demerjian, Lev & McVay (2012), *Management Science* 58(7), 1229-1248
        （完整 DEA+Tobit 需行业内全量公司数据，v0.1.7 无此能力，本函数为简化代理方案）。

    子信号：
      - roic_trend: ROIC 近期趋势（ebit*(1-tax)/(total_assets-total_cur_liab)；
        数据不足以计算 ROIC 时改用 roe 字段并标注"代理指标: ROE"）
      - margin_trajectory: 毛利率 + 净利率同比变化
      - insider_consistency: 复用 insider_signal() 结果映射分值
      - capex_efficiency: ΔRevenue / CAPEX

    ⚠️ 学术边界（补充资料 3.4）：Demerjian et al. (2012) 方法仅覆盖运营效率维度，
    企业文化/诚信/战略远见/接班人计划等软维度无法从公开数据量化。本函数结果为
    "定量代理评分 + 定性补充，置信度中等"，不构成对管理层的信任/不信任二元判断。
    """
    rows = _sorted_rows(financials)
    sources: list[str] = []
    insufficient: list[str] = []
    detail: dict[str, Any] = {}
    weighted_points: list[float] = []  # 每项已经是 0-25 分制

    # -- roic_trend --
    roic_pts, roic_detail, roic_sources, roic_missing = _score_roic_trend(rows)
    detail["roic_trend"] = roic_detail
    if roic_pts is not None:
        weighted_points.append(roic_pts)
        sources.extend(roic_sources)
    else:
        insufficient.append(roic_missing)

    # -- margin_trajectory --
    margin_pts, margin_detail, margin_sources, margin_missing = _score_margin_trajectory(rows)
    detail["margin_trajectory"] = margin_detail
    if margin_pts is not None:
        weighted_points.append(margin_pts)
        sources.extend(margin_sources)
    else:
        insufficient.append(margin_missing)

    # -- insider_consistency --
    signal = insider_signal(holder_changes)
    insider_pts = _INSIDER_SIGNAL_POINTS.get(signal)
    if insider_pts is not None:
        weighted_points.append(insider_pts)
        sources.append("holder_changes")
        detail["insider_consistency"] = {"signal": signal, "score": insider_pts}
    else:
        insufficient.append("insider_consistency（holder_changes 数据不足，无法生成内部人一致性信号）")
        detail["insider_consistency"] = {"signal": signal, "score": None, "note": "数据不足，跳过"}

    # -- capex_efficiency --
    capex_pts, capex_detail, capex_sources, capex_missing = _score_capex_efficiency(rows)
    detail["capex_efficiency"] = capex_detail
    if capex_pts is not None:
        weighted_points.append(capex_pts)
        sources.extend(capex_sources)
    else:
        insufficient.append(capex_missing)

    if weighted_points:
        score = round(sum(weighted_points) / (25.0 * len(weighted_points)) * 100, 1)
    else:
        score = None

    return {
        "score": score,
        "partial": len(insufficient) > 0,
        "insufficient_data": insufficient,
        "sources": sorted(set(sources)),
        "detail": detail,
        "note": (
            "定量代理评分 + 定性补充，置信度中等（Demerjian, Lev & McVay 2012 方法仅覆盖运营效率维度，"
            "企业文化/诚信/战略远见/接班人计划等软维度需人工定性判断，不构成信任/不信任的二元结论）"
        ),
    }


def _score_roic_trend(rows: list[dict]) -> tuple[float | None, dict, list[str], str]:
    series: list[float] = []
    metric_label = "ROIC"
    used_sources = ["ebit", "total_assets", "total_cur_liab"]
    for r in rows[-_RECENT_ROIC_PERIODS:]:
        ebit = _field(r, "ebit")
        total_assets = _field(r, "total_assets")
        total_cur_liab = _field(r, "total_cur_liab")
        if ebit is not None and total_assets is not None and total_cur_liab is not None:
            denom = total_assets - total_cur_liab
            if abs(denom) > 1e-9:
                tax_rate = _infer_tax_rate(r)
                series.append(ebit * (1 - tax_rate) / denom)
                continue
        series.append(None)
    series = [v for v in series if v is not None]

    if len(series) < 3:
        # 回退到 ROE 代理指标
        roe_series = [v for v in (_field(r, "roe") for r in rows[-_RECENT_ROIC_PERIODS:]) if v is not None]
        if len(roe_series) >= 3:
            series = roe_series
            metric_label = "代理指标: ROE"
            used_sources = ["roe"]
        else:
            return (
                None,
                {"score": None, "note": "数据不足，跳过"},
                [],
                "roic_trend（ebit/total_assets/total_cur_liab 与 roe 均不足以判断近 3 期趋势）",
            )

    last3 = series[-3:]
    if last3[0] < last3[1] < last3[2]:
        score, note = 25.0, f"近 3 期{metric_label}连续上升"
    elif last3[-1] > last3[-2]:
        score, note = 15.0, f"近 2 期{metric_label}上升，未满 3 期连续上升条件"
    elif last3[-1] == last3[-2] == last3[0]:
        score, note = 8.0, f"{metric_label}近期持平"
    else:
        score, note = 5.0, f"{metric_label}近期未见连续上升趋势"
    return score, {"metric": metric_label, "series": [round(v, 4) for v in last3], "score": score, "note": note}, used_sources, ""


def _score_margin_trajectory(rows: list[dict]) -> tuple[float | None, dict, list[str], str]:
    if not rows:
        return None, {"score": None, "note": "数据不足，跳过"}, [], "margin_trajectory（无财务数据）"
    latest = rows[-1]
    yoy = _find_yoy_row(rows, latest)
    if yoy is None and len(rows) >= 2:
        yoy = rows[-2]
        period_note = "未找到严格同比期，改用环比上一期"
    elif yoy is not None:
        period_note = "同比"
    else:
        return None, {"score": None, "note": "数据不足，跳过"}, [], "margin_trajectory（历史财报期数不足以比较）"

    gm_latest = _field(latest, "grossprofit_margin", "gross_margin")
    gm_prior = _field(yoy, "grossprofit_margin", "gross_margin")
    nm_latest = _field(latest, "netprofit_margin", "net_margin")
    nm_prior = _field(yoy, "netprofit_margin", "net_margin")

    if None in (gm_latest, gm_prior, nm_latest, nm_prior):
        return (
            None,
            {"score": None, "note": "数据不足，跳过"},
            [],
            "margin_trajectory（grossprofit_margin/netprofit_margin 任一期缺失）",
        )

    gm_change = gm_latest - gm_prior
    nm_change = nm_latest - nm_prior
    if gm_change > 2 and nm_change > 2:
        score = 25.0
    elif gm_change > 2 or nm_change > 2:
        score = 12.0
    elif gm_change > 0 and nm_change > 0:
        score = 8.0
    else:
        score = 0.0
    note = f"{period_note}毛利率变化 {gm_change:+.2f}pp，净利率变化 {nm_change:+.2f}pp"
    return (
        score,
        {"gm_change": round(gm_change, 2), "nm_change": round(nm_change, 2), "score": score, "note": note},
        ["grossprofit_margin", "netprofit_margin"],
        "",
    )


def _score_capex_efficiency(rows: list[dict]) -> tuple[float | None, dict, list[str], str]:
    if len(rows) < 2:
        return None, {"score": None, "note": "数据不足，跳过"}, [], "capex_efficiency（revenue/cap_ex 至少需 2 期数据）"
    latest, prev = rows[-1], rows[-2]
    rev_latest = _field(latest, "revenue")
    rev_prev = _field(prev, "revenue")
    cap_ex_raw = _field(latest, "cap_ex")
    if None in (rev_latest, rev_prev, cap_ex_raw):
        return None, {"score": None, "note": "数据不足，跳过"}, [], "capex_efficiency（revenue/cap_ex 缺失）"
    cap_ex = abs(cap_ex_raw)
    if cap_ex < 1e-9:
        return None, {"score": None, "note": "数据不足，跳过"}, [], "capex_efficiency（cap_ex 为 0，无法计算比率）"

    delta_rev = rev_latest - rev_prev
    ratio = delta_rev / cap_ex
    if ratio > 2:
        score, note = 25.0, f"ΔRevenue/CAPEX={ratio:.2f}（>2x），投资回报效率较高"
    elif ratio > 1:
        score, note = 15.0, f"ΔRevenue/CAPEX={ratio:.2f}（1-2x）"
    elif ratio > 0:
        score, note = 8.0, f"ΔRevenue/CAPEX={ratio:.2f}（0-1x），投资回报效率偏低"
    else:
        score, note = 0.0, f"ΔRevenue/CAPEX={ratio:.2f}（营收未增长或下滑）"
    return score, {"ratio": round(ratio, 3), "score": score, "note": note}, ["revenue", "cap_ex"], ""


# ---- AI 分析置信度矩阵 ----

def _dimension_confidence(collection: dict, key: str, module_label: str) -> dict:
    dim = collection.get(key) if isinstance(collection, dict) else None
    if not isinstance(dim, dict):
        return {
            "module": module_label, "confidence": "低",
            "reason": f"{key} 维度数据不可得（未采集或采集结果缺失）",
        }
    status = dim.get("status")
    multi_source = bool((dim.get("_meta") or {}).get("multi_source"))
    if status == "available" and multi_source:
        return {"module": module_label, "confidence": "高", "reason": f"{key} 维度数据可得且多源交叉验证"}
    if status == "available":
        return {"module": module_label, "confidence": "中", "reason": f"{key} 维度数据可得，单源未交叉验证"}
    if status == "partial":
        return {"module": module_label, "confidence": "中", "reason": f"{key} 维度数据部分可得"}
    return {"module": module_label, "confidence": "低", "reason": f"{key} 维度数据不可得（status={status!r}）"}


def confidence_matrix(collection: dict) -> dict:
    """AI 分析置信度矩阵：8 模块 × 高/中/低，由引擎根据数据可得性自动计算（非 LLM 主观判断）。

    `collection` 为顶层采集结果，键为维度名（如 "financials"/"kline"），
    值为该维度的 legacy dict（`schema.DimensionResult.to_legacy_dict()` 输出，
    含 `status` 与 `_meta.multi_source` 字段）。

    "估值判断"固定标注"中/低"、"周期拐点判断"固定标注"低"——这是学术共识（估值依赖
    主观假设参数、周期拐点具有不可预测性），不由数据完整性决定，即使数据完整也不提升。
    """
    collection = collection if isinstance(collection, dict) else {}
    rows = [
        _dimension_confidence(collection, "financials", "财务数据分析"),
        _dimension_confidence(collection, "holder_changes", "股东与内部人行为分析"),
        _dimension_confidence(collection, "research", "机构预期与研报分析"),
        _dimension_confidence(collection, "industry", "行业与产业链分析"),
        _dimension_confidence(collection, "northbound", "资金流向分析"),
        _dimension_confidence(collection, "kline", "技术走势分析"),
        {
            "module": "估值判断",
            "confidence": "中/低",
            "reason": "估值依赖增长率/贴现率等假设参数的主观选择，非单纯数据可得性问题，学术共识不给高置信度",
        },
        {
            "module": "周期拐点判断",
            "confidence": "低",
            "reason": "周期拐点具有不可预测性（学术共识），无论数据完整度如何均固定为低置信度",
        },
    ]
    insufficient_data = [
        r["module"] for r in rows if r["confidence"] == "低" and r["module"] != "周期拐点判断"
    ]
    return {
        "rows": rows,
        "sources": ["collection[dim].status", "collection[dim]._meta.multi_source"],
        "insufficient_data": insufficient_data,
    }
