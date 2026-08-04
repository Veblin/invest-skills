"""Shared A-share symbol / exchange / board helpers (Batch D)."""

from __future__ import annotations

__all__ = [
    "symbol_to_ts_code",
    "exchange_code",
    "classify_board",
    "market_label",
    "is_st_or_delisted",
    "ts_code_to_baostock",
    "etf_symbol_to_ts_code",
    "is_etf_symbol",
]


def symbol_to_ts_code(symbol: str) -> str:
    """6-digit code → Tushare ``ts_code`` (``600176.SH``).

    Rules aligned with ``collector._exchange_code``:
    ``6``/``9`` → SH; ``4``/``8`` → BJ; else → SZ.
    BSE 920xxx (trading since 2025-10) also starts with ``9`` → BJ.
    Invalid input returns ``""``.
    """
    s = str(symbol).strip()
    if not s.isdigit():
        return ""
    s = s.zfill(6)
    if len(s) != 6:
        return ""
    if s.startswith("920"):
        return f"{s}.BJ"
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
    s = symbol.strip()
    if not s.isdigit():
        raise ValueError(f"Invalid symbol: {symbol!r} (must be 1-6 digits)")
    s = s.zfill(6)
    if s.startswith("920"):
        return {"tushare": f"{s}.BJ", "baostock": f"bj.{s}", "akshare": f"bj{s}"}
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


def market_label(raw: str | None) -> str:
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
    }
    return mapping.get(text, f"未知({text})")


def is_st_or_delisted(name: str | None) -> bool:
    """Return True if stock name indicates ST or delisting (退市)."""
    if not name:
        return False
    up = name.upper()
    return "ST" in up or "退" in name


def ts_code_to_baostock(ts_code: str) -> str:
    """Convert Tushare ts_code (600176.SH) to baostock format (sh.600176).

    Uses the authoritative exchange suffix from the ts_code itself
    (``parts[1].lower()``), NOT digit-prefix inference — suffix rules differ
    from A-share prefix rules (BSE 920xxx, 5-prefix ETFs), so re-inferring
    the exchange would misroute them.

    Malformed input (no dot separator) is returned as-is for downstream
    graceful degradation.
    """
    parts = ts_code.strip().split(".")
    if len(parts) != 2:
        return ts_code
    return f"{parts[1].lower()}.{parts[0]}"


def etf_symbol_to_ts_code(symbol: str) -> str:
    """ETF code → Tushare ts_code. ETF rules: 5/6→SH, 0/1/3→SZ, 4/8→BJ.

    Distinct from :func:`symbol_to_ts_code` (A-share rules: 5xxxxx → SZ);
    ETF 5-prefix codes are Shanghai-listed. BSE 920xxx funds → BJ.
    """
    s = str(symbol).strip()
    if not s.isdigit():
        return ""
    if s.startswith("920"):
        return f"{s}.BJ"
    if s.startswith(("5", "6")):
        return f"{s}.SH"
    if s.startswith(("4", "8")):
        return f"{s}.BJ"
    return f"{s}.SZ"


def is_etf_symbol(symbol: str) -> bool:
    """6 位代码是否为 ETF（报告 QC 判定用，非交易所路由用）。

    ETF 代码：159xxx（深市）、51xxxx/56xxxx/58xxxx（沪市）。
    920xxx 恒为 False：北交所 2025-10 起既有基金也有股票，前缀无法区分——
    报告 QC 按股票处理（股票报告必须走 audit/quality/rigor；基金报告误走
    stock 检查只会产生可见告警，优于静默跳过）。
    无法识别前缀时返回 False（按 stock 兜底，报告内容仍可 lint）。
    前缀政策集中于此（report_qc 曾自维护一份并在此规则上发生过分歧）。
    """
    return str(symbol).startswith(("159", "51", "56", "58"))
