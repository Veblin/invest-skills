"""backtest 纯函数库单元测试 — 全部 fixture 数据，不联网。"""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

import pytest

_SKILLS_LIB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SKILLS_LIB))  # 无条件插 0：防其他 skill 目录先行入 path 遮蔽同名模块

from backtest import (  # noqa: E402
    cohen_d,
    daily_returns,
    describe,
    in_window,
    permutation_test,
    rolling_span_effects,
    significance_grade,
    split_window,
    welch_t,
    yearly_effects,
)

D = dt.date


def _rows_from_closes(closes: list[float], start: dt.date) -> list[dict]:
    """由收盘价序列构造 rows（自然日递增，够测试用）。"""
    return [{"date": start + dt.timedelta(days=i), "close": c} for i, c in enumerate(closes)]


def _rets(closes: list[float], start: dt.date) -> list[tuple[dt.date, float]]:
    return daily_returns(_rows_from_closes(closes, start))


class TestDailyReturns:
    def test_returns_pct(self):
        rets = _rets([100.0, 110.0, 99.0], D(2026, 1, 5))
        assert len(rets) == 2
        assert rets[0][1] == pytest.approx(10.0)
        assert rets[1][1] == pytest.approx(-10.0)

    def test_skips_zero_prev_close(self):
        rets = daily_returns([{"date": D(2026, 1, 5), "close": 0.0}, {"date": D(2026, 1, 6), "close": 10.0}])
        assert rets == []

    def test_too_few_rows(self):
        assert daily_returns([{"date": D(2026, 1, 5), "close": 1.0}]) == []


class TestWindowSplit:
    def test_in_window_boundaries(self):
        assert in_window(D(2026, 8, 15))
        assert in_window(D(2026, 8, 31))
        assert not in_window(D(2026, 8, 14))
        assert not in_window(D(2026, 9, 1))
        assert not in_window(D(2026, 7, 31))

    def test_split_window(self):
        rets = [
            (D(2026, 8, 14), 1.0),
            (D(2026, 8, 15), 2.0),
            (D(2026, 8, 20), 3.0),
            (D(2026, 8, 31), 4.0),
            (D(2026, 9, 1), 5.0),
        ]
        inside, outside = split_window(rets)
        assert inside == [2.0, 3.0, 4.0]
        assert outside == [1.0, 5.0]


class TestWelchT:
    def test_known_result(self):
        # 手工公式对照：a=[1,2,3], b=[4,5,6] → mean 差 3，样本方差各 1
        a = [1.0, 2.0, 3.0]
        b = [4.0, 5.0, 6.0]
        t, dof = welch_t(a, b)
        # t = (2-5)/sqrt(1/3+1/3) = -3/sqrt(2/3) = -3.6742346...
        assert t == pytest.approx(-3.6742346, abs=1e-5)
        # dof = (2/3)^2 / (2*(1/3)^2/2) = 4/9 / (1/9) = 4
        assert dof == pytest.approx(4.0)

    def test_identical_groups_zero_t(self):
        a = [1.0, 2.0, 3.0, 4.0]
        t, _ = welch_t(a, list(a))
        assert t == 0.0

    def test_fail_loud_small_sample(self):
        with pytest.raises(ValueError):
            welch_t([1.0], [1.0, 2.0])
        with pytest.raises(ValueError):
            welch_t([], [1.0, 2.0])


class TestPermutation:
    def test_deterministic_seed(self):
        a = [1.0, 2.0, 3.0, 4.0, 5.0]
        b = [6.0, 7.0, 8.0, 9.0, 10.0]
        r1 = permutation_test(a, b, n_perm=500, seed=42)
        r2 = permutation_test(a, b, n_perm=500, seed=42)
        assert r1 == r2

    def test_same_distribution_high_p(self):
        # 同分布两组 → p 不应显著（应远大于 0.05）
        a = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
        r = permutation_test(a, list(a), n_perm=200, seed=7)
        assert r["p_value"] > 0.05

    def test_p_bounds(self):
        a = [0.0, 0.1, -0.1, 0.2] * 5
        b = [10.0, 9.9, 10.1, 10.0] * 5
        r = permutation_test(a, b, n_perm=100, seed=1)
        assert 0.0 <= r["p_value"] <= 1.0
        assert r["observed"] > 0

    def test_zero_variance_undefined(self):
        with pytest.raises(ValueError):
            permutation_test([0.0, 0.0, 0.0], [10.0, 10.0, 10.0])


class TestDescribe:
    def test_describe(self):
        d = describe([1.0, -2.0, 3.0, -4.0])
        assert d["n"] == 4
        assert d["mean_daily_pct"] == pytest.approx(-0.5)
        assert d["down_prob"] == pytest.approx(0.5)
        assert d["up_prob"] == pytest.approx(0.5)
        assert d["median_daily_pct"] == pytest.approx(-0.5)

    def test_fail_loud_empty(self):
        with pytest.raises(ValueError):
            describe([])


class TestCohenD:
    def test_cohen_d(self):
        # 合并方差 0.5 → 合并标准差 ≈0.7071；d = (1.5-3.5)/0.7071 ≈ -2.8284
        assert cohen_d([1.0, 2.0], [3.0, 4.0]) == pytest.approx(-2.8284, abs=1e-4)


class TestYearlyAndRolling:
    def test_yearly_effects(self):
        # 2025: 窗口内 [2,4] 窗口外 [1]；2026: 窗口内 [3] 窗口外 [5]
        rets = [
            (D(2025, 3, 1), 1.0),
            (D(2025, 8, 20), 2.0),
            (D(2025, 8, 21), 4.0),
            (D(2026, 2, 1), 5.0),
            (D(2026, 8, 15), 3.0),
        ]
        y = yearly_effects(rets)
        assert [row["year"] for row in y] == [2025, 2026]
        assert y[0]["n_in"] == 2 and y[0]["mean_in_pct"] == pytest.approx(3.0)
        assert y[0]["n_out"] == 1 and y[0]["mean_out_pct"] == pytest.approx(1.0)
        assert y[0]["diff_pct"] == pytest.approx(2.0)

    def test_rolling_5y(self):
        rets = [(D(y, 8, 15), 2.0) for y in range(2010, 2017)]
        rets += [(D(y, 3, 1), 1.0) for y in range(2010, 2017)]
        spans = rolling_span_effects(rets, span_years=5)
        assert len(spans) == 3  # 7 年逐年效应 → 2010-2014 / 2011-2015 / 2012-2016
        assert spans[0]["span"] == "2010-2014"

    def test_rolling_too_short(self):
        rets = [(D(2025, 8, 15), 2.0), (D(2025, 3, 1), 1.0)]
        assert rolling_span_effects(rets, span_years=5) == []


class TestGrade:
    def test_grade_thresholds(self):
        assert significance_grade(3.0) == "✅"
        assert significance_grade(2.0) == "⚠️"
        assert significance_grade(1.99) == "❌"
        assert significance_grade(-3.5) == "❌"