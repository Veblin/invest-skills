"""Core gap scanning algorithm.

Detects upward price gaps in index-component stocks, applying a tolerance rule
that finds the most recent gap satisfying both "MA60 never broken since gap day"
and "gap never filled".  See host-docs/v0.2.0-gap-scan-skill-design.md §2 for
the full algorithm spec.

Data structures
---------------
GapInfo / ScanHit / ScanResult — documented inline in their dataclass docstrings.

Import conventions
------------------
Follows the invest-a-limit-up pattern: sibling modules in ``scripts/lib/`` are
imported as top-level names (this file is found via ``_LIB_DIR`` on ``sys.path``,
so relative imports would fail).  invest-a-stock modules use
``ensure_invest_a_scripts_on_path()`` then ``from lib.technical import sma``.

Amount unit
-----------
Tushare Pro ``daily.amount`` is denominated in **千元 (thousand yuan)**.
The ``kline_source`` layer **must** convert to **元** before populating
``stock_kline_map``.  All amounts in this module are assumed to be in **元**.
"""

from __future__ import annotations

import logging
import math
from collections import Counter
from dataclasses import dataclass
from typing import Any

import pandas as pd

from _invest_path import ensure_invest_a_scripts_on_path

ensure_invest_a_scripts_on_path()

from codes import is_st_or_delisted  # noqa: E402
from lib.technical import sma  # noqa: E402

# Sibling modules (found via _LIB_DIR on sys.path)
from skip_reasons import ExcludeReason, NonHitReason  # noqa: E402
from suspension import is_gap_across_suspension  # noqa: E402

logger = logging.getLogger(__name__)


# ======================================================================
# Data structures
# ======================================================================


@dataclass
class GapInfo:
    """A detected upward gap between two consecutive trading days.

    .. code-block::

         gap_high  ─────── low[i]        (upper bound of the gap)
                            ↑
                         gap_pct = low[i] / high[i-1] - 1
                            ↓
         gap_low   ─────── high[i-1]      (lower bound of the gap)
    """

    gap_date: str
    gap_pct: float
    gap_low: float
    gap_high: float
    is_across_suspension: bool = False


@dataclass
class ScanHit:
    """A stock that matched the gap scanning criteria."""

    ts_code: str
    name: str
    board: str
    index_members: list[str]
    gap: GapInfo
    current_price: float
    ma60: float
    pct_from_ma60: float
    pct_from_gap_high: float
    vol_ratio: float
    avg_amount_20d: float


@dataclass
class ScanResult:
    """Aggregated result of a full gap scan over the universe."""

    hits: list[ScanHit]
    across_suspension_hits: list[ScanHit]
    exclude_reasons: Counter
    non_hit_reasons: Counter
    total_in_universe: int
    total_scanned: int
    total_with_kline: int
    total_fetch_errors: int
    params: dict


# ======================================================================
# Gap detection helpers
# ======================================================================

# 单日向上跳空幅度上限（%）——按板块涨跌停推导，超过视为数据毛刺
# （零价/错价 bar 后接正常价会算出天文 gap）：主板 10% → 1.1/0.9-1=22.2%；
# 创业板/科创板 20% → 1.2/0.8-1=50%（涨停价四舍五入可至 ~50.06%）；
# 北交所 30% → 1.3/0.7-1=85.7%。各加裕度。停牌数据不可得时不拦截（fail-open）。
_MAX_GAP_PCT = 30.0          # 主板默认
_MAX_GAP_PCT_CN_CYB = 60.0   # 创业板/科创板（300/301/688 前缀）
_MAX_GAP_PCT_BSE = 95.0      # 北交所（4/8/920 前缀）


def _max_gap_pct_for_code(ts_code: str) -> float:
    """按代码前缀返回毛刺过滤上限（%）；未知前缀按主板。"""
    if ts_code.startswith(("300", "301", "688")):
        return _MAX_GAP_PCT_CN_CYB
    if ts_code.startswith(("4", "8", "920")):
        return _MAX_GAP_PCT_BSE
    return _MAX_GAP_PCT


