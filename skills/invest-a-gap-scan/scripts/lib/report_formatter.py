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
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from gap_scanner import ScanResult, ScanHit
from lib.nums import fmt_amount

logger = logging.getLogger(__name__)

# ── Version (canonical source: pyproject.toml [project].version) ──


def _read_project_version() -> str:
    """Walk up to pyproject.toml and read ``[project].version`` only."""
    try:
        from version import get_package_version  # skills/lib（bootstrap 后可达）
    except ModuleNotFoundError:
        return "0.0.0"  # 无 skills/lib 环境时保持旧实现"永不失败"不变量
    return get_package_version(default="0.0.0", stop_at_first=True)


_VERSION = _read_project_version()


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
    """Format amount in yuan to human-readable string (亿 or 万).

    Delegates to shared ``lib.nums.fmt_amount``; 亿 keeps 2 decimals,
    below 亿 uses integer precision (compact display) — matching the
    historical output.
    """
    if abs(val) >= 1e8:
        return fmt_amount(val)
    return fmt_amount(val, precision=0)


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


def _fmt_amount_safe(val: Any) -> str:
    """金额格式化：NaN/None/非有限值 → "N/A"（不打印假数字）。"""
    if val is None:
        return "N/A"
    try:
        f = float(val)
    except (TypeError, ValueError):
        return "N/A"
    if not math.isfinite(f):
        return "N/A"
    return _fmt_amount(f)


# ── 风险声明（首尾各一条，须完整可见；AGENTS.md 检查项「首部/尾部有风险声明」）──

RISK_STATEMENT: tuple[str, ...] = (
    "**风险声明（非投资建议）**：本报告由规则化扫描自动生成，列出的是「向上跳空缺口未回补」的"
    "**观察清单** —— 不是交易信号、不是预测、不构成证券买卖/持有/仓位建议，也未对命中标的做"
    "基本面或事件核验。",
    "缺口形态可能在任何时点被回补；形态检出为历史数据描述，不预示未来表现。"
    "数据可能存在延迟、缺失或错误（见「数据与来源」），请以原始行情与公告自行核验，并独立决策。",
)


def _risk_lines(prefix: str = "> ") -> list[str]:
    return [prefix + line for line in RISK_STATEMENT]


def _vol_threshold_active(vr: Any) -> bool:
    """量比门槛是否生效——与引擎判据同构：**任何非 1.0 值都生效**。

    旧渲染层写 `vr > 1.0`，于是 `--gap-min-vol-ratio 0.5` 时过滤生效但报告
    不显示该参数 → 报告无法按所载参数复现。另对 NaN/inf/≤0 加有限性门槛：
    这类值不构成有效门槛，打印它等于展示一个从未生效的参数。
    """
    try:
        f = float(vr)
    except (TypeError, ValueError):
        return False
    if not math.isfinite(f) or f <= 0:
        return False
    return not math.isclose(f, 1.0, abs_tol=1e-9)


def _data_as_of_label(result: ScanResult) -> str:
    """「数据截止」标签：**全池**最新 bar 日期 + 它是否含今日。

    这是全池口径，不是逐股口径：命中表另有「数据日」列给出各标的自身 bar
    日期（停牌或数据落后的标的更早），「最新收盘」取的是各自数据日的收盘价。
    报告生成日与数据截止日是两件事：缓存（TTL 3 天）或盘前运行时，数据可能
    是数日前收盘，不得读作「当前价」。
    """
    d = str(getattr(result, "data_as_of", "") or "")
    if len(d) != 8 or not d.isdigit():
        return "未知（引擎未取到 bar 日期）"
    today = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d")
    scope = "全池最新 bar"
    if d == today:
        return f"{d}（{scope}，含今日）"
    return f"{d}（{scope}，不含今日；逐股数据日见「数据日」列）"


# ── 数据与来源（V31-40/42：数据截止、缓存状态、实际源 vs 尝试源、字段级来源）──


