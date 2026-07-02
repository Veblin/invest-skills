"""Tests for lib/events.py — event classification, dedup, filtering, and integration."""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock

import pytest

from lib.events import (
    attach_events,
    _classify_event,
    _filter_by_days,
    _dedup_events,
    _build_summary,
    _normalize_date,
    _clean_title,
    _normalize_title_for_dedup,
    _get_logic_relation,
    _fetch_notice_events,
    _fetch_dividend_events,
    _fetch_shareholder_events,
    INDUSTRY_EVENTS_PLACEHOLDER,
    MARKET_EVENTS_PLACEHOLDER,
    PLACEHOLDER_NOTE_INDUSTRY,
    PLACEHOLDER_NOTE_MARKET,
)


# ── _classify_event ──


class TestClassifyEvent:
    def test_buyback(self):
        result = _classify_event({"title": "关于回购公司股份的公告", "raw_type": ""})
        assert result["event_type"] == "buyback"
        assert result["impact_dimension"] == "估值"
        assert result["duration"] == "中长期变量"

    def test_equity_incentive(self):
        result = _classify_event({"title": "股权激励计划草案", "raw_type": ""})
        assert result["event_type"] == "equity_incentive"

    def test_equity_incentive_via_stock_option(self):
        result = _classify_event({"title": "股票期权激励计划", "raw_type": ""})
        assert result["event_type"] == "equity_incentive"

    def test_private_placement(self):
        result = _classify_event({"title": "非公开发行A股股票预案", "raw_type": ""})
        assert result["event_type"] == "private_placement"

    def test_mna(self):
        result = _classify_event({"title": "关于收购XX公司股权的公告", "raw_type": ""})
        assert result["event_type"] == "mna"

    def test_mna_reorganize(self):
        result = _classify_event({"title": "重大资产重组公告", "raw_type": ""})
        assert result["event_type"] == "mna"

    def test_dividend(self):
        result = _classify_event({"title": "2025年度利润分配方案", "raw_type": ""})
        assert result["event_type"] == "dividend"

    def test_holder_decrease(self):
        result = _classify_event({"title": "关于股东减持计划的公告", "raw_type": ""})
        assert result["event_type"] == "holder_decrease"

    def test_holder_increase(self):
        result = _classify_event({"title": "关于控股股东增持公司股份的公告", "raw_type": ""})
        assert result["event_type"] == "holder_increase"

    def test_major_contract(self):
        result = _classify_event({"title": "关于签订重大合同的公告", "raw_type": ""})
        assert result["event_type"] == "major_contract"

    def test_major_contract_bid_win(self):
        result = _classify_event({"title": "项目中标公告", "raw_type": ""})
        assert result["event_type"] == "major_contract"

    def test_litigation(self):
        result = _classify_event({"title": "关于诉讼事项的公告", "raw_type": ""})
        assert result["event_type"] == "litigation"

    def test_st_risk(self):
        result = _classify_event({"title": "关于公司股票被实施退市风险警示的公告", "raw_type": ""})
        assert result["event_type"] == "st_risk"

    def test_st_risk_true_positive_standalone_st(self):
        result = _classify_event({"title": "关于公司股票被实施ST的公告", "raw_type": ""})
        assert result["event_type"] == "st_risk"

    def test_st_risk_false_positive_star(self):
        result = _classify_event({"title": "投资STAR科创板的公告", "raw_type": ""})
        assert result["event_type"] == "other"

    def test_st_risk_false_positive_request(self):
        result = _classify_event({"title": "关于公司REQUEST的说明", "raw_type": ""})
        assert result["event_type"] == "other"

    def test_annual_report(self):
        result = _classify_event({"title": "2025年年度报告", "raw_type": ""})
        assert result["event_type"] == "earnings_report"

    def test_semi_annual_report(self):
        result = _classify_event({"title": "2025年半年度报告", "raw_type": ""})
        assert result["event_type"] == "earnings_report"

    def test_quarterly_report(self):
        result = _classify_event({"title": "2026年第一季度报告", "raw_type": ""})
        assert result["event_type"] == "earnings_report"

    def test_earnings_guidance(self):
        result = _classify_event({"title": "2026年半年度业绩预告", "raw_type": ""})
        assert result["event_type"] == "earnings_guidance"

    def test_earnings_preview(self):
        result = _classify_event({"title": "2025年度业绩快报", "raw_type": ""})
        assert result["event_type"] == "earnings_preview"

    def test_earnings_guidance_revision(self):
        result = _classify_event({"title": "业绩修正公告", "raw_type": ""})
        assert result["event_type"] == "earnings_guidance"

    def test_other_default(self):
        result = _classify_event({"title": "关于召开股东大会的提示性公告", "raw_type": ""})
        assert result["event_type"] == "other"
        assert result["impact_dimension"] == "治理"
        assert result["duration"] == "短期扰动"

    def test_classify_uses_raw_type_fallback(self):
        """当标题不匹配时，应检查 raw_type 字段"""
        result = _classify_event({"title": "董事会决议公告", "raw_type": "回购"})
        assert result["event_type"] == "buyback"

    def test_private_placement_fundraising(self):
        result = _classify_event({"title": "关于募集资金使用的公告", "raw_type": ""})
        assert result["event_type"] == "private_placement"


