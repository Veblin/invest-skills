"""PCR 时点与查询预算回归：全 mock，无网络。"""

from __future__ import annotations

import threading
import time
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
    # 2026-09-30 裁决：额度先保历史下限（此前 new_high 的预留先于历史采样，
    # 同档 headroom 下 history_budget 只有个位数 → pct_5y 结构性为 None）。
    assert result["history_days"] >= orch._PCR_MIN_HISTORY_SAMPLES
    assert result["percentile_5y"] is not None
    # 计划点全部到齐 → 没有缺失可披露，partial 应为 False
    assert result["history_days"] == result["history_sample_target"]
    assert result["recent_observed_days"] == result["recent_days"]
    assert result["partial"] is False
    assert result["sample_points"] <= orch._PCR_MAX_DAILY_QUERIES
    assert len(tc.requested) <= 80  # 不得超出该接口当刻额度
    assert "20260930" not in tc.requested
    assert tc.requested[0] == "20260929"  # 最新已发布日探针
    assert all(d >= "20260801" for d in tc.requested[1:9])  # 并发请求先覆盖近期


def test_pcr_coverage_threshold_tolerates_up_to_one_tenth_missing():
    """覆盖率门槛：容忍 ≤10% 缺失，但不低于 floor（2026-09-30 裁决）。"""
    from lib.collector import _orchestrate as orch

    # 两个门槛取**更严**者：计划 100 点时要求 90 点（floor=30 不再起作用）
    assert orch._pcr_coverage_ok(90, 100, 30) is True
    assert orch._pcr_coverage_ok(89, 100, 30) is False
    # floor 在计划点很少时才起作用：计划 20 点、90% 只要 18，但 floor=30 → 18 不达
    assert orch._pcr_coverage_ok(18, 20, 30) is False
    assert orch._pcr_coverage_ok(30, 31, 30) is True
    # 90% 取上取整：42 点允许缺 4 点（38/42 = 90.4%），缺 5 点即不达
    assert orch._pcr_coverage_ok(38, 42, 5) is True
    assert orch._pcr_coverage_ok(37, 42, 5) is False
    # 空计划保护
    assert orch._pcr_coverage_ok(0, 0, 5) is False


def test_pcr_partial_sample_within_coverage_still_reports_percentiles(monkeypatch):
    """少量缺失（≤10%）不再让分位整块消失——披露责任改由 partial 承担。"""
    from lib.collector import _orchestrate as orch

    now = datetime(2026, 9, 30, 13, 31, tzinfo=ZoneInfo("Asia/Shanghai"))
    monkeypatch.setattr(orch, "shanghai_now", lambda: now)
    monkeypatch.setattr(orch, "_today", lambda: "20260930")
    monkeypatch.setattr(
        orch, "_days_ago",
        lambda n: (now.date() - timedelta(days=n)).strftime("%Y%m%d"),
    )
    dates = [d.strftime("%Y%m%d") for d in pd.bdate_range(end="2026-09-30", periods=1215)]
    failing = {"20260922", "20260923"}  # 近期窗口内 2 天取不到

    class FakeTC:
        def __init__(self):
            self.requested: list[str] = []

        def available_rate_limit_slots(self, api):
            return 79 if self.requested else 80

        def query(self, api, **kwargs):
            if api == "opt_basic":
                return pd.DataFrame([
                    {"ts_code": "C.SH", "name": "50ETF购", "call_put": "C"},
                    {"ts_code": "P.SH", "name": "50ETF沽", "call_put": "P"},
                ])
            if api == "trade_cal":
                return pd.DataFrame({"cal_date": dates})
            self.requested.append(kwargs["trade_date"])
            if kwargs["trade_date"] in failing:
                return pd.DataFrame()
            return pd.DataFrame({"ts_code": ["C.SH", "P.SH"], "vol": [100, 50]})

    result = orch._ms_fetch_put_call_ratio(FakeTC())
    assert result is not None
    assert result["recent_observed_days"] < result["recent_days"]
    assert result["percentile_60d"] is not None  # 覆盖率仍达门槛
    assert result["partial"] is True  # 缺失照样披露


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


# ── issue #35 残留（2026-10-02）：预算硬上界 / 常量锁定 / worker 层超时熔断 / 五年分母 ──


