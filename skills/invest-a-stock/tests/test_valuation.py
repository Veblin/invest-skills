"""估值模块单元测试。

测试覆盖:
  - percentile_rank 计算正确性
  - zone_label 30/70 阈值
  - null/负值过滤
  - 样本不足标注
  - valuation_summary 输出结构
"""

from __future__ import annotations

import pytest


class TestPercentileRank:
    def test_basic(self):
        """基本百分位计算。"""
        from lib.valuation import percentile_rank
        seq = [10.0, 20.0, 30.0, 40.0, 50.0]
        # current=25 → 低于 10,20 → 2/5 = 40%
        assert percentile_rank(seq, 25.0) == pytest.approx(40.0)
        # current=10 → 低于 0 个 → 0%
        assert percentile_rank(seq, 10.0) == pytest.approx(0.0)
        # current=60 → 低于全部 5 个 → 100%
        assert percentile_rank(seq, 60.0) == pytest.approx(100.0)

    def test_with_nulls(self):
        """含 null 和负值时过滤。"""
        from lib.valuation import percentile_rank
        seq = [10.0, None, -5.0, 20.0, 0.0, 30.0]
        # 有效正数: 10, 20, 30
        result = percentile_rank(seq, 25.0)
        assert result == pytest.approx(2 / 3 * 100)  # 低于 10,20

    def test_all_null(self):
        """全部为 null 返回 None。"""
        from lib.valuation import percentile_rank
        seq = [None, None]
        assert percentile_rank(seq, 10.0) is None

    def test_empty_seq(self):
        """空序列返回 None。"""
        from lib.valuation import percentile_rank
        assert percentile_rank([], 10.0) is None


class TestZoneLabel:
    def test_zones(self):
        from lib.valuation import zone_label
        assert zone_label(10.0) == "偏低"
        assert zone_label(29.9) == "偏低"
        assert zone_label(30.0) == "适中"
        assert zone_label(50.0) == "适中"
        assert zone_label(70.0) == "适中"
        assert zone_label(70.1) == "偏高"
        assert zone_label(90.0) == "偏高"


class TestValuationSummary:
    def _make_seq(self, n: int = 100) -> list[float]:
        """生成 mock PE 序列。"""
        import random
        rng = random.Random(42)
        return [20.0 + rng.uniform(0, 40) for _ in range(n)]

    def test_basic_output(self):
        """基本输出结构。"""
        from lib.valuation import valuation_summary
        pe_seq = self._make_seq(100)
        pb_seq = [p / 5 for p in pe_seq]
        result = valuation_summary(pe_seq, pb_seq)

        assert result["window_label"] == "近5年"
        assert result["n_samples"] == 100
        assert result["sufficient"] is True
        assert result["pe"]["current"] is not None
        assert result["pe"]["pct"] is not None
        assert result["pe"]["zone"] in ("偏低", "适中", "偏高")
        assert result["pb"]["current"] is not None
        assert "summary_text" in result

    def test_small_sample_warning(self):
        """样本不足 30 时产生警告。"""
        from lib.valuation import valuation_summary
        pe_seq = [20.0, 25.0, 30.0]  # 仅 3 条
        pb_seq = [4.0, 5.0, 6.0]
        result = valuation_summary(pe_seq, pb_seq)
        assert result["sufficient"] is False
        assert len(result["warnings"]) > 0
        assert "样本不足" in result["warnings"][0]

    def test_explicit_current(self):
        """显式传入 current 值。"""
        from lib.valuation import valuation_summary
        pe_seq = [10.0, 20.0, 30.0]
        pb_seq = [1.0, 2.0, 3.0]
        result = valuation_summary(pe_seq, pb_seq, current_pe=25.0, current_pb=2.5)
        assert result["pe"]["current"] == 25.0
        assert result["pb"]["current"] == 2.5

    def test_empty_sequences(self):
        """空序列时输出 reason。"""
        from lib.valuation import valuation_summary
        result = valuation_summary([], [])
        assert result["pe"]["reason"] is not None
        assert result["pb"]["reason"] is not None

    def test_null_filtering(self):
        """null 值被过滤。"""
        from lib.valuation import valuation_summary
        pe_seq = [None, 20.0, None, 30.0, -10.0, 0.0]
        pb_seq = [None, 4.0, None, 6.0]
        result = valuation_summary(pe_seq, pb_seq)
        assert result["n_samples"] == 2  # 仅 20, 30

    def test_dv_ratio(self):
        """股息率。"""
        from lib.valuation import valuation_summary
        result = valuation_summary([20.0, 30.0], [2.0, 3.0], dv_ratio=0.015)
        assert result["dv_ratio"] == 0.015

    def test_dv_ratio_passthrough_zero(self):
        """dv_ratio=0 时不被误判为 None。"""
        from lib.valuation import valuation_summary
        result = valuation_summary([20.0, 30.0], [2.0, 3.0], dv_ratio=0.0)
        assert result["dv_ratio"] == 0.0

    def test_dv_ratio_passthrough_none(self):
        """dv_ratio=None 时保持 None。"""
        from lib.valuation import valuation_summary
        result = valuation_summary([20.0, 30.0], [2.0, 3.0], dv_ratio=None)
        assert result["dv_ratio"] is None

    def test_ps_inferred_from_sequence(self):
        """未传 current_ps 时从序列末位推断（与 PE/PB 一致）。"""
        from lib.valuation import valuation_summary

        pe_seq = [10.0, 20.0, 30.0]
        pb_seq = [1.0, 2.0, 3.0]
        ps_seq = [4.0, 5.0, 6.5]
        result = valuation_summary(pe_seq, pb_seq, ps_seq=ps_seq)
        assert result["ps"]["current"] == 6.5
        assert result["ps"]["pct"] is not None


