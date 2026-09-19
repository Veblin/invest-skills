"""hk.py CLI 加载器（测试辅助）。

⚠️ 独立成**唯一命名**模块，不走 `conftest`：pytest 默认 prepend 导入模式下
`conftest` 是**同名模块**——多技能共存时 `from conftest import ...` 会命中
另一个技能的 conftest（2026-09-12 全仓跑测时实测：hk 的测试拿到了
discover-scan 的 conftest 并报 ImportError）。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_SKILL_ROOT = Path(__file__).resolve().parent.parent


def load_hk_cli():
    """按**文件路径**加载 CLI 模块（单例，跨测试文件共享）。

    不能 `import hk`：conftest 只注入 lib 目录，且刻意不插 `scripts/` 根
    （避免 `import lib` 抢先命中本 skill 而非共享层）。CLI 自身在 import 时
    完成路径引导（_LIB + ensure_*），故用 importlib 按路径加载最干净。
    """
    name = "hk_cli_under_test"
    if name in sys.modules:
        return sys.modules[name]
    p = _SKILL_ROOT / "scripts" / "hk.py"
    spec = importlib.util.spec_from_file_location(name, p)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod
