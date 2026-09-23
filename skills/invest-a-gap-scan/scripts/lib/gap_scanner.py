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
Follows the sibling-module import pattern: modules in ``scripts/lib/`` are
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
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

from _invest_path import ensure_invest_a_scripts_on_path

ensure_invest_a_scripts_on_path()

from codes import is_st_or_delisted  # noqa: E402
from dates import shanghai_now  # noqa: E402
from lib.technical import limit_pct_for_symbol, sma  # noqa: E402

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
    """A stock that matched the gap scanning criteria.

    ``data_date`` 是该股 K 线最后一根 bar 的交易日（yyyymmdd）——报告中的
    「最新收盘」即该日收盘价，与全池 ``ScanResult.data_as_of`` 可能不同
    （停牌股更早），故逐条携带，供报告标注数据时点。

    ``gap_day_amount`` / ``vol_ratio_denom`` 使量比可复算：
    ``vol_ratio = gap_day_amount / vol_ratio_denom``，其中分母是缺口日前
    **至多 20 根** bar 的日均成交额（``min(20, gap_idx)``）——与
    ``avg_amount_20d``（当前尾部 20 日均额，用于低流动性门槛）是**两个不同的
    窗口**，不可互算。

    ``ma60_valid_bars`` / ``ma60_total_bars`` 是 MA60 判定覆盖度：``total`` 为
    缺口日起的 bar 数，``valid`` 为其中 MA60 有值（非前 59 根预热区）的 bar 数。
    覆盖度不足（比例 < 25% 或有效 bar < 3）的标的不判 MA60，归入 MA60破。
    """

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
    data_date: str = ""
    gap_day_amount: float = float("nan")
    vol_ratio_denom: float = float("nan")
    ma60_valid_bars: int = 0
    ma60_total_bars: int = 0


@dataclass
class ScanResult:
    """Aggregated result of a full gap scan over the universe.

    ``data_as_of`` 是全池 K 线最新 bar 的交易日（yyyymmdd）——报告据此标注
    「数据截止日」，避免把数日前的收盘读作「当前」。``cache_status`` 与
    ``attempted_sources`` 由 ``scan.py`` 在采集层回填（本模块不感知缓存）。
    """

    hits: list[ScanHit]
    across_suspension_hits: list[ScanHit]
    exclude_reasons: Counter
    non_hit_reasons: Counter
    total_in_universe: int
    total_scanned: int
    total_with_kline: int
    total_fetch_errors: int
    params: dict
    data_as_of: str = ""
    cache_status: dict | None = None
    attempted_sources: list[str] | None = None


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
    """按板块涨跌停推导毛刺过滤上限（%）；未知前缀按主板。

    涨跌停阈值表唯一权威：lib.technical.limit_pct_for_symbol（跨 skill 共享，
    不在此维护第二份前缀表）。此处仅把涨跌停阈值映射为「涨停价/跌停价 - 1」
    的极端跳空上限并加裕度（涨停价四舍五入裕度）：
    10% → 1.1/0.9-1 = 22.2% → 30%；20% → 1.2/0.8-1 = 50% → 60%；
    30% → 1.3/0.7-1 = 85.7% → 95%。
    """
    symbol = str(ts_code).split(".")[0]
    thr = limit_pct_for_symbol(symbol)
    return {10.0: _MAX_GAP_PCT, 20.0: _MAX_GAP_PCT_CN_CYB, 30.0: _MAX_GAP_PCT_BSE}[thr]


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


def _normalize_date(value: Any) -> str:
    """规范化交易日为 ``yyyymmdd``；**无法唯一确定日期时返回 ``""``**。

    兼容 ``YYYYMMDD``、``YYYY-MM-DD`` 与 pandas Timestamp 的
    ``YYYY-MM-DD HH:MM:SS`` 前缀。位数异常（如 9 位的 ``202609023``）一律返回
    ``""``——**不做截断猜测**：截断会把坏数据变成看似合理的日期，再被用作
    「bar 是否已完成」「数据截止日」这类判定的输入。
    （日历合法性如 ``20260230`` 不在此处判：见 :func:`is_cached_bar_settled`。）
    """
    s = str(value).strip().replace("-", "")
    if len(s) >= 8 and s[:8].isdigit() and (len(s) == 8 or not s[8].isdigit()):
        return s[:8]
    return ""


