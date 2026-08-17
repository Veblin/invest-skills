"""Collector 层 6 项 code-review CONFIRMED 缺陷修复回归测试（全 mock，零活体网络）。

修复清单：
1. PCR partial 标志：降采样是设计内的，partial 只在采样本身失败/缺失时置 True
   （此前 raw_days > len(sampled) 在 5 年窗口下恒 True → 报告恒显示「历史样本不足」）。
2. margin akshare 降级路径 change_pct：与 Tushare 主路径同窗口口径（最近 15 交易日
   两端），此前取全历史首尾（约 2 年）→ 同字段两种窗口语义。
3. qfq fallback 最新日 raw：标记 has_qfq_gap，除权日时不再让假跳变静默进入
   data[-1] 连续性消费者（MA20 偏离/10 日趋势）。
4. management_hold run 级缓存：cninfo 全市场接口（2 calls/符号）同 run 内只取一次。
5. cascade 单源 deadline：挂起的首选源受控超时（此前无 deadline，按 socket 30s
   串行阻塞）。
6. 腾讯行情北交所（4/8/920 前缀）明确跳过：此前误路由到 sh920xxx / sz8xxxxx
   （sz 前缀可能命中旧三板返回别家公司报价）。
"""

from __future__ import annotations

import time

import pytest


def _null_ctx():
    from contextlib import nullcontext
    return nullcontext()


# ---------- 缺陷 1：PCR partial 标志 ----------


class TestPcrPartialFlag:
    @staticmethod
    def _fake_tc(cal: list[str], fail_dates: set[str] | None = None):
        """opt_basic + trade_cal + opt_daily 假客户端；fail_dates 内查询返回空表。"""
        import pandas as pd

        fail_dates = fail_dates or set()

        class FakeTC:
            def query(self, api, **kw):
                if api == "opt_basic":
                    return pd.DataFrame([
                        {"ts_code": "10004567.SH", "name": "50ETF购2601", "call_put": "C"},
                        {"ts_code": "10004568.SH", "name": "50ETF沽2601", "call_put": "P"},
                    ])
                if api == "trade_cal":
                    return pd.DataFrame({"cal_date": cal})
                if api == "opt_daily":
                    td = str(kw.get("trade_date") or "")
                    if td in fail_dates:
                        return pd.DataFrame()
                    return pd.DataFrame({"ts_code": ["10004567.SH", "10004568.SH"],
                                         "vol": [100.0, 50.0]})  # put/call = 0.5
                return pd.DataFrame()

        return FakeTC()

    @staticmethod
    def _five_year_cal() -> list[str]:
        """~5 年（1230 交易日）日历，> 采样上限 80 → 必然触发降采样。"""
        import pandas as pd

        dates = pd.bdate_range(end=pd.Timestamp.now().normalize(), periods=1230)
        return [d.strftime("%Y%m%d") for d in dates]

    def test_pcr_sampling_success_partial_false(self):
        """5 年窗口降采样且全部采样日查询成功 → partial=False（修复点）。

        修复前：raw_days(1230) > len(sampled)(~80) 恒 True → partial 永久 true →
        报告恒显示「历史样本不足」警告。
        """
        from lib.collector._orchestrate import _ms_fetch_put_call_ratio

        cal = self._five_year_cal()
        r = _ms_fetch_put_call_ratio(self._fake_tc(cal))
        assert r is not None
        assert r["sampled"] is True            # 采样确实发生（5 年 > 80 点上限）
        assert r["partial"] is False           # 修复点：采样成功 → 非 partial
        assert r["current_date"] == cal[-1]
        assert r["ratio"] == 0.5

    def test_pcr_sample_query_missing_partial_true(self):
        """部分采样日查询失败（采样本身缺失）→ partial=True。"""
        from lib.collector._orchestrate import (
            _PCR_MAX_DAILY_QUERIES, _ms_fetch_put_call_ratio,
            _ms_subsample_trade_dates,
        )

        cal = self._five_year_cal()
        sampled = _ms_subsample_trade_dates(cal, _PCR_MAX_DAILY_QUERIES)
        fail_dates = set(sampled[::7])  # 每 7 个采样日失败 → 实得 < 计划
        r = _ms_fetch_put_call_ratio(self._fake_tc(cal, fail_dates))
        assert r is not None
        assert r["sampled"] is True
        assert r["partial"] is True            # 实得采样点数 < 计划点数
        assert r["history_days"] < len(sampled)


