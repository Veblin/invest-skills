"""Shim: canonical implementation at skills/lib/data_util.py. Backward compatible."""
from __future__ import annotations

from ._invest_path import ensure_skills_lib_on_path

ensure_skills_lib_on_path()

from data_util import has_data, merge_first_non_empty  # noqa: E402, F401