def _data_source_rows(result: ScanResult) -> list[tuple[str, str]]:
    """「数据与来源」表行：实际使用源 / 尝试源 / 缓存 / 日历 / 池规模 / 覆盖率。"""
    p = result.params
    rows: list[tuple[str, str]] = []

    source_label = p.get("source_label", "未知")
    rows.append(("实际使用源", source_label))

    attempted = getattr(result, "attempted_sources", None) or []
    if attempted:
        # 降级原因由采集层如实给出（source_selection_note），渲染层不臆测
        note = str(p.get("source_note", "") or "")
        rows.append(("尝试过的源", " → ".join(attempted) + (f"（{note}）" if note else "")))

    cache = getattr(result, "cache_status", None)
    if cache:
        # 「新拉/拉取失败」两个分支共用同一口径：**实际**成功数，不是计划数
        fetched = cache.get("fetched", 0)
        failed = cache.get("fetch_failed", 0)
        fail_str = (
            f" / 拉取失败 {failed}（计入「获取失败/数据缺失」排除桶）" if failed else ""
        )
        if not cache.get("enabled", True):
            # --no-cache 也是「先计划后执行」：全部重拉是计划，成功/失败须分别列出
            rows.append((
                "K 线缓存",
                f"已禁用（--no-cache）：计划全部重拉 {cache.get('planned_fetch', 0)} 只"
                f" / 新拉 {fetched}{fail_str}",
            ))
        else:
            age_min = cache.get("min_age_hours")
            age_max = cache.get("max_age_hours")
            age_str = (
                f"；缓存条目年龄 {age_min:.1f}~{age_max:.1f} 小时"
                if age_min is not None and age_max is not None
                else ""
            )
            unsettled = cache.get("refreshed_unsettled", 0)
            unsettled_str = (
                f"；其中 {unsettled} 只为「缓存末根 bar 未定稿（写入早于当日收盘）」而重拉"
                if unsettled else ""
            )
            rows.append((
                "K 线缓存",
                f"缓存命中 {cache.get('hit', 0)} / 新拉 {fetched}{fail_str}"
                f"；TTL {cache.get('ttl_days', 0):.1f} 天{age_str}{unsettled_str}",
            ))

    cal = "自然日估算（Mon-Fri，**停牌检测已跳过**）" if p.get("cal_estimated") else \
        "Tushare trade_cal（真实日历，停牌检测已启用）"
    rows.append(("交易日历", cal))

    with_kline = getattr(result, "total_with_kline", result.total_scanned)
    coverage = with_kline / max(result.total_in_universe, 1) * 100.0
    rows.append(("池规模 / 覆盖率", f"{result.total_in_universe} 只（随指数调仓变动，非固定值）"
                                    f" / {coverage:.1f}%（{with_kline} 只有 K 线）"))
    return rows


def _universe_source_text(result: ScanResult) -> str:
    """成分股池的实际来源（三级降级链用到哪一级）。

    ``build_universe`` 逐指数按 akshare `index_stock_cons` → akshare
    `index_stock_cons_sina` → Tushare `index_weight` 降级；**复用当日缓存时
    来源只能来自 sidecar，缺失即「未记录」**——不得回落成首选源的名字，
    否则读者无法追溯、也无法复现报告里的池。
    """
    prov = result.params.get("universe_provenance") or {}
    per_index = prov.get("per_index") or {}
    if not per_index:
        return ("**未记录**：本次复用成分股缓存，其来源未随缓存保存"
                "（该缓存由更早版本或未写 sidecar 的运行产生），无法追溯实际来源")
    by_source: dict[str, list[str]] = {}
    failed: list[str] = []
    for key, src in per_index.items():
        display = _INDEX_META.get(key, (key, 0))[0]
        if src:
            by_source.setdefault(str(src), []).append(display)
        else:
            failed.append(display)
    parts = [f"{src}（{' + '.join(names)}）" for src, names in sorted(by_source.items())]
    if failed:
        parts.append(f"全部源失败未纳入池（{' + '.join(failed)}）")
    text = "；".join(parts) if parts else "**未记录**（sidecar 内无有效来源）"
    if prov.get("from_cache"):
        text += "；本次复用当日成分股缓存（来源取自 sidecar）"
    return text