# ---------- R-14：PCR 探针重试/复用（review 二轮） ----------


class TestPcrProbeRetry:
    """F1-5 探针：单次超时不得抹掉整个 PCR 维度；探针结果复用不重复取数。"""

    @staticmethod
    def _counting_fake_tc(cal: list[str]):
        import pandas as pd

        class FakeTC:
            def __init__(self, cal):
                self.cal = cal
                self.opt_daily_calls = 0

            def query(self, api, **kw):
                if api == "opt_basic":
                    return pd.DataFrame([
                        {"ts_code": "10004567.SH", "name": "50ETF购2601", "call_put": "C"},
                        {"ts_code": "10004568.SH", "name": "50ETF沽2601", "call_put": "P"},
                    ])
                if api == "trade_cal":
                    return pd.DataFrame({"cal_date": self.cal})
                if api == "opt_daily":
                    self.opt_daily_calls += 1
                    return pd.DataFrame({"ts_code": ["10004567.SH", "10004568.SH"],
                                         "vol": [100.0, 50.0]})  # put/call = 0.5
                return pd.DataFrame()

        return FakeTC(cal)

    @staticmethod
    def _short_cal(n: int = 5) -> list[str]:
        import pandas as pd

        dates = pd.bdate_range(end=pd.Timestamp.now().normalize(), periods=n)
        return [d.strftime("%Y%m%d") for d in dates]

    def test_probe_retry_then_result_reuse(self, monkeypatch):
        """首次探针超时 → 重试成功 → 不整体降级；探针结果复用，
        最新日不重复取（opt_daily 总调用 == fetch_dates 数，修复前多 1 次）。"""
        from lib.collector import _orchestrate
        from lib.collector._orchestrate import (
            _PCR_MAX_DAILY_QUERIES, _ms_subsample_trade_dates,
        )

        cal = self._short_cal()
        fake = self._counting_fake_tc(cal)
        probe_states = iter([None, "ok"])

        def _fake_run(fn, timeout, label):
            if label.startswith("opt_daily-probe"):
                state = next(probe_states, "ok")
                return None if state is None else fn()
            return fn()

        monkeypatch.setattr(_orchestrate, "_run_with_timeout", _fake_run)
        r = _orchestrate._ms_fetch_put_call_ratio(fake)
        assert r is not None
        assert r["ratio"] == 0.5
        fetch_dates = sorted(
            set(_ms_subsample_trade_dates(cal, _PCR_MAX_DAILY_QUERIES))
        )
        # 探针日 1 次（probe2 成功那次）+ 其余 N-1 日各 1 次
        assert fake.opt_daily_calls == len(fetch_dates)

    def test_probe_double_failure_drops_dimension(self, monkeypatch):
        """两次探针均失败 → 整体降级 return None，不逐日空转。"""
        from lib.collector import _orchestrate

        cal = self._short_cal()
        fake = self._counting_fake_tc(cal)

        def _fake_run(fn, timeout, label):
            if label.startswith("opt_daily-probe"):
                return None
            return fn()

        monkeypatch.setattr(_orchestrate, "_run_with_timeout", _fake_run)
        r = _orchestrate._ms_fetch_put_call_ratio(fake)
        assert r is None
        assert fake.opt_daily_calls == 0  # 未进入逐日取数


# ---------- 缺陷 2：margin 降级路径 15 日窗口 ----------