def _resolve_after_close(last_bar_date: str, now: datetime | None = None) -> bool:
    """该股最新 bar 是否「已完成」——决定最新 bar 的缺口能否下未回补结论。

    只按墙钟判（``hour >= 15``）会把**盘前/周末/节假日**运行时的最新 bar
    （实为上一交易日的已完成 bar）误判为「未收盘」，本可判定的命中被丢进
    ``GAP_UNCONFIRMED`` 终结桶（实测：同一份数据，周六 23:46 跑出 10 命中，
    周一 06:40 跑出 9 命中 + 1 条待确认）。故按**数据日期**判：

    - 最新 bar 日期 < 今日 → 已完成（盘前、周末、节假日、停牌股皆属此类）
    - 最新 bar 日期 == 今日 → 仅上海时间 ≥15:00（日线已发布）算完成；
      盘中 bar 仍在变动，不得提前确认
    - 日期不可解析 → 保守判 False（不猜）
    """
    today = (now or shanghai_now()).strftime("%Y%m%d")
    bar = _normalize_date(last_bar_date)
    if len(bar) != 8 or not bar.isdigit():
        return False
    if bar < today:
        return True
    if bar == today:
        return (now or shanghai_now()).hour >= 15
    return False


def is_cached_bar_settled(last_bar_date: str, cache_mtime: float | None,
                          now: datetime | None = None) -> bool:
    """缓存里最后一根 bar 是否已「定稿」——未定稿的缓存不得用于任何结论。

    数据源可能在**盘中**返回当日尚未走完的 bar；缓存 TTL 为 3 天，这份盘中
    快照会被留到次日。次日按日期比较时「昨日 bar < 今日」成立，
    `_resolve_after_close` 会把它当成已完成 bar，从而用**未走完的低点**断言
    「缺口未回补」——而当日真实收盘可能早已回补（实测：盘中快照产出 1 条命中，
    真实收盘数据下该缺口根本不成立）。

    判据：**缓存文件的写入时刻必须晚于该 bar 自身交易日的收盘（15:00）**。
    不能只看「bar 日期是否为今日」——盘中写入的当日 bar 到了次日就变成
    「历史 bar」，日期比较会认为它天然定稿，而它仍是未走完的快照。

    - bar 日期不可解析 / 晚于今日（异常数据）→ 不信任 ❌
    - 写入时刻不可得（stat 失败）→ 保守判未定稿 ❌

    **本函数永不抛异常**：它是缓存校验谓词，调用点在逐股异常隔离之外，抛出会
    让**整次扫描中止**。故 8 位但非法的日历日期（`20260230`）、时间戳越界等
    一律归入「未定稿」→ 该股按缓存未命中重拉，坏条目被覆盖。
    """
    _now = now or shanghai_now()
    bar = _normalize_date(last_bar_date)
    if len(bar) != 8 or not bar.isdigit():
        return False
    if bar > _now.strftime("%Y%m%d"):
        return False
    if cache_mtime is None:
        return False
    tz = _now.tzinfo or ZoneInfo("Asia/Shanghai")
    try:
        bar_close = datetime.strptime(bar, "%Y%m%d").replace(hour=15, tzinfo=tz)
        written = datetime.fromtimestamp(cache_mtime, tz=tz)
    except (ValueError, OverflowError, OSError):
        # 非法日历日期（20260230）、月/日为 00、时间戳越界 → 不信任，按未定稿重拉
        return False
    return written >= bar_close


