"""Tests for skills/lib/stats.py — pure helpers, no network.

覆盖 v0.2.3 共用库提升：median / percentile_rank_inclusive 新增，
percentile_rank 既有语义回归。
"""

from __future__ import annotations

import sys
from pathlib import Path

_SKILLS_LIB = Path(__file__).resolve().parents[1]
if str(_SKILLS_LIB) not in sys.path:
    sys.path.insert(0, str(_SKILLS_LIB))

from stats import median, percentile_rank, percentile_rank_inclusive  # noqa: E402


class TestMedian:
    def test_odd(self):
        assert median([3.0, 1.0, 2.0]) == 2.0

    def test_even(self):
        assert median([1.0, 2.0, 3.0, 4.0]) == 2.5

    def test_empty(self):
        assert median([]) is None


class TestPercentileRankInclusive:
    def test_basic(self):
        pop = [10.0, 20.0, 30.0, 40.0, 50.0]
        assert percentile_rank_inclusive(pop, 30.0) == 60.0  # 3 of 5 at or below

    def test_max_reaches_100(self):
        pop = [10.0, 20.0, 30.0, 40.0, 50.0]
        assert percentile_rank_inclusive(pop, 50.0) == 100.0

    def test_all_equal(self):
        assert percentile_rank_inclusive([5.0, 5.0, 5.0], 5.0) == 100.0

    def test_none_value(self):
        assert percentile_rank_inclusive([1.0, 2.0], None) is None

    def test_empty_pop(self):
        assert percentile_rank_inclusive([], 1.0) is None

    def test_round_to(self):
        assert percentile_rank_inclusive([1.0, 2.0, 3.0], 2.0, round_to=1) == 66.7

    def test_keeps_non_positive_values(self):
        # 含边界变体不剔除非正值（与 percentile_rank 的 >0 过滤互补）
        assert percentile_rank_inclusive([-5.0, 10.0, 20.0], 15.0) == 2 / 3 * 100


class TestPercentileRankRegression:
    def test_strict_less_than(self):
        pop = [10.0, 20.0, 30.0]
        assert percentile_rank(pop, 20.0) == 1 / 3 * 100  # 仅 10 < 20

    def test_filters_non_positive(self):
        # 负数不入分母：[-5, 10, 20] 有效样本 2 个
        assert percentile_rank([-5.0, 10.0, 20.0], 15.0) == 50.0

    def test_empty(self):
        assert percentile_rank([], 1.0) is None

    def test_all_non_positive_returns_none(self):
        assert percentile_rank([-1.0, -2.0], 1.0) is None
