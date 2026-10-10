"""Tests for lib.financials helpers and related bug fixes."""

from __future__ import annotations

import pytest


class TestFinancialsHelpers:
    def test_normalize_end_date_formats(self):
        from lib.financials import normalize_end_date

        assert normalize_end_date("20231231") == "20231231"
        assert normalize_end_date("2023-12-31") == "20231231"

    def test_parse_end_date_dot_format(self):
        from lib.financials import parse_end_date

        parsed = parse_end_date("2026.01.01")
        assert parsed is not None
        assert parsed.year == 2026
        assert parsed.month == 1
        assert parsed.day == 1

    def test_prior_year_end_date_hyphenated(self):
        from lib.financials import prior_year_end_date

        assert prior_year_end_date("2023-12-31") == "20221231"
        assert prior_year_end_date("20240331") == "20230331"

    def test_prior_year_end_date_invalid_returns_empty(self):
        from lib.financials import prior_year_end_date

        assert prior_year_end_date("abcdefgh") == ""

    def test_find_yoy_row_hyphenated_prior_record(self):
        from lib.financials import find_yoy_row

        rows = [
            {"end_date": "2023-12-31", "revenue": 100.0},
            {"end_date": "20241231", "revenue": 120.0},
        ]
        latest = {"end_date": "20241231", "revenue": 120.0}
        yoy = find_yoy_row(rows, latest)
        assert yoy is not None
        assert yoy["revenue"] == 100.0

    def test_find_yoy_row_hyphenated_latest_record(self):
        from lib.financials import find_yoy_row

        rows = [
            {"end_date": "20231231", "revenue": 100.0},
            {"end_date": "2024-12-31", "revenue": 120.0},
        ]
        latest = {"end_date": "2024-12-31", "revenue": 120.0}
        yoy = find_yoy_row(rows, latest)
        assert yoy is not None
        assert yoy["revenue"] == 100.0

    def test_yoy_from_fina_rows_hyphenated_dates(self):
        from lib.store import _yoy_from_fina_rows

        rows = [
            {"end_date": "2022-12-31", "revenue": 100.0},
            {"end_date": "2023-12-31", "revenue": 120.0},
        ]
        assert _yoy_from_fina_rows(rows, "revenue") == 20.0


