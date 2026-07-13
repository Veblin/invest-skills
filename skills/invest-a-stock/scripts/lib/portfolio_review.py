"""Portfolio risk characteristics (v0.1.9) — no rebalancing advice (LAW 6)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_holdings(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("holdings.json 须为 [{symbol, weight}, ...] 数组")
    return data


def _returns_from_kline(kline: list[dict]) -> list[tuple[str, float]]:
    from .technical import sort_kline_asc
    rows = sort_kline_asc(kline)
    out: list[tuple[str, float]] = []
    prev = None
    for r in rows:
        c = r.get("close")
        d = str(r.get("trade_date") or "")
        if c is None or not d:
            continue
        if prev is not None and prev > 0:
            out.append((d, float(c) / prev - 1))
        prev = float(c)
    return out


def review_portfolio(holdings: list[dict], *, stress: bool = False) -> dict[str, Any]:
    from .collector import collect_basic_info, collect_kline

    industries: dict[str, float] = {}
    kline_by_sym: dict[str, dict[str, float]] = {}
    skipped: list[str] = []
    active_symbols: list[str] = []
    weights_by_sym: dict[str, float] = {}

    for h in holdings:
        sym = str(h.get("symbol", "")).strip()
        weight = float(h.get("weight", 0))
        if not sym:
            continue
        weights_by_sym[sym] = weight
        basic = collect_basic_info(sym)
        data = basic.get("data") if isinstance(basic, dict) else {}
        industry = "未知"
        if isinstance(data, dict):
            industry = data.get("industry") or data.get("行业") or "未知"
        industries[industry] = industries.get(industry, 0) + weight

        kdim = collect_kline(sym, start_date=_days_ago(150))
        kdata = kdim.get("data") if isinstance(kdim, dict) else None
        if not isinstance(kdata, list) or len(kdata) < 60:
            skipped.append(sym)
            continue
        active_symbols.append(sym)
        for d, ret in _returns_from_kline(kdata)[-120:]:
            kline_by_sym.setdefault(sym, {})[d] = ret

    # Industry concentration
    ind_table = sorted(industries.items(), key=lambda x: -x[1])

    # Correlation matrix (inner join dates) — only active_symbols
    corr: dict[str, Any] = {"skipped": "持仓 < 3 只，跳过相关性"}
    if len(active_symbols) >= 3:
        common_dates = None
        for sym in active_symbols:
            dates = set(kline_by_sym.get(sym, {}).keys())
            common_dates = dates if common_dates is None else common_dates & dates
        if common_dates and len(common_dates) >= 20:
            aligned = sorted(common_dates)[-120:]
            series = {
                sym: [kline_by_sym[sym][d] for d in aligned if d in kline_by_sym.get(sym, {})]
                for sym in active_symbols
            }
            corr = {"matrix": _corr_matrix(series), "n_days": len(aligned)}
        else:
            corr = {"error": "交集交易日不足"}

    # Weight sum over holdings that contributed (have symbol)
    w_sum = sum(weights_by_sym.values())
    weight_warning = None
    if weights_by_sym and abs(w_sum - 1.0) > 0.05:
        weight_warning = f"权重和={w_sum:.3f}，偏离 1.0 超过 5%"

    stress_result = None
    if stress:
        # beta≈1: portfolio moves with index; scale by w_sum if not normalized
        scale = w_sum if abs(w_sum - 1.0) > 0.05 else 1.0
        note = (
            "指数情景下组合市值估算变动（假设组合 beta≈1），非调仓建议"
            + ("；权重未归一，已按权重和缩放" if abs(w_sum - 1.0) > 0.05 else "")
        )
        stress_result = {
            "-10%": round(-0.10 * scale, 4),
            "-20%": round(-0.20 * scale, 4),
            "-30%": round(-0.30 * scale, 4),
            "note": note,
        }

    out: dict[str, Any] = {
        "industry_concentration": ind_table,
        "correlation": corr,
        "skipped_symbols": skipped,
        "stress": stress_result,
        "disclaimer": "纯风险特征描述，不构成投资建议或调仓建议。",
    }
    if weight_warning:
        out["weight_warning"] = weight_warning
    return out


def _days_ago(n: int) -> str:
    from datetime import datetime, timedelta
    return (datetime.now() - timedelta(days=n)).strftime("%Y%m%d")


def _corr_matrix(series: dict[str, list[float]]) -> dict[str, dict[str, float | None]]:
    import statistics
    syms = list(series.keys())
    matrix: dict[str, dict[str, float | None]] = {}
    for a in syms:
        matrix[a] = {}
        for b in syms:
            sa, sb = series[a], series[b]
            n = min(len(sa), len(sb))
            if n < 12:
                matrix[a][b] = None
                continue
            ma, mb = statistics.mean(sa[:n]), statistics.mean(sb[:n])
            cov = sum((sa[i] - ma) * (sb[i] - mb) for i in range(n)) / (n - 1)
            va = sum((x - ma) ** 2 for x in sa[:n]) / (n - 1)
            vb = sum((x - mb) ** 2 for x in sb[:n]) / (n - 1)
            if va <= 0 or vb <= 0:
                matrix[a][b] = None
            else:
                matrix[a][b] = round(cov / (va ** 0.5 * vb ** 0.5), 3)
    return matrix


def format_portfolio_review(result: dict) -> str:
    lines = ["# 组合风险特征", ""]
    if result.get("weight_warning"):
        lines.append(f"⚠️ {result['weight_warning']}")
        lines.append("")
    lines.append("## 行业集中度（申万一级）")
    for ind, w in result.get("industry_concentration", []):
        lines.append(f"- {ind}: {w:.1%}")
    lines.append("")
    corr = result.get("correlation", {})
    if corr.get("matrix"):
        lines.append("## 持仓相关性矩阵（120日收益率）")
        for a, row in corr["matrix"].items():
            cells = ", ".join(f"{b}={v}" for b, v in row.items())
            lines.append(f"- {a}: {cells}")
    else:
        lines.append(f"## 相关性: {corr.get('skipped') or corr.get('error', '—')}")
    if result.get("skipped_symbols"):
        lines.append(f"\n数据不足跳过: {', '.join(result['skipped_symbols'])}")
    if result.get("stress"):
        lines.append("\n## 情景压力测试（指数下跌）")
        for k, v in result["stress"].items():
            if k != "note":
                lines.append(f"- 指数 {k}: 组合市值影响约 {v:.1%}")
        lines.append(f"\n*{result['stress'].get('note', '')}*")
    lines.append(f"\n*{result.get('disclaimer', '')}*")
    return "\n".join(lines)