class TestMarginFallbackWindow:
    class _FakeDF:
        """df 最小兼容：columns / empty / sort_values / to_dict。"""

        def __init__(self, records, columns):
            self._records = records
            self._cols = columns
            self.empty = not records

        @property
        def columns(self):
            return self._cols

        def sort_values(self, by, **kw):
            return self

        def to_dict(self, orient="records"):
            return self._records

    @staticmethod
    def _margin_records(n: int) -> list[dict]:
        """n 行两融记录：融资余额 = 100 + i（第 i 天，i 从 0 起），日期连续。"""
        import pandas as pd

        dates = pd.bdate_range(end=pd.Timestamp("2026-08-05"), periods=n)
        return [
            {"交易日期": d.strftime("%Y%m%d"), "融资余额": 100.0 + i}
            for i, d in enumerate(dates)
        ]

    def _call_fallback(self, monkeypatch, records):
        """空 margin_detail → akshare 降级路径，返回 result dict。"""
        from lib.collector import _orchestrate as orch

        class FakeTC:
            def query(self, api, **kw):
                assert api == "margin_detail"
                return self._empty_df()

            @staticmethod
            def _empty_df():
                import pandas as pd
                return pd.DataFrame()

        monkeypatch.setattr(
            "lib.market_pulse.fetch_margin_account_info",
            lambda: self._FakeDF(records, ["交易日期", "融资余额"]),
        )
        return orch._ms_fetch_margin(FakeTC(), "600176")

    def test_fallback_change_pct_uses_15_day_window(self, monkeypatch):
        """降级路径 change_pct = 最近 15 交易日两端，而非全历史首尾。"""
        records = self._margin_records(30)  # 30 行：全历史窗口 ≠ 15 日窗口
        r = self._call_fallback(monkeypatch, records)
        assert r is not None
        assert r["source"] == "akshare.margin_account"

        window = records[-15:]
        expected = (window[-1]["融资余额"] - window[0]["融资余额"]) \
            / window[0]["融资余额"] * 100
        full_history = (records[-1]["融资余额"] - records[0]["融资余额"]) \
            / records[0]["融资余额"] * 100
        assert r["change_pct"] == pytest.approx(round(expected, 2))
        assert r["change_pct"] != pytest.approx(round(full_history, 2))  # 修复点
        assert r["records"] == records[-10:]

    def test_fallback_short_history_uses_all_rows(self, monkeypatch):
        """不足 15 行时窗口退化为全量（与主路径 len<2 拒绝语义一致）。"""
        records = self._margin_records(5)
        r = self._call_fallback(monkeypatch, records)
        assert r is not None
        expected = (records[-1]["融资余额"] - records[0]["融资余额"]) \
            / records[0]["融资余额"] * 100
        assert r["change_pct"] == pytest.approx(round(expected, 2))


# ---------- 缺陷 3：qfq fallback 最新日标记 ----------


class TestQfqFallbackGapMark:
    @staticmethod
    def _rows():
        return [
            {"trade_date": "20260710", "open": 10.0, "high": 10.5, "low": 9.5, "close": 10.0},
            {"trade_date": "20260711", "open": 11.0, "high": 11.5, "low": 10.5, "close": 11.0},
            {"trade_date": "20260712", "open": 12.0, "high": 12.5, "low": 11.5, "close": 12.0},
        ]

    def test_fallback_marks_newest_raw_day_gap(self):
        """最新日缺 adj_factor（盘中常态）→ fallback 路径最新 raw 日标记 has_qfq_gap。

        修复点：最新日若为除权日，D-1(qfq) → D(raw) 边界是假跳变（10% 分红看似
        跌 10%）；标记后连续性消费者（MA20 偏离/10 日趋势）可显式排除，不让
        假跳变静默进入 data[-1]。
        """
        from lib.collector._orchestrate import _apply_qfq_with_newest_raw_fallback

        rows = self._rows()
        factors = {"20260710": 1.2, "20260711": 1.1}  # 最新日缺失（盘中常态）
        out = _apply_qfq_with_newest_raw_fallback(rows, factors)
        assert out is not None
        assert out[-1]["trade_date"] == "20260712"
        assert out[-1]["has_qfq_gap"] is True    # 修复点：最新 raw 日被标注
        assert out[-1]["close"] == 12.0          # raw 原样保留
        # 历史 qfq 段无标记（与最新日的不同标度是序列唯一的断裂点）
        assert all("has_qfq_gap" not in r for r in out[:-1])
        # 输入行不被突变（dict 拷贝标记）
        assert "has_qfq_gap" not in rows[-1]

    def test_ex_div_newest_day_boundary_signaled(self):
        """除权日场景：最新日 raw 收盘明显低于前一交易日 qfq（10% 分红）→ 有标记。"""
        from lib.collector._orchestrate import _apply_qfq_with_newest_raw_fallback

        rows = [
            {"trade_date": "20260710", "close": 10.0},
            {"trade_date": "20260711", "close": 11.0},
            {"trade_date": "20260712", "close": 9.9},   # 除权日：raw 跌 ~10%
        ]
        factors = {"20260710": 1.2, "20260711": 1.1}    # 最新日因子盘后才发布
        out = _apply_qfq_with_newest_raw_fallback(rows, factors)
        assert out is not None
        assert out[-1]["trade_date"] == "20260712"
        assert out[-1]["has_qfq_gap"] is True           # 修复点：假跳变被标注
        # 历史段锚定 20260711 的 qfq（连续），最新 raw 日不参与连续性口径
        r11 = [r for r in out if r["trade_date"] == "20260711"][0]
        assert r11["close"] == 11.0

    def test_full_factors_no_gap_marker(self):
        """全部因子就绪（盘后常态）→ 无 has_qfq_gap 标记。"""
        from lib.collector._orchestrate import _apply_qfq_with_newest_raw_fallback

        rows = self._rows()
        factors = {"20260710": 1.2, "20260711": 1.1, "20260712": 1.0}
        out = _apply_qfq_with_newest_raw_fallback(rows, factors)
        assert out is not None
        assert all("has_qfq_gap" not in r for r in out)


