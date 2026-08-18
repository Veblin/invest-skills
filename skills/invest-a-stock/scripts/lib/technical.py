"""Shim: canonical implementation at skills/lib/technical.py. Backward compatible."""
from __future__ import annotations

from ._invest_path import ensure_skills_lib_on_path

ensure_skills_lib_on_path()

from technical import *  # noqa: E402, F403
from technical import _ema, _rsi, _ytd_low  # noqa: E402  # 测试需要
