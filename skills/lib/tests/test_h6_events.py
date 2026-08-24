"""H6 事件检测纯函数测试 — fixture DataFrame，不联网。

预注册口径：① MA20 日内穿越 ② BOLL 下轨触及 ③ 近 60 日向上缺口首次回探；
排除 pct_chg ≤ −9.5；ADX 分层 ranging(<20)/trending(≥25)/中间带不分组。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pandas as pd

_REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO_ROOT / "skills"))
sys.path.insert(0, str(_REPO_ROOT / "skills" / "lib"))

_spec = importlib.util.spec_from_file_location(
    "backtest_h6", _REPO_ROOT / "scripts" / "archive" / "backtest_h6.py")
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

    def test_gap_skips_retested_nearer_gap(self):
        """回归：最近的缺口已回探时不得 break，须继续向更老缺口找。

        g1（较新，idx 90，下沿 107）在 idx 95 已回探；g2（较老，idx 65，下沿 102）
        未回探。idx 100 首次触及 g2 下沿 → 必须计事件（旧实现 break 在 g1 上漏计）。
        直接置 ADX/指标列，只测缺口扫描逻辑。
        """
        closes = [100.0] * 65 + [105.0] * 25 + [110.0] * 5 + [108.0] * 5 + [102.0] * 11
        highs = [c + 2 for c in closes]
        lows = [c - 2 for c in closes]
        lows[65] = 104.0   # 缺口 g2：low 104 > highs[64] 102 → g_lo2 = 102
        lows[90] = 109.0   # 缺口 g1：low 109 > highs[89] 107 → g_lo1 = 107
        lows[95] = 106.0   # g1 首次回探（≤ 107）
        lows[100] = 100.0  # g2 首次触及（≤ 102）
        df = _frame(closes, highs, lows)
        df["adx14"] = 30.0
        df["ma20"] = float("nan")
        df["boll_lower"] = float("nan")
        gap_events = [e for e in h6.detect_events(df) if e["type"] == "gap"]
        assert 95 in [e["idx"] for e in gap_events]   # g1 首次回探
        assert 100 in [e["idx"] for e in gap_events]  # g2 首次回探（旧实现漏计）

    def test_middle_band_excluded(self):
        # ADX 中间带（20-25）不分组：用横盘 + 微趋势序列使 ADX 落中间带难以精确构造，
        # 此处只验证 detect_events 输出的 regime 仅含 ranging/trending
        closes = [100.0 + (i % 5) * 0.5 for i in range(120)]
        highs = [c + 1 for c in closes]
        lows = [c - 1 for c in closes]
        df = h6.compute_indicators(_frame(closes, highs, lows))
        events = h6.detect_events(df)
        assert all(e["regime"] in ("ranging", "trending") for e in events)

    def test_no_phantom_gap_at_series_start(self):
        """回归（finding #6）：i=60 扫描 g 不得下探到 0（highs[-1] 是未来数据）。

        高位平台后崩盘：lows[0]=99.6 > highs[-1]=50.4、lows[60]=49.6 <= 50.4、
        全程无真实向上缺口。旧实现以 highs[-1]（末日高点）为下沿在 i=60
        伪造 gap 事件（已实测复现 [(60, 0, 50.4)]）；修复后必须 0 事件。
        """
        closes = [100.0] * 60 + [50.0] * 20
        highs = [c + 0.4 for c in closes]
        lows = [c - 0.4 for c in closes]
        df = _frame(closes, highs, lows)
        df["adx14"] = 30.0
        df["ma20"] = float("nan")
        df["boll_lower"] = float("nan")
        gap_events = [e for e in h6.detect_events(df) if e["type"] == "gap"]
        assert gap_events == []
