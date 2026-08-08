"""Shim: canonical implementation at skills/lib/stats.py. Backward compatible."""
from __future__ import annotations

from ._invest_path import ensure_skills_lib_on_path

ensure_skills_lib_on_path()

from stats import (  # noqa: E402, F401
    calc_beta,
    median,
    percentile_rank,
    percentile_rank_inclusive,
    percentile_rank_mid,
)
