"""Tests for limit_up_scanner — 纯函数测试，无网络依赖。"""

from __future__ import annotations

import math
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

_LIB = Path(__file__).resolve().parent.parent / "scripts" / "lib"
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

from limit_up_scanner import (
    filter_stocks,
    format_market_brief,
    format_stock_table,
    quality_filter,
    scan_market,
    _fetch_day_with_aux,
    _merge_daily_results,
    _compute_breadth,
    _compute_seal_quality,
    _daily_counts_from_stocks,
    get_trade_dates,
    _safe_float,
    _nullable_float,
    _fmt_yi,
    _fmt_date,
    _empty_breadth,
    _latest_market_cap,
    _one_to_two_rate,
)

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from scan import build_parser, resolve_cli_filter  # noqa: E402
from tushare_enrich import (
    _get_client,
    _market_label,
    _safe_float_or_none,
    _safe_str,
    _to_ts_code,
    enrich_price_data,
    enrich_stock_info,
    get_trade_dates,
)


# ---- Fixtures ----

def _make_stock(symbol, name, sector, max_consecutive=1, total_appearances=1,
                market_cap=5e9, seal_time="093000", break_count=0, turnover=5.0,
                first_date="20260710", last_date="20260710", **extra):
    stock = {
        "symbol": symbol, "name": name, "sector": sector,
        "max_consecutive": max_consecutive, "total_appearances": total_appearances,
        "first_date": first_date, "last_date": last_date,
        "appearances": [{
            "date": last_date, "consecutive": max_consecutive,
            "seal_time": seal_time, "seal_amount": 1e8,
            "break_count": break_count, "turnover": turnover,
            "market_cap": market_cap, "change_pct": 10.0,
            "stat": f"{max_consecutive}/{max_consecutive}",
        }],
        "flags": {"in_strong": False, "in_previous": False, "in_zbgc": False},
    }
    stock.update(extra)
    return stock


def _make_result(stocks, trading_days=3, scan_date="20260713"):
    daily_counts = {f"202607{10+i:02d}": len(stocks) for i in range(trading_days)}
    return {
        "scan_date": scan_date,
        "trading_days_scanned": trading_days,
        "stocks": stocks,
        "market_breadth": _compute_breadth(stocks, daily_counts),
        "errors": [],
        "enrichment": {"tushare": False, "enriched_count": 0},
    }


# ---- filter_stocks ----

class TestFilterStocks:

    def test_filter_by_min_consecutive(self):
        s1 = _make_stock("000001", "A", "银行", max_consecutive=1)
        s2 = _make_stock("000002", "B", "银行", max_consecutive=3)
        r = _make_result([s1, s2])
        f = filter_stocks(r, min_consecutive=2)
        assert len(f["stocks"]) == 1
        assert f["stocks"][0]["symbol"] == "000002"

    def test_filter_by_sector(self):
        s1 = _make_stock("000001", "A", "银行")
        s2 = _make_stock("000002", "B", "半导体")
        r = _make_result([s1, s2])
        f = filter_stocks(r, sectors=["半导体"])
        assert len(f["stocks"]) == 1
        assert f["stocks"][0]["name"] == "B"

    def test_filter_exclude_delisting(self):
        s1 = _make_stock("000001", "国华退", "软件")
        s2 = _make_stock("000002", "正常股", "软件")
        r = _make_result([s1, s2])
        f = filter_stocks(r)
        assert len(f["stocks"]) == 1
        assert f["stocks"][0]["name"] == "正常股"

    def test_exclude_names_without_tui_keeps_delisting_rule(self):
        """exclude_names_contain 不含「退」时，退市排除仍生效（加法而非替换）。"""
        s1 = _make_stock("000001", "国华退", "软件", market_cap=5e10)
        s2 = _make_stock("000002", "正常", "软件", market_cap=5e10)
        s3 = _make_stock("000003", "风险股", "软件", market_cap=5e10)
        r = _make_result([s1, s2, s3])
        f = filter_stocks(r, exclude_names_contain=["风险"])
        assert [s["symbol"] for s in f["stocks"]] == ["000002"]
        assert f["filter_stats"]["filtered_reasons"].get("delisting") == 1
        assert f["filter_stats"]["filtered_reasons"].get("name_exclude") == 1

    def test_filter_by_market_cap(self):
        s1 = _make_stock("000001", "小盘", "银行", market_cap=1e9)
        s2 = _make_stock("000002", "中盘", "银行", market_cap=5e10)
        r = _make_result([s1, s2])
        f = filter_stocks(r, min_market_cap=2e10)
        assert len(f["stocks"]) == 1
        assert f["stocks"][0]["name"] == "中盘"

    def test_filter_combined(self):
        s1 = _make_stock("000001", "A", "半导体", max_consecutive=2, market_cap=5e10)
        s2 = _make_stock("000002", "B", "银行", max_consecutive=1, market_cap=1e9)
        s3 = _make_stock("000003", "ST退", "半导体", max_consecutive=2)
        s4 = _make_stock("000004", "C", "半导体", max_consecutive=3, market_cap=3e10)
        r = _make_result([s1, s2, s3, s4])
        f = filter_stocks(r, sectors=["半导体"], min_consecutive=2, min_market_cap=2e10)
        syms = {s["symbol"] for s in f["stocks"]}
        assert syms == {"000001", "000004"}


