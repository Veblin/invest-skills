"""Shared date-string helpers across skills (Batch D / L-03)."""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

__all__ = [
    "yyyymmdd_to_iso",
    "shanghai_now",
    "shanghai_today",
    "shanghai_days_ago",
    "normalize_end_date",
]

_SHANGHAI = ZoneInfo("Asia/Shanghai")


def yyyymmdd_to_iso(yyyymmdd: str) -> str:
    """YYYYMMDD → YYYY-MM-DD；非 8 位数字则原样返回。"""
    s = yyyymmdd.strip()
    if len(s) == 8 and s.isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    return s


def normalize_end_date(ed: str) -> str:
    """Normalize report period to YYYYMMDD.

    Accepts: YYYYMMDD, YYYY-MM-DD, YYYY.MM.DD, interval formats
    (e.g. "2015.07.23-2015.07.23"). 失败返回空串 — 调用方用 truthiness
    检查跳过无法解析的记录。
    """
    raw = str(ed).strip()
    # Already YYYYMMDD
    if re.match(r'^\d{8}$', raw):
        return raw
    # YYYY-MM-DD or YYYY.MM.DD
    m = re.search(r'(\d{4})[-./](\d{1,2})[-./](\d{1,2})', raw)
    if m:
        return f"{m.group(1)}{int(m.group(2)):02d}{int(m.group(3)):02d}"
    # Fallback: first 8 digits
    if len(raw) >= 8 and raw[:8].isdigit():
        return raw[:8]
    # Return empty string on total failure — callers use truthiness checks
    # (e.g. ``if norm_date:``) to skip unparseable records.
    return ""


def shanghai_now() -> datetime:
    """当前上海时区时间（A 股工具统一时区）。"""
    return datetime.now(_SHANGHAI)


def shanghai_today() -> str:
    """上海时区今日日期，YYYYMMDD。"""
    return shanghai_now().strftime("%Y%m%d")


def shanghai_days_ago(n: int) -> str:
    """上海时区 N 天前的日期，YYYYMMDD。"""
    return (shanghai_now() - timedelta(days=n)).strftime("%Y%m%d")
