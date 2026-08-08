"""Shim: canonical implementation at skills/lib/version.py. Backward compatible."""
from __future__ import annotations

from ._invest_path import ensure_skills_lib_on_path

ensure_skills_lib_on_path()

from version import get_package_version  # noqa: E402, F401
