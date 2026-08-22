"""Shim: canonical implementation at skills/lib/nums.py. Backward compatible."""
from __future__ import annotations

from ._invest_path import ensure_skills_lib_on_path

ensure_skills_lib_on_path()

from nums import (  # noqa: E402, F401
    ONE_PER_WAN,
    ONE_PER_YI,
    QIAN_PER_YI,
    WAN_PER_YI,
    coalesce_field,
    fmt_amount,
    parse_shares_wan,
    row_value_or_last,
    safe_float,
)
