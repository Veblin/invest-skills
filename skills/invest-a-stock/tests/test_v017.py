"""v0.1.7 审查修复单元测试。"""
from __future__ import annotations

import pytest


class TestParseHolderChangeVol:
    def test_ths_text_wan(self):
        from lib.collector import _parse_holder_change_vol

        assert _parse_holder_change_vol("增持58.11万") == pytest.approx(581100)

    def test_float_passthrough(self):
        from lib.collector import _parse_holder_change_vol

        assert _parse_holder_change_vol(5901992.0) == pytest.approx(5901992.0)

    def test_nan_returns_none(self):
        from lib.collector import _parse_holder_change_vol

        assert _parse_holder_change_vol(float("nan")) is None


class TestHolderDirectionAndDictHelpers:
    def test_infer_direction_from_change_type(self):
        from lib.collector import _infer_holder_direction

        row = {"变动类型": "减持", "变动数量": 1000000.0}
        assert _infer_holder_direction(row, 1000000.0, 1000000.0) == "减持"

    def test_infer_direction_from_numeric_sign(self):
        from lib.collector import _infer_holder_direction

        assert _infer_holder_direction({}, -500000.0, -500000.0) == "减持"
        assert _infer_holder_direction({}, 500000.0, 500000.0) == "增持"

    def test_first_present_preserves_zero(self):
        from lib.collector import _first_present

        row = {"变动数量": 0, "变动股数": 500}
        assert _first_present(row, "变动数量", "变动股数") == 0

    def test_coalesce_field_preserves_zero(self):
        from lib.nums import coalesce_field

        row = {"成交均价": 0, "交易均价": 12.5}
        assert coalesce_field(row, "成交均价", "交易均价") == 0.0

    def test_source_has_data_rejects_empty_list(self):
        from lib.collector import _source_has_data

        assert _source_has_data([]) is False
        assert _source_has_data([{"x": 1}]) is True
        assert _source_has_data(None) is False


class TestMergeHolderRecords:
    def _sr(self, source: str, records: list[dict]):
        from lib.schema import SourceResult

        return SourceResult(source, records, "holder_changes")

    def test_same_source_same_day_keeps_both(self):
        from lib.collector import _merge_holder_records

        ths = self._sr("akshare.stock_shareholder_change_ths", [
            {
                "ann_date": "20150917",
                "holder_name": "定向资产管理计划(振石)",
                "direction": "增持",
                "change_vol": 581100.0,
                "source": "akshare ths",
            },
            {
                "ann_date": "20150917",
                "holder_name": "定向资产管理计划(振石)",
                "direction": "增持",
                "change_vol": 394100.0,
                "source": "akshare ths",
            },
        ])
        merged = _merge_holder_records([ths])
        assert len(merged) == 2
        assert all(r.get("cross_check") == 1 for r in merged)

    def test_cross_source_merges_with_distinct_count(self):
        from lib.collector import _merge_holder_records

        ts = self._sr("tushare.stk_holdertrade", [{
            "ann_date": "20260516",
            "holder_name": "中国建材股份有限公司",
            "direction": "增持",
            "change_vol": 5901992.0,
            "source": "Tushare stk_holdertrade",
        }])
        ths = self._sr("akshare.stock_shareholder_change_ths", [{
            "ann_date": "20260516",
            "holder_name": "中国建材股份有限公司",
            "direction": "增持",
            "change_vol": 5902000.0,
            "source": "akshare ths",
        }])
        merged = _merge_holder_records([ts, ths])
        assert len(merged) == 1
        assert merged[0]["cross_check"] == 2
        assert merged[0]["source"] == "Tushare stk_holdertrade"

    def test_source_rank_prefers_tushare(self):
        from lib.collector import _merge_holder_records

        ths = self._sr("akshare.stock_shareholder_change_ths", [{
            "ann_date": "20260516",
            "holder_name": "中国建材股份有限公司",
            "direction": "增持",
            "change_vol": 5902000.0,
            "source": "akshare ths",
        }])
        ts = self._sr("tushare.stk_holdertrade", [{
            "ann_date": "20260516",
            "holder_name": "中国建材股份有限公司",
            "direction": "增持",
            "change_vol": 5901992.0,
            "source": "Tushare stk_holdertrade",
        }])
        merged = _merge_holder_records([ths, ts])
        assert merged[0]["source"] == "Tushare stk_holdertrade"

    def test_different_holders_same_prefix_not_merged(self):
        from lib.collector import _merge_holder_records

        ths = self._sr("akshare.stock_shareholder_change_ths", [{
            "ann_date": "20260516",
            "holder_name": "中国建材股份有限公司",
            "direction": "增持",
            "change_vol": 1000000.0,
            "source": "akshare ths",
        }])
        ts = self._sr("tushare.stk_holdertrade", [{
            "ann_date": "20260516",
            "holder_name": "中国建材集团控股有限公司",
            "direction": "增持",
            "change_vol": 1000000.0,
            "source": "Tushare stk_holdertrade",
        }])
        merged = _merge_holder_records([ths, ts])
        assert len(merged) == 2


