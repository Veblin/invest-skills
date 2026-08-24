"""collector 包 — v0.2.3 Phase 2（真正拆分）。

子模块: _base (utils + parallel helpers) | _sources (source queries) | _orchestrate (dimensions + market + peers).
_legacy.py 为 thin backward-compat 层（显式 re-export，code-review #6 弃用
dir()-copy 隐式再导出），所有业务代码已迁至子模块。
外部消费者（invest.py / render.py / 测试等）无感知。
"""

from __future__ import annotations

from . import _legacy as _mod

for _name, _value in vars(_mod).items():
    if _name.startswith("__"):
        continue
    globals()[_name] = _value

del _name, _value, _mod
