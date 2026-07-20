"""JSON 序列化辅助：统一处理 dataclass、numpy、datetime 等类型。"""

from __future__ import annotations

import logging
from datetime import date, datetime
from decimal import Decimal
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
        return sorted(obj) if all(isinstance(x, (str, int, float)) for x in obj) else list(obj)
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