# ---- quality_filter ----

class TestQualityFilter:

    def test_max_break_count(self):
        s1 = _make_stock("000001", "稳", "银行", break_count=0, market_cap=5e10)
        s2 = _make_stock("000002", "炸", "银行", break_count=3, market_cap=5e10)
        r = _make_result([s1, s2])
        f = quality_filter(r, max_break_count=3, min_price=0, min_float_mkt_cap=0, exclude_st=False)
        assert len(f["stocks"]) == 1
        assert f["stocks"][0]["symbol"] == "000001"
        assert f["filter_stats"]["filtered_reasons"]["max_break_count"] == 1

    def test_exclude_st_when_enriched(self):
        s1 = _make_stock("000001", "正常", "银行", market_cap=5e10, is_st=False)
        s2 = _make_stock("000002", "ST风险", "银行", market_cap=5e10, is_st=True)
        r = _make_result([s1, s2])
        f = quality_filter(r, exclude_st=True, min_price=0, min_float_mkt_cap=0)
        assert len(f["stocks"]) == 1
        assert f["stocks"][0]["symbol"] == "000001"

    def test_min_price_unknown_when_missing(self):
        """有 min_price 但无 L1/L2 股价 → 剔除（不再静默放行）。"""
        s = _make_stock("000001", "A", "银行", market_cap=5e10)
        r = _make_result([s])
        f = quality_filter(r, min_price=5.0, min_float_mkt_cap=0, exclude_st=False)
        assert len(f["stocks"]) == 0
        assert f["filter_stats"]["filtered_reasons"]["price_unknown"] == 1

    def test_min_price_uses_l1_appearance_close(self):
        """无 Tushare close 时回退 appearance.close（来自涨停池最新价）。"""
        s1 = _make_stock("000001", "贵", "银行", market_cap=5e10)
        s1["appearances"][0]["close"] = 10.0
        s2 = _make_stock("000002", "便宜", "银行", market_cap=5e10)
        s2["appearances"][0]["close"] = 3.0
        r = _make_result([s1, s2])
        f = quality_filter(r, min_price=5.0, min_float_mkt_cap=0, exclude_st=False)
        assert [s["symbol"] for s in f["stocks"]] == ["000001"]

    def test_min_price_when_enriched(self):
        s1 = _make_stock("000001", "贵", "银行", market_cap=5e10, close=10.0)
        s2 = _make_stock("000002", "便宜", "银行", market_cap=5e10, close=3.0)
        r = _make_result([s1, s2])
        f = quality_filter(r, min_price=5.0, min_float_mkt_cap=0, exclude_st=False)
        assert len(f["stocks"]) == 1
        assert f["stocks"][0]["symbol"] == "000001"

    def test_min_float_mkt_cap(self):
        s1 = _make_stock("000001", "大", "银行", market_cap=5e10, float_mkt_cap=30e8)
        s2 = _make_stock("000002", "小", "银行", market_cap=5e10, float_mkt_cap=10e8)
        r = _make_result([s1, s2])
        f = quality_filter(r, min_float_mkt_cap=20e8, min_price=0, exclude_st=False)
        assert len(f["stocks"]) == 1
        assert f["stocks"][0]["symbol"] == "000001"

    def test_exclude_delisting(self):
        s1 = _make_stock("000001", "国华退", "软件", market_cap=5e10)
        s2 = _make_stock("000002", "正常", "软件", market_cap=5e10)
        r = _make_result([s1, s2])
        f = quality_filter(r, min_price=0, min_float_mkt_cap=0, exclude_st=False)
        assert len(f["stocks"]) == 1

    def test_market_cap_unknown_when_constrained(self):
        """M9: 有市值阈值但缺数据 → 剔除。"""
        s = _make_stock("000001", "无市值", "银行", market_cap=0)
        r = _make_result([s])
        f = quality_filter(
            r, min_market_cap=1e8, min_price=0, min_float_mkt_cap=0, exclude_st=False,
        )
        assert len(f["stocks"]) == 0
        assert f["filter_stats"]["filtered_reasons"]["market_cap_unknown"] == 1

    def test_filtered_daily_counts_consistent(self):
        """筛选后 daily_counts 来自筛选集，并保留原日历零日。"""
        s1 = _make_stock(
            "000001", "A", "半导体", max_consecutive=2, market_cap=5e10, last_date="20260710",
        )
        s1["appearances"] = [
            {"date": "20260710", "consecutive": 1, "seal_time": "093000",
             "seal_amount": 1e8, "break_count": 0, "turnover": 5.0,
             "market_cap": 5e10, "change_pct": 10.0, "stat": "1/1"},
            {"date": "20260711", "consecutive": 2, "seal_time": "093000",
             "seal_amount": 1e8, "break_count": 0, "turnover": 5.0,
             "market_cap": 5e10, "change_pct": 10.0, "stat": "2/2"},
        ]
        s2 = _make_stock(
            "000002", "B", "银行", max_consecutive=1, market_cap=5e10, last_date="20260710",
        )
        r = _make_result([s1, s2])
        # 全市场含第三日（零涨停）与虚高计数
        r["market_breadth"]["daily_counts"] = {
            "20260710": 100, "20260711": 100, "20260712": 0,
        }
        f = quality_filter(
            r, sectors=["半导体"], min_price=0, min_float_mkt_cap=0, exclude_st=False,
        )
        assert f["market_breadth"]["daily_counts"] == {
            "20260710": 1, "20260711": 1, "20260712": 0,
        }
        assert f["market_breadth"]["avg_daily_count"] == round(2 / 3, 1)

    def test_filter_mode_full_vs_lightweight(self):
        s = _make_stock("000001", "A", "银行", market_cap=5e10)
        r = _make_result([s])
        full = quality_filter(r, filter_mode="full", min_price=0, min_float_mkt_cap=0, exclude_st=False)
        light = quality_filter(r, filter_mode="lightweight")
        assert full["filter_mode"] == "full"
        assert full["quality_filter_applied"] is True
        assert light["filter_mode"] == "lightweight"
        assert light["quality_filter_applied"] is False

    def test_lightweight_defaults_skip_price_break_st(self):
        """lightweight 默认不施加价/炸板/ST；显式参数仍可覆盖。"""
        cheap = _make_stock("000001", "便宜", "银行", market_cap=5e10, close=3.0, break_count=5)
        st = _make_stock("000002", "ST风险", "银行", market_cap=5e10, close=10.0, is_st=True)
        r = _make_result([cheap, st])
        light = quality_filter(r, filter_mode="lightweight")
        assert {s["symbol"] for s in light["stocks"]} == {"000001", "000002"}
        assert light["quality_filter_applied"] is False
        # 显式覆盖仍生效
        overridden = quality_filter(r, filter_mode="lightweight", min_price=5.0)
        assert [s["symbol"] for s in overridden["stocks"]] == ["000002"]

    def test_full_defaults_apply_price_break_st(self):
        cheap = _make_stock("000001", "便宜", "银行", market_cap=5e10, close=3.0)
        r = _make_result([cheap])
        full = quality_filter(r)  # filter_mode=full 默认
        assert full["stocks"] == []
        assert full["filter_stats"]["filtered_reasons"]["min_price"] == 1

    def test_filter_passthrough_unknown_keys(self):
        s = _make_stock("000001", "A", "银行", market_cap=5e10)
        r = _make_result([s])
        r["custom_meta"] = {"foo": 1}
        f = quality_filter(r, filter_mode="lightweight")
        assert f["custom_meta"] == {"foo": 1}

    def test_filter_stocks_name_exclude_reason(self):
        s1 = _make_stock("000001", "正常", "软件", market_cap=5e10)
        s2 = _make_stock("000002", "风险股", "软件", market_cap=5e10)
        r = _make_result([s1, s2])
        f = filter_stocks(r, exclude_names_contain=["退", "风险"])
        assert len(f["stocks"]) == 1
        assert f["filter_mode"] == "lightweight"
        assert f["quality_filter_applied"] is False
        assert f["filter_stats"]["filtered_reasons"].get("name_exclude") == 1
        assert f["filter_stats"]["output_count"] == 1


