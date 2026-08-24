"""Shared date-string helpers across skills (Batch D / L-03)."""

from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

__all__ = [
    "parse_date",
    "yyyymmdd_to_iso",
    "shanghai_now",
    "shanghai_today",
    "shanghai_days_ago",
    "normalize_end_date",
    "latest_month_row",
    "parse_utc_iso",
    "fmt_fetched_at",
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


def parse_utc_iso(raw: Any) -> datetime | None:
    """解析 fetched_at ISO 串 → aware UTC datetime；失败返回 None。

    兼容 'Z' 后缀 / '+HH:MM' 偏移 / naive（按 UTC 假定——存量数据全由
    collector._assemble_result 以 UTC 生成，与 store._parse_fetched_at
    同一不变式）。非 str / 空串 → None。
    """
    if not isinstance(raw, str):
        return None
    s = raw.strip()
    if not s:
        return None
    if s.endswith(("Z", "z")):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def fmt_fetched_at(iso_str: Any, *, pattern: str = "%Y-%m-%d %H:%M") -> str:
    """UTC ISO → 上海时区 'YYYY-MM-DD HH:MM (北京时间)'；解析失败回退原串截 16 字符。"""
    dt = parse_utc_iso(iso_str)
    if dt is None:
        return str(iso_str)[:16] if iso_str else ""
    return f"{dt.astimezone(_SHANGHAI).strftime(pattern)} (北京时间)"


def shanghai_today() -> str:
    """上海时区今日日期，YYYYMMDD。"""
    return shanghai_now().strftime("%Y%m%d")


def shanghai_days_ago(n: int) -> str:
    """上海时区 N 天前的日期，YYYYMMDD。"""
    return (shanghai_now() - timedelta(days=n)).strftime("%Y%m%d")


def latest_month_row(rows: list) -> Any:
    """从 akshare 宏观序列行中取「月份」最新的一行。

    akshare macro_china_pmi/cpi/ppi 返回的序列**最新在前**（首行为最新期、
    末行为 2008 年），直接 `iloc[-1]` 会取到最旧行（F0-4 缺陷根因）。
    此处按「YYYY年MM月份」解析后取最大 (年, 月)，与行序无关。

    全部解析失败时回退首行（akshare 序列约定最新在前）。
    """
    best_row: Any = None
    best_key: tuple[int, int] | None = None
    first_row = rows[0] if rows else None
    first_parse_ok = False
    for row in rows:
        m = re.search(r"(\d{4})年(\d{1,2})月", str(row.get("月份", "")))
        if not m:
            continue
        key = (int(m.group(1)), int(m.group(2)))
        if best_key is None or key > best_key:
            best_key, best_row = key, row
        if row is first_row:
            first_parse_ok = True
    if best_row is None and rows:
        # 全部行解析失败（akshare 改了月份列格式等）：显式告警而非静默回退
        # 首行——宏观标签若用了旧期数字，CLAUDE.md F2-7「核验最新期」需要
        # 知道这里发生了什么。
        logger.warning(
            "latest_month_row: 全部 %d 行「月份」列解析失败（期望「YYYY年M月」），"
            "回退首行——可能非最新期，宏观标签需人工核验", len(rows),
        )
        best_row = rows[0]
    elif first_row is not None and not first_parse_ok and best_key is not None:
        # 最新行（首行，akshare 约定最新在前）解析失败而选中了更早行：
        # 同样显式告警，避免把上一期 PMI/CPI/PPI 静默当作当期。
        logger.warning(
            "latest_month_row: 首行「月份」不可解析（%r），取到 %04d-%02d 期行——"
            "可能非最新期，宏观标签需人工核验",
            first_row.get("月份"), best_key[0], best_key[1],
        )
    return best_row