def _ma60_streak_stats(closes: list[float], ma60: list[float | None],
                        gap_idx: int, min_valid_ratio: float = 0.25,
                        ) -> tuple[bool, int, int]:
    """Return ``(passed, valid_bars, total_bars)`` for the MA60 streak check.

    ``passed`` is True iff ``close[t] >= MA60[t]`` for every ``t >= gap_idx``
    (within tolerance).  Entries where ``ma60[t] is None`` are skipped (the
    first 59 positions in the SMA output).  To prevent vacuous passes, at
    least *min_valid_ratio* of the post-gap positions must have a valid MA60
    value (default 25 %, i.e. the gap must have formed well after the first
    59 bars of MA60 warmup) **and** at least 3 valid bars (``min(3, total)``
    when fewer than 3 bars exist in total).

    Tolerance: ``math.isclose(close, ma60, rel_tol=1e-5, abs_tol=0.01)`` ——
    **绝对带 0.01 元** 为下界（低价股上近似 0.2%），仅用于吸收前复权换算的
    浮点尾差；真实跌破（幅度远大于此）必被检出。

    ``valid_bars``/``total_bars`` 随返回值输出，供报告披露 MA60 判定的覆盖度
    （短历史标的的有效 bar 少，命中含义弱于长历史标的）。
    """
    valid_count = 0
    total_count = 0
    for t in range(gap_idx, len(closes)):
        total_count += 1
        m = ma60[t]
        if m is not None:
            valid_count += 1
            if not (closes[t] >= m or math.isclose(closes[t], m, rel_tol=1e-5, abs_tol=0.01)):
                return False, valid_count, total_count
    # 仅 gap bar 自身（无后续数据）：强度验证无意义，放行交由调用方的
    # GAP_UNCONFIRMED / after_close 分支决定（不被绝对下限误拒）
    if total_count == 1:
        return True, valid_count, total_count
    # 绝对下限（防短历史标的比例被稀释）：MA60 从 bar 59 起有效，61-bar
    # 标的前期缺口只有 1-2 个真实 MA60 值，比例 50% 仍过 25% 门槛 ——
    # 需要同时满足 min_valid_ratio 比例与至少 3 个有效 bar。
    # min(3, total_count)：total==2（缺口在倒数第二根 bar = 昨日缺口）时
    # 绝对下限 3 不可满足会恒拒——退化为比例门槛（2 根 bar 需 2 个有效，
    # 历史充足标的天然满足）；total>=3 行为与绝对下限 3 完全一致。
    min_valid_bars = max(math.ceil(total_count * min_valid_ratio),
                         min(3, total_count))
    if total_count > 0 and valid_count < min_valid_bars:
        return False, valid_count, total_count  # too few valid MA60 bars
    return True, valid_count, total_count


def _check_ma60_streak(closes: list[float], ma60: list[float | None],
                        gap_idx: int, min_valid_ratio: float = 0.25) -> bool:
    """``_ma60_streak_stats`` 的布尔投影（保留既有调用面与测试）。"""
    return _ma60_streak_stats(closes, ma60, gap_idx, min_valid_ratio)[0]


