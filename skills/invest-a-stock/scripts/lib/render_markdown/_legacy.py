"""Backward-compat layer — re-exports from sub-modules.

v0.2.3 Phase 2: 真正拆分。_legacy.py 不再包含业务逻辑，
所有代码已迁移至 _base / _v2 / _v3 / _concise 子模块。
"""
from . import _base, _v2, _v3, _concise

_all_modules = [_base, _v2, _v3, _concise]
for _mod in _all_modules:
    for _name, _value in vars(_mod).items():
        if _name.startswith("__"):
            continue
        globals()[_name] = _value

del _mod, _name, _value, _all_modules
