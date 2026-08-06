"""R12h 数据源降级链测试（全 mock，零活体网络）。

覆盖验收点（execution-plan §6.3）：
- 非 L2 维度首选源单发：首选成功 → 第二源「未尝试」；首选失败 → 按序降级
- mock tushare 失败 → 自动切非东财源（baostock）
- 全源失败 → 维度降级 + attempted sources 清单（不声称成功）
- 限流断言：连续东财调用时间戳间隔 ≥0.5s（mock 时钟）
- 指数退避重试：1s → 2s → 4s，max 3 次（mock sleep）
"""

from __future__ import annotations

import pytest

from lib.collector._base import _run_sources_cascade


class TestRunSourcesCascade:
    def _result(self, data=None, error=None):
        from lib.schema import SourceResult
        return SourceResult("s", data, "test", error=error)

    def test_first_success_skips_rest_as_not_attempted(self):
        """首选成功 → 后续源「未尝试」（data=None 且无 error）。"""
        results = _run_sources_cascade(
            [("a", lambda: [1]), ("b", lambda: [2]), ("c", lambda: [3])],
            "test",
        )
        assert len(results) == 3
        assert results[0].data == [1]
        assert results[1].data is None and results[1].error is None  # 未尝试
        assert results[2].data is None and results[2].error is None

    def test_first_failure_degrades_to_next(self):
        """首选失败 → 第二源启动；第二源成功 → 第三源未尝试。"""
        results = _run_sources_cascade(
            [("a", lambda: None), ("b", lambda: [2]), ("c", lambda: [3])],
            "test",
        )
        assert results[0].error is not None  # No data returned
        assert results[1].data == [2]
        assert results[2].data is None and results[2].error is None

    def test_all_fail_marks_each_attempted(self):
        """全源失败 → 每源都有错误（均为已尝试），无「未尝试」。"""
        results = _run_sources_cascade(
            [("a", lambda: None), ("b", lambda: None)],
            "test",
        )
        assert results[0].error is not None
        assert results[1].error is not None

    def test_exception_propagates_as_failure_then_falls_back(self):
        def _boom():
            raise ConnectionError("eastmoney blocked")

        results = _run_sources_cascade(
            [("a", _boom), ("b", lambda: [9])],
            "test",
        )
        assert results[0].error is not None
        assert results[1].data == [9]


class TestCollectKlineCascade:
    def test_tushare_failure_falls_back_to_baostock(self, monkeypatch):
        """mock tushare 失败 → 自动切非东财源（baostock）。"""
        from lib.collector import _orchestrate as orch

        monkeypatch.setenv("INVEST_KLINE_CACHE", "0")
        monkeypatch.setattr(orch.env, "is_tushare_available", lambda cfg: True)
        monkeypatch.setattr(orch.env, "baostock_kline_enabled", lambda: True)
        monkeypatch.setattr(orch.env, "tickflow_kline_enabled", lambda: False)
        monkeypatch.setattr(orch.env, "is_akshare_available", lambda: False)
        monkeypatch.setattr(orch, "akshare_push2_available", lambda: False)
        monkeypatch.setattr(orch, "_q_tushare_daily_qfq", lambda *a, **k: None)
        fake_rows = [{"trade_date": "2026-08-05", "close": 10.0}]
        monkeypatch.setattr(orch, "_q_baostock_kline", lambda *a, **k: fake_rows)

        res = orch.collect_kline("600000")
        assert res.get("data") is not None
        meta = res.get("_meta") or {}
        assert "baostock.kline" in str(meta.get("source", ""))
        assert res.get("status") in ("成功", "available", None) or meta

    def test_all_sources_fail_degraded_with_attempted_list(self, monkeypatch):
        """东财全断模拟 → 维度降级 + attempted sources 清单（不声称完整）。"""
        from lib.collector import _orchestrate as orch

        monkeypatch.setenv("INVEST_KLINE_CACHE", "0")
        monkeypatch.setattr(orch.env, "is_tushare_available", lambda cfg: True)
        monkeypatch.setattr(orch.env, "baostock_kline_enabled", lambda: True)
        monkeypatch.setattr(orch.env, "tickflow_kline_enabled", lambda: False)
        monkeypatch.setattr(orch.env, "is_akshare_available", lambda: False)
        monkeypatch.setattr(orch, "akshare_push2_available", lambda: False)
        monkeypatch.setattr(orch, "_q_tushare_daily_qfq", lambda *a, **k: None)
        monkeypatch.setattr(orch, "_q_baostock_kline", lambda *a, **k: None)

        res = orch.collect_kline("600000")
        assert res.get("data") is None
        meta = res.get("_meta") or {}
        sources = [s.get("source") for s in meta.get("all_sources", [])]
        assert "tushare.daily" in sources
        assert "baostock.kline" in sources  # 已尝试（失败），非「未尝试」
        assert res.get("error")  # 失败原因可追溯


