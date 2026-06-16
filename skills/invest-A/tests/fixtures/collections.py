"""v0.1.2 报告渲染与 diff 测试用 collection fixture。"""

from __future__ import annotations

from typing import Any


def make_kline_rows(n: int = 60, *, descending: bool = False) -> list[dict]:
    """生成升序（默认）或降序 synthetic K 线。"""
    rows = []
    for i in range(n):
        close = 100.0 + i * 0.5
        rows.append({
            "trade_date": f"2025{1 + i // 28:02d}{(i % 28) + 1:02d}",
            "open": close - 0.3,
            "high": close + 0.5,
            "low": close - 0.5,
            "close": round(close, 2),
            "vol": 1_000_000 + i * 1000,
        })
    if descending:
        rows.reverse()
    return rows


def make_daily_basic_series(n: int = 50, *, descending: bool = False) -> list[dict]:
    """模拟 Tushare daily_basic 序列。"""
    rows = []
    for i in range(n):
        rows.append({
            "trade_date": f"2025{1 + i // 28:02d}{(i % 28) + 1:02d}",
            "pe_ttm": round(20.0 + i * 0.1, 2),
            "pb": round(3.0 + i * 0.02, 2),
            "ps_ttm": round(5.0 + i * 0.05, 2),
            "dv_ratio": 0.42,
        })
    if descending:
        rows.reverse()
    return rows


def _make_financials(descending: bool = False) -> list[dict]:
    """生成升序（默认）或降序财务报告序列（模拟 Tushare fina_indicator）。"""
    rows = [
        {"end_date": "20240331", "roe": 18.5, "eps": 2.1,
         "profit_dedt": 1e8, "revenue": 5e9, "net_profit": 1.2e8,
         "n_cashflow_act": 1.5e8, "ocf": 1.5e8},
        {"end_date": "20241231", "roe": 20.2, "eps": 2.4,
         "profit_dedt": 1.1e8, "revenue": 5.5e9, "net_profit": 1.3e8,
         "n_cashflow_act": 1.0e8, "ocf": 1.0e8},
    ]
    if descending:
        rows.reverse()
    return rows


def collection_kline_insufficient() -> dict[str, Any]:
    """K 线数据不足 26 条（MACD 不可得）的 collection。"""
    base = collection_v2_minimal()
    for dim in base["dimensions"]:
        if dim["dimension"] == "kline":
            dim["data"] = make_kline_rows(15)
    return base


def collection_v2_minimal(*, kline_descending: bool = False) -> dict[str, Any]:
    """最小八段报告所需维度（内存构造，不依赖网络）。"""
    return {
        "symbol": "600176",
        "fetched_at": "2026-06-11T12:00:00+00:00",
        "dimensions": [
            {
                "dimension": "basic_info",
                "display": "基本信息",
                "data": {
                    "name": "测试股份",
                    "industry": "电气设备",
                    "list_date": "20110330",
                    "area": "安徽",
                },
                "status": "available",
                "_meta": {"source": "test.fixture", "query_params": "fixture"},
            },
            {
                "dimension": "financials",
                "display": "财务报告",
                "data": _make_financials(kline_descending),
                "status": "available",
                "_meta": {"source": "test.fixture"},
            },
            {
                "dimension": "valuation",
                "display": "估值分析",
                "data": make_daily_basic_series(50, descending=kline_descending),
                "status": "available",
                "_meta": {"source": "tushare.daily_basic"},
            },
            {
                "dimension": "quote",
                "display": "实时行情",
                "data": {"close": 155.0, "change_pct": 1.2, "turnover_rate": 3.5},
                "status": "available",
                "_meta": {"source": "tencent_finance"},
            },
            {
                "dimension": "kline",
                "display": "日K线",
                "data": make_kline_rows(60, descending=kline_descending),
                "status": "available",
                "_meta": {"source": "tushare.daily"},
            },
        ],
        "summary": {"total": 5, "available": 5, "degraded": 0, "missing": 0},
    }


def collection_valuation_snapshot_only() -> dict[str, Any]:
    """无 Tushare 历史序列时的 valuation 降级（腾讯快照）。"""
    base = collection_v2_minimal()
    for dim in base["dimensions"]:
        if dim["dimension"] == "valuation":
            dim["data"] = {
                "pe_ttm": 25.8,
                "history_available": False,
            }
            dim["_meta"]["source"] = "tencent_finance"
    return base
