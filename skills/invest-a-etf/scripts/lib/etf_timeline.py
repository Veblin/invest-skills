"""事件-时间线工具（R11b，v0.2.4）：大波动日识别 + 事件-价格对齐 + 事件文件校验。

事件文件格式（``events/{symbol}.json``，JSON Lines，UTF-8）：:

    {"date": "2026-05-11", "event": "…", "source_url": "https://…",
     "published_date": "2026-05-11", "confidence": "一手"}

字段要求：
- ``date`` / ``published_date`` — ISO 日期
- ``source_url`` — 必填（一手来源链接）
- ``confidence`` — 枚举 ``一手`` / ``二手``

任一非法 → 整文件拒绝并报行号（``validate_events_file``）。

对齐口径：事件与价格 ±1 交易日对齐（周末近似跳过）；「同日事实」为纯时间线
罗列，不做因果断言；「可能关联（待验证）」按 confidence 分级标注。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from lib.nums import safe_float

# 事件置信度枚举（一手 = 官方/一手来源，可进高可信说明；其余一律待验证）
CONFIDENCE_LEVELS = ("一手", "二手")

BIG_MOVE_DEFAULT_THRESHOLD_PCT = 5.0


def detect_big_move_days(
    nav_history: list[dict], threshold_pct: float = BIG_MOVE_DEFAULT_THRESHOLD_PCT
) -> list[dict]:
    """单日 |change_pct| >= threshold_pct 的交易日清单（引擎计算，AI 不得心算）。

    输入 nav_history（nav 链路 {date, nav, change_pct}）或 baostock K 线行
    （{date, open, close, ...}），change_pct 由收盘价逐日重算（与
    compute_history_stats.big_move_days 同口径）。

    Returns
    -------
    list[dict]
        升序 [{date, change_pct}]，change_pct 保留 2 位小数。
    """
    out: list[dict[str, Any]] = []
    prev_close: float | None = None
    for r in nav_history:
        close = safe_float(r.get("nav", r.get("close")))
        d = str(r.get("date", ""))[:10]
        if close is None or not d:
            continue
        if prev_close is not None and prev_close > 0:
            chg = (close / prev_close - 1) * 100
            if abs(chg) >= threshold_pct:
                out.append({"date": d, "change_pct": round(chg, 2)})
        prev_close = close
    return out


def _prev_trading_day(d: datetime.date) -> datetime.date:
    """前一交易日（委托 lib.trade_cal：有 token 走 SSE 真实日历含节假日/调休，
    无 token 周末近似——收敛 C8 #12，替代本模块 holiday-blind 实现）。"""
    from lib.trade_cal import prev_trading_day as _prev

    return _prev(d)


def _next_trading_day(d: datetime.date) -> datetime.date:
    """后一交易日（委托 lib.trade_cal，见 _prev_trading_day）。"""
    from lib.trade_cal import next_trading_day as _next

    return _next(d)


