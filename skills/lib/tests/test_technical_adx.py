"""technical.adx 回归测试 — Wilder 平均口径（累计均值种子 + 平均递推）。

背景（2026-08-15 /code-review #10）：_wilder 曾用「前 n 项和」种子配合
平均形式递推（v/n），早期条数被放大 ~n 倍后缓慢衰减，趋势反转序列上
前 ~30-50 根 ADX 最多偏高 ~53 点。参考实现锁定正确口径，防止回退。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO_ROOT / "skills"))
sys.path.insert(0, str(_REPO_ROOT / "skills" / "lib"))

from technical import adx  # noqa: E402


def _wilder_avg(vals: list[float], n: int) -> list[float]:
    """Wilder 平滑（平均口径参考实现）：累计均值种子 + 平均递推。"""
    out: list[float] = []
    for i, v in enumerate(vals):
        if i < n:
            out.append(sum(vals[: i + 1]) / (i + 1))
        else:
            out.append(out[-1] * (n - 1) / n + v / n)
    return out


def _adx_ref(highs: list[float], lows: list[float], closes: list[float], n: int = 14):
    """教科书 Wilder ADX（独立参考实现）：TR/±DM 平滑 → DI → DX → ADX。"""
    m = len(closes)
    out: list[float | None] = [None] * m
    if m < 2 * n:
        return out
    tr: list[float] = [0.0]
    plus_dm: list[float] = [0.0]
    minus_dm: list[float] = [0.0]
    for i in range(1, m):
        h_l = highs[i] - lows[i]
        h_pc = abs(highs[i] - closes[i - 1])
        l_pc = abs(lows[i] - closes[i - 1])
        tr.append(max(h_l, h_pc, l_pc))
        up = highs[i] - highs[i - 1]
        dn = lows[i - 1] - lows[i]
        plus_dm.append(up if (up > dn and up > 0) else 0.0)
        minus_dm.append(dn if (dn > up and dn > 0) else 0.0)
    tr_s = _wilder_avg(tr, n)
    plus_s = _wilder_avg(plus_dm, n)
    minus_s = _wilder_avg(minus_dm, n)
    dx_idx: list[int] = []
    dx_vals: list[float] = []
    for i in range(1, m):
        if tr_s[i] > 0:
            pdi = 100 * plus_s[i] / tr_s[i]
            mdi = 100 * minus_s[i] / tr_s[i]
            denom = pdi + mdi
            dx_vals.append(100 * abs(pdi - mdi) / denom if denom > 0 else 0.0)
            dx_idx.append(i)
    adx_s: list[float] = []
    for j, v in enumerate(dx_vals):
        if j < n:
            adx_s.append(sum(dx_vals[: j + 1]) / (j + 1))
        else:
            adx_s.append(adx_s[-1] * (n - 1) / n + v / n)
    for j in range(n - 1, len(adx_s)):
        if dx_idx[j] >= 2 * n - 1:
            out[dx_idx[j]] = round(adx_s[j], 2)
    return out


def _uptrend_then_reverse(n_each: int = 60):
    closes = [100.0 + i for i in range(n_each)] + [100.0 + n_each - i for i in range(n_each)]
    highs = [c + 1.5 for c in closes]
    lows = [c - 1.5 for c in closes]
    return highs, lows, closes


class TestAdxWilderConsistency:
    def test_matches_reference_on_regime_change(self):
        """趋势反转序列：逐条与参考实现一致（旧口径最多偏离 ~53 点）。"""
        highs, lows, closes = _uptrend_then_reverse()
        got = adx(highs, lows, closes)
        ref = _adx_ref(highs, lows, closes)
        assert len(got) == len(ref)
        for g, r in zip(got, ref):
            if g is None:
                assert r is None
            else:
                assert abs(g - r) <= 0.011

    def test_matches_reference_on_random_walk(self):
        """随机游走序列：逐条一致（种子口径错误同样会系统性偏离）。"""
        rng_state = 0x5EED
        closes: list[float] = [100.0]
        for _ in range(120):
            rng_state = (rng_state * 1103515245 + 12345) % (2**31)
            step = (rng_state % 501) / 100.0 - 2.5
            closes.append(closes[-1] + step)
        highs = [c + 0.8 for c in closes]
        lows = [c - 0.8 for c in closes]
        got = adx(highs, lows, closes)
        ref = _adx_ref(highs, lows, closes)
        for g, r in zip(got, ref):
            if g is None:
                assert r is None
            else:
                assert abs(g - r) <= 0.011

    def test_publish_range(self):
        """发布范围：2n−1 起全非 None，之前全 None。"""
        highs, lows, closes = _uptrend_then_reverse()
        got = adx(highs, lows, closes)
        assert all(v is None for v in got[:27])
        assert all(v is not None for v in got[27:])

    def test_steady_trend_is_100(self):
        """稳定单边趋势：+DM/TR 恒比例 → DX=100 → ADX=100（DI 比值不变量）。"""
        closes = [100.0 + 0.5 * i for i in range(80)]
        highs = [c + 0.4 for c in closes]
        lows = [c - 0.4 for c in closes]
        got = adx(highs, lows, closes)
        assert all(v is not None for v in got[27:])
        assert all(abs(v - 100.0) < 0.011 for v in got[27:])

    def test_short_series_all_none(self):
        assert adx([1.0] * 20, [0.9] * 20, [1.0] * 20) == [None] * 20


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