def _freeze_pcr_clock(monkeypatch):
    from lib.collector import _orchestrate as orch

    now = datetime(2026, 9, 30, 13, 31, tzinfo=ZoneInfo("Asia/Shanghai"))
    monkeypatch.setattr(orch, "shanghai_now", lambda: now)
    monkeypatch.setattr(orch, "_today", lambda: "20260930")
    monkeypatch.setattr(
        orch, "_days_ago",
        lambda n: (now.date() - timedelta(days=n)).strftime("%Y%m%d"),
    )
    return orch


def _pcr_calendar(n: int = 1215) -> list[str]:
    return [d.strftime("%Y%m%d") for d in pd.bdate_range(end="2026-09-30", periods=n)]


def _opt_frames():
    basic = pd.DataFrame([
        {"ts_code": "10004567.SH", "name": "50ETF购2601", "call_put": "C"},
        {"ts_code": "10004568.SH", "name": "50ETF沽2601", "call_put": "P"},
    ])
    daily = pd.DataFrame({"ts_code": ["10004567.SH", "10004568.SH"], "vol": [100.0, 50.0]})
    return basic, daily


class _PcrFakeTC:
    """opt_basic/trade_cal/opt_daily 假客户端（无 available_rate_limit_slots）。"""

    def __init__(self, cal, *, first_call_empty=False, always_empty=False):
        self.cal = cal
        self.first_call_empty = first_call_empty
        self.always_empty = always_empty
        self.opt_daily_calls = 0
        self._basic, self._daily = _opt_frames()

    def query(self, api, **kw):
        if api == "opt_basic":
            return self._basic
        if api == "trade_cal":
            return pd.DataFrame({"cal_date": self.cal})
        assert api == "opt_daily", api
        self.opt_daily_calls += 1
        if self.always_empty or (self.first_call_empty and self.opt_daily_calls == 1):
            return pd.DataFrame()
        return self._daily


class _PcrFakeTCWithSlots(_PcrFakeTC):
    def __init__(self, cal, slots):
        super().__init__(cal)
        self._slots = slots

    def available_rate_limit_slots(self, api):
        assert api == "opt_daily"
        return self._slots(self.opt_daily_calls)


def test_pcr_budget_invariant_locked():
    """常量漂移锁：硬上限不得超过接口推导额度（issue #35 A；审计 R5）。"""
    from lib.collector import _orchestrate as orch
    from lib.tushare_client import rate_limit_for_api

    assert orch._PCR_HISTORY_5Y_CAL_DAYS == 1825
    assert orch._PCR_HISTORY_60D == 60
    assert orch._PCR_QUERY_TIMEOUT_SEC == 8.0
    assert orch._NEW_HIGH_QUERY_TIMEOUT_SEC == 8.0
    assert orch._PCR_MAX_DAILY_QUERIES <= rate_limit_for_api("opt_daily")


def test_pcr_hard_clamp_without_slots_method(monkeypatch):
    """无 available_rate_limit_slots 的 client 也必须被硬上界钳住：
    probe + 扇出 ≤ _PCR_MAX_DAILY_QUERIES（修复前探针空帧时总调用可达 101+）。"""
    orch = _freeze_pcr_clock(monkeypatch)

    def _head_subsample(dates, max_points):
        return sorted(dates)[:max_points]

    monkeypatch.setattr(orch, "_ms_subsample_trade_dates", _head_subsample)
    tc = _PcrFakeTC(_pcr_calendar(), first_call_empty=True)
    result = orch._ms_fetch_put_call_ratio(tc)
    assert result is not None
    assert tc.opt_daily_calls <= orch._PCR_MAX_DAILY_QUERIES


def test_pcr_respects_explicit_low_headroom(monkeypatch):
    """显式低额度（12/分）：实际 opt_daily 调用不得超过当刻 headroom（R5 场景 1）。"""
    orch = _freeze_pcr_clock(monkeypatch)
    tc = _PcrFakeTCWithSlots(_pcr_calendar(), lambda used: max(0, 12 - used))
    result = orch._ms_fetch_put_call_ratio(tc)
    assert result is not None
    assert tc.opt_daily_calls <= 12
    assert result["partial"] is True