class TestYoySharedBase:
    """A05-2：同比共享计算基础（financials.yoy_from_fina_rows）反例。

    离线对照（A基础-财务纯函数反例.py yoy 组）：
    - 无效值（NaN/±Inf/非数字）→ None（旧 store 路径曾透出 NaN/Inf）；
    - 同期行回扫策略保留为参数（store=True / collector=False）；
    - ISO 与 compact 报告期等价。
    """

    def test_invalid_current_values_return_none(self):
        from lib.financials import yoy_from_fina_rows

        for bad in (float("nan"), float("inf"), float("-inf"), "n/a"):
            rows = [{"end_date": "20250630", "revenue": 100.0},
                    {"end_date": "20260630", "revenue": bad}]
            assert yoy_from_fina_rows(rows, "revenue") is None, bad

    def test_invalid_prior_values_return_none(self):
        from lib.financials import yoy_from_fina_rows

        for bad in (float("nan"), "n/a"):
            rows = [{"end_date": "20250630", "revenue": bad},
                    {"end_date": "20260630", "revenue": 110.0}]
            assert yoy_from_fina_rows(rows, "revenue") is None, bad

    def test_rescan_policy_only_differs_on_invalid_prior(self):
        """同期重复行：store 回扫取可解析值；collector 首条命中即停。"""
        from lib.financials import yoy_from_fina_rows

        rows = [
            {"end_date": "20250630", "revenue": 90.0},          # 逆序遍历时先命中
            {"end_date": "20250630", "revenue": float("nan")},  # 坏值在后（存储序末）
            {"end_date": "20260630", "revenue": 110.0},
        ]
        # 逆序遍历（reversed(sorted_rows[:-1])）= [nan 行, 90 行]
        assert yoy_from_fina_rows(rows, "revenue", rescan_invalid_prior=True) == round(
            (110.0 - 90.0) / 90.0 * 100, 2)
        assert yoy_from_fina_rows(rows, "revenue", rescan_invalid_prior=False) is None

    def test_iso_and_compact_periods_equivalent(self):
        from lib.financials import yoy_from_fina_rows

        compact = [{"end_date": "20250630", "revenue": 100.0},
                   {"end_date": "20260630", "revenue": 130.0}]
        iso = [{"end_date": "2025-06-30", "revenue": 100.0},
               {"end_date": "2026-06-30", "revenue": 130.0}]
        assert yoy_from_fina_rows(compact, "revenue") == 30.0
        assert yoy_from_fina_rows(iso, "revenue") == 30.0

    def test_missing_prior_year_returns_none(self):
        from lib.financials import yoy_from_fina_rows

        rows = [{"end_date": "20230630", "revenue": 80.0},
                {"end_date": "20260630", "revenue": 110.0}]
        assert yoy_from_fina_rows(rows, "revenue") is None

    def test_store_and_collector_delegate_to_shared(self):
        """两处调用方 == 共享实现（各自 flag）；替换回退即红。"""
        from lib.collector import _revenue_yoy_from_fina_rows
        from lib.financials import yoy_from_fina_rows
        from lib.store import _yoy_from_fina_rows

        rows = [{"end_date": "20250630", "revenue": 100.0},
                {"end_date": "20260630", "revenue": 110.0}]
        assert _yoy_from_fina_rows(rows, "revenue") == yoy_from_fina_rows(
            rows, "revenue", rescan_invalid_prior=True)
        assert _revenue_yoy_from_fina_rows(rows) == yoy_from_fina_rows(
            rows, "revenue", rescan_invalid_prior=False)

    def test_flow_amount_yuan_preserves_zero(self):
        from lib.collector import _flow_amount_yuan

        assert _flow_amount_yuan({"net_mf_amount": 0.0}) == 0.0
        assert _flow_amount_yuan({"net_mf_vol": 0.0}) == 0.0

    def test_equity_multiplier_low_debt_ratio(self):
        from lib.collector import _q_tushare_financials

        import pandas as pd
        from unittest.mock import MagicMock, patch

        df = pd.DataFrame([{
            "ts_code": "600176.SH",
            "end_date": "20231231",
            "debt_to_assets": 0.8,
            "eqt_to_debt": None,
        }])
        mock_tc = MagicMock()
        mock_tc.query.side_effect = lambda api, **kw: (
            df if api == "fina_indicator" else pd.DataFrame()
        )

        # _q_tushare_financials 定义于 _sources，从 _sources globals 解析 _tushare_client
        with patch("lib.collector._sources._tushare_client", return_value=mock_tc), patch(
            "lib.env.is_tushare_available", return_value=True
        ), patch("lib.env.get_config", return_value={"TUSHARE_TOKEN": "x" * 32}):
            records = _q_tushare_financials("600176")

        assert records is not None
        em = records[0]["equity_multiplier"]
        assert abs(em - 1.008) < 0.001

    def test_interest_expense_zero_not_missing(self):
        from lib.risk_scanner import scan_financial_risks

        financials = [
            {"end_date": "20221231", "ebit": 1e8, "int_exp": 1e7},
            {"end_date": "20231231", "ebit": 1e8, "int_exp": 0.0},
        ]
        signals = scan_financial_risks(financials)
        int_sig = next(s for s in signals if s["id"] == "interest_coverage_weak")
        assert not int_sig["triggered"]
        assert "为零" in int_sig["detail"] or "不适用" in int_sig["detail"]


