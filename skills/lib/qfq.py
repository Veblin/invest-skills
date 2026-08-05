"""Qian-fu-quan (前复权) price adjustment using adj_factor — 供各 skill 共享。

Formula: qfq_price = raw_price * adj_factor / latest_adj_factor

历史：gap-scan qfq.py（DataFrame 版）与 stock collector/_sources._apply_qfq
（list[dict] 版）公式一致但校验严格度与输出形态不同，双路径保留（勿互走）：
- ``apply_qfq``（DataFrame）— gap 语义：拒绝 <=1e-12 / 非有限 / 任一 NaN
  （含 OHLC NaN，整股排除）；保留原列并追加 ``{col}_qfq``；不 rounding
- ``apply_qfq_rows``（list[dict]）— stock 语义：拒绝 <=0（epsilon 参数化）；
  round 4 位（round_prices）；输出新 dict 替换 OHLC 键；输入不突变
"""

from __future__ import annotations

import math
from typing import Any

import pandas as pd

PRICE_COLS = ("open", "high", "low", "close")

# Reject zero / near-zero / non-positive factors that would explode qfq prices.
_ADJ_EPS = 1e-12


def _factor_invalid(val: float, *, epsilon: float, check_finite: bool) -> bool:
    """True if *val* 非法：check_finite 且非有限，或 <= epsilon。"""
    return (check_finite and not math.isfinite(val)) or val <= epsilon


def apply_qfq(daily_df: pd.DataFrame, adj_factor_df: pd.DataFrame | None) -> pd.DataFrame | None:
    """Apply qian-fu-quan adjustment to daily price data（DataFrame 版，gap 语义）。

    Merges *adj_factor_df* onto *daily_df* on ``trade_date``, then computes
    qfq-adjusted OHLC columns.  The original columns are preserved alongside
    the new ``{col}_qfq`` columns.

    校验：adj_factor_df None/空 → None；任一 bar 缺因子（merge 后 NaN）→ None；
    任一因子非有限/<=1e-12 → None；任一 OHLC NaN → None（整股排除）。
    trade_date 归一化为 str 后按最大值锚定最新日（非末行）。
    """
    if adj_factor_df is None or adj_factor_df.empty:
        return None

    # Merge on trade_date (left join to preserve all daily rows).
    # Dense daily adj_factor assumed — sparse (ex-rights-only) → mostly NaN → reject.
    merged = daily_df.merge(
        adj_factor_df[["trade_date", "adj_factor"]],
        on="trade_date",
        how="left",
    )

    # Reject incomplete coverage — including trailing NaN that would poison scale
    if merged["adj_factor"].isna().any():
        return None

    factors = merged["adj_factor"].astype(float)
    # Row-level + latest: reject non-finite / <=0 / near-zero (avoids astronomical prices)
    if any(_factor_invalid(float(v), epsilon=_ADJ_EPS, check_finite=True) for v in factors):
        return None

    # 归一化 trade_date 为 str：datetime64 → 'YYYY-MM-DD'、int → 'yyyymmdd'、
    # str 原样（字典序 max 保持时间序）。merged 是 fresh merge 结果，改列不
    # 影响调用方输入。
    merged["trade_date"] = merged["trade_date"].astype(str)
    # 锚定最新交易日（按 trade_date 取最大行，而非末行）：Tushare 原生日线为
    # 降序，降序输入时 factors.iloc[-1] 是最旧 bar 的因子 → 整个序列被
    # f_oldest 错标度（与 _sources._apply_qfq 的锚定语义保持一致）
    newest = merged["trade_date"].max()
    latest_adj = float(merged.loc[
        merged["trade_date"] == newest, "adj_factor"].iloc[0])
    scale = factors / latest_adj

    # Reject if any OHLC column contains NaN (whole-stock exclude)
    for col in PRICE_COLS:
        if col in merged.columns and merged[col].isna().any():
            return None

    for col in PRICE_COLS:
        merged[f"{col}_qfq"] = merged[col] * scale

    return merged


def apply_qfq_rows(rows: list[dict], factors: dict[str, float], *,
                   epsilon: float = 0.0, check_finite: bool = False,
                   round_prices: bool = False) -> list[dict] | None:
    """前复权（list[dict] 版，stock 语义）：qfq_price = raw_price × factor / latest。

    校验：rows/factors 空 → None；因子缺失或非法（<=epsilon，可加 isfinite）
    → 整体拒绝；锚定"最新日"按 trade_date 取最大行（非 rows[-1]，Tushare 日线
    降序返回）。输出 {**r, "open":..., ...} 新 dict，输入行不突变。
    """
    if not rows or not factors:
        return None
    newest = max(rows, key=lambda r: str(r.get("trade_date", "")))
    latest = factors.get(str(newest.get("trade_date")))
    if latest is None or _factor_invalid(latest, epsilon=epsilon,
                                         check_finite=check_finite):
        return None
    out: list[dict[str, Any]] = []
    for r in rows:
        f = factors.get(str(r.get("trade_date")))
        if f is None or _factor_invalid(f, epsilon=epsilon,
                                        check_finite=check_finite):
            return None  # 缺失 → 整体拒绝
        ratio = f / latest
        out.append({
            **r,
            "open": (round(r["open"] * ratio, 4) if round_prices else r["open"] * ratio)
                    if r.get("open") is not None else None,
            "high": (round(r["high"] * ratio, 4) if round_prices else r["high"] * ratio)
                    if r.get("high") is not None else None,
            "low": (round(r["low"] * ratio, 4) if round_prices else r["low"] * ratio)
                    if r.get("low") is not None else None,
            "close": (round(r["close"] * ratio, 4) if round_prices else r["close"] * ratio)
                     if r.get("close") is not None else None,
        })
    return out


__all__ = ["PRICE_COLS", "apply_qfq", "apply_qfq_rows"]
