"""催化剂日历（v0.2.3 新增）— 聚合前瞻性事件。

数据源:
  1. 分红除权日 — akshare stock_history_dividend_detail（已采集）
  2. 限售解禁 — akshare stock_restricted_release_queue_em
  3. 公告日期 — akshare stock_individual_notice_report（NLP 提取未来日期）

使用方式:
    from lib.catalyst import collect_catalyst_events, format_catalyst_calendar

    events, unavailable = collect_catalyst_events("600176", days=90)
    print(format_catalyst_calendar(events, symbol="600176", days=90,
                                   unavailable=unavailable))

取数失败以 ``unavailable`` 显式外显：三条腿任一失败时，产物必须写明
「不可得 ≠ 无事件」，不得把失败渲染成「未检索到事件」（review C3）。
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

def _fetch_dividend_events(symbol: str, lookahead_days: int) -> tuple[list[CatalystEvent], str | None]:
    """从 akshare 分红数据提取未来除权日。

    返回 ``(events, error)``：error=None 表示取数成功（含合法空结果），
    否则为失败原因——调用方必须区分「无事件」与「取数失败」。
    """
    events: list[CatalystEvent] = []
    today = _shanghai_now().date()
    cutoff = today + timedelta(days=lookahead_days)

    try:
        from lib.env import is_akshare_available
        from lib.collector import akshare_direct_session

        if not is_akshare_available():
            logger.info("akshare unavailable, skip dividend events")
            return events, "akshare 不可用"

        with akshare_direct_session():
            import akshare as ak
            try:
                df = ak.stock_history_dividend_detail(symbol=symbol, indicator="分红")
            except Exception:
                # 尝试不带 indicator
                df = ak.stock_history_dividend_detail(symbol=symbol)

        if df is None or df.empty:
            return events, None

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
        return events, f"{type(exc).__name__}: {exc}"

    return events, None


def _fetch_restricted_unlock_events(symbol: str, lookahead_days: int) -> tuple[list[CatalystEvent], str | None]:
    """从个股解禁队列提取未来解禁事件。

    源实现已收敛至共享模块 ``skills/lib/unlock_source.py``（invest-a-event-calendar v2
    同源复用；失败以 (rows, error) 显式区分，不再静默空）。

    返回 ``(events, error)``；error 须原样透传给调用方，不得只记日志后丢弃——
    否则取数失败会被产物写成「未检索到事件」这一事实性缺席断言。
    """
    events: list[CatalystEvent] = []
    today = _shanghai_now().date()

    try:
        from lib.env import is_akshare_available

        if not is_akshare_available():
            return events, "akshare 不可用"
    except Exception:  # env 判定失败不阻断（fetch 内部自会返回失败原因）
        pass

    try:  # 共享库引导（skills/lib；包内由 builder 重写为 lib.unlock_source）
        from ._invest_path import ensure_skills_lib_on_path

        ensure_skills_lib_on_path()
    except Exception:  # pragma: no cover
        pass

    try:  # 导入在 try 内：上方 bootstrap 是 best-effort（except: pass），引导失败
        # 或 skills/lib 不在 sys.path 时 import 会抛 ModuleNotFoundError——必须只
        # 降级本段，否则 collect_catalyst_events 整块中止，同一调用中已采到的分红/
        # 公告事件一并丢失。
        from unlock_source import fetch_symbol_unlocks

        rows, err = fetch_symbol_unlocks(symbol, lookahead_days=lookahead_days,
                                         today=today)
    except Exception as exc:  # noqa: BLE001
        logger.warning("restricted unlock fetch failed: %s", exc)
        return events, f"{type(exc).__name__}: {exc}"
    if err:
        logger.info("restricted release API unavailable: %s", err)
        return events, err

    for r in rows:
        try:
            event_date = date.fromisoformat(r["date"])
        except (ValueError, TypeError):
            continue
        shares_yi = r["qty_yi"] or 0
        holder_count = r["holders"]
        holder_label = f"{holder_count} 个股东" if holder_count is not None else "股东数不可得"
        events.append(CatalystEvent(
            symbol=symbol, date=event_date, event_type="restricted_unlock",
            title=f"限售解禁 {shares_yi:.2f} 亿股" if shares_yi > 0 else "限售解禁",
            detail=f"{holder_label}, {r['kind']}",
            impact="高", source="akshare.stock_restricted_release_queue_em",
        ))
    return events, None


def _fetch_announcement_events(symbol: str, lookahead_days: int) -> tuple[list[CatalystEvent], str | None]:
    """从公告中提取未来日期（如"定于 XXXX年XX月XX日 召开股东大会"）。

    返回 ``(events, error)``，语义同 ``_fetch_dividend_events``。
    """
    events: list[CatalystEvent] = []
    today = _shanghai_now().date()
    cutoff = today + timedelta(days=lookahead_days)

    try:
        from lib.env import is_akshare_available
        from lib.collector import akshare_direct_session

        if not is_akshare_available():
            return events, "akshare 不可用"

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
                    return events, f"{type(exc).__name__}: {exc}"

        if df is None or df.empty:
            return events, None

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
        return events, f"{type(exc).__name__}: {exc}"

    return events, None


# ---------------------------------------------------------------------------
# 聚合与格式化
# ---------------------------------------------------------------------------

def collect_catalyst_events(symbol: str, days: int = 90) -> tuple[list[CatalystEvent], list[str]]:
    """采集未来 N 天的催化剂事件。

    Args:
        symbol: 6 位股票代码
        days: 前瞻天数（默认 90）

    Returns:
        ``(events, unavailable)``：events 为按日期升序排列的 CatalystEvent 列表；
        unavailable 为取数失败的来源说明（空列表 = 三条腿都成功）。调用方
        **必须**把 unavailable 带进产物——取数失败 ≠ 没有事件（review C3）。
    """
    all_events: list[CatalystEvent] = []
    unavailable: list[str] = []

    for label, fetch in (("分红除权", _fetch_dividend_events),
                         ("限售解禁", _fetch_restricted_unlock_events),
                         ("公告事件", _fetch_announcement_events)):
        try:
            events, err = fetch(symbol, days)
        except Exception as exc:  # noqa: BLE001 —— 单腿异常不得中断整块采集
            events, err = [], f"{type(exc).__name__}: {exc}"
        all_events.extend(events)
        if err:
            unavailable.append(f"{label}（{err}）")

    # 去重（同日期 + 同标题）
    seen = set()
    unique: list[CatalystEvent] = []
    for e in sorted(all_events, key=lambda x: x.date):
        key = (e.date, e.title)
        if key not in seen:
            seen.add(key)
            unique.append(e)
    return unique, unavailable


def format_catalyst_calendar(events: list[CatalystEvent], symbol: str = "",
                             days: int = 90,
                             unavailable: list[str] | None = None) -> str:
    """格式化为 Markdown 日历表格。

    ``unavailable`` 非空时必须在产物内明示：把取数失败写成「未检索到事件」
    是关于报告内容的事实性断言，在源失败时不成立（review C3）。
    """
    degraded = list(unavailable or [])

    if not events:
        if degraded:
            # 与失败说明**同句**：单独的「未检索到」会把工具故障读成干净的缺席。
            claim = (f"未来 {days} 天内未检索到已知催化剂事件；**但以下来源取数失败，"
                     f"不可得 ≠ 无事件**：" + "；".join(degraded) + "。")
        else:
            claim = f"未来 {days} 天内未检索到已知催化剂事件。"
        return "\n".join([
            f"## 催化剂日历 — {symbol}",
            "",
            f"> {claim}",
            "> 财报日期需通过 akshare 财报预约披露接口获取（当前不可用）。",
        ])

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

    if degraded:
        lines.extend([
            "",
            "> ⚠️ 以下来源**取数失败**，相关事件可能缺失（不可得 ≠ 无事件）："
            + "；".join(degraded) + "。",
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


