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


class TestLossRatioStructured:
    """R12c: PE 亏损期占比结构化暴露（P0-2 标题标注的数据基础）。"""

    def test_loss_ratio_computed(self):
        from lib.valuation import valuation_summary

        # 30 个交易日：12 个亏损期（负 PE 被过滤），18 个正值
        pe_seq = [-5.0] * 12 + [20.0 + i for i in range(18)]
        result = valuation_summary(pe_seq, [2.0] * 30)
        pe = result["pe"]
        assert pe["loss_days"] == 12
        assert pe["loss_ratio"] == round(12 / 30, 4)
        assert pe["loss_ratio"] > 0.3  # 触发标题失真标注阈值

    def test_no_loss_ratio_zero(self):
        from lib.valuation import valuation_summary

        pe_seq = [20.0 + i for i in range(30)]
        result = valuation_summary(pe_seq, [2.0] * 30)
        assert result["pe"]["loss_days"] == 0
        assert result["pe"]["loss_ratio"] == 0.0


class TestSanitizeCpi:
    """R12c: CPI 口径归一（修复 107.1 → +107.1% 异常渲染）。"""

    def test_index_scale_converted(self):
        from lib.macro import _sanitize_cpi

        assert _sanitize_cpi(107.1) == 7.1  # 基期指数口径 → 同比

    def test_pct_passthrough(self):
        from lib.macro import _sanitize_cpi

        assert _sanitize_cpi(0.3) == 0.3
        assert _sanitize_cpi(-1.5) == -1.5

    def test_out_of_sane_range_rejected(self):
        from lib.macro import _sanitize_cpi

        assert _sanitize_cpi(999.0) is None
        assert _sanitize_cpi(50.0) is None


class TestMaterialGapReport:
    """R12c: 12 题数据缺口检查器。"""

    def _collection(self, **overrides):
        base = {
            "dimensions": [
                {"dimension": "financials", "data": [
                    {"end_date": "20260331", "roe": -1.85,
                     "grossprofit_margin": 9.59, "revenue": 355088600.0,
                     "netprofit_margin": -7.57, "n_cashflow_act": -16923620.0,
                     "profit_dedt": -1.0, "accounts_receiv": 1.0},
                ]},
                {"dimension": "valuation", "data": [
                    {"trade_date": "20260805", "pe_ttm": 6612.0, "pb": 9.71},
                ]},
                {"dimension": "market_structure", "data": {"sw_index": {"pct": -8.39}}},
            ],
        }
        return base

    def test_full_data_no_gap(self):
        from lib.render_utils import material_gap_report

        gap = material_gap_report(self._collection())
        missing = [q for q, s in gap.items() if not s["available"]]
        # 引擎可覆盖项无缺口；peer 类缺口标记 r12a
        assert "A-③ 毛利率 vs 行业" not in missing
        assert "B-① 护城河（ROE）" not in missing
        assert "D-① PE/PB 历史分位" not in missing
        assert gap["A-② 竞争位置"] == {"available": False, "requires": "r12a"}
        assert gap["D-② PE vs 行业中位"] == {"available": False, "requires": "r12a"}

    def test_missing_financials_marked(self):
        from lib.render_utils import material_gap_report

        coll = self._collection()
        coll["dimensions"][0] = {"dimension": "financials", "data": []}
        gap = material_gap_report(coll)
        assert gap["C-① 营收 CAGR（≥3 期）"]["available"] is False
        assert gap["C-① 营收 CAGR（≥3 期）"]["requires"] == "engine"


