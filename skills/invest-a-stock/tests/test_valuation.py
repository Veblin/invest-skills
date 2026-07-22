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


class TestCalcOcfQuality:
    """calc_ocf_quality: EPS/OCFPS 按 end_date 对齐，且要求连续 4 季 TTM。"""

    @staticmethod
    def _fin_rows_mismatched_quarters() -> list[dict]:
        """5 期 EPS，最近一期缺 OCFPS；旧逻辑按位置取 last-4 会错位。"""
        return [
            {"end_date": "20230331", "eps": 1.0, "ocfps": 10.0},
            {"end_date": "20230630", "eps": 3.0, "ocfps": 30.0},
            {"end_date": "20230930", "eps": 6.0, "ocfps": 60.0},
            {"end_date": "20231231", "eps": 10.0, "ocfps": 100.0},
            {"end_date": "20240331", "eps": 5.0},  # 无 ocfps → 与 EPS 不匹配
        ]

    def test_aligns_eps_ocfps_by_end_date(self):
        from valuation_calc import calc_ocf_quality

        result = calc_ocf_quality(self._fin_rows_mismatched_quarters())
        assert "error" not in result
        # 匹配 20230331–20231231 四期：单季 EPS 1+2+3+4=10，OCFPS 10+20+30+40=100
        assert result["ttm_eps"] == pytest.approx(10.0)
        assert result["ttm_ocfps"] == pytest.approx(100.0)
        assert result["ocf_np_ratio"] == pytest.approx(10.0)
        assert result["end_date"] == "20231231"

    def test_insufficient_matched_pairs(self):
        from valuation_calc import calc_ocf_quality

        rows = [
            {"end_date": "20230331", "eps": 1.0, "ocfps": 10.0},
            {"end_date": "20230630", "eps": 3.0},
            {"end_date": "20230930", "eps": 6.0},
            {"end_date": "20231231", "eps": 10.0},
            {"end_date": "20240331", "eps": 5.0},
        ]
        result = calc_ocf_quality(rows)
        assert result["ocf_np_ratio"] is None
        assert "数据不足" in result["error"]
        assert "匹配仅1期" in result["error"]

    def test_gapped_matched_dates_rejected(self):
        """匹配 last-4 非连续 → _latest_contiguous_ttm_dates 拒绝。"""
        from valuation_calc import _latest_contiguous_ttm_dates

        # 缺 20230930：任意 last-4 会跨断档
        matched = ["20230331", "20230630", "20231231", "20240331"]
        assert _latest_contiguous_ttm_dates(matched) is None

    def test_contiguous_helper_prefers_newest_complete_window(self):
        from valuation_calc import _latest_contiguous_ttm_dates

        matched = [
            "20220930", "20221231", "20230331", "20230630",
            "20231231", "20240331",  # 缺 0930，无法从这两点回推满 4 季
        ]
        assert _latest_contiguous_ttm_dates(matched) == [
            "20220930", "20221231", "20230331", "20230630",
        ]

    def test_falls_back_to_older_contiguous_window(self):
        """最新锚点断档时，回退到更早的连续 4 季。"""
        from valuation_calc import calc_ocf_quality

        # 2022H1 仅作 20220930 单季差的累计底数；窗口为 2022Q3–2023Q2。
        # 2023Q3 缺 ocfps → 2023Q4 无法做单季差；2024Q1 匹配但无法凑齐连续 4 季。
        rows = [
            {"end_date": "20220630", "eps": 2.0, "ocfps": 20.0},
            {"end_date": "20220930", "eps": 3.0, "ocfps": 30.0},   # 单季 1 / 10
            {"end_date": "20221231", "eps": 4.0, "ocfps": 40.0},   # 单季 1 / 10
            {"end_date": "20230331", "eps": 1.0, "ocfps": 10.0},   # 单季 1 / 10
            {"end_date": "20230630", "eps": 3.0, "ocfps": 30.0},   # 单季 2 / 20
            {"end_date": "20230930", "eps": 6.0},                 # 无 ocfps → 断档
            {"end_date": "20231231", "eps": 10.0, "ocfps": 100.0},  # 缺上期 ocf → 不进 ocf 单季
            {"end_date": "20240331", "eps": 5.0, "ocfps": 50.0},
        ]
        result = calc_ocf_quality(rows)
        assert "error" not in result
        assert result["end_date"] == "20230630"
        assert result["ttm_eps"] == pytest.approx(5.0)   # 1+1+1+2
        assert result["ttm_ocfps"] == pytest.approx(50.0)  # 10+10+10+20
        assert result["ocf_np_ratio"] == pytest.approx(10.0)

    def test_prev_report_end_date_chain(self):
        from valuation_calc import _prev_report_end_date

        assert _prev_report_end_date("20240331") == "20231231"
        assert _prev_report_end_date("20231231") == "20230930"
        assert _prev_report_end_date("20230630") == "20230331"
        assert _prev_report_end_date("bad") is None


class TestCalcHistoricalPercentile:
    """calc_historical_percentile: PE/PB 独立计算，不因一侧缺失丢弃另一侧。"""

    def test_pe_only_when_pb_empty(self):
        from valuation_calc import calc_historical_percentile

        daily_rows = [
            {"pe_ttm": 10.0, "pb": None},
            {"pe_ttm": 20.0, "pb": None},
            {"pe_ttm": 30.0, "pb": None},
        ]
        result = calc_historical_percentile(daily_rows)
        assert "error" not in result
        assert result["pe_current"] == 30.0
        assert result["pe_pct"] == pytest.approx(66.7, abs=0.1)
        assert result["pe_median"] == 20.0
        assert "pb_current" not in result
        assert "pb_pct" not in result

    def test_error_only_when_both_empty(self):
        from valuation_calc import calc_historical_percentile

        assert calc_historical_percentile([])["error"] == "PE/PB 历史数据不足"
        assert calc_historical_percentile([{"pe_ttm": None, "pb": None}])["error"] == "PE/PB 历史数据不足"

    def test_pb_only_when_pe_empty(self):
        from valuation_calc import calc_historical_percentile

        daily_rows = [
            {"pe_ttm": None, "pb": 1.0},
            {"pe_ttm": None, "pb": 2.0},
            {"pe_ttm": None, "pb": 3.0},
        ]
        result = calc_historical_percentile(daily_rows)
        assert "error" not in result
        assert result["pb_current"] == 3.0
        assert result["pb_pct"] == pytest.approx(66.7, abs=0.1)
        assert "pe_current" not in result
