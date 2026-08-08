"""Shim: canonical implementation at skills/lib/kline_cache.py. Backward compatible."""
from __future__ import annotations

from ._invest_path import ensure_skills_lib_on_path

ensure_skills_lib_on_path()

from kline_cache import KlineTTLCache  # noqa: E402, F401
