"""Single-symbol quality check — 7 metrics + industry-aware exemptions (v0.2.3)."""

from __future__ import annotations

from typing import Any

from .financials import normalize_end_date
from .industry import get_quality_overrides, get_sector_group
from .nums import ONE_PER_YI, coalesce_field, safe_float
from .risk_scanner import ocf_np_divergence_flag
from .scoring import _score_roic_trend
from .valuation import extract_financial_rows


def _sorted_fin_rows(collection: dict) -> list[dict]:
    from .schema import index_dimensions
    dims = index_dimensions(collection)
    fin = dims.get("financials", {}).get("data")
    if isinstance(fin, list):
        rows = [r for r in fin if isinstance(r, dict)]
    else:
        rows = extract_financial_rows(dims.get("financials", {}))
    return sorted(
        rows,
        key=lambda r: normalize_end_date(str(r.get("end_date") or "")),
    )


def _exemptions(collection: dict, rows: list[dict]) -> list[str]:
    """Return triggered exemption labels (v0.2.3: industry-aware)."""
    from .schema import index_dimensions
    from datetime import datetime

    ex: list[str] = []
    basic = index_dimensions(collection).get("basic_info", {}).get("data") or {}
    if isinstance(basic, dict):
        industry = str(basic.get("industry") or basic.get("行业") or "")
        if industry:
            sector = get_sector_group(industry)
            if sector != "general":
                ex.append(f"行业模块: {sector}")
        list_date = str(basic.get("list_date") or basic.get("上市时间") or "")
        if list_date:
            s = list_date.replace("-", "")[:8]
            try:
                ld = datetime.strptime(s, "%Y%m%d")
                years = (datetime.now() - ld).days / 365.25
                if years < 3:
                    ex.append("上市 < 3 年（数据不足）")
            except ValueError:
                pass

    # 仅比较年报（end_date 在 12 月），避免季度数据因季节性触发误判
    if len(rows) >= 4:
        from .financials import parse_end_date
        annual_rows = [
            r for r in rows[-8:]  # 足够找 2 个年报
            if str(r.get("end_date", "")).endswith(("12-31", "1231"))
        ]
        if len(annual_rows) >= 2:
            revs = [coalesce_field(r, "revenue", "total_revenue") for r in annual_rows[-2:]]
            revs = [v for v in revs if v is not None]
            if len(revs) == 2:
                change = abs(revs[-1] - revs[0]) / abs(revs[0])
                if change > 0.3:
                    ex.append("转型期（营收结构变更 > 30%）")
    return ex


def _metric_roic(rows: list[dict]) -> dict[str, Any]:
    pts, detail, _, missing = _score_roic_trend(rows)
    series_pct = detail.get("series") or []
    if not series_pct:
        return {"id": 1, "name": "ROIC (3年均)", "status": "skip", "detail": missing}
    metric = str(detail.get("metric") or "ROIC")
    # ROIC from scoring is a decimal ratio (0.15); ROE proxy is usually already in %
    if "ROE" in metric:
        as_pct = (
            [v * 100 for v in series_pct]
            if max(abs(v) for v in series_pct) < 1
            else list(series_pct)
        )
    else:
        as_pct = [v * 100 for v in series_pct]
    avg = sum(as_pct) / len(as_pct)
    fail = avg < 5.0
    return {
        "id": 1, "name": "ROIC (3年均)", "value": round(avg, 2),
        "threshold": "< 5% 否决", "status": "fail" if fail else "pass",
        "type": "veto", "detail": f"近 {len(as_pct)} 期均值 {avg:.2f}% ({metric})",
    }


def _metric_fcf_5y(rows: list[dict]) -> dict[str, Any]:
    # n_cashflow_act 为财年累计口径（Q1→H1→3Q→年报 逐期累加），把重叠的累计期
    # 直接相加会把同一财年时期重复计入（约 2.75x），季节负 Q1 的公司 5 行累计
    # 还可能 <0 被误否决。仅取年报（1231）行求和，消除重叠；年报不足则标不可得。
    annual_rows = [
        r for r in rows[-20:]  # 季度数据时 20 行 ≈ 5 个财年
        if normalize_end_date(str(r.get("end_date") or "")).endswith("1231")
    ]
    totals: list[float] = []
    for r in annual_rows[-5:]:
        ocf = coalesce_field(r, "n_cashflow_act", "ocf")
        capex = coalesce_field(r, "cap_ex", "c_pay_acq_const_fiolta")
        if ocf is not None and capex is not None:
            # CapEx 字段约定：正数=资本支出金额。abs() 兼容少数来源返回负数的场景。
            # 若新来源使用不同约定（如负数=支出），需在此处显式判断符号。
            totals.append(ocf - abs(capex))
    if len(totals) < 2:
        return {
            "id": 2, "name": "累计 FCF (5年)", "status": "skip",
            "detail": f"无足够年度数据（n_cashflow_act 为累计口径，需 ≥2 个年报期去重求和，现有 {len(totals)} 期）",
        }
    total = sum(totals)
    total_yi = total / ONE_PER_YI if abs(total) > 1e6 else total
    fail = total_yi < 0
    return {
        "id": 2, "name": "累计 FCF (5年)", "value": round(total_yi, 2),
        "threshold": "< 0 否决", "status": "fail" if fail else "pass",
        "type": "veto", "detail": f"{len(totals)} 个年报期累计 {total_yi:.2f} 亿",
    }


