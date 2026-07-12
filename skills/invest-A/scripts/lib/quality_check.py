"""Single-symbol quality check — 7 metrics + 3 exemptions (v0.1.9)."""

from __future__ import annotations

from typing import Any

from .nums import coalesce_field, safe_float
from .risk_scanner import ocf_np_divergence_flag
from .scoring import _score_roic_trend
from .valuation import extract_financial_rows


def _sorted_fin_rows(collection: dict) -> list[dict]:
    from .financials import normalize_end_date
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
    """Return triggered exemption labels."""
    from .schema import index_dimensions
    from datetime import datetime

    ex: list[str] = []
    basic = index_dimensions(collection).get("basic_info", {}).get("data") or {}
    if isinstance(basic, dict):
        industry = str(basic.get("industry") or basic.get("行业") or "")
        if any(k in industry for k in ("银行", "保险", "证券", "地产", "房地产")):
            ex.append("行业特殊（金融/地产）")
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

    if len(rows) >= 4:
        revs = [coalesce_field(r, "revenue", "total_revenue") for r in rows[-4:]]
        revs = [v for v in revs if v is not None]
        if len(revs) >= 2 and revs[-1] is not None and revs[0] is not None:
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
    total = 0.0
    count = 0
    for r in rows[-5:]:
        ocf = coalesce_field(r, "n_cashflow_act", "ocf")
        capex = coalesce_field(r, "cap_ex", "c_pay_acq_const_fiolta")
        if ocf is not None:
            fcf = ocf - abs(capex or 0)
            total += fcf
            count += 1
    if count == 0:
        return {"id": 2, "name": "累计 FCF (5年)", "status": "skip", "detail": "无现金流数据"}
    total_yi = total / 1e8 if abs(total) > 1e6 else total
    fail = total_yi < 0
    return {
        "id": 2, "name": "累计 FCF (5年)", "value": round(total_yi, 2),
        "threshold": "< 0 否决", "status": "fail" if fail else "pass",
        "type": "veto", "detail": f"{count} 期累计 {total_yi:.2f} 亿",
    }


def _metric_interest_coverage(rows: list[dict]) -> dict[str, Any]:
    latest = rows[-1] if rows else {}
    ebit = coalesce_field(latest, "ebit", "operate_profit")
    interest = coalesce_field(latest, "fin_exp_int_exp", "interest_expense", "interestexpense")
    if ebit is None or interest is None or interest == 0:
        return {"id": 3, "name": "利息覆盖倍数", "status": "skip", "detail": "字段缺失"}
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
        if rev and rev != 0 and np_ is not None:
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
    """Run 7 metrics with exemptions."""
    rows = _sorted_fin_rows(collection)
    exemptions = _exemptions(collection, rows)
    metrics = [
        _metric_roic(rows),
        _metric_fcf_5y(rows),
        _metric_interest_coverage(rows),
        _metric_gross_margin_vol(rows),
        _metric_ocf_np(rows),
        _metric_net_margin_trend(rows),
        _metric_share_dilution(rows),
    ]

    # Apply exemptions to veto metrics (上市 / 行业特殊 / 转型期)
    skip_veto = any(
        any(k in e for k in ("上市", "行业特殊", "转型期"))
        for e in exemptions
    )
    if skip_veto:
        for m in metrics:
            if m.get("type") == "veto" and m.get("status") == "fail":
                m["status"] = "exempted"
                m["detail"] = f"豁免: {', '.join(exemptions)}"

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
