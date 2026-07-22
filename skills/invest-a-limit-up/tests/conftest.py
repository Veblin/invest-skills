"""pytest 配置：skills/lib + scripts/lib；scripts 仅 append 以导入 scan CLI 模块。"""
from __future__ import annotations

import sys
from pathlib import Path

_SKILL_ROOT = Path(__file__).resolve().parent.parent
_SCRIPTS = _SKILL_ROOT / "scripts"
_LIB = _SCRIPTS / "lib"
_SKILLS_LIB = _SKILL_ROOT.parent / "lib"

for p in (_SKILLS_LIB, _LIB):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

# invest-a-stock lib.* must win over any skill scripts/lib package name
from _invest_path import ensure_invest_a_scripts_on_path  # noqa: E402

ensure_invest_a_scripts_on_path()

# scan.py / tushare_enrich.py live under scripts/ — append so `lib` stays stock's
if str(_SCRIPTS) not in sys.path:
    sys.path.append(str(_SCRIPTS))
