"""discover-scan 测试配置：只插 skills/lib + 自身 scripts/lib。

隔离纪律（同 hk/journal/etf）：**不插 `scripts/` 根目录**——`lib` 包保留给 invest-a-stock。
CLI 级测试用 `load_scan_cli()` 按文件路径加载 `discover_scan.py`。
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

# CLI 加载器见 `_scan_cli.py`（唯一命名模块；conftest 同名跨技能会互相覆盖）。
