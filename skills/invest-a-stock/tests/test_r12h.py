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
import requests

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

    def test_always_success_does_not_block_cascade_fallback(self):
        """review #8（第二轮）：always 源成功不得阻断降级链。

        quote 场景（tushare 失败 → 腾讯实时快照成功 → akshare K 线回退）：
        always 成功若标记链完成，后续 akshare 永不尝试——与 docstring
        「其成功/失败与降级链无关，保持纯级联语义」矛盾。
        """
        results = _run_sources_cascade(
            [("a", lambda: None),                  # 首选失败
             ("b", lambda: {"price": 10.0}),       # always 源成功
             ("c", lambda: [1, 2, 3])],            # 降级链源
            "test",
            always_attempt={"b"},
        )
        assert len(results) == 3
        assert results[0].error is not None          # a 失败
        assert results[1].data == {"price": 10.0}    # b always 成功
        assert results[2].data == [1, 2, 3]          # c 仍被尝试（不被 b 阻断）
        assert results[2].error is None

    def test_always_failure_still_degrades_chain(self):
        """always 源失败也不影响链（与成功对称）。"""
        results = _run_sources_cascade(
            [("a", lambda: None),
             ("b", lambda: None),   # always 失败
             ("c", lambda: [7])],
            "test",
            always_attempt={"b"},
        )
        assert results[1].error is not None
        assert results[2].data == [7]


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
                raise requests.exceptions.ConnectionError("transient")
            return "ok"

        assert proxy.em_request_with_retry(_flaky, retries=3) == "ok"
        assert attempts["n"] == 3
        assert sleeps == [1.0, 2.0]

    def test_exhausts_retries_then_raises(self, monkeypatch):
        from lib import proxy

        sleeps: list[float] = []
        monkeypatch.setattr(proxy.time, "sleep", lambda s: sleeps.append(s))

        def _always_fail():
            raise requests.exceptions.ConnectionError("blocked")

        import pytest
        with pytest.raises(requests.exceptions.ConnectionError):
            proxy.em_request_with_retry(_always_fail, retries=3)
        assert sleeps == [1.0, 2.0, 4.0]  # max 3 次退避

    def test_non_transient_error_raises_immediately(self, monkeypatch):
        """逻辑错误（ValueError）不在重试白名单内 → 立即上抛不重试。"""
        from lib import proxy

        sleeps: list[float] = []
        monkeypatch.setattr(proxy.time, "sleep", lambda s: sleeps.append(s))
        attempts = {"n": 0}

        def _boom():
            attempts["n"] += 1
            raise ValueError("bad arg")

        import pytest
        with pytest.raises(ValueError):
            proxy.em_request_with_retry(_boom, retries=3)
        assert attempts["n"] == 1
        assert sleeps == []

    def test_deadline_expired_no_further_retries(self, monkeypatch):
        """deadline 到期后立即上抛，不再退避（切断后台重试链）。"""
        from lib import proxy

        sleeps: list[float] = []
        monkeypatch.setattr(proxy.time, "sleep", lambda s: sleeps.append(s))
        monkeypatch.setattr(proxy, "_em_now", lambda: 100.0)  # 固定时钟

        def _always_fail():
            raise requests.exceptions.ConnectionError("blocked")

        import pytest
        with pytest.raises(requests.exceptions.ConnectionError):
            proxy.em_request_with_retry(_always_fail, retries=3, deadline=100.0)
        assert sleeps == []  # deadline 到期 → 0 次退避

    def test_success_first_try_no_sleep(self, monkeypatch):
        from lib import proxy

        sleeps: list[float] = []
        monkeypatch.setattr(proxy.time, "sleep", lambda s: sleeps.append(s))
        assert proxy.em_request_with_retry(lambda: "ok", retries=3) == "ok"
        assert sleeps == []


