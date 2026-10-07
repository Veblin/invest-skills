"""v0.3.1 主线收尾（2026-10-07）引擎侧反例测试：REV-01/02/04/07 与 R15。

原发现：`host-docs/v0.3.1/报告复检_600519_20261005_175318/review.md`
（REV-01 累计利润混期 / REV-02 ERP 来源标签 / REV-04 g_implied 与营收 CAGR
比较 / REV-07 CAPEX 评分混期）与 `三轮收敛_20261005/round-8/codex-review.md`
（R15 低 PE「只能」穷尽解释）。R14（事件表标题格）检查器反例在
`test_report_qc_gates.py`。
"""
from __future__ import annotations


class TestRev01ProfitDirection:
    """REV-01：累计期不得直接相减——单季拆分环比 → 同报告期同比 → 不可得。"""

    @staticmethod
    def _fn():
        from lib.render_markdown._v3 import _v3_recent_profit_direction
        return _v3_recent_profit_direction

    def test_mixed_cumulative_split_qoq(self):
        # 600519 封存 175 数值：2026 半年累计 445.17 亿 vs 一季度累计 272.43 亿
        # ——直接相减曾得「↑正向」；单季拆分二季度 172.74 亿为 ↓。
        d, label, basis = self._fn()([
            {"end_date": "20260331", "net_profit": 272.4251288645e8},
            {"end_date": "20260630", "net_profit": 445.1688042186e8},
        ])
        assert d == "↓负向"
        assert label == "净利润环比"
        assert "2026Q2 vs 2026Q1" in basis

    def test_two_q1_values_are_yoy_not_qoq(self):
        # MC-01（2026-10-07 独立探针）：两个 Q1 相隔一年，不是环比——须退回
        # 同报告期同比（缺口年份的「单季」列不出相邻季度）。
        d, label, basis = self._fn()([
            {"end_date": "20250331", "net_profit": 100},
            {"end_date": "20260331", "net_profit": 120},
        ])
        assert label == "净利润同比"
        assert "环比" not in label and "环比" not in basis
        assert d == "↑正向" and "同报告期" in basis

    def test_skipped_year_stops(self):
        # 年份缺口（2024Q1 → 2026Q1）：无相邻上一季度、也无同报告期基期 → 停笔。
        d, label, basis = self._fn()([
            {"end_date": "20240331", "net_profit": 100},
            {"end_date": "20260331", "net_profit": 120},
        ])
        assert (d, label, basis) == ("", "", "")

    def test_cross_year_q4_to_q1_legal_qoq(self):
        # 合法跨年环比：2026Q1 vs 2025Q4（Q4 单季 = 2025 年报 − 9M）。
        d, label, basis = self._fn()([
            {"end_date": "20250930", "net_profit": 900},
            {"end_date": "20251231", "net_profit": 1200},
            {"end_date": "20260331", "net_profit": 400},
        ])
        assert label == "净利润环比"
        assert d == "↑正向"
        assert "2026Q1 vs 2025Q4" in basis

    def test_year_gap_no_qoq_and_no_yoy(self):
        # 跨年但上年 Q4 序列缺中间期（无 0930）→ 环比不可得；又无 2025Q1 基期
        # → 同比亦不可得 → 停笔（不得拿 2024 年的值硬比）。
        d, label, basis = self._fn()([
            {"end_date": "20240930", "net_profit": 800},
            {"end_date": "20241231", "net_profit": 1000},
            {"end_date": "20260331", "net_profit": 400},
        ])
        assert (d, label, basis) == ("", "", "")

    def test_same_period_yoy_fallback(self):
        d, label, basis = self._fn()([
            {"end_date": "20250630", "net_profit": 454.029622981e8},
            {"end_date": "20260630", "net_profit": 445.1688042186e8},
        ])
        assert d == "↓负向"
        assert label == "净利润同比"
        assert "同报告期" in basis

    def test_missing_intermediate_not_comparable(self):
        # 缺中间期（1231 的 Q4 单季拆分需 0930 行）且无上年同报告期 → 不可得。
        d, label, basis = self._fn()([
            {"end_date": "20251231", "net_profit": 8.2e10},
            {"end_date": "20260331", "net_profit": 2.7e10},
        ])
        assert (d, label, basis) == ("", "", "")

    def test_duplicate_rows_deduped(self):
        # 600036 封存 172 的重复行（同 end_date）：不去重时 fin[-1] 与 fin[-2]
        # 是同一报告期 → 曾得「→中性」（自比）。
        d, label, _ = self._fn()([
            {"end_date": "20250630", "net_profit": 7.493e10, "ann_date": "20250830"},
            {"end_date": "20250630", "net_profit": 7.493e10, "ann_date": "20250830"},
            {"end_date": "20260331", "net_profit": 3.7852e10},
            {"end_date": "20260630", "net_profit": 7.6445e10},
            {"end_date": "20260630", "net_profit": 7.6445e10},
        ])
        assert label == "净利润环比"
        assert d in ("↑正向", "↓负向")

    def test_iso_dates_equivalent_to_compact(self):
        # 2026-10-07 复检修复：日期先经 _norm_ed 归一（再做季度拆分/查找），
        # ISO 与全 compact 输入必须给出完全相同的 (方向, 标签, basis)。
        iso = self._fn()([
            {"end_date": "2026-03-31", "net_profit": 272.4251288645e8},
            {"end_date": "2026-06-30", "net_profit": 445.1688042186e8},
        ])
        compact = self._fn()([
            {"end_date": "20260331", "net_profit": 272.4251288645e8},
            {"end_date": "20260630", "net_profit": 445.1688042186e8},
        ])
        assert iso == compact
        d, label, basis = iso
        assert d == "↓负向" and label == "净利润环比"
        assert "2026Q2 vs 2026Q1 单季" in basis

    def test_mixed_formats_latest_period_not_misordered(self):
        # compact 早于 ISO 的实际后期（20260331 vs 2026-06-30）：按裸字符串
        # 排序会把「-」排在数字前、把 ISO 行顶到前面，令最新期错落到 Q1。
        # 修复后须仍识别 Q2 为最新期并做单季环比；输入顺序不影响结果。
        mixed = self._fn()([
            {"end_date": "20260331", "net_profit": 272.4251288645e8},
            {"end_date": "2026-06-30", "net_profit": 445.1688042186e8},
        ])
        assert (mixed[1], mixed[0]) == ("净利润环比", "↓负向")
        assert "2026Q2 vs 2026Q1 单季" in mixed[2]
        reversed_input = self._fn()([
            {"end_date": "2026-06-30", "net_profit": 445.1688042186e8},
            {"end_date": "20260331", "net_profit": 272.4251288645e8},
        ])
        assert reversed_input == mixed

    def test_mixed_format_revision_dedup_uses_latest_ann_date(self):
        # 同报告期 ISO/compact 两条记录（修订）：去重须按 ann_date 取修订值，
        # 且归一后不得影响拆分。初版 100 → 单季 345.17 亿 > Q1 100 →「↑」；
        # 修订值 272.43 → 单季 172.74 亿 < Q1 272.43 →「↓」。
        d, label, basis = self._fn()([
            {"end_date": "20250630", "net_profit": 454.029622981e8},
            {"end_date": "20260331", "net_profit": 100.0, "ann_date": "20260401"},
            {"end_date": "2026-03-31", "net_profit": 272.4251288645e8,
             "ann_date": "20260428"},
            {"end_date": "20260630", "net_profit": 445.1688042186e8},
        ])
        assert (label, d) == ("净利润环比", "↓负向")
        assert "2026Q2 vs 2026Q1 单季" in basis

    def test_cross_year_q4_q1_mixed_formats_legal_qoq(self):
        # 跨年 Q4→Q1 且上年序列为 ISO：归一后 2026Q1 vs 2025Q4 环比仍成立
        # （compact 混合不得退化为停笔）。
        d, label, basis = self._fn()([
            {"end_date": "2025-09-30", "net_profit": 900.0},
            {"end_date": "2025-12-31", "net_profit": 1200.0},
            {"end_date": "20260331", "net_profit": 400.0},
        ])
        assert label == "净利润环比" and d == "↑正向"
        assert "2026Q1 vs 2025Q4" in basis

    def test_mixed_formats_missing_intermediate_still_stops(self):
        # 缺中间期纪律不因格式归一放松：ISO 年报（缺 0930 无法拆 Q4）+
        # compact Q1，且无上年同报告期基期 → 停笔。
        assert self._fn()([
            {"end_date": "2025-12-31", "net_profit": 8.2e10},
            {"end_date": "20260331", "net_profit": 2.7e10},
        ]) == ("", "", "")

    def test_mixed_format_yoy_basis_normalized(self):
        # 同比回退分支的 basis 同样须归一：ISO 基期行 + compact 最新期
        # →「20260630 vs 20250630 同报告期」，不得留 ISO 原样。
        d, label, basis = self._fn()([
            {"end_date": "2025-06-30", "net_profit": 454.029622981e8},
            {"end_date": "20260630", "net_profit": 445.1688042186e8},
        ])
        assert d == "↓负向" and label == "净利润同比"
        assert basis == "20260630 vs 20250630 同报告期"

    def test_dynamic_drivers_iso_dates_same_readout(self):
        # _dynamic_drivers 消费者覆盖：全 ISO 财报期与 compact 版读出同一条
        # 因子行（旧实现退化为「净利润同比」）。
        from fixtures.collections import make_kline_rows
        from lib.render_markdown._v3 import _section_dynamic_drivers

        dims = {
            "financials": {"data": [
                {"end_date": "2025-06-30", "net_profit": 454.029622981e8},
                {"end_date": "2026-03-31", "net_profit": 272.4251288645e8},
                {"end_date": "2026-06-30", "net_profit": 445.1688042186e8},
            ]},
            "kline": {"data": make_kline_rows(60, descending=True)},
        }
        text = _section_dynamic_drivers(
            {"fetched_at": "2026-10-07T00:00:00+00:00"}, "600519", dims, {})
        assert "| 基本面 | 净利润环比 | ↓负向 |" in text
        assert "2026Q2 vs 2026Q1 单季" in text

    def test_driver_matrix_and_cv6_use_same_period(self):
        from fixtures.collections import make_kline_rows
        from lib.render_markdown._v3 import _section_dynamic_drivers

        dims = {
            "financials": {"data": [
                {"end_date": "20250630", "net_profit": 454.029622981e8},
                {"end_date": "20260331", "net_profit": 272.4251288645e8},
                {"end_date": "20260630", "net_profit": 445.1688042186e8},
            ]},
            # 降序 K 线 → 空头排列（ma_dir=↓负向），与单季环比同向 → CV-6 走
            # convergence 分支，可核对财报窗口披露串。
            "kline": {"data": make_kline_rows(60, descending=True)},
        }
        text = _section_dynamic_drivers(
            {"fetched_at": "2026-10-07T00:00:00+00:00"}, "600519", dims, {})
        assert "| 基本面 | 净利润环比 | ↓负向 |" in text
        assert "2026Q2 vs 2026Q1 单季" in text  # 财报窗口披露
        # 技术趋势 ↑ 与单季环比 ↓ 不同向 → divergence（CV-6 文本带期间标注）。
        assert "与净利润环比方向 ↓负向 不一致" in text
        assert "仅作方向对照" in text
        # 旧混期比较不再产出正向读数
        assert "净利润环比 | ↑正向" not in text