def _field_source_rows(result: ScanResult) -> list[tuple[str, str]]:
    """字段级来源：每个数字来自哪个源/哪一步计算。"""
    p = result.params
    name = p.get("source_name", "")
    if name == "tushare":
        kline_src = "Tushare Pro（按交易日批量拉取 daily）"
        qfq_src = "原始价 × adj_factor / 最新 adj_factor（本地自算前复权）"
    elif name == "baostock":
        kline_src = "baostock（逐股查询）"
        qfq_src = "baostock adjustflag=2（源端前复权）"
    else:
        kline_src = p.get("source_label", "未知")
        qfq_src = "见实际使用源"

    return [
        ("成分股池", _universe_source_text(result)),
        ("日线 OHLC / 成交额", kline_src),
        ("前复权价", qfq_src),
        ("交易日历", "Tushare trade_cal；失败时自然日估算（此时跳过停牌检测）"),
        ("停牌日期", "交易日历与该股日线比对（日历为估算时不产出，排除统计相应减少）"),
        ("缺口区间 / MA60 / 量比 / 距上沿%", "本地 Python 计算（`gap_scanner.py`），无外部来源"),
    ]


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

    # --- 风险声明（首部完整可见）---
    lines.append("")
    lines.extend(_risk_lines(prefix="  "))
    lines.append("")

    # --- Universe summary ---
    indices = _parse_universe_indices(p)
    idx_parts = [f"{name}({size})" for name, size in indices if size > 0]
    idx_str = " + ".join(idx_parts)
    lines.append(
        f"池构成: {idx_str} -> 去重 {result.total_in_universe} 只（随指数调仓变动）"
    )

    # --- Data as-of / source / cache（数据时点不得读作「当前」）---
    lines.append(f"数据截止: {_data_as_of_label(result)}")
    source_label = p.get("source_label", "未知")
    attempted = getattr(result, "attempted_sources", None) or []
    attempt_str = f" | 尝试源: {' → '.join(attempted)}" if attempted else ""
    lines.append(f"数据源: {source_label}{attempt_str}")
    cache = getattr(result, "cache_status", None)
    if cache and cache.get("enabled", True):
        age_min, age_max = cache.get("min_age_hours"), cache.get("max_age_hours")
        age_str = (f"，缓存条目年龄 {age_min:.1f}~{age_max:.1f}h"
                   if age_min is not None and age_max is not None else "")
        fetch_str = f"新拉 {cache.get('fetched', 0)}"
        if cache.get("fetch_failed", 0):
            fetch_str += f" / 拉取失败 {cache['fetch_failed']}"
        if cache.get("refreshed_unsettled", 0):
            fetch_str += f"（含未定稿重拉 {cache['refreshed_unsettled']}）"
        lines.append(
            f"K线缓存: 缓存命中 {cache.get('hit', 0)} / {fetch_str}"
            f"（TTL {cache.get('ttl_days', 0):.1f} 天{age_str}）"
        )
    elif cache:
        # 与详文档同口径：--no-cache 下「计划重拉」与「实际新拉/失败」分别列出
        fetch_str = f"新拉 {cache.get('fetched', 0)}"
        if cache.get("fetch_failed", 0):
            fetch_str += f" / 拉取失败 {cache['fetch_failed']}"
        lines.append(
            f"K线缓存: 已禁用（--no-cache）| 计划重拉 {cache.get('planned_fetch', 0)}"
            f" / {fetch_str}"
        )

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
        "MA60（含容差与有效值门槛）",
        f"日均额≥{_fmt_amount(p.get('min_avg_amount', 100_000_000))}（当前20日均额）",
    ]
    vr = p.get("gap_min_vol_ratio", 1.0)
    if _vol_threshold_active(vr):
        param_parts.append(f"量比≥{vr}")
    lines.append("参数: " + " | ".join(param_parts))

    # --- Exclude / non-hit breakdown ---
    lines.append("")
    exclude_str = _counter_breakdown(result.exclude_reasons, _EXCLUDE_LABELS)
    non_hit_str = _counter_breakdown(result.non_hit_reasons, _NON_HIT_LABELS)
    if exclude_str:
        lines.append(f"排除（扫描前）: {exclude_str}")
    if non_hit_str:
        lines.append(f"未命中（扫描后）: {non_hit_str}")

    # --- Hit table (regular) ---
    if result.hits:
        lines.append("")
        lines.append("命中（观察清单，非交易信号、非预测、非推荐排序）:")
        lines.extend(_build_hit_table(result.hits, limit=top_n))
    else:
        lines.append("")
        lines.append("> 无命中标的。")

    # --- Cross-suspension table ---
    if result.across_suspension_hits:
        lines.append("")
        lines.append("(跨停牌缺口 -- 单独列出，不参与默认排序)")
        lines.extend(_build_hit_table(result.across_suspension_hits, limit=None))

    # --- Footer（尾部风险声明，与首部对称）---
    lines.append("")
    lines.extend(_risk_lines(prefix="  "))
    lines.append("")

    return "\n".join(lines)


