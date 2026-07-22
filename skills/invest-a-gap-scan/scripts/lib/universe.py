"""Stock universe builder for invest-a-gap-scan.

Builds a deduplicated stock universe from index constituent union
(CSI 300 / CSI A500 / STAR 50), enriched with board classification,
and cached to disk.

Typical usage::

    from universe import build_universe
    universe = build_universe()
"""

from __future__ import annotations

import logging
import pickle
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

from _invest_path import ensure_invest_a_scripts_on_path

ensure_invest_a_scripts_on_path()
from codes import classify_board, symbol_to_ts_code  # noqa: E402
from lib import env  # noqa: E402
from lib.proxy import akshare_direct_session  # noqa: E402
from lib.tushare_client import TushareClient  # noqa: E402

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Index configurations
# ---------------------------------------------------------------------------

_INDICES_CONFIG: dict[str, dict[str, str | None]] = {
    "csi300": {
        "akshare": "000300",
        "sina": "000300",
        "tushare": "000300.SH",
    },
    "a500": {
        "akshare": "000510",
        "sina": None,
        "tushare": "000510.CSI",
    },
    "star50": {
        "akshare": "000688",
        "sina": None,
        "tushare": "000688.SH",
    },
}

DEFAULT_INDICES = ["csi300", "a500", "star50"]

# ---------------------------------------------------------------------------
# StockInfo dataclass
# ---------------------------------------------------------------------------


@dataclass
class StockInfo:
    """A stock in the scan universe.

    Attributes:
        ts_code: Tushare-format stock code (e.g. ``"600176.SH"``).
        name: Stock short name (e.g. ``"中国巨石"``).
        index_membership: Which index(s) this stock belongs to
            (e.g. ``["csi300", "a500"]``).
        board: Board classification — one of ``"主板"``, ``"创业板"``,
            ``"科创板"``.
        list_date: Listing date in ``yyyymmdd`` format (e.g. ``"19990422"``).
    """

    ts_code: str
    name: str
    index_membership: list[str]
    board: str
    list_date: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Stock code helpers (skills/lib/codes.py)
# ---------------------------------------------------------------------------


def _filter_st_and_delist(name: str) -> bool:
    """Check whether a stock name indicates ST or delisting status.

    Args:
        name: Stock short name.

    Returns:
        ``True`` if the stock should be **excluded** from the universe.
    """
    if not name:
        return False
    if "ST" in name.upper():
        return True
    if "退" in name:
        return True
    return False


# ---------------------------------------------------------------------------
# Cache functions
# ---------------------------------------------------------------------------


def _cache_path(date_str: str | None = None) -> Path:
    """Resolve the cache file path for a given date.

    Path: ``{STORE_DIR}/gap_scan_cache/universe_{yyyymmdd}.pkl``

    Args:
        date_str: Date in ``yyyymmdd`` format.  Defaults to today.

    Returns:
        Absolute path to the cache pickle file.
    """
    if date_str is None:
        date_str = date.today().strftime("%Y%m%d")
    return env.STORE_DIR / "gap_scan_cache" / f"universe_{date_str}.pkl"


def _load_cache(path: Path) -> list[StockInfo] | None:
    """Load a cached stock universe from pickle.

    Args:
        path: Cache file path.

    Returns:
        List of :class:`StockInfo` if the cache exists and is valid,
        ``None`` on miss or corruption.
    """
    if not path.exists():
        return None
    try:
        with open(path, "rb") as f:
            data = pickle.load(f)
        if isinstance(data, list) and (
            not data or isinstance(data[0], StockInfo)
        ):
            logger.info("Loaded universe from cache: %s", path)
            return data
        logger.warning("Cache has unexpected type in %s, discarding", path)
        return None
    except Exception as exc:
        logger.warning("Failed to load universe cache %s: %s", path, exc)
        return None