# ── _get_logic_relation ──


class TestGetLogicRelation:
    def test_buyback_reinforces(self):
        assert _get_logic_relation("buyback") == "强化"

    def test_holder_decrease_weakens(self):
        assert _get_logic_relation("holder_decrease") == "削弱"

    def test_st_risk_weakens(self):
        assert _get_logic_relation("st_risk") == "削弱"

    def test_unknown_does_not_change(self):
        assert _get_logic_relation("unknown_type") == "不改变"


# ── _normalize_date ──


class TestNormalizeDate:
    def test_standard_format(self):
        assert _normalize_date("2026-06-15") == "2026-06-15"

    def test_compact_format(self):
        assert _normalize_date("20260615") == "2026-06-15"

    def test_slash_format(self):
        assert _normalize_date("2026/06/15") == "2026-06-15"

    def test_chinese_format(self):
        assert _normalize_date("2026年06月15日") == "2026-06-15"

    def test_single_digit_month(self):
        assert _normalize_date("2026/6/15") == "2026-06-15"

    def test_empty(self):
        assert _normalize_date("") is None

    def test_none(self):
        assert _normalize_date("--") is None

    def test_none_value(self):
        assert _normalize_date("N/A") is None

    def test_embedded_date(self):
        assert _normalize_date("2026-06-15 10:30:00") == "2026-06-15"


# ── _clean_title ──


class TestCleanTitle:
    def test_strip_whitespace(self):
        assert _clean_title("  公告内容  ") == "公告内容"

    def test_remove_url_encoding(self):
        assert _clean_title("关于%20回购的公告") == "关于回购的公告"


# ── _normalize_title_for_dedup ──


class TestNormalizeTitleForDedup:
    def test_remove_prefix(self):
        result = _normalize_title_for_dedup("关于回购公司股份的公告")
        assert "回购公司股份" in result
        assert "关于" not in result[:5]

    def test_remove_suffix(self):
        result = _normalize_title_for_dedup("回购股份公告")
        assert "回购股份" in result

    def test_compress_whitespace(self):
        result = _normalize_title_for_dedup("回购 公司 股份")
        assert "回购公司股份" in result


# ── _filter_by_days ──


class TestFilterByDays:
    def test_filter_old_events(self):
        today_str = datetime.now().strftime("%Y-%m-%d")
        old_str = (datetime.now() - timedelta(days=60)).strftime("%Y-%m-%d")
        events = [
            {"date": today_str, "title": "Recent", "type": "other"},
            {"date": old_str, "title": "Old", "type": "other"},
        ]
        result = _filter_by_days(events, 30)
        assert len(result) == 1
        assert result[0]["title"] == "Recent"

    def test_all_within_window(self):
        today_str = datetime.now().strftime("%Y-%m-%d")
        recent_str = (datetime.now() - timedelta(days=5)).strftime("%Y-%m-%d")
        events = [
            {"date": today_str, "title": "Today", "type": "other"},
            {"date": recent_str, "title": "Recent", "type": "other"},
        ]
        result = _filter_by_days(events, 30)
        assert len(result) == 2

    def test_empty_events(self):
        assert _filter_by_days([], 30) == []

    def test_zero_days(self):
        events = [{"date": "2026-06-29", "title": "Test", "type": "other"}]
        assert _filter_by_days(events, 0) == []

    def test_missing_date_preserved(self):
        events = [{"title": "No date", "type": "other"}]
        result = _filter_by_days(events, 30)
        assert len(result) == 1

    def test_invalid_date_dropped(self):
        events = [{"date": "bad-date", "title": "Bad date", "type": "other"}]
        result = _filter_by_days(events, 30)
        assert result == []

    def test_boundary_exact_cutoff(self):
        cutoff_str = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
        events = [
            {"date": cutoff_str, "title": "Boundary", "type": "other"},
        ]
        result = _filter_by_days(events, 30)
        assert len(result) == 1


