"""discover-scan CLI 加载器（测试辅助）。

⚠️ 独立成**唯一命名**模块，不走 `conftest`：pytest 默认 prepend 导入模式下
`conftest` 是**同名模块**——多技能共存时 `from conftest import ...` 会命中
另一个技能的 conftest（2026-09-12 全仓跑测时实测踩坑）。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_SKILL_ROOT = Path(__file__).resolve().parent.parent


def load_scan_cli(name: str = "discover_scan.py"):
    """按文件路径加载 CLI 模块（按 name 单例）。"""
    mod_name = f"discover_cli_under_test_{name.replace('.', '_')}"
    if mod_name in sys.modules:
        return sys.modules[mod_name]
    p = _SKILL_ROOT / "scripts" / name
    spec = importlib.util.spec_from_file_location(mod_name, p)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod
