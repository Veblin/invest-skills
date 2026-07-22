"""Shared stock-test helpers (avoid bare `from conftest import` — clashes with other skills)."""
from __future__ import annotations

from typing import Any

# 合规：用户可见输出禁止词（AGENTS.md / v0.1.2 plan §0.8）
FORBIDDEN_SIGNAL_WORDS = (
    "金叉", "死叉", "买入", "卖出", "抄底", "追涨", "建仓", "目标价",
)


def make_store_collection(
    symbol: str = "000001",
    fetched_at: str = "2026-06-01T00:00:00Z",
    quote_close: float = 10.0,
) -> dict[str, Any]:
    """构建最小 collection dict（store / diff 测试用）。"""
    return {
        "symbol": symbol,
        "fetched_at": fetched_at,
        "dimensions": [
            {
                "dimension": "basic_info",
                "display": "基本信息",
                "data": {"name": "测试银行", "industry": "银行"},
                "status": "available",
                "_meta": {"source": "test"},
            },
            {
                "dimension": "quote",
                "display": "实时行情",
                "data": {"close": quote_close, "vol": 1_000_000},
                "status": "available",
                "_meta": {"source": "test"},
            },
            {
                "dimension": "kline",
                "display": "日K线",
                "data": [
                    {"trade_date": "20260101", "close": 9.0, "open": 9.0,
                     "high": 9.5, "low": 8.8, "vol": 1e6},
                    {"trade_date": "20260102", "close": quote_close, "open": 9.2,
                     "high": quote_close + 0.5, "low": 9.0, "vol": 1.1e6},
                ],
                "status": "available",
                "_meta": {"source": "test"},
            },
        ],
        "summary": {"total": 3, "available": 3, "degraded": 0, "missing": 0},
    }
