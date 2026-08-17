"""v0.2.6 /code-review max 二轮修复的回归测试（2026-08-17，15 项审查发现）。

覆盖缺陷（审查逐项 live repro 确认）：
  R-1  F2-4 import 崩溃：scripts/lib 无 dates.py，report --outdir 主流程
       ModuleNotFoundError（正确入口是 lib.shared_dates 引导 re-export）
  R-2  F2-1 income_driver CAGR 负数基底复数 TypeError（窗口终点亏损即崩）
  R-3  F2-1 起点亏损窗口衰减反转（亏损恢复标的拿全权重 vs 稳健增长被衰减）
  R-4  F0-8 CAGR 行过滤只认 8 位数字日期（akshare "2025-12-31" 全滤）
  R-5  F0-8 _compute_metric_cagr 终点亏损返回复数（渲染 "-70.76+50.65j%"）
  R-6  F0-2 同比基期 dash 日期匹配（_prior_year_row 只认 8 位数字）
  R-7  F0-8/F2-1 行业提取只查 "industry" 键（akshare basic_info 用「行业」）
  R-8  F0-7 financial_rigor PB 仍 val_data[-1] 取最旧行（与 PE 修复不对称）
  R-9  F0-1 risk_reward net_debt=None 用 0 替代（每股目标价被净债务抬高）
  R-10 latest_month_row 首行「月份」解析失败时静默取上一期（无告警）
  R-11 F1-1 腾讯金额单位（万元 vs 元）——锁定于 test_v026_p1_fixes.py
  R-12 F0-8 护城河 ROE 趋势锚点（_roe_trend_anchors：年报优先/同 MMDD
       兜底/不可解析守卫）
  R-13 F0-4 macro/_orchestrate dict 行上 iloc 的 AttributeError 崩溃级
       修复（收敛到 nums.row_value_or_last）
  R-14 F1-5 PCR 探针重试/复用——锁定于 test_collector_fixes.py
"""

from __future__ import annotations

import logging

import pytest


# ---------------------------------------------------------------------------
# R-1: F2-4 import 崩溃
# ---------------------------------------------------------------------------
class TestSharedDatesBootstrap:
    def test_shanghai_now_importable_via_shared_dates(self):
        """scripts/lib 无 dates.py——必须经 shared_dates 引导 re-export
        （invest.py report --outdir 曾写 from lib.dates import，必崩）。"""
        from lib.shared_dates import shanghai_now
        assert shanghai_now().tzinfo is not None

    def test_other_dates_helpers_reexported(self):
        from lib.shared_dates import latest_month_row, normalize_end_date
        assert normalize_end_date("2025-12-31") == "20251231"
        assert latest_month_row([{"月份": "2026年8月"}])["月份"] == "2026年8月"