class TestFinancialSoftSignals:
    def test_revenue_acceleration_flag_uses_true_yoy_pairs(self):
        from lib.risk_scanner import revenue_acceleration_flag

        rows = [
            {"end_date": "2023-03-31", "revenue": 80.0},
            {"end_date": "2024-03-31", "revenue": 100.0},
            {"end_date": "2024-06-30", "revenue": 150.0},
            {"end_date": "2025-03-31", "revenue": 130.0},
            {"end_date": "2025-06-30", "revenue": 210.0},
        ]
        out = revenue_acceleration_flag(rows)
        assert out["triggered"] is True
        assert out["accel_pp"] == 10.0
        assert "2025-03-31=30.0%" in out["detail"]
        assert "2025-06-30=40.0%" in out["detail"]

    def test_revenue_acceleration_flag_requires_two_yoy_pairs(self):
        from lib.risk_scanner import revenue_acceleration_flag

        rows = [
            {"end_date": "2024-03-31", "revenue": 100.0},
            {"end_date": "2025-03-31", "revenue": 130.0},
            {"end_date": "2025-06-30", "revenue": 210.0},
        ]
        out = revenue_acceleration_flag(rows)
        assert out["triggered"] is False
        assert "缺少最近两组可比同比配对" in out["detail"]

    def test_revenue_acceleration_flag_triggers_on_negative_five_boundary(self):
        from lib.risk_scanner import revenue_acceleration_flag

        rows = [
            {"end_date": "2023-03-31", "revenue": 100.0},
            {"end_date": "2024-03-31", "revenue": 120.0},
            {"end_date": "2023-06-30", "revenue": 100.0},
            {"end_date": "2024-06-30", "revenue": 115.0},
        ]
        out = revenue_acceleration_flag(rows)
        assert out["triggered"] is True
        assert out["accel_pp"] == -5.0

    def test_ocf_np_divergence_flag(self):
        from lib.risk_scanner import ocf_np_divergence_flag

        rows = [{"end_date": "20231231", "n_cashflow_act": 1e6, "n_income_attr_p": 5e6}]
        out = ocf_np_divergence_flag(rows)
        assert out["triggered"] is True
        assert "ratio" in out

    def test_ocf_np_divergence_flag_uses_point_six_threshold(self):
        from lib.risk_scanner import ocf_np_divergence_flag

        rows = [{"end_date": "20231231", "n_cashflow_act": 0.55e6, "n_income_attr_p": 1e6}]
        out = ocf_np_divergence_flag(rows)
        assert out["triggered"] is True
        assert out["ratio"] == 0.55

    def test_ocf_np_divergence_flag_accepts_net_profit_field(self):
        from lib.risk_scanner import ocf_np_divergence_flag

        rows = [{"end_date": "20231231", "n_cashflow_act": 0.5e6, "net_profit": 1e6}]
        out = ocf_np_divergence_flag(rows)
        assert out["triggered"] is True
        assert out["ratio"] == 0.5
        assert "缺失" not in out["detail"]

    def test_ocf_np_divergence_flag_preserves_zero_net_profit(self):
        from lib.risk_scanner import ocf_np_divergence_flag

        rows = [{"end_date": "20231231", "n_cashflow_act": 1e6, "net_profit": 0.0}]
        out = ocf_np_divergence_flag(rows)
        assert out["triggered"] is False
        assert "净利润为 0" in out["detail"]
        assert "缺失" not in out["detail"]

    def test_ocf_np_divergence_flag_zero_primary_not_fallthrough(self):
        from lib.risk_scanner import ocf_np_divergence_flag

        # n_income_attr_p=0 must not fall through via `or` to net_profit
        rows = [{
            "end_date": "20231231",
            "n_cashflow_act": 1e6,
            "n_income_attr_p": 0.0,
            "net_profit": 1e6,
        }]
        out = ocf_np_divergence_flag(rows)
        assert out["triggered"] is False
        assert "净利润为 0" in out["detail"]
        assert "ratio" not in out

    def test_ocf_np_divergence_flag_negative_profit_positive_ocf(self):
        from lib.risk_scanner import ocf_np_divergence_flag

        rows = [{"end_date": "20231231", "n_cashflow_act": 2e6, "n_income_attr_p": -1e6}]
        out = ocf_np_divergence_flag(rows)
        assert out["triggered"] is True
        assert out["ratio"] == -2.0


