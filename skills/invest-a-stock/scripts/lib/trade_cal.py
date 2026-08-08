"""Shim: canonical implementation at skills/lib/trade_cal.py. Backward compatible."""
from __future__ import annotations

from ._invest_path import ensure_skills_lib_on_path

ensure_skills_lib_on_path()

from trade_cal import (  # noqa: E402, F401
    fetch_trade_cal,
    last_trade_dates,
    next_trading_day,
    prev_trading_day,
)
