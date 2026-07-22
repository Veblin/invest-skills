"""pytest 配置：skills/lib + scripts/lib（shim 测 invest_path；勿遮蔽 invest-a-stock lib）。"""
from __future__ import annotations

import sys
from pathlib import Path

_SKILL_ROOT = Path(__file__).resolve().parent.parent
_LIB = _SKILL_ROOT / "scripts" / "lib"
_SKILLS_LIB = _SKILL_ROOT.parent / "lib"

for p in (_SKILLS_LIB, _LIB):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)
