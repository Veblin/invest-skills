"""Data source abstraction for fetching K-line data.

Provides two implementations:

- **TushareBulkSource** — fetches daily bars by ``trade_date`` (one call
  returns *all* stocks for that date).  Uses ``pro.daily`` for raw bars and
  ``pro.adj_factor`` for qian-fu-quan factors.  Requires a Tushare token.

- **BaostockSource** — fetches per-stock daily bars via baostock with
  ``adjustflag="2"`` (qian-fu-quan).  No token required, but roughly
  1–2 s / stock; uses a global lock for baostock login.

A factory function :func:`create_source` selects the appropriate source
based on availability or explicit user choice.

Usage::

    from kline_source import create_source, build_stock_kline

    src = create_source("auto")          # Tushare if token available, else baostock
    daily_all = src.fetch_daily_batch(trade_dates)

    for ts_code in universe_ts_codes:
        adj = src.fetch_adj_factor(ts_code)
        kline = build_stock_kline(daily_all, adj, ts_code, min_bars=120)
        if kline is not None:
            ...  # scan for gaps
"""

from __future__ import annotations

import logging
import threading
from abc import ABC, abstractmethod
from typing import Any

import pandas as pd

from _invest_path import ensure_invest_a_scripts_on_path

ensure_invest_a_scripts_on_path()
from qfq import apply_qfq  # noqa: E402  (same-directory import)
from lib.proxy import proxy_bypass  # noqa: E402

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Global lock for baostock (single-threaded login / query)
# ---------------------------------------------------------------------------

_BAOSTOCK_LOCK = threading.Lock()

# ---------------------------------------------------------------------------
# Abstract base class
# ---------------------------------------------------------------------------


class KlineSource(ABC):
    """Abstract data source for daily K-line fetching.

    Concrete subclasses must implement :meth:`fetch_daily_batch`,
    :meth:`fetch_adj_factor`, and :meth:`source_name`.
    """

    @abstractmethod
    def fetch_daily_batch(self, trade_dates: list[str]) -> pd.DataFrame:
        """Fetch daily K-line data for **all** stocks on given trade dates.

        Args:
            trade_dates: List of trade dates in ``yyyymmdd`` format, sorted
                ascending.

        Returns:
            DataFrame with columns ``[ts_code, trade_date, open, high, low,
            close, vol, amount]``.  Returns an empty DataFrame on complete
            failure; partial results (some dates) are returned with a
            warning logged.
        """
        ...

    @abstractmethod
    def fetch_adj_factor(self, ts_code: str) -> pd.DataFrame | None:
        """Fetch the adjustment factor series for a single stock.

        Args:
            ts_code: Tushare-format stock code (e.g. ``"600176.SH"``).

        Returns:
            DataFrame with columns ``[trade_date, adj_factor]``, or
            ``None`` if the source does not provide adj factors (or
            on error).
        """
        ...

    @abstractmethod
    def source_name(self) -> str:
        """A human-readable name for this data source.

        Returns:
            ``"tushare"``, ``"baostock"``, etc.
        """
        ...

    def fetch_adj_factor_batch(self, trade_dates: list[str]) -> pd.DataFrame:
        """Fetch adjustment factors for all stocks on each trade date.

        Default returns an empty DataFrame.  Tushare overrides with
        ``pro.adj_factor(trade_date=…)`` bulk calls.
        """
        return pd.DataFrame()


# ---------------------------------------------------------------------------
# TushareBulkSource
# ---------------------------------------------------------------------------


