"""Shim — re-exports canonical skills/lib/qfq（DataFrame 版 apply_qfq）。

保留本文件而非删除：bootstrap 前（如 pytest 收集期先于 skills/lib 入 path 时）
`from qfq import apply_qfq` 经本 shim 解析到 canonical，避免 ImportError。

使用 importlib 按文件路径加载，避免与本 shim 模块名 ``qfq`` 冲突（裸 `from qfq`
import 会解析回部分初始化的自身 → 循环导入）。

**必须 re-export canonical 全表面（含 stock 专属 apply_qfq_rows）**：sys.modules
缓存先到先得——全仓收集时若本 shim 先于 canonical 被导入并缓存为 ``qfq``，
lib/tests/test_qfq.py 的 `from qfq import apply_qfq_rows` 会命中缓存而失败。
删减导出面会破坏收集顺序无关性（/code-review low F2 处置记录）。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_QFQ_LIB = Path(__file__).resolve().parent.parent.parent.parent / "lib"

_spec = importlib.util.spec_from_file_location("invest_a_lib_qfq", _QFQ_LIB / "qfq.py")
if _spec is None or _spec.loader is None:
    raise ImportError(f"cannot load skills/lib qfq from {_QFQ_LIB}")
_mod = importlib.util.module_from_spec(_spec)
sys.modules["invest_a_lib_qfq"] = _mod  # 注册加载名，防 canonical 内部重入
_spec.loader.exec_module(_mod)

PRICE_COLS = _mod.PRICE_COLS
apply_qfq = _mod.apply_qfq
apply_qfq_rows = _mod.apply_qfq_rows

__all__ = ["PRICE_COLS", "apply_qfq", "apply_qfq_rows"]
