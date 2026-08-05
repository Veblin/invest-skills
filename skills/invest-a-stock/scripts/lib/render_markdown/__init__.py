"""render_markdown 包 — v0.2.3 Phase 2（真正拆分）。

子模块: _base (helpers) | _v2 (legacy V2) | _v3 (core sections) | _concise (enhancer + V3 entry).
_legacy.py 为 thin backward-compat 层，所有业务代码已迁至子模块。
外部消费者（render.py / render_html.py / 测试）无感知。
"""

from __future__ import annotations

from . import _legacy as _mod

for _name, _value in vars(_mod).items():
    if _name.startswith("__"):
        continue
    globals()[_name] = _value

del _name, _value, _mod