# ---------------------------------------------------------------------------
# R-2/R-3: F2-1 income_driver CAGR 守卫
# ---------------------------------------------------------------------------
class TestIncomeDriverCagrGuards:
    def _rows(self, profits: list[float]) -> list[dict]:
        return [
            {"end_date": f"{2020 + i}1231", "net_profit": v}
            for i, v in enumerate(profits)
        ]

    def test_loss_at_window_end_does_not_crash(self):
        """窗口终点亏损：修复前负数底数小数次幂 → 复数 → min() TypeError。"""
        from lib.income_driver import classify_income_driver
        result = classify_income_driver(
            self._rows([100.0, 80.0, 50.0, -20.0])
        )
        assert isinstance(result, dict)
        assert result.get("driver") is not None

    def test_loss_at_window_start_does_not_crash(self):
        """窗口起点亏损：不崩且增速量级按最近年增速近似（删除该近似分支
        时 scale 回 1.0 → score 1.2，本断言 growth_score < 0.75 会失败——
        锁定机制）。"""
        from lib.income_driver import classify_income_driver
        result = classify_income_driver(
            self._rows([-50.0, 10.0, 10.5, 11.0])
        )
        assert isinstance(result, dict)
        assert result.get("driver") is not None
        growth_score = (result.get("scores") or {}).get("成长兑现", 1.0)
        assert growth_score < 0.75

    def test_loss_at_window_end_attenuated_not_full_weight(self):
        """终点亏损窗口（[100,120,140,-20]）：review 二轮实测修复前
        growth_scale=1.0 满权重、driver 误判「成长兑现」——终点亏损必须
        落入增速下限 0.15。"""
        from lib.income_driver import classify_income_driver
        result = classify_income_driver(
            self._rows([100.0, 120.0, 140.0, -20.0])
        )
        growth_score = (result.get("scores") or {}).get("成长兑现", 1.0)
        assert growth_score < 0.2

    def test_high_growth_recovery_capped(self):
        """高增速恢复年（-50→200→210→600，单年 185.7%）不得拿满权重：
        含亏损年窗口再封顶 0.5（review 二轮实测 185.7%/8 cap 到 1.0 满权重
        → score 1.2；封顶后 0.7）。"""
        from lib.income_driver import classify_income_driver
        result = classify_income_driver(
            self._rows([-50.0, 200.0, 210.0, 600.0])
        )
        growth_score = (result.get("scores") or {}).get("成长兑现", 1.0)
        assert growth_score < 0.75

    def test_recovering_cyclical_not_classified_growth_with_low_speed(self):
        """起点亏损但最近年增速个位数：不得因 CAGR 不可算而拿全权重
        被判「成长兑现」（F2-1 量级约束的对称性）。"""
        from lib.income_driver import classify_income_driver
        result = classify_income_driver(
            self._rows([-50.0, 100.0, 103.0, 106.0])  # 恢复后 ~3%/年
        )
        growth_score = (result.get("scores") or {}).get("成长兑现", 0.0)
        assert growth_score < 1.0

    def test_counter_evidence_no_fixer_note_leak(self):
        """反例字符串不得泄漏实现注记（曾输出「（应为近 5 年口径）」）。"""
        from lib.income_driver import classify_income_driver
        result = classify_income_driver(
            self._rows([100.0, 120.0, 140.0, 170.0]),
            refi_times=3,
        )
        for item in result.get("counter_evidence", []):
            assert "应为近" not in item


# ---------------------------------------------------------------------------
# R-4/R-5: F0-8 CAGR 日期格式 + 终点亏损
# ---------------------------------------------------------------------------
class TestCagrDashDates:
    def test_dash_end_dates_compute_cagr(self):
        """akshare 源 end_date 为 "2025-12-31"：修复前全部行被
        isdigit()/len==8 过滤 → CAGR 静默消失。"""
        from lib.render_utils import _compute_metric_cagr
        cagr, span = _compute_metric_cagr([
            {"end_date": "2022-12-31", "revenue": 1000.0},
            {"end_date": "2023-12-31", "revenue": 1200.0},
            {"end_date": "2024-12-31", "revenue": 1500.0},
            {"end_date": "2025-12-31", "revenue": 1800.0},
        ], "revenue")
        assert cagr == pytest.approx(21.64, abs=0.01)
        assert span == 3.0

    def test_mixed_formats_group_annual(self):
        """混合格式（dash + 8 位数字）按 normalize 后同报告期分组。"""
        from lib.render_utils import _compute_metric_cagr
        cagr, span = _compute_metric_cagr([
            {"end_date": "2022-12-31", "revenue": 1000.0},
            {"end_date": "20230630", "revenue": 600.0},
            {"end_date": "2023-12-31", "revenue": 1200.0},
            {"end_date": "2024-12-31", "revenue": 1500.0},
        ], "revenue")
        assert cagr is not None  # 年报组 2022→2024 成立
        assert span == 2.0

    def test_loss_at_end_returns_none_not_complex(self):
        """终点亏损：返回 None（修复前复数 CAGR 渲染成垃圾数字）。"""
        from lib.render_utils import _compute_metric_cagr
        cagr, span = _compute_metric_cagr([
            {"end_date": "20221231", "net_profit": 100.0},
            {"end_date": "20231231", "net_profit": 80.0},
            {"end_date": "20241231", "net_profit": 50.0},
            {"end_date": "20251231", "net_profit": -20.0},
        ], "net_profit")
        assert cagr is None

    def test_period_rows_dash_format(self):
        from lib.render_utils import cagr_period_rows
        rows = cagr_period_rows([
            {"end_date": "2022-12-31", "revenue": 1000.0},
            {"end_date": "2023-12-31", "revenue": 1200.0},
            {"end_date": "2024-12-31", "revenue": 1500.0},
        ], "revenue")
        assert len(rows) == 3


