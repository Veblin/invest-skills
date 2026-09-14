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


# ---------- R2/T9-1′：PCR 不可得原因分类（D-H=H1 验收） ----------


class TestPcrUnavailableReason:
    """降级标注须显式且**区分原因**——权限不足要给出权限提示。

    修复前三种原因共用一句静态文案
    「opt_daily empty, no 50ETF options, or permission denied (5000 pts)」：
    用户无法判断该去补积分、该重试、还是该接受空数据。
    约束：原因文本会**原样**进报告（`unavailable: …` → 「（不可得：…）」），
    故不得回显底层异常文本（R12h，见 `_ms_try_fetch` 注释）。
    """

    @staticmethod
    def _reason(tc):
        from lib.collector._orchestrate import _ms_pcr_unavailable_reason
        return _ms_pcr_unavailable_reason(tc)

    def test_permission_denied_gives_points_hint(self):
        """走**私有集合 fallback** 分支（假 client 无公开方法）。"""
        class TC:
            last_error = "无接口权限（本会话已确认）: opt_daily"
            _permission_denied_apis = {"opt_daily"}
        r = self._reason(TC())
        # 门槛值从积分表推导，不把配置值钉进测试（改配置即误报）
        from lib.tushare_client import api_min_points

        assert "权限" in r and str(api_min_points("opt_daily")) in r, f"权限提示缺失: {r!r}"

    def test_permission_denied_via_public_client_method(self):
        """走**公开方法**分支（生产真 client 的路径，此前无覆盖）。"""
        class TC:
            last_error = ""
            def is_permission_denied(self, api):     # noqa: D102
                return api == "opt_daily"
        r = self._reason(TC())
        assert "权限" in r, f"公开方法分支未生效: {r!r}"

    def test_real_client_public_method_reflects_denied_set(self):
        """`TushareClient.is_permission_denied` 的语义（新增公开 API）。"""
        from lib.tushare_client import TushareClient

        client = TushareClient(token="dummy")
        assert client.is_permission_denied("opt_daily") is False
        client._permission_denied_apis.add("opt_daily")   # 模拟 query 判定后的记录
        assert client.is_permission_denied("opt_daily") is True
        assert client.is_permission_denied("daily") is False

    def test_missing_token_is_named(self):
        class TC:
            last_error = "未配置 TUSHARE_TOKEN"
            _permission_denied_apis = set()
        assert "TUSHARE_TOKEN" in self._reason(TC())

    def test_timeout_classified_without_raw_exception_text(self):
        class TC:
            last_error = ("ReadTimeout: HTTPSConnectionPool(host='api.tushare.pro', "
                          "port=443): Read timed out.")
            _permission_denied_apis = set()
        r = self._reason(TC())
        assert ("超时" in r) or ("网络" in r), f"未归类为超时/网络: {r!r}"
        assert "HTTPSConnectionPool" not in r, "不得回显底层异常文本（R12h）"

    def test_empty_without_error_reports_no_data(self):
        class TC:
            last_error = None
            _permission_denied_apis = set()
        r = self._reason(TC())
        assert "无" in r and ("成交" in r or "数据" in r), f"空数据未说明: {r!r}"

    def test_none_client_is_explicit(self):
        assert self._reason(None), "client 不可用也须给出非空原因"

    def test_reason_flows_into_report_note(self):
        """原因经 availability → 渲染端须出「（不可得：权限…）」。"""
        from lib.collector._orchestrate import _ms_set_unavailable
        from lib.render_markdown._v3 import _v3_ms_availability_note

        class TC:
            last_error = "无接口权限（本会话已确认）: opt_daily"
            _permission_denied_apis = {"opt_daily"}
        avail: dict[str, str] = {}
        _ms_set_unavailable(avail, "put_call_ratio", self._reason(TC()))
        note = _v3_ms_availability_note(avail, "put_call_ratio")
        assert "不可得" in note and "权限" in note