class TushareBulkSource(KlineSource):
    """Bulk daily K-line fetcher using Tushare Pro.

    Monkey-patches ``RATE_LIMIT_PER_MINUTE`` and ``DAILY_CALL_LIMIT`` in
    ``tushare_client`` to 180 and 5000 respectively (appropriate for a
    2000-point Tushare account operating in an isolated process).

    ``fetch_daily_batch`` calls ``pro.daily(trade_date=…)`` once per date
    and returns the union of all stocks.  ``fetch_adj_factor`` calls
    ``pro.adj_factor(ts_code=…)`` per stock.
    """

    def __init__(self) -> None:
        # Monkey-patch rate limits for 2000-point account
        import lib.tushare_client as tc  # noqa: F811

        tc.RATE_LIMIT_PER_MINUTE = 180
        tc.DAILY_CALL_LIMIT = 5000

        from lib.tushare_client import TushareClient  # noqa: F811

        self._client = TushareClient(token=None)
        if not self._client.is_available():
            logger.warning(
                "TushareBulkSource: TushareClient reports unavailable — "
                "queries will likely return empty DataFrames"
            )

    def fetch_daily_batch(self, trade_dates: list[str]) -> pd.DataFrame:
        """Fetch daily bars for all stocks on each trade date.

        Iterates over *trade_dates* and calls ``pro.daily(trade_date=…)``
        for each.  Drops the ``pre_close``, ``change``, and ``pct_chg``
        columns returned by Tushare and converts ``amount`` from 千元
        to 元 (multiplies by 1000).

        Args:
            trade_dates: Sorted list of trade dates in ``yyyymmdd`` format.

        Returns:
            DataFrame with columns ``[ts_code, trade_date, open, high, low,
            close, vol, amount]``.  Empty if *trade_dates* is empty or all
            date queries failed.
        """
        if not trade_dates:
            logger.warning("TushareBulkSource.fetch_daily_batch: empty trade_dates")
            return pd.DataFrame()

        frames: list[pd.DataFrame] = []
        total = len(trade_dates)
        for i, date_str in enumerate(trade_dates):
            try:
                df = self._client.query("daily", trade_date=date_str)
            except Exception as exc:
                logger.warning(
                    "Tushare daily failed for %s: %s", date_str, exc
                )
                continue

            if df is None or df.empty:
                logger.debug("Tushare daily empty for %s", date_str)
                continue

            frames.append(df)
            # Progress every 50 dates or at the end
            if (i + 1) % 50 == 0 or i == total - 1:
                logger.info(
                    "Tushare 日线拉取进度: %d / %d (成功 %d 日)",
                    i + 1, total, len(frames),
                )

        if not frames:
            logger.warning(
                "TushareBulkSource: no data returned for any trade date"
            )
            return pd.DataFrame()

        combined = pd.concat(frames, ignore_index=True)

        # Keep only required columns
        keep_cols = [
            "ts_code",
            "trade_date",
            "open",
            "high",
            "low",
            "close",
            "vol",
            "amount",
        ]
        existing = [c for c in keep_cols if c in combined.columns]
        combined = combined[existing].copy()

        # Convert amount from 千元 to 元
        if "amount" in combined.columns:
            combined["amount"] = combined["amount"].astype(float) * 1000.0

        logger.info(
            "TushareBulkSource: fetched %d rows across %d dates",
            len(combined),
            len(frames),
        )
        return combined

    def fetch_adj_factor(self, ts_code: str) -> pd.DataFrame | None:
        """Fetch the adjustment factor series for one stock.

        Prefer :meth:`fetch_adj_factor_batch` on the main scan path.
        Kept for compatibility / debugging.

        Args:
            ts_code: Stock code in Tushare format (e.g. ``"600176.SH"``).

        Returns:
            DataFrame with columns ``[trade_date, adj_factor]``, or
            ``None`` if empty or on error.
        """
        try:
            df = self._client.query("adj_factor", ts_code=ts_code)
        except Exception as exc:
            logger.warning(
                "Tushare adj_factor failed for %s: %s", ts_code, exc
            )
            return None

        if df is None or df.empty:
            logger.debug("Tushare adj_factor empty for %s", ts_code)
            return None

        needed = ["trade_date", "adj_factor"]
        if not all(c in df.columns for c in needed):
            logger.warning(
                "Tushare adj_factor for %s: missing columns (got %s)",
                ts_code,
                list(df.columns),
            )
            return None

        return df[needed].copy()

    def fetch_adj_factor_batch(self, trade_dates: list[str]) -> pd.DataFrame:
        """Fetch adj_factor for all stocks via ``pro.adj_factor(trade_date=…)``.

        Args:
            trade_dates: Sorted list of trade dates in ``yyyymmdd`` format.

        Returns:
            DataFrame with columns ``[ts_code, trade_date, adj_factor]``.
            Empty if *trade_dates* is empty or all queries failed.
        """
        if not trade_dates:
            return pd.DataFrame()

        frames: list[pd.DataFrame] = []
        total = len(trade_dates)
        for i, date_str in enumerate(trade_dates):
            try:
                df = self._client.query("adj_factor", trade_date=date_str)
            except Exception as exc:
                logger.warning(
                    "Tushare adj_factor batch failed for %s: %s", date_str, exc
                )
                continue
            if df is None or df.empty:
                continue
            frames.append(df)
            # Progress every 50 dates or at the end
            if (i + 1) % 50 == 0 or i == total - 1:
                logger.info(
                    "Tushare 复权因子拉取进度: %d / %d (成功 %d 日)",
                    i + 1, total, len(frames),
                )

        if not frames:
            logger.warning(
                "TushareBulkSource: no adj_factor returned for any trade date"
            )
            return pd.DataFrame()

        combined = pd.concat(frames, ignore_index=True)
        needed = ["ts_code", "trade_date", "adj_factor"]
        existing = [c for c in needed if c in combined.columns]
        if set(needed) - set(existing):
            logger.warning(
                "Tushare adj_factor batch missing columns (got %s)",
                list(combined.columns),
            )
            return pd.DataFrame()

        logger.info(
            "TushareBulkSource: fetched adj_factor %d rows across %d dates",
            len(combined),
            len(frames),
        )
        return combined[needed].copy()

    def source_name(self) -> str:
        return "tushare"