# ── _dedup_events ──


class TestDedupEvents:
    def test_dedup_same_source(self):
        events = [
            {"date": "2026-06-15", "title": "关于回购公司股份的公告", "type": "buyback",
             "source": "akshare stock_individual_notice_report"},
            {"date": "2026-06-15", "title": "关于回购公司股份的公告", "type": "buyback",
             "source": "akshare stock_individual_notice_report"},
        ]
        result = _dedup_events(events)
        assert len(result) == 1

    def test_different_sources_kept(self):
        events = [
            {"date": "2026-06-15", "title": "回购公告", "type": "buyback",
             "source": "akshare stock_individual_notice_report"},
            {"date": "2026-06-15", "title": "回购公告", "type": "buyback",
             "source": "akshare stock_history_dividend_detail"},
        ]
        result = _dedup_events(events)
        assert len(result) == 2

    def test_different_dates_kept(self):
        events = [
            {"date": "2026-06-15", "title": "回购公告", "type": "buyback",
             "source": "akshare stock_individual_notice_report"},
            {"date": "2026-06-16", "title": "回购公告", "type": "buyback",
             "source": "akshare stock_individual_notice_report"},
        ]
        result = _dedup_events(events)
        assert len(result) == 2

    def test_different_titles_kept(self):
        events = [
            {"date": "2026-06-15", "title": "回购公告", "type": "buyback",
             "source": "akshare stock_individual_notice_report"},
            {"date": "2026-06-15", "title": "分红公告", "type": "dividend",
             "source": "akshare stock_individual_notice_report"},
        ]
        result = _dedup_events(events)
        assert len(result) == 2

    def test_empty_list(self):
        assert _dedup_events([]) == []


# ── _build_summary ──


class TestBuildSummary:
    def test_single_event(self):
        events = [{"date": "2026-06-15", "type": "buyback", "title": "回购"}]
        summary = _build_summary(events, 30)
        assert summary["count_30d"] == 1
        assert summary["latest_date"] == "2026-06-15"
        assert summary["window_days"] == 30
        assert summary["top_types"][0]["type"] == "buyback"
        assert summary["top_types"][0]["count"] == 1

    def test_multiple_types(self):
        events = [
            {"date": "2026-06-15", "type": "buyback", "title": "回购1"},
            {"date": "2026-06-16", "type": "buyback", "title": "回购2"},
            {"date": "2026-06-17", "type": "dividend", "title": "分红"},
            {"date": "2026-06-18", "type": "dividend", "title": "分红2"},
            {"date": "2026-06-19", "type": "dividend", "title": "分红3"},
            {"date": "2026-06-20", "type": "other", "title": "其他"},
        ]
        summary = _build_summary(events, 30)
        assert summary["count_30d"] == 6
        assert summary["latest_date"] == "2026-06-20"
        top = summary["top_types"]
        assert top[0]["type"] == "dividend"
        assert top[0]["count"] == 3
        assert top[1]["type"] == "buyback"
        assert top[1]["count"] == 2

    def test_empty_events(self):
        summary = _build_summary([], 30)
        assert summary["count_30d"] == 0
        assert summary["latest_date"] is None
        assert summary["top_types"] == []

    def test_top_types_limited_to_five(self):
        events = [{"date": "2026-06-15", "type": f"type_{i}", "title": f"e{i}"}
                  for i in range(10)]
        summary = _build_summary(events, 30)
        assert len(summary["top_types"]) == 5  # max 5


# ── 占位槽测试 ──


class TestPlaceholders:
    def test_industry_placeholder_empty(self):
        assert isinstance(INDUSTRY_EVENTS_PLACEHOLDER, list)
        assert len(INDUSTRY_EVENTS_PLACEHOLDER) == 0

    def test_market_placeholder_empty(self):
        assert isinstance(MARKET_EVENTS_PLACEHOLDER, list)
        assert len(MARKET_EVENTS_PLACEHOLDER) == 0

    def test_placeholder_notes_defined(self):
        assert "⏭️" in PLACEHOLDER_NOTE_INDUSTRY
        assert "⏭️" in PLACEHOLDER_NOTE_MARKET


# ── 集成测试（mock akshare） ──


