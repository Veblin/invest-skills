"""Output formatting for gap scan results.

Three output modes:
- ``format_brief(result, top_n=30)`` — stdout brief (pipe table, summary stats)
- ``format_markdown_report(result, output_path)`` — detailed markdown report
- ``format_json(result)`` — JSON serialization

Import conventions follow the same pattern as ``gap_scanner.py`` (``_LIB_DIR``
on ``sys.path``, top-level imports for sibling modules).
"""

from __future__ import annotations

import json
import logging
import math
import re
from collections import Counter
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from gap_scanner import ScanResult, ScanHit

logger = logging.getLogger(__name__)

# ── Version (canonical source: pyproject.toml, synced via sync_version.py) ──
_VERSION = "0.0.0"
_toml_path = Path(__file__).resolve().parents[4] / "pyproject.toml"
if _toml_path.exists():
    for _line in _toml_path.read_text(encoding="utf-8").splitlines():
        _m = re.match(r'^version\s*=\s*["\']([^"\']+)["\']', _line.strip())
        if _m:
            _VERSION = _m.group(1)
            break


# ======================================================================
# Index name/size lookup (for summary line)
# ======================================================================

_INDEX_META: dict[str, tuple[str, int]] = {
    "csi300": ("沪深300", 300),
    "a500": ("中证A500", 500),
    "star50": ("科创50", 50),
}


def _parse_universe_indices(params: dict) -> list[tuple[str, int]]:
    """Parse the universe string from params into index name + size tuples.

    Falls back to the default three indices if ``universe_str`` is missing
    from *params*.
    """
    raw = params.get("universe_str", "csi300,a500,star50")
    parts = raw.split(",")
    result: list[tuple[str, int]] = []
    for p in parts:
        p = p.strip()
        meta = _INDEX_META.get(p)
        if meta is not None:
            result.append(meta)
        else:
            result.append((p.upper(), 0))
    return result


def _index_members_label(hit: ScanHit) -> str:
    """Short comma-separated index label, e.g. ``"300+500+50"``."""
    members = getattr(hit, "index_members", [])
    label_parts: list[str] = []
    for key, (_name, size) in _INDEX_META.items():
        if key in members:
            label_parts.append(str(size))
    return "+".join(label_parts) if label_parts else ", ".join(members[:3])


def _fmt_amount(val: float) -> str:
    """Format amount in yuan to human-readable string (亿 or 万)."""
    if abs(val) >= 1e8:
        return f"{val / 1e8:.2f}亿"
    if abs(val) >= 1e4:
        return f"{val / 1e4:.0f}万"
    return f"{val:.0f}"


def _fmt_pct(val: float) -> str:
    """Format percentage with sign.  NaN → "N/A"."""
    if math.isnan(val):
        return "N/A"
    if val >= 0:
        return f"+{val:.2f}%"
    return f"{val:.2f}%"


def _fmt_price(val: float) -> str:
    """Format price with appropriate decimal places.  NaN → "N/A"."""
    if math.isnan(val):
        return "N/A"
    if val >= 1000:
        return f"{val:.1f}"
    if val >= 100:
        return f"{val:.2f}"
    return f"{val:.3f}"


# ======================================================================
# Brief (stdout)
# ======================================================================


def format_brief(result: ScanResult, top_n: int = 30) -> str:
    """Format stdout brief summary with hit table.

    Parameters
    ----------
    result : ScanResult
        The scan result to format.
    top_n : int
        Maximum number of hits to include in the stdout table.
        (Default 30; the markdown report always shows all hits.)
    """
    lines: list[str] = []
    p = result.params

    # --- Header ---
    lines.append("=" * 72)
    lines.append(f"  invest-a-gap-scan v{_VERSION} -- 跳空缺口扫描")
    lines.append("=" * 72)

    # --- Universe summary ---
    indices = _parse_universe_indices(p)
    idx_parts = [f"{name}({size})" for name, size in indices if size > 0]
    idx_str = " + ".join(idx_parts)
    lines.append(
        f"池构成: {idx_str} -> 去重 {result.total_in_universe} 只"
    )

    # --- Data source ---
    source_label = p.get("source_label", "Tushare Pro")
    lines.append(f"数据源: {source_label} (前复权)")

    # --- Coverage (usable K-line / universe) ---
    with_kline = getattr(result, "total_with_kline", result.total_scanned)
    coverage = with_kline / max(result.total_in_universe, 1) * 100.0
    coverage_flag = " ⚠️" if coverage < 90 else ""
    lines.append(
        f"覆盖率: {coverage:.1f}% ({with_kline}/"
        f"{result.total_in_universe} 有K线){coverage_flag}"
        f" | 命中: {len(result.hits)} 只"
        f" | 跨停牌: {len(result.across_suspension_hits)} 只"
    )

    # --- Parameters ---
    param_parts: list[str] = [
        f"缺口≥{p.get('gap_min_pct', 1.0)}%",
        f"回溯{p.get('gap_lookback', 60)}日",
        "MA60",
        f"日均额≥{_fmt_amount(p.get('min_avg_amount', 100_000_000))}",
    ]
    vr = p.get("gap_min_vol_ratio", 1.0)
    if vr > 1.0:
        param_parts.append(f"量比≥{vr}")
    lines.append("参数: " + " | ".join(param_parts))

    # --- Exclude / non-hit breakdown ---
    lines.append("")
    exclude_str = _counter_breakdown(result.exclude_reasons, _EXCLUDE_LABELS)
    non_hit_str = _counter_breakdown(result.non_hit_reasons, _NON_HIT_LABELS)
    if exclude_str:
        lines.append(f"排除: {exclude_str}")
    if non_hit_str:
        lines.append(f"未命中: {non_hit_str}")

    # --- Hit table (regular) ---
    if result.hits:
        lines.append("")
        lines.extend(_build_hit_table(result.hits, limit=top_n))
    else:
        lines.append("")
        lines.append("> 无命中标的。")

    # --- Cross-suspension table ---
    if result.across_suspension_hits:
        lines.append("")
        lines.append("(跨停牌缺口 -- 单独列出，不参与默认排序)")
        lines.extend(_build_hit_table(result.across_suspension_hits, limit=None))

    # --- Footer ---
    lines.append("")
    lines.append(
        "数据来源: Tushare Pro / baostock (前复权)。仅供研究，不构成投资建议。"
    )
    lines.append("")

    return "\n".join(lines)