# ---- scan_market (mocked) ----

class TestScanMarket:

    def test_scan_market_structure_and_empty_day(self):
        """含空涨停日仍计入 trading_days_scanned；结构完整。"""
        def fake_fetch(date):
            if date == "20260710":
                return (
                    [{"代码": "000001", "名称": "A", "连板数": 1, "首次封板时间": "093000",
                      "封板资金": 1e8, "炸板次数": 0, "换手率": 5.0, "总市值": 1e10,
                      "涨跌幅": 10.0, "涨停统计": "1/1", "所属行业": "银行"}],
                    None,
                    {"strong": {"000001"}, "previous": set(), "zbgc": set()},
                    [],
                )
            if date == "20260711":
                return [], None, {"strong": set(), "previous": set(), "zbgc": set()}, []
            return (
                [{"代码": "000001", "名称": "A", "连板数": 2, "首次封板时间": "093015",
                  "封板资金": 1e8, "炸板次数": 0, "换手率": 5.0, "总市值": 1e10,
                  "涨跌幅": 10.0, "涨停统计": "2/2", "所属行业": "银行"}],
                None,
                {"strong": set(), "previous": set(), "zbgc": set()},
                [],
            )

        with patch("limit_up_scanner.env.is_akshare_available", return_value=True), \
             patch("limit_up_scanner.akshare_push2_available", return_value=True), \
             patch("limit_up_scanner.get_trade_dates", return_value=["20260712", "20260711", "20260710"]), \
             patch("limit_up_scanner._fetch_day_with_aux", side_effect=fake_fetch), \
             patch("limit_up_scanner._apply_tushare_enrich", return_value={"tushare": False, "enriched_count": 0}):
            result = scan_market(days=3)

        assert result["trading_days_scanned"] == 3
        assert result["market_breadth"]["days_with_limit_ups"] == 2
        assert result["market_breadth"]["daily_counts"]["20260711"] == 0
        assert len(result["stocks"]) == 1
        assert result["stocks"][0]["max_consecutive"] == 2
        assert result["stocks"][0]["total_appearances"] == 2
        assert result["stocks"][0]["flags"]["in_strong"] is True
        assert "seal_quality" in result["market_breadth"]

    def test_akshare_unavailable(self):
        """Gate is env.is_akshare_available — must not hit live EastMoney APIs."""
        with patch("limit_up_scanner.env.is_akshare_available", return_value=False), \
             patch("limit_up_scanner.akshare_push2_available", return_value=True), \
             patch("limit_up_scanner.get_trade_dates") as mock_dates, \
             patch("limit_up_scanner._fetch_day_with_aux") as mock_fetch:
            result = scan_market(days=3)
        mock_dates.assert_not_called()
        mock_fetch.assert_not_called()
        assert result["trading_days_scanned"] == 0
        assert result["stocks"] == []
        assert "push2" in result["errors"][0].lower() or "不可用" in result["errors"][0]

    def test_push2_unavailable(self):
        """push2 blocked but akshare stock_zt_pool_em may still work — continue scan."""
        with patch("limit_up_scanner.env.is_akshare_available", return_value=True), \
             patch("limit_up_scanner.akshare_push2_available", return_value=False), \
             patch("limit_up_scanner.get_trade_dates") as mock_dates, \
             patch("limit_up_scanner._fetch_day_with_aux") as mock_fetch:
            mock_dates.return_value = ["20260713", "20260712"]
            mock_fetch.return_value = (
                [{"代码": "000001", "名称": "Test", "连板数": 1, "首次封板时间": "093000",
                  "封板资金": 1e8, "炸板次数": 0, "换手率": 5.0, "总市值": 1e10,
                  "涨跌幅": 10.0, "涨停统计": "1/1", "所属行业": "银行"}],
                None,
                {"strong": set(), "previous": set(), "zbgc": set()},
                [],
            )
            result = scan_market(days=3)
        # push2 unavailable is logged as warning but scan continues normally
        assert result["trading_days_scanned"] > 0
        assert len(result["stocks"]) > 0

    def test_tushare_fallback(self):
        def fake_fetch(date):
            return (
                [{"代码": "000001", "名称": "A", "连板数": 1, "首次封板时间": "093000",
                  "封板资金": 1e8, "炸板次数": 0, "换手率": 5.0, "总市值": 1e10,
                  "涨跌幅": 10.0, "涨停统计": "1/1", "所属行业": "银行"}],
                None,
                {"strong": set(), "previous": set(), "zbgc": set()},
                [],
            )

        with patch("limit_up_scanner.env.is_akshare_available", return_value=True), \
             patch("limit_up_scanner.akshare_push2_available", return_value=True), \
             patch("limit_up_scanner.get_trade_dates", return_value=["20260710"]), \
             patch("limit_up_scanner._fetch_day_with_aux", side_effect=fake_fetch), \
             patch("limit_up_scanner.enrich_stock_info", return_value={}), \
             patch("limit_up_scanner.enrich_price_data", return_value={}):
            result = scan_market(days=1)

        assert result["enrichment"]["tushare"] is False
        assert len(result["stocks"]) == 1


