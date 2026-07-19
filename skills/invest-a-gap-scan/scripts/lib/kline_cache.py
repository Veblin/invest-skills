"""Pickle-based daily cache for K-line data.

Caches daily DataFrames on disk under ``{STORE_DIR}/gap_scan_cache/{yyyymmdd}/``
so that repeated scans on the same day do not re-fetch data from upstream
APIs.

Cache root resolves to ``~/.local/share/investment/gap_scan_cache/`` via
invest-a-stock's ``lib.env.STORE_DIR`` (cross-skill import).
"""

from __future__ import annotations

import pickle
import time
from datetime import date
from pathlib import Path

import pandas as pd

from _invest_path import ensure_invest_a_scripts_on_path

ensure_invest_a_scripts_on_path()
from lib import env  # noqa: E402


# ---- Constants ----

CACHE_TTL_DAYS = 3
"""Cache entries older than this (by file mtime) are considered expired."""


# ---- Internal helpers ----

def _cache_root() -> Path:
    """Return the cache root directory (``{STORE_DIR}/gap_scan_cache``)."""
    return env.STORE_DIR / "gap_scan_cache"


def _cache_dir(date_str: str) -> Path:
    """Return the cache subdirectory for a specific date.

    Args:
        date_str: Date string in ``yyyymmdd`` format (e.g. ``"20260719"``).

    Returns:
        ``{cache_root}/{date_str}``.
    """
    return _cache_root() / date_str


# ---- Public API ----


def save(ts_code: str, df: pd.DataFrame, date_str: str | None = None,
         source_name: str | None = None) -> None:
    """Save a DataFrame as a pickle to ``{cache_dir}/{source_name}/{ts_code}.pkl``.

    Args:
        ts_code: Stock code (e.g. ``"000001.SZ"``).
        df: DataFrame to cache.  Will be serialized via ``pickle.dump``.
        date_str: Date string in ``yyyymmdd`` format.  Defaults to today's
            date if not provided.
        source_name: Optional data source identifier (e.g. ``"tushare"``,
            ``"baostock"``).  When provided, the cache is stored in a
            source-specific subdirectory to prevent cross-source reuse.
    """
    if date_str is None:
        date_str = date.today().strftime("%Y%m%d")
    cache_dir = _cache_dir(date_str)
    if source_name:
        cache_dir = cache_dir / source_name
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / f"{ts_code}.pkl"
    with open(path, "wb") as f:
        pickle.dump(df, f)


def load(ts_code: str, date_str: str | None = None,
         source_name: str | None = None) -> pd.DataFrame | None:
    """Load a cached DataFrame if it exists and has not expired.

    Args:
        ts_code: Stock code (e.g. ``"000001.SZ"``).
        date_str: Date string in ``yyyymmdd`` format.  Defaults to today's
            date if not provided.
        source_name: Optional data source identifier (e.g. ``"tushare"``,
            ``"baostock"``).  When provided, looks in a source-specific
            subdirectory.

    Returns:
        The deserialized DataFrame if the pickle exists and its file mtime
        is younger than ``CACHE_TTL_DAYS`` days; ``None`` on cache miss
        or expiration.
    """
    if date_str is None:
        date_str = date.today().strftime("%Y%m%d")
    cache_dir = _cache_dir(date_str)
    if source_name:
        cache_dir = cache_dir / source_name
    path = cache_dir / f"{ts_code}.pkl"
    if not path.exists():
        return None

    # Check expiration based on file modification time
    mtime = path.stat().st_mtime
    age_days = (time.time() - mtime) / 86400
    if age_days > CACHE_TTL_DAYS:
        return None

    try:
        with open(path, "rb") as f:
            return pickle.load(f)
    except Exception:
        # Corrupt or truncated file — treat as cache miss.
        # Common causes: Ctrl-C during save, disk full, concurrent write.
        return None


def cleanup_old() -> None:
    """Remove cache directories older than ``CACHE_TTL_DAYS``.

    Iterates over all subdirectories of the cache root; any directory whose
    modification time exceeds the TTL is removed recursively.  Non-directory
    entries at the root level are ignored.
    """
    root = _cache_root()
    if not root.exists():
        return
    import shutil

    now = time.time()
    for entry in root.iterdir():
        if not entry.is_dir():
            continue
        age_days = (now - entry.stat().st_mtime) / 86400
        if age_days > CACHE_TTL_DAYS:
            shutil.rmtree(entry)