class TestRev02ErpSeriesLabel:
    """REV-02：ERP 行标签须绑定实际参与运算的 10Y 序列（含币种）。"""

    @staticmethod
    def _erp_line(erp: dict) -> str:
        from fixtures.collections import collection_v2_minimal
        from lib.render import render_report_v3

        c = collection_v2_minimal()
        c["market_structure"] = {"erp": erp}
        text = render_report_v3(c, "600176")
        return next(ln for ln in text.splitlines()
                    if ln.startswith("- ERP（沪深300）"))

    def test_usd_series_with_cn_current_value_labeled_usd(self):
        # 封存 175 形态：ERP 2.27% 由 FRED.DGS10（USD）算出，另有独立 cn10y 现值。
        line = self._erp_line({
            "raw": 2.27, "percentile_5y": 1.4, "dgs10": 5.24,
            "rf_currency": "USD", "y10_source": "FRED.DGS10",
            "cn10y": 1.682, "cn10y_source": "akshare.bond_zh_us_rate(CN10Y)",
            "cn10y_date": "2026-09-30", "erp_days": 1211, "partial": False,
            "source": "tushare.index_dailybasic+FRED.DGS10",
        })
        assert "10Y 国债来源: FRED.DGS10" in line
        assert "美元口径" in line
        assert "中国 10Y 国债" not in line
        assert "未参与本 ERP 计算" in line

    def test_legacy_source_string_infers_usd(self):
        line = self._erp_line({
            "raw": 2.32, "percentile_5y": 2.7, "dgs10": 5.24,
            "erp_days": 1211, "partial": False,
            "source": "tushare.index_dailybasic+FRED.DGS10",
        })
        assert "10Y 国债来源: FRED.DGS10" in line
        assert "美元口径" in line

    def test_cny_series_label_unchanged(self):
        line = self._erp_line({
            "raw": 4.2, "percentile_5y": 30.0, "erp_days": 60, "partial": False,
            "source": "tushare.index_dailybasic+akshare.bond_zh_us_rate",
        })
        assert "10Y 国债来源: akshare.bond_zh_us_rate" in line
        assert "美元口径" not in line

    def test_cny_same_source_series_not_claimed_excluded(self):
        # 2026-10-07 复检修复：CNY 且 cn10y 现值与 ERP 对齐序列同源（collector
        # CN 分支两者同一字符串）——现值是否参与取决于交易日对齐，不得无证据
        # 断言「未参与」。旧实现无条件写「未参与本 ERP 计算」。
        line = self._erp_line({
            "raw": 4.2, "percentile_5y": 30.0, "erp_days": 60, "partial": False,
            "rf_currency": "CNY", "y10_source": "akshare.bond_zh_us_rate(CN10Y)",
            "cn10y": 1.682, "cn10y_source": "akshare.bond_zh_us_rate(CN10Y)",
            "source": "tushare.index_dailybasic+akshare.bond_zh_us_rate(CN10Y)",
        })
        assert "同源人民币序列" in line
        assert "按交易日对齐" in line
        assert "未参与" not in line
        # 旧封存快照形态：source 串拆分出的序列名无「(CN10Y)」后缀，同源判定
        # 须归一掉参数后缀后仍成立。
        legacy = self._erp_line({
            "raw": 4.2, "percentile_5y": 30.0, "erp_days": 60, "partial": False,
            "cn10y": 1.682, "cn10y_source": "akshare.bond_zh_us_rate(CN10Y)",
            "source": "tushare.index_dailybasic+akshare.bond_zh_us_rate",
        })
        assert "同源人民币序列" in legacy
        assert "未参与" not in legacy

    def test_unconfirmed_currency_or_source_no_exclusion_claim(self):
        # 来源/关系未确认时只披露现值，不断言参与与否（尤其不得写「未参与」）：
        # ① 无任何序列来源；② 有来源但币种无法判断。
        for erp in (
            {"raw": 3.1, "percentile_5y": 10.0, "erp_days": 100, "partial": False,
             "cn10y": 2.5, "cn10y_source": "akshare.bond_zh_us_rate(CN10Y)"},
            {"raw": 3.1, "percentile_5y": 10.0, "erp_days": 100, "partial": False,
             "cn10y": 2.5, "cn10y_source": "mystery.rf.series",
             "source": "tushare.index_dailybasic+mystery.rf.series"},
        ):
            line = self._erp_line(erp)
            assert "2.5%" in line
            assert "关系未确认" in line
            assert "未参与" not in line