# ---- _merge_daily_results ----

class TestMergeDailyResults:

    def test_dedup_across_days(self):
        daily = {
            "20260710": [
                {"代码": "000001", "名称": "A", "连板数": 1, "首次封板时间": "093000",
                 "封板资金": 1e8, "炸板次数": 0, "换手率": 5.0, "总市值": 1e10,
                 "涨跌幅": 10.0, "涨停统计": "1/1", "所属行业": "银行"},
            ],
            "20260711": [
                {"代码": "000001", "名称": "A", "连板数": 2, "首次封板时间": "093015",
                 "封板资金": 1.5e8, "炸板次数": 0, "换手率": 6.0, "总市值": 1.1e10,
                 "涨跌幅": 10.0, "涨停统计": "2/2", "所属行业": "银行"},
            ],
        }
        stocks = _merge_daily_results(daily)
        assert len(stocks) == 1
        assert stocks[0]["total_appearances"] == 2
        assert stocks[0]["max_consecutive"] == 2
        assert len(stocks[0]["appearances"]) == 2
        breadth = _compute_breadth(stocks, daily)
        assert breadth["total_unique_stocks"] == 1

    def test_exclude_delisting_in_merge(self):
        daily = {
            "20260710": [
                {"代码": "000001", "名称": "国华退", "连板数": 1, "首次封板时间": "093000",
                 "封板资金": 1e8, "炸板次数": 0, "换手率": 5.0, "总市值": 1e9,
                 "涨跌幅": 10.0, "涨停统计": "1/1", "所属行业": "软件"},
            ],
        }
        stocks = _merge_daily_results(daily)
        assert len(stocks) == 0

    def test_float_mkt_cap_from_akshare(self):
        daily = {
            "20260710": [
                {"代码": "000001", "名称": "A", "连板数": 1, "首次封板时间": "093000",
                 "封板资金": 1e8, "炸板次数": 0, "换手率": 5.0, "总市值": 1e10,
                 "流通市值": 5e9, "涨跌幅": 10.0, "涨停统计": "1/1", "所属行业": "银行"},
            ],
        }
        stocks = _merge_daily_results(daily)
        assert stocks[0]["float_mkt_cap"] == 5e9

    def test_close_from_akshare_latest_price(self):
        daily = {
            "20260710": [
                {"代码": "000001", "名称": "A", "连板数": 1, "首次封板时间": "093000",
                 "封板资金": 1e8, "炸板次数": 0, "换手率": 5.0, "总市值": 1e10,
                 "最新价": 12.5, "涨跌幅": 10.0, "涨停统计": "1/1", "所属行业": "银行"},
            ],
        }
        stocks = _merge_daily_results(daily)
        assert stocks[0]["close"] == 12.5
        assert stocks[0]["appearances"][0]["close"] == 12.5

    def test_aux_flags_merged(self):
        daily = {
            "20260710": [
                {"代码": "000001", "名称": "A", "连板数": 1, "首次封板时间": "093000",
                 "封板资金": 1e8, "炸板次数": 0, "换手率": 5.0, "总市值": 1e10,
                 "涨跌幅": 10.0, "涨停统计": "1/1", "所属行业": "银行"},
            ],
        }
        aux = {"20260710": {"strong": {"000001"}, "previous": set(), "zbgc": {"000001"}}}
        stocks = _merge_daily_results(daily, aux)
        assert stocks[0]["flags"]["in_strong"] is True
        assert stocks[0]["flags"]["in_zbgc"] is True
        assert stocks[0]["appearances"][0]["in_strong"] is True