class MockDataFrame:
    """模拟 akshare 返回的 DataFrame。"""

    def __init__(self, records: list[dict]):
        self._records = records

    @property
    def empty(self) -> bool:
        return len(self._records) == 0

    def to_dict(self, orient: str = "records") -> list[dict]:
        return self._records

    def __len__(self) -> int:
        return len(self._records)

    def __bool__(self) -> bool:
        return not self.empty

    def __repr__(self) -> str:
        return f"MockDataFrame({len(self._records)} records)"


class TestAttachEventsIntegration:
    @patch("lib.events._fetch_notice_events")
    @patch("lib.events._fetch_dividend_events")
    @patch("lib.events._fetch_shareholder_events")
    def test_attach_events_basic(self, mock_shareholder, mock_dividend, mock_notice):
        """验证 attach_events 的正确流程。"""
        mock_notice.return_value = [
            {
                "date": "2026-06-28",
                "type": "buyback",
                "title": "关于回购公司股份的公告",
                "impact_dimension": "估值",
                "duration": "中长期变量",
                "logic_relation": "强化",
                "source": "akshare stock_individual_notice_report",
                "url": "http://example.com",
            },
            {
                "date": "2026-06-15",
                "type": "dividend",
                "title": "分红方案公告",
                "impact_dimension": "估值",
                "duration": "短期扰动",
                "logic_relation": "强化",
                "source": "akshare stock_individual_notice_report",
                "url": "",
            },
        ]
        mock_dividend.return_value = []
        mock_shareholder.return_value = []

        collection: dict = {}
        result = attach_events(collection, "600176", days=30)

        assert "events" in result
        assert len(result["events"]) == 2
        assert result["events"][0]["date"] == "2026-06-28"  # 降序
        assert result["events"][1]["date"] == "2026-06-15"

        # 占位槽
        assert result["industry_events"] == []
        assert result["market_events"] == []

        # meta
        assert result["_meta"]["events_window_days"] == 30
        assert result["_meta"]["events_summary"]["count_30d"] == 2
        assert "⏭️" in result["_meta"]["industry_events_note"]
        assert "⏭️" in result["_meta"]["market_events_note"]

    @patch("lib.events._fetch_notice_events")
    @patch("lib.events._fetch_dividend_events")
    @patch("lib.events._fetch_shareholder_events")
    def test_attach_events_all_sources_fail(self, mock_shareholder, mock_dividend, mock_notice):
        """所有源失败时应返回空 events 列表。"""
        mock_notice.return_value = []  # 模拟成功但无数据
        mock_dividend.return_value = []
        mock_shareholder.return_value = []

        collection: dict = {}
        result = attach_events(collection, "600176", days=30)
        assert result["events"] == []
        assert result["_meta"]["events_summary"]["count_30d"] == 0

    @patch("lib.events._fetch_notice_events")
    @patch("lib.events._fetch_dividend_events")
    @patch("lib.events._fetch_shareholder_events")
    def test_attach_events_partial_source_failure(self, mock_shareholder, mock_dividend, mock_notice):
        """单源失败不应影响其他源的正常数据。"""
        mock_notice.return_value = [
            {
                "date": "2026-06-20",
                "type": "buyback",
                "title": "回购公告",
                "impact_dimension": "估值",
                "duration": "中长期变量",
                "logic_relation": "强化",
                "source": "akshare stock_individual_notice_report",
                "url": "",
            },
        ]
        # dividend 源抛出异常
        mock_dividend.side_effect = RuntimeError("API Error")
        mock_shareholder.return_value = []

        collection: dict = {}
        result = attach_events(collection, "600176", days=30)

        assert len(result["events"]) == 1
        assert result["events"][0]["type"] == "buyback"

    @patch("lib.events._fetch_notice_events")
    @patch("lib.events._fetch_dividend_events")
    @patch("lib.events._fetch_shareholder_events")
    def test_industry_market_placeholders_in_output(self, mock_shareholder, mock_dividend, mock_notice):
        """验证占位槽始终存在。"""
        mock_notice.return_value = []
        mock_dividend.return_value = []
        mock_shareholder.return_value = []

        collection: dict = {}
        result = attach_events(collection, "600176", days=30)

        assert "industry_events" in result
        assert "market_events" in result
        assert isinstance(result["industry_events"], list)
        assert isinstance(result["market_events"], list)

    @patch("lib.events._fetch_notice_events")
    @patch("lib.events._fetch_dividend_events")
    @patch("lib.events._fetch_shareholder_events")
    def test_existing_collection_preserved(self, mock_shareholder, mock_dividend, mock_notice):
        """验证已有 collection 的其他字段不受影响。"""
        mock_notice.return_value = []
        mock_dividend.return_value = []
        mock_shareholder.return_value = []

        collection = {
            "symbol": "600176",
            "dimensions": [{"dimension": "basic_info", "data": {"name": "中国巨石"}}],
        }
        result = attach_events(collection, "600176", days=30)

        assert result["symbol"] == "600176"
        assert len(result["dimensions"]) == 1
        assert result["dimensions"][0]["dimension"] == "basic_info"