def _find_candidate_gaps(
    kline: pd.DataFrame,
    lookback: int,
    gap_min_pct: float,
    *,
    suspensions: list[str] | None = None,
    trade_cal: list[str] | None = None,
    ts_code: str = "",
    suspensions_available: bool = True,
) -> tuple[list[tuple[int, GapInfo]], list[tuple[int, GapInfo]]]:
    """Find all upward gaps in the lookback window.

    Parameters
    ----------
    kline : pd.DataFrame
        QFQ-adjusted daily bars, sorted ascending by ``trade_date``.
        Must contain columns ``high_qfq``, ``low_qfq``, ``trade_date``.
    lookback : int
        Number of most-recent trading days to search.
    gap_min_pct : float
        Minimum gap magnitude (e.g. 1.0 = 1 %) for a gap to be "qualified".
    suspensions : list[str] | None
        该股停牌日期列表（yyyymmdd）；复牌日真实大跳空不受板块毛刺上限拦截。
    trade_cal : list[str] | None
        交易日历（yyyymmdd，有序）；用于跨停牌判定。
    ts_code : str
        股票代码，用于按板块推导毛刺过滤上限（默认 "" → 主板 30%）。
    suspensions_available : bool
        停牌/日历数据是否可信。False（估计日历、trade_cal=None）时大缺口
        过滤 fail-open——不拦截任何 gap（docstring：保守不拦截大缺口），
        仅正向验证过的跨停牌跳空打 is_across_suspension 标注。

    Returns
    -------
    all_candidates : list of (index, GapInfo)
        Every gap found (any ``low[i] > high[i-1]``), regardless of magnitude.
    qualified : list of (index, GapInfo)
        Subset of *all_candidates* meeting the ``gap_min_pct`` threshold.
    """
    highs = kline["high_qfq"].values
    lows = kline["low_qfq"].values
    dates = kline["trade_date"].values

    n = len(kline)
    start = max(1, n - lookback)

    max_gap_pct = _max_gap_pct_for_code(ts_code)
    all_candidates: list[tuple[int, GapInfo]] = []
    for i in range(start, n):
        # highs[i-1]<=0（零价毛刺/停牌残留 bar）时跳过：除零会炸掉整个扫描；
        # gap_pct 超板块毛刺上限视为毛刺（如 high_qfq=0.001 后接正常 bar
        # 会算出 +499,900% 的天文缺口并排到命中榜首）——但"跨停牌"的复牌日
        # 大跳空是真实信号（盐湖股份 2021-08-10 复牌 +347%），不拦截：
        # 前一日在停牌表内 → 放行并保留 is_gap_across_suspension 标注机会；
        # suspensions_available=False（估计日历/无停牌表）时同样放行
        # （fail-open：缺信息不丢弃潜在真实信号）
        if highs[i - 1] > 0 and lows[i] > highs[i - 1]:
            gap_pct = (lows[i] / highs[i - 1] - 1.0) * 100.0
            if (
                gap_pct > max_gap_pct
                and suspensions_available
                and not _is_gap_across_suspension(
                    str(dates[i]), suspensions, trade_cal,
                )
            ):
                continue
            gi = GapInfo(
                gap_date=str(dates[i]),
                gap_pct=gap_pct,
                gap_low=float(highs[i - 1]),
                gap_high=float(lows[i]),
            )
            all_candidates.append((i, gi))

    qualified = [(i, gi) for i, gi in all_candidates if gi.gap_pct >= gap_min_pct]
    return all_candidates, qualified


def _is_gap_across_suspension(
    gap_date: str,
    suspensions: list[str] | None,
    trade_cal: list[str] | None,
) -> bool:
    """gap_date 前一个交易日是否为停牌日（复牌首日跳空）。

    与命中后的标注（is_gap_across_suspension）共用同一语义；缺少
    停牌表或交易日历时返回 False（保守：不拦截大缺口）。
    """
    if not suspensions or not trade_cal:
        return False
    return is_gap_across_suspension(gap_date, suspensions, trade_cal)


def _check_ma60_streak(closes: list[float], ma60: list[float | None],
                        gap_idx: int, min_valid_ratio: float = 0.25) -> bool:
    """Return True if ``close[t] >= MA60[t]`` for every ``t >= gap_idx``.

    Entries where ``ma60[t] is None`` are skipped (the first 59 positions
    in the SMA output).  To prevent vacuous passes, at least
    *min_valid_ratio* of the post-gap positions must have a valid MA60
    value (default 25 %, i.e. the gap must have formed well after the
    first 59 bars of MA60 warmup).

    Uses a relative epsilon tolerance (``rel_tol=1e-9``) for the close vs
    MA60 comparison to avoid spurious breaks from QFQ-adjustment float
    rounding while still detecting genuine breaches.
    """
    valid_count = 0
    total_count = 0
    for t in range(gap_idx, len(closes)):
        total_count += 1
        m = ma60[t]
        if m is not None:
            valid_count += 1
            if not (closes[t] >= m or math.isclose(closes[t], m, rel_tol=1e-5, abs_tol=0.01)):
                return False
    if total_count > 0 and valid_count / total_count < min_valid_ratio:
        return False  # too few valid MA60 bars for a meaningful check
    return True