# ---- _compute_breadth ----

class TestComputeBreadth:

    def test_consecutive_distribution(self):
        s1 = _make_stock("000001", "A", "X", max_consecutive=1)
        s2 = _make_stock("000002", "B", "X", max_consecutive=2)
        s3 = _make_stock("000003", "C", "X", max_consecutive=3)
        s4 = _make_stock("000004", "D", "X", max_consecutive=5)
        daily = {"20260710": 4}
        b = _compute_breadth([s1, s2, s3, s4], daily)
        assert b["consecutive_dist"]["1板"] == 1
        assert b["consecutive_dist"]["2板"] == 1
        assert b["consecutive_dist"]["3板"] == 1
        assert b["consecutive_dist"]["4板+"] == 1
        assert b["total_unique_stocks"] == 4

    def test_sector_distribution(self):
        s1 = _make_stock("000001", "A", "银行")
        s2 = _make_stock("000002", "B", "银行")
        s3 = _make_stock("000003", "C", "半导体")
        daily = {"20260710": 3}
        b = _compute_breadth([s1, s2, s3], daily)
        assert ("银行", 2) in b["sector_dist"]
        assert ("半导体", 1) in b["sector_dist"]

    def test_empty_input(self):
        b = _compute_breadth([], {})
        assert b["total_unique_stocks"] == 0
        assert b["avg_daily_count"] == 0
        assert b["days_with_limit_ups"] == 0

    def test_market_dist_and_seal_quality(self):
        s1 = _make_stock(
            "000001", "A", "银行", max_consecutive=1, seal_time="093000",
            break_count=1, market_cap=5e10, float_mkt_cap=20e8, market="主板",
        )
        s1["appearances"][0]["seal_amount"] = 2e8  # 封流比 10%
        s2 = _make_stock(
            "000002", "B", "银行", max_consecutive=1, seal_time="100000",
            break_count=0, market_cap=5e10, market="创业板",
        )
        b = _compute_breadth([s1, s2], {"20260710": 2})
        assert b["market_dist"]["主板"] == 1
        assert b["market_dist"]["创业板"] == 1
        assert b["seal_quality"]["early_seal_rate"] == 0.5
        assert b["seal_quality"]["avg_break_count"] == 0.5
        assert b["seal_quality"]["seal_flow_gt_5pct"] == 1.0

    def test_one_to_two_rate(self):
        s = {
            "symbol": "000001", "name": "A", "sector": "X",
            "max_consecutive": 2, "total_appearances": 2,
            "appearances": [
                {"date": "20260710", "consecutive": 1, "seal_time": "093000",
                 "seal_amount": 1e8, "break_count": 0, "turnover": 5.0,
                 "market_cap": 1e10, "change_pct": 10.0, "stat": "1/1"},
                {"date": "20260711", "consecutive": 2, "seal_time": "093000",
                 "seal_amount": 1e8, "break_count": 0, "turnover": 5.0,
                 "market_cap": 1e10, "change_pct": 10.0, "stat": "2/2"},
            ],
        }
        s_fail = {
            "symbol": "000002", "name": "B", "sector": "X",
            "max_consecutive": 1, "total_appearances": 1,
            "appearances": [
                {"date": "20260710", "consecutive": 1, "seal_time": "093000",
                 "seal_amount": 1e8, "break_count": 0, "turnover": 5.0,
                 "market_cap": 1e10, "change_pct": 10.0, "stat": "1/1"},
            ],
        }
        cal = ["20260710", "20260711"]
        assert _one_to_two_rate([s, s_fail], trade_dates=cal) == 0.5
        # 无日历 → 0（不再误用 appearances 回退）
        assert _one_to_two_rate([s, s_fail]) == 0.0
        b = _compute_breadth([s, s_fail], {"20260710": 2, "20260711": 1})
        assert b["seal_quality"]["one_to_two_rate"] == 0.5

    def test_one_to_two_rate_uses_trade_calendar_gap(self):
        """中间空交易日不应把 10→12 当成相邻晋级。"""
        s = {
            "symbol": "000001", "name": "A", "sector": "X",
            "max_consecutive": 2, "total_appearances": 2,
            "appearances": [
                {"date": "20260710", "consecutive": 1, "seal_time": "093000",
                 "seal_amount": 1e8, "break_count": 0, "turnover": 5.0,
                 "market_cap": 1e10, "change_pct": 10.0, "stat": "1/1"},
                {"date": "20260712", "consecutive": 2, "seal_time": "093000",
                 "seal_amount": 1e8, "break_count": 0, "turnover": 5.0,
                 "market_cap": 1e10, "change_pct": 10.0, "stat": "2/2"},
            ],
        }
        assert _one_to_two_rate([s]) == 0.0
        assert _one_to_two_rate(
            [s], trade_dates=["20260710", "20260711", "20260712"],
        ) == 0.0

    def test_consecutive_dist_skips_zero(self):
        s0 = _make_stock("000001", "Z", "X", max_consecutive=0)
        s0["max_consecutive"] = 0
        s1 = _make_stock("000002", "A", "X", max_consecutive=1)
        b = _compute_breadth([s0, s1], {"20260710": 1})
        assert b["consecutive_dist"].get("1板") == 1
        assert b["consecutive_dist"].get("其它") == 1
        assert sum(b["consecutive_dist"].values()) == b["total_unique_stocks"]


