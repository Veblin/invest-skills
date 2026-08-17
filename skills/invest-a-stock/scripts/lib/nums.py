"""Shim: canonical implementation at skills/lib/nums.py. Backward compatible."""
from __future__ import annotations

from ._invest_path import ensure_skills_lib_on_path

ensure_skills_lib_on_path()

from nums import safe_float, coalesce_field, fmt_amount, row_value_or_last  # noqa: E402, F401