def align_events_with_price(move_days: list[dict], events: list[dict]) -> list[dict]:
    """事件与价格 ±1 交易日对齐（R11b）。

    Parameters
    ----------
    move_days : list[dict]
        ``detect_big_move_days`` 输出（[{date, change_pct}]，升序）。
    events : list[dict]
        ``validate_events_file`` 校验通过的事件（[{date, event, source_url,
        published_date, confidence}]）。

    Returns
    -------
    list[dict]
        每事件一行：
        - ``date`` / ``event`` / ``source_url`` / ``confidence`` — 事件原字段
        - ``同日事实`` — 与事件**同日**的大波动（纯时间线罗列，不做因果断言）；
          无则为 []
        - ``可能关联（待验证）`` — confidence=一手 可进高可信说明（仍需一手来源
          交叉验证），其余一律标注待验证；±1 交易日重合但非同日的标注「邻近」
        - ``aligned`` — 是否与任何大波动日（同日或 ±1 交易日）重合
    """
    by_date: dict[str, dict] = {m["date"]: m for m in move_days}
    aligned: list[dict[str, Any]] = []
    for ev in events:
        ev_date = ev["date"]
        same_day = [
            {"date": d, "change_pct": m["change_pct"]}
            for d, m in sorted(by_date.items())
            if d == ev_date
        ]
        # ±1 交易日窗口（周末近似）：事件日前一/后一交易日
        try:
            d0 = datetime.strptime(ev_date, "%Y-%m-%d").date()
            window = {str(_prev_trading_day(d0)), ev_date, str(_next_trading_day(d0))}
        except ValueError:
            window = {ev_date}
        nearby = [
            {"date": d, "change_pct": m["change_pct"]}
            for d, m in sorted(by_date.items())
            if d in window and d != ev_date
        ]
        moves = same_day + nearby
        if not moves:
            aligned.append({
                "date": ev_date,
                "event": ev["event"],
                "source_url": ev["source_url"],
                "confidence": ev.get("confidence"),
                "同日事实": [],
                "可能关联（待验证）": None,
                "aligned": False,
            })
            continue
        # 「同日事实」= 纯时间线罗列（仅同日，不做因果断言）；±1 交易日邻近
        # 重合只进入「可能关联（待验证）」说明
        facts = [
            f"{m['date']} 单日 {m['change_pct']:+.2f}%" for m in same_day
        ]
        if ev.get("confidence") == "一手":
            note = (
                "高可信说明候选：一手来源事件与价格大波动"
                + ("同日" if same_day else "邻近(±1 交易日)")
                + "重合；因果方向仍须一手来源交叉验证后再作表述"
            )
        else:
            note = (
                "待验证：事件与价格大波动"
                + ("同日" if same_day else "邻近(±1 交易日)")
                + "重合，需核实事件时效与因果路径"
            )
        aligned.append({
            "date": ev_date,
            "event": ev["event"],
            "source_url": ev["source_url"],
            "confidence": ev.get("confidence"),
            "同日事实": facts,
            "可能关联（待验证）": note,
            "aligned": True,
        })
    return aligned


def _is_iso_date(v: Any) -> bool:
    """ISO 日期（YYYY-MM-DD，可带 T 时间部分）。"""
    if not isinstance(v, str):
        return False
    try:
        datetime.fromisoformat(v)
        return True
    except ValueError:
        return False


def validate_events_file(path: str | Path) -> tuple[bool, str]:
    """事件文件校验（JSON Lines）。

    任一条目出现非 ISO 日期 / 缺 source_url / confidence 非法 → 整文件拒绝
    并报行号。返回 ``(ok, message)``。
    """
    p = Path(path)
    if not p.exists():
        return False, f"事件文件不存在: {p}"
    lines = p.read_text(encoding="utf-8").splitlines()
    if not lines:
        return False, f"事件文件为空: {p}"
    count = 0
    for i, line in enumerate(lines, start=1):
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError as exc:
            return False, f"第 {i} 行 JSON 解析失败: {exc}"
        if not isinstance(ev, dict):
            return False, f"第 {i} 行非 JSON 对象（应为一事件条目）"
        if not _is_iso_date(ev.get("date")):
            return False, f"第 {i} 行 date 非 ISO 日期: {ev.get('date')!r}"
        if not str(ev.get("event") or "").strip():
            return False, f"第 {i} 行缺 event"
        if not str(ev.get("source_url") or "").strip():
            return False, f"第 {i} 行缺 source_url（必填）"
        if not _is_iso_date(ev.get("published_date")):
            return False, f"第 {i} 行 published_date 非 ISO 日期: {ev.get('published_date')!r}"
        if ev.get("confidence") not in CONFIDENCE_LEVELS:
            return False, (
                f"第 {i} 行 confidence 非法: {ev.get('confidence')!r}"
                f"（须 {'/'.join(CONFIDENCE_LEVELS)}）"
            )
        count += 1
    if count == 0:
        return False, f"事件文件无有效条目: {p}"
    return True, f"校验通过: {count} 条事件"


def load_events_file(path: str | Path) -> tuple[list[dict] | None, str]:
    """校验并加载事件文件。校验失败返回 ``(None, message)``。"""
    ok, msg = validate_events_file(path)
    if not ok:
        return None, msg
    events = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        events.append(json.loads(line))
    return events, msg