def test_pcr_respects_headroom_occupied_by_other_queries(monkeypatch):
    """账号桶已被其他查询占用 7 次：PCR 只能拿到剩余 5 次（R5 场景 2）。"""
    orch = _freeze_pcr_clock(monkeypatch)
    tc = _PcrFakeTCWithSlots(_pcr_calendar(), lambda used: max(0, 12 - 7 - used))
    result = orch._ms_fetch_put_call_ratio(tc)
    assert result is not None
    assert tc.opt_daily_calls <= 5


def test_pcr_empty_frames_are_empty_rows_not_timeout(monkeypatch):
    """正常空帧（端点健康）→ empty_rows，不得记成超时；总调用仍有界（R5 场景 3）。"""
    orch = _freeze_pcr_clock(monkeypatch)
    tc = _PcrFakeTC(_pcr_calendar(), always_empty=True)
    diag = {}
    assert orch._ms_fetch_put_call_ratio(tc, diag=diag) is None
    assert diag["reason"] == "empty_rows"
    assert "timeout_streak" not in diag
    assert tc.opt_daily_calls <= orch._PCR_MAX_DAILY_QUERIES


def test_pcr_probe_uses_named_timeout_constant(monkeypatch):
    """探针的超时预算必须取自具名常量，不再散落 8.0 字面量。"""
    orch = _freeze_pcr_clock(monkeypatch)
    recorded: list[tuple[str, float]] = []
    real = orch._run_with_timeout

    def spy(fn, timeout, label):
        recorded.append((label, timeout))
        return real(fn, timeout, label)

    monkeypatch.setattr(orch, "_run_with_timeout", spy)
    tc = _PcrFakeTC(_pcr_calendar(120))
    assert orch._ms_fetch_put_call_ratio(tc) is not None
    assert recorded, "探针必须经由 _run_with_timeout"
    assert all(label.startswith("opt_daily-probe") for label, _ in recorded)
    assert all(t == orch._PCR_QUERY_TIMEOUT_SEC for _, t in recorded)


def test_new_high_uses_named_timeout_constant(monkeypatch):
    orch = _freeze_pcr_clock(monkeypatch)
    recorded: list[tuple[str, float]] = []
    real = orch._run_with_timeout

    def spy(fn, timeout, label):
        recorded.append((label, timeout))
        return real(fn, timeout, label)

    monkeypatch.setattr(orch, "_run_with_timeout", spy)

    class FakeTC:
        def query(self, api, **_kw):
            if api == "stock_basic":
                return pd.DataFrame({"ts_code": [f"{i:06d}.SZ" for i in range(5)]})
            assert api == "daily"
            return pd.DataFrame([
                {"trade_date": f"202609{day:02d}", "close": day, "high": day}
                for day in range(1, 12)
            ])

    assert orch._ms_fetch_new_high_ratio(FakeTC()) is not None
    assert recorded and all(label.startswith("daily:") for label, _ in recorded)
    assert all(t == orch._NEW_HIGH_QUERY_TIMEOUT_SEC for _, t in recorded)


def test_ms_pcr_query_one_classifies_ok_empty_error_timeout(monkeypatch):
    """worker 层分类：ok/empty/error/timeout 四分（旧实现超时与空帧同返 None）。"""
    orch = _freeze_pcr_clock(monkeypatch)
    _, ok_frame = _opt_frames()
    frames = iter([
        (ok_frame, None),
        (pd.DataFrame(), None),
        (None, TimeoutError("timeout after 8.0s")),
        (None, ValueError("boom")),
    ])
    calls: list[tuple[str, float]] = []

    def fake_run(fn, timeout, label):
        calls.append((label, timeout))
        return next(frames)

    monkeypatch.setattr(orch, "_run_in_thread", fake_run)
    outcomes = [
        orch._ms_pcr_query_one(object(), "20260929", {"10004568.SH"}, {"10004567.SH"})
        for _ in range(4)
    ]
    assert outcomes[0] == (0.5, "ok")
    assert outcomes[1] == (None, "empty")
    assert outcomes[2] == (None, "timeout")
    assert outcomes[3] == (None, "error")
    assert all(label == "opt_daily:20260929" for label, _ in calls)
    assert all(t == orch._PCR_QUERY_TIMEOUT_SEC for _, t in calls)


