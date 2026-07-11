"""Financial rigor checks on collection JSON (v0.1.9).

Does not fetch data — operates on an existing collection dict from collect/report.
"""

from __future__ import annotations

import ast
import operator
import re
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from .nums import coalesce_field, safe_float
from .schema import SourceResult, _auto_cross_validate, index_dimensions, relative_diff_pct

FAIL_THRESHOLD_PCT = 5.0
WARN_THRESHOLD_PCT = 1.0

_DECIMAL_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.USub: operator.neg,
}


@dataclass
class RigorReport:
    command: str
    field: str
    reported_value: float | Decimal | None
    computed_value: float | Decimal | None
    deviation_pct: float
    status: str  # pass | warn | fail
    detail: str

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        for k in ("reported_value", "computed_value"):
            v = d[k]
            if isinstance(v, Decimal):
                d[k] = float(v)
        return d


def _status_from_deviation(deviation_pct: float) -> str:
    if deviation_pct > FAIL_THRESHOLD_PCT:
        return "fail"
    if deviation_pct > WARN_THRESHOLD_PCT:
        return "warn"
    return "pass"


def _deviation_pct(reported: float | None, computed: float | None) -> float:
    if reported is None or computed is None:
        return 0.0
    avg = (abs(reported) + abs(computed)) / 2.0
    if avg < 1e-12:
        return 0.0 if reported == computed else 100.0
    return abs(reported - computed) / avg * 100.0


def _quote_snapshot(quote_data: Any) -> dict:
    if isinstance(quote_data, dict):
        return quote_data
    if isinstance(quote_data, list) and quote_data:
        return quote_data[-1] if isinstance(quote_data[-1], dict) else {}
    return {}


def _parse_share_count(basic: dict) -> float | None:
    """Parse total shares from basic_info (万股 → 万股 float)."""
    raw = basic.get("总股本") or basic.get("total_share") or basic.get("float_share")
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    s = str(raw).strip().replace(",", "")
    mult = 1.0
    if "亿" in s:
        mult = 1e4  # 亿股 → 万股
        s = s.replace("亿股", "").replace("亿", "")
    elif "万" in s:
        s = s.replace("万股", "").replace("万", "")
    try:
        return float(s) * mult
    except ValueError:
        return safe_float(raw)


def verify_market_cap(collection: dict) -> list[RigorReport]:
    """股价 × 总股本 vs 报告市值。"""
    dims = index_dimensions(collection)
    quote = _quote_snapshot(dims.get("quote", {}).get("data"))
    basic = dims.get("basic_info", {}).get("data") or {}
    if not isinstance(basic, dict):
        basic = {}

    price = coalesce_field(quote, "price", "close")
    shares_wan = _parse_share_count(basic)
    reported_mv = coalesce_field(quote, "total_mv")

    if price is None or shares_wan is None or reported_mv is None:
        return [RigorReport(
            command="verify-market-cap",
            field="total_mv",
            reported_value=reported_mv,
            computed_value=None,
            deviation_pct=0.0,
            status="warn",
            detail="缺少 price/总股本/total_mv 之一，跳过验算",
        )]

    # price 元/股 × 万股 × 1e4 / 1e8 = 亿元
    computed_mv = price * shares_wan * 1e4 / 1e8
    dev = _deviation_pct(reported_mv, computed_mv)
    return [RigorReport(
        command="verify-market-cap",
        field="total_mv",
        reported_value=round(reported_mv, 4),
        computed_value=round(computed_mv, 4),
        deviation_pct=round(dev, 2),
        status=_status_from_deviation(dev),
        detail=f"报告市值 {reported_mv:.2f}亿 vs 验算 {computed_mv:.2f}亿（股价×总股本）",
    )]


