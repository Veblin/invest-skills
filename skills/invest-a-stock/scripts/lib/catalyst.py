"""催化剂日历（v0.2.3 新增）— 聚合前瞻性事件。

数据源:
  1. 分红除权日 — akshare stock_history_dividend_detail（已采集）
  2. 限售解禁 — akshare stock_restricted_release_queue_em
  3. 公告日期 — akshare stock_individual_notice_report（NLP 提取未来日期）

使用方式:
    from lib.catalyst import collect_catalyst_events, format_catalyst_calendar

    events = collect_catalyst_events("600176", days=90)
    print(format_catalyst_calendar(events))
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from .nums import ONE_PER_YI
from .shared_dates import parse_date as _parse_date, shanghai_now as _shanghai_now

logger = logging.getLogger(__name__)

_CATALYST_TYPES = {
    "dividend": {"label": "📊 分红", "impact": "中"},
    "restricted_unlock": {"label": "🔓 解禁", "impact": "高"},
    "announcement": {"label": "📋 公告", "impact": "中"},
    "earnings_estimate": {"label": "📊 财报", "impact": "高"},
}


@dataclass
class CatalystEvent:
    symbol: str
    date: date
    event_type: str  # dividend | restricted_unlock | announcement
    title: str
    detail: str = ""
    impact: str = "中"  # 高/中/低
    source: str = ""

    def label(self) -> str:
        return _CATALYST_TYPES.get(self.event_type, {}).get("label", "📅 其他")


# ---------------------------------------------------------------------------
# 数据源采集
# ---------------------------------------------------------------------------

def _fetch_dividend_events(symbol: str, lookahead_days: int) -> list[CatalystEvent]:
    """从 akshare 分红数据提取未来除权日。"""
    events: list[CatalystEvent] = []
    today = _shanghai_now().date()
    cutoff = today + timedelta(days=lookahead_days)

    try:
        from lib.env import is_akshare_available
        from lib.collector import akshare_direct_session

        if not is_akshare_available():
            logger.info("akshare unavailable, skip dividend events")
            return events

        with akshare_direct_session():
            import akshare as ak
            try:
                df = ak.stock_history_dividend_detail(symbol=symbol, indicator="分红")
            except Exception:
                # 尝试不带 indicator
                df = ak.stock_history_dividend_detail(symbol=symbol)

        if df is None or df.empty:
            return events

        for _, row in df.iterrows():
            raw_date = row.get("除权除息日") or row.get("date") or ""
            if not raw_date:
                continue
            try:
                event_date = _parse_date(raw_date)
                if event_date is None:
                    continue
            except Exception:
                continue

            if today <= event_date <= cutoff:
                plan = row.get("分红方案") or row.get("plan") or ""
                events.append(CatalystEvent(
                    symbol=symbol, date=event_date, event_type="dividend",
                    title=plan if plan else "分红除权",
                    detail=f"方案: {plan}" if plan else "",
                    impact="中", source="akshare.stock_history_dividend_detail",
                ))
    except Exception as exc:
        logger.warning("dividend fetch failed: %s", exc)

    return events


def _fetch_restricted_unlock_events(symbol: str, lookahead_days: int) -> list[CatalystEvent]:
    """从 akshare 限售解禁队列提取未来解禁事件。"""
    events: list[CatalystEvent] = []
    today = _shanghai_now().date()
    cutoff = today + timedelta(days=lookahead_days)

    try:
        from lib.env import is_akshare_available
        from lib.collector import akshare_direct_session

        if not is_akshare_available():
            return events

        with akshare_direct_session():
            import akshare as ak
            try:
                df = ak.stock_restricted_release_queue_em(symbol=symbol)
            except Exception as exc:
                logger.info("restricted release API unavailable: %s", exc)
                return events

        if df is None or df.empty:
            return events

        for _, row in df.iterrows():
            raw_date = row.get("解禁时间") or ""
            if not raw_date:
                continue
            try:
                event_date = _parse_date(raw_date)
                if event_date is None:
                    continue
            except Exception:
                continue

            if today <= event_date <= cutoff:
                shares = row.get("解禁数量") or 0
                shares_yi = float(shares) / ONE_PER_YI if shares else 0
                holder_count = _safe_int(row.get("解禁股东数", 0))
                holder_label = f"{holder_count} 个股东" if holder_count is not None else "股东数不可得"
                stock_type = row.get("限售股类型", "")
                events.append(CatalystEvent(
                    symbol=symbol, date=event_date, event_type="restricted_unlock",
                    title=f"限售解禁 {shares_yi:.2f} 亿股" if shares_yi > 0 else "限售解禁",
                    detail=f"{holder_label}, {stock_type}",
                    impact="高", source="akshare.stock_restricted_release_queue_em",
                ))
    except Exception as exc:
        logger.warning("restricted unlock fetch failed: %s", exc)

    return events


def _fetch_announcement_events(symbol: str, lookahead_days: int) -> list[CatalystEvent]:
    """从公告中提取未来日期（如"定于 XXXX年XX月XX日 召开股东大会"）。"""
    events: list[CatalystEvent] = []
    today = _shanghai_now().date()
    cutoff = today + timedelta(days=lookahead_days)

    try:
        from lib.env import is_akshare_available
        from lib.collector import akshare_direct_session

        if not is_akshare_available():
            return events

        with akshare_direct_session():
            import akshare as ak
            # Use the existing notice report API (same as events.py)
            try:
                df = ak.stock_individual_notice_report(security=symbol)
            except Exception:
                try:
                    df = ak.stock_notice_report(symbol=symbol)
                except Exception as exc:
                    logger.warning("announcement NLP failed: %s", exc)
                    return events

        if df is None or df.empty:
            return events

        # NLP: extract future dates from announcement titles
        _DATE_PATTERNS = [
            re.compile(r"(\d{4})年(\d{1,2})月(\d{1,2})日"),
            re.compile(r"(\d{4})-(\d{2})-(\d{2})"),
            re.compile(r"(\d{4})/(\d{1,2})/(\d{1,2})"),
        ]
        _EVENT_KEYWORDS = {
            "股东大会": "股东大会",
            "业绩说明会": "业绩说明会",
            "路演": "路演",
            "债券付息": "债券付息",
            "兑付": "债券兑付",
        }

        for _, row in df.iterrows():
            title = str(row.get("announcement_title") or
                       row.get("title") or
                       row.get("name") or "")
            if not title:
                continue

            # Check for event keywords
            matched_event = None
            for kw, label in _EVENT_KEYWORDS.items():
                if kw in title:
                    matched_event = label
                    break
            if not matched_event:
                continue

            # Extract date
            for pattern in _DATE_PATTERNS:
                m = pattern.search(title)
                if m:
                    try:
                        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
                        event_date = date(y, mo, d)
                        if today <= event_date <= cutoff:
                            events.append(CatalystEvent(
                                symbol=symbol, date=event_date,
                                event_type="announcement",
                                title=f"{matched_event}: {title[:50]}",
                                detail=title,
                                impact="中",
                                source="akshare.stock_individual_notice_report",
                            ))
                    except ValueError:
                        continue
                    break  # 只取第一个日期
    except Exception as exc:
        logger.warning("announcement NLP failed: %s", exc)

    return events


# ---------------------------------------------------------------------------
# 聚合与格式化
# ---------------------------------------------------------------------------

def collect_catalyst_events(symbol: str, days: int = 90) -> list[CatalystEvent]:
    """采集未来 N 天的催化剂事件。

    Args:
        symbol: 6 位股票代码
        days: 前瞻天数（默认 90）

    Returns:
        按日期升序排列的 CatalystEvent 列表
    """
    all_events: list[CatalystEvent] = []

    all_events.extend(_fetch_dividend_events(symbol, days))
    all_events.extend(_fetch_restricted_unlock_events(symbol, days))
    all_events.extend(_fetch_announcement_events(symbol, days))

    # 去重（同日期 + 同标题）
    seen = set()
    unique: list[CatalystEvent] = []
    for e in sorted(all_events, key=lambda x: x.date):
        key = (e.date, e.title)
        if key not in seen:
            seen.add(key)
            unique.append(e)
    return unique


def format_catalyst_calendar(events: list[CatalystEvent], symbol: str = "") -> str:
    """格式化为 Markdown 日历表格。"""
    if not events:
        return (f"## 催化剂日历 — {symbol}\n\n"
                "> 未来 90 天内未发现已知催化剂事件。\n"
                "> 财报日期需通过 akshare 财报预约披露接口获取（当前不可用）。")

    lines = [
        f"## 催化剂日历 — {symbol}",
        "",
        f"| 日期 | 事件 | 类型 | 详情 | 影响 |",
        f"|------|------|:---:|------|:---:|",
    ]

    for e in events:
        detail = e.detail[:80] + "..." if len(e.detail) > 80 else e.detail
        lines.append(
            f"| {e.date.strftime('%m-%d')} | {e.title[:40]} | "
            f"{e.label()} | {detail} | {e.impact} |"
        )

    # 数据源说明
    sources = set(e.source for e in events if e.source)
    if sources:
        lines.extend([
            "",
            f"> 数据来源: {', '.join(sorted(sources))}",
            "> ⚠️ 财报发布日期需通过 akshare 财报预约披露获取（当前版本不可用）。"
            "行业事件（展会/会议等）不在当前覆盖范围。",
        ])

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------

def _safe_int(value: Any, default: int | None = None) -> int | None:
    """NaN 安全整数转换：pandas NaN / float('nan') → None，缺失或非法 → default。

    单条记录字段异常不再抛错吞掉整批事件（如解禁股东数为 NaN）。
    """
    try:
        import pandas as pd
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