# ---------- 缺陷 4：management_hold run 级缓存 ----------


class TestManagementHoldRunCache:
    @staticmethod
    def _install(monkeypatch, df, calls):
        from lib.collector import _orchestrate as orch

        def fake_cninfo(symbol):
            calls["n"] += 1
            return df

        monkeypatch.setattr(orch, "_cninfo_hold_cache", {})
        monkeypatch.setattr(orch, "_cninfo_hold_cache_day", "")
        monkeypatch.setattr(orch, "_cninfo_hold_cache_today", lambda: "2026-08-06")
        monkeypatch.setattr(orch, "akshare_direct_session", lambda: _null_ctx())
        monkeypatch.setattr(orch.env, "is_akshare_available", lambda: True)
        monkeypatch.setattr("akshare.stock_hold_management_detail_cninfo", fake_cninfo)
        return orch._q_akshare_management_hold

    @staticmethod
    def _hold_df():
        import pandas as pd

        return pd.DataFrame({
            "证券代码": ["600176", "000001"],
            "董监高姓名": ["张三", "李四"],
            "变动数量": [1000.0, -500.0],
        })

    def test_second_call_in_same_run_no_refetch(self, monkeypatch):
        """同 run 第二次调用 → akshare 全市场接口仅取一次（每方向 1 call，共 2）。"""
        calls = {"n": 0}
        fn = self._install(monkeypatch, self._hold_df(), calls)

        r1 = fn("600176")
        assert calls["n"] == 2  # 首次：增持 + 减持 各 1 call
        r2 = fn("600176")
        assert calls["n"] == 2  # 修复点：第二次 0 call（此前 2 calls/次）
        assert r1 is not None and r2 is not None
        assert r1 == r2

    def test_cache_shared_across_symbols(self, monkeypatch):
        """同 run 不同 symbol → 复用同一份全市场数据（watchlist/compare 场景）。"""
        calls = {"n": 0}
        fn = self._install(monkeypatch, self._hold_df(), calls)

        r1 = fn("600176")
        r2 = fn("000001")
        assert calls["n"] == 2  # 修复点：N 标的只取一次全市场（此前 2 calls/标的）
        assert r1 is not None and r2 is not None
        assert any("600176" in r.get("holder_name", "") or True for r in r1)
        assert r1[0]["change_vol"] == 1000.0
        assert r2[0]["change_vol"] == -500.0

    def test_cache_expires_next_day(self, monkeypatch):
        """跨自然日 → 缓存失效重建（按日失效）。"""
        from lib.collector import _orchestrate as orch

        days = {"d": "2026-08-06"}
        calls = {"n": 0}
        fn = self._install(monkeypatch, self._hold_df(), calls)
        monkeypatch.setattr(orch, "_cninfo_hold_cache_today", lambda: days["d"])

        fn("600176")
        assert calls["n"] == 2
        days["d"] = "2026-08-07"
        fn("600176")
        assert calls["n"] == 4  # 跨日重建

    def test_timeout_not_cached_next_symbol_retries(self, monkeypatch):
        """review #14（第二轮）：超时/异常结果（None）不落缓存——
        否则整个 run 其余 symbol 复用 None（该方向数据全缺失且不重试）。"""
        from lib.collector import _orchestrate as orch

        calls = {"n": 0}
        fn = self._install(monkeypatch, self._hold_df(), calls)
        # 第一次调用：增持方向超时 → None；减持正常
        def fake_cninfo_timeout(symbol):
            calls["n"] += 1
            if symbol == "增持" and calls.get("timeout_done"):
                return self._hold_df()
            if symbol == "增持":
                calls["timeout_done"] = True
                return None
            return self._hold_df()

        monkeypatch.setattr("akshare.stock_hold_management_detail_cninfo",
                            fake_cninfo_timeout)

        r1 = fn("600176")  # 增持超时跳过，减持正常
        assert r1 is not None
        assert calls["n"] == 2
        # 缓存中不含增持方向（None 未落缓存）→ 第二次调用重试
        assert "增持" not in orch._cninfo_hold_cache
        r2 = fn("600176")
        assert calls["n"] == 3  # 只重试增持方向
        assert r2 is not None