# ======================================================================
# Table builder
# ======================================================================


def _build_hit_table(hits: list[ScanHit], limit: int | None = 30) -> list[str]:
    """Build a pipe-formatted hit table.

    Columns: 代码 | 名称 | 指数 | 板块 | 缺口日 | 缺口% | 缺口区间 | 数据日
             | 最新收盘 | MA60 | MA60% | 距上沿% | 量比 | 量比分母 | 当前20日均额

    「数据日」= 该股 K 线最后一根 bar 的交易日：逐股携带，**可能早于全池数据截止**
    （停牌、数据落后、部分缓存命中），故必须逐行给出——否则读者会以为全表价格
    取自同一时点。「最新收盘」即该数据日的收盘价，不是实时价。
    「量比分母」= 缺口日前**至多 20 根** bar 的日均成交额（`min(20, gap_idx)`），
    与「当前20日均额」是**两个不同窗口**——只有给出分母才能复算
    `量比 = 缺口日成交额 / 量比分母`。
    """
    display = hits[:limit] if limit is not None else hits
    if not display:
        return []

    # Column headers (15 columns)
    headers = [
        "代码", "名称", "指数", "板块",
        "缺口日", "缺口%", "缺口区间", "数据日",
        "最新收盘", "MA60", "MA60%",
        "距上沿%", "量比", "量比分母", "当前20日均额",
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
            str(getattr(h, "data_date", "") or "未知"),
            _fmt_price(h.current_price),
            _fmt_price(h.ma60),
            _fmt_pct(h.pct_from_ma60),
            _fmt_pct(h.pct_from_gap_high),
            f"{h.vol_ratio:.2f}",
            _fmt_amount_safe(getattr(h, "vol_ratio_denom", None)),
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
    "gap_unconfirmed": "最新bar待收盘确认",
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

    报告必须自证数据时点：首部风险声明 + 「数据与来源」（实际源/尝试源/缓存
    状态/数据截止）+ 字段级来源；逐股简析给出该股数据日与 MA60 判定覆盖度；
    事件/催化只留**未采集**槽位，不由引擎编造。
    """
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    lines: list[str] = []

    # --- Title ---
    lines.append("# 跳空缺口扫描报告")
    lines.append("")
    lines.extend(_risk_lines())
    lines.append("")
    lines.append(f"**报告生成:** {now.strftime('%Y-%m-%d %H:%M')} (Asia/Shanghai)")
    lines.append(f"**数据截止:** {_data_as_of_label(result)}")
    lines.append("")

    # --- 数据与来源 ---
    lines.append("## 数据与来源")
    lines.append("")
    lines.append("| 项 | 值 |")
    lines.append("|---|---|")
    for k, v in _data_source_rows(result):
        lines.append(f"| {k} | {v} |")
    lines.append("")
    lines.append("**字段级来源**")
    lines.append("")
    lines.append("| 字段 | 来源 |")
    lines.append("|---|---|")
    for k, v in _field_source_rows(result):
        lines.append(f"| {k} | {v} |")
    lines.append("")

    # --- Summary ---
    lines.append("## 扫描摘要")
    lines.append("")
    with_kline = getattr(result, "total_with_kline", result.total_scanned)
    coverage = with_kline / max(result.total_in_universe, 1) * 100.0
    lines.append(f"- 池大小: {result.total_in_universe} 只（随指数调仓变动，非固定值）")
    lines.append(f"- 有可用K线: {with_kline} 只 ({coverage:.1f}%)")
    lines.append(f"- 进入缺口判定: {result.total_scanned} 只")
    lines.append(f"- 获取失败: {result.total_fetch_errors} 只")
    lines.append(f"- **命中: {len(result.hits)} 只**（观察清单，非交易信号）")
    if result.across_suspension_hits:
        lines.append(f"- 跨停牌缺口: {len(result.across_suspension_hits)} 只")
    lines.append("")

    p = result.params
    lines.append(f"**参数:**")
    lines.append(f"- 缺口幅度阈值: ≥{p.get('gap_min_pct', 1.0)}%")
    lines.append(f"- 回溯窗口: {p.get('gap_lookback', 60)} 个交易日")
    lines.append(
        "- MA60 判据: 在**可计算 MA60 的 bar** 上收盘未跌破（含 epsilon 容差，"
        "有效值门槛 ≥25% 且 ≥3 根 bar 才判定；短历史标的极易被归入 MA60破）"
    )
    lines.append(
        f"- 日均额门槛: {_fmt_amount(p.get('min_avg_amount', 100_000_000))}"
        "（**当前尾部** 20 个交易日平均，与量比分母窗口不同）"
    )
    vr = p.get("gap_min_vol_ratio", 1.0)
    if _vol_threshold_active(vr):
        lines.append(
            f"- 缺口日量比门槛: ≥{vr}"
            "（分母 = 缺口日前至多 20 根 bar 日均额；任何非 1.0 值均生效）"
        )
    lines.append("")

    # --- Exclude / non-hit breakdown ---
    lines.append("### 排除统计（扫描前）")
    lines.append("")
    exclude_str = _counter_breakdown(result.exclude_reasons, _EXCLUDE_LABELS)
    lines.append(exclude_str if exclude_str else "（无）")
    lines.append("")

    lines.append("### 未命中统计（扫描后）")
    lines.append("")
    non_hit_str = _counter_breakdown(result.non_hit_reasons, _NON_HIT_LABELS)
    lines.append(non_hit_str if non_hit_str else "（无）")
    lines.append("")
    lines.append(
        "> 桶归属按优先级判定：无缺口 / 低于阈值 / MA60破 / **最新bar待收盘确认**"
        "（终结桶，仅当没有任何已确认未回补的缺口时出现）/ 缺口回补 / 量比低。"
        "零值桶不显示，可由「排除 + 未命中 + 命中 + 跨停牌 = 池规模」推得。"
    )
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
            lines.append(
                f"- **数据日:** {getattr(h, 'data_date', '') or '未知'}"
                "（该股 K 线最后一根 bar；停牌股可能早于全池数据截止）"
            )
            lines.append(f"- **缺口日:** {h.gap.gap_date}")
            lines.append(
                f"- **缺口幅度:** +{h.gap.gap_pct:.2f}%"
                f" (区间 {_fmt_price(h.gap.gap_low)} ~ {_fmt_price(h.gap.gap_high)})"
            )
            lines.append(
                f"- **最新收盘:** {_fmt_price(h.current_price)}"
                f" (该数据日收盘；MA60={_fmt_price(h.ma60)}, "
                f"偏离 {_fmt_pct(h.pct_from_ma60)})"
            )
            lines.append(
                f"- **距缺口上沿:** {_fmt_pct(h.pct_from_gap_high)}"
                "（非负：候选条件已要求缺口未回补；= 0 即收盘正好落在上沿）"
            )
            lines.append(
                f"- **缺口日量比:** {h.vol_ratio:.2f}"
                f"（缺口日成交额 {_fmt_amount_safe(getattr(h, 'gap_day_amount', None))}"
                f" ÷ 分母 {_fmt_amount_safe(getattr(h, 'vol_ratio_denom', None))}，"
                f"分母 = 缺口日前至多 20 根 bar 日均额；"
                f"当前尾部 20 日均额 {_fmt_amount(h.avg_amount_20d)} 为另一窗口）"
            )
            total = getattr(h, "ma60_total_bars", 0)
            valid = getattr(h, "ma60_valid_bars", 0)
            lines.append(
                f"- **MA60 判定覆盖:** {valid}/{total} 根 bar 有 MA60 值"
                "（覆盖不足的标的不判 MA60，归入 MA60破）"
            )
            lines.append("")
            lines.append(
                "> **事件/催化: 未采集。** 引擎只做价格形态检出，不做事件归因；"
                "如需补充请另行检索，并标注来源与日期（不得直接采信为事实）。"
            )
            lines.append("")

    # --- Footer ---
    lines.append("---")
    lines.append("")
    lines.extend(_risk_lines())
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
    basic Python types.  顶层附 ``nature`` / ``disclaimer``：JSON 是下游脚本的
    可消费接口，同样不得被读成技术信号（``hits`` = 观察清单）。
    """
    data = _dataclass_to_dict(result)
    payload = {
        "nature": "观察清单（非交易信号、非预测、非推荐排序）",
        "disclaimer": "本输出由规则化扫描生成，仅供个人研究参考，"
                      "不构成证券买卖、持有或仓位建议；未做基本面与事件核验。",
        **data,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2, default=str)