# ── 数据源函数单元测试（mock akshare） ──


class TestFetchNoticeEvents:
    @patch("akshare.stock_individual_notice_report")
    def test_normal_notice_data(self, mock_notice):
        """验证正常情况下的公告事件提取。"""
        mock_notice.return_value = MockDataFrame([
            {"代码": "600176", "名称": "中国巨石", "公告标题": "关于回购公司股份的公告",
             "公告类型": "回购", "公告日期": "2026-06-15", "网址": "http://example.com"},
            {"代码": "600176", "名称": "中国巨石", "公告标题": "2025年年度报告",
             "公告类型": "定期报告", "公告日期": "2026-04-28", "网址": ""},
        ])

        events = _fetch_notice_events("600176")
        assert len(events) == 2
        assert events[0]["type"] == "buyback"
        assert events[0]["date"] == "2026-06-15"
        assert events[1]["type"] == "earnings_report"
        assert events[1]["date"] == "2026-04-28"

    @patch("akshare.stock_individual_notice_report")
    def test_empty_notice_data(self, mock_notice):
        mock_notice.return_value = MockDataFrame([])
        events = _fetch_notice_events("600176")
        assert events == []

    @patch("akshare.stock_individual_notice_report")
    def test_api_failure(self, mock_notice):
        mock_notice.side_effect = RuntimeError("Connection error")
        events = _fetch_notice_events("600176")
        assert events == []


class TestFetchDividendEvents:
    @patch("akshare.stock_history_dividend_detail")
    def test_dividend_from_history(self, mock_dividend):
        mock_dividend.return_value = MockDataFrame([
            {"股权登记日": "2026-06-20", "方案说明": "10派5元", "送转比例": ""},
        ])

        events = _fetch_dividend_events("600176")
        assert len(events) == 1
        assert events[0]["type"] == "dividend"
        assert events[0]["date"] == "2026-06-20"
        assert "10派5元" in events[0]["title"]

    @patch("akshare.stock_history_dividend_detail")
    @patch("akshare.stock_dividend_cninfo")
    def test_dividend_history_empty_fallback_to_cninfo(self, mock_cninfo, mock_history):
        mock_history.return_value = MockDataFrame([])
        mock_cninfo.return_value = MockDataFrame([
            {"股权登记日": "2026-06-25", "分红说明": "10派3元"},
        ])

        events = _fetch_dividend_events("600176")
        assert len(events) == 1
        assert events[0]["date"] == "2026-06-25"

    @patch("akshare.stock_history_dividend_detail")
    @patch("akshare.stock_dividend_cninfo")
    def test_dividend_both_sources_empty(self, mock_cninfo, mock_history):
        mock_history.return_value = MockDataFrame([])
        mock_cninfo.return_value = MockDataFrame([])

        events = _fetch_dividend_events("600176")
        assert events == []


class TestFetchShareholderEvents:
    @patch("akshare.stock_shareholder_change_ths")
    def test_shareholder_increase(self, mock_shareholder):
        mock_shareholder.return_value = MockDataFrame([
            {"股东名称": "大股东", "变动类型": "增持", "变动日期": "2026-06-10",
             "变动数量": "1000000"},
        ])

        events = _fetch_shareholder_events("600176")
        assert len(events) == 1
        assert events[0]["type"] == "holder_increase"

    @patch("akshare.stock_shareholder_change_ths")
    def test_shareholder_decrease(self, mock_shareholder):
        mock_shareholder.return_value = MockDataFrame([
            {"股东名称": "大股东", "变动类型": "减持", "变动日期": "2026-06-10",
             "变动数量": "500000"},
        ])

        events = _fetch_shareholder_events("600176")
        assert len(events) == 1
        assert events[0]["type"] == "holder_decrease"

    @patch("akshare.stock_shareholder_change_ths")
    def test_shareholder_empty(self, mock_shareholder):
        mock_shareholder.return_value = MockDataFrame([])
        events = _fetch_shareholder_events("600176")
        assert events == []

    @patch("akshare.stock_shareholder_change_ths")
    def test_shareholder_api_failure(self, mock_shareholder):
        mock_shareholder.side_effect = RuntimeError("API error")
        events = _fetch_shareholder_events("600176")
        assert events == []


