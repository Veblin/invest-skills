"""JSON 序列化辅助：统一处理 dataclass、numpy、datetime 等类型。"""

from __future__ import annotations

import logging
import os
import tempfile
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def json_default(obj: Any) -> Any:
    """json.dumps(..., default=json_default) 的安全回退。"""
    if hasattr(obj, "to_dict") and callable(obj.to_dict):
        return obj.to_dict()
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, (bytes, bytearray)):
        return obj.decode("utf-8", errors="replace")
    if isinstance(obj, (set, frozenset)):
        # review #12（2026-10-14，与 snapshot_core.default_serializer 同式）：
        # 混合类型（如 {1, "a"}）通过原类型校验后 sorted() 抛 TypeError；
        # 改按（类型名, 值）全序，不可比时保守回落 list(obj)（旧未知类型分支语义）。
        if all(isinstance(x, (str, int, float)) for x in obj):
            try:
                return sorted(obj, key=lambda x: (type(x).__name__, x))
            except TypeError:  # 理论不可达：已知类型同组内可比
                pass
        return list(obj)
    try:
        import numpy as np
        if isinstance(obj, np.generic):
            return obj.item()
        if isinstance(obj, np.ndarray):
            return obj.tolist()
    except ImportError:
        pass
    logger.debug("json_default: falling back to str for %s", type(obj).__name__)
    return str(obj)


def dumps_json(data: Any, *, indent: int | None = 2) -> str:
    """序列化为 JSON 字符串（优先保留数值类型）。"""
    import json
    return json.dumps(data, ensure_ascii=False, indent=indent, default=json_default)


# ---------------------------------------------------------------------------
# A05-1（R2，v0.3.2）：原子写入底层
#
# 收敛三处逐字重复的同目录临时文件 + flush/fsync + replace + 异常清理
# （invest.py:_write_analysis_sidecar / research_profile.write_profile_sidecar
# / insight_model._atomic_json）。各自的 schema、空输入行为、命名与 manifest
# 发布顺序仍由调用方保留——本层只做「原子写字节」。
# ---------------------------------------------------------------------------

def atomic_write_text(path: Path, text: str, *, fsync: bool = True) -> None:
    """原子写文本：同目录 mkstemp → UTF-8 写入 → replace；异常清理临时文件。

    调用方负责目录存在（与三处历史实现一致：target 目录均由产物链保证；
    本函数**不** mkdir，避免为旧调用点引入新的目录创建行为）。
    写入失败时旧文件保持原样、临时文件被删除（不留半截文件）。
    """
    path = Path(path)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent), text=True,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            if fsync:
                os.fsync(handle.fileno())
        Path(temp_name).replace(path)
    except Exception:
        Path(temp_name).unlink(missing_ok=True)
        raise


def atomic_write_json(path: Path, payload: Any, *, fsync: bool = True) -> None:
    """原子写 JSON（``json.dumps(payload, ensure_ascii=False, indent=2)`` + 尾换行）。

    与三处历史实现逐字节一致（不启用 json_default——历史调用点均为纯 JSON
    负载；类型回退差异会改变产物字节）。
    """
    import json

    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    atomic_write_text(path, text, fsync=fsync)