class TestSteadyEarnings:
    """R2: 稳态盈利估值（穿越周期视角）。"""

    def test_median_steady(self):
        from valuation_calc import calc_steady_earnings

        rows = [{"year": f"20{i:02d}1231", "net_profit": v}
                for i, v in enumerate([100.0, 80.0, 120.0, 90.0, 1700.0, 95.0, 110.0, 85.0, 105.0, 98.0])]
        # 周期峰值年 1700 存在 → 中位数不受峰值影响（海力士式场景）
        ste = calc_steady_earnings(rows)
        assert ste["available"] is True
        assert ste["steady_earnings"] == 99.0  # sorted: 80,85,90,95,98,100,105,110,120,1700 → (98+100)/2
        assert ste["n_years"] == 10

    def test_insufficient_sample(self):
        from valuation_calc import calc_steady_earnings

        rows = [{"year": "20241231", "net_profit": 100.0},
                {"year": "20251231", "net_profit": 110.0}]
        ste = calc_steady_earnings(rows)
        assert ste["available"] is False
        assert "样本" in ste["reason"]

    def test_cycle_range(self):
        from valuation_calc import calc_steady_earnings

        rows = [{"year": f"{2016 + i}1231", "net_profit": float(v)}
                for i, v in enumerate([10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0, 80.0, 90.0, 100.0])]
        ste = calc_steady_earnings(rows, cycle_start="20211231", cycle_end="20251231", method="range")
        assert ste["available"] is True
        assert ste["n_years"] == 5
        assert ste["steady_earnings"] == 80.0  # 2021~2025 → 60,70,80,90,100 → mean 80.0

    def test_band(self):
        from valuation_calc import steady_valuation_band

        ste = {"available": True, "steady_earnings": 100.0}
        band = steady_valuation_band(ste, 12.0)
        assert band is not None
        assert band["mid"] == 1200.0
        assert band["low"] == 900.0
        assert band["high"] == 1500.0

    def test_cycle_pe_precedence(self):
        from valuation_calc import calc_cycle_pe

        assert calc_cycle_pe(user_pe=7.0) == 7.0  # 用户覆盖
        assert calc_cycle_pe(industry="钢铁") == 8.0  # 行业配置
        assert calc_cycle_pe(industry="未知行业") == 12.0  # 默认


class TestEvEbitda:
    """R3: EV/EBITDA 企业价值桥接表。"""

    def test_bridge_full(self):
        from valuation_calc import calc_ev_ebitda

        r = calc_ev_ebitda(
            total_mv_yi=100.0, cash=5e8, st_loan=10e8,
            lt_loan=5e8, bond_payable=0.0, ebitda=20e8,
        )
        assert r["available"] is True
        b = r["bridge"]
        assert b["ev_yi"] == 110.0  # 100 + 15 - 5
        assert b["interest_debt_yi"] == 15.0
        assert r["ebitda_yi"] == 20.0
        assert r["ev_ebitda"] == 5.5

    def test_bridge_missing_debt_falls_back(self):
        from valuation_calc import calc_ev_ebitda

        r = calc_ev_ebitda(total_mv_yi=100.0, cash=5e8, ebitda=20e8)
        assert r["available"] is True
        assert r["debt_available"] is False
        assert r["bridge"]["ev_yi"] == 95.0  # 净现金口径
        assert "有息负债不可得" in r["note"]

    def test_financial_exempt(self):
        from valuation_calc import calc_ev_ebitda

        r = calc_ev_ebitda(total_mv_yi=100.0, cash=5e8, ebitda=20e8, industry="银行")
        assert r["available"] is False
        assert r["exempt"] is True

    def test_ebitda_missing_not_available(self):
        from valuation_calc import calc_ev_ebitda

        r = calc_ev_ebitda(total_mv_yi=100.0, cash=5e8, ebitda=None)
        assert r["available"] is False
        assert "ebitda" in r["missing"]