class TestCalcWacc:
    def test_no_debt(self):
        from lib.valuation import calc_wacc

        r = calc_wacc(beta=1.15, risk_free_rate=0.0265, erp=0.058)
        assert r["wacc"] == pytest.approx(0.0932, abs=1e-4)
        assert "warning" not in r

    def test_debt_without_weight_warns(self):
        from lib.valuation import calc_wacc

        r = calc_wacc(
            beta=1.15, risk_free_rate=0.0265, erp=0.058, cost_of_debt=0.04,
        )
        assert r["wacc"] == pytest.approx(0.0932, abs=1e-4)
        assert "warning" in r
        assert "debt_weight" not in r["components"]

    def test_debt_with_weight(self):
        from lib.valuation import calc_wacc

        r = calc_wacc(
            beta=1.15, risk_free_rate=0.0265, erp=0.058,
            cost_of_debt=0.04, debt_weight=0.3,
        )
        assert r["wacc"] == pytest.approx(0.07424, abs=1e-4)
        assert r["components"]["equity_weight"] == pytest.approx(0.7)


class TestCalcFcff:
    def test_standard_fcff(self):
        from lib.valuation import calc_fcff

        r = calc_fcff(ebit=100.0, tax_rate=0.25, depr=20.0, cap_ex=30.0, delta_nwc=5.0)
        # nopat=75, fcff=75+20-30-5=60
        assert r["nopat"] == pytest.approx(75.0)
        assert r["fcff"] == pytest.approx(60.0)
        assert r["fcff_margin"] == pytest.approx(0.6)

    def test_zero_ebit_no_margin(self):
        from lib.valuation import calc_fcff

        r = calc_fcff(ebit=0, tax_rate=0.25, depr=10.0, cap_ex=5.0)
        assert r["fcff_margin"] is None


class TestCalcNetDebt:
    def test_net_debt_positive(self):
        from lib.valuation import calc_net_debt

        r = calc_net_debt(debt_total=500.0, money_cap=100.0)
        assert r["net_debt"] == pytest.approx(400.0)
        assert r["is_net_cash"] is False

    def test_net_cash(self):
        from lib.valuation import calc_net_debt

        r = calc_net_debt(debt_total=100.0, money_cap=500.0)
        assert r["net_debt"] == pytest.approx(-400.0)
        assert r["is_net_cash"] is True


class TestCalcEvToEquity:
    def test_per_share(self):
        from lib.valuation import calc_ev_to_equity

        r = calc_ev_to_equity(enterprise_value=1000.0, net_debt=200.0, shares_outstanding=100)
        assert r["equity_value"] == pytest.approx(800.0)
        assert r["per_share"] == pytest.approx(8.0)

    def test_zero_shares(self):
        from lib.valuation import calc_ev_to_equity

        r = calc_ev_to_equity(enterprise_value=1000.0, net_debt=0.0, shares_outstanding=0)
        assert r["per_share"] is None


class TestCalcBeta:
    def test_returns_beta(self):
        from lib.valuation import calc_beta

        stock = [0.02, 0.01, -0.01, 0.03, 0.0] * 3
        market = [0.01, 0.005, -0.005, 0.015, 0.0] * 3
        r = calc_beta(stock, market)
        assert r["beta"] is not None
        assert r["observations"] == 15

    def test_insufficient_data(self):
        from lib.valuation import calc_beta

        r = calc_beta([0.01] * 5, [0.01] * 5)
        assert r["beta"] is None
        assert "error" in r

    def test_zero_market_variance(self):
        from lib.valuation import calc_beta

        r = calc_beta([0.01] * 15, [0.0] * 15)
        assert r["beta"] is None
        assert "error" in r

    def test_near_zero_market_variance(self):
        from lib.valuation import calc_beta

        market = [1e-10] * 15
        r = calc_beta([0.01] * 15, market)
        assert r["beta"] is None
        assert "市场方差为零" in r.get("error", "")


