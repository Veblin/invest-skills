"""Stock-path _compute_technical smoke (offline fixture)."""

from __future__ import annotations

from query_data import _compute_technical


def _stock_rows(n: int = 30, base: float = 10.0) -> list[dict]:
    rows = []
    for i in range(n):
        c = base + i * 0.1
        rows.append(
            {
                "trade_date": f"202601{i + 1:02d}" if i < 9 else f"202602{i - 8:02d}",
                "open": c,
                "high": c + 0.2,
                "low": c - 0.2,
                "close": c,
                "vol": 1e6,
            }
        )
    return rows


def test_compute_technical_stock_missing():
    result = {
        "asset_type": "stock",
        "kline": {"status": "missing", "data": []},
        "technical": {},
    }
    _compute_technical(result)
    assert result["technical"]["status"] == "missing"
    assert result["technical"]["kline_days"] == 0


def test_compute_technical_stock_insufficient():
    result = {
        "asset_type": "stock",
        "kline": {"status": "available", "data": _stock_rows(10)},
        "technical": {},
    }
    _compute_technical(result)
    assert result["technical"]["status"] == "insufficient"


def test_compute_technical_stock_available():
    result = {
        "asset_type": "stock",
        "kline": {"status": "available", "data": _stock_rows(40)},
        "technical": {},
    }
    _compute_technical(result)
    assert result["technical"]["status"] == "available"
    assert result["technical"]["latest_close"] is not None
    assert result["technical"]["kline_days"] == 40