class TestScanFinancialRisksCoalesce:
    def test_profit_quality_respects_zero_n_income_attr_p(self):
        from lib.risk_scanner import scan_financial_risks

        financials = [
            {
                "end_date": "20221231",
                "n_cashflow_act": 1e6,
                "n_income_attr_p": 0.0,
                "net_profit": 5e6,
            },
            {
                "end_date": "20231231",
                "n_cashflow_act": 2e6,
                "n_income_attr_p": 0.0,
                "net_profit": 6e6,
            },
        ]
        signals = scan_financial_risks(financials)
        qual = next(s for s in signals if s["id"] == "profit_quality_low")
        assert not qual["triggered"]
        assert "缺少 OCF/净利润字段" in qual["detail"]

    def test_deducted_profit_respects_zero_n_income_attr_p(self):
        from lib.risk_scanner import scan_financial_risks

        financials = [
            {"end_date": "20221231", "profit_dedt": 1e6, "n_income_attr_p": 0.0, "net_profit": 5e6},
            {"end_date": "20231231", "profit_dedt": 2e6, "n_income_attr_p": 0.0, "net_profit": 6e6},
        ]
        signals = scan_financial_risks(financials)
        ded = next(s for s in signals if s["id"] == "deducted_profit_divergence")
        assert not ded["triggered"]
        assert "扣非或净利润字段不足" in ded["detail"]


class TestLatestByEndDateAF01:
    """A-F01：财务最新期选取必须先归一日期（ISO/compact 混排错期反例）。

    负责人定点探针：20250331=100、2025-06-30=200、20260331=110、2026-06-30=260
    → 旧实现（原始字符串排序）输 10%，正确 30%。
    """

    MIXED = [
        {"end_date": "20250331", "revenue": 100.0, "roe": 5.0},
        {"end_date": "2025-06-30", "revenue": 200.0, "roe": 6.0},
        {"end_date": "20260331", "revenue": 110.0, "roe": 5.5},
        {"end_date": "2026-06-30", "revenue": 260.0, "roe": 7.0},
    ]

    def test_yoy_uses_normalized_latest_mixed_formats(self):
        """正例：混排最新期 = 2026-06-30 → 260/200-1 = 30%。"""
        from lib.financials import yoy_from_fina_rows

        assert yoy_from_fina_rows(self.MIXED, "revenue") == 30.0

    def test_yoy_compact_control_unchanged(self):
        """对照：全 compact 同数据仍 30%（无回归）。"""
        from lib.financials import yoy_from_fina_rows

        compact = [{"end_date": "20250331", "revenue": 100.0},
                   {"end_date": "20250630", "revenue": 200.0},
                   {"end_date": "20260331", "revenue": 110.0},
                   {"end_date": "20260630", "revenue": 260.0}]
        assert yoy_from_fina_rows(compact, "revenue") == 30.0

    def test_unparseable_date_never_becomes_latest(self):
        """反例：不可解析日期行不得压过有效日期充当最新期。"""
        from lib.financials import latest_by_end_date, yoy_from_fina_rows

        rows = [
            {"end_date": "2025-06-30", "revenue": 200.0},
            {"end_date": "2026-06-30", "revenue": 260.0},
            {"end_date": "Q2-2026", "revenue": 999.0},
        ]
        assert latest_by_end_date(rows)["revenue"] == 260.0
        assert yoy_from_fina_rows(rows, "revenue") == 30.0
        assert latest_by_end_date([{"end_date": "Q2-2026"}]) is None  # 全不可解析 → None

    def test_latest_by_end_date_tie_takes_last_input(self):
        """同报告期并列（修订/重复行）取输入序后者；修订取舍归 dedupe_by_end_date。"""
        from lib.financials import latest_by_end_date

        rows = [{"end_date": "2026-06-30", "revenue": 250.0},
                {"end_date": "20260630", "revenue": 260.0}]
        assert latest_by_end_date(rows)["revenue"] == 260.0

    def test_sort_by_end_date_ascending_puts_unparseable_first(self):
        """升序输出：归一升序；不可解析键 "" 恒最前（rows[-1] 必为有效最新期）。"""
        from lib.financials import sort_by_end_date

        rows = [{"end_date": "2026-06-30"}, {"end_date": "bad"},
                {"end_date": "20250331"}, {"end_date": "2025-06-30"}]
        assert [str(r["end_date"]) for r in sort_by_end_date(rows)] == [
            "bad", "20250331", "2025-06-30", "2026-06-30"]

    def test_rescan_policy_unchanged_after_normalization(self):
        """同期重复回扫政策不变（store=True 回扫 / collector=False 首条命中即停）。"""
        from lib.financials import yoy_from_fina_rows

        rows = [
            {"end_date": "20250630", "revenue": 90.0},
            {"end_date": "20250630", "revenue": float("nan")},
            {"end_date": "20260630", "revenue": 110.0},
        ]
        assert yoy_from_fina_rows(rows, "revenue", rescan_invalid_prior=True) == round(
            (110.0 - 90.0) / 90.0 * 100, 2)
        assert yoy_from_fina_rows(rows, "revenue", rescan_invalid_prior=False) is None

    def test_prior_year_scan_order_stable_by_input(self):
        """混排同期重复：逆序扫描按输入序（后者先试）——扫描顺序确定且可复现。"""
        from lib.financials import yoy_from_fina_rows

        rows = [
            {"end_date": "2025-06-30", "revenue": 200.0},  # 先输入
            {"end_date": "20250630", "revenue": float("nan")},  # 后输入=先试
            {"end_date": "2026-06-30", "revenue": 260.0},
        ]
        assert yoy_from_fina_rows(rows, "revenue", rescan_invalid_prior=False) is None
        assert yoy_from_fina_rows(rows, "revenue", rescan_invalid_prior=True) == 30.0