class TestValuationDailyBasicNormalization:
    """缺陷修复回归：tushare daily_basic 万元→亿元 + 降序行序 → 跨源校验 convergence。

    缺陷场景（code-review CONFIRMED）：Tushare daily_basic 返回最新在前（降序）且
    total_mv 单位为万元，而 schema 提取假定升序（取 data[-1]=最新）且与腾讯快照
    （亿元）无单位换算 → 正常运行时恒算 ~200% relative_diff_pct，每份报告误标
    divergence。原测试喂单元素同单位列表，顺序与单位问题被构造数据掩盖。
    """

    class _FakeDF:
        """df.to_dict('records') 兼容的最小假 DataFrame。"""

        def __init__(self, records):
            self._records = records
            self.empty = not records

        def to_dict(self, orient="records"):
            return self._records

    def _install_tushare(self, monkeypatch, records):
        """安装假 Tushare 客户端：daily_basic 返回「万元 + 最新在前（降序）」原始序。"""
        from lib.collector import _orchestrate as orch

        fake_df = self._FakeDF(records)

        class _FakeTC:
            def query(self, api, **kw):
                assert api == "daily_basic"
                return fake_df

        monkeypatch.setattr(orch.env, "is_tushare_available", lambda cfg: True)
        monkeypatch.setattr(orch, "_require_tushare", lambda: (None, _FakeTC()))

    def test_daily_basic_wan_to_yi_and_asc_order(self, monkeypatch):
        """万元 total_mv + 降序输入 → 亿元输出 + 升序（最新在尾）。"""
        from lib.collector import _orchestrate as orch

        self._install_tushare(monkeypatch, [
            {"trade_date": "20260805", "total_mv": 1520000.0, "pe_ttm": 15.3},  # 最新
            {"trade_date": "20260701", "total_mv": 1500000.0, "pe_ttm": 15.0},
        ])
        rows = orch._q_tushare_daily_basic("600000")
        assert rows is not None
        assert [r["trade_date"] for r in rows] == ["20260701", "20260805"]  # 升序
        assert rows[-1]["total_mv"] == pytest.approx(152.0)  # 最新行 万元→亿元
        assert rows[0]["total_mv"] == pytest.approx(150.0)

    def test_collect_valuation_cv_converges_after_normalization(self, monkeypatch):
        """万元/亿元混合源（真实生产序）→ 归一化后 convergence，不再误标 divergence。

        修复前：误取最旧行 1500000（万元） vs 腾讯 151.0（亿元）→ 199.98% → divergence；
        修复后：最新行 152.0 亿 vs 151.0 亿 → 0.66% → convergence。
        """
        from lib.collector import _orchestrate as orch

        self._install_tushare(monkeypatch, [
            {"trade_date": "20260805", "total_mv": 1520000.0, "pe_ttm": 15.3},  # 最新
            {"trade_date": "20260701", "total_mv": 1500000.0, "pe_ttm": 15.0},
        ])
        monkeypatch.setattr(
            orch, "_q_tencent_quote",
            lambda symbol: {"total_mv": 151.0, "pe_ratio": 15.2},  # 腾讯原生亿元
        )
        res = orch.collect_valuation("600000")
        meta = res.get("_meta") or {}
        assert meta.get("cross_validation") == "convergence"
        assert "源一致" in (meta.get("cross_validation_detail") or "")