class TestRev04ImpliedGrowthGap:
    """REV-04：g_implied 与历史 CAGR 的差额不得裁决市场定价方向。"""

    @staticmethod
    def _ctx():
        from fixtures.collections import collection_v2_minimal
        from lib.render_markdown._v3 import _FundamentalsContext

        c = collection_v2_minimal()
        c["market_structure"] = {"erp": {
            "cn10y": 1.682, "cn10y_source": "akshare.bond_zh_us_rate(CN10Y)",
        }}
        dims = {d["dimension"]: d for d in c["dimensions"]}
        return _FundamentalsContext(dims, c)

    @classmethod
    def _d3_text(cls, cagr: float) -> str:
        from lib.render_markdown._v3 import _section_4d_valuation_expectation

        ctx = cls._ctx()
        ctx.cagr, ctx.cagr_years_span = cagr, 3.0
        ctx.np_cagr, ctx.np_cagr_years_span = cagr, 3.0
        return "\n".join(_section_4d_valuation_expectation(ctx, []))

    def test_large_gap_has_no_direction_verdict(self):
        # CAGR 远高于 g_implied（原「市场定价偏悲观，可能存在低估」分支）。
        text = self._d3_text(10.81)
        assert "市场定价偏悲观" not in text
        assert "可能存在低估" not in text
        assert "市场定价偏乐观" not in text
        # REV-04 全文补充（2026-10-07 主线收尾）：不输出差值/相对差。
        assert "相差" not in text
        assert "条件模型读数" in text and "不可直接相减" in text

    def test_close_gap_has_no_pricing_verdict(self):
        from lib.valuation import implied_growth

        ctx = self._ctx()
        ig = implied_growth(ctx.current_pe, 0.01682, erp=0.06)
        g_pct = ig["g_implied"] * 100
        text = self._d3_text(g_pct * 1.05)  # 原「接近」分支
        assert "定价基本反映历史增长" not in text
        assert "不可直接相减" in text
        assert "相差" not in text

    def test_5d_gap_line_has_no_direction_verdict(self):
        from lib.render_risk import _section_bull_bear

        dims = {
            "valuation": {"data": [
                {"trade_date": "2024-01-01", "pe_ttm": 12.0, "total_mv": 300.0},
                {"trade_date": "2024-06-01", "pe_ttm": 15.0, "total_mv": 320.0},
                {"trade_date": "2025-01-01", "pe_ttm": 20.0, "total_mv": 396.0},
            ]},
            "financials": {"data": [
                {"end_date": "20231231", "revenue": 1.0e9, "net_profit": 1.0e8},
                {"end_date": "20241231", "revenue": 1.4e9, "net_profit": 1.4e8},
            ]},
        }
        ms = {"erp": {"cn10y": 1.682, "cn10y_source": "akshare.bond_zh_us_rate(CN10Y)"}}
        text = _section_bull_bear({}, "600176", dims, ms, {"signals": []})
        section_5d = text.split("### 5d. 预期差", 1)[1]
        assert "定价偏悲观" not in section_5d
        assert "定价偏乐观" not in section_5d
        # 分别列读数 + 不可直接比较；不输出差值/相对差
        assert "历史增速读数（有限历史区间）" in section_5d
        assert "不可直接比较" in section_5d
        gap_line = next(ln for ln in section_5d.splitlines() if "历史增速读数" in ln)
        assert "相差" not in gap_line and "相对" not in gap_line

    def test_5c_no_difference_or_side_readings(self):
        """REV-04 全文补充：5c [隐含增长 vs 实际增长] 不得输出差值或
        Bull/Bear 方向读法——改列两个读数与不可直接比较的原因。"""
        from lib.render_risk import _section_bull_bear

        dims = {
            "valuation": {"data": [
                {"trade_date": "2024-01-01", "pe_ttm": 12.0, "total_mv": 300.0},
                {"trade_date": "2024-06-01", "pe_ttm": 15.0, "total_mv": 320.0},
                {"trade_date": "2025-01-01", "pe_ttm": 20.0, "total_mv": 396.0},
            ]},
            "financials": {"data": [
                {"end_date": "20231231", "revenue": 1.0e9, "net_profit": 1.0e8},
                {"end_date": "20241231", "revenue": 1.4e9, "net_profit": 1.4e8},
            ]},
        }
        ms = {"erp": {"cn10y": 1.682, "cn10y_source": "akshare.bond_zh_us_rate(CN10Y)"}}
        text = _section_bull_bear({}, "600176", dims, ms, {"signals": []})
        section_5c = text.split("### 5c. 关键分歧点", 1)[1].split("### 5d", 1)[0]
        line = next(ln for ln in section_5c.splitlines() if "隐含增长读数" in ln)
        assert "不可直接比较" in line
        assert "相差" not in line and "Bull 读作" not in line and "Bear 读作" not in line