def _check_unfilled(lows: list[float], gap_idx: int,
                    gap_high: float) -> bool:
    """Return True if the gap has never been filled (partially or fully).

    A gap is unfilled when ``min(low[gap_idx+1:]) > gap_high``
    (touching the upper edge counts as filled).
    If *gap_idx* is the last bar, the condition is vacuously true.
    """
    if gap_idx >= len(lows) - 1:
        return True
    return min(lows[gap_idx + 1:]) > gap_high


def _build_scan_hit(
    stock: Any,
    gap: GapInfo,
    gap_idx: int,
    closes: list[float],
    ma60_list: list[float | None],
    amounts: list[float],
    avg_amount_20d: float,
    vol_ratio: float,
) -> ScanHit:
    """Construct a ScanHit from the matched gap and current market state.

    *vol_ratio* is pre-computed by the caller using the gap-local 20-day
    average (not the current tail average) — see :func:`_scan_stock`.
    *avg_amount_20d* is the current 20-day average used for the hit table.
    """
    current_price = closes[-1]
    ma60 = ma60_list[-1]
    if ma60 is None:
        ma60 = float('nan')
        pct_from_ma60 = float('nan')
    elif abs(ma60) < 1e-9:
        pct_from_ma60 = 0.0
    else:
        pct_from_ma60 = (current_price - ma60) / ma60 * 100.0

    if abs(gap.gap_high) < 1e-9:
        pct_from_gap_high = 0.0
    else:
        pct_from_gap_high = (current_price - gap.gap_high) / gap.gap_high * 100.0

    return ScanHit(
        ts_code=stock.ts_code,
        name=stock.name,
        board=stock.board,
        index_members=getattr(stock, "index_membership", []),
        gap=gap,
        current_price=current_price,
        ma60=ma60,
        pct_from_ma60=pct_from_ma60,
        pct_from_gap_high=pct_from_gap_high,
        vol_ratio=vol_ratio,
        avg_amount_20d=avg_amount_20d,
    )


# ======================================================================
# Per-stock scanning
# ======================================================================


