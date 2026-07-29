"""render_markdown 包 — v0.2.3 重构版。

当前阶段（Phase 1）：透明 re-export，从 _legacy.py 导出全部名称。
后续阶段：逐步拆分 _legacy.py 为子模块，更新本文件。

外部消费者（render.py / render_html.py / 测试）无感知。
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Phase 1：从 _legacy 透明 re-export（镜像 render.py 的 _reexport 模式）
# ---------------------------------------------------------------------------

from . import _legacy as _mod

for _name, _value in vars(_mod).items():
    if _name.startswith("__"):
        continue
    globals()[_name] = _value

# 清理循环变量
del _name, _value, _mod
