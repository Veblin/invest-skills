"""Shared date-string helpers across skills (Batch D / L-03)."""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

__all__ = [
    "parse_date",
    "yyyymmdd_to_iso",
    "shanghai_now",
    "shanghai_today",
    "shanghai_days_ago",
    "normalize_end_date",
]

_SHANGHAI = ZoneInfo("Asia/Shanghai")


def parse_date(raw: Any) -> date | None:
    """解析多种日期格式 → date 对象；无法解析 → None。

    并集语义（C3 收敛自 catalyst._parse_date + events._normalize_date）：
    - None / pandas NaT / NaN → None
    - datetime / date / pd.Timestamp 实例 → .date()
    - 哨兵（空 / nat / n/a / -- / —）→ None
    - 四种格式：YYYY-MM-DD / YYYYMMDD / YYYY/MM/DD / YYYY年MM月DD日
    - 长串中提取 YYYY-MM-DD（如 "2026-06-15 10:30:00"）

    快路径（review 第三轮 #8）：str / datetime / date 走纯标准库分支，
    pandas 仅在"其他类型"（float nan、pd.NaT 等）上调用——events 热循环
    每事件零 pandas dispatch。
    """
    if raw is None:
        return None
    if isinstance(raw, datetime):
        try:
            d = raw.date()
        except Exception:
            return None
        # pd.NaT 是 datetime 伪子类：NaT.date() 返回 NaT 不抛异常——
        # 必须在 datetime 分支内显式判空（test_catalyst.test_pandas_nat）
        return None if str(d) == "NaT" else d
    if isinstance(raw, date):
        return raw
    if isinstance(raw, str):
        s = raw.strip()
        if not s or s.lower() in ("nat", "n/a") or s in ("--", "—"):
            return None
        for fmt in ("%Y-%m-%d", "%Y%m%d", "%Y/%m/%d", "%Y年%m月%d日"):
            try:
                return datetime.strptime(s, fmt).date()
            except ValueError:
                continue
        m = re.search(r"(\d{4})-(\d{2})-(\d{2})", s)
        if m:
            try:
                return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            except ValueError:
                return None
        return None
    # 非 str / 非 datetime（float nan、pd.NaT 等）→ pandas 判空
    try:
        import pandas as pd  # 惰性：仅非常规类型路径引入 pandas

        if pd.isna(raw):
            return None
    except (ImportError, TypeError, ValueError):
        pass
    return None


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
