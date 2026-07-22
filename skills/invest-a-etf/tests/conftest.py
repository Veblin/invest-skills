"""pytest 配置：scripts/lib 入 path（勿把 scripts/ 顶到前面，以免遮蔽 invest-a-stock 的 lib）。"""
from __future__ import annotations

import sys
from pathlib import Path

_LIB = Path(__file__).resolve().parent.parent / "scripts" / "lib"
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
