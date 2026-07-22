"""Shared A-share symbol / exchange / board helpers (Batch D)."""

from __future__ import annotations

__all__ = [
    "symbol_to_ts_code",
    "exchange_code",
    "classify_board",
    "market_label",
]


def symbol_to_ts_code(symbol: str) -> str:
    """6-digit code → Tushare ``ts_code`` (``600176.SH``).

    Rules aligned with ``collector._exchange_code``:
    ``6``/``9`` → SH; ``4``/``8`` → BJ; else → SZ.
    Invalid input returns ``""``.
    """
    s = str(symbol).strip()
    if not s.isdigit():
        return ""
    s = s.zfill(6)
    if len(s) != 6:
        return ""
    if s.startswith(("6", "9")):
        return f"{s}.SH"
    if s.startswith(("4", "8")):
        return f"{s}.BJ"
    return f"{s}.SZ"


def exchange_code(symbol: str) -> dict[str, str]:
    """Return exchange-specific code formats for a 6-digit A-share symbol.

    Keys: ``tushare`` (``600176.SH``), ``baostock`` (``sh.600176``),
    ``akshare`` (``sh600176``).
    """
    s = symbol.strip().zfill(6)
    if s.startswith(("6", "9")):
        return {"tushare": f"{s}.SH", "baostock": f"sh.{s}", "akshare": f"sh{s}"}
    if s.startswith(("4", "8")):
        return {"tushare": f"{s}.BJ", "baostock": f"bj.{s}", "akshare": f"bj{s}"}
    return {"tushare": f"{s}.SZ", "baostock": f"sz.{s}", "akshare": f"sz{s}"}


def classify_board(ts_code: str, market: str = "") -> str:
    """Infer board label from Tushare ``ts_code`` prefix or ``market`` field.

    Returns ``"主板"``, ``"创业板"``, or ``"科创板"``.
    """
    if market in ("主板", "创业板", "科创板"):
        return market
    if ts_code.startswith("688"):
        return "科创板"
    if ts_code.startswith(("300", "301")):
        return "创业板"
    return "主板"


def market_label(raw: str) -> str:
    """Map Tushare ``market`` field (numeric or Chinese) to a Chinese label."""
    text = str(raw or "").strip()
    if not text:
        return "未知"
    known_cn = {"主板", "创业板", "科创板", "北交所", "CDR"}
    if text in known_cn:
        return text
    mapping = {
        "0": "主板",
        "1": "创业板",
        "2": "科创板",
        "3": "北交所",
        "4": "CDR",
        "主板": "主板",
        "创业板": "创业板",
        "科创板": "科创板",
        "北交所": "北交所",
        "CDR": "CDR",
    }
    return mapping.get(text, text if text else f"未知({raw})")