def _metric_interest_coverage(rows: list[dict]) -> dict[str, Any]:
    latest = rows[-1] if rows else {}
    ebit = coalesce_field(latest, "ebit", "operate_profit")
    interest = coalesce_field(latest, "fin_exp_int_exp", "interest_expense", "interestexpense")
    if ebit is None or interest is None:
        return {"id": 3, "name": "利息覆盖倍数", "status": "skip", "detail": "字段缺失"}
    if abs(interest) < 1e-9:
        return {"id": 3, "name": "利息覆盖倍数", "status": "skip", "detail": "利息费用为零或接近零，覆盖率无定义"}
    cov = ebit / abs(interest)
    fail = cov < 2.0
    return {
        "id": 3, "name": "利息覆盖倍数", "value": round(cov, 2),
        "threshold": "< 2x 否决", "status": "fail" if fail else "pass",
        "type": "veto", "detail": f"EBIT/利息 = {cov:.2f}x",
    }


def _metric_gross_margin_vol(rows: list[dict]) -> dict[str, Any]:
    margins = []
    for r in rows[-5:]:
        gm = coalesce_field(r, "grossprofit_margin", "gross_margin")
        if gm is not None:
            margins.append(gm)
    if len(margins) < 3:
        return {"id": 4, "name": "毛利率波动 (5年 std)", "status": "skip", "detail": "数据不足"}
    mean = sum(margins) / len(margins)
    var = sum((m - mean) ** 2 for m in margins) / (len(margins) - 1)
    std = var ** 0.5
    # Without peer universe, use heuristic: std > 10pp warns
    warn = std > 10.0
    return {
        "id": 4, "name": "毛利率波动 (5年 std)", "value": round(std, 2),
        "threshold": "> 10pp 警告（单标的启发式）", "status": "warn" if warn else "pass",
        "type": "warning", "detail": f"5年 std = {std:.2f}pp",
    }


def _metric_ocf_np(rows: list[dict]) -> dict[str, Any]:
    flag = ocf_np_divergence_flag(rows)
    ratio = flag.get("ratio")
    warn = flag.get("triggered", False)
    return {
        "id": 5, "name": "OCF/净利润 (最新期)", "value": ratio,
        "threshold": "< 0.6 警告", "status": "warn" if warn else "pass",
        "type": "warning", "detail": flag.get("detail", ""),
    }


def _metric_net_margin_trend(rows: list[dict]) -> dict[str, Any]:
    margins = []
    for r in rows[-3:]:
        rev = coalesce_field(r, "revenue", "total_revenue")
        np_ = coalesce_field(r, "n_income_attr_p", "net_profit", "netprofit")
        if rev is not None and abs(rev) > 1e-9 and np_ is not None:
            margins.append(np_ / rev * 100)
    if len(margins) < 3:
        return {"id": 6, "name": "净利率趋势 (3年)", "status": "skip", "detail": "数据不足"}
    declining = all(margins[i] > margins[i + 1] for i in range(len(margins) - 1))
    return {
        "id": 6, "name": "净利率趋势 (3年)", "value": [round(m, 2) for m in margins],
        "threshold": "连续下降 警告", "status": "warn" if declining else "pass",
        "type": "warning",
        "detail": f"序列: {[round(m,1) for m in margins]}%",
    }


def _metric_share_dilution(rows: list[dict]) -> dict[str, Any]:
    shares = []
    for r in rows[-4:]:
        s = coalesce_field(r, "total_share", "total_share_capital")
        if s is not None:
            shares.append(s)
    if len(shares) < 2:
        return {"id": 7, "name": "股本膨胀", "status": "skip", "detail": "股本字段缺失"}
    annual_rates = []
    for i in range(1, len(shares)):
        if shares[i - 1] > 0:
            annual_rates.append((shares[i] / shares[i - 1] - 1) * 100)
    max_rate = max(annual_rates) if annual_rates else 0
    warn = max_rate > 5.0
    return {
        "id": 7, "name": "股本膨胀", "value": round(max_rate, 2),
        "threshold": "> 5%/年 警告", "status": "warn" if warn else "pass",
        "type": "warning", "detail": f"最大年化膨胀 {max_rate:.2f}%",
    }