class TestExtractKeySnapshotFinancialLatestAF01:
    def test_financial_snapshot_uses_normalized_latest(self):
        from lib.store import extract_key_snapshot

        raw = {"raw_json": {
            "symbol": "600000",
            "dimensions": [{"dimension": "financials", "data": [
                {"end_date": "20250331", "revenue": 100.0, "net_profit": 10.0, "roe": 5.0},
                {"end_date": "2025-06-30", "revenue": 200.0, "net_profit": 20.0, "roe": 6.0},
                {"end_date": "20260331", "revenue": 110.0, "net_profit": 11.0, "roe": 5.5},
                {"end_date": "2026-06-30", "revenue": 260.0, "net_profit": 26.0, "roe": 7.0},
            ]}],
        }}
        snap = extract_key_snapshot(raw)
        assert snap["financials"]["roe"] == 7.0
        assert snap["financials"]["revenue_yoy"] == 30.0
        assert snap["financials"]["net_profit_yoy"] == 30.0




@pytest.mark.parametrize("bad_date", ["20991399", "2099-02-30", "20260229", "00000000"])
def test_latest_period_ignores_invalid_calendar_date(bad_date):
    from lib.financials import latest_by_end_date, sort_by_end_date, yoy_from_fina_rows
    rows = [{"end_date": "20250630", "revenue": 200},
            {"end_date": "20260630", "revenue": 260},
            {"end_date": bad_date, "revenue": 9999}]
    assert latest_by_end_date(rows)["end_date"] == "20260630"
    assert sort_by_end_date(rows)[-1]["end_date"] == "20260630"
    assert yoy_from_fina_rows(rows, "revenue") == 30.0
    assert latest_by_end_date([rows[-1]]) is None


def test_revision_selection_ignores_invalid_announcement_dates():
    from lib.financials import dedupe_by_end_date
    valid = {"end_date": "20260630", "ann_date": "20260830", "revenue": 260}
    invalid = {"end_date": "20260630", "ann_date": "20991399", "revenue": 9999}
    assert dedupe_by_end_date([valid, invalid]) == [valid]
    assert dedupe_by_end_date([invalid, valid]) == [valid]
    invalid_periods = [{"end_date": "20991399", "ann_date": "20260830"},
                       {"end_date": "20991399", "ann_date": "20260831"}]
    assert dedupe_by_end_date(invalid_periods) == invalid_periods