class TestIndustryPricingIndustry:
    def test_dim_wrapper_passes_resolved_industry(self, monkeypatch):
        captured: dict[str, str] = {}

        def fake_pricing(sym: str, industry: str = "") -> dict:
            captured["industry"] = industry
            return {"dimension": "industry_pricing", "data": {"industry": industry}}

        monkeypatch.setattr("lib.collector.collect_industry_pricing", fake_pricing)
        monkeypatch.setattr(
            "lib.collector._resolve_industry_for_pricing",
            lambda sym, dr=None: "玻纤",
        )
        from lib.collector import collect_industry_pricing_dim

        collect_industry_pricing_dim("600176")
        assert captured["industry"] == "玻纤"

    def test_resolve_industry_from_parallel_basic_info(self):
        from lib.collector import _resolve_industry_for_pricing

        dim_results = {
            "basic_info": {"data": {"industry": "玻纤"}},
        }
        assert _resolve_industry_for_pricing("600176", dim_results) == "玻纤"


class TestGetFuturesForIndustry:
    def test_new_energy_vehicle(self):
        from lib.chain import get_futures_for_industry

        r = get_futures_for_industry("新能源汽车")
        assert ("碳酸锂", "LC") in r

    def test_pharma_empty(self):
        from lib.chain import get_futures_for_industry

        assert get_futures_for_industry("医药生物") == []


class TestDetectPriceShock:
    def test_from_close_sequence(self):
        from lib.collector import _detect_price_shock

        kline = [
            {"trade_date": "20260101", "close": 10.0},
            {"trade_date": "20260102", "close": 11.0},  # +10%
        ]
        r = _detect_price_shock("600176", kline)
        assert r["has_shock"] is True
        assert r["shock_dates"][0]["type"] == "limit_up"

    def test_empty_no_crash(self):
        from lib.collector import _detect_price_shock

        r = _detect_price_shock("600176", [])
        assert r["has_shock"] is False


class TestReportEnhancer:
    def test_has_price_signal(self):
        from lib.render import _has_price_signal

        data = {
            "industry_pricing": {
                "_meta": {
                    "all_sources": [
                        {"data": {"signal": "确认"}},
                    ],
                },
            },
        }
        assert _has_price_signal(data) is True

    def test_has_price_signal_non_dict_safe(self):
        from lib.render import _has_price_signal

        assert _has_price_signal({"industry_pricing": "bad"}) is False
        assert _has_price_signal({}) is False

    def test_valuation_extreme(self, monkeypatch):
        # patch facade：_is_valuation_extreme 运行时经 lib.render 查找
        from lib.render import _is_valuation_extreme

        monkeypatch.setattr(
            "lib.render._v3_valuation_percentiles",
            lambda dims, cache: (85.0, 50.0, "偏高"),
        )
        collection = {"dimensions": [{"dimension": "valuation", "data": []}]}
        assert _is_valuation_extreme(collection, percentile=80) is True
        monkeypatch.setattr(
            "lib.render._v3_valuation_percentiles",
            lambda dims, cache: (50.0, 50.0, "适中"),
        )
        assert _is_valuation_extreme(collection, percentile=80) is False


class TestNewsDateFilter:
    def test_within_30_days(self):
        from datetime import datetime, timedelta
        from lib.collector import _news_date_within

        recent = (datetime.now() - timedelta(days=5)).strftime("%Y-%m-%d %H:%M:%S")
        cutoff = datetime.now() - timedelta(days=30)
        assert _news_date_within(recent, cutoff) is True

    def test_outside_30_days(self):
        from datetime import datetime, timedelta
        from lib.collector import _news_date_within

        cutoff = datetime.now() - timedelta(days=30)
        assert _news_date_within("2020-01-01 10:00:00", cutoff) is False


class TestIndustryPricingRender:
    def test_futures_in_snapshot_block(self):
        from lib.render import _render_pricing_futures_section

        block = _render_pricing_futures_section({
            "data": {
                "has_futures": True,
                "industry": "玻璃",
                "玻璃": {"code": "FG", "spot_price": 1000, "dom_price": 980,
                         "dom_basis_rate": -0.02, "trend_30d": "↗ +1.0%"},
            },
            "_meta": {"all_sources": []},
        })
        assert "### 原材料成本速览" in block
        assert "FG" in block

    def test_futures_pd_na_renders_dash(self):
        import pandas as pd
        from lib.render import _render_pricing_futures_section

        block = _render_pricing_futures_section({
            "data": {
                "has_futures": True,
                "industry": "玻璃",
                "玻璃": {"code": "FG", "spot_price": pd.NA, "dom_price": 980,
                         "dom_basis_rate": -0.02, "trend_30d": "—"},
            },
            "_meta": {"all_sources": []},
        })
        assert "nan" not in block.lower()
        assert "—" in block

    def test_news_in_drivers_block(self):
        from lib.render import _render_pricing_news_section

        block = _render_pricing_news_section({
            "data": {"industry": "玻璃"},
            "_meta": {
                "all_sources": [{
                    "source": "akshare.stock_news_em",
                    "data": {
                        "signal": "确认",
                        "signal_detail": "近 30 日 2 条涨价相关新闻",
                        "matches": [{"date": "2026-06-01", "title": "提价落地"}],
                    },
                }],
            },
        })
        assert "### 涨价信号" in block
        assert "涨价趋势确认" in block


