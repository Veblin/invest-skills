"""Shared date-string helpers across skills (Batch D / L-03)."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

__all__ = [
    "yyyymmdd_to_iso",
    "shanghai_now",
    "shanghai_today",
    "shanghai_days_ago",
]

_SHANGHAI = ZoneInfo("Asia/Shanghai")


def yyyymmdd_to_iso(yyyymmdd: str) -> str:
    """YYYYMMDD → YYYY-MM-DD；非 8 位数字则原样返回。"""
    s = yyyymmdd.strip()
    if len(s) == 8 and s.isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    return s


def shanghai_now() -> datetime:
    """当前上海时区时间（A 股工具统一时区）。"""
    return datetime.now(_SHANGHAI)


def shanghai_today() -> str:
    """上海时区今日日期，YYYYMMDD。"""
    return shanghai_now().strftime("%Y%m%d")


def shanghai_days_ago(n: int) -> str:
    """上海时区 N 天前的日期，YYYYMMDD。"""
    return (shanghai_now() - timedelta(days=n)).strftime("%Y%m%d")