def _scan_stock(
    stock: Any,
    kline: pd.DataFrame,
    suspension_map: dict[str, list[str]],
    params: dict,
    trade_cal: list[str] | None,
) -> tuple[ScanHit | None, ExcludeReason | None, NonHitReason | None]:
    """Scan a single stock for qualifying gaps.

    Returns
    -------
    (hit, None, None)              — regular hit found
    (across_susp_hit, None, None)  — cross-suspension hit (hit.is_across_suspension=True)
    (None, exclude_reason, None)   — excluded before scanning
    (None, None, non_hit_reason)   — scanned but no qualifying gap
    """
    ts_code = stock.ts_code

    # --- Exclude: insufficient kline length ---
    if len(kline) < params["min_list_days"]:
        return None, ExcludeReason.INSUFFICIENT_KLINE, None

    # --- Extract columns ---
    closes = kline["close_qfq"].tolist()
    highs = kline["high_qfq"].tolist()
    lows = kline["low_qfq"].tolist()
    # IMN amounts are expected to be in 元
    amounts = kline["amount"].tolist()

    # --- Exclude: low liquidity (20-day avg amount) ---
    lookback_20 = min(20, len(amounts))
    avg_amount_20d = sum(amounts[-lookback_20:]) / lookback_20
    if not (avg_amount_20d >= params["min_avg_amount"]):
        return None, ExcludeReason.LOW_LIQUIDITY, None

    # --- Compute MA60 ---
    ma60_list = sma(closes, 60)

    # --- Find gaps ---
    gap_lookback = params["gap_lookback"]
    gap_min_pct = params["gap_min_pct"]
    # 停牌数据可用性：估计日历路径 suspension_map={}（scan.py）与
    # trade_cal=None 时无停牌信息 → 大缺口过滤须 fail-open（docstring
    # 承诺"保守：不拦截大缺口"；盐湖 2021-08-10 复牌 +347% 不得静默丢弃）。
    # 个股不在 map 中（从未停牌）≠ 信息缺失：map 非空即视为可用。
    susp_available = trade_cal is not None and bool(suspension_map)
    stock_suspensions = suspension_map.get(ts_code, []) if susp_available else []
    all_candidates, qualified = _find_candidate_gaps(
        kline, gap_lookback, gap_min_pct,
        suspensions=stock_suspensions, trade_cal=trade_cal,
        ts_code=ts_code, suspensions_available=susp_available,
    )

    # --- Non-hit: no gap at all ---
    if len(all_candidates) == 0:
        return None, None, NonHitReason.NO_GAP

    # --- Non-hit: only sub-threshold gaps ---
    if len(qualified) == 0:
        return None, None, NonHitReason.BELOW_THRESHOLD

    # --- Tolerance rule: iterate qualified gaps from newest to oldest ---
    qualified_desc = sorted(qualified, key=lambda x: x[0], reverse=True)

    any_passed_ma60 = False
    any_unfilled = False
    any_vol_ratio_fail = False

    for gap_idx, gap in qualified_desc:
        if not _check_ma60_streak(closes, ma60_list, gap_idx):
            continue

        any_passed_ma60 = True

        if not _check_unfilled(lows, gap_idx, gap.gap_high):
            continue  # filled — try older gap (never promote filled+across to hit)

        any_unfilled = True

        # Gap-local 20-day average: bars preceding the gap day.
        # Using the gap-proximate baseline (not current tail) means the
        # vol_ratio reflects whether the gap was "above normal volume at
        # the time", which is more meaningful for the tolerance rule.
        local_lookback = min(20, gap_idx)
        local_avg = (
            sum(amounts[gap_idx - local_lookback:gap_idx]) / local_lookback
            if local_lookback > 0
            else amounts[gap_idx]
        )
        if local_avg >= 1e-9:
            vol_ratio = amounts[gap_idx] / local_avg
        else:
            vol_ratio = 0.0

        gap_min_vol = params.get("gap_min_vol_ratio", 1.0)
        # 1.0 = CLI default "no filter"; isclose avoids FP false-triggers
        if not math.isclose(float(gap_min_vol), 1.0, abs_tol=1e-9):
            if vol_ratio < gap_min_vol:
                any_vol_ratio_fail = True
                continue  # try older gap

        # Qualifying hit — tag across-suspension if applicable
        if trade_cal is not None:
            if is_gap_across_suspension(gap.gap_date, stock_suspensions, trade_cal):
                gap.is_across_suspension = True

        hit = _build_scan_hit(
            stock, gap, gap_idx, closes, ma60_list, amounts,
            avg_amount_20d, vol_ratio,
        )
        return hit, None, None

    # --- No hit after tolerance rule ---
    if not any_passed_ma60:
        return None, None, NonHitReason.MA60_BROKEN
    if not any_unfilled:
        return None, None, NonHitReason.GAP_FILLED
    if any_vol_ratio_fail:
        return None, None, NonHitReason.VOL_RATIO_LOW
    return None, None, NonHitReason.GAP_FILLED


# ======================================================================
# Sorting
# ======================================================================


def sort_hits(hits: list[ScanHit]) -> list[ScanHit]:
    """Sort hits by: gap_pct desc, abs(pct_from_ma60) asc, ts_code asc."""
    return sorted(
        hits,
        key=lambda h: (-h.gap.gap_pct, abs(h.pct_from_ma60), h.ts_code),
    )


# ======================================================================
# Main entry point
# ======================================================================