# ---------- 缺陷 5：cascade 单源 deadline ----------


class TestCascadeSourceDeadline:
    def test_hung_source_returns_timeout_controlled(self):
        """挂起首选源 → 受控超时（非 socket 30s 裸等），链继续降级。"""
        from lib.collector._base import _run_sources_cascade

        t0 = time.monotonic()
        results = _run_sources_cascade(
            [("a", lambda: time.sleep(60)), ("b", lambda: [1])],
            "test",
            deadline_sec=0.2,
        )
        elapsed = time.monotonic() - t0
        assert elapsed < 2.0                      # 修复点：0.2s deadline 受控返回
        assert results[0].error is not None
        assert "timeout" in results[0].error      # 对齐 parallel 的 timeout 语义
        assert results[1].data == [1]             # 超时后链继续降级

    def test_timeout_error_message_contains_deadline(self):
        """timeout error 带 deadline 数值，可追溯。"""
        from lib.collector._base import _run_sources_cascade

        results = _run_sources_cascade(
            [("a", lambda: time.sleep(30))], "test", deadline_sec=0.3,
        )
        assert "timeout after 0.3s" in results[0].error

    def test_deadline_preserves_exception_message(self):
        """deadline 路径下异常消息不吞（与无 deadline 路径可追溯性一致）。"""
        from lib.collector._base import _run_sources_cascade

        def _boom():
            raise ConnectionError("eastmoney blocked")

        results = _run_sources_cascade(
            [("a", _boom), ("b", lambda: [9])], "test", deadline_sec=5,
        )
        assert "eastmoney blocked" in results[0].error
        assert results[1].data == [9]

    def test_deadline_zero_means_no_limit(self):
        """deadline_sec=0 → 不设限（与 _run_sources_parallel 语义一致）。"""
        from lib.collector._base import _run_sources_cascade

        results = _run_sources_cascade(
            [("a", lambda: [1]), ("b", lambda: [2])], "test", deadline_sec=0,
        )
        assert results[0].data == [1]
        assert results[1].data is None and results[1].error is None  # 未尝试


# ---------- 缺陷 6：腾讯行情北交所跳过 ----------


class TestTencentBjSkip:
    def test_bj_symbols_skipped_without_request(self, monkeypatch):
        """北交所代码 → 腾讯不发起请求、返回 None（标注不可得）。"""
        from lib.collector._sources import _q_tencent_quote

        requested: list[str] = []

        class _FakeSess:
            def get(self, url, timeout):
                requested.append(url)
                raise AssertionError("北交所代码不应发起腾讯请求")

        class _FakeCtx:
            def __enter__(self):
                return _FakeSess()

            def __exit__(self, *args):
                return False

        monkeypatch.setattr("lib.collector._sources.no_proxy_session",
                            lambda: _FakeCtx())

        for sym in ("920001", "830799", "430047", "920001.BJ"):
            assert _q_tencent_quote(sym) is None, sym
        assert requested == []  # 修复点：绝不对北交所代码发起请求（此前误路由）

    def test_sh_symbol_still_routes_sh_market(self, monkeypatch):
        """非北交所代码行为不变：600000 → sh600000。"""
        from lib.collector._sources import _q_tencent_quote

        captured: dict = {}

        class _FakeResp:
            status_code = 200
            text = "~".join(["0"] * 50)  # p[3]..p[45] 均可解析

        class _FakeSess:
            def get(self, url, timeout):
                captured["url"] = url
                return _FakeResp()

        class _FakeCtx:
            def __enter__(self):
                return _FakeSess()

            def __exit__(self, *args):
                return False

        monkeypatch.setattr("lib.collector._sources.no_proxy_session",
                            lambda: _FakeCtx())

        r = _q_tencent_quote("600000")
        assert captured["url"] == "http://qt.gtimg.cn/q=sh600000"
        assert r is not None and r["price"] == 0.0

    def test_qp_tencent_bj_annotates_unavailable(self):
        """查询参数字符串：北交所标注不请求，非北交所格式不变。"""
        from lib.collector._sources import _qp_tencent

        assert "北交所" in _qp_tencent("920001")
        assert "北交所" in _qp_tencent("830799")
        assert _qp_tencent("600000") == "qt.gtimg.cn/q=sh600000"
        assert _qp_tencent("000001") == "qt.gtimg.cn/q=sz000001"


