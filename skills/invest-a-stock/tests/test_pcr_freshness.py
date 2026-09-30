"""PCR 时点与查询预算回归：全 mock，无网络。"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd


def test_pcr_uses_published_day_and_recent_window_first(monkeypatch):
    from lib.collector import _orchestrate as orch

    now = datetime(2026, 9, 30, 13, 31, tzinfo=ZoneInfo("Asia/Shanghai"))
    monkeypatch.setattr(orch, "shanghai_now", lambda: now)
    monkeypatch.setattr(orch, "_today", lambda: "20260930")
    monkeypatch.setattr(
        orch, "_days_ago",
        lambda n: (now.date() - timedelta(days=n)).strftime("%Y%m%d"),
    )
    dates = [d.strftime("%Y%m%d") for d in pd.bdate_range(end="2026-09-30", periods=1215)]

    class FakeTC:
        def __init__(self):
            self.requested: list[str] = []

        def available_rate_limit_slots(self, api):
            assert api == "opt_daily"
            return 79 if self.requested else 80

        def query(self, api, **kwargs):
            if api == "opt_basic":
                return pd.DataFrame([
                    {"ts_code": "C.SH", "name": "50ETF购", "call_put": "C"},
                    {"ts_code": "P.SH", "name": "50ETF沽", "call_put": "P"},
                ])
            if api == "trade_cal":
                return pd.DataFrame({"cal_date": dates})
            assert api == "opt_daily"
            self.requested.append(kwargs["trade_date"])
            return pd.DataFrame({"ts_code": ["C.SH", "P.SH"], "vol": [100, 50]})

    tc = FakeTC()
    result = orch._ms_fetch_put_call_ratio(tc)
    assert result is not None
    assert result["current_date"] == result["expected_latest_date"] == "20260929"
    assert result["percentile_60d"] is not None
    assert result["percentile_5y"] is None  # 七个历史点不足以代表五年分位
    assert result["partial"] is True  # 额度不足时明确降级
    assert result["sample_points"] <= 100
    assert len(tc.requested) <= 51
    assert len(tc.requested) <= 80 - orch._NEW_HIGH_SAMPLE - 2
    assert "20260930" not in tc.requested
    assert tc.requested[0] == "20260929"  # 最新已发布日探针
    assert all(d >= "20260801" for d in tc.requested[1:9])  # 并发请求先覆盖近期


def test_stale_pcr_excluded_from_current_cv8():
    from lib.render_utils import _v3_cv8_block, pcr_is_current_for_snapshot

    collection = {"dimensions": [{"dimension": "kline", "data": [
        {"trade_date": "20260929", "close": 100.0},
    ]}]}
    erp = {"percentile_5y": 3.3}
    margin = {"growth_pct": 0.35}
    stale = {"percentile_5y": 22.8, "current_date": "20260729"}
    assert not pcr_is_current_for_snapshot(stale, collection)
    assert _v3_cv8_block(erp, stale, margin, collection=collection) is None

    fresh = {**stale, "current_date": "20260929"}
    assert pcr_is_current_for_snapshot(fresh, collection)
    assert "CV-8" in _v3_cv8_block(erp, fresh, margin, collection=collection)


def test_pcr_does_not_start_timeout_storm_when_client_budget_is_empty(monkeypatch):
    from lib.collector import _orchestrate as orch

    monkeypatch.setattr(orch, "shanghai_now",
                        lambda: datetime(2026, 9, 30, 13, tzinfo=ZoneInfo("Asia/Shanghai")))

    class FakeTC:
        def query(self, api, **_kwargs):
            if api == "opt_basic":
                return pd.DataFrame([
                    {"ts_code": "C.SH", "name": "50ETF购", "call_put": "C"},
                    {"ts_code": "P.SH", "name": "50ETF沽", "call_put": "P"},
                ])
            if api == "trade_cal":
                return pd.DataFrame({"cal_date": ["20260928", "20260929", "20260930"]})
            raise AssertionError("额度为零时不应请求 opt_daily")

        def available_rate_limit_slots(self, api):
            assert api == "opt_daily"
            return 0

    diag = {}
    assert orch._ms_fetch_put_call_ratio(FakeTC(), diag=diag) is None
    assert diag["reason"] == "rate_limited"


def test_source_appendix_keeps_realtime_price_with_its_source():
    from lib.render_utils import _references_appendix

    collection = {"dimensions": [{
        "dimension": "quote", "display": "实时行情",
        "data": {"price": 1251.1, "kline": [{"trade_date": "20260929", "close": 1235.58}]},
        "_meta": {"all_sources": [
            {"source": "tushare.daily", "data_available": True,
             "data": [{"trade_date": "20260929", "close": 1235.58}]},
            {"source": "tencent_finance", "data_available": True,
             "data": {"price": 1251.1}},
        ]},
    }]}
    rows = _references_appendix(collection).splitlines()
    tushare_row = next(row for row in rows if "tushare.daily" in row)
    tencent_row = next(row for row in rows if "tencent_finance" in row)
    assert "最新价" not in tushare_row
    assert "最新价" in tencent_row


def test_client_headroom_accounts_for_shared_budget():
    from lib.tushare_client import TushareClient

    client = TushareClient(token="x" * 32, rate_limit_per_minute=5)
    for _ in range(3):
        client._wait_for_rate_limit("daily", reserve=True)
    assert client.available_rate_limit_slots("opt_daily") == 2


def test_new_high_ratio_stops_at_available_daily_slots(monkeypatch):
    from lib.collector import _orchestrate as orch

    monkeypatch.setattr(orch, "_today", lambda: "20260930")

    class FakeTC:
        def __init__(self):
            self.daily_calls = 0

        def available_rate_limit_slots(self, api):
            assert api == "daily"
            return 6

        def query(self, api, **_kwargs):
            if api == "stock_basic":
                return pd.DataFrame({"ts_code": [f"{i:06d}.SZ" for i in range(50)]})
            assert api == "daily"
            self.daily_calls += 1
            return pd.DataFrame([
                {"trade_date": "20260928", "close": 10, "high": 10},
                {"trade_date": "20260929", "close": 11, "high": 11},
            ])

    tc = FakeTC()
    result = orch._ms_fetch_new_high_ratio(tc)
    assert result is not None
    assert tc.daily_calls == 6
    assert result["sample_requested"] == 30
    assert result["sample_size"] == 6
    assert result["partial"] is True
    assert result["percentile_60d"] is None


def test_new_high_ratio_fewer_than_30_active_codes_is_partial(monkeypatch):
    from lib.collector import _orchestrate as orch

    monkeypatch.setattr(orch, "_today", lambda: "20260930")

    class FakeTC:
        def query(self, api, **_kwargs):
            if api == "stock_basic":
                return pd.DataFrame({"ts_code": [f"{i:06d}.SZ" for i in range(25)]})
            assert api == "daily"
            return pd.DataFrame([
                {"trade_date": f"202609{day:02d}", "close": day, "high": day}
                for day in range(1, 12)
            ])

    result = orch._ms_fetch_new_high_ratio(FakeTC())
    assert result is not None
    assert result["sample_requested"] == result["sample_size"] == 25
    assert result["sample_target"] == 30
    assert result["partial"] is True
    assert result["percentile_60d"] is None


def test_quote_marks_merged_source_and_realtime_price_provenance(monkeypatch):
    from types import SimpleNamespace
    from lib.collector import _orchestrate as orch

    realtime = SimpleNamespace(
        source="tencent_finance", data={"price": 1251.1},
        fetched_at="2026-09-30T05:31:54+00:00",
    )

    def fake_collect(_dimension, _tasks, *, postprocess, **_kwargs):
        return postprocess(
            {"data": [{"trade_date": "20260929", "close": 1235.58}],
             "_meta": {"source": "tushare.daily"}},
            [realtime],
        )

    monkeypatch.setattr(orch, "_collect_dimension", fake_collect)
    quote = orch.collect_quote("600519")
    assert quote["_meta"]["source"] == "merged:tushare.daily+tencent_finance"
    assert quote["_meta"]["price_source"] == "tencent_finance"
    assert quote["_meta"]["price_fetched_at"] == realtime.fetched_at
    assert quote["data"]["kline"][0]["trade_date"] == "20260929"


def test_partial_new_high_is_diagnostic_only_in_report():
    from lib.render_markdown._v3 import _section_market_structure

    collection = {"dimensions": [], "market_structure": {}}
    panel = {"new_high_ratio": {
        "ratio_pct": 24.0, "percentile_60d": 82.0,
        "sample_size": 25, "sample_requested": 25,
        "sample_target": 30, "partial": True,
    }}
    rendered = _section_market_structure(collection, "600519", panel)
    assert "样本 25/30；样本不完整，不作市场广度判断" in rendered
    assert "60日分位 —" in rendered
    assert "82.0%" not in rendered


def test_collection_period_shows_full_window_and_old_snapshot_fallback():
    from lib.shared_dates import fmt_collection_period

    collection = {
        "collection_started_at": "2026-09-30T05:31:53+00:00",
        "collection_completed_at": "2026-09-30T05:33:20+00:00",
        "fetched_at": "2026-09-30T05:31:54+00:00",
    }
    assert fmt_collection_period(collection) == (
        "2026-09-30 13:31–13:33 (北京时间；各维度取数时点不同)"
    )
    assert fmt_collection_period({"fetched_at": collection["fetched_at"]}) == (
        "2026-09-30 13:31 (北京时间)"
    )