def test_pcr_timeout_breaker_stops_query_storm(monkeypatch):
    """连续 5 次超时即熔断：停止信号在真实查询前生效，不再制造风暴（审计 R5）。"""
    orch = _freeze_pcr_clock(monkeypatch)
    monkeypatch.setenv("INVEST_MAX_WORKERS", "1")
    tc = _PcrFakeTC(_pcr_calendar())
    attempts = {"n": 0}

    def fake_run(fn, timeout, label):
        if label.startswith("opt_daily-probe"):
            return (pd.DataFrame(), None)
        attempts["n"] += 1
        return (None, TimeoutError("timeout after 8s"))

    monkeypatch.setattr(orch, "_run_in_thread", fake_run)
    diag = {}
    assert orch._ms_fetch_put_call_ratio(tc, diag=diag) is None
    assert attempts["n"] == orch._PCR_TIMEOUT_STREAK_LIMIT
    assert diag["reason"] == "timeout_streak"
    assert diag["timeout_streak"] == orch._PCR_TIMEOUT_STREAK_LIMIT
    reason = orch._ms_pcr_unavailable_reason(tc, diag)
    assert "超时" in reason
    assert "当日无 50ETF 期权成交" not in reason


def test_pcr_timeout_breaker_partial_result_marks_trip(monkeypatch):
    """熔断置停后保留已得样本：部分结果 partial=True，查询数远小于全窗口。"""
    orch = _freeze_pcr_clock(monkeypatch)
    monkeypatch.setenv("INVEST_MAX_WORKERS", "1")
    tc = _PcrFakeTC(_pcr_calendar(12))
    _, ok_frame = _opt_frames()
    fanout = {"n": 0}

    def fake_run(fn, timeout, label):
        if label.startswith("opt_daily-probe"):
            return (ok_frame, None)
        fanout["n"] += 1
        if fanout["n"] == 1:
            return (ok_frame, None)
        return (None, TimeoutError("timeout after 8s"))

    monkeypatch.setattr(orch, "_run_in_thread", fake_run)
    result = orch._ms_fetch_put_call_ratio(tc)
    assert result is not None
    assert result["partial"] is True
    assert fanout["n"] == 1 + orch._PCR_TIMEOUT_STREAK_LIMIT  # 1 成功 + 5 超时后熔断
    # 实际值如实记录（复核 P2-1）：扇出 6 次 + 探针 1 次；跳过的日期不计
    assert result["query_calls"] == 7
    assert result["queried_points"] == 7
    assert result["timeout_streak"] == orch._PCR_TIMEOUT_STREAK_LIMIT


def test_pcr_breaker_tick_freezes_after_trip():
    """熔断触发后计数冻结：迟到的任何完成不得改写（复核 P2-2）。"""
    from lib.collector import _orchestrate as orch

    state = {"streak": 0, "tripped": False}
    for _ in range(orch._PCR_TIMEOUT_STREAK_LIMIT - 1):
        assert orch._pcr_breaker_tick(state, "timeout") is False
    assert orch._pcr_breaker_tick(state, "timeout") is True
    assert state == {"streak": orch._PCR_TIMEOUT_STREAK_LIMIT, "tripped": True}
    # 迟到的成功/超时都不再改写触发时的证据
    assert orch._pcr_breaker_tick(state, "ok") is False
    assert orch._pcr_breaker_tick(state, "timeout") is False
    assert state == {"streak": orch._PCR_TIMEOUT_STREAK_LIMIT, "tripped": True}
    # 未触发路径：非超时完成清零连续计数
    live = {"streak": 2, "tripped": False}
    assert orch._pcr_breaker_tick(live, "empty") is False
    assert live["streak"] == 0