# ---------------------------------------------------------------------------
# R-6: F0-2 同比基期 dash 日期
# ---------------------------------------------------------------------------
class TestPriorYearRowDash:
    def test_dash_end_dates_match_prior_year(self):
        """akshare dash 格式下同比基期仍可匹配（修复前直接 return None）。"""
        from lib.render_markdown._v3 import _prior_year_row
        rows = [
            {"end_date": "2025-06-30", "revenue": 1788.86},
            {"end_date": "2026-03-31", "revenue": 1291.31},
            {"end_date": "2026-06-30", "revenue": 2769.17},
        ]
        prev = _prior_year_row(rows, rows[-1])
        assert prev is not None and prev["end_date"] == "2025-06-30"

    def test_cross_format_match(self):
        """基期 8 位数字、当期 dash：normalize 后仍同报告期。"""
        from lib.render_markdown._v3 import _prior_year_row
        rows = [
            {"end_date": "20250630", "revenue": 1788.86},
            {"end_date": "2026-06-30", "revenue": 2769.17},
        ]
        prev = _prior_year_row(rows, rows[-1])
        assert prev is not None and prev["end_date"] == "20250630"


# ---------------------------------------------------------------------------
# R-7: F0-8/F2-1 行业提取双键
# ---------------------------------------------------------------------------
class TestExtractIndustry:
    def test_tushare_industry_key(self):
        from lib.render_markdown._base import _extract_industry
        assert _extract_industry({"industry": "银行"}) == "银行"

    def test_akshare_chinese_key(self):
        """akshare stock_individual_info_em 用「行业」键——只查 industry
        会静默失配（金融豁免/成长减权被跳过）。"""
        from lib.render_markdown._base import _extract_industry
        assert _extract_industry({"行业": "证券"}) == "证券"

    def test_list_of_rows_both_keys(self):
        from lib.render_markdown._base import _extract_industry
        rows = [{"industry": "保险"}, {"行业": "银行"}]
        assert _extract_industry(rows) == "保险"
        rows2 = [{"other": 1}, {"行业": "银行"}]
        assert _extract_industry(rows2) == "银行"

    def test_empty_returns_empty_string(self):
        from lib.render_markdown._base import _extract_industry
        assert _extract_industry({}) == ""
        assert _extract_industry(None) == ""
        assert _extract_industry([]) == ""


