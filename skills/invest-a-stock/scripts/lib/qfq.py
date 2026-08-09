"""Shim: canonical implementation at skills/lib/qfq.py. Backward compatible."""
from __future__ import annotations

from ._invest_path import ensure_skills_lib_on_path

ensure_skills_lib_on_path()

from qfq import PRICE_COLS, apply_qfq, apply_qfq_rows  # noqa: E402, F401