class TestRenderHolderChanges:
    def test_nan_avg_price_renders_dash(self):
        from lib.render import _section_holder_changes

        section = _section_holder_changes({
            "data": [{
                "ann_date": "20260304",
                "holder_name": "测试股东",
                "direction": "增持",
                "change_vol": 1000000,
                "change_ratio": 0.5,
                "avg_price": float("nan"),
                "source": "Tushare stk_holdertrade",
                "cross_check": 1,
            }],
        })
        assert "## 3d. 股东增减持动向" in section
        assert "nan" not in section.lower()
        assert "—" in section

    def test_enhancement_hints_in_extras(self):
        from lib.render import _render_enhancement_hints

        hints = _render_enhancement_hints({
            "_enhancements": {
                "valuation_high_alert": {"triggered": True},
            },
        })
        assert any("历史位置" in h for h in hints)
        assert not any("估值分位" in h for h in hints)

    def test_enhancement_hints_none_pct_chg_no_crash(self):
        from lib.render import _render_enhancement_hints

        hints = _render_enhancement_hints({
            "_enhancements": {
                "price_shock_detect": {
                    "has_shock": True,
                    "shock_type": "连续涨停",
                    "shock_dates": [{"date": "20260101", "pct_chg": None}],
                },
            },
        })
        assert any("20260101(—)" in h for h in hints)


class TestCollectHolderChangesFallback:
    def test_empty_tushare_triggers_cninfo(self, monkeypatch):
        from lib.collector import collect_holder_changes
        from lib.schema import SourceResult

        cninfo_called = {"value": False}

        def fake_parallel(tasks, dim):
            return [SourceResult("tushare.stk_holdertrade", [], dim)]

        def fake_cninfo(symbol):
            cninfo_called["value"] = True
            return [{"ann_date": "20260101", "direction": "增持", "source": "akshare cninfo"}]

        monkeypatch.setattr("lib.collector._run_sources_parallel", fake_parallel)
        monkeypatch.setattr("lib.collector._q_akshare_management_hold", fake_cninfo)
        monkeypatch.setattr("lib.collector.env.is_akshare_available", lambda: True)
        monkeypatch.setattr("lib.collector.env.is_tushare_available", lambda cfg: True)

        collect_holder_changes("600176")
        assert cninfo_called["value"] is True

    def test_tushare_without_ths_triggers_cninfo(self, monkeypatch):
        from lib.collector import collect_holder_changes
        from lib.schema import SourceResult

        cninfo_called = {"value": False}

        def fake_parallel(tasks, dim):
            return [
                SourceResult("tushare.stk_holdertrade", [{
                    "ann_date": "20260101",
                    "holder_name": "测试",
                    "direction": "增持",
                    "change_vol": 1000.0,
                }], dim),
                SourceResult("akshare.stock_shareholder_change_ths", [], dim),
            ]

        def fake_cninfo(symbol):
            cninfo_called["value"] = True
            return [{"ann_date": "20260101", "direction": "增持", "source": "akshare cninfo"}]

        monkeypatch.setattr("lib.collector._run_sources_parallel", fake_parallel)
        monkeypatch.setattr("lib.collector._q_akshare_management_hold", fake_cninfo)
        monkeypatch.setattr("lib.collector.env.is_akshare_available", lambda: True)
        monkeypatch.setattr("lib.collector.env.is_tushare_available", lambda cfg: True)

        collect_holder_changes("600176")
        assert cninfo_called["value"] is True

    def test_both_tushare_and_ths_skips_cninfo(self, monkeypatch):
        from lib.collector import collect_holder_changes
        from lib.schema import SourceResult

        cninfo_called = {"value": False}

        def fake_parallel(tasks, dim):
            return [
                SourceResult("tushare.stk_holdertrade", [{
                    "ann_date": "20260101",
                    "holder_name": "测试",
                    "direction": "增持",
                    "change_vol": 1000.0,
                }], dim),
                SourceResult("akshare.stock_shareholder_change_ths", [{
                    "ann_date": "20260101",
                    "holder_name": "测试",
                    "direction": "增持",
                    "change_vol": 1000.0,
                }], dim),
            ]

        def fake_cninfo(symbol):
            cninfo_called["value"] = True
            return [{"ann_date": "20260101", "direction": "增持", "source": "akshare cninfo"}]

        monkeypatch.setattr("lib.collector._run_sources_parallel", fake_parallel)
        monkeypatch.setattr("lib.collector._q_akshare_management_hold", fake_cninfo)
        monkeypatch.setattr("lib.collector.env.is_akshare_available", lambda: True)
        monkeypatch.setattr("lib.collector.env.is_tushare_available", lambda cfg: True)

        collect_holder_changes("600176")
        assert cninfo_called["value"] is False