# ---------------------------------------------------------------------------
# R-8: F0-7 financial_rigor PB 取行
# ---------------------------------------------------------------------------
class TestRigorPbLatestRow:
    def test_pb_takes_latest_row_when_newest_first(self):
        """valuation 行序最新在前时：PB 修复前 val_data[-1] 取最旧行，
        与最新净资产错配产生伪 PB 偏差。"""
        from lib.financial_rigor import verify_valuation
        collection = {
            "dimensions": [
                {
                    "dimension": "valuation",
                    "data": [
                        {"trade_date": "20260814", "pb": 3.5},
                        {"trade_date": "20260715", "pb": 2.1},
                    ],
                },
                {
                    "dimension": "financials",
                    "data": [
                        {"end_date": "20251231", "total_hldr_eqy_exc_min_int": 1000.0},
                    ],
                },
                {
                    "dimension": "quote",
                    "data": {"price": 10.0, "total_mv": 3500.0},
                },
            ],
        }
        reports = verify_valuation(collection)
        pb_reports = [r for r in reports if r.field == "pb"]
        # 最新行 PB 3.5 → 3500/1000 = 3.5，偏差 0 → 无 warn
        assert all(r.status != "warn" for r in pb_reports)

    def test_missing_trade_date_does_not_win_latest(self):
        """无日期行不得被选为「最新」：review 二轮三轮修复——上一版把
        无日期行排到末尾、[-1] 恒选中它（fail 9.9 vs 3.5），本版改为
        「有日期行取日期最大者」。强断言 status == "pass"（弱断言
        != "warn" 会放过 fail，曾被审查证实空转）。"""
        from lib.financial_rigor import verify_valuation
        collection = {
            "dimensions": [
                {
                    "dimension": "valuation",
                    "data": [
                        {"pb": 9.9},  # 无日期行
                        {"trade_date": "20260814", "pb": 3.5},
                    ],
                },
                {
                    "dimension": "financials",
                    "data": [
                        {"end_date": "20251231", "total_hldr_eqy_exc_min_int": 1000.0},
                    ],
                },
                {
                    "dimension": "quote",
                    "data": {"price": 10.0, "total_mv": 3500.0},
                },
            ],
        }
        reports = verify_valuation(collection)
        pb_reports = [r for r in reports if r.field == "pb"]
        assert pb_reports, "PB 验算应产生报告"
        assert all(r.status == "pass" for r in pb_reports)

    def test_dash_trade_date_still_picks_latest(self):
        """dash 格式 trade_date 与 8 位数字混排时按 normalize 后比较
        （'-'(0x2D) < '0' 的词法序会把 dash 日期误排最前）。"""
        from lib.financial_rigor import verify_valuation
        collection = {
            "dimensions": [
                {
                    "dimension": "valuation",
                    "data": [
                        {"trade_date": "20260814", "pb": 3.5},
                        {"trade_date": "2026-07-15", "pb": 2.1},
                    ],
                },
                {
                    "dimension": "financials",
                    "data": [
                        {"end_date": "20251231", "total_hldr_eqy_exc_min_int": 1000.0},
                    ],
                },
                {
                    "dimension": "quote",
                    "data": {"price": 10.0, "total_mv": 3500.0},
                },
            ],
        }
        reports = verify_valuation(collection)
        pb_reports = [r for r in reports if r.field == "pb"]
        assert pb_reports and all(r.status == "pass" for r in pb_reports)


# ---------------------------------------------------------------------------
# R-9: F0-1 risk_reward net_debt 抑制
# ---------------------------------------------------------------------------
class TestRiskRewardNetDebt:
    def test_net_debt_none_suppresses_per_share(self):
        """net_debt=None 时不再用 0 替代（目标价被整个净债务抬高），
        与 render_dcf 同口径显式失败。"""
        from lib.risk_reward import compute_dcf_risk_reward
        collection = {
            "dimensions": [
                {
                    "dimension": "kline",
                    "data": [
                        {"trade_date": "20260814", "close": 10.0},
                        {"trade_date": "20260815", "close": 10.2},
                    ],
                },
                {"dimension": "basic_info", "data": {"总股本": "24.6亿股"}},
                {"dimension": "financials", "data": []},
            ],
        }
        result = compute_dcf_risk_reward(collection)
        assert "error" in result
        assert "每股换算已抑制" in result["error"]

    def test_net_debt_suppression_reason_surfaced(self):
        from lib.risk_reward import compute_dcf_risk_reward
        collection = {
            "dimensions": [
                {
                    "dimension": "kline",
                    "data": [{"trade_date": "20260815", "close": 10.2}],
                },
                {"dimension": "basic_info", "data": {"总股本": "24.6亿股"}},
                {"dimension": "financials", "data": []},
            ],
        }
        result = compute_dcf_risk_reward(collection)
        assert "有息负债字段未采集" in result["error"]


# ---------------------------------------------------------------------------
# R-10: latest_month_row 静默回退
# ---------------------------------------------------------------------------
class TestLatestMonthRowWarnings:
    def test_first_row_unparseable_picks_second_with_warning(self, caplog):
        """首行（最新，akshare 约定最新在前）月份解析失败：选中次行并
        显式告警——宏观标签不得静默使用上一期数字。"""
        from lib.shared_dates import latest_month_row
        rows = [
            {"月份": "202608", "值": 49.2},      # 新格式，解析失败
            {"月份": "2026年7月", "值": 50.1},
        ]
        with caplog.at_level(logging.WARNING):
            row = latest_month_row(rows)
        assert row["月份"] == "2026年7月"
        assert any("首行" in m and "不可解析" in m for m in caplog.messages)

    def test_all_unparseable_falls_back_first_with_warning(self, caplog):
        from lib.shared_dates import latest_month_row
        rows = [{"月份": "202608", "值": 49.2}, {"月份": "202607", "值": 50.1}]
        with caplog.at_level(logging.WARNING):
            row = latest_month_row(rows)
        assert row["月份"] == "202608"
        assert any("全部" in m and "解析失败" in m for m in caplog.messages)

    def test_parseable_first_row_no_warning(self, caplog):
        from lib.shared_dates import latest_month_row
        rows = [
            {"月份": "2026年8月", "值": 49.2},
            {"月份": "2026年7月", "值": 50.1},
        ]
        with caplog.at_level(logging.WARNING):
            row = latest_month_row(rows)
        assert row["月份"] == "2026年8月"
        assert caplog.messages == []


