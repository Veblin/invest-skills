"""collector 包 — v0.2.3 重构版。

当前阶段（Phase 1）：透明 re-export，从 _legacy.py 导出全部名称。
外部消费者（invest.py / render.py / 测试等）无感知。
"""

from __future__ import annotations

from . import _legacy as _mod

for _name, _value in vars(_mod).items():
    if _name.startswith("__"):
        continue
    globals()[_name] = _value

del _name, _value, _mod
