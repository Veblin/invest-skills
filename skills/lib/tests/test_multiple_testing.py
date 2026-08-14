"""multiple_testing 纯函数测试 — 合成数据，不联网。"""

from __future__ import annotations

import random
import sys
from pathlib import Path

import pytest

_SKILLS_LIB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SKILLS_LIB))

from multiple_testing import (  # noqa: E402
    bh_fdr,
    block_bootstrap_indices,
    bootstrap_ci,
    reality_check,
)


def _random_walk_rules(n_rules: int, t: int, seed: int) -> list[list[float]]:
    """K 条零均值随机规则（已扣基准，H0 为真）。"""
    rng = random.Random(seed)
    return [[rng.gauss(0, 1) for _ in range(t)] for _ in range(n_rules)]


class TestBlockBootstrap:
    def test_shape_and_bounds(self):
        idx = block_bootstrap_indices(100, block_size=5, n_boot=50, seed=1)
        assert len(idx) == 50
        for group in idx:
            assert len(group) == 100
            assert all(0 <= i < 100 for i in group)

    def test_deterministic_seed(self):
        a = block_bootstrap_indices(50, n_boot=20, seed=7)
        b = block_bootstrap_indices(50, n_boot=20, seed=7)
        assert a == b

    def test_fail_loud(self):
        with pytest.raises(ValueError):
            block_bootstrap_indices(1)
        with pytest.raises(ValueError):
            block_bootstrap_indices(10, block_size=0)


class TestRealityCheck:
    def test_noise_rules_not_significant(self):
        """K=20 条纯噪声规则 → RC p 应大（α=0.05 不显著）。"""
        cols = _random_walk_rules(20, 250, seed=3)  # K×T
        mat = [list(row) for row in zip(*cols)]     # T×K（行=交易日）
        r = reality_check(mat, n_boot=2000, seed=42)
        assert r["n_rules"] == 20
        assert r["p_value"] > 0.05

    def test_drift_rule_detected(self):
        """植入 1 条确定性漂移规则（常数 +0.5）→ p 小且 best_rule 命中。

        常数漂移列保证样本均值精确为 0.5，噪声规则（均值 ~N(0, 1/250)）
        不可能超越——测试不依赖随机运气。
        """
        cols = _random_walk_rules(19, 250, seed=3)
        cols.append([0.5] * 250)
        mat = [list(row) for row in zip(*cols)]
        r = reality_check(mat, n_boot=2000, seed=42)
        assert r["best_rule"] == 19
        assert r["p_value"] < 0.05

    def test_fail_loud(self):
        with pytest.raises(ValueError):
            reality_check([], n_boot=10)
        with pytest.raises(ValueError):
            reality_check([[1.0, 2.0]], n_boot=10)  # K=1
        with pytest.raises(ValueError):
            reality_check([[1.0, 2.0], [1.0]], n_boot=10)  # 列长不一致


class TestBhFdr:
    def test_known_sequence(self):
        # 经典例：p=[0.01,0.02,0.03,0.04,0.5] m=5
        # 排序后 BH：0.01*5/1=0.05 ≤0.05 ✓; 0.02*5/2=0.05 ✓;
        # 0.03*5/3=0.05 ✓; 0.04*5/4=0.05 ✓; 0.5*5/5=0.5 ✗ → 拒绝前 4
        r = bh_fdr([0.01, 0.02, 0.03, 0.04, 0.5])
        assert r["n_rejected"] == 4
        assert r["significant"] == [True, True, True, True, False]
        assert r["q_values"][0] == pytest.approx(0.05, abs=1e-9)
        assert r["q_values"][4] == pytest.approx(0.5)

    def test_none_handling(self):
        r = bh_fdr([None, 0.01, 0.9])
        assert r["significant"][0] is False
        assert r["q_values"][0] is None
        assert r["n_rejected"] == 1

    def test_empty(self):
        r = bh_fdr([])
        assert r["significant"] == [] and r["n_rejected"] == 0


class TestBootstrapCi:
    def test_covers_mean(self):
        rng = random.Random(5)
        xs = [rng.gauss(10.0, 1.0) for _ in range(200)]
        r = bootstrap_ci(xs, n_boot=5000, seed=42)
        assert r["lower"] < r["observed"] < r["upper"]
        assert r["lower"] < 10.0 + 0.2 and r["upper"] > 10.0 - 0.2  # 覆盖真均值

    def test_deterministic(self):
        xs = [1.0, 2.0, 3.0, 4.0, 5.0]
        assert bootstrap_ci(xs, n_boot=500, seed=1) == bootstrap_ci(xs, n_boot=500, seed=1)

    def test_fail_loud(self):
        with pytest.raises(ValueError):
            bootstrap_ci([1.0])
