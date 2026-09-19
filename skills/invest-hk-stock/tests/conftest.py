"""invest-hk-stock 测试配置：只插 skills/lib + 自身 scripts/lib。

隔离纪律（三铁律，同 etf/journal conftest 注释）：**不插 scripts/ 根目录**，
避免 `import lib` 抢先命中本 skill —— `lib` 包保留给 invest-a-stock。
"""
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

# CLI 加载器见 `_hk_cli.py`（唯一命名模块；conftest 同名跨技能会互相覆盖）。