# ---------------------------------------------------------------------------
# BaostockSource
# ---------------------------------------------------------------------------


class BaostockSource(KlineSource):
    """Per-stock K-line fetcher using baostock (adjustflag="2" = qian-fu-quan).

    Baostock requires per-stock queries — there is no bulk-by-date endpoint.
    This class accepts the universe ``ts_codes`` at construction time and
    fetches each one's full daily history within the requested date range.

    Uses a global lock (``_BAOSTOCK_LOCK``) to serialise baostock login
    and queries.  The lock is held only for I/O operations, not across
    the entire batch.

    Prices returned by baostock with ``adjustflag="2"`` are already
    qian-fu-quan adjusted, so :meth:`fetch_adj_factor` returns ``None``
    and the caller should set ``already_qfq=True`` when calling
    :func:`build_stock_kline`.
    """

    def __init__(self, ts_codes: list[str] | None = None) -> None:
        self._ts_codes: list[str] = list(ts_codes) if ts_codes else []
        self._logged_in = False
        self._bs: Any = None  # baostock module (lazy import)
        logger.info(
            "BaostockSource initialised with %d stocks",
            len(self._ts_codes),
        )

    def set_ts_codes(self, ts_codes: list[str]) -> None:
        """Set or update the list of stock codes to fetch.

        Args:
            ts_codes: List of Tushare-format stock codes.
        """
        self._ts_codes = list(ts_codes)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _ts_code_to_baostock(ts_code: str) -> str:
        """Convert a Tushare ``ts_code`` to baostock format.

        ``"600176.SH"`` → ``"sh.600176"``
        ``"000001.SZ"`` → ``"sz.000001"``

        Args:
            ts_code: Tushare format stock code.

        Returns:
            Baostock format stock code.
        """
        parts = ts_code.strip().split(".")
        if len(parts) != 2:
            logger.warning(
                "Cannot convert %s to baostock format, using as-is", ts_code
            )
            return ts_code
        return f"{parts[1].lower()}.{parts[0]}"

    def _ensure_logged_in(self) -> None:
        """Ensure baostock is logged in (thread-safe with global lock)."""
        if self._logged_in:
            return
        with _BAOSTOCK_LOCK, proxy_bypass():
            if self._logged_in:
                return
            import baostock as bs  # noqa: F811

            self._bs = bs
            lg = self._bs.login()
            if lg.error_code != "0":
                raise RuntimeError(
                    f"Baostock login failed: {lg.error_msg}"
                )
            self._logged_in = True
            logger.info("Baostock logged in")

    def _fetch_one_stock(
        self,
        ts_code: str,
        start_date: str,
        end_date: str,
    ) -> pd.DataFrame | None:
        """Fetch daily bars for a single stock from baostock.

        Args:
            ts_code: Tushare-format stock code.
            start_date: Earliest date in ``yyyymmdd`` format.
            end_date: Latest date in ``yyyymmdd`` format.

        Returns:
            DataFrame with columns ``[ts_code, trade_date, open, high, low,
            close, vol, amount]``, or ``None`` on failure.
        """
        bs_code = self._ts_code_to_baostock(ts_code)

        # Baostock expects YYYY-MM-DD; convert from yyyymmdd
        start_fmt = f"{start_date[:4]}-{start_date[4:6]}-{start_date[6:8]}"
        end_fmt = f"{end_date[:4]}-{end_date[4:6]}-{end_date[6:8]}"

        with _BAOSTOCK_LOCK, proxy_bypass():
            try:
                rs = self._bs.query_history_k_data_plus(
                    bs_code,
                    "date,open,high,low,close,volume,amount",
                    start_date=start_fmt,
                    end_date=end_fmt,
                    frequency="d",
                    adjustflag="2",  # qian-fu-quan
                )
            except Exception as exc:
                logger.warning(
                    "Baostock query failed for %s: %s", ts_code, exc
                )
                return None

            if rs is None:
                logger.warning("Baostock query returned None for %s", ts_code)
                return None
            if rs.error_code != "0":
                logger.warning(
                    "Baostock error for %s: code=%s msg=%s",
                    ts_code,
                    rs.error_code,
                    rs.error_msg,
                )
                return None

            # Collect rows
            rows: list[list[str]] = []
            while (rs.error_code == "0") and rs.next():
                rows.append(rs.get_row_data())

        if not rows:
            logger.debug("Baostock returned no data for %s", ts_code)
            return None

        df = pd.DataFrame(rows, columns=rs.fields)

        # Rename and select columns
        rename_map = {
            "date": "trade_date",
            "volume": "vol",
        }
        df.rename(columns=rename_map, inplace=True)

        # Normalize trade_date to yyyymmdd (baostock returns YYYY-MM-DD)
        if "trade_date" in df.columns:
            df["trade_date"] = (
                df["trade_date"].astype(str).str.replace("-", "", regex=False)
            )

        # Convert numeric columns
        numeric_cols = ["open", "high", "low", "close", "vol", "amount"]
        for col in numeric_cols:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        # Add ts_code
        df["ts_code"] = ts_code

        # Ensure column ordering
        result_cols = [
            "ts_code",
            "trade_date",
            "open",
            "high",
            "low",
            "close",
            "vol",
            "amount",
        ]
        existing = [c for c in result_cols if c in df.columns]
        return df[existing]

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def fetch_daily_batch(self, trade_dates: list[str]) -> pd.DataFrame:
        """Fetch daily bars for all configured stocks from baostock.

        Because baostock has no bulk-by-date API, this method iterates
        over the internally stored ``ts_codes`` and fetches each one's
        full history between the first and last trade dates.

        Prices are already qian-fu-quan adjusted (``adjustflag="2"``),
        so :meth:`fetch_adj_factor` returns ``None`` and callers should
        set ``already_qfq=True`` in :func:`build_stock_kline`.

        Args:
            trade_dates: Sorted list of trade dates in ``yyyymmdd`` format.
                Only the earliest and latest are used as the date range.

        Returns:
            DataFrame with columns ``[ts_code, trade_date, open, high, low,
            close, vol, amount]``.  Empty if no stocks configured or all
            queries failed.
        """
        if not self._ts_codes:
            logger.warning("BaostockSource: no ts_codes configured")
            return pd.DataFrame()

        if not trade_dates:
            logger.warning("BaostockSource: empty trade_dates")
            return pd.DataFrame()

        start_date = trade_dates[0]
        end_date = trade_dates[-1]

        self._ensure_logged_in()

        frames: list[pd.DataFrame] = []
        total = len(self._ts_codes)
        for i, ts_code in enumerate(self._ts_codes):
            df = self._fetch_one_stock(ts_code, start_date, end_date)
            if df is not None and not df.empty:
                frames.append(df)
            # Progress every 50 stocks or at the end
            if (i + 1) % 50 == 0 or i == total - 1:
                logger.info(
                    "Baostock 拉取进度: %d / %d (成功 %d)",
                    i + 1, total, len(frames),
                )

        if not frames:
            logger.warning("BaostockSource: no data returned for any stock")
            return pd.DataFrame()

        combined = pd.concat(frames, ignore_index=True)

        logger.info(
            "BaostockSource: fetched %d rows for %d stocks",
            len(combined),
            len(frames),
        )
        return combined

    def fetch_adj_factor(self, ts_code: str) -> pd.DataFrame | None:
        """Return ``None`` — baostock already provides qfq-adjusted prices.

        Args:
            ts_code: Ignored (returns ``None`` unconditionally).

        Returns:
            Always ``None``.
        """
        return None

    def source_name(self) -> str:
        return "baostock"

    def cleanup(self) -> None:
        """Log out from baostock (idempotent).  Call at end of session."""
        if not self._logged_in:
            return
        try:
            with _BAOSTOCK_LOCK, proxy_bypass():
                if self._logged_in and self._bs is not None:
                    self._bs.logout()
                    self._logged_in = False
                    logger.info("Baostock logged out")
        except Exception as exc:
            logger.warning("Baostock logout error: %s", exc)

    def __enter__(self) -> BaostockSource:
        return self

    def __exit__(self, *args: Any) -> None:
        self.cleanup()