class TestIncomeDriver:
    """R1: 收益驱动假设分类（研究路径分流）。"""

    def _annual(self, vals):
        return [{"year": f"{2015 + i}1231", "net_profit": float(v)}
                for i, v in enumerate(vals)]

    def test_growth_driver(self):
        from lib.income_driver import classify_income_driver, DRIVER_GROWTH

        # 持续高增长 + FCF 强
        annual = self._annual([1.0, 1.5, 2.2, 3.1, 4.5, 6.0, 8.0, 11.0, 15.0, 20.0])
        fin = [{"fcff": 1e8 * i} for i in range(1, 7)]
        r = classify_income_driver(annual, fin)
        assert r["driver"] == DRIVER_GROWTH
        assert r["confidence"] in ("高", "中")

    def test_value_driver(self):
        from lib.income_driver import classify_income_driver, DRIVER_VALUE

        # 盈利平稳 + 连续分红
        annual = self._annual([10.0, 10.5, 9.8, 10.2, 10.1, 10.4, 10.0, 10.3, 10.2, 10.5])
        fin = [{"fcff": 5e8}] * 6
        r = classify_income_driver(annual, fin, div_years=8, div_yield=0.04)
        assert r["driver"] == DRIVER_VALUE

    def test_cycle_driver(self):
        from lib.income_driver import classify_income_driver, DRIVER_CYCLE

        # 剧烈波动 + 亏损年（海力士式）
        annual = self._annual([5.0, -3.0, 12.0, 2.0, -5.0, 20.0, 1.0, -2.0, 8.0, 30.0])
        fin = [{"fcff": 1e8}] * 5
        r = classify_income_driver(annual, fin)
        assert r["driver"] == DRIVER_CYCLE

    def test_unknown_when_insufficient(self):
        from lib.income_driver import classify_income_driver, DRIVER_UNKNOWN

        r = classify_income_driver(self._annual([1.0, 1.2, 1.4]), [])
        assert r["driver"] == DRIVER_UNKNOWN

    def test_missing_evidence_marked(self):
        from lib.income_driver import classify_income_driver

        r = classify_income_driver(self._annual([1.0, 1.2, 1.4, 1.5]), [])
        assert "dividend" in r["missing_evidence"]
        assert "refi" in r["missing_evidence"]


class TestLimitStreakDetector:
    """R12e: 近端价格结构检测（连板识别）。"""

    def _kline(self, closes, pcts=None):
        rows = []
        for i, c in enumerate(closes):
            row = {"trade_date": f"2026080{i+1:02d}", "close": float(c)}
            if pcts:
                row["change_pct"] = pcts[i]
            rows.append(row)
        return rows

    def test_three_consecutive_limit_ups(self):
        from lib.technical import detect_limit_streaks

        # 7-30 跌停 → 8-03 小跌 → 8-04/8-05/8-06 三连板（沃格实证结构）
        closes = [66.92, 64.94, 71.43, 78.57, 86.43]
        pcts = [-10.01, -5.61, 9.99, 10.00, 10.00]
        st = detect_limit_streaks(self._kline(closes, pcts), symbol="603773")
        assert st["available"] is True
        assert st["recent_limit_ups"] == 3
        assert st["recent_limit_downs"] == 1
        up_streaks = [s for s in st["streaks"] if s["type"] == "up"]
        assert up_streaks and up_streaks[0]["days"] == 3
        assert up_streaks[0]["total_pct"] == 33.1  # 71.43/64.94 起算? 实际 base=64.94 → 86.43/64.94-1
        # 实际断言用计算值：86.43/64.94-1 = 33.09 → 33.1

    def test_threshold_20pct_for_gem(self):
        from lib.technical import detect_limit_streaks

        closes = [10.0, 12.0, 14.4]  # 20% 涨停×2
        pcts = [0.0, 20.0, 20.0]
        st = detect_limit_streaks(self._kline(closes, pcts), symbol="300328")
        assert st["limit_threshold"] == 20.0
        assert st["recent_limit_ups"] == 2

    def test_window_pct_and_low(self):
        from lib.technical import detect_limit_streaks

        closes = [95.0, 85.5, 76.95, 64.94, 78.57]
        pcts = [-5.05, -10.0, -10.0, -5.61, 10.0]
        st = detect_limit_streaks(self._kline(closes, pcts), symbol="603773")
        assert st["window_pct"] == round((78.57 / 95.0 - 1) * 100, 2)
        assert st["period_low"]["value"] == 64.94
        down_streaks = [s for s in st["streaks"] if s["type"] == "down"]
        assert down_streaks and down_streaks[0]["days"] == 2  # 85.5/76.95 两连跌停
