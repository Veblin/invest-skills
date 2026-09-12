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


def load_hk_cli():
    """按**文件路径**加载 CLI 模块（单例，跨测试文件共享）。

    不能 `import hk`：conftest 只注入 lib 目录，且刻意不插 `scripts/` 根
    （避免 `import lib` 抢先命中本 skill 而非共享层）。CLI 自身在 import 时
    完成路径引导（_LIB + ensure_*），故用 importlib 按路径加载最干净。
    """
    import importlib.util

    name = "hk_cli_under_test"
    if name in sys.modules:
        return sys.modules[name]
    p = _SKILL_ROOT / "scripts" / "hk.py"
    spec = importlib.util.spec_from_file_location(name, p)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod
