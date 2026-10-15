"""Shim: canonical implementation at skills/lib/env.py (A02 owner 迁移, v0.3.2).

身份保持 shim（非逐名转发）：加载顶层 canonical 后把本模块在 sys.modules
中**替换为 canonical 模块对象** —— ``lib.env`` 与顶层 ``env`` 是同一实例，

- monkeypatch 接缝不变（``patch("lib.env.get_config")`` 命中 canonical）；
- 导出面 = canonical 全表面（不逐名转发，新增名字自动可见）；
- 独立包内由构建器以 canonical 文件取代本 shim（包内单实例）。
"""

from __future__ import annotations

import sys
from pathlib import Path

_shared_lib = str(Path(__file__).resolve().parents[3] / "lib")
if _shared_lib not in sys.path:
    sys.path.insert(0, _shared_lib)

from invest_path import _load_shared_module  # noqa: E402

_canonical = _load_shared_module("env", shim_name=__name__)

sys.modules[__name__] = _canonical