# ---------------------------------------------------------------------------
# Factory function
# ---------------------------------------------------------------------------


def create_source(source: str = "auto", ts_codes: list[str] | None = None) -> KlineSource:
    """Create an appropriate :class:`KlineSource` based on availability.

    Args:
        source: One of:
            - ``"auto"``: try Tushare first, fall back to baostock.
            - ``"tushare"``: force TushareBulkSource.
            - ``"baostock"``: force BaostockSource.
        ts_codes: List of stock codes (required only for baostock path
            so it knows which stocks to fetch).  Ignored for Tushare.

    Returns:
        A :class:`KlineSource` instance ready for data fetching.

    Raises:
        ValueError: If *source* is ``"auto"`` and neither Tushare nor
            baostock is available.
    """
    if source == "tushare":
        return TushareBulkSource()
    if source == "baostock":
        return BaostockSource(ts_codes=ts_codes or [])

    # auto: Tushare first, then baostock
    # Defer expensive import until needed
    import lib.tushare_client as tc  # noqa: F811

    from lib.tushare_client import TushareClient  # noqa: F811

    # Check Tushare token without calling the API (avoid rate-limit noise)
    from lib import env  # noqa: F811

    config = env.get_config()
    if config.get("TUSHARE_TOKEN"):
        with TushareClient(token=None) as test_client:
            if test_client.is_available():
                return TushareBulkSource()

    # Fall back to baostock
    try:
        import baostock  # noqa: F401

        logger.info(
            "create_source: Tushare unavailable, falling back to baostock "
            "(%d ts_codes provided)",
            len(ts_codes or []),
        )
        return BaostockSource(ts_codes=ts_codes or [])
    except ImportError:
        raise ValueError(
            "No data source available: Tushare unavailable and baostock "
            "not installed.  Install baostock or set TUSHARE_TOKEN."
        )