# ---------- 缺陷 7：_ms_try_fetch 异常不泄漏（batch-test P1-1） ----------

class TestMsTryFetchExceptionNotLeaked:
    """异常分支必须写静态 unavailable_msg，不得把 str(exc) 写入 availability
    （否则渲染层输出「不可得：'str' object has no attribute 'get'」式裸异常文本）。"""

    def test_exception_writes_static_msg_not_exc(self):
        from lib.collector._orchestrate import _ms_set_unavailable, _ms_try_fetch

        def _boom():
            raise AttributeError("'str' object has no attribute 'get'")

        result: dict = {"availability": {}}
        _ms_try_fetch(
            result, "new_high_ratio", _boom,
            unavailable_msg="daily sample empty or insufficient",
        )
        status = result["availability"]["new_high_ratio"]
        assert status.startswith("unavailable:")
        assert "'str' object" not in status
        assert "daily sample empty or insufficient" in status

    def test_none_value_also_uses_static_msg(self):
        from lib.collector._orchestrate import _ms_try_fetch

        result: dict = {"availability": {}}
        _ms_try_fetch(
            result, "pmi", lambda: None,
            unavailable_msg="akshare macro_china_pmi unavailable",
        )
        assert result["availability"]["pmi"] == (
            "unavailable: akshare macro_china_pmi unavailable")


# ---------- 缺陷 8：new_high_ratio _map_parallel 双包装（600206 batch-test 实证） ----------

