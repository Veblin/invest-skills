"""H6 事件检测纯函数测试 — fixture DataFrame，不联网。

预注册口径：① MA20 日内穿越 ② BOLL 下轨触及 ③ 近 60 日向上缺口首次回探；
排除 pct_chg ≤ −9.5；ADX 分层 ranging(<20)/trending(≥25)/中间带不分组。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pandas as pd
import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO_ROOT / "skills"))
sys.path.insert(0, str(_REPO_ROOT / "skills" / "lib"))

_spec = importlib.util.spec_from_file_location("backtest_h6", _REPO_ROOT / "scripts" / "backtest_h6.py")
h6 = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(h6)


def _frame(closes, highs, lows, pct=0.5) -> pd.DataFrame:
    n = len(closes)
    return pd.DataFrame({
        "date": [f"2026-01-{i + 1:02d}" for i in range(n)],
        "open": closes, "high": highs, "low": lows, "close": closes,
        "pct_chg": [pct] * n,
    })


class TestComputeIndicators:
    def test_indicators_attached(self):
        closes = [100.0 + i * 0.1 for i in range(100)]
        df = _frame(closes, [c + 1 for c in closes], [c - 1 for c in closes])
        out = h6.compute_indicators(df)
        assert "ma20" in out.columns and "boll_lower" in out.columns and "adx14" in out.columns
        # 单边上涨 → ADX 高（趋势市）
        assert out["adx14"].iloc[-1] is not None and out["adx14"].iloc[-1] > 25


class TestDetectEvents:
    def test_ma20_cross_touch(self):
        # 构造：价格从上方回落穿越 MA20 的 V 形
        closes = [100.0] * 30 + [95.0, 94.0, 93.0, 94.0, 95.0] + [96.0] * 30
        highs = [c + 2 for c in closes]
        lows = [c - 2 for c in closes]
        df = h6.compute_indicators(_frame(closes, highs, lows))
        events = h6.detect_events(df)
        types = {e["type"] for e in events}
        # V 形跌破 MA20 → 日内穿越事件必存在
        assert "ma20" in types

    def test_drop_day_excluded(self):
        closes = [100.0] * 30 + [90.0] + [95.0] * 30
        highs = [c + 2 for c in closes]
        lows = [c - 2 for c in closes]
        df = _frame(closes, highs, lows, pct=-9.8)
        df = h6.compute_indicators(df)
        events = h6.detect_events(df)
        # 大跌日 pct=-9.8 → 该日不计事件（其余日 pct 正常但事件须不落在跌停日）
        assert all(df["pct_chg"].iloc[e["idx"]] > -9.5 for e in events)

    def test_gap_first_retest(self):
        # 缺口形成（第 62 日跳空高开，避开 GAP_LOOKBACK warmup 区），
        # 之后首次回探缺口下沿（i=72）计事件；二次回探不再计（首探后 prior_touch=True）
        closes = [100.0] * 62 + [108.0] * 10 + [102.0, 103.0, 104.0] + [101.0] * 20
        highs = [c + 2 for c in closes]
        lows = [c - 2 for c in closes]
        lows[62] = 106.0  # 跳空：第 63 日 low 106 > 第 62 日 high 102
        df = h6.compute_indicators(_frame(closes, highs, lows))
        gap_events = [e for e in h6.detect_events(df) if e["type"] == "gap"]
        assert len(gap_events) >= 1
        # 首探在 i=72（102 段首日，low=100 ≤ g_lo=102）；之后各日 prior_touch=True
        # 不再重复计（二次回探不计数）
        assert 72 in [e["idx"] for e in gap_events]
        assert not any(e["idx"] > 72 for e in gap_events)

    def test_middle_band_excluded(self):
        # ADX 中间带（20-25）不分组：用横盘 + 微趋势序列使 ADX 落中间带难以精确构造，
        # 此处只验证 detect_events 输出的 regime 仅含 ranging/trending
        closes = [100.0 + (i % 5) * 0.5 for i in range(120)]
        highs = [c + 1 for c in closes]
        lows = [c - 1 for c in closes]
        df = h6.compute_indicators(_frame(closes, highs, lows))
        events = h6.detect_events(df)
        assert all(e["regime"] in ("ranging", "trending") for e in events)