# ======================================================================
# Table builder
# ======================================================================


def _build_hit_table(hits: list[ScanHit], limit: int | None = 30) -> list[str]:
    """Build a pipe-formatted hit table.

    Columns: 代码 | 名称 | 指数 | 板块 | 缺口日 | 缺口% | 缺口区间
             | 现价 | MA60 | MA60% | 距上沿% | 量比 | 20日均额
    """
    display = hits[:limit] if limit is not None else hits
    if not display:
        return []

    # Column headers (13 columns)
    headers = [
        "代码", "名称", "指数", "板块",
        "缺口日", "缺口%", "缺口区间",
        "现价", "MA60", "MA60%",
        "距上沿%", "量比", "20日均额",
    ]
    col_count = len(headers)

    sep = "|" + "|".join("---" for _ in range(col_count)) + "|"

    lines: list[str] = []
    lines.append("| " + " | ".join(headers) + " |")
    lines.append(sep)

    for h in display:
        gap_low_str = _fmt_price(h.gap.gap_low)
        gap_high_str = _fmt_price(h.gap.gap_high)
        gap_zone = f"{gap_low_str}~{gap_high_str}"

        index_label = _index_members_label(h)
        row = [
            h.ts_code,
            h.name,
            index_label,
            h.board,
            h.gap.gap_date,
            f"+{h.gap.gap_pct:.2f}%",
            gap_zone,
            _fmt_price(h.current_price),
            _fmt_price(h.ma60),
            _fmt_pct(h.pct_from_ma60),
            _fmt_pct(h.pct_from_gap_high),
            f"{h.vol_ratio:.2f}",
            _fmt_amount(h.avg_amount_20d),
        ]
        lines.append("| " + " | ".join(row) + " |")

    return lines


# ======================================================================
# Counter breakdown labels
# ======================================================================

_EXCLUDE_LABELS: dict[str, str] = {
    "st_stock": "ST",
    "delist": "退市",
    "insufficient_kline": "上市不足",
    "missing_adj_factor": "数据缺失",
    "fetch_error": "获取失败",
    "low_liquidity": "低流动性",
}

_NON_HIT_LABELS: dict[str, str] = {
    "no_gap": "无缺口",
    "below_threshold": "低于阈值",
    "ma60_broken": "MA60破",
    "gap_filled": "缺口回补",
    "vol_ratio_low": "量比低",
}


def _counter_breakdown(counter: Counter, labels: dict[str, str]) -> str:
    """Format a counter into a human-readable breakdown string.

    Only entries with count > 0 are included.
    """
    parts: list[str] = []
    for key in labels:
        count = counter.get(key, 0)
        if count > 0:
            parts.append(f"{labels[key]} {count}")
    return " | ".join(parts)


# ======================================================================
# Markdown report
# ======================================================================