class TestValuationSufficientThreshold:
    """sufficient 阈值：≥30 条为 sufficient。"""

    def test_29_samples_insufficient(self):
        from lib.valuation import valuation_summary, percentile_rank
        pe = [20.0 + i * 0.1 for i in range(29)]
        pb = [3.0 + i * 0.02 for i in range(29)]
        result = valuation_summary(pe, pb)
        assert result["sufficient"] is False
        assert len(result["warnings"]) > 0
        assert "样本不足" in result["warnings"][0]

    def test_30_samples_sufficient(self):
        from lib.valuation import valuation_summary
        pe = [20.0 + i * 0.1 for i in range(30)]
        pb = [3.0 + i * 0.02 for i in range(30)]
        result = valuation_summary(pe, pb)
        assert result["sufficient"] is True
        assert len(result["warnings"]) == 0

    def test_31_samples_sufficient(self):
        from lib.valuation import valuation_summary
        pe = [20.0 + i * 0.1 for i in range(31)]
        pb = [3.0 + i * 0.02 for i in range(31)]
        result = valuation_summary(pe, pb)
        assert result["sufficient"] is True


class TestMedian:
    def test_odd(self):
        from lib.valuation import _median
        assert _median([1.0, 3.0, 2.0]) == 2.0

    def test_even(self):
        from lib.valuation import _median
        assert _median([1.0, 2.0, 3.0, 4.0]) == 2.5

    def test_empty(self):
        from lib.valuation import _median
        assert _median([]) is None


class TestCalcRoeAnnualized:
    """calc_roe_annualized: 报告期乘数与 roe_cumulative 字段."""

    @pytest.mark.parametrize(
        "end_date,roe,expected_ann",
        [
            ("20250331", 5.0, 20.0),
            ("20250630", 10.0, 20.0),
            ("20250930", 15.0, 20.0),
            ("20251231", 18.0, 18.0),
        ],
    )
    def test_period_multipliers(self, end_date: str, roe: float, expected_ann: float):
        from valuation_calc import calc_roe_annualized

        result = calc_roe_annualized([{"end_date": end_date, "roe": roe}])
        assert "roe_quarterly" not in result
        assert result["roe_cumulative"] == roe
        assert result["roe_annualized"] == pytest.approx(expected_ann)
        assert result["end_date"] == end_date

    def test_unknown_end_date_conservative_multiplier(self):
        from valuation_calc import calc_roe_annualized

        result = calc_roe_annualized([{"end_date": "", "roe": 8.0}])
        assert result["roe_cumulative"] == 8.0
        assert result["roe_annualized"] == 8.0
        assert result["end_date"] == ""
