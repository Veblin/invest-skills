"""Tests for skills/lib/data_util.py — 空值判定与逐 key 合并（无网络）。

覆盖 v0.2.3 补丁（/code-review max A9）：merge_first_non_empty 对 numpy
数组/pandas 容器不再 ValueError，NaN（含 np.float64）视为空值跳过。
"""

from __future__ import annotations

import sys
from pathlib import Path

_SKILLS_LIB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SKILLS_LIB))  # 无条件插 0：防其他 skill 目录先行入 path 遮蔽同名模块

import numpy as np
import pandas as pd

from data_util import merge_first_non_empty  # noqa: E402


class TestMergeFirstNonEmptyNumpy:
    def test_ndarray_value_passthrough(self):
        r = merge_first_non_empty([{"x": np.array([1.0, 2.0])}])
        assert r["x"].tolist() == [1.0, 2.0]

    def test_series_value_passthrough(self):
        s = pd.Series([1.0, 2.0])
        r = merge_first_non_empty([{"x": s}])
        assert r["x"] is s

    def test_dataframe_value_passthrough(self):
        df = pd.DataFrame({"a": [1, 2]})
        r = merge_first_non_empty([{"x": df}])
        assert r["x"] is df

    def test_float_nan_skipped(self):
        r = merge_first_non_empty([{"a": float("nan")}, {"a": 3.0}])
        assert r["a"] == 3.0

    def test_np_float64_nan_skipped(self):
        r = merge_first_non_empty([{"a": np.float64("nan")}, {"a": 3.0}])
        assert r["a"] == 3.0

    def test_mixed_first_wins(self):
        r = merge_first_non_empty([{"a": np.array([1.0])}, {"a": 2.0}])
        assert r["a"].tolist() == [1.0]

    def test_empty_values_none_semantics(self):
        # empty_values=(None,)（financial_rigor 口径）：NaN 仍视为空、"" 不算空
        r = merge_first_non_empty(
            [{"a": float("nan")}, {"a": ""}, {"a": "x"}], empty_values=(None,))
        assert r["a"] == ""
        r2 = merge_first_non_empty(
            [{"a": float("nan")}, {"a": 1.0}], empty_values=(None,))
        assert r2["a"] == 1.0

    def test_standard_empties_unchanged(self):
        assert merge_first_non_empty(
            [{"a": None}, {"a": []}, {"a": ""}, {"a": "x"}])["a"] == "x"
        # 0 非空
        assert merge_first_non_empty([{"a": 0}, {"a": 5}])["a"] == 0