def _check_unfilled(lows: list[float], gap_idx: int,
                    gap_high: float, after_close: bool = False,
                    gap_low: float | None = None) -> bool:
    """Return True if the gap has never been filled (partially or fully).

    A gap is unfilled when ``min(low[gap_idx+1:]) > gap_high``
    (touching the upper edge counts as filled).
    最新 bar（gap_idx == len(lows)-1，无后续数据）：盘中无法确认回补，
    恒返回 False（由调用方以 GAP_UNCONFIRMED 区分「待收盘确认」与
    「已回补」）；收盘后（after_close=True）日线 bar 完整，缺口未回补
    的充要条件是 ``lows[gap_idx] > highs[gap_idx-1] == gap_low``（检测
    谓词已保证成立）。注意不能用 ``lows[gap_idx] > gap_high``：gap_high
    就是该 bar 自身的 low，自比较恒 False（38a7e1e 回归，review #1）。
    """
    if gap_idx >= len(lows) - 1:
        if after_close:
            # 调用点恒传 gap_low（GapInfo.gap_low = highs[gap_idx-1]）；
            # 检测谓词已保证 lows[gap_idx] > gap_low（缺口未回补）。
            # None 显式拒绝（review 第三轮 #2：签名不得宣称 None 合法——
            # 裸比较会抛 TypeError，显式 ValueError 契约清晰）。
            if gap_low is None:
                raise ValueError(
                    "after_close=True 时必须提供 gap_low（缺口下沿 = 前一日 high）"
                )
            return lows[gap_idx] > gap_low
        return False
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
    *,
    vol_ratio_denom: float = float("nan"),
    ma60_stats: tuple[int, int] = (0, 0),
    data_date: str = "",
) -> ScanHit:
    """Construct a ScanHit from the matched gap and current market state.

    *vol_ratio* is pre-computed by the caller using the gap-local 20-day
    average (not the current tail average) — see :func:`_scan_stock`.
    *avg_amount_20d* is the current 20-day average used for the hit table.
    *vol_ratio_denom* is the gap-local denominator, carried so that the
    report can show ``量比 × 分母 = 缺口日成交额`` (recomputable).
    *ma60_stats* is ``(valid_bars, total_bars)`` for the MA60 streak check.
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
        data_date=data_date,
        gap_day_amount=float(amounts[gap_idx]),
        vol_ratio_denom=vol_ratio_denom,
        ma60_valid_bars=ma60_stats[0],
        ma60_total_bars=ma60_stats[1],
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
    after_close: bool = False,
) -> tuple[ScanHit | None, ExcludeReason | None, NonHitReason | None]:
    """Scan a single stock for qualifying gaps.

    *after_close* 由调用方按**该股最新 bar 日期**判定（见
    :func:`_resolve_after_close`），不是全局墙钟。

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

    # --- 该股最新 bar 日期（报告「数据日」；停牌股早于全池 data_as_of） ---
    data_date = _normalize_date(kline["trade_date"].values[-1])

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
    any_unconfirmed = False

    for gap_idx, gap in qualified_desc:
        ma60_ok, ma60_valid, ma60_total = _ma60_streak_stats(closes, ma60_list, gap_idx)
        if not ma60_ok:
            continue

        any_passed_ma60 = True

        # 最新 bar 的跳空无后续数据：bar 尚未完成时**既不能下「未回补」结论，
        # 也不能下「已回补」结论**（`_check_unfilled` 对无后续数据的 bar 恒
        # 返回 False，若只靠它会误归入「缺口回补」终结桶），标「待收盘确认」
        # 并回退到更早的已确认缺口。after_close=True（最新 bar 已完成，见
        # `_resolve_after_close`）时 lows[gap_idx] > highs[gap_idx-1]（gap_low）
        # 即证明未回补——同日缺口的定义性豁免，直接放行。
        if gap_idx >= len(lows) - 1 and not after_close:
            any_unconfirmed = True
            continue

        if not _check_unfilled(lows, gap_idx, gap.gap_high,
                               after_close=after_close, gap_low=gap.gap_low):
            continue  # filled — try older gap (never promote filled+across to hit)

        any_unfilled = True

        # Gap-local average: 缺口日**前**至多 20 根 bar 的日均成交额
        # (`min(20, gap_idx)`)——注意与 `avg_amount_20d`（当前尾部 20 日均额，
        # 用于低流动性门槛）是不同窗口，报告须同时给出分母使量比可复算。
        # 用缺口邻近基准（而非当前尾部）意味着 vol_ratio 反映的是「缺口当日
        # 成交额相对当时常态水平」，对容忍规则更有意义。
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
            vol_ratio_denom=local_avg,
            ma60_stats=(ma60_valid, ma60_total),
            data_date=data_date,
        )
        return hit, None, None

    # --- No hit after tolerance rule ---
    if not any_passed_ma60:
        return None, None, NonHitReason.MA60_BROKEN
    if not any_unfilled and any_unconfirmed:
        return None, None, NonHitReason.GAP_UNCONFIRMED
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
    after_close: bool | None = None,
    now: datetime | None = None,
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
    after_close : bool | None
        ``None``（默认，生产路径）→ **逐股**按 ``_resolve_after_close``
        用该股最新 bar 日期与 ``now`` 判定「最新 bar 是否已完成」；
        显式 True/False 则强制（测试或显式重放用）。
    now : datetime | None
        「现在」（默认 ``shanghai_now()``）；仅用于 ``after_close`` 的
        自动判定，便于测试注入固定时点。

    Returns
    -------
    ScanResult
    """
    exclude, non_hit = Counter(), Counter()
    hits: list[ScanHit] = []
    across_suspension_hits: list[ScanHit] = []

    _now = now or shanghai_now()
    last_dates = [
        _normalize_date(k["trade_date"].values[-1])
        for k in stock_kline_map.values()
        if k is not None and not k.empty and "trade_date" in k.columns
    ]
    # 全池最新 bar 日期 = 报告的数据截止日（报告层不得把它读作「报告生成日」）
    data_as_of = max(last_dates) if last_dates else ""

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
        if after_close is None:
            effective_after_close = _resolve_after_close(
                _normalize_date(kline["trade_date"].values[-1]), _now,
            )
        else:
            effective_after_close = after_close
        try:
            hit, excl_reason, non_reason = _scan_stock(
                stock, kline, suspension_map, params, trade_cal,
                after_close=effective_after_close,
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
        data_as_of=data_as_of,
    )
