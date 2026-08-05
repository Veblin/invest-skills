"""Shim — re-exports canonical skills/lib/qfq（DataFrame 版 apply_qfq）。

保留本文件而非删除：bootstrap 前（如 pytest 收集期先于 _invest_path 加载）
`from qfq import apply_qfq` 经本 shim 解析到 canonical，避免 ImportError。
"""

from __future__ import annotations

import sys
from pathlib import Path

_skills_lib = Path(__file__).resolve().parent.parent.parent.parent / "lib"
_s = str(_skills_lib)
if _s not in sys.path:
    sys.path.insert(0, _s)

from qfq import PRICE_COLS, apply_qfq, apply_qfq_rows  # noqa: E402, F401

__all__ = ["PRICE_COLS", "apply_qfq", "apply_qfq_rows"]