class TestNewHighRatioPanel:
    """_fetch_daily_panel_row 返回 (ts_code, records) 元组而 _map_parallel 契约
    也返回 (item, result)——双重包装使 panel 值为元组，rows[0]=str(ts_code)，
    _ms_new_high_ratio_from_panel 对其 .get("close") → AttributeError
    （600206 实证：market_structure new_high_ratio fetch failed）。"""

    def test_fetch_new_high_ratio_computes_ratio(self):
        from lib.collector._orchestrate import _ms_fetch_new_high_ratio

        import pandas as pd

        class _FakeTC:
            """stock_basic 全量 + daily 单标的 5 行样本（closes/highs 精心构造）。"""

            def __init__(self):
                self.daily_calls = 0

            def query(self, api, **kw):
                if api == "stock_basic":
                    return pd.DataFrame(
                        {"ts_code": ["000001.SZ", "000002.SZ", "000003.SZ"]})
                if api == "daily":
                    self.daily_calls += 1
                    code = kw["ts_code"]
                    dates = ["20260805", "20260806", "20260807", "20260810", "20260811"]
                    if code == "000001.SZ":
                        # 收盘创新高：14 >= max(前 4 日高=13.5)
                        close, high = [10, 11, 12, 13, 14], [10.5, 11.5, 12.5, 13.5, 15.0]
                    elif code == "000002.SZ":
                        # 非新高：16 < max(前 4 日高=21)
                        close, high = [20, 19, 18, 17, 16], [21, 20, 19, 18, 17]
                    else:
                        # 平历史高（>=）算新高：5 >= 5
                        close, high = [5, 5, 5, 5, 5], [5, 5, 5, 5, 5]
                    return pd.DataFrame({
                        "trade_date": dates, "close": close, "high": high,
                    })
                raise AssertionError(f"unexpected api: {api}")

        tc = _FakeTC()
        result = _ms_fetch_new_high_ratio(tc)
        assert result is not None
        assert result["sample_size"] == 3
        assert result["ratio_pct"] == 66.67  # 2/3 创新高
        assert result["sample_requested"] == 3

    def test_empty_daily_df_filtered_from_panel(self):
        """审查 finding #4 守卫 1：daily 返回空 df → _fetch_daily_panel_row
        返回 None → 面板 `if records:` 过滤，样本不含该标的（停牌/权限不足
        场景），不崩溃、比率基于剩余样本。"""
        from lib.collector._orchestrate import _ms_fetch_new_high_ratio

        import pandas as pd

        class _FakeTC:
            def query(self, api, **kw):
                if api == "stock_basic":
                    return pd.DataFrame(
                        {"ts_code": ["000001.SZ", "000002.SZ", "000003.SZ"]})
                if api == "daily":
                    code = kw["ts_code"]
                    dates = ["20260805", "20260806", "20260807", "20260810", "20260811"]
                    if code == "000003.SZ":
                        # 停牌/权限不足：空 df
                        return pd.DataFrame()
                    # 两只均创新高
                    close, high = [10, 11, 12, 13, 14], [10.5, 11.5, 12.5, 13.5, 15.0]
                    return pd.DataFrame({
                        "trade_date": dates, "close": close, "high": high,
                    })
                raise AssertionError(f"unexpected api: {api}")

        result = _ms_fetch_new_high_ratio(_FakeTC())
        assert result is not None
        assert result["sample_size"] == 2  # 空 df 标的被过滤
        assert result["ratio_pct"] == 100.0  # 2/2 创新高
        assert result["sample_requested"] == 3

    def test_panel_error_placeholder_filtered(self, caplog):
        """审查 finding #4 守卫 2：_fetch_daily_panel_row 异常逃出超时包装
        （如 df 缺 trade_date 列 → sort_values KeyError）→ _map_parallel
        on_error 记日志并返回 (item, None) 占位 → 面板 `if records:` 过滤
        None，不崩溃、返回 None（无有效样本）。"""
        from lib.collector._orchestrate import _ms_fetch_new_high_ratio

        import pandas as pd

        class _FakeTC:
            def query(self, api, **kw):
                if api == "stock_basic":
                    return pd.DataFrame(
                        {"ts_code": ["000001.SZ", "000002.SZ", "000003.SZ"]})
                if api == "daily":
                    # 缺 trade_date 列：sort_values 在超时包装外抛 KeyError
                    return pd.DataFrame({"close": [1.0], "high": [1.0]})
                raise AssertionError(f"unexpected api: {api}")

        import logging

        with caplog.at_level(logging.WARNING, logger="lib.collector._orchestrate"):
            result = _ms_fetch_new_high_ratio(_FakeTC())
        assert result is None
        assert "new_high_ratio daily fetch failed" in caplog.text


# ---------- code-review 清理：industry PE 空名守卫（P0 静默数据错误） ----------


class TestIndustryPeEmptyNameGuard:
    @staticmethod
    def _fake_akshare():
        import pandas as pd

        class _FakeAk:
            @staticmethod
            def stock_board_industry_pe_ratio_cninfo():
                return pd.DataFrame([{
                    "行业名称": "银行", "市盈率中位数": 5.0,
                    "市盈率平均值": 5.5, "公司数量": 42,
                }])

        return _FakeAk()

    def test_empty_industry_name_returns_none(self, monkeypatch):
        """行业字段缺失（预取失败 → 空名）必须返回 None。

        修复前：`str.contains("")` 全表匹配 → matched=整个巨潮 PE 表 →
        matched.iloc[0] 把首行（如「银行 5.5x」）静默当作本股行业 PE。
        """
        import sys

        from lib.collector import _sources as src

        monkeypatch.setitem(sys.modules, "akshare", self._fake_akshare())
        monkeypatch.setattr(src, "akshare_direct_session", lambda: _null_ctx())
        monkeypatch.setattr(src.env, "is_akshare_available", lambda: True)
        monkeypatch.setattr(src, "akshare_push2_available", lambda: True)
        monkeypatch.setattr(src, "_q_akshare_basic", lambda s: None)  # 预取失败
        assert src._q_akshare_industry_pe("600176", industry_name="") is None

    def test_valid_industry_name_still_matches(self, monkeypatch):
        """守卫不破坏正常路径：非空名照常匹配。"""
        import sys

        from lib.collector import _sources as src

        monkeypatch.setitem(sys.modules, "akshare", self._fake_akshare())
        monkeypatch.setattr(src, "akshare_direct_session", lambda: _null_ctx())
        monkeypatch.setattr(src.env, "is_akshare_available", lambda: True)
        monkeypatch.setattr(src, "akshare_push2_available", lambda: True)
        result = src._q_akshare_industry_pe("600176", industry_name="银行")
        assert result is not None
        assert result["industry_name"] == "银行"
        assert result["industry_pe_median"] == 5.0


