"""个股限售解禁队列源（共享模块）——invest-a-event-calendar v2 / invest-a-stock catalyst。

数据源：akshare ``stock_restricted_release_queue_em``（东财，单标的）。

设计要点（R1 审查教训内化）：
- **错误不静默**：返回 ``(rows, error)``——error=None 表示取数成功（含合法空结果），
  否则为失败原因字符串；调用方必须区分「无解禁记录」与「取数失败」。
- 窗口过滤与列名口径沿用 catalyst.py 既有实现（解禁数量 股→亿；股东数 NaN 容错）。
- 不做缓存（调用方按需处理）；akshare 代理直连经 ``lib.proxy.akshare_direct_session``。
"""

from __future__ import annotations

import datetime as _dt
from typing import Any

ONE_PER_YI = 1e8


def _clean_str(v: Any) -> str | None:
    """字符串清洗（None/NaN/空串 → None）。"""
    if v is None:
        return None
    s = str(v).strip()
    if not s or s.lower() in ("nan", "none", "<na>", "nat"):
        return None
    return s


def _safe_int(v: Any) -> int | None:
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f:  # NaN
        return None
    return int(f)


def _parse_unlock_date(raw: Any) -> _dt.date | None:
    """解禁时间 → date；兼容 '2026-11-05' / '20261105' / datetime 对象。"""
    if raw is None:
        return None
    if isinstance(raw, _dt.datetime):
        return raw.date()
    if isinstance(raw, _dt.date):
        return raw
    s = _clean_str(raw)
    if not s:
        return None
    digits = "".join(ch for ch in s if ch.isdigit())
    if len(digits) < 8:
        return None
    try:
        return _dt.datetime.strptime(digits[:8], "%Y%m%d").date()
    except ValueError:
        return None


def fetch_symbol_unlocks(symbol: str, *, lookahead_days: int = 90,
                         today: _dt.date | None = None,
                         include_past_days: int = 0) -> tuple[list[dict], str | None]:
    """拉取单标的解禁队列并过滤窗口。

    Returns
    -------
    (rows, error)
        rows: [{date: "YYYY-MM-DD", qty_yi: float | None, holders: int | None,
                kind: str}]，窗口内按日期升序；date 为字符串便于 JSON 状态文件。
        error: None=成功（含空 rows）；否则失败原因（网络/权限/接口变更）。

    窗口 = [today - include_past_days, today + lookahead_days]。默认只看未来。
    """
    today = today or _dt.date.today()
    try:
        from lib.proxy import akshare_direct_session

        with akshare_direct_session():
            import akshare as ak

            df = ak.stock_restricted_release_queue_em(symbol=symbol)
    except Exception as exc:  # noqa: BLE001 — 失败原因必须显式返回（不静默空）
        return [], f"{type(exc).__name__}: {str(exc)[:120]}"

    if df is None:
        return [], "接口返回 None（非预期形态，疑接口变更）"
    if df.empty:
        return [], None

    lo = today - _dt.timedelta(days=include_past_days)
    hi = today + _dt.timedelta(days=lookahead_days)
    rows: list[dict] = []
    for _, raw in df.iterrows():
        d = _parse_unlock_date(raw.get("解禁时间"))
        if d is None or not (lo <= d <= hi):
            continue
        try:
            shares = float(raw.get("解禁数量") or 0)
        except (TypeError, ValueError):
            shares = 0.0
        rows.append({
            "date": d.strftime("%Y-%m-%d"),
            "qty_yi": round(shares / ONE_PER_YI, 4) if shares > 0 else None,
            "holders": _safe_int(raw.get("解禁股东数")),
            "kind": _clean_str(raw.get("限售股类型")) or "",
        })
    rows.sort(key=lambda r: r["date"])
    return rows, None