class TestCascadeNotAttemptedNotFailure:
    """修复回归（code-review CONFIRMED）：cascade「未尝试」源不计为失败。

    修复前：SourceResult(data=None, error=None) 占位被 success() 判为失败 →
    健康级联运行（首选源成功、备用源未尝试）每个维度 status='partial' →
    summary.degraded≥5 → 每次健康采集都打印降级告警，真实降级无法区分。
    """

    def test_healthy_cascade_status_available(self):
        """首选成功 → 备用源「未尝试」（attempted=False）→ status='available'。"""
        from lib.schema import DimensionResult

        results = _run_sources_cascade(
            [("a", lambda: [1]), ("b", lambda: [2]), ("c", lambda: [3])],
            "test",
        )
        assert results[0].data == [1]
        assert results[1].attempted is False and results[1].error is None
        assert results[2].attempted is False and results[2].error is None
        dim = DimensionResult("test", results)
        assert dim.status == "available"  # 修复前: partial（未尝试被计为失败）
        assert dim.multi_source is False

    def test_real_degradation_still_partial(self):
        """首选源真实失败 → 仍标 partial（真实降级与健康运行可区分）。"""
        from lib.schema import DimensionResult

        results = _run_sources_cascade(
            [("a", lambda: None), ("b", lambda: [2]), ("c", lambda: [3])],
            "test",
        )
        assert results[0].attempted is True and results[0].error is not None
        assert results[1].attempted is True and results[1].data == [2]
        dim = DimensionResult("test", results)
        assert dim.status == "partial"


class TestHealthyCollectionNoDegraded:
    """端到端回归：8 维度健康采集 → 无降级告警（summary.degraded == 0）。"""

    def test_collect_all_healthy_no_degraded_warning(self, monkeypatch):
        from lib.collector import _DEFAULT_DIMS, collect_all
        from lib.collector import _orchestrate as orch

        monkeypatch.setenv("INVEST_KLINE_CACHE", "0")
        # tushare 健康；akshare/baostock/tickflow 不可用 → cascade 备用源「未尝试」
        monkeypatch.setattr(orch.env, "is_tushare_available", lambda cfg: True)
        monkeypatch.setattr(orch.env, "is_akshare_available", lambda: False)
        monkeypatch.setattr(orch, "akshare_push2_available", lambda: False)
        monkeypatch.setattr(orch.env, "baostock_kline_enabled", lambda: False)
        monkeypatch.setattr(orch.env, "tickflow_kline_enabled", lambda: False)
        monkeypatch.setattr(orch.env, "is_baostock_available", lambda: False)

        kline_rows = [{"trade_date": "2026-08-05", "close": 10.0}]
        monkeypatch.setattr(orch, "_q_tushare_basic",
                            lambda s: {"name": "测试股份", "industry": "测试行业"})
        monkeypatch.setattr(orch, "_q_tushare_financials", lambda s: kline_rows)
        monkeypatch.setattr(orch, "_quote_tushare_rows", lambda s: kline_rows)
        monkeypatch.setattr(orch, "_q_tencent_quote",
                            lambda s: {"price": 10.0, "change_pct": 1.2,
                                       "turnover_rate": 3.0, "pe_ratio": 15.0,
                                       "total_mv": 100.0})
        monkeypatch.setattr(orch, "_q_tushare_shareholders", lambda s: kline_rows)
        monkeypatch.setattr(orch, "_q_tushare_hsgt_top10", lambda s: kline_rows)
        monkeypatch.setattr(orch, "_q_tushare_daily_basic", lambda s: kline_rows)
        monkeypatch.setattr(orch, "_q_tencent_valuation_snapshot",
                            lambda s: {"pe_ttm": 15.0, "total_mv": 100.0})
        monkeypatch.setattr(orch, "_q_tushare_daily_qfq", lambda s, **k: kline_rows)
        monkeypatch.setattr(orch, "_q_tushare_holdertrade", lambda s: kline_rows)
        # 非采集挂载钩子去网络化（industry_peers / events）
        monkeypatch.setattr(orch, "attach_phase2_extras", lambda r, s: None)
        import lib.events as events_mod
        monkeypatch.setattr(events_mod, "attach_events",
                            lambda r, s, days=30: None)

        result = collect_all("600000", dims=list(_DEFAULT_DIMS))
        summary = result.get("summary") or {}
        assert summary.get("degraded", -1) == 0  # 修复前: ≥5（每次健康采集误报降级）
        assert summary.get("available", 0) == len(_DEFAULT_DIMS)
        for d in result.get("dimensions", []):
            assert d.get("status") == "available", d.get("dimension")