class TestTryFetchCallableReason:
    """`unavailable_msg` 支持 callable —— 在**失败时刻**取原因。

    静态串在调用前就固定了，读不到本次查询刚写入的 client 信号。
    """

    def test_callable_reason_invoked_at_failure_time(self):
        from lib.collector._orchestrate import _ms_try_fetch

        state = {"n": 0}

        def reason() -> str:
            state["n"] += 1
            return "动态原因"

        result: dict = {"availability": {}}
        _ms_try_fetch(result, "k", lambda: None, unavailable_msg=reason)
        assert result["availability"]["k"] == "unavailable: 动态原因"
        assert state["n"] == 1

    def test_callable_reason_used_on_exception_too(self):
        from lib.collector._orchestrate import _ms_try_fetch

        def boom():
            raise RuntimeError("x")

        result: dict = {"availability": {}}
        _ms_try_fetch(result, "k", boom, unavailable_msg=lambda: "异常原因")
        assert result["availability"]["k"] == "unavailable: 异常原因"

    def test_static_msg_unchanged(self):
        """静态串路径行为不变（其余 13 个维度仍在用）。"""
        from lib.collector._orchestrate import _ms_try_fetch

        result: dict = {"availability": {}}
        _ms_try_fetch(result, "k", lambda: None, unavailable_msg="静态原因")
        assert result["availability"]["k"] == "unavailable: 静态原因"

    def test_broken_reason_callable_does_not_raise(self):
        """原因函数自身抛错不得把降级路径变成崩溃。"""
        from lib.collector._orchestrate import _ms_try_fetch

        def bad() -> str:
            raise RuntimeError("reason 自身故障")

        result: dict = {"availability": {}}
        _ms_try_fetch(result, "k", lambda: None, unavailable_msg=bad)
        assert result["availability"]["k"].startswith("unavailable:")


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
            def stock_industry_pe_ratio_cninfo(symbol="证监会行业分类", date="20210910"):
                return pd.DataFrame([{
                    "行业名称": "银行", "静态市盈率-中位数": 5.0,
                    "静态市盈率-算术平均": 5.5, "公司数量": 42,
                }])

        return _FakeAk()

    def test_empty_industry_name_is_explicitly_unavailable(self, monkeypatch):
        """行业字段缺失（预取失败 → 空名）必须保留三态原因。

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
        result = src._q_akshare_industry_pe("600176", industry_name="")
        assert result["status"] == "unavailable"
        assert "行业名称" in result["note"]

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


# ---------- R2/T9-5（D-G=G2）：行业 PE 显式三态标注 ----------


class TestIndustryPeExplicitUnavailable:
    """取数失败须**显式三态标注**，不得静默丢键。

    修复前：`except Exception: logger.debug(...); return None` → `_merge_industry`
    只合并 dict，None 使 `industry_pe_median` **在维度 data 里直接消失**；而该键
    在全部渲染代码中零引用 → 缺失既无处可见、也无告警（只有一条 debug 日志）。
    """

    @staticmethod
    def _ok_ak():
        import pandas as pd

        class _FakeAk:
            @staticmethod
            def stock_industry_pe_ratio_cninfo(symbol="证监会行业分类", date="20210910"):
                return pd.DataFrame([{"行业名称": "银行", "静态市盈率-中位数": 5.0,
                                      "静态市盈率-算术平均": 5.5, "公司数量": 42}])
        return _FakeAk()

    @staticmethod
    def _patch_env(monkeypatch, src, fake_ak):
        import sys

        monkeypatch.setitem(sys.modules, "akshare", fake_ak)
        monkeypatch.setattr(src, "akshare_direct_session", lambda: _null_ctx())
        monkeypatch.setattr(src.env, "is_akshare_available", lambda: True)
        monkeypatch.setattr(src, "akshare_push2_available", lambda: True)

    def test_upstream_exception_yields_explicit_unavailable(self, monkeypatch):
        from lib.collector import _sources as src

        def boom():
            raise RuntimeError("cninfo 504 upstream down")

        class _BoomAk:
            stock_board_industry_pe_ratio_cninfo = staticmethod(boom)

        self._patch_env(monkeypatch, src, _BoomAk())
        r = src._q_akshare_industry_pe("600176", industry_name="银行")
        assert r is not None, "异常路径不得再静默返回 None（键会整个消失）"
        assert r["status"] == "unavailable"
        assert r["note"], "不可得须带原因"
        for leak in ("RuntimeError", "cninfo 504", "upstream down"):
            assert leak not in r["note"], f"不得回显底层异常原文（R12h）: {leak}"

    def test_success_carries_available_status(self, monkeypatch):
        from lib.collector import _sources as src

        self._patch_env(monkeypatch, src, self._ok_ak())
        r = src._q_akshare_industry_pe("600176", industry_name="银行")
        assert r["status"] == "available"
        assert r["industry_pe_median"] == 5.0

    def test_empty_table_is_explicit_unavailable(self, monkeypatch):
        import pandas as pd

        from lib.collector import _sources as src

        class _EmptyAk:
            # 须用**新接口名**：旧名在 akshare 1.18.64 已移除，用它只会走
            # AttributeError 分支，测不到「逐日正常返回空」这条路径（R2 审查修正）
            @staticmethod
            def stock_industry_pe_ratio_cninfo(symbol="证监会行业分类", date="20210910"):
                return pd.DataFrame()

        self._patch_env(monkeypatch, src, _EmptyAk())
        r = src._q_akshare_industry_pe("600176", industry_name="银行")
        assert r is not None and r["status"] == "unavailable" and r["note"]
        assert "均无数据" in r["note"] and "取数失败" not in r["note"]

    def test_no_match_is_explicit_unavailable(self, monkeypatch):
        import pandas as pd

        from lib.collector import _sources as src

        class _OtherAk:
            @staticmethod
            def stock_industry_pe_ratio_cninfo(symbol="证监会行业分类", date="20210910"):
                return pd.DataFrame([{"行业名称": "煤炭", "静态市盈率-中位数": 9.0}])

        self._patch_env(monkeypatch, src, _OtherAk())
        r = src._q_akshare_industry_pe("600176", industry_name="银行")
        assert r is not None and r["status"] == "unavailable" and r["note"]
        assert "未匹配" in r["note"]

    def test_all_days_raising_is_fetch_failure_not_no_data(self, monkeypatch):
        """逐日异常 ≠「近 7 日均无数据」——前者是**接口故障**，后者是对源内容的断言。

        裸 `continue` 吞掉异常后只能报「均无数据」，用户会去核对源页面，而真因
        （改名/签名变化/上游 5xx）要看 data-interface-map —— T9-5 要消除的误归因。
        """
        from lib.collector import _sources as src

        class _BoomAk:
            @staticmethod
            def stock_industry_pe_ratio_cninfo(symbol="证监会行业分类", date="20210910"):
                raise RuntimeError("cninfo 504 upstream down")

        self._patch_env(monkeypatch, src, _BoomAk())
        r = src._q_akshare_industry_pe("600176", industry_name="银行")
        assert r["status"] == "unavailable"
        assert "取数失败" in r["note"] and "均无数据" not in r["note"]
        for leak in ("RuntimeError", "cninfo 504", "upstream down"):
            assert leak not in r["note"], f"不得回显底层异常原文（R12h）: {leak}"

    def test_partial_day_failure_discloses_both_causes(self, monkeypatch):
        """部分日抛错 + 其余日正常空 → 两种成因都要说清（否则无法区分该修哪里）。"""
        import pandas as pd

        from lib.collector import _sources as src

        calls = {"n": 0}

        class _FlakyAk:
            @staticmethod
            def stock_industry_pe_ratio_cninfo(symbol="证监会行业分类", date="20210910"):
                calls["n"] += 1
                if calls["n"] == 1:
                    raise RuntimeError("boom")
                return pd.DataFrame()

        self._patch_env(monkeypatch, src, _FlakyAk())
        r = src._q_akshare_industry_pe("600176", industry_name="银行")
        assert r["status"] == "unavailable"
        assert "无数据" in r["note"] and "1 天取数失败" in r["note"]

    def test_empty_name_guard_is_explicitly_unavailable(self, monkeypatch):
        """空名守卫既防全表误匹配，也必须保留可渲染的不可得原因。"""
        from lib.collector import _sources as src

        self._patch_env(monkeypatch, src, self._ok_ak())
        monkeypatch.setattr(src, "_q_akshare_basic", lambda s: None)
        r = src._q_akshare_industry_pe("600176", industry_name="")
        assert r["status"] == "unavailable"
        assert "行业名称" in r["note"]


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


class TestPcrReasonWiringIntegration:
    """走**真实装配路径**（`collect_market_structure`）断言 PCR 不可得文案已分类。

    回归（R2 code-review HIGH）：`_ms_pcr_unavailable_reason` 曾写好但**没接到
    调用点**——`TestPcrUnavailableReason` 直接 import 私有函数断言返回值、
    `TestTryFetchCallableReason` 直接构造 `_ms_try_fetch`，**没有一条经过
    `collect_market_structure`**，故「写好了没接上」在测试面上完全不可见。
    """

    _FETCHERS = (
        "_ms_fetch_sw_index", "_ms_fetch_northbound_stock", "_ms_fetch_margin",
        "_ms_fetch_moneyflow", "_ms_fetch_turnover", "_ms_fetch_erp", "_ms_fetch_pmi",
        "_ms_fetch_put_call_ratio", "_ms_fetch_short_margin_growth",
        "_ms_fetch_new_high_ratio", "_ms_fetch_etf_flow",
    )

    @classmethod
    def _run(cls, monkeypatch):
        from lib.collector import _orchestrate as orch

        class _TC:
            last_error = "无接口权限（本会话已确认）: opt_daily"
            _permission_denied_apis = {"opt_daily"}

            def is_permission_denied(self, api):
                return api in self._permission_denied_apis

        monkeypatch.setattr(orch.env, "is_tushare_available", lambda cfg: True)
        monkeypatch.setattr(orch, "_tushare_client", lambda cfg: _TC())
        for name in cls._FETCHERS:
            monkeypatch.setattr(orch, name, lambda *a, **k: None)
        return orch.collect_market_structure("600176")

    def test_pcr_reason_is_classified_through_real_path(self, monkeypatch):
        avail = self._run(monkeypatch)["availability"]["put_call_ratio"]
        assert "权限" in avail, f"PCR 原因未分类（调用点未接线？）: {avail!r}"
        assert "5000" in avail, f"权限提示缺积分门槛: {avail!r}"

    def test_other_dimensions_keep_shape(self, monkeypatch):
        """改动只针对 PCR：其余维度仍是 `unavailable: {静态原因}` 形态。"""
        avail = self._run(monkeypatch)["availability"]
        assert avail["moneyflow"].startswith("unavailable: ")
        assert avail["sw_index"].startswith("unavailable: ")


class TestIndustryPeNotCountedAsSuccess:
    """显式不可得**不得**被记为成功源（R2 code-review HIGH 回归）。

    把失败从 `None` 改成 dict 后，`SourceResult.data is not None` → data_available
    为真、source_count 计入、`_merge_industry` 进 sources_ok → multi_source=true，
    证据表把不可得的源当**可用来源**渲染——「键静默消失」变成了「伪装成功」。
    """

    @staticmethod
    def _patch(monkeypatch, pe_result):
        from lib.collector import _orchestrate as orch

        monkeypatch.setattr(orch.env, "is_akshare_available", lambda: True)
        monkeypatch.setattr(orch, "akshare_push2_available", lambda: True)
        monkeypatch.setattr(orch, "_q_akshare_basic", lambda s: {"industry": "银行"})
        monkeypatch.setattr(orch, "_q_akshare_industry_board",
                            lambda s, industry_name="": {"industry_name": "银行",
                                                         "recent_return_pct": 5.0})
        monkeypatch.setattr(orch, "_q_akshare_industry_pe",
                            lambda s, industry_name="": pe_result)
        return orch

    def test_unavailable_pe_is_not_a_source_success(self, monkeypatch):
        orch = self._patch(monkeypatch, {
            "industry_name": "银行", "industry_pe_median": None, "industry_pe_avg": None,
            "status": "unavailable", "note": "巨潮行业 PE 接口调用失败（上游异常）"})
        dim = orch.collect_industry("600176")
        data = dim.get("data") or {}
        assert "industry_pe_median" not in data, "不可得的值不得冒充数据点"
        assert dim["_meta"].get("multi_source") is not True, "单源成功却被标 multi_source"
        assert data.get("industry_pe_status") == "unavailable"
        assert "失败" in (data.get("industry_pe_note") or ""), "原因须可见"

    def test_available_pe_still_merges_as_before(self, monkeypatch):
        orch = self._patch(monkeypatch, {
            "industry_name": "银行", "industry_pe_median": 5.0, "status": "available"})
        dim = orch.collect_industry("600176")
        assert (dim.get("data") or {}).get("industry_pe_median") == 5.0
        assert dim["_meta"].get("multi_source") is True

    def test_unavailable_pe_note_is_consumed_by_report(self):
        """不可得状态须进报告，不能只留在聚合 JSON 内。"""
        from lib.render_markdown._v2 import render_valuation_section

        text = render_valuation_section({
            "valuation": {"data": None, "error": "估值维度无数据", "_meta": {}},
            "industry": {"data": {
                "industry_pe_status": "unavailable",
                "industry_pe_note": "行业名称不可得，无法匹配巨潮口径",
            }},
        })
        assert "行业 PE 不可得" in text
        assert "行业名称不可得" in text

    def test_unavailable_pe_note_is_consumed_by_v3_report(self):
        """默认 Markdown 报告的 D-② 同样须呈现该三态原因。"""
        from types import SimpleNamespace

        from lib.render_markdown._v3 import _section_4d_valuation_expectation

        ctx = SimpleNamespace(
            vs=None, pe_avail=False, pe_pct=None, pb_pct_ext=None,
            hist_pe_median=None, current_pe=None, industry_peers={},
            industry_data={
                "industry_pe_status": "unavailable",
                "industry_pe_note": "行业名称不可得，无法匹配巨潮口径",
            },
        )
        text = "\n".join(_section_4d_valuation_expectation(ctx, []))
        assert "巨潮行业 PE 不可得" in text
        assert "行业名称不可得" in text


class TestIndustryPeCninfoRenamed:
    """巨潮行业 PE 接口在 akshare 1.18.64 **改名且改签名**（R0~R2 review 修复）。

    旧名 `stock_board_industry_pe_ratio_cninfo` 已移除（data-interface-map E 节登记为
    待办，但调用点从未跟进）→ 该维度**永久坏**，而 T9-5 的显式三态把原因写成
    「上游异常，本模块不修上游」——归因错误，真凶是本模块自己的过期接口名。

    新接口 `stock_industry_pe_ratio_cninfo(symbol, date)` 按**日期**取（默认值是
    2021 年的），且列名不同：`静态市盈率-中位数` / `静态市盈率-算术平均`。
    """

    @staticmethod
    def _new_ak(rows_by_date: dict[str, list[dict]], calls: list[str]):
        import pandas as pd

        class _Ak:
            @staticmethod
            def stock_industry_pe_ratio_cninfo(symbol="证监会行业分类", date="20210910"):
                calls.append(date)
                rows = rows_by_date.get(date)
                if rows is None:
                    raise ValueError("Length mismatch: 该日期无数据")
                return pd.DataFrame(rows)

        return _Ak()

    @staticmethod
    def _patch(monkeypatch, src, fake_ak):
        import sys

        monkeypatch.setitem(sys.modules, "akshare", fake_ak)
        monkeypatch.setattr(src, "akshare_direct_session", lambda: _null_ctx())
        monkeypatch.setattr(src.env, "is_akshare_available", lambda: True)
        monkeypatch.setattr(src, "akshare_push2_available", lambda: True)

    def test_uses_renamed_interface_and_new_columns(self, monkeypatch):
        from lib.collector import _sources as src

        calls: list[str] = []
        ak = self._new_ak({"20260910": [
            {"行业名称": "银行", "公司数量": 42, "静态市盈率-中位数": 5.0,
             "静态市盈率-算术平均": 5.5}]}, calls)
        self._patch(monkeypatch, src, ak)

        r = src._q_akshare_industry_pe("600176", industry_name="银行")
        assert r.get("status") == "available", f"改名后仍不可得: {r}"
        assert r["industry_pe_median"] == 5.0, "新列名 静态市盈率-中位数 未映射"
        assert r["industry_pe_avg"] == 5.5, "新列名 静态市盈率-算术平均 未映射"

    def test_looks_back_when_latest_date_has_no_data(self, monkeypatch):
        """周末/假日巨潮按日期取不到 → 回溯到最近有数据的日期，而非直接判不可得。"""
        from lib.collector import _sources as src

        calls: list[str] = []
        ak = self._new_ak({"20260909": [
            {"行业名称": "银行", "公司数量": 42, "静态市盈率-中位数": 5.0}]}, calls)
        self._patch(monkeypatch, src, ak)

        r = src._q_akshare_industry_pe("600176", industry_name="银行")
        assert r.get("status") == "available", f"未回溯取数: {r}"
        assert r["industry_pe_median"] == 5.0
        assert len(calls) >= 2, "须尝试多个日期而非一次就放弃"

    def test_all_dates_empty_is_explicit_unavailable(self, monkeypatch):
        from lib.collector import _sources as src

        calls: list[str] = []
        self._patch(monkeypatch, src, self._new_ak({}, calls))
        r = src._q_akshare_industry_pe("600176", industry_name="银行")
        assert r is not None and r["status"] == "unavailable"
        # 归因须指向本模块的接口面，而不是笼统的「上游异常」
        assert "巨潮" in r["note"] or "接口" in r["note"]

    def test_legacy_column_names_still_mapped(self, monkeypatch):
        """向后兼容：若某版本仍返回旧列名，映射不得失配。"""
        from lib.collector import _sources as src

        calls: list[str] = []
        ak = self._new_ak({"20260910": [
            {"行业名称": "银行", "公司数量": 42, "市盈率中位数": 7.0,
             "市盈率平均值": 7.5}]}, calls)
        self._patch(monkeypatch, src, ak)
        r = src._q_akshare_industry_pe("600176", industry_name="银行")
        assert r["industry_pe_median"] == 7.0
        assert r["industry_pe_avg"] == 7.5

    def test_cninfo_not_gated_on_eastmoney_push2(self, monkeypatch):
        """巨潮（cninfo）**不依赖东财 push2** → 代理环境下东财不通不应让本维度不可得。

        实测：本机 `akshare_push2_available()` 为 False（检测到代理且 push2 探测失败），
        而巨潮接口经 `akshare_direct_session` 可达 → 原早退守卫把整个维度挡成
        「akshare 不可用」，属**错误归因**（该维度此后一直不可得）。
        """
        from lib.collector import _sources as src

        calls: list[str] = []
        ak = self._new_ak({"20260910": [
            {"行业名称": "银行", "静态市盈率-中位数": 5.0}]}, calls)
        self._patch(monkeypatch, src, ak)
        monkeypatch.setattr(src, "akshare_push2_available", lambda: False)

        r = src._q_akshare_industry_pe("600176", industry_name="银行")
        assert r is not None and r.get("status") == "available", f"被东财探测误挡: {r}"


class TestPcrReasonOwnObservation:
    """PCR 原因须优先用**取数自身**的观测，而非共享的 `tc.last_error` 槽。

    回归（R0~R2 review）：超时时该槽仍为 None（`query` 入口置 None 后请求从未返回），
    于是落到「当日无 50ETF 期权成交（接口正常返回空）」——**把端点故障记成合法空结果**。
    且该槽被 trade_cal / 探针 / 并行扇出共享，任一错误都可能被误归因到 opt_daily。
    """

    @staticmethod
    def _tc():
        class TC:
            last_error = None            # 超时场景：从未被写入
            _permission_denied_apis: set = set()

            def query(self, api, **kw):
                import pandas as pd
                if api == "opt_basic":
                    return pd.DataFrame([
                        {"ts_code": "10004567.SH", "name": "50ETF购2601", "call_put": "C"},
                        {"ts_code": "10004568.SH", "name": "50ETF沽2601", "call_put": "P"},
                    ])
                if api == "trade_cal":
                    return pd.DataFrame({"cal_date": ["20260901", "20260902"]})
                return pd.DataFrame()
        return TC()

    def test_probe_timeout_reported_as_timeout_not_empty(self, monkeypatch):
        from lib.collector import _orchestrate as orch

        monkeypatch.setattr(orch, "_run_with_timeout", lambda fn, t, tag: None)
        diag: dict = {}
        assert orch._ms_fetch_put_call_ratio(self._tc(), diag=diag) is None
        assert diag.get("reason") == "probe_timeout", f"未记录自身观测: {diag}"

        reason = orch._ms_pcr_unavailable_reason(self._tc(), diag)
        assert ("超时" in reason) or ("网络" in reason), f"端点故障被记成空数据: {reason!r}"
        assert "正常返回空" not in reason, f"仍是「正常返回空」: {reason!r}"

    def test_empty_rows_reported_as_legit_empty(self, monkeypatch):
        """取到帧但无可用 PCR 行 → 才是「接口正常返回空」。"""
        from lib.collector import _orchestrate as orch

        class _EmptyFrame:
            empty = True

        monkeypatch.setattr(orch, "_run_with_timeout", lambda fn, t, tag: _EmptyFrame())
        diag: dict = {}
        assert orch._ms_fetch_put_call_ratio(self._tc(), diag=diag) is None
        assert diag.get("reason") == "empty_rows"
        assert "空" in orch._ms_pcr_unavailable_reason(self._tc(), diag)

    def test_falls_back_to_client_signal_when_no_own_observation(self):
        """无自身观测时仍回退到 client 信号（权限提示不丢）。"""
        from lib.collector import _orchestrate as orch

        class TC:
            last_error = "无接口权限（本会话已确认）: opt_daily"
            _permission_denied_apis = {"opt_daily"}
        assert "权限" in orch._ms_pcr_unavailable_reason(TC(), {})

    def test_permission_denied_beats_empty_rows_claim(self):
        """权限被拒时**不得**报「接口正常返回空」（R2 review P0）。

        积分不足/无权限时 `tc.query` 返回**空帧而不抛**（TushareClient 契约），
        探针因此「成功」→ diag=empty_rows → 旧顺序（diag 优先）把权限失败报成合法
        空结果，报告据此**断言「当日无 50ETF 期权成交」这一假市场事实**——比改动前
        的静态文案更差（那句至少点了权限拒绝）。权限标记是**已确证的事实**，
        故优先于「空返回」的成因推断。
        """
        from lib.collector import _orchestrate as orch

        class TC:
            last_error = "code=-2001 无接口权限: opt_daily"
            _permission_denied_apis = {"opt_daily"}

            def is_permission_denied(self, api):
                return api in self._permission_denied_apis

        reason = orch._ms_pcr_unavailable_reason(TC(), {"reason": "empty_rows"})
        assert "权限" in reason, f"权限被拒仍被报成空返回: {reason!r}"
        assert "正常返回空" not in reason

    def test_invalid_token_named_in_pcr_too(self):
        """PCR 侧同因同判：token 失效不得落进「数据源返回异常（非权限问题）」。"""
        from lib.collector import _orchestrate as orch

        class TC:
            last_error = "code=-2002 您的token不对"
            _permission_denied_apis: set = set()

            def is_permission_denied(self, api):
                return False

        reason = orch._ms_pcr_unavailable_reason(TC(), {})
        assert "TOKEN" in reason.upper(), f"token 失效未点名: {reason!r}"
        assert "非权限问题" not in reason

    def test_opt_basic_permission_denial_is_not_misclassified(self):
        """PCR 在 opt_basic 被拒时不会走到 opt_daily，仍须报告权限不足。"""
        import pandas as pd

        from lib.collector import _orchestrate as orch

        class TC:
            last_error = "抱歉，您没有接口(opt_basic)访问权限"
            _permission_denied_apis: set[str] = set()
            queried: list[str] = []

            def query(self, api, **kw):
                self.queried.append(api)
                return pd.DataFrame()

        tc = TC()
        diag: dict = {}
        assert orch._ms_fetch_put_call_ratio(tc, diag=diag) is None
        assert tc.queried == ["opt_basic"]
        reason = orch._ms_pcr_unavailable_reason(tc, diag)
        assert "权限" in reason and "opt_basic" in reason
        assert "非权限问题" not in reason


class TestForecastUnavailableReason:
    """forecast 不可得须**区分原因**（R0~R2 review P2）。

    回归：`_q_tushare_forecast` 不读 `tc.last_error`（该字段正是为「query 改返回空帧
    而非抛异常」而加），所有失败都被报成「权限不足或无数据（需 Tushare 2000+积分）」
    —— token 过期 / 配额用完 / 超时都误导用户**去买积分**，且「真空窗 vs 取数失败」
    不可区分。
    """

    @staticmethod
    def _reason(diag):
        from lib.collector._orchestrate import _forecast_unavailable_reason
        return _forecast_unavailable_reason(diag)

    def test_no_token_is_named_not_blamed_on_points(self):
        r = self._reason({"reason": "no_token"})
        assert "TUSHARE_TOKEN" in r
        assert "无数据" not in r

    def test_permission_denied_gives_points_hint(self):
        from lib.tushare_client import api_min_points

        r = self._reason({"reason": "permission"})
        assert "权限" in r
        assert str(api_min_points("forecast")) in r, f"缺积分门槛: {r!r}"

    def test_timeout_is_not_reported_as_permission(self):
        r = self._reason({"reason": "timeout"})
        assert ("超时" in r) or ("网络" in r)
        # 文案含「非权限问题」是有意说明；禁的是**引导买积分**
        assert "需 Tushare" not in r, f"超时被误导成买积分: {r!r}"

    def test_empty_window_is_a_legit_empty(self):
        r = self._reason({"reason": "empty"})
        assert "空" in r and "无数据" not in r

    def test_invalid_token_named_as_token_not_generic_error(self):
        """token 失效（tushare code=-2002）须**点名 token**。

        分类器只映射了 -2001（配额），-2002 落到兜底 `error` → 报「数据源返回异常
        （非权限问题）」，把用户引离唯一有效的动作（重签 token）——正是本修复要消除
        的误归因（R2 review P2）。
        """
        from lib.collector import _orchestrate as orch

        class TC:
            last_error = "code=-2002 您的token不对"
            _permission_denied_apis: set = set()

            def is_permission_denied(self, api):
                return False

        assert orch._classify_tushare_error(TC(), "forecast") == "token_invalid"
        r = self._reason({"reason": "token_invalid"})
        assert "TOKEN" in r.upper() and "无数据" not in r
        assert "非权限问题" not in r


class TestForecastReasonWiring:
    """走**真实装配路径**（collect_research）断言原因已接线——防「写好了没接上」。"""

    @staticmethod
    def _run(monkeypatch, exc=None, data=None):
        from lib.collector import _orchestrate as orch

        def fake_forecast(symbol, *, diag=None):
            if exc is not None:
                raise exc
            if diag is not None and data is None:
                diag["reason"] = "empty"
            return data

        monkeypatch.setattr(orch, "_q_tushare_forecast", fake_forecast)
        monkeypatch.setattr(orch, "_q_tushare_report_rc", lambda s: None)   # rc 走空 → 落到 forecast
        return orch.collect_research("600176")

    def test_missing_token_reports_token_not_points(self, monkeypatch):
        res = self._run(monkeypatch, exc=RuntimeError("TUSHARE_TOKEN not configured"))
        fc = [x.get("error") for x in (res.get("_meta") or {}).get("all_sources", [])
              if isinstance(x, dict) and x.get("source") == "tushare.forecast"]
        assert fc, "forecast 源未出现在 all_sources"
        assert "TUSHARE_TOKEN" in fc[0], f"未按 token 缺失报因: {fc[0]}"
        assert "权限不足" not in fc[0], f"仍把缺 token 说成权限不足: {fc[0]}"

    def test_empty_window_reported_as_empty(self, monkeypatch):
        res = self._run(monkeypatch, data=None)
        errs = [x.get("error") for x in (res.get("_meta") or {}).get("all_sources", [])
                if isinstance(x, dict) and x.get("error")]
        joined = " ".join(errs)
        assert "空" in joined, f"真空窗未如实说明: {errs}"
