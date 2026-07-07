"""Numeric helpers shared across lib modules."""

from __future__ import annotations

from typing import Any


def safe_float(v: Any) -> float | None:
    """安全转为 float；None / NaN / ±inf / 非数字返回 None。"""
    if v is None:
        return None
    try:
        f = float(v)
        if f != f:  # NaN
            return None
        if f in (float("inf"), float("-inf")):  # inf
            return None
        return f
    except (TypeError, ValueError):
        return None


def coalesce_field(row: dict, *keys: str) -> float | None:
    """取 dict 中第一个非 None 的数值字段（保留负值与 0，避免 `or` 误判）。

    一次只尝试一个 key，命中后返回 safe_float 结果；跳过 NaN/inf/非数字。
    """
    for k in keys:
        v = safe_float(row.get(k))
        if v is not None:
            return v
    return None
