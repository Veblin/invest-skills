"""Re-export shared code helpers from ``skills/lib/codes.py``.

Canonical implementation lives in skills/lib; this module only bootstraps
the path and re-exports for invest-a-stock internal imports.
"""

from __future__ import annotations

from ._invest_path import ensure_skills_lib_on_path

ensure_skills_lib_on_path()

from codes import (  # noqa: E402
    classify_board,
    etf_symbol_to_ts_code,
    exchange_code,
    is_st_or_delisted,
    market_label,
    symbol_to_ts_code,
    ts_code_to_baostock,
)

__all__ = [
    "symbol_to_ts_code",
    "exchange_code",
    "classify_board",
    "market_label",
    "is_st_or_delisted",
    "ts_code_to_baostock",
    "etf_symbol_to_ts_code",
]
