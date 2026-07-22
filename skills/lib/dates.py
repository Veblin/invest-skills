"""Shared date-string helpers across skills (Batch D / L-03)."""

from __future__ import annotations

__all__ = ["yyyymmdd_to_iso"]


def yyyymmdd_to_iso(yyyymmdd: str) -> str:
    """YYYYMMDD → YYYY-MM-DD；非 8 位数字则原样返回。"""
    s = yyyymmdd.strip()
    if len(s) == 8 and s.isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    return s
