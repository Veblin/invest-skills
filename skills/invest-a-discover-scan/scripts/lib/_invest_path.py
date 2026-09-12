"""invest-a-discover-scan path shim（照抄 hk/journal/etf 版：只转发 skills/lib 与 invest-a-stock scripts）。

隔离纪律：**不插 `scripts/` 根目录**——`lib` 包保留给 invest-a-stock，
插了会让 `import lib` 抢先命中本 skill 自己的 `scripts/lib`。
"""
from __future__ import annotations

import sys
from pathlib import Path

_LIB = Path(__file__).resolve().parent          # .../scripts/lib
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

# _LIB.parents: [0]=invest-a-discover-scan/scripts [1]=invest-a-discover-scan [2]=skills [3]=code
_SKILLS_LIB = _LIB.parents[2] / "lib"           # skills/lib
if str(_SKILLS_LIB) not in sys.path:
    sys.path.insert(0, str(_SKILLS_LIB))

from invest_path import ensure_invest_a_scripts_on_path  # noqa: E402
from invest_path import ensure_shared_lib_on_path        # noqa: E402

ensure_skills_lib_on_path = ensure_shared_lib_on_path    # 别名对齐既有 shim 命名