def test_pcr_timeout_breaker_late_success_freezes_evidence(monkeypatch):
    """熔断后到达的在途成功不得清零证据（复核 P2-2 复现）：
    diag 与部分结果都必须保留触发时的 streak 与原因。"""
    orch = _freeze_pcr_clock(monkeypatch)
    monkeypatch.setenv("INVEST_MAX_WORKERS", "8")
    tc = _PcrFakeTC(_pcr_calendar(12))
    _, ok_frame = _opt_frames()
    limit = orch._PCR_TIMEOUT_STREAK_LIMIT

    started = threading.Semaphore(0)
    release = [threading.Event() for _ in range(64)]
    gate_lock = threading.Lock()
    idx = {"n": 0}
    done = {"n": 0}

    def fake_run(fn, timeout, label):
        if label.startswith("opt_daily-probe"):
            return (ok_frame, None)
        with gate_lock:
            idx["n"] += 1
            i = idx["n"]
        started.release()
        if not release[i - 1].wait(timeout=5):  # 门未开 → 超时兜底，避免挂测试
            return (None, TimeoutError("gate timeout"))
        if i <= limit:
            with gate_lock:
                done["n"] += 1
            return (None, TimeoutError("timeout after 8s"))
        return (ok_frame, None)  # 第 6 个及以后 = 熔断后到达的在途成功

    monkeypatch.setattr(orch, "_run_in_thread", fake_run)
    diag = {}
    box: dict = {}

    def _run() -> None:
        box["result"] = orch._ms_fetch_put_call_ratio(tc, diag=diag)

    worker = threading.Thread(target=_run)
    worker.start()
    try:
        for _ in range(limit + 1):  # 等前 6 个扇出查询全部进入在途
            assert started.acquire(timeout=5)
        for ev in release[:limit]:  # 释放 5 个超时 → 触发熔断
            ev.set()
        deadline = time.monotonic() + 5
        while done["n"] < limit and time.monotonic() < deadline:
            time.sleep(0.01)  # 5 个超时结果已返回（熔断 tick 随即发生）
        time.sleep(0.3)  # 给熔断置位留出时序余量
        release[limit].set()  # 迟到的成功（熔断已发生）
    finally:
        for ev in release:  # 释放其余任何已进入在途的查询；断言失败也不挂线程
            ev.set()
    worker.join(timeout=10)
    assert not worker.is_alive()

    result = box["result"]
    assert diag["timeout_streak"] == limit
    assert diag["reason"] == "timeout_streak"
    assert result is not None
    assert result["partial"] is True
    assert result["timeout_streak"] == limit


def test_pcr_breaker_bounds_inflight_under_concurrency(monkeypatch):
    """真实 _run_in_thread + 短超时：**扇出阶段全部超时**时，已发出的扇出查询
    ≤ 阈值 + workers - 1（该上界以全超时为前提；成功/空帧会重置连续计数，
    任意序列的通用上界见 test_pcr_streak_resets_but_total_budget_still_bounds）；
    queried_points/query_calls 记录实际值（复核 P2-1/P2-3）。"""
    orch = _freeze_pcr_clock(monkeypatch)
    workers = 8
    monkeypatch.setenv("INVEST_MAX_WORKERS", str(workers))
    monkeypatch.setattr(orch, "_PCR_QUERY_TIMEOUT_SEC", 0.05)
    dates = _pcr_calendar(180)
    probe_date = [d for d in dates if d < "20260930"][-1]
    count_lock = threading.Lock()

    class SlowTC(_PcrFakeTC):
        def query(self, api, **kw):
            if api != "opt_daily":
                return super().query(api, **kw)
            with count_lock:
                self.opt_daily_calls += 1
            if str(kw.get("trade_date")) == probe_date:
                return self._daily
            time.sleep(0.3)  # 远超 0.05s 预算 → 必然判超时
            return self._daily

    tc = SlowTC(dates)
    diag = {}
    result = orch._ms_fetch_put_call_ratio(tc, diag=diag)
    limit = orch._PCR_TIMEOUT_STREAK_LIMIT
    fanout = tc.opt_daily_calls - 1  # 探针 1 次
    assert limit <= fanout <= limit + workers - 1
    assert result is not None          # 探针结果保留 → 部分结果
    assert result["partial"] is True
    assert result["timeout_streak"] == limit
    assert result["query_calls"] == 1 + fanout
    assert result["queried_points"] == 1 + fanout
    assert diag["reason"] == "timeout_streak"


