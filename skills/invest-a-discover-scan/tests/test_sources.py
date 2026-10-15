"""A 股数据层（sources.py）—— 交易日历降级口径（review #6，2026-10-14）。

⚠️ `latest_trade_date()` 的估算回退必须用**沪市当日**（dates.shanghai_today）：
该值是 daily_basic 的查询键，宿主本地时区日期会查错交易日——宿主在北美时
（美东晚间 = 沪市上午）本地日期落后一天，扫描静默落在上一交易日；宿主在
沪市以东则可能查到非交易日、空返回 exit 3。
"""
from __future__ import annotations

import sys
import types

import pytest

import sources


def _fake_calendar(monkeypatch, *, days=None, error=None):
    fake = types.ModuleType("lib.trade_cal")
    if error is not None:
        def boom(_n):
            raise error
        fake.last_trade_dates = boom
    else:
        fake.last_trade_dates = lambda _n: list(days or [])
    monkeypatch.setitem(sys.modules, "lib.trade_cal", fake)


def test_fallback_uses_shanghai_today_not_host_tz(monkeypatch):
    import dates as dates_mod

    _fake_calendar(monkeypatch, error=RuntimeError("calendar down"))
    monkeypatch.setattr(dates_mod, "shanghai_today", lambda: "20260930")
    sources.reset_warnings()
    assert sources.latest_trade_date() == "20260930"
    assert any("退化为沪市当日估算" in w for w in sources.warnings), sources.warnings


def test_empty_calendar_result_also_warns_and_estimates(monkeypatch):
    import dates as dates_mod

    _fake_calendar(monkeypatch, days=[])
    monkeypatch.setattr(dates_mod, "shanghai_today", lambda: "20260930")
    sources.reset_warnings()
    assert sources.latest_trade_date() == "20260930"
    assert any("退化为沪市当日估算" in w for w in sources.warnings), sources.warnings


def test_calendar_hit_is_used_without_warning(monkeypatch):
    _fake_calendar(monkeypatch, days=["20260929"])
    sources.reset_warnings()
    assert sources.latest_trade_date() == "20260929"
    assert sources.warnings == []


@pytest.mark.parametrize("available", ["20261015", "20261014", "20261013", None])
def test_latest_daily_basic_uses_bounded_published_session(monkeypatch, available):
    import pandas as pd

    days = ["20261015", "20261014", "20261013"]
    _fake_calendar(monkeypatch, days=days)
    monkeypatch.setattr(sources, "latest_trade_date", lambda: days[0])
    calls = []

    def query(api, **params):
        assert api == "daily_basic"
        day = params["trade_date"]
        calls.append(day)
        return pd.DataFrame([{"ts_code": "600000.SH", "trade_date": day, "pe_ttm": 7.0}]
                            if day == available else [])

    monkeypatch.setattr(sources, "client", lambda: types.SimpleNamespace(query=query))
    sources.reset_warnings()
    if available is None:
        with pytest.raises(RuntimeError, match="20261013"):
            sources.fetch_latest_daily_basic()
        assert calls == days
    else:
        day, rows = sources.fetch_latest_daily_basic()
        assert day == available and rows[0]["trade_date"] == available
        assert calls == days[:days.index(available) + 1]
        if available != days[0]:
            assert any(available in w for w in sources.warnings)
    assert sources.CALL_COUNT["daily_basic"] == len(calls)


def test_daily_basic_wrong_response_date_is_rejected(monkeypatch):
    _stub_client(monkeypatch, {"daily_basic": [{"ts_code": "600000.SH", "trade_date": "20261014"}]})
    with pytest.raises(RuntimeError, match="日期"):
        sources.fetch_daily_basic("20261015")


def test_daily_basic_query_error_is_not_hidden_by_backtracking(monkeypatch):
    calls = []
    monkeypatch.setattr(sources, "latest_trade_date", lambda: "20261015")

    def query(api, **params):
        calls.append(params["trade_date"])
        raise RuntimeError("permission denied")

    monkeypatch.setattr(sources, "client", lambda: types.SimpleNamespace(query=query))
    with pytest.raises(RuntimeError, match="permission denied"):
        sources.fetch_latest_daily_basic()
    assert calls == ["20261015"]


# ── 财务/预告取数持久缓存（review #13，2026-10-14） ─────────────────────────

class _FakeCache:
    """DataCache 最小替身（不触盘）。"""

    def __init__(self):
        self.rows: dict = {}

    def get(self, dimension, symbol, *, max_age_seconds=None):
        return self.rows.get((dimension, symbol))

    def set(self, dimension, symbol, value, *, ttl_seconds=None, source=None):
        self.rows[(dimension, symbol)] = value


def _stub_client(monkeypatch, rows_by_api):
    import pandas as pd

    calls: list[str] = []

    def query(api_name, **params):
        calls.append(api_name)
        return pd.DataFrame(rows_by_api.get(api_name, []))

    monkeypatch.setattr(sources, "client",
                        lambda: types.SimpleNamespace(query=query))
    return calls


def test_fina_indicator_second_lookup_hits_cache(monkeypatch):
    """同日（同查询窗）第二次调用不再发请求；命中不计调用量（限流诊断口径）。"""
    fake = _FakeCache()
    monkeypatch.setattr(sources, "_cache", lambda: fake)
    calls = _stub_client(monkeypatch, {"fina_indicator": [{"end_date": "20260630"}]})
    before = sources.CALL_COUNT["fina_indicator"]
    rows1 = sources.fetch_fina_indicator("600000.SH",
                                         start_date="20230101", end_date="20260930")
    rows2 = sources.fetch_fina_indicator("600000.SH",
                                         start_date="20230101", end_date="20260930")
    assert rows1 == rows2 != []
    assert calls == ["fina_indicator"], "第二次应命中缓存，不得再发请求"
    assert sources.CALL_COUNT["fina_indicator"] - before == 1, "缓存命中不计调用量"


def test_fina_indicator_empty_result_not_cached(monkeypatch):
    """空返回不入缓存（D6）：一次失败不得被固化成一天，下次调用必须重查。"""
    fake = _FakeCache()
    monkeypatch.setattr(sources, "_cache", lambda: fake)
    monkeypatch.setattr(sources, "_RETRY_SLEEP_SEC", 0.0)
    calls = _stub_client(monkeypatch, {})
    rows1 = sources.fetch_fina_indicator("600000.SH",
                                         start_date="20230101", end_date="20260930")
    rows2 = sources.fetch_fina_indicator("600000.SH",
                                         start_date="20230101", end_date="20260930")
    assert rows1 == [] and rows2 == []
    assert len(calls) == 4, f"空结果不得入缓存（重试 2×2 次查询）：{calls}"


def test_forecast_second_lookup_hits_cache(monkeypatch):
    fake = _FakeCache()
    monkeypatch.setattr(sources, "_cache", lambda: fake)
    calls = _stub_client(monkeypatch, {"forecast": [{"p_change_max": 30.0}]})
    before = sources.CALL_COUNT["forecast"]
    rows1 = sources.fetch_forecast("600000.SH")
    rows2 = sources.fetch_forecast("600000.SH")
    assert rows1 == rows2 != []
    assert calls == ["forecast"]
    assert sources.CALL_COUNT["forecast"] - before == 1
