"""
大宗商品数据模块。

覆盖：
- get_gold_price: 黄金价格
- get_crude_price: 原油价格

Fallback：
  yfinance（GC=F, CL=F）→ FRED（DCOILWTICO, GOLDAMGBD228NLBR）
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


def _build_meta(
    source: str | None,
    source_group: str | None,
    fallback_chain: list[str],
    attempted_sources: list[str],
    latency_ms: float,
    success: bool,
) -> dict[str, Any]:
    return {
        "source": source or "none",
        "source_group": source_group or "unknown",
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "fallback_chain": fallback_chain,
        "confidence": "high" if (source and "fred" in str(source)) else "medium",
        "latency_ms": round(latency_ms, 1),
        "success": success,
        "error_type": None if success else "empty",
    }


def _safe_float(val: Any) -> float | None:
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


# ------------------------------------------------------------------
# get_gold_price
# ------------------------------------------------------------------

def get_gold_price() -> dict[str, Any]:
    """获取黄金价格。

    Returns:
        dict: {price, currency, unit, fetched_at, source, _meta}
    """
    attempted_sources: list[str] = []
    result: dict[str, Any] = {}
    source = ""
    source_group = ""
    start = datetime.now(timezone.utc)

    fallback_chain = ["yfinance(GC=F)", "FRED(GOLDAMGBD228NLBR)"]

    # yfinance
    try:
        import yfinance as yf
        attempted_sources.append("yfinance")
        gold = yf.Ticker("GC=F")
        hist = gold.history(period="1d")
        if not hist.empty:
            result = {
                "price": float(hist["Close"].iloc[-1]),
                "currency": "USD",
                "unit": "per troy ounce",
                "source": "yfinance(GC=F)",
                "fetched_at": datetime.now(timezone.utc).isoformat(),
            }
            source = "yfinance.GC=F"
            source_group = "global"
    except Exception as e:
        logger.warning("yfinance 黄金价格获取失败: %s", e)

    # FRED fallback
    if not result:
        try:
            from scripts.lib.global_macro import get_fred_series
            attempted_sources.append("fred")
            series = get_fred_series("GOLDAMGBD228NLBR")
            data = series.get("data")
            if data is not None and len(data) > 0:
                latest = float(data.dropna().iloc[-1])
                result = {
                    "price": latest,
                    "currency": "USD",
                    "unit": "per troy ounce (London PM fix)",
                    "source": "FRED(GOLDAMGBD228NLBR)",
                    "fetched_at": datetime.now(timezone.utc).isoformat(),
                }
                source = "FRED.GOLDAMGBD228NLBR"
                source_group = "official"
        except Exception as e:
            logger.warning("FRED 黄金价格获取失败: %s", e)

    latency = (datetime.now(timezone.utc) - start).total_seconds() * 1000

    if not result:
        result = {"error": "黄金价格不可得", "attempted_sources": attempted_sources}

    result["_meta"] = _build_meta(
        source=source or None,
        source_group=source_group or None,
        fallback_chain=fallback_chain,
        attempted_sources=attempted_sources,
        latency_ms=latency,
        success=bool(source),
    )
    return result


# ------------------------------------------------------------------
# get_crude_price
# ------------------------------------------------------------------

def get_crude_price() -> dict[str, Any]:
    """获取原油价格（WTI）。

    Returns:
        dict: {price, currency, unit, fetched_at, source, _meta}
    """
    attempted_sources: list[str] = []
    result: dict[str, Any] = {}
    source = ""
    source_group = ""
    start = datetime.now(timezone.utc)

    fallback_chain = ["yfinance(CL=F)", "FRED(DCOILWTICO)"]

    # yfinance
    try:
        import yfinance as yf
        attempted_sources.append("yfinance")
        crude = yf.Ticker("CL=F")
        hist = crude.history(period="1d")
        if not hist.empty:
            result = {
                "price": float(hist["Close"].iloc[-1]),
                "currency": "USD",
                "unit": "per barrel (WTI)",
                "source": "yfinance(CL=F)",
                "fetched_at": datetime.now(timezone.utc).isoformat(),
            }
            source = "yfinance.CL=F"
            source_group = "global"
    except Exception as e:
        logger.warning("yfinance 原油价格获取失败: %s", e)

    # FRED
    if not result:
        try:
            from scripts.lib.global_macro import get_fred_series
            attempted_sources.append("fred")
            series = get_fred_series("DCOILWTICO")
            data = series.get("data")
            if data is not None and len(data) > 0:
                latest = float(data.dropna().iloc[-1])
                result = {
                    "price": latest,
                    "currency": "USD",
                    "unit": "per barrel (WTI spot)",
                    "source": "FRED(DCOILWTICO)",
                    "fetched_at": datetime.now(timezone.utc).isoformat(),
                }
                source = "FRED.DCOILWTICO"
                source_group = "official"
        except Exception as e:
            logger.warning("FRED 原油价格获取失败: %s", e)

    latency = (datetime.now(timezone.utc) - start).total_seconds() * 1000

    if not result:
        result = {"error": "原油价格不可得", "attempted_sources": attempted_sources}

    result["_meta"] = _build_meta(
        source=source or None,
        source_group=source_group or None,
        fallback_chain=fallback_chain,
        attempted_sources=attempted_sources,
        latency_ms=latency,
        success=bool(source),
    )
    return result


# ------------------------------------------------------------------
# 测试入口
# ------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    print("=== 黄金价格 ===")
    gold = get_gold_price()
    print(gold)

    print("\n=== 原油价格 ===")
    crude = get_crude_price()
    print(crude)
