"""journal 原 _percentile/_median 包装的委托实现测试（skills/lib/stats）。

query_data 的 _percentile/_median 包装已随 F5（复用 valuation_summary）删除；
其语义委托给 skills/lib/stats 的 percentile_rank_inclusive / median，
此处直接测试 canonical 实现（round_to=1 与旧 journal 口径一致）。
"""

from __future__ import annotations

from _invest_path import ensure_invest_a_scripts_on_path

ensure_invest_a_scripts_on_path()  # lib 包经 invest-a-stock scripts 路径解析

from lib.stats import median, percentile_rank_inclusive  # noqa: E402


class TestPercentile:
    def test_basic(self):
        pop = [10.0, 20.0, 30.0, 40.0, 50.0]
        assert percentile_rank_inclusive(pop, 30.0, round_to=1) == 60.0  # 3 of 5 at or below

    def test_max_reaches_100(self):
        pop = [10.0, 20.0, 30.0, 40.0, 50.0]
        assert percentile_rank_inclusive(pop, 50.0, round_to=1) == 100.0

    def test_all_equal(self):
        pop = [5.0, 5.0, 5.0]
        assert percentile_rank_inclusive(pop, 5.0, round_to=1) == 100.0

    def test_none_value(self):
        assert percentile_rank_inclusive([1.0, 2.0], None) is None

    def test_empty_pop(self):
        assert percentile_rank_inclusive([], 1.0) is None


class TestMedian:
    def test_odd(self):
        assert median([3.0, 1.0, 2.0]) == 2.0

    def test_even(self):
        assert median([1.0, 2.0, 3.0, 4.0]) == 2.5

    def test_empty(self):
        assert median([]) is None