class TestQuoteTencentRealtimeIndependent:
    """修复回归（code-review CONFIRMED）：腾讯实时快照不依赖 tushare 成功。

    修复前：collect_quote 为 cascade，tushare 健康时腾讯永不尝试 → quote 维度
    失去实时字段（change_pct/turnover_rate/pe_ratio/total_mv）。
    """

    def test_tencent_attempted_and_merged_when_tushare_healthy(self, monkeypatch):
        from lib.collector import _orchestrate as orch

        monkeypatch.setattr(orch.env, "is_tushare_available", lambda cfg: True)
        monkeypatch.setattr(orch.env, "is_akshare_available", lambda: False)
        monkeypatch.setattr(orch, "akshare_push2_available", lambda: False)
        kline_rows = [{"trade_date": "2026-08-05", "close": 10.0, "pct_chg": 1.2}]
        monkeypatch.setattr(orch, "_quote_tushare_rows", lambda s: kline_rows)
        monkeypatch.setattr(
            orch, "_q_tencent_quote",
            lambda s: {"price": 10.12, "change_pct": 1.2, "turnover_rate": 3.1,
                       "pe_ratio": 15.2, "total_mv": 101.2})

        res = orch.collect_quote("600000")
        meta = res.get("_meta") or {}
        srcs = {s.get("source"): s for s in meta.get("all_sources", [])}
        assert srcs["tencent_finance"]["data_available"] is True  # 修复前: 未尝试
        assert res.get("status") == "available"
        data = res.get("data")
        assert isinstance(data, dict)  # 实时字段并入维度数据
        assert data.get("change_pct") == 1.2
        assert data.get("turnover_rate") == 3.1
        assert data.get("pe_ratio") == 15.2
        assert data.get("total_mv") == 101.2
        assert data["kline"] == kline_rows  # 10 日 K 线保留

    def test_tencent_failure_does_not_block_tushare(self, monkeypatch):
        from lib.collector import _orchestrate as orch

        monkeypatch.setattr(orch.env, "is_tushare_available", lambda cfg: True)
        monkeypatch.setattr(orch.env, "is_akshare_available", lambda: False)
        monkeypatch.setattr(orch, "akshare_push2_available", lambda: False)
        kline_rows = [{"trade_date": "2026-08-05", "close": 10.0}]
        monkeypatch.setattr(orch, "_quote_tushare_rows", lambda s: kline_rows)
        monkeypatch.setattr(orch, "_q_tencent_quote", lambda s: None)

        res = orch.collect_quote("600000")
        assert res.get("data") == kline_rows  # tushare 结果不受腾讯失败影响
        meta = res.get("_meta") or {}
        srcs = {s.get("source"): s for s in meta.get("all_sources", [])}
        assert srcs["tencent_finance"]["data_available"] is False
        assert srcs["tencent_finance"].get("error")  # 失败原因可追溯


class TestHsgtRunCacheResetPerTest:
    """hsgt_top10 run 级缓存必须每测试重置（conftest autouse fixture）——
    修复前仅 test_v013_phase1 局部重置，test_r12h 写入的假行会跨测试/
    跨文件串味（code-review）。"""

    def test_a_populates_hsgt_cache(self, monkeypatch):
        from lib.collector import _orchestrate as orch

        monkeypatch.setattr(orch, "_q_tushare_hsgt_top10",
                            lambda s: [{"trade_date": "20260813", "net_mf_amount": 1.0}])
        assert orch._hsgt_top10_cached("600000") is not None
        assert orch._hsgt_top10_cache  # 本测试内已写入

    def test_b_cache_empty_at_test_start(self):
        from lib.collector import _orchestrate as orch

        assert orch._hsgt_top10_cache == {}
        assert orch._hsgt_top10_cache_day == ""
