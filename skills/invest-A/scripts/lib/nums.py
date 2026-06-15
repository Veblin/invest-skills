"""Numeric helpers shared across lib modules."""

from __future__ import annotations

from typing import Any


def safe_float(v: Any) -> float | None:
    """安全转为 float；None / NaN / 非数字返回 None。"""
    if v is None:
        return None
    try:
        f = float(v)
        if f != f:  # NaN
            return None
        return f
    except (TypeError, ValueError):
        return None