def verify_valuation(collection: dict) -> list[RigorReport]:
    """PE/PB/ROE/FCF Yield 逐项反算（基于可得字段）。"""
    dims = index_dimensions(collection)
    quote = _quote_snapshot(dims.get("quote", {}).get("data"))
    val_data = dims.get("valuation", {}).get("data")
    fin_data = dims.get("financials", {}).get("data")

    reports: list[RigorReport] = []
    price = coalesce_field(quote, "price", "close")
    reported_mv = coalesce_field(quote, "total_mv")

    pe_reported = coalesce_field(quote, "pe_ratio", "pe_ttm")
    if pe_reported is None and isinstance(val_data, list) and val_data:
        last = val_data[-1] if isinstance(val_data[-1], dict) else {}
        pe_reported = coalesce_field(last, "pe_ttm", "pe")

    latest_fin: dict = {}
    if isinstance(fin_data, list) and fin_data:
        latest_fin = fin_data[-1] if isinstance(fin_data[-1], dict) else {}

    net_income = coalesce_field(
        latest_fin, "n_income_attr_p", "net_profit", "netprofit",
    )
    equity = coalesce_field(latest_fin, "total_hldr_eqy_exc_min_int", "total_equity", "equity")
    ocf = coalesce_field(latest_fin, "n_cashflow_act", "ocf")

    # PE = 市值(亿) / 净利润(亿) — 仅年报净利可对标 PE(TTM)
    if pe_reported is not None and reported_mv is not None and net_income is not None:
        from .financials import normalize_end_date
        ed = normalize_end_date(str(latest_fin.get("end_date") or ""))
        if not ed.endswith("1231"):
            reports.append(RigorReport(
                command="verify-valuation",
                field="pe_ttm",
                reported_value=round(pe_reported, 2),
                computed_value=None,
                deviation_pct=0.0,
                status="warn",
                detail=f"非年报净利（end_date={ed or '—'}），跳过 PE(TTM) 验算",
            ))
        else:
            ni_yi = net_income / 1e8 if abs(net_income) > 1e6 else net_income
            if ni_yi and ni_yi > 0:
                pe_calc = reported_mv / ni_yi
                dev = _deviation_pct(pe_reported, pe_calc)
                reports.append(RigorReport(
                    command="verify-valuation",
                    field="pe_ttm",
                    reported_value=round(pe_reported, 2),
                    computed_value=round(pe_calc, 2),
                    deviation_pct=round(dev, 2),
                    status=_status_from_deviation(dev),
                    detail=f"PE 报告 {pe_reported:.2f}x vs 市值/净利 {pe_calc:.2f}x",
                ))

    pb_reported = None
    if isinstance(val_data, list) and val_data:
        last = val_data[-1] if isinstance(val_data[-1], dict) else {}
        pb_reported = coalesce_field(last, "pb")
    if pb_reported is not None and reported_mv is not None and equity is not None:
        eq_yi = equity / 1e8 if abs(equity) > 1e6 else equity
        if eq_yi and eq_yi > 0:
            pb_calc = reported_mv / eq_yi
            dev = _deviation_pct(pb_reported, pb_calc)
            reports.append(RigorReport(
                command="verify-valuation",
                field="pb",
                reported_value=round(pb_reported, 2),
                computed_value=round(pb_calc, 2),
                deviation_pct=round(dev, 2),
                status=_status_from_deviation(dev),
                detail=f"PB 报告 {pb_reported:.2f}x vs 市值/净资产 {pb_calc:.2f}x",
            ))

    roe_reported = coalesce_field(latest_fin, "roe")
    if roe_reported is not None and net_income is not None and equity is not None and equity != 0:
        roe_calc = net_income / equity * 100
        dev = _deviation_pct(roe_reported, roe_calc)
        reports.append(RigorReport(
            command="verify-valuation",
            field="roe",
            reported_value=round(roe_reported, 2),
            computed_value=round(roe_calc, 2),
            deviation_pct=round(dev, 2),
            status=_status_from_deviation(dev),
            detail=f"ROE 报告 {roe_reported:.2f}% vs 净利/净资产 {roe_calc:.2f}%",
        ))

    if reported_mv is not None and ocf is not None and reported_mv > 0:
        ocf_yi = ocf / 1e8 if abs(ocf) > 1e6 else ocf
        fcf_yield_calc = ocf_yi / reported_mv * 100
        reports.append(RigorReport(
            command="verify-valuation",
            field="fcf_yield_proxy",
            reported_value=None,
            computed_value=round(fcf_yield_calc, 2),
            deviation_pct=0.0,
            status="pass",
            detail=f"OCF/市值 代理 FCF Yield ≈ {fcf_yield_calc:.2f}%（无独立报告值对比）",
        ))

    if not reports:
        reports.append(RigorReport(
            command="verify-valuation",
            field="valuation",
            reported_value=None,
            computed_value=None,
            deviation_pct=0.0,
            status="warn",
            detail="估值验算字段不足，跳过",
        ))
    return reports


