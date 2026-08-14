"""形态扫描器编排（v0.2.6 M5）——双底/三角形底全市场检出。

数据管线复用 gap-scan canonical（kline_source 批量日线 + universe 成分池，
经 skills/lib/invest_path.load_gap_scan_module 显式路径加载）。
检测核心 = skills/lib/lmw.py（LMW 模板纯函数）；数据窥探防护 =
multiple_testing.reality_check（White 2000，规则×股票 forward 收益矩阵）。

输出为研究信号（研究工具，非决策工具）——LAW 6 边界：检出 ≠ 交易建议。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from invest_path import load_gap_scan_module  # noqa: E402

logger = logging.getLogger(__name__)

BANDWIDTHS = (0.3, 0.5, 1.0)
HORIZONS = (5, 10, 20)
MIN_BARS = 120
LOOKBACK_DAYS = 150  # 前 120 日回撤 + 形态 + 20 日前向


@dataclass
class ScanHit:
    ts_code: str
    pattern: str
    endpoint_idx: int
    bandwidth: float
    detail: dict = field(default_factory=dict)
    retest_status: str | None = None  # P2 回踩分类占位（本轮不实现）


def fetch_daily_and_adj(dates: list[str]):
    """批量日线 + 复权因子（TushareBulkSource 逐日期 × 1 调用）。"""
    kline_source = load_gap_scan_module("kline_source")
    source = kline_source.create_source("auto")
    daily = source.fetch_daily_batch(dates)
    adj = source.fetch_adj_factor_batch(dates)
    return source, daily, adj


def scan_universe(
    ts_codes: list[str],
    dates: list[str],
    bandwidths: tuple[float, ...] = BANDWIDTHS,
) -> tuple[list[ScanHit], dict]:
    """逐股检测 → 命中列表 + 规则×股票 forward 矩阵（供 reality_check）。

    返回 (hits, rule_matrix)：rule_matrix = {f"{pattern}_bw{bw}_{h}": [收益%...]}
    每列对应一条「规则」（形态×带宽×窗口）在全部命中上的 forward 收益；
    未命中股票的规则列以 0 填充（已扣无条件基线语义：命中 vs 全池均值）。
    """
    from lmw import detect_patterns, pattern_forward_stats  # noqa: E402

    kline_source = load_gap_scan_module("kline_source")
    source, daily, adj = fetch_daily_and_adj(dates)
    grouped = kline_source.group_daily_by_ts_code(daily)
    adj_by_ts = {str(c): g for c, g in adj.groupby("ts_code", sort=False)} if adj is not None and not adj.empty else {}

    hits: list[ScanHit] = []
    per_stock_fwd: dict[str, dict[str, float | None]] = {}
    for code in ts_codes:
        kline = kline_source.build_stock_kline(
            daily, adj_by_ts.get(code), code, min_bars=MIN_BARS, daily_by_ts=grouped,
        )
        if kline is None or kline.empty:
            continue
        closes = [float(v) for v in kline["close_qfq"].tolist()]
        for bw in bandwidths:
            res = detect_patterns(closes, bandwidth=bw, min_bars=MIN_BARS)
            for pat_kind in ("double_bottoms", "triangle_bottoms"):
                for p in res[pat_kind]:
                    ep = p["endpoint_idx"]
                    if ep >= len(closes) - 1:
                        continue
                    hits.append(ScanHit(
                        ts_code=code,
                        pattern="double_bottom" if pat_kind == "double_bottoms" else "triangle_bottom",
                        endpoint_idx=ep,
                        bandwidth=bw,
                        detail=p,
                    ))
        # 每只股票每种形态×带宽的 forward 收益（取首个命中；无命中 None）
        for bw in bandwidths:
            res = detect_patterns(closes, bandwidth=bw, min_bars=MIN_BARS)
            for pat_kind in ("double_bottoms", "triangle_bottoms"):
                pat_name = "double_bottom" if pat_kind == "double_bottoms" else "triangle_bottom"
                pats = res[pat_kind]
                if pats:
                    fwd = pattern_forward_stats(closes, pats[:1], horizons=HORIZONS)
                    for h in HORIZONS:
                        key = f"{pat_name}_bw{bw}_+{h}"
                        per_stock_fwd.setdefault(key, {})[code] = fwd[f"+{h}"][0] if fwd[f"+{h}"] else None

    rule_matrix: dict[str, list[float]] = {}
    # 全规则宇宙（2 形态 × 3 带宽 × 3 窗口 = 18）——无命中形态也必须占位，
    # 否则 RC 的规则宇宙随数据漂移（数据窥探口径不自洽）
    for pat in ("double_bottom", "triangle_bottom"):
        for bw in BANDWIDTHS:
            for h in HORIZONS:
                key = f"{pat}_bw{bw}_+{h}"
                by_code = per_stock_fwd.get(key, {})
                vals = [v for v in by_code.values() if v is not None]
                baseline = sum(vals) / len(vals) if vals else 0.0
                # 已扣基准：命中股票收益 − 全池该规则均值（RC 输入要求）；
                # 无命中股票记 0（中性）
                rule_matrix[key] = [
                    (by_code.get(c, baseline) or baseline) - baseline for c in ts_codes
                ]
    return hits, rule_matrix


def reality_check_report(rule_matrix: dict[str, list[float]]) -> dict:
    """数据窥探防护：全规则宇宙 RC 检验（White 2000）。"""
    from multiple_testing import reality_check  # noqa: E402

    if not rule_matrix:
        return {"error": "空规则矩阵"}
    keys = sorted(rule_matrix)
    rows = []
    n = len(rule_matrix[keys[0]])
    for i in range(n):
        rows.append([rule_matrix[k][i] for k in keys])
    rc = reality_check(rows, n_boot=5000, seed=42)
    rc["rule_names"] = keys
    rc["best_rule_name"] = keys[rc["best_rule"]] if rc["best_rule"] < len(keys) else None
    return rc
