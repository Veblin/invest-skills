"""Shim: canonical implementation at skills/lib/market_pulse.py. Backward compatible."""
from __future__ import annotations

from ._invest_path import ensure_skills_lib_on_path

ensure_skills_lib_on_path()

from market_pulse import fetch_margin_account_info  # noqa: E402, F401
