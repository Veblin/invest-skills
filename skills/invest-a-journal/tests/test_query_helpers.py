"""Pure helpers from query_data — no network."""

from __future__ import annotations

import sys
from pathlib import Path

_LIB = Path(__file__).resolve().parent.parent / "scripts" / "lib"
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

from query_data import _median, _percentile  # noqa: E402


class TestPercentile:
    def test_basic(self):
        pop = [10.0, 20.0, 30.0, 40.0, 50.0]
        assert _percentile(30.0, pop) == 40.0  # 2 of 5 strictly below

    def test_none_value(self):
        assert _percentile(None, [1.0, 2.0]) is None

    def test_empty_pop(self):
        assert _percentile(1.0, []) is None


class TestMedian:
    def test_odd(self):
        assert _median([3.0, 1.0, 2.0]) == 2.0

    def test_even(self):
        assert _median([1.0, 2.0, 3.0, 4.0]) == 2.5

    def test_empty(self):
        assert _median([]) is None
