"""v0.1.3-beta Phase 2 测试：基本面 12 题、估值 LAW 15、同行分位排名、CV-2。"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from fixtures.collections import (
    collection_v2_minimal,
    make_daily_basic_series,
    make_kline_rows,
)


def _collection_phase2() -> dict:
    """构建含完整 Phase 2 测试数据的 collection。"""
    c = collection_v2_minimal()
    c["market_structure"] = {
        "sw_index": {
            "index_code": "851121.SI",
            "industry": "电气设备",
            "return_20d_pct": 3.5,
            "stock_return_20d_pct": 4.7,
            "relative_vs_benchmark_pct": 2.1,
            "stock_vs_industry_pct": 1.2,
            "source": "tushare.sw_daily",
        },
        "erp": {
            "raw": 4.2,
            "percentile_5y": 72.0,
            "dgs10": 2.85,
            "erp_days": 200,
            "partial": False,
            "source": "tushare.index_dailybasic+FRED.DGS10",
        },
        "availability": {"sw_index": "available", "erp": "available"},
    }
    # 扩展财务数据（更多期以支持 CAGR 计算）
    for dim in c["dimensions"]:
        if dim["dimension"] == "financials":
            dim["data"] = [
                {"end_date": "20230331", "roe": 18.5, "eps": 2.1,
                 "profit_dedt": 1e8, "revenue": 4e9, "net_profit": 1.2e8,
                 "n_cashflow_act": 1.5e8, "ocf": 1.5e8},
                {"end_date": "20230630", "roe": 19.0, "eps": 2.2,
                 "profit_dedt": 1.05e8, "revenue": 4.2e9, "net_profit": 1.25e8,
                 "n_cashflow_act": 1.4e8, "ocf": 1.4e8},
                {"end_date": "20230930", "roe": 19.5, "eps": 2.3,
                 "profit_dedt": 1.1e8, "revenue": 4.5e9, "net_profit": 1.3e8,
                 "n_cashflow_act": 1.3e8, "ocf": 1.3e8},
                {"end_date": "20231231", "roe": 20.2, "eps": 2.4,
                 "profit_dedt": 1.15e8, "revenue": 5.0e9, "net_profit": 1.4e8,
                 "n_cashflow_act": 1.1e8, "ocf": 1.1e8},
                {"end_date": "20240331", "roe": 20.8, "eps": 2.5,
                 "profit_dedt": 1.2e8, "revenue": 5.2e9, "net_profit": 1.45e8,
                 "n_cashflow_act": 1.2e8, "ocf": 1.2e8},
                {"end_date": "20240630", "roe": 21.5, "eps": 2.6,
                 "profit_dedt": 1.3e8, "revenue": 5.5e9, "net_profit": 1.55e8,
                 "n_cashflow_act": 1.0e8, "ocf": 1.0e8},
            ]
        if dim["dimension"] == "valuation":
            dim["data"] = make_daily_basic_series(60)
        if dim["dimension"] == "kline":
            dim["data"] = make_kline_rows(60)
    # 行业同行数据
    c["industry_peers"] = {
        "peers": [
            {"symbol": "002001", "name": "同行A", "pe_ttm": 25.0, "pb": 3.5, "roe": 18.0, "revenue_yoy": 12.5},
            {"symbol": "002002", "name": "同行B", "pe_ttm": 35.0, "pb": 5.0, "roe": 15.0, "revenue_yoy": 8.0},
            {"symbol": "002003", "name": "同行C", "pe_ttm": 15.0, "pb": 2.0, "roe": 25.0, "revenue_yoy": 20.0},
        ],
        "target": {"symbol": "600176", "name": "测试股份", "pe_ttm": 22.0, "pb": 3.2, "roe": 20.2, "revenue_yoy": 10.0},
        "rankings": {
            "pe_ttm_pct": 33.3, "pe_ttm_rank": 2, "pe_ttm_total": 4,
            "pb_pct": 33.3, "pb_rank": 2, "pb_total": 4,
            "roe_pct": 66.7, "roe_rank": 3, "roe_total": 4,
            "revenue_yoy_pct": 33.3, "revenue_yoy_rank": 2, "revenue_yoy_total": 4,
        },
        "industry_name": "电气设备",
        "peer_source": "sw_index_member",
        "sufficient": True,
    }
    return c


class TestValuationV2:
    """Task 2.1：implied_growth + pe_band_series。"""

    def test_implied_growth_basic(self):
        from lib.valuation import implied_growth

        result = implied_growth(pe_ttm=20.0, risk_free_rate=0.03, erp=0.06)
        assert result["pe"] == 20.0
        assert result["risk_free_rate"] == 0.03
        assert result["erp"] == 0.06
        assert result["r"] == 0.09
        # g = 0.09 - 1/20 = 0.09 - 0.05 = 0.04
        assert abs(result["g_implied"] - 0.04) < 0.001
        assert "warning" not in result

    def test_implied_growth_high_pe_warning(self):
        from lib.valuation import implied_growth

        result = implied_growth(pe_ttm=80.0, risk_free_rate=0.03, erp=0.06)
        assert "warning" in result
        assert "PE > 50" in result["warning"]
        assert "参考价值有限" in result["warning"]

    def test_implied_growth_negative_pe(self):
        from lib.valuation import implied_growth

        result = implied_growth(pe_ttm=-5.0, risk_free_rate=0.03, erp=0.06)
        assert result.get("error") is not None
        assert result["g_implied"] is None

    def test_implied_growth_zero_pe(self):
        from lib.valuation import implied_growth

        result = implied_growth(pe_ttm=0.0, risk_free_rate=0.03, erp=0.06)
        assert result.get("error") is not None

    def test_implied_growth_output_has_all_fields(self):
        from lib.valuation import implied_growth

        result = implied_growth(pe_ttm=25.0, risk_free_rate=0.0285, erp=0.06)
        for key in ("pe", "risk_free_rate", "erp", "r", "g_implied"):
            assert key in result
        # g ≈ 0.0885 - 0.04 = 0.0485
        assert abs(result["g_implied"] - 0.0485) < 0.001

    def test_pe_band_series_basic(self):
        from lib.valuation import pe_band_series

        rows = make_daily_basic_series(60)
        result = pe_band_series(rows)
        assert result["n_samples"] > 0
        assert result["current_pe"] is not None
        assert result["mean"] is not None
        assert result["sigma"] is not None
        assert result["upper_1σ"] is not None
        assert result["lower_1σ"] is not None
        assert result["upper_2σ"] is not None
        assert result["lower_2σ"] is not None
        assert result["current_position"] != "数据不足"
        assert result["upper_1σ"] > result["mean"] > result["lower_1σ"]

    def test_pe_band_series_empty_input(self):
        from lib.valuation import pe_band_series

        result = pe_band_series([])
        assert result["n_samples"] == 0
        assert result["current_pe"] is None
        assert result["current_position"] == "数据不足"
        assert result["upper_1σ"] is None
        assert result["lower_2σ"] is None
        assert result["years"] == 5

    def test_pe_band_empty_json_serializable(self):
        import json
        from lib.valuation import pe_band_series

        band = pe_band_series([])
        json.dumps(band)


class TestRenderFundamentals:
    """Task 2.0：12 题框架 + LAW 10 分析提示。"""

    def test_twelve_core_questions_headings(self):
        from lib.render import render_report_v3

        text = render_report_v3(_collection_phase2(), "600176")
        # 12 道核心题标题
        expected_headings = [
            "A-① 行业景气度",
            "A-② 竞争位置",
            "A-③ 毛利率 vs 行业中位数",
            "B-① 护城河来源",
            "B-② 增长驱动力",
            "B-③ 现金流模式",
            "C-① 近 3 年营收 CAGR",
            "C-② 杜邦拆解 ROE",
            "C-③ 现金流覆盖",
            "C-④ 扣非/净利润",
            "D-① PE/PB 历史分位",
            "D-② PE vs 行业中位数",
            "D-③ 隐性预期差（LAW 15）",
        ]
        for heading in expected_headings:
            assert heading in text, f"Missing heading: {heading}"

    def test_data_insufficient_format(self):
        from lib.render import render_report_v3

        c = collection_v2_minimal()
        c["market_structure"] = {"availability": {}}
        text = render_report_v3(c, "600176")
        # LAW 14: 数据不足时写「数据不足：[缺少什么]」
        assert "数据不足：" in text
        assert "数据不足：[" in text

    def test_analysis_hint_law10_three_parts(self):
        from lib.render import render_report_v3

        text = render_report_v3(_collection_phase2(), "600176")
        # LAW 10 三块：为什么重要 / 常见分析误区 / 下一步交叉验证
        assert "为什么重要" in text
        assert "常见分析误区" in text
        assert "下一步交叉验证" in text

    def test_analysis_hint_not_generic(self):
        """LAW 10 禁止通用知识——分析提示必须引用本次报告具体数据点。"""
        from lib.render import render_report_v3

        text = render_report_v3(_collection_phase2(), "600176")
        hints = [line for line in text.splitlines() if "常见分析误区" in line]
        assert hints, "应有分析提示块"
        import re
        has_number = any(re.search(r"\d+\.?\d*", h) for h in hints)
        assert has_number, "LAW 10 violated: pitfalls lack report-specific numeric data"

    def test_competitive_position_uses_growth_percentile(self):
        from lib.render import render_report_v3

        c = _collection_phase2()
        # 目标增速最高 → 分位应判龙头
        c["industry_peers"]["target"]["revenue_yoy"] = 25.0
        c["industry_peers"]["rankings"]["revenue_yoy_pct"] = 100.0
        c["industry_peers"]["rankings"]["revenue_yoy_rank"] = 1
        c["industry_peers"]["rankings"]["revenue_yoy_total"] = 4
        text = render_report_v3(c, "600176")
        mod4 = text.split("## 4.")[1].split("## 5.")[0]
        assert "龙头" in mod4.split("A-②")[1].split("A-③")[0]

    def test_attach_phase2_extras_wiring(self):
        from lib import collector
        from lib.render import render

        c = collection_v2_minimal()
        with patch.object(collector, "attach_market_structure") as ms_mock, \
             patch.object(collector, "attach_industry_peers") as ip_mock, \
             patch.object(collector, "attach_pe_band") as pb_mock:
            ms_mock.side_effect = lambda coll, sym: coll.update({"market_structure": {}}) or {}
            ip_mock.side_effect = lambda coll, sym: coll.update({"industry_peers": {}}) or {}
            pb_mock.return_value = {"n_samples": 0}
            render(c, "600176", "md")
        ip_mock.assert_called_once()
        pb_mock.assert_called_once()

    def test_pe_band_series_respects_years_window(self):
        from lib.valuation import pe_band_series
        from datetime import datetime, timedelta

        old_date = (datetime.now() - timedelta(days=2000)).strftime("%Y%m%d")
        recent = make_daily_basic_series(30)
        rows = [{"trade_date": old_date, "pe_ttm": 99.0}, *recent]
        result = pe_band_series(rows, years=5)
        assert result["years"] == 5
        assert result["n_samples"] == len(recent)
        assert 99.0 not in result["pe_values"]

    def test_cv2_in_module4(self):
        from lib.render import render_report_v3

        c = _collection_phase2()
        # 给财务数据添加应收/存货字段以触发完整 CV-2
        for dim in c["dimensions"]:
            if dim["dimension"] == "financials":
                for i, r in enumerate(dim["data"]):
                    r["accounts_receiv"] = 1.0e8 + i * 0.2e8  # 应收逐期上升
                    r["inventory"] = 0.8e8 + i * 0.05e8
                break
        text = render_report_v3(c, "600176")
        mod4 = text.split("## 4.")[1].split("## 5.")[0]
        assert "CV-2" in mod4

    def test_industry_peers_table_present(self):
        from lib.render import render_report_v3

        text = render_report_v3(_collection_phase2(), "600176")
        mod4 = text.split("## 4.")[1].split("## 5.")[0]
        assert "同行可比公司" in mod4
        assert "同行A" in mod4
        assert "同行B" in mod4
        assert "同行C" in mod4

    def test_industry_peers_percentile_ranking(self):
        from lib.render import render_report_v3

        text = render_report_v3(_collection_phase2(), "600176")
        mod4 = text.split("## 4.")[1].split("## 5.")[0]
        assert "分位排名" in mod4
        assert "PE" in mod4.split("分位排名")[1].split("\n\n")[0]

    def test_industry_peers_insufficient_no_table(self):
        from lib.render import render_report_v3

        c = _collection_phase2()
        c["industry_peers"] = {"peers": [], "target": None, "rankings": {},
                               "industry_name": "电气设备", "sufficient": False}
        text = render_report_v3(c, "600176")
        mod4 = text.split("## 4.")[1].split("## 5.")[0]
        assert "同行可比公司" not in mod4

    def test_law15_implied_growth_output(self):
        from lib.render import render_report_v3

        text = render_report_v3(_collection_phase2(), "600176")
        mod4 = text.split("## 4.")[1].split("## 5.")[0]
        # LAW 15 固定格式字段
        assert "PE(TTM)" in mod4
        assert "10Y 国债收益率" in mod4
        assert "ERP 假设" in mod4 or "6%" in mod4
        assert "市场隐含增长率" in mod4
        assert "净利润 CAGR" in mod4
        assert "解读" in mod4

    def test_law15_high_pe_disclaimer(self):
        from lib.render import render_report_v3

        c = _collection_phase2()
        # 修改 valuation 数据让 PE > 50
        for dim in c["dimensions"]:
            if dim["dimension"] == "valuation":
                rows = make_daily_basic_series(60)
                for r in rows:
                    r["pe_ttm"] = 55.0 + r["pe_ttm"] * 0.1
                dim["data"] = rows
        text = render_report_v3(c, "600176")
        mod4 = text.split("## 4.")[1].split("## 5.")[0]
        # 免责声明
        assert "参考价值有限" in mod4 or "PE > 50" in mod4

    def test_dupont_analysis_present(self):
        from lib.render import render_report_v3

        c = _collection_phase2()
        # 给财务数据加杜邦字段
        for dim in c["dimensions"]:
            if dim["dimension"] == "financials":
                for r in dim["data"]:
                    r["netprofit_margin"] = 25.0
                    r["asset_turnover"] = 0.8
                    r["equity_multiplier"] = 1.5
                break
        text = render_report_v3(c, "600176")
        mod4 = text.split("## 4.")[1].split("## 5.")[0]
        assert "净利润率" in mod4
        assert "资产周转率" in mod4
        assert "权益乘数" in mod4

    def test_cagr_calculation_present(self):
        from lib.render import render_report_v3

        text = render_report_v3(_collection_phase2(), "600176")
        mod4 = text.split("## 4.")[1].split("## 5.")[0]
        assert "CAGR" in mod4

    def test_modules_5_7_have_real_content(self):
        """模块 5/7 已实现多空分歧与风险扫描（Phase 3）。"""
        from lib.render import render_report_v3

        text = render_report_v3(_collection_phase2(), "600176")
        mod5 = text.split("## 5.")[1].split("## 6.")[0]
        mod7 = text.split("## 7.")[1].split("## 8.")[0]
        assert "Phase 3" not in mod5
        assert "Phase 3" not in mod7
        assert "多头逻辑链" in mod5
        assert "空头逻辑链" in mod5
        assert "关键分歧点" in mod5
        assert "预期差" in mod5
        assert "报表风险（Financial Statement）" in mod7
        assert "Known Unknowns" in mod7

    def test_nine_modules_all_present(self):
        from lib.render import render_report_v3

        text = render_report_v3(_collection_phase2(), "600176")
        for i in range(9):
            assert f"## {i}." in text, f"Missing module {i}"


class TestCollectIndustryPeers:
    """Task 2.2：collect_industry_peers 单元测试。"""

    def test_collect_industry_peers_no_token(self):
        from lib import collector

        with patch.object(collector.env, "is_tushare_available", return_value=False):
            result = collector.collect_industry_peers("600176")
        assert "error" in result
        assert not result["sufficient"]
        assert result["peers"] == []

    def test_collect_industry_peers_has_structure(self):
        """未连网时的降级逻辑——直接传入 industry + mock 成员。"""
        from lib import collector

        mock_tc = MagicMock()
        call_count = {"stock_basic": 0}

        def _query(api, **kwargs):
            m = MagicMock()
            if api == "stock_basic" and kwargs.get("market") == "主板":
                # 回退：按行业筛选成分股
                m.empty = False
                m.iterrows = lambda: iter([
                    (0, {"ts_code": "002001.SZ", "name": "同行A", "industry": "电气设备"}),
                    (1, {"ts_code": "002002.SZ", "name": "同行B", "industry": "电气设备"}),
                    (2, {"ts_code": "002003.SZ", "name": "同行C", "industry": "电气设备"}),
                ])
                return m
            if api == "stock_basic":
                # 查目标公司行业（第一次调用）或后续
                call_count["stock_basic"] += 1
                m.empty = False
                m.iloc = MagicMock()
                mock_row = MagicMock()
                mock_row.get.return_value = "电气设备"
                m.iloc.__getitem__ = lambda _, idx: mock_row
                return m
            if api == "fina_indicator" or api == "daily_basic":
                m.empty = True
                return m
            if api == "index_classify":
                m.empty = False
                m.iterrows = lambda: iter([
                    (0, {"industry_name": "电气设备", "index_code": "851121.SI"}),
                ])
                return m
            if api == "index_member":
                m.empty = True  # 无 index_member 数据 → 触发回退
                return m
            m.empty = True
            return m

        mock_tc.query.side_effect = _query

        with patch.object(collector.env, "is_tushare_available", return_value=True), \
             patch.object(collector.env, "get_config", return_value={"TUSHARE_TOKEN": "x" * 32}), \
             patch.object(collector, "_tushare_client", return_value=mock_tc), \
             patch.object(collector, "_ms_lookup_sw_index_code", return_value="851121.SI"):
            result = collector.collect_industry_peers("600176", industry="电气设备")

        assert result["industry_name"] == "电气设备"
        # 由于 fina_indicator/daily_basic 查询返回空，同行数据可能不完整
        # 但结构应该正确
        assert "peers" in result
        assert "rankings" in result

    def test_collect_industry_peers_ranking_higher_growth_is_better(self):
        """营收增速排名：值越高 rank 越小（1=最快）。"""
        pv = [12.5, 8.0, 20.0]
        tv = 25.0
        below = sum(1 for v in pv if v < tv)
        above = sum(1 for v in pv if v > tv)
        rank = above + 1
        assert rank == 1
        pct = below / len(pv) * 100
        from lib.render import _competitive_position_label
        assert _competitive_position_label(pct) == "龙头"

    def test_rev_growth_without_ar_no_crash(self):
        """AR 缺失但存货+营收存在时不应 NameError。"""
        from lib.render import render_report_v3

        c = _collection_phase2()
        for dim in c["dimensions"]:
            if dim["dimension"] == "financials":
                for i, r in enumerate(dim["data"]):
                    r.pop("accounts_receiv", None)
                    r["inventory"] = 0.8e8 + i * 0.1e8
                break
        text = render_report_v3(c, "600176")
        assert "存货增速" in text
        assert "NameError" not in text

    def test_peer_table_roe_zero_displayed(self):
        from lib.render import render_report_v3

        c = _collection_phase2()
        c["industry_peers"]["target"]["roe"] = 0.0
        text = render_report_v3(c, "600176")
        assert "| **0.00** |" in text or "**0.00**" in text.split("同行可比公司")[1]

    def test_median_of_even_count(self):
        from lib.valuation import median_of

        assert median_of([15.0, 20.0, 25.0, 30.0]) == 22.5

    def test_collect_all_attaches_phase2_extras(self):
        from lib import collector

        def _fake_basic(_symbol: str) -> dict:
            return {"dimension": "basic_info", "data": {}, "status": "available"}

        with patch.object(
            collector, "COLLECTORS", {"basic_info": ("基本信息", _fake_basic)},
        ), patch.object(collector, "attach_phase2_extras") as mock_attach:
            result = collector.collect_all("600176", dims=["basic_info"])
        mock_attach.assert_called_once_with(result, "600176")

    def test_safe_float_val(self):
        from lib.nums import safe_float

        assert safe_float(3.14) == 3.14
        assert safe_float(None) is None
        assert safe_float(float("nan")) is None
        assert safe_float("abc") is None
        assert safe_float("3.14") == 3.14
        assert safe_float(0.0) == 0.0


class TestCollectorPeerHelpers:
    """同行 YoY 同期对齐与申万回退标记。"""

    def test_prior_year_end_date(self):
        from lib.collector import _prior_year_end_date

        assert _prior_year_end_date("20240331") == "20230331"
        assert _prior_year_end_date("20231231") == "20221231"
        assert _prior_year_end_date("2023-12-31") == "20221231"

    def test_revenue_yoy_aligned_same_period(self):
        from lib.collector import _revenue_yoy_from_fina_rows

        rows = [
            {"end_date": "20230331", "revenue": 4.0e9},
            {"end_date": "20240331", "revenue": 5.0e9},
        ]
        yoy = _revenue_yoy_from_fina_rows(rows)
        assert yoy is not None
        assert abs(yoy - 25.0) < 0.01

    def test_revenue_yoy_missing_prior_period(self):
        from lib.collector import _revenue_yoy_from_fina_rows

        rows = [{"end_date": "20240331", "revenue": 5.0e9}]
        assert _revenue_yoy_from_fina_rows(rows) is None

    def test_peer_source_fallback_not_sufficient(self):
        from lib.render import render_report_v3

        c = _collection_phase2()
        c["industry_peers"]["peer_source"] = "stock_basic_fallback"
        c["industry_peers"]["sufficient"] = False
        c["industry_peers"]["warning"] = "非申万 L3"
        text = render_report_v3(c, "600176")
        mod4 = text.split("## 4.")[1].split("## 5.")[0]
        assert "非申万 L3" in mod4
        assert "同行可比公司" not in mod4

    def test_peer_metrics_from_fina_fields(self):
        from lib.collector import _peer_metrics_from_fina

        rows = [
            {"end_date": "20221231", "grossprofit_margin": 35.0, "debt_to_assets": 40.0},
            {"end_date": "20231231", "grossprofit_margin": 30.0, "debt_to_assets": 45.0},
        ]
        metrics = _peer_metrics_from_fina(rows, rows[-1])
        assert metrics["gross_margin"] == 30.0
        assert metrics["debt_to_assets"] == 45.0
        assert metrics["gross_margin_trend"] == "down"

    def test_pcr_subsample_caps_api_calls(self):
        from lib.collector import _ms_subsample_trade_dates, _PCR_MAX_DAILY_QUERIES

        dates = [f"2020{i:02d}{j:02d}" for i in range(1, 13) for j in (1, 15)]
        sampled = _ms_subsample_trade_dates(dates, _PCR_MAX_DAILY_QUERIES)
        assert len(sampled) <= _PCR_MAX_DAILY_QUERIES
        assert sampled[-1] == dates[-1]

    def test_valuation_percentiles_uses_local_cache_not_collection(self):
        from lib.render import _index_dims, _v3_valuation_percentiles

        c = _collection_phase2()
        dims = _index_dims(c)
        cache: dict = {}
        r1 = _v3_valuation_percentiles(dims, cache)
        r2 = _v3_valuation_percentiles(dims, cache)
        assert r1 == r2
        assert "_v3_val_pct" not in c
        assert cache["result"] == r1