# ---------------------------------------------------------------------------
# R-12: F0-8 护城河 ROE 趋势锚点
# ---------------------------------------------------------------------------
class TestRoeTrendAnchors:
    def test_two_annual_rows_use_annual_anchors(self):
        """年报 ≥2 期 → 首尾年报 ROE 做锚点，季度累计行不参与混比。"""
        from lib.render_markdown._v3 import _roe_trend_anchors
        rows = [
            {"end_date": "20231231", "roe": 10.0},
            {"end_date": "20240331", "roe": 2.5},
            {"end_date": "20241231", "roe": 12.0},
            {"end_date": "20250331", "roe": 3.0},
        ]
        first, ann_last, n = _roe_trend_anchors(rows, rows[-1])
        assert n == 2 and first == 10.0 and ann_last == 12.0

    def test_single_annual_same_period_fallback(self):
        """新上市（1 年报 + 多季度）：起点取与最新行同 MMDD 的最老行，
        避免「年报 ROE 12.0% vs 季累计 ROE 3.1%」跨期伪侵蚀。"""
        from lib.render_markdown._v3 import _roe_trend_anchors
        rows = [
            {"end_date": "20250630", "roe": 1.8},
            {"end_date": "20251231", "roe": 12.0},
            {"end_date": "20260331", "roe": 2.9},
            {"end_date": "20260630", "roe": 3.1},
        ]
        first, ann_last, n = _roe_trend_anchors(rows, rows[-1])
        assert n == 1 and first == 1.8 and ann_last is None

    def test_unparseable_latest_no_anchor(self):
        """最新行 end_date 不可解析（normalize → ""）：不得把所有不可解析行
        归入同一 same_period 混比（review 二轮：""[4:]=="" 全行匹配缺陷）。"""
        from lib.render_markdown._v3 import _roe_trend_anchors
        rows = [
            {"end_date": "bad-format", "roe": 9.9},
            {"end_date": "20260630", "roe": 3.1},
        ]
        first, ann_last, n = _roe_trend_anchors(rows, rows[0])
        assert first is None and ann_last is None

    def test_dash_dates_annual_match(self):
        """akshare dash 格式年报行同样识别（normalize 后 endswith 1231）。"""
        from lib.render_markdown._v3 import _roe_trend_anchors
        rows = [
            {"end_date": "2023-12-31", "roe": 10.0},
            {"end_date": "2024-12-31", "roe": 12.0},
        ]
        first, ann_last, n = _roe_trend_anchors(rows, rows[-1])
        assert n == 2 and first == 10.0 and ann_last == 12.0


# ---------------------------------------------------------------------------
# R-13: F0-4 macro dict 行末列兜底（iloc AttributeError 崩溃级修复）
# ---------------------------------------------------------------------------
class TestRowValueOrLast:
    def test_named_column_hit(self):
        from lib.nums import row_value_or_last
        assert row_value_or_last(
            {"制造业-指数": 49.2, "x": 1}, "制造业-指数", "制造业",
        ) == 49.2

    def test_fallback_last_value(self):
        """指标列缺失/改名时回退末列值（旧 Series.iloc[-1] 语义的 dict 等价，
        修复前 dict 上 iloc 直接 AttributeError）。"""
        from lib.nums import row_value_or_last
        assert row_value_or_last(
            {"月份": "2026年8月", "其他": 50.1}, "制造业-指数", "制造业",
        ) == 50.1

    def test_empty_row_none(self):
        from lib.nums import row_value_or_last
        assert row_value_or_last({}, "制造业-指数") is None