# ---- Formatting ----

class TestFormatting:

    def test_market_brief_contains_key_sections(self):
        s = _make_stock("000001", "测试股", "银行", market="主板")
        r = _make_result([s])
        brief = format_market_brief(r)
        assert "涨停扫描" in brief
        assert "每日涨停趋势" in brief
        assert "连板分布" in brief
        assert "行业热度" in brief
        assert "市场分布" in brief
        assert "封板质量" in brief

    def test_stock_table_sorted_by_consecutive(self):
        s1 = _make_stock("000001", "A", "X", max_consecutive=1)
        s2 = _make_stock("000002", "B", "X", max_consecutive=3)
        s3 = _make_stock("000003", "C", "X", max_consecutive=2)
        table = format_stock_table([s1, s2, s3], max_rows=10)
        lines = table.split("\n")
        data_lines = [l for l in lines if l.startswith("| 0")]
        assert len(data_lines) == 3
        assert "000002" in data_lines[0]
        assert "000001" in data_lines[2]

    def test_stock_table_respects_max_rows(self):
        stocks = [_make_stock(f"00000{i}", f"S{i}", "X") for i in range(10)]
        table = format_stock_table(stocks, max_rows=5)
        data_lines = [l for l in table.split("\n") if l.startswith("| 0")]
        assert len(data_lines) == 5

    def test_leaders_section(self):
        s = _make_stock("000001", "龙头", "银行", max_consecutive=3)
        r = _make_result([s])
        brief = format_market_brief(r)
        assert "连板龙头" in brief
        assert "000001" in brief

    def test_leaders_empty_appearances_no_crash(self):
        s = _make_stock("000001", "龙头", "银行", max_consecutive=3)
        s["appearances"] = []
        r = _make_result([s])
        brief = format_market_brief(r)
        assert "连板龙头" in brief


# ---- Utilities ----