def _save_cache(path: Path, universe: list[StockInfo]) -> None:
    """Save a stock universe to pickle cache.

    Args:
        path: Cache file path.
        universe: List of StockInfo to persist.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(path, "wb") as f:
            pickle.dump(universe, f)
        logger.info(
            "Saved universe cache: %s (%d stocks)", path, len(universe)
        )
    except Exception as exc:
        logger.warning("Failed to save universe cache %s: %s", path, exc)


# ---------------------------------------------------------------------------
# Index constituent fetching
# ---------------------------------------------------------------------------


def _fetch_index_akshare(
    index_key: str,
    config: dict[str, str | None],
) -> pd.DataFrame | None:
    """Fetch index constituents via akshare ``index_stock_cons``.

    Args:
        index_key: Human-readable index name (``"csi300"`` etc.).
        config: Index configuration dict from :data:`_INDICES_CONFIG`.

    Returns:
        DataFrame with index constituents, or ``None`` on failure.
    """
    ak_code = config.get("akshare")
    if not ak_code:
        return None

    import akshare as ak  # noqa: F811

    try:
        with akshare_direct_session():
            df = ak.index_stock_cons(symbol=ak_code)
        if df is None or df.empty:
            logger.warning(
                "akshare index_stock_cons returned empty for %s", index_key
            )
            return None
        logger.info(
            "akshare index_stock_cons %s (code=%s): %d rows",
            index_key,
            ak_code,
            len(df),
        )
        return df
    except Exception as exc:
        logger.warning(
            "akshare index_stock_cons failed for %s: %s", index_key, exc
        )
        return None


def _fetch_index_sina(
    index_key: str,
    config: dict[str, str | None],
) -> pd.DataFrame | None:
    """Fetch index constituents via akshare ``index_stock_cons_sina``.

    Only works for CSI 300 (``sina`` config present).

    Args:
        index_key: Human-readable index name.
        config: Index configuration dict from :data:`_INDICES_CONFIG`.

    Returns:
        DataFrame with index constituents, or ``None`` on failure.
    """
    sina_code = config.get("sina")
    if not sina_code:
        return None

    import akshare as ak  # noqa: F811

    try:
        with akshare_direct_session():
            df = ak.index_stock_cons_sina(symbol=sina_code)
        if df is None or df.empty:
            logger.warning(
                "akshare index_stock_cons_sina returned empty for %s",
                index_key,
            )
            return None
        logger.info(
            "akshare index_stock_cons_sina %s (code=%s): %d rows",
            index_key,
            sina_code,
            len(df),
        )
        return df
    except Exception as exc:
        logger.warning(
            "akshare index_stock_cons_sina failed for %s: %s",
            index_key,
            exc,
        )
        return None


def _fetch_index_tushare(
    index_key: str,
    config: dict[str, str | None],
    client: TushareClient,
) -> pd.DataFrame | None:
    """Fetch index constituents via Tushare ``index_weight``.

    Args:
        index_key: Human-readable index name.
        config: Index configuration dict from :data:`_INDICES_CONFIG`.
        client: An authenticated :class:`TushareClient` instance.

    Returns:
        DataFrame with columns ``con_code`` etc., or ``None`` on failure.
    """
    ts_code = config.get("tushare")
    if not ts_code:
        return None

    try:
        df = client.query("index_weight", index_code=ts_code)
        if df is None or df.empty:
            logger.warning(
                "Tushare index_weight returned empty for %s", index_key
            )
            return None
        logger.info(
            "Tushare index_weight %s (code=%s): %d rows",
            index_key,
            ts_code,
            len(df),
        )
        return df
    except Exception as exc:
        logger.warning(
            "Tushare index_weight failed for %s: %s", index_key, exc
        )
        return None


# ---------------------------------------------------------------------------
# Code extraction
# ---------------------------------------------------------------------------


def _extract_codes_from_akshare_df(df: pd.DataFrame) -> set[str]:
    """Extract 6-digit stock codes from an akshare index constituent DataFrame.

    akshare ``index_stock_cons`` returns a DataFrame with columns
    ``品种代码``, ``品种名称``, ``纳入日期``.
    """
    codes: set[str] = set()
    for col in ("品种代码", "code"):
        if col in df.columns:
            for val in df[col]:
                raw = str(val).strip()
                if raw and len(raw) >= 6:
                    codes.add(raw[:6])
            return codes

    # Fallback: try the first string-like column
    for col in df.columns:
        if any(kw in col for kw in ("code", "代码", "代")):
            for val in df[col]:
                raw = str(val).strip()
                if raw and len(raw) >= 6:
                    codes.add(raw[:6])
            return codes

    # Last resort: try the first column
    for val in df.iloc[:, 0]:
        raw = str(val).strip()
        if raw and len(raw) >= 6:
            codes.add(raw[:6])
    return codes


def _extract_codes_from_tushare_df(df: pd.DataFrame) -> set[str]:
    """Extract 6-digit stock codes from a Tushare ``index_weight`` DataFrame.

    Tushare ``index_weight`` returns a DataFrame with a ``con_code`` column
    in ``ts_code`` format (e.g. ``"600176.SH"``).
    """
    codes: set[str] = set()
    col = "con_code"
    if col in df.columns:
        for val in df[col]:
            raw = str(val).strip()
            base = raw.split(".")[0]
            if base and len(base) >= 6:
                codes.add(base)
        return codes

    # Fallback: try first column
    for val in df.iloc[:, 0]:
        raw = str(val).strip()
        base = raw.split(".")[0]
        if base and len(base) >= 6:
            codes.add(base)
    return codes


# ---------------------------------------------------------------------------
# Enrichment
# ---------------------------------------------------------------------------


def _enrich_with_tushare(
    stock_map: dict[str, dict[str, Any]],
    client: TushareClient,
) -> None:
    """Enrich stock entries with name, market, list_date from Tushare stock_basic.

    Args:
        stock_map: Mutable dict of ``ts_code → metadata dict``.
        client: An authenticated :class:`TushareClient` instance.
    """
    try:
        df = client.query(
            "stock_basic",
            fields="ts_code,name,market,list_date",
        )
        if df is None or df.empty:
            logger.warning("Tushare stock_basic returned empty, skipping enrichment")
            return

        for _, row in df.iterrows():
            ts_code = str(row.get("ts_code", "")).strip()
            if ts_code not in stock_map:
                continue
            name_val = row.get("name", "")
            if pd.notna(name_val):
                stock_map[ts_code]["name"] = str(name_val)
            market_val = row.get("market", "")
            if pd.notna(market_val):
                stock_map[ts_code]["market"] = str(market_val)
            list_date_val = row.get("list_date", "")
            if pd.notna(list_date_val):
                stock_map[ts_code]["list_date"] = str(list_date_val)

    except Exception as exc:
        logger.warning("Tushare stock_basic enrichment failed: %s", exc)


def _enrich_with_akshare(stock_map: dict[str, dict[str, Any]]) -> None:
    """Fallback enrichment using akshare ``stock_info_a_code_name``.

    Only populates the ``name`` field; market and list_date remain empty.

    Args:
        stock_map: Mutable dict of ``ts_code → metadata dict``.
    """
    import akshare as ak  # noqa: F811

    try:
        with akshare_direct_session():
            df = ak.stock_info_a_code_name()
        if df is None or df.empty:
            logger.warning("akshare stock_info_a_code_name returned empty")
            return

        for _, row in df.iterrows():
            code_6d = str(row.get("code", "")).strip()
            name_val = row.get("name", "")
            if code_6d and pd.notna(name_val):
                ts_code = symbol_to_ts_code(code_6d)
                if ts_code in stock_map:
                    stock_map[ts_code]["name"] = str(name_val)

    except Exception as exc:
        logger.warning(
            "akshare stock_info_a_code_name enrichment failed: %s", exc
        )


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def build_universe(
    indices: list[str] | None = None,
    force_refresh: bool = False,
    universe_limit: int | None = None,
) -> list[StockInfo]:
    """Build the gap-scan stock universe from index constituent union.

    Workflow:

    1. **Check disk cache** — if a pickle for today's date exists and
       ``force_refresh`` is ``False``, return it directly.
    2. **Fetch index constituents** — for each index in *indices*, try
       sources in priority order (akshare → sina → Tushare).  Each source
       is independent; a failure on one index does not block the others.
    3. **Deduplicate** — union all stocks across indices, tracking which
       index(s) each stock belongs to.
    4. **Enrich** — fetch names, market, and list dates via Tushare
       ``stock_basic``, falling back to akshare ``stock_info_a_code_name``.
    5. **Filter** — exclude ST and delisting stocks by name.
    6. **Classify board** — ``market`` field from ``stock_basic``, or code
       prefix fallback (``688`` → 科创板, ``300``/``301`` → 创业板).
    7. **Cache** — persist the result list as pickle.
    8. **Return** — optionally trimmed to *universe_limit*.

    Args:
        indices: List of index keys.  Each key must exist in
            :data:`_INDICES_CONFIG`.  Defaults to
            ``["csi300", "a500", "star50"]``.
        force_refresh: If ``True``, bypass disk cache and re-fetch from
            upstream APIs.
        universe_limit: If set, return at most this many stocks (useful
            for development/debugging).

    Returns:
        List of :class:`StockInfo` objects, sorted by ``ts_code``.
        May be empty if no constituents could be loaded.
    """
    if indices is None:
        indices = DEFAULT_INDICES

    today_str = date.today().strftime("%Y%m%d")

    # ---- Cache check ----
    if not force_refresh:
        cached = _load_cache(_cache_path(today_str))
        if cached is not None:
            if universe_limit is not None:
                return cached[:universe_limit]
            return cached

    # ---- Prepare Tushare client ----
    client = TushareClient(token=None)
    tushare_available = client.is_available()

    # ---- Fetch index constituents ----
    # stock_map: ts_code → {ts_code, name, index_membership, market, list_date}
    stock_map: dict[str, dict[str, Any]] = {}

    for idx_key in indices:
        config = _INDICES_CONFIG.get(idx_key)
        if config is None:
            logger.warning("Unknown index key %r, skipping", idx_key)
            continue

        codes: set[str] = set()

        # Priority 1: akshare index_stock_cons
        df = _fetch_index_akshare(idx_key, config)
        if df is not None and not df.empty:
            codes = _extract_codes_from_akshare_df(df)

        # Priority 2: akshare index_stock_cons_sina (only csi300)
        if not codes:
            df = _fetch_index_sina(idx_key, config)
            if df is not None and not df.empty:
                codes = _extract_codes_from_akshare_df(df)

        # Priority 3: Tushare index_weight
        if not codes and tushare_available:
            df = _fetch_index_tushare(idx_key, config, client)
            if df is not None and not df.empty:
                codes = _extract_codes_from_tushare_df(df)

        if not codes:
            logger.warning(
                "All sources failed for index %r — no constituents loaded",
                idx_key,
            )
            continue

        # Record membership
        for code_6d in codes:
            ts_code = symbol_to_ts_code(code_6d)
            if ts_code not in stock_map:
                stock_map[ts_code] = {
                    "ts_code": ts_code,
                    "name": "",
                    "index_membership": [],
                    "market": "",
                    "list_date": "",
                }
            if idx_key not in stock_map[ts_code]["index_membership"]:
                stock_map[ts_code]["index_membership"].append(idx_key)

    if not stock_map:
        logger.error("No constituents loaded from any index — empty universe")
        return []

    logger.info(
        "Union of %d indices: %d unique stocks",
        len(indices),
        len(stock_map),
    )

    # ---- Enrich ----
    if tushare_available:
        _enrich_with_tushare(stock_map, client)
    else:
        _enrich_with_akshare(stock_map)

    # ---- Build final list with filtering ----
    result: list[StockInfo] = []
    for info in stock_map.values():
        name = info.get("name", "")
        if _filter_st_and_delist(name):
            continue

        board = classify_board(info["ts_code"], info.get("market", ""))

        si = StockInfo(
            ts_code=info["ts_code"],
            name=name or info["ts_code"],
            index_membership=info["index_membership"],
            board=board,
            list_date=info.get("list_date", ""),
        )
        result.append(si)

    # Sort by ts_code for deterministic ordering
    result.sort(key=lambda s: s.ts_code)

    logger.info(
        "Universe built: %d stocks (after ST/delist filter from %d raw)",
        len(result),
        len(stock_map),
    )

    # ---- Cache ----
    _save_cache(_cache_path(today_str), result)

    if universe_limit is not None:
        return result[:universe_limit]
    return result