def test_pcr_streak_resets_but_total_budget_still_bounds(monkeypatch):
    """成功完成会重置连续计数 →「阈值 + workers − 1」不是任意序列的上界；
    任意序列的通用硬上界是 _PCR_MAX_DAILY_QUERIES（复核 2026-10-02 复现）。"""
    orch = _freeze_pcr_clock(monkeypatch)
    monkeypatch.setenv("INVEST_MAX_WORKERS", "1")
    tc = _PcrFakeTC(_pcr_calendar())
    _, ok_frame = _opt_frames()
    # 4 次超时 → 成功 → 4 次超时 → 成功 → 5 次超时（第 15 次触发熔断）
    seq = ([None] * 4 + ["ok"] + [None] * 4 + ["ok"] + [None] * 5)
    state = {"n": 0}

    def fake_run(fn, timeout, label):
        if label.startswith("opt_daily-probe"):
            return (ok_frame, None)
        state["n"] += 1
        kind = seq[state["n"] - 1] if state["n"] <= len(seq) else None
        return (ok_frame, None) if kind == "ok" else (None, TimeoutError("timeout"))

    monkeypatch.setattr(orch, "_run_in_thread", fake_run)
    diag = {}
    result = orch._ms_fetch_put_call_ratio(tc, diag=diag)
    fanout = state["n"]
    assert fanout == 15  # 逐项复现复核场景
    # 全超时口径的界（5 + 1 − 1 = 5）不适用于本序列 —— 这正是需修正的表述前提
    assert fanout > orch._PCR_TIMEOUT_STREAK_LIMIT + 1 - 1
    assert diag["timeout_streak"] == orch._PCR_TIMEOUT_STREAK_LIMIT  # 触发时计数冻结
    assert result is not None
    assert result["partial"] is True
    assert result["query_calls"] == 1 + fanout
    assert result["query_calls"] <= orch._PCR_MAX_DAILY_QUERIES  # 通用硬上界


def test_pcr_probe_retry_counts_against_budget(monkeypatch):
    """首探针失败、重试成功：重试计入预算，实际调用/日期数被如实记录（复核 A/P2-1）。"""
    orch = _freeze_pcr_clock(monkeypatch)
    tc = _PcrFakeTC(_pcr_calendar())
    real_run = orch._run_with_timeout
    attempts = {"n": 0}

    def fake_run(fn, timeout, label):
        if label.startswith("opt_daily-probe"):
            attempts["n"] += 1
            if attempts["n"] == 1:
                return None  # 首次探针失败
        return real_run(fn, timeout, label)

    monkeypatch.setattr(orch, "_run_with_timeout", fake_run)
    result = orch._ms_fetch_put_call_ratio(tc)
    assert result is not None
    assert attempts["n"] == 2
    # tc 实际收到 1（重试探针）+ 扇出；query_calls 把失败的首探针也计为一次调用
    assert result["query_calls"] == tc.opt_daily_calls + 1
    assert result["queried_points"] == tc.opt_daily_calls
    assert result["query_calls"] <= orch._PCR_MAX_DAILY_QUERIES


def test_pcr_sample_note_shows_five_year_denominator():
    """读者面披露五年「观测/计划」分母（issue #35 B，窄化为五年分母）。"""
    from lib.render_markdown._v3 import _section_market_structure

    collection = {"dimensions": [], "market_structure": {}}
    pcr = {
        "ratio": 0.779, "percentile_5y": 20.4, "percentile_60d": 71.4,
        "current_date": "20260924", "history_days": 79, "history_sample_target": 80,
        "recent_observed_days": 42, "recent_days": 43, "source": "tushare.opt_daily",
    }
    rendered = _section_market_structure(collection, "600519", {"put_call_ratio": pcr})
    assert "五年均匀样本 79/80 点" in rendered
    assert "近期窗口 42/43 个交易日" in rendered


def test_pcr_sample_note_absent_for_old_snapshots():
    """旧快照（无 history_sample_target）保持整段抑制，不渲染半截计数。"""
    from lib.render_markdown._v3 import _section_market_structure

    collection = {"dimensions": [], "market_structure": {}}
    pcr = {
        "ratio": 0.779, "percentile_5y": 20.4, "percentile_60d": 71.4,
        "current_date": "20260924", "history_days": 79,
        "recent_observed_days": 42, "recent_days": 43, "source": "tushare.opt_daily",
    }
    rendered = _section_market_structure(collection, "600519", {"put_call_ratio": pcr})
    assert "五年均匀样本" not in rendered
    assert "近期窗口" not in rendered
