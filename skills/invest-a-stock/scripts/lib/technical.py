"""Shim: canonical implementation at skills/lib/technical.py. Backward compatible."""
from __future__ import annotations

import sys
from pathlib import Path

_lib = str(Path(__file__).resolve().parent.parent.parent.parent / "lib")
if _lib not in sys.path:
    sys.path.insert(0, _lib)

from technical import *  # noqa: E402, F403
from technical import _ema, _rsi  # noqa: E402  # 测试需要