class TestEastmoneyThrottle:
    @pytest.fixture(autouse=True)
    def _reset_throttle(self, monkeypatch):
        """重置模块级 _em_last_call，避免跨测试污染。"""
        from lib import proxy
        monkeypatch.setattr(proxy, "_em_last_call", float("-inf"))

    def test_throttle_enforces_min_interval(self, monkeypatch):
        """连续东财调用间隔 ≥0.5s（mock 时钟 + 捕获 sleep）。"""
        from lib import proxy

        clock = {"t": 0.0}
        sleeps: list[float] = []
        monkeypatch.setattr(proxy, "_em_now", lambda: clock["t"])
        monkeypatch.setattr(proxy.time, "sleep", lambda s: sleeps.append(s))
        monkeypatch.setattr(proxy, "EM_REQUEST_INTERVAL_SEC", 0.5)

        proxy.throttle_eastmoney()          # 首次：不等待
        assert sleeps == []
        clock["t"] += 0.1
        proxy.throttle_eastmoney()          # 间隔 0.1s < 0.5 → sleep 0.4
        assert sleeps == [0.4]
        clock["t"] += 0.6
        proxy.throttle_eastmoney()          # 间隔 0.6s ≥ 0.5 → 不等待
        assert sleeps == [0.4]

    def test_throttle_resets_last_call_after_wait(self, monkeypatch):
        from lib import proxy

        clock = {"t": 0.0}
        sleeps: list[float] = []
        monkeypatch.setattr(proxy, "_em_now", lambda: clock["t"])
        monkeypatch.setattr(proxy.time, "sleep", lambda s: sleeps.append(s))
        monkeypatch.setattr(proxy, "EM_REQUEST_INTERVAL_SEC", 0.5)

        proxy.throttle_eastmoney()
        clock["t"] += 0.0
        proxy.throttle_eastmoney()          # 无间隔 → sleep 0.5
        assert sleeps == [0.5]

    def test_akshare_direct_session_throttles(self, monkeypatch):
        """akshare_direct_session（EM 调用总闸口）接入限流。"""
        from lib import proxy
        from lib.proxy import akshare_direct_session

        calls: list[float] = []
        monkeypatch.setattr(proxy, "_em_now", lambda: 1.0)  # 固定时钟 → 首次不等待
        monkeypatch.setattr(proxy.time, "sleep", lambda s: calls.append(s))
        monkeypatch.setattr(proxy, "EM_REQUEST_INTERVAL_SEC", 0.5)
        monkeypatch.setattr(proxy, "_direct_scope", lambda **k: _null_ctx())

        with akshare_direct_session():
            pass
        assert calls == []  # 首次不等待

        # 第二次进入（时钟不前进）→ 强制 sleep ≥0.5
        with akshare_direct_session():
            pass
        assert calls and calls[0] >= 0.5


def _null_ctx():
    from contextlib import nullcontext
    return nullcontext()


class TestEmRequestWithRetry:
    def test_retries_exponential_backoff_then_succeeds(self, monkeypatch):
        """失败 2 次后成功 → sleep 1s + 2s（指数退避）。"""
        from lib import proxy

        sleeps: list[float] = []
        monkeypatch.setattr(proxy.time, "sleep", lambda s: sleeps.append(s))
        attempts = {"n": 0}

        def _flaky():
            attempts["n"] += 1
            if attempts["n"] < 3:
                raise ConnectionError("transient")
            return "ok"

        assert proxy.em_request_with_retry(_flaky, retries=3) == "ok"
        assert attempts["n"] == 3
        assert sleeps == [1.0, 2.0]

    def test_exhausts_retries_then_raises(self, monkeypatch):
        from lib import proxy

        sleeps: list[float] = []
        monkeypatch.setattr(proxy.time, "sleep", lambda s: sleeps.append(s))

        def _always_fail():
            raise ConnectionError("blocked")

        import pytest
        with pytest.raises(ConnectionError):
            proxy.em_request_with_retry(_always_fail, retries=3)
        assert sleeps == [1.0, 2.0, 4.0]  # max 3 次退避

    def test_success_first_try_no_sleep(self, monkeypatch):
        from lib import proxy

        sleeps: list[float] = []
        monkeypatch.setattr(proxy.time, "sleep", lambda s: sleeps.append(s))
        assert proxy.em_request_with_retry(lambda: "ok", retries=3) == "ok"
        assert sleeps == []