class TestUtilities:

    def test_get_trade_dates(self):
        dates = get_trade_dates(5)
        assert len(dates) >= 5
        assert all(len(d) == 8 for d in dates)

    def test_safe_float(self):
        assert _safe_float("3.14") == 3.14
        assert _safe_float(None) == 0.0
        assert _safe_float("abc") == 0.0
        assert _safe_float(float("nan")) == 0.0

    def test_nullable_float(self):
        assert _nullable_float(None) is None
        assert _nullable_float("abc") is None
        assert _nullable_float(float("nan")) is None
        assert _nullable_float(0.0) == 0.0
        assert _nullable_float("1.5") == 1.5

    def test_seal_quality_falls_back_to_stock_float_mkt_cap(self):
        """Appearance missing/0.0 float_mkt_cap must use Tushare-enriched stock field."""
        for app_cap in (None, 0.0):
            stock = {
                "symbol": "000001",
                "float_mkt_cap": 1e10,
                "appearances": [{
                    "date": "20260710",
                    "seal_time": "093000",
                    "seal_amount": 6e8,
                    "break_count": 0,
                    "float_mkt_cap": app_cap,
                    "consecutive": 1,
                }],
            }
            q = _compute_seal_quality([stock], trade_dates=["20260710", "20260711"])
            assert q["seal_flow_gt_5pct"] == 1.0, app_cap

    def test_seal_quality_missing_seal_amount_no_crash(self):
        """seal_amount=None must not TypeError; flow metric stays 0.0."""
        stock = {
            "symbol": "000001",
            "float_mkt_cap": 1e10,
            "appearances": [{
                "date": "20260710",
                "seal_time": "093000",
                "seal_amount": None,
                "break_count": 0,
                "float_mkt_cap": 1e10,
                "consecutive": 1,
            }],
        }
        q = _compute_seal_quality([stock], trade_dates=["20260710", "20260711"])
        assert q["seal_flow_gt_5pct"] == 0.0

    def test_fmt_yi(self):
        assert _fmt_yi(1e8) == "1.0"
        assert _fmt_yi(0) == "-"
        assert _fmt_yi(None) == "-"

    def test_fmt_date(self):
        assert _fmt_date("20260713") == "2026-07-13"
        assert _fmt_date("abc") == "abc"

    def test_empty_breadth(self):
        b = _empty_breadth()
        assert b["total_unique_stocks"] == 0
        assert b["sector_dist"] == []
        assert "seal_quality" in b

    def test_latest_market_cap(self):
        s = _make_stock("000001", "A", "X", market_cap=5e9)
        assert _latest_market_cap(s) == 5e9
        assert _latest_market_cap({"appearances": []}) is None
        assert _latest_market_cap({"appearances": [{"market_cap": 0}]}) is None

    def test_daily_counts_from_stocks(self):
        s = _make_stock("000001", "A", "X")
        s["appearances"] = [
            {"date": "20260710"},
            {"date": "20260711"},
        ]
        assert _daily_counts_from_stocks([s]) == {"20260710": 1, "20260711": 1}
        assert _daily_counts_from_stocks(
            [s], calendar={"20260710": 9, "20260711": 9, "20260712": 0},
        ) == {"20260710": 1, "20260711": 1, "20260712": 0}


class TestApplyTushareEnrich:

    def test_keeps_zero_close(self):
        from limit_up_scanner import _apply_tushare_enrich

        stocks = [_make_stock("000001", "A", "X")]
        with patch("limit_up_scanner.enrich_stock_info", return_value={}), \
             patch(
                 "limit_up_scanner.enrich_price_data",
                 return_value={"000001": {"close": 0.0, "amount": 0.0, "float_mkt_cap": 1e9}},
             ):
            info = _apply_tushare_enrich(stocks, "20260710")
        assert info["tushare"] is True
        assert stocks[0]["close"] == 0.0
        assert stocks[0]["amount"] == 0.0
        assert stocks[0]["float_mkt_cap"] == 1e9


# ---- tushare_enrich helpers / H4-H7 ----

class TestTushareEnrichHelpers:

    def test_safe_float_or_none_rejects_nan(self):
        assert _safe_float_or_none(float("nan")) is None
        assert _safe_float_or_none(None) is None
        assert _safe_float_or_none("12.5") == 12.5

    def test_safe_str_rejects_nan(self):
        assert _safe_str(float("nan")) == ""
        assert _safe_str(None) == ""
        assert _safe_str("主板") == "主板"

    def test_market_label_chinese(self):
        assert _market_label("主板") == "主板"
        assert _market_label("创业板") == "创业板"
        assert _market_label("0") == "主板"

    def test_to_ts_code(self):
        assert _to_ts_code("600176") == "600176.SH"
        assert _to_ts_code("000001") == "000001.SZ"
        assert _to_ts_code("xxx") == ""


class TestTushareEnrichGuards:

    def test_enrich_price_empty_ts_codes_no_query(self):
        with patch("tushare_enrich._get_client") as mock_client:
            client = MagicMock()
            mock_client.return_value = client
            out = enrich_price_data(["bad"], "20260713")
            assert out == {}
            client.query.assert_not_called()

    def test_enrich_stock_info_passes_ts_code_filter(self):
        import pandas as pd

        with patch("tushare_enrich._get_client") as mock_client:
            client = MagicMock()
            mock_client.return_value = client
            client.query.return_value = pd.DataFrame([{
                "ts_code": "600176.SH", "name": "中国巨石",
                "market": "主板", "list_date": "19990521",
            }])
            out = enrich_stock_info(["600176"])
            assert "600176" in out
            kwargs = client.query.call_args.kwargs
            assert "ts_code" in kwargs
            assert "600176.SH" in kwargs["ts_code"]

    def test_enrich_price_skips_nan_close(self):
        import pandas as pd

        with patch("tushare_enrich._get_client") as mock_client:
            client = MagicMock()
            mock_client.return_value = client

            def _query(api_name, **kwargs):
                if api_name == "daily":
                    return pd.DataFrame([{
                        "ts_code": "600176.SH", "trade_date": "20260713",
                        "close": float("nan"), "pct_chg": float("nan"),
                        "amount": float("nan"),
                    }])
                return pd.DataFrame()

            client.query.side_effect = _query
            out = enrich_price_data(["600176"], "20260713")
            assert "600176" not in out