def format_markdown_report(result: ScanResult, output_path: str) -> str:
    """Generate a detailed markdown report and save to *output_path*.

    Returns the path as a string.
    """
    today = datetime.now().strftime("%Y-%m-%d")
    lines: list[str] = []

    # --- Title ---
    lines.append(f"# 跳空缺口扫描报告")
    lines.append(f"")
    lines.append(f"**日期:** {today}")
    lines.append("")

    # --- Summary ---
    lines.append("## 扫描摘要")
    lines.append("")
    with_kline = getattr(result, "total_with_kline", result.total_scanned)
    coverage = with_kline / max(result.total_in_universe, 1) * 100.0
    lines.append(f"- 池大小: {result.total_in_universe} 只")
    lines.append(f"- 有可用K线: {with_kline} 只 ({coverage:.1f}%)")
    lines.append(f"- 进入缺口判定: {result.total_scanned} 只")
    lines.append(f"- 获取失败: {result.total_fetch_errors} 只")
    lines.append(f"- **命中: {len(result.hits)} 只**")
    if result.across_suspension_hits:
        lines.append(f"- 跨停牌缺口: {len(result.across_suspension_hits)} 只")
    lines.append("")

    p = result.params
    lines.append(f"**参数:**")
    lines.append(f"- 缺口幅度阈值: ≥{p.get('gap_min_pct', 1.0)}%")
    lines.append(f"- 回溯窗口: {p.get('gap_lookback', 60)} 个交易日")
    lines.append(f"- 均线: MA60 (自缺口日起收盘始终在 MA60 上方)")
    lines.append(f"- 日均额门槛: {_fmt_amount(p.get('min_avg_amount', 100_000_000))}")
    vr = p.get("gap_min_vol_ratio", 1.0)
    if vr > 1.0:
        lines.append(f"- 缺口日量比门槛: ≥{vr}")
    lines.append("")

    # --- Exclude / non-hit breakdown ---
    lines.append("### 排除统计")
    lines.append("")
    exclude_str = _counter_breakdown(result.exclude_reasons, _EXCLUDE_LABELS)
    lines.append(exclude_str if exclude_str else "（无）")
    lines.append("")

    lines.append("### 未命中统计")
    lines.append("")
    non_hit_str = _counter_breakdown(result.non_hit_reasons, _NON_HIT_LABELS)
    lines.append(non_hit_str if non_hit_str else "（无）")
    lines.append("")

    # --- Full hit table ---
    lines.append("## 命中列表")
    lines.append("")
    lines.extend(_build_hit_table(result.hits, limit=None))
    lines.append("")

    # --- Cross-suspension hits ---
    if result.across_suspension_hits:
        lines.append("## 跨停牌缺口")
        lines.append("")
        lines.append(
            "以下标的的缺口形成时跨越了停牌期，仅作参考，不参与常规排序。"
        )
        lines.append("")
        lines.extend(_build_hit_table(result.across_suspension_hits, limit=None))
        lines.append("")

    # --- Per-stock brief analysis ---
    lines.append("## 逐股简析")
    lines.append("")
    all_hits = result.hits + result.across_suspension_hits
    if not all_hits:
        lines.append("> 无命中标的，无逐股分析。")
        lines.append("")
    else:
        for h in all_hits:
            label = " [跨停牌]" if h.gap.is_across_suspension else ""
            lines.append(f"### {h.ts_code} {h.name}{label}")
            lines.append("")
            lines.append(f"- **缺口日:** {h.gap.gap_date}")
            lines.append(
                f"- **缺口幅度:** +{h.gap.gap_pct:.2f}%"
                f" (区间 {_fmt_price(h.gap.gap_low)} ~ {_fmt_price(h.gap.gap_high)})"
            )
            lines.append(
                f"- **最新收盘价:** {_fmt_price(h.current_price)}"
                f" (MA60={_fmt_price(h.ma60)}, "
                f"偏离 {_fmt_pct(h.pct_from_ma60)})"
            )
            lines.append(
                f"- **距缺口上沿:** {_fmt_pct(h.pct_from_gap_high)}"
            )
            lines.append(
                f"- **缺口日量比:** {h.vol_ratio:.2f}"
                f" (20日均额 {_fmt_amount(h.avg_amount_20d)})"
            )
            lines.append("")
            lines.append("> [催化待查]")
            lines.append("")

    # --- Footer ---
    lines.append("---")
    lines.append("")
    lines.append(
        "*数据来源: Tushare Pro / baostock (前复权)。"
        "仅供研究，不构成投资建议。*"
    )
    lines.append("")

    content = "\n".join(lines)

    # Write to file
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(content, encoding="utf-8")

    logger.info("报告已保存: %s", out.resolve())
    return str(out)


# ======================================================================
# JSON output
# ======================================================================


def _dataclass_to_dict(obj: Any) -> Any:
    """Recursively convert dataclass instances to dicts.

    Handles :class:`Counter`, :class:`ScanHit`, :class:`GapInfo`, and
    nested lists/dicts.  Avoids :func:`dataclasses.asdict` because it
    corrupts :class:`Counter` fields (treats them as iterables of tuples).
    """
    if isinstance(obj, Counter):
        # Convert Enum keys to their string values for JSON compatibility
        return {k.value if hasattr(k, "value") else str(k): v for k, v in obj.items()}
    if hasattr(obj, "__dataclass_fields__"):
        return {f: _dataclass_to_dict(getattr(obj, f)) for f in obj.__dataclass_fields__}
    if isinstance(obj, dict):
        return {k: _dataclass_to_dict(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_dataclass_to_dict(v) for v in obj]
    return obj


def format_json(result: ScanResult) -> str:
    """Format *result* as a JSON string.

    Uses a custom serialization that handles dataclasses, Counters, and
    basic Python types.
    """
    data = _dataclass_to_dict(result)
    return json.dumps(data, ensure_ascii=False, indent=2, default=str)