def run_quality_check(collection: dict) -> dict[str, Any]:
    """Run 7 metrics with industry-aware exemptions (v0.2.3).

    Per-metric overrides from the industry module allow skipping metrics
    that don't apply to a sector (e.g., gross_margin for banks).
    """
    from .schema import index_dimensions

    rows = _sorted_fin_rows(collection)
    exemptions = _exemptions(collection, rows)

    # 获取行业特异性质量检查覆盖规则
    basic = index_dimensions(collection).get("basic_info", {}).get("data") or {}
    industry = str(basic.get("industry") or basic.get("行业") or "") if isinstance(basic, dict) else ""
    overrides = get_quality_overrides(industry) if industry else {}

    # 指标名 → 覆盖率映射（用于跳过不适用的指标）
    _METRIC_OVERRIDE_MAP = {
        "ROIC (3年均)": overrides.get("roic"),
        "累计 FCF (5年)": None,  # FCF is always relevant
        "利息覆盖倍数": overrides.get("interest_coverage"),
        "毛利率波动 (5年 std)": overrides.get("gross_margin_volatility") or overrides.get("gross_margin"),
        "OCF/净利润 (最新期)": overrides.get("ocf_to_np") or overrides.get("ocf_negative"),
        "净利率趋势 (3年)": None,  # net margin is always relevant
        "股本膨胀": None,  # share dilution is always relevant
    }

    metrics = [
        _metric_roic(rows),
        _metric_fcf_5y(rows),
        _metric_interest_coverage(rows),
        _metric_gross_margin_vol(rows),
        _metric_ocf_np(rows),
        _metric_net_margin_trend(rows),
        _metric_share_dilution(rows),
    ]

    # Apply per-metric industry overrides (v0.2.3)
    for m in metrics:
        metric_name = m.get("name", "")
        override = _METRIC_OVERRIDE_MAP.get(metric_name)
        if override == "skip":
            m["status"] = "exempted"
            m["detail"] = f"行业豁免 ({sector_label(industry)}); 原值: {m.get('detail', '')}"

    # Apply legacy exemptions (上市 <3y / 转型期) to veto metrics
    skip_veto = any(
        any(k in e for k in ("上市", "转型期"))
        for e in exemptions
    )
    if skip_veto:
        for m in metrics:
            if m.get("type") == "veto" and m.get("status") == "fail":
                if m.get("status") != "exempted":
                    m["status"] = "exempted"
                    m["detail"] = f"豁免({', '.join(exemptions)}); 原值: {m['detail']}"

    veto_fails = sum(1 for m in metrics if m.get("type") == "veto" and m.get("status") == "fail")
    warnings = sum(1 for m in metrics if m.get("status") == "warn")

    return {
        "symbol": collection.get("symbol"),
        "metrics": metrics,
        "exemptions": exemptions,
        "summary": {
            "veto_failures": veto_fails,
            "warnings": warnings,
            "overall": "fail" if veto_fails else ("warn" if warnings else "pass"),
        },
        "disclaimer": "指标阈值为行业启发式规则，非精确学术阈值。",
    }


def sector_label(industry: str) -> str:
    """返回行业可读标签。"""
    sg = get_sector_group(industry)
    labels = {
        "financial": "金融行业",
        "tech": "科技行业",
        "consumer": "消费品行业",
        "industrial": "周期性行业",
        "healthcare": "医药行业",
        "general": "通用",
    }
    return labels.get(sg, sg)


def format_quality_check(result: dict) -> str:
    """Human-readable output."""
    lines = [
        f"# 质地检查 — {result.get('symbol', '?')}",
        "",
        f"**总体**: {result['summary']['overall']}",
    ]
    if result.get("exemptions"):
        lines.append(f"**豁免**: {', '.join(result['exemptions'])}")
    lines.append("")
    for m in result.get("metrics", []):
        icon = {"pass": "✅", "fail": "❌", "warn": "⚠️", "skip": "⏭", "exempted": "🔓"}.get(
            m.get("status", ""), "?"
        )
        lines.append(f"{icon} **{m.get('name')}**: {m.get('detail', m.get('status'))}")
    lines.extend(["", f"*{result.get('disclaimer', '')}*"])
    return "\n".join(lines)