# ---- CLI filter resolution ----

class TestResolveCliFilter:

    def _parse(self, *argv: str):
        return build_parser().parse_args(list(argv))

    def test_include_st_alone_does_not_enable_full(self):
        args = self._parse("--include-st")
        assert resolve_cli_filter(args) is None

    def test_include_st_with_quality_keeps_st(self):
        args = self._parse("--quality-filter", "--include-st")
        kwargs = resolve_cli_filter(args)
        assert kwargs is not None
        assert kwargs["filter_mode"] == "full"
        assert kwargs["exclude_st"] is False

    def test_min_price_auto_enables_full(self):
        args = self._parse("--min-price", "8")
        kwargs = resolve_cli_filter(args)
        assert kwargs is not None
        assert kwargs["filter_mode"] == "full"
        assert kwargs["min_price"] == 8.0

    def test_sector_only_is_lightweight(self):
        args = self._parse("--sector", "半导体")
        kwargs = resolve_cli_filter(args)
        assert kwargs is not None
        assert kwargs["filter_mode"] == "lightweight"


# ---- _fetch_day_with_aux ----

class TestFetchDayWithAux:

    def test_skips_aux_when_main_pool_empty(self):
        import pandas as pd

        ak = MagicMock()
        ak.stock_zt_pool_em.return_value = pd.DataFrame()
        strong = MagicMock(name="strong")
        ak.stock_zt_pool_strong_em = strong
        ak.stock_zt_pool_previous_em = MagicMock(name="previous")
        ak.stock_zt_pool_zbgc_em = MagicMock(name="zbgc")

        with patch("limit_up_scanner.akshare_direct_session") as sess, \
             patch.dict("sys.modules", {"akshare": ak}):
            sess.return_value.__enter__ = MagicMock(return_value=None)
            sess.return_value.__exit__ = MagicMock(return_value=False)
            records, err, aux, aux_errors = _fetch_day_with_aux("20260710")

        assert records is None
        assert err is None
        assert aux_errors == []
        assert aux == {"strong": set(), "previous": set(), "zbgc": set()}
        strong.assert_not_called()
        ak.stock_zt_pool_previous_em.assert_not_called()
        ak.stock_zt_pool_zbgc_em.assert_not_called()

    def test_fetches_aux_when_main_has_rows(self):
        import pandas as pd

        ak = MagicMock()
        ak.stock_zt_pool_em.return_value = pd.DataFrame([{
            "代码": "000001", "名称": "平安银行", "连板数": 1,
        }])
        ak.stock_zt_pool_strong_em.return_value = pd.DataFrame([{"代码": "000001"}])
        ak.stock_zt_pool_previous_em.return_value = pd.DataFrame()
        ak.stock_zt_pool_zbgc_em.return_value = pd.DataFrame()

        with patch("limit_up_scanner.akshare_direct_session") as sess, \
             patch.dict("sys.modules", {"akshare": ak}):
            sess.return_value.__enter__ = MagicMock(return_value=None)
            sess.return_value.__exit__ = MagicMock(return_value=False)
            records, err, aux, aux_errors = _fetch_day_with_aux("20260710")

        assert err is None
        assert records is not None and len(records) == 1
        assert aux["strong"] == {"000001"}
        ak.stock_zt_pool_strong_em.assert_called_once_with("20260710")


# ---- Tushare enrichment degradation paths (no token / no client) ----


class TestTushareEnrichDegradation:
    """Degradation paths when Tushare client is unavailable."""

    def setup_method(self):
        """Reset global client cache before each test."""
        import tushare_enrich
        tushare_enrich._tushare = None
        tushare_enrich._tushare_checked = False

    def test_get_client_returns_none_when_token_unavailable(self):
        """When env.is_tushare_available returns False, _get_client must return None."""
        import tushare_enrich
        tushare_enrich._tushare_checked = False
        tushare_enrich._tushare = None
        with patch.object(tushare_enrich.env, "get_config", return_value={}), \
             patch.object(tushare_enrich.env, "is_tushare_available", return_value=False):
            client = _get_client()
        assert client is None
        assert tushare_enrich._tushare_checked is True

    def test_get_trade_dates_fallback_no_tushare(self):
        """Without Tushare token, fall back to n*1.4 calendar days in YYYYMMDD format."""
        with patch("tushare_enrich._get_client", return_value=None):
            dates = get_trade_dates(10)
        assert len(dates) >= 10
        assert len(dates) <= 20  # 10 * 1.4 = 14, but allow margin
        for d in dates:
            assert len(d) == 8
            assert d.isdigit()

    def test_enrich_stock_info_no_tushare_token(self):
        """When _get_client returns None, enrich_stock_info returns {}."""
        with patch("tushare_enrich._get_client", return_value=None):
            out = enrich_stock_info(["600176", "000001"])
        assert out == {}

    def test_enrich_price_data_no_tushare_token(self):
        """When _get_client returns None, enrich_price_data returns {}."""
        with patch("tushare_enrich._get_client", return_value=None):
            out = enrich_price_data(["600176", "000001"], "20260713")
        assert out == {}