class TestCollectAllDeepEvents:
    @patch("lib.events.attach_events")
    def test_deep_mode_sets_meta_and_90_day_window(self, mock_attach_events):
        from lib import collector

        def _fake_basic(_symbol: str) -> dict:
            return {"dimension": "basic_info", "data": {}, "status": "available"}

        with patch.object(
            collector, "COLLECTORS", {"basic_info": ("基本信息", _fake_basic)},
        ), patch.object(collector, "attach_phase2_extras"), patch(
            "lib.manifest.generate_manifest", return_value={},
        ), patch("lib.analysis_templates.build_analysis_cards"):
            result = collector.collect_all("600176", dims=["basic_info"], deep=True)

        assert result["_meta"]["deep"] is True
        mock_attach_events.assert_called_once()
        _, kwargs = mock_attach_events.call_args
        assert kwargs["days"] == 90

    @patch("lib.events.attach_events")
    def test_normal_mode_uses_30_day_window(self, mock_attach_events):
        from lib import collector

        def _fake_basic(_symbol: str) -> dict:
            return {"dimension": "basic_info", "data": {}, "status": "available"}

        with patch.object(
            collector, "COLLECTORS", {"basic_info": ("基本信息", _fake_basic)},
        ), patch.object(collector, "attach_phase2_extras"), patch(
            "lib.manifest.generate_manifest", return_value={},
        ), patch("lib.analysis_templates.build_analysis_cards"):
            collector.collect_all("600176", dims=["basic_info"], deep=False)

        _, kwargs = mock_attach_events.call_args
        assert kwargs["days"] == 30


# ── v0.1.6 review fixes ──


class TestNeedsEventsBackfill:
    def test_missing_events_key(self):
        from lib.events import needs_events_backfill

        assert needs_events_backfill({}) is True

    def test_empty_without_summary(self):
        from lib.events import needs_events_backfill

        assert needs_events_backfill({"events": []}) is True

    def test_empty_with_summary(self):
        from lib.events import needs_events_backfill

        coll = {
            "events": [],
            "_meta": {"events_summary": {"event_count": 0, "window_days": 30}},
        }
        assert needs_events_backfill(coll) is False

    def test_nonempty_events(self):
        from lib.events import needs_events_backfill

        coll = {"events": [{"date": "2026-06-01", "title": "x", "type": "other"}]}
        assert needs_events_backfill(coll) is False


class TestBuildSummaryEventCount:
    def test_90_day_window_exposes_event_count(self):
        events = [{"date": "2026-06-15", "type": "buyback", "title": "回购"}]
        summary = _build_summary(events, 90)
        assert summary["count_90d"] == 1
        assert summary["event_count"] == 1
        assert summary["window_days"] == 90


class TestAkshareDirectSession:
    @patch("lib.proxy.akshare_direct_session")
    def test_notice_fetcher_uses_direct_session(self, mock_session):
        mock_session.return_value.__enter__ = MagicMock(return_value=None)
        mock_session.return_value.__exit__ = MagicMock(return_value=False)
        with patch("akshare.stock_individual_notice_report", side_effect=Exception("skip")):
            _fetch_notice_events("600176")
        mock_session.assert_called_once()

    @patch("lib.proxy.akshare_direct_session")
    def test_dividend_fetcher_uses_direct_session(self, mock_session):
        mock_session.return_value.__enter__ = MagicMock(return_value=None)
        mock_session.return_value.__exit__ = MagicMock(return_value=False)
        with patch("akshare.stock_history_dividend_detail", side_effect=Exception("skip")), patch(
            "akshare.stock_dividend_cninfo", side_effect=Exception("skip"),
        ):
            _fetch_dividend_events("600176")
        assert mock_session.call_count == 2

    @patch("lib.proxy.akshare_direct_session")
    def test_shareholder_fetcher_uses_direct_session(self, mock_session):
        mock_session.return_value.__enter__ = MagicMock(return_value=None)
        mock_session.return_value.__exit__ = MagicMock(return_value=False)
        with patch("akshare.stock_shareholder_change_ths", side_effect=Exception("skip")):
            _fetch_shareholder_events("600176")
        mock_session.assert_called_once()