def cross_validate(collection: dict) -> list[RigorReport]:
    """多源交叉验证，复用 schema._auto_cross_validate。"""
    reports: list[RigorReport] = []
    for dim in collection.get("dimensions", []):
        if not isinstance(dim, dict):
            continue
        meta = dim.get("_meta") or {}
        all_src_dicts = meta.get("all_sources") or []
        if len(all_src_dicts) < 2:
            continue
        sources: list[SourceResult] = []
        for sd in all_src_dicts:
            if not isinstance(sd, dict) or not sd.get("success"):
                continue
            sources.append(SourceResult(
                source=str(sd.get("source", "unknown")),
                data=sd.get("data"),
                dimension=str(dim.get("dimension", "")),
                query_params=str(sd.get("query_params", "")),
                confidence=str(sd.get("confidence", "low")),
                latency_ms=float(sd.get("latency_ms") or 0),
                error=sd.get("error"),
                fetched_at=str(sd.get("fetched_at", "")),
            ))
        if len(sources) < 2:
            continue
        cv = _auto_cross_validate(str(dim.get("dimension", "")), sources)
        if cv is None:
            continue
        m = re.search(r"([\d.]+)%", cv.detail or "")
        dev_pct = float(m.group(1)) if m else 0.0
        reports.append(RigorReport(
            command="cross-validate",
            field=str(dim.get("dimension", "")),
            reported_value=None,
            computed_value=None,
            deviation_pct=dev_pct,
            status="fail" if dev_pct > FAIL_THRESHOLD_PCT else (
                "warn" if dev_pct > WARN_THRESHOLD_PCT else "pass"
            ),
            detail=cv.detail or cv.status,
        ))
    if not reports:
        reports.append(RigorReport(
            command="cross-validate",
            field="—",
            reported_value=None,
            computed_value=None,
            deviation_pct=0.0,
            status="pass",
            detail="无可交叉验证的多源数值维度",
        ))
    return reports


def _eval_decimal(expr: str) -> Decimal:
    """Safe Decimal arithmetic from expression string."""
    tree = ast.parse(expr.strip(), mode="eval")

    def _eval(node: ast.AST) -> Decimal:
        if isinstance(node, ast.Expression):
            return _eval(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float, str)):
            return Decimal(str(node.value))
        if isinstance(node, ast.UnaryOp) and type(node.op) in _DECIMAL_OPS:
            return _DECIMAL_OPS[type(node.op)](_eval(node.operand))
        if isinstance(node, ast.BinOp) and type(node.op) in _DECIMAL_OPS:
            return _DECIMAL_OPS[type(node.op)](_eval(node.left), _eval(node.right))
        raise ValueError(f"不支持的表达式: {ast.dump(node)}")

    return _eval(tree)


def calc(expression: str) -> RigorReport:
    """精确 Decimal 算术（可选验算）。"""
    try:
        result = _eval_decimal(expression)
        return RigorReport(
            command="calc",
            field="expression",
            reported_value=None,
            computed_value=result,
            deviation_pct=0.0,
            status="pass",
            detail=f"{expression} = {result}",
        )
    except (InvalidOperation, ValueError, SyntaxError) as exc:
        return RigorReport(
            command="calc",
            field="expression",
            reported_value=None,
            computed_value=None,
            deviation_pct=0.0,
            status="fail",
            detail=f"计算失败: {exc}",
        )


def run_rigor(
    collection: dict,
    commands: list[str] | None = None,
    *,
    calc_expr: str | None = None,
) -> list[RigorReport]:
    """Run rigor commands on collection."""
    all_cmds = commands or [
        "verify-market-cap", "verify-valuation", "cross-validate",
    ]
    reports: list[RigorReport] = []
    for cmd in all_cmds:
        if cmd == "verify-market-cap":
            reports.extend(verify_market_cap(collection))
        elif cmd == "verify-valuation":
            reports.extend(verify_valuation(collection))
        elif cmd == "cross-validate":
            reports.extend(cross_validate(collection))
        elif cmd == "calc" and calc_expr:
            reports.append(calc(calc_expr))
    return reports


def has_blocking_failures(reports: list[RigorReport], *, strict: bool = False) -> bool:
    """True if strict mode and any fail status."""
    if not strict:
        return False
    return any(r.status == "fail" for r in reports)