# ---------- code-review 清理 D3：sw_index 单遍拉表（6 次 API → 3 次） ----------


class TestMsLookupAkshareSwCodeSingleLoad:
    def test_loaders_each_called_once_and_substring_fallback(self, monkeypatch):
        import sys

        import pandas as pd

        from lib.collector import _orchestrate as orch

        calls = {"third": 0, "second": 0, "first": 0}

        def _table(rows):
            return pd.DataFrame([{"行业名称": n, "行业代码": c} for n, c in rows])

        class _FakeAk:
            @staticmethod
            def sw_index_third_info():
                calls["third"] += 1
                return _table([("电子", "801080")])

            @staticmethod
            def sw_index_second_info():
                calls["second"] += 1
                return _table([("半导体", "801081")])

            @staticmethod
            def sw_index_first_info():
                calls["first"] += 1
                return _table([("电子元件", "801083")])

        monkeypatch.setitem(sys.modules, "akshare", _FakeAk())
        monkeypatch.setattr(orch, "akshare_direct_session", lambda: _null_ctx())
        monkeypatch.setattr(orch.env, "is_akshare_available", lambda: True)

        # exact 命中（third 表首行）→ 短路返回：仅 third 拉 1 次
        # （code-review：修复前先拉全 3 表，常见精确命中场景 1 次调用退化为 3 次）
        assert orch._ms_lookup_akshare_sw_code("电子") == "801080"
        assert calls == {"third": 1, "second": 0, "first": 0}

        # exact 命中（second 表）→ 短路：third 拉 1 次无命中，second 命中
        assert orch._ms_lookup_akshare_sw_code("半导体") == "801081"
        assert calls == {"third": 2, "second": 1, "first": 0}

        # substring 命中：exact 全 miss → 复用本调用已拉表做 substring（各多 1 次）
        assert orch._ms_lookup_akshare_sw_code("元件") == "801083"
        assert calls == {"third": 3, "second": 2, "first": 1}

        # 空名直接返回 None（不拉表）
        assert orch._ms_lookup_akshare_sw_code("  ") is None
        assert calls == {"third": 3, "second": 2, "first": 1}

    def test_loader_failure_does_not_discard_other_table_matches(self, monkeypatch):
        """表 2 拉取抛异常：跳过继续，其余表仍可匹配（修复前 blanket except
        让表 1 已找到的匹配整体返回 None，行业指数数据静默缺失）。"""
        import sys

        import pandas as pd

        from lib.collector import _orchestrate as orch

        calls = {"third": 0, "second": 0, "first": 0}

        def _table(rows):
            return pd.DataFrame([{"行业名称": n, "行业代码": c} for n, c in rows])

        class _FakeAk:
            @staticmethod
            def sw_index_third_info():
                calls["third"] += 1
                return _table([("电子", "801080")])

            @staticmethod
            def sw_index_second_info():
                calls["second"] += 1
                raise RuntimeError("rate limited")

            @staticmethod
            def sw_index_first_info():
                calls["first"] += 1
                return _table([("电子元件", "801083")])

        monkeypatch.setitem(sys.modules, "akshare", _FakeAk())
        monkeypatch.setattr(orch, "akshare_direct_session", lambda: _null_ctx())
        monkeypatch.setattr(orch.env, "is_akshare_available", lambda: True)

        # exact 命中在表 2 之前 → 表 2 根本不被调用
        assert orch._ms_lookup_akshare_sw_code("电子") == "801080"
        assert calls == {"third": 1, "second": 0, "first": 0}

        # 表 2 抛异常 → 跳过，表 1/3 substring 扫描仍命中
        assert orch._ms_lookup_akshare_sw_code("元件") == "801083"
        assert calls == {"third": 2, "second": 1, "first": 1}