# ---------------------------------------------------------------------------
# Utility: build per-stock K-line
# ---------------------------------------------------------------------------


def build_stock_kline(
    daily_df: pd.DataFrame,
    adj_factor_df: pd.DataFrame | None,
    ts_code: str,
    min_bars: int = 120,
    already_qfq: bool = False,
) -> pd.DataFrame | None:
    """Extract one stock's daily bars, apply qfq adjustment, and validate.

    Args:
        daily_df: Full daily data for **all** stocks (as returned by
            :meth:`KlineSource.fetch_daily_batch`).
        adj_factor_df: Adjustment factor series for this stock, or
            ``None`` if the source does not provide factors.
        ts_code: The stock code to extract (e.g. ``"600176.SH"``).
        min_bars: Minimum number of bars required.  If the stock has
            fewer bars than this, returns ``None``.
        already_qfq: If ``True``, the prices in *daily_df* are already
            qian-fu-quan adjusted (e.g. baostock ``adjustflag="2"``).
            The ``*_qfq`` columns will simply mirror the original price
            columns.  If ``False`` (default), ``apply_qfq`` is called
            with *adj_factor_df* and the result must be non-``None``.

    Returns:
        A DataFrame with columns ``[trade_date, open, high, low, close,
        vol, amount, open_qfq, high_qfq, low_qfq, close_qfq]``, sorted
        by ``trade_date`` ascending.  Returns ``None`` if:

        - The stock has no rows in *daily_df* (or fewer than *min_bars*).
        - ``already_qfq`` is ``False`` and *adj_factor_df* is ``None``
          (missing adj factor — the stock is skipped).
        - ``apply_qfq`` fails (returns ``None``).
    """
    if daily_df is None or daily_df.empty:
        return None

    # Filter by ts_code
    stock_df = daily_df[daily_df["ts_code"] == ts_code].copy()
    if stock_df.empty:
        logger.debug("build_stock_kline: no data for %s", ts_code)
        return None

    # Sort by trade_date ascending
    if "trade_date" in stock_df.columns:
        stock_df.sort_values("trade_date", ascending=True, inplace=True)
        stock_df.reset_index(drop=True, inplace=True)

    # Ensure required columns exist
    price_cols = ["open", "high", "low", "close"]
    for c in price_cols + ["trade_date", "vol", "amount"]:
        if c not in stock_df.columns:
            logger.warning(
                "build_stock_kline for %s: missing column %r", ts_code, c
            )
            return None

    # Apply qfq or copy prices
    if already_qfq:
        # Prices are already qfq-adjusted; copy to *_qfq columns
        for col in price_cols:
            stock_df[f"{col}_qfq"] = stock_df[col]
    else:
        # Need adj_factor
        if adj_factor_df is None:
            logger.debug(
                "build_stock_kline for %s: adj_factor is None (skipping)",
                ts_code,
            )
            return None

        merged = apply_qfq(stock_df, adj_factor_df)
        if merged is None:
            logger.debug(
                "build_stock_kline for %s: apply_qfq returned None "
                "(adj_factor missing or non-overlapping)",
                ts_code,
            )
            return None
        stock_df = merged

    # Check minimum bar count
    if len(stock_df) < min_bars:
        logger.debug(
            "build_stock_kline for %s: %d bars < %d min (skipping)",
            ts_code,
            len(stock_df),
            min_bars,
        )
        return None

    # Select final columns (trade_date, OHLC, vol, amount, OHLC_qfq)
    final_cols = [
        "trade_date",
        "open",
        "high",
        "low",
        "close",
        "vol",
        "amount",
        "open_qfq",
        "high_qfq",
        "low_qfq",
        "close_qfq",
    ]
    existing = [c for c in final_cols if c in stock_df.columns]
    return stock_df[existing].reset_index(drop=True)