class TestAkshareCompanyNewsPrice:
    def test_uses_direct_session(self, monkeypatch):
        from lib.collector import _q_akshare_company_news_price

        calls = {"direct": 0, "news": 0}

        class FakeCtx:
            def __enter__(self):
                calls["direct"] += 1
                return self

            def __exit__(self, *args):
                return False

        def fake_news(symbol):
            calls["news"] += 1
            return None

        monkeypatch.setattr("lib.collector.akshare_direct_session", lambda: FakeCtx())
        monkeypatch.setattr("lib.collector.env.is_akshare_available", lambda: True)
        monkeypatch.setattr("akshare.stock_news_em", fake_news)

        _q_akshare_company_news_price("600176")
        assert calls["direct"] == 1
        assert calls["news"] == 1


class TestCninfoManagementHold:
    def test_missing_symbol_column_skips(self, monkeypatch):
        import pandas as pd
        from lib.collector import _q_akshare_management_hold

        df = pd.DataFrame([{"董监高姓名": "张三", "变动数量": 1000}])

        monkeypatch.setattr("lib.collector.env.is_akshare_available", lambda: True)
        monkeypatch.setattr(
            "akshare.stock_hold_management_detail_cninfo",
            lambda symbol: df,
        )

        assert _q_akshare_management_hold("600176") is None

    def test_timeout_skips_direction(self, monkeypatch):
        import time
        from lib.collector import _q_akshare_management_hold

        def slow_cninfo(symbol):
            time.sleep(2)
            return None

        monkeypatch.setattr("lib.collector.env.is_akshare_available", lambda: True)
        monkeypatch.setattr("lib.collector.env.CNINFO_HOLDER_TIMEOUT_SEC", 0)
        monkeypatch.setattr(
            "akshare.stock_hold_management_detail_cninfo",
            slow_cninfo,
        )

        assert _q_akshare_management_hold("600176") is None


class TestDcfPreprocess:
    def test_build_from_latest_period(self):
        from lib.valuation import attach_dcf_preprocess, build_dcf_preprocess

        financials = {
            "data": [
                {"end_date": "20240331", "ebit": 1e9},
                {
                    "end_date": "20241231",
                    "ebit": 2e9,
                    "depr_amort": 3e8,
                    "cap_ex": -4e8,
                    "income_tax": 2e8,
                    "total_profit": 1e9,
                    "total_liab": 5e9,
                    "money_cap": 1e9,
                },
            ],
        }
        block = build_dcf_preprocess(financials)
        assert block is not None
        assert block["end_date"] == "20241231"
        assert block["fcff"]["fcff"] == pytest.approx(1.5e9, rel=1e-3)
        assert block["net_debt"]["net_debt"] == pytest.approx(4e9)

        attach_dcf_preprocess(financials)
        assert financials["dcf_preprocess"]["status"] == "available"

    def test_collect_financials_attaches_preprocess(self, monkeypatch):
        from lib.collector import collect_financials
        from lib.schema import SourceResult

        rows = [{
            "end_date": "20241231",
            "ebit": 1e9,
            "depr_amort": 1e8,
            "cap_ex": 2e8,
            "total_liab": 3e9,
            "money_cap": 5e8,
        }]

        monkeypatch.setattr(
            "lib.collector._run_sources_parallel",
            lambda tasks, dim: [SourceResult("tushare.fina_indicator", rows, dim)],
        )
        monkeypatch.setattr("lib.collector.env.is_tushare_available", lambda cfg: True)
        monkeypatch.setattr("lib.collector.env.is_akshare_available", lambda: False)

        legacy = collect_financials("600176")
        assert "dcf_preprocess" in legacy
        assert legacy["dcf_preprocess"]["computed"] == ["fcff", "net_debt"]

