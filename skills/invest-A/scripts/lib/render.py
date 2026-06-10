"""报告渲染模块。从采集结果生成 compact/json/md 格式输出。"""

from __future__ import annotations

import json
from typing import Any


def _fmt(v: Any, unit: str = "") -> str:
    if v is None: return "-"
    if isinstance(v, float):
        if abs(v) >= 1e8: return f"{v/1e8:.2f}亿"
        if abs(v) >= 1e4: return f"{v/1e4:.2f}万"
        return f"{v:.2f}{unit}" if unit else f"{v:.2f}"
    return str(v)


def render_compact(collection: dict[str, Any], symbol: str) -> str:
    lines = [f"# {symbol} 采集报告",
             f"采集时间: {collection.get('fetched_at','')[:19]}",
             f"状态: {collection['summary']['available']}/{collection['summary']['total']} 维度可用", ""]
    for dim in collection.get("dimensions", []):
        dn, display = dim["dimension"], dim["display"]
        data, meta = dim.get("data"), dim.get("_meta", {})
        source = meta.get("source", "none")
        icon = "✅" if dim["status"] == "available" else "❌"
        lines.append(f"## {icon} {display}  [{source}]")
        if data is None:
            lines.append(f"数据不可得: {dim.get('error','')}\n")
            continue
        if dn == "basic_info" and isinstance(data, dict):
            for k, v in data.items(): lines.append(f"- {k}: {v}")
        elif dn == "financials" and isinstance(data, list):
            lines.append("| 期间 | ROE | EPS | 扣非净利润 |\n|------|-----|-----|-----------|")
            for r in data[:5]:
                lines.append(f"| {r.get('end_date','?')} | {_fmt(r.get('roe'),'%')} | {_fmt(r.get('eps'))} | {_fmt(r.get('profit_dedt'))} |")
        elif dn == "quote" and isinstance(data, dict):
            for k, v in data.items(): lines.append(f"- {k}: {v}")
        elif dn == "shareholders" and isinstance(data, list):
            lines.append("| 股东 | 持股比例 |\n|------|---------|")
            for r in data[:10]:
                lines.append(f"| {r.get('holder_name','?')} | {_fmt(r.get('hold_ratio'),'%')} |")
        elif dn == "northbound" and isinstance(data, list):
            lines.append("| 日期 | 净流向 |\n|------|-------|")
            for r in data[:7]:
                lines.append(f"| {r.get('trade_date','?')} | {_fmt(r.get('net_mf_vol'))} |")
        lines.append("")
    lines.append("---\n## 数据源清单\n")
    for dim in collection.get("dimensions", []):
        m = dim.get("_meta", {})
        icon = "✅" if dim["status"] == "available" else "❌"
        fb = " → ".join(str(s) for s in m.get("fallback_chain", [])) if m.get("fallback_chain") else ""
        lines.append(f"- {icon} {dim['display']}: {m.get('source','?')} {f'(降级: {fb})' if fb else ''}")
    return "\n".join(lines)


def render_json(collection: dict[str, Any]) -> str:
    return json.dumps(collection, ensure_ascii=False, indent=2, default=str)


def render(collection: dict[str, Any], symbol: str, fmt: str = "compact") -> str:
    if fmt == "json": return render_json(collection)
    return render_compact(collection, symbol)
