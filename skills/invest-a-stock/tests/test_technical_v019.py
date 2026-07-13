"""Tests for v0.1.9 technical extensions."""

from __future__ import annotations

import math


def _kline(n: int = 120, base: float = 100.0) -> list[dict]:
    rows = []
    for i in range(n):
        c = base + math.sin(i / 5) * 5 + i * 0.1
        rows.append({
            "trade_date": f"2024{(i // 28 + 1):02d}{(i % 28 + 1):02d}",
            "open": c, "high": c + 1, "low": c - 1, "close": c, "vol": 1e6,
        })
    return rows


class TestTechnicalV019:
    def test_ichimoku_summary(self):
        from lib.technical import ichimoku_summary

        k = _kline(120)
        highs = [r["high"] for r in k]
        lows = [r["low"] for r in k]
        closes = [r["close"] for r in k]
        out = ichimoku_summary(highs, lows, closes)
        assert out.get("tenkan_latest") is not None
        assert out.get("price_vs_cloud") in ("云带上方", "云带下方", "云带内部")

    def test_volatility_cone(self):
        from lib.technical import volatility_cone

        closes = [r["close"] for r in _kline(300)]
        cone = volatility_cone(closes)
        assert cone.get("current_hv") is not None
        assert cone.get("percentile") is not None

    def test_relative_strength(self):
        from lib.technical import relative_strength

        stock = [100 + i for i in range(60)]
        bench = [100 + i * 0.5 for i in range(60)]
        rs = relative_strength(stock, bench)
        assert rs["rs_latest"] is not None

    def test_rolling_beta(self):
        from lib.technical import rolling_beta

        stock = [100 * (1.01 ** i) for i in range(80)]
        bench = [100 * (1.005 ** i) for i in range(80)]
        beta = rolling_beta(stock, bench, windows=[60])
        assert "60" in beta["windows"]

    def test_compute_includes_ichimoku(self):
        from lib.technical import compute

        out = compute(_kline(120))
        assert "ichimoku" in out
        assert "volatility_cone" in out