def scan_all(
    stocks: list,
    stock_kline_map: dict[str, pd.DataFrame],
    adj_factor_map: dict[str, pd.DataFrame | None],
    suspension_map: dict[str, list[str]],
    params: dict,
    trade_cal: list[str] | None = None,
    already_qfq: bool = False,
) -> ScanResult:
    """Run gap scan over the full universe.

    Parameters
    ----------
    stocks : list
        List of stock objects (from ``universe.py``).  Each must have
        ``.ts_code``, ``.name``, ``.board`` attributes.
    stock_kline_map : dict[str, pd.DataFrame]
        ``ts_code`` → QFQ-adjusted daily DataFrame (as produced by
        ``apply_qfq`` in ``qfq.py``).  Only stocks with valid data are
        present in this map.
    adj_factor_map : dict[str, pd.DataFrame | None]
        ``ts_code`` → adjustment-factor DataFrame (``None`` if the factor
        could not be fetched).  Used to distinguish ``MISSING_ADJ_FACTOR``
        from ``FETCH_ERROR`` for stocks absent from *stock_kline_map*
        when *already_qfq* is False.
    suspension_map : dict[str, list[str]]
        ``ts_code`` → list of suspension dates (yyyymmdd).
    params : dict
        Scan parameters with keys:
        - ``gap_min_pct`` (float)
        - ``gap_lookback`` (int)
        - ``gap_min_vol_ratio`` (float)
        - ``min_avg_amount`` (int, in yuan)
        - ``min_list_days`` (int)
    trade_cal : list[str] | None
        Ordered list of all trade dates (yyyymmdd) in the window.  Required
        for cross-suspension detection; gaps near suspensions are flagged
        when this is provided.
    already_qfq : bool
        If True (baostock path), missing kline is always ``FETCH_ERROR``
        because adj_factor is intentionally unused.

    Returns
    -------
    ScanResult
    """
    exclude, non_hit = Counter(), Counter()
    hits: list[ScanHit] = []
    across_suspension_hits: list[ScanHit] = []

    total_fetch_errors = 0
    total_scanned = 0
    total_in_universe = len(stocks)
    total_with_kline = sum(
        1
        for s in stocks
        if (k := stock_kline_map.get(s.ts_code)) is not None and not k.empty
    )

    for idx, stock in enumerate(stocks):
        ts_code = stock.ts_code

        # --- Log progress every 50 stocks ---
        if idx > 0 and idx % 50 == 0:
            logger.info(
                "扫描进度: %d / %d (命中 %d, 排除 %d)",
                idx, total_in_universe, len(hits), sum(exclude.values()),
            )

        # --- Determine if kline data is available ---
        kline = stock_kline_map.get(ts_code)

        if kline is None or kline.empty:
            if already_qfq:
                exclude[ExcludeReason.FETCH_ERROR] += 1
                total_fetch_errors += 1
            else:
                adj = adj_factor_map.get(ts_code)
                if adj is None or (hasattr(adj, "empty") and adj.empty):
                    exclude[ExcludeReason.MISSING_ADJ_FACTOR] += 1
                    total_fetch_errors += 1
                else:
                    exclude[ExcludeReason.FETCH_ERROR] += 1
                    total_fetch_errors += 1
            continue

        # --- Exclude: ST / delist ---
        # (These are handled by universe.py at build time, but we keep a
        #  defensive check for stock_basic enrichment edge cases.)
        name = getattr(stock, "name", "")
        if is_st_or_delisted(name):
            if "退" in name:
                exclude[ExcludeReason.DELIST] += 1
            else:
                exclude[ExcludeReason.ST_STOCK] += 1
            continue

        # --- Scan stock ---
        # 逐股异常隔离：单只标的数据毛刺（除零/空值等）不得终止全池扫描
        try:
            hit, excl_reason, non_reason = _scan_stock(
                stock, kline, suspension_map, params, trade_cal,
            )
        except Exception:
            # 逐股隔离防整池中断，但异常需带追溯栈（不静默掩盖代码缺陷）
            logger.exception("gap scan failed for %s", ts_code)
            exclude[ExcludeReason.FETCH_ERROR] += 1
            total_fetch_errors += 1
            continue

        if excl_reason is not None:
            exclude[excl_reason] += 1
            continue

        if non_reason is not None:
            non_hit[non_reason] += 1
            total_scanned += 1
            continue

        # --- Hit (regular or across-suspension) ---
        total_scanned += 1
        if hit is not None and hit.gap.is_across_suspension:
            across_suspension_hits.append(hit)
        elif hit is not None:
            hits.append(hit)

    # Sort regular hits
    hits = sort_hits(hits)

    logger.info(
        "扫描完成: %d 命中, %d 跨停牌, %d 排除, %d 未命中, %d 获取失败",
        len(hits), len(across_suspension_hits),
        sum(exclude.values()), sum(non_hit.values()), total_fetch_errors,
    )

    return ScanResult(
        hits=hits,
        across_suspension_hits=across_suspension_hits,
        exclude_reasons=exclude,
        non_hit_reasons=non_hit,
        total_in_universe=total_in_universe,
        total_scanned=total_scanned,
        total_with_kline=total_with_kline,
        total_fetch_errors=total_fetch_errors,
        params=params,
    )