class TestRev07CapexProxy:
    """REV-07：ΔRevenue/CAPEX 同报告期窗口；命名不得称研发/投资回报。"""

    @staticmethod
    def _score(rows):
        from lib.scoring import _score_capex_efficiency
        return _score_capex_efficiency(rows)

    def test_mixed_period_high_ratio_not_reproduced(self):
        # 600519 原反例：半年营收−一季度营收 367.94 亿 / 半年 CAPEX 8.32 亿
        # = 44.22（>2x 满分）。同报告期窗口应为 (907.03−893.89)/8.32 ≈ 1.58。
        rows = [
            {"end_date": "20250630", "revenue": 893.8935441684e8, "cap_ex": 1.59599580923e9},
            {"end_date": "20260331", "revenue": 539.0925222051e8, "cap_ex": 6.0479158389e8},
            {"end_date": "20260630", "revenue": 907.0326096448e8, "cap_ex": 8.3214275228e8},
        ]
        pts, detail, sources, missing = self._score(rows)
        assert missing == "" and sources == ["revenue", "cap_ex"]
        assert abs(detail["ratio"] - 1.579) < 0.01
        assert pts == 15.0
        assert "非投资回报率或研发回报" in detail["note"]
        assert "20260630 vs 20250630" in detail["note"]

    def test_no_same_period_base_is_insufficient(self):
        pts, detail, _, missing = self._score([
            {"end_date": "20260331", "revenue": 5.4e9, "cap_ex": 0.6e8},
            {"end_date": "20260630", "revenue": 9.1e9, "cap_ex": 0.8e8},
        ])
        assert pts is None
        assert "数据不足" in detail["note"]
        assert "同报告期" in missing

    def test_render_label_not_research_return(self):
        from fixtures.collections import collection_v2_minimal
        from lib.render import render_report_v3

        c = collection_v2_minimal()
        for dim in c["dimensions"]:
            if dim["dimension"] == "financials":
                dim["data"] = [
                    {"end_date": "20250630", "roe": 19.2, "revenue": 8.9e9,
                     "net_profit": 4.5e8, "cap_ex": 1.6e8, "ocf": 1.3e8,
                     "grossprofit_margin": 91.3, "netprofit_margin": 52.6},
                    {"end_date": "20260630", "roe": 18.0, "revenue": 9.1e9,
                     "net_profit": 4.4e8, "cap_ex": 1.7e8, "ocf": 1.4e8,
                     "grossprofit_margin": 89.6, "netprofit_margin": 50.8},
                ]
        text = render_report_v3(c, "600176")
        assert "研发回报（代理" not in text
        assert "营收增量/CAPEX（同报告期代理读数）" in text


class TestR15LowPeChainConditional:
    """R15：低 PE 陷阱路径为「候选路径（非穷尽）+ 条件」，不得「只能是」。"""

    def test_low_pe_bull_chain_is_candidates_not_exhaustive(self):
        from lib.render_risk import _section_bull_bear

        rows = [{"trade_date": f"2024-{m:02d}-01", "pe_ttm": 60 - 4 * m,
                 "total_mv": 500.0} for m in range(1, 13)]
        dims = {
            "valuation": {"data": rows},
            "financials": {"data": [
                {"end_date": "20241231", "revenue": 1.0e9, "net_profit": 1.0e8},
                {"end_date": "20251231", "revenue": 1.2e9, "net_profit": 1.2e8},
            ]},
        }
        text = _section_bull_bear({}, "600176", dims, {}, {"signals": []})
        assert "低 PE 陷阱" in text
        assert "只能是" not in text
        assert "（非穷尽）" in text
