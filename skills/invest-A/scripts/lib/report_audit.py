"""Report audit: extract data points + verdict (v0.1.9).

Step 2 (Claude verification) is interactive — not implemented here.
"""

from __future__ import annotations

import json
import random
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Patterns for common financial data points in Chinese reports
_EXTRACT_PATTERNS: list[tuple[str, str, str]] = [
    (r"归母净利[润]?[：:\s]*([0-9,.]+)\s*亿", "归母净利", "亿元"),
    (r"营收[：:\s]*([0-9,.]+)\s*亿", "营收", "亿元"),
    (r"PE\(TTM\)[：:\s]*([0-9,.]+)x", "PE(TTM)", "x"),
    (r"PE\(TTM\)[：:\s]*([0-9,.]+)", "PE(TTM)", "x"),
    (r"PB[：:\s]*([0-9,.]+)x", "PB", "x"),
    (r"毛利率[：:\s]*([0-9,.]+)%", "毛利率", "%"),
    (r"净利率[：:\s]*([0-9,.]+)%", "净利率", "%"),
    (r"ROE[：:\s]*([0-9,.]+)%", "ROE", "%"),
    (r"总市值[：:\s]*([0-9,.]+)\s*亿", "总市值", "亿元"),
]


def _parse_value(s: str) -> float:
    return float(s.replace(",", ""))


def extract_points(report_text: str, *, sample_rate: float = 0.15) -> dict[str, Any]:
    """Extract numeric points from markdown report; sample 15%."""
    points: list[dict[str, Any]] = []
    seen: set[str] = set()
    lines = report_text.splitlines()
    context = ""

    for line in lines:
        if line.startswith("#"):
            context = line.strip()
        for pattern, label, unit in _EXTRACT_PATTERNS:
            for m in re.finditer(pattern, line):
                key = f"{label}:{m.group(1)}"
                if key in seen:
                    continue
                seen.add(key)
                try:
                    val = _parse_value(m.group(1))
                except ValueError:
                    continue
                points.append({
                    "label": label,
                    "reported_value": val,
                    "reported_unit": unit,
                    "context": context or line[:60],
                })

    total = len(points)
    n_sample = max(1, int(total * sample_rate)) if total else 0
    rng = random.Random(42)
    sampled = rng.sample(points, min(n_sample, total)) if total else []

    checks = []
    for i, p in enumerate(sampled, 1):
        checks.append({
            "id": i,
            "label": p["label"],
            "reported_value": p["reported_value"],
            "reported_unit": p["reported_unit"],
            "context": p["context"],
            "fetched_value": None,
            "fetched_source": None,
            "deviation_pct": None,
        })

    return {
        "total_points": total,
        "sampled_points": len(checks),
        "checks": checks,
    }


def extract_report(report_path: Path) -> dict[str, Any]:
    """Run --extract: write audit_checklist.json alongside report."""
    text = report_path.read_text(encoding="utf-8")
    data = extract_points(text)
    data["report"] = str(report_path)
    data["extracted_at"] = datetime.now(timezone.utc).isoformat()

    out_path = report_path.with_suffix(".audit_checklist.json")
    out_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return {"output": str(out_path), **data}


def _deviation_pct(reported: float, fetched: float) -> float:
    avg = (abs(reported) + abs(fetched)) / 2.0
    if avg < 1e-12:
        return 0.0 if abs(reported - fetched) < 1e-12 else 100.0
    return abs(reported - fetched) / avg * 100.0


def verdict_report(report_path: Path) -> dict[str, Any]:
    """Run --verdict: read audit_checklist.json and compute PASS/FAIL/REVISIONS_NEEDED."""
    checklist_path = report_path.with_suffix(".audit_checklist.json")
    if not checklist_path.exists():
        return {"verdict": "FAIL", "error": f"未找到 {checklist_path}"}

    data = json.loads(checklist_path.read_text(encoding="utf-8"))
    checks = data.get("checks") or []
    verified = 0
    failed = 0
    pending = 0

    for c in checks:
        rv = c.get("reported_value")
        fv = c.get("fetched_value")
        if fv is None or rv is None:
            pending += 1
            continue
        try:
            rv_f = float(rv)
            fv_f = float(fv)
        except (TypeError, ValueError):
            pending += 1
            continue
        dev = _deviation_pct(rv_f, fv_f)
        c["deviation_pct"] = round(dev, 2)
        verified += 1
        if dev > 5.0:
            failed += 1

    checklist_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    if pending > 0:
        verdict = "REVISIONS_NEEDED"
    elif failed > 0:
        verdict = "FAIL"
    else:
        verdict = "PASS"

    return {
        "verdict": verdict,
        "verified": verified,
        "failed": failed,
        "pending": pending,
        "total_checks": len(checks),
        "checklist": str(checklist_path),
    }
