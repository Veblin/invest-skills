"""collector 辅助逻辑测试（无网络）。"""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor

import pytest


class TestLatestQuarterDates:
    def test_quarter_end_dates(self):
        """季末日期应为 0331/0630/0930/1231，而非错位的月末。"""
        from datetime import datetime
        from lib.collector import _latest_quarter_dates

        dates = _latest_quarter_dates(as_of=datetime(2026, 6, 11))
        assert dates[0] == "20260331"
        assert dates[1] == "20251231"
        assert dates[2] == "20250930"
        assert dates[3] == "20250630"

    def test_exact_quarter_end_day_returns_that_quarter(self):
        """季度末当天（如 3/31）该季度视为已完成，含在返回列表中。"""
        from datetime import datetime
        from lib.collector import _latest_quarter_dates

        dates = _latest_quarter_dates(as_of=datetime(2026, 3, 31))
        # 3/31 当天 Q1 已完成 → dates[0] = 20260331
        assert dates[0] == "20260331"
        assert dates[1] == "20251231"

    def test_year_start_returns_previous_year_q4(self):
        """年初（1/1）最新已完成季度是上年 Q4。"""
        from datetime import datetime
        from lib.collector import _latest_quarter_dates

        dates = _latest_quarter_dates(as_of=datetime(2026, 1, 1))
        assert dates[0] == "20251231"

    def test_leap_year_february(self):
        """闰年 2 月仍应正确计算 Q4 的 12/31。"""
        from datetime import datetime
        from lib.collector import _latest_quarter_dates

        dates = _latest_quarter_dates(as_of=datetime(2024, 2, 29))
        assert dates[0] == "20231231"

    def test_returns_five_dates_by_default(self):
        """默认返回 5 个季末日期（股东查询日期重试）。"""
        from datetime import datetime
        from lib.collector import _latest_quarter_dates

        for month in range(1, 13):
            dates = _latest_quarter_dates(as_of=datetime(2025, month, 15))
            assert len(dates) == 5, f"month={month} 返回 {len(dates)} 条"
            for d in dates:
                assert d[4:] in ("0331", "0630", "0930", "1231"), f"非季末: {d}"

    def test_count_override(self):
        from datetime import datetime
        from lib.collector import _latest_quarter_dates

        dates = _latest_quarter_dates(as_of=datetime(2026, 6, 11), count=4)
        assert len(dates) == 4


class TestAkshareShareholdersRetry:
    def test_connection_error_retries_next_quarter(self, monkeypatch):
        """临时 Connection 错误应继续尝试下一报告期，而非整函数失败。"""
        calls: list[str] = []

        class _Result:
            def to_dict(self, orient: str = "records"):
                return [{"股东名称": "甲", "持股数": 100, "占总股本持股比例": 10.0}]

        def _fake_em(symbol: str, date: str):
            calls.append(date)
            if len(calls) < 3:
                raise ConnectionError("Connection refused")
            return _Result()

        import akshare as ak

        monkeypatch.setattr(ak, "stock_gdfx_top_10_em", _fake_em)
        from lib.collector import _q_akshare_shareholders

        result = _q_akshare_shareholders("600519")
        assert result is not None
        assert len(calls) == 3
        assert result[0]["holder_name"] == "甲"


class TestProxyBypass:
    def test_collector_proxy_bypass_clears_http_proxy(self):
        """collector 导出 _proxy_bypass，在 context 内清除 HTTP 代理变量。"""
        from lib.collector import _proxy_bypass

        old_http = os.environ.get("HTTP_PROXY")
        os.environ["HTTP_PROXY"] = "http://test-proxy:8080"
        try:
            with _proxy_bypass():
                assert os.environ.get("HTTP_PROXY") is None
                assert ".eastmoney.com" in os.environ.get("no_proxy", "")
        finally:
            if old_http is None:
                os.environ.pop("HTTP_PROXY", None)
            else:
                os.environ["HTTP_PROXY"] = old_http
            os.environ.pop("no_proxy", None)



# ======================================================================
# /code-review max 修复回归（2026-08-04）
# F1 QFQ 锚定 / F12 增减持三态 / F4 融券分位 / F5 PCR 60d / F6 创新高抽样 / F16 worker 钳制
# ======================================================================


class TestApplyQfqAnchor:
    """F1: _apply_qfq 必须以最新日为锚（Tushare 降序行下 rows[-1] 是最旧 bar）。"""

    def test_desc_rows_anchor_on_newest(self):
        from lib.collector._sources import _apply_qfq

        # Tushare 风格降序（最新在前）：最新日 raw close 39.65（300628 实测案例）
        rows = [
            {"trade_date": "20260801", "open": 40.0, "high": 40.5,
             "low": 39.5, "close": 39.65},
            {"trade_date": "20260731", "open": 39.0, "high": 39.4,
             "low": 38.6, "close": 38.9},
            {"trade_date": "20260730", "open": 38.0, "high": 38.5,
             "low": 37.5, "close": 37.8},
        ]
        factors = {"20260730": 20.0, "20260731": 20.99, "20260801": 20.9908}
        out = _apply_qfq(rows, factors)
        assert out is not None
        # 最新日 qfq == raw（锚定最新因子）；修复前 rows[-1]=20260730 → 39.65×20.9908/20 ≈ 41.61
        assert out[0]["close"] == pytest.approx(39.65)
        assert out[0]["trade_date"] == "20260801"  # 输出保持输入顺序（降序）
        assert out[1]["close"] == pytest.approx(38.9 * 20.99 / 20.9908)
        assert out[2]["close"] == pytest.approx(37.8 * 20.0 / 20.9908)

    def test_asc_rows_still_anchored_latest(self):
        from lib.collector._sources import _apply_qfq

        rows = [
            {"trade_date": "20260730", "open": 38.0, "high": 38.5,
             "low": 37.5, "close": 37.8},
            {"trade_date": "20260801", "open": 40.0, "high": 40.5,
             "low": 39.5, "close": 39.65},
        ]
        factors = {"20260730": 20.0, "20260801": 20.9908}
        out = _apply_qfq(rows, factors)
        assert out is not None
        assert out[1]["close"] == pytest.approx(39.65)
        assert out[0]["close"] == pytest.approx(37.8 * 20.0 / 20.9908)

    def test_missing_factor_rejects_whole(self):
        from lib.collector._sources import _apply_qfq

        rows = [{"trade_date": "20260801", "open": 1.0, "high": 1.0,
                 "low": 1.0, "close": 1.0}]
        assert _apply_qfq(rows, {}) is None


class TestHoldertradeDirection:
    """F12: in_de 缺失/NaN 不得默认标"减持"。"""

    def test_missing_in_de_marks_unknown(self, monkeypatch):
        from lib.collector._orchestrate import _q_tushare_holdertrade
        import pandas as pd

        class FakeTC:
            def query(self, *a, **k):
                return pd.DataFrame([
                    {"ann_date": "20260701", "holder_name": "A", "in_de": "IN"},
                    {"ann_date": "20260702", "holder_name": "B", "in_de": "DE"},
                    {"ann_date": "20260703", "holder_name": "C", "in_de": None},
                    {"ann_date": "20260704", "holder_name": "D", "in_de": float("nan")},
                ])

        monkeypatch.setattr(
            "lib.collector._orchestrate._require_tushare",
            lambda: (None, FakeTC()),
        )
        recs = _q_tushare_holdertrade("300628") or []
        assert [r["direction"] for r in recs] == ["增持", "减持", "未知", "未知"]


class TestShortMarginPercentile:
    """F4: 负增速日必须参与分位（mid-rank，不剔非正值、冻结序列中性）。"""

    def test_negative_growth_percentile_not_zero(self):
        from lib.collector._orchestrate import _ms_fetch_short_margin_growth
        import pandas as pd

        class FakeTC:
            def query(self, *a, **k):
                # 16 日：前 11 日 base=100，后 5 日 → growths = [0, -10, -5, -10, -5, -10]
                dates = [f"2026{i:02d}01" for i in range(1, 17)]
                rqye = [100.0] * 11 + [90.0, 95.0, 90.0, 95.0, 90.0]
                return pd.DataFrame({"trade_date": dates, "rqye": rqye})

        r = _ms_fetch_short_margin_growth(FakeTC(), "300628")
        assert r is not None
        assert r["growth_pct"] == -10.0
        # 修复前 percentile_rank(v>0 过滤) → 全负序列 → None；
        # mid-rank = (count(< -10) + 0.5×count(==-10))/6 = (0 + 1.5)/6 = 25
        assert r["percentile_5y"] == 25.0

    def test_frozen_series_neutral_percentile(self):
        """恒等序列 → 50%（旧含边界分位给 100% 假"5年最高位"信号）。"""
        from lib.collector._orchestrate import _ms_fetch_short_margin_growth
        import pandas as pd

        class FakeTC:
            def query(self, *a, **k):
                dates = [f"2026{i:02d}01" for i in range(1, 17)]
                return pd.DataFrame({"trade_date": dates, "rqye": [100.0] * 16})

        r = _ms_fetch_short_margin_growth(FakeTC(), "300628")
        assert r is not None
        assert r["growth_pct"] == 0.0
        assert r["percentile_5y"] == 50.0


class TestPutCallRatio60dWindow:
    """F5: 60日分位必须用最近 60 自然日全分辨率，而非降采样序列的末 60 点。"""

    def test_60d_window_full_resolution(self):
        from lib.collector._orchestrate import (
            _ms_fetch_put_call_ratio, _days_ago, _PCR_MAX_DAILY_QUERIES,
        )
        import pandas as pd

        cutoff = _days_ago(60)
        # 260 个交易日（>80 触发 5 年降采样），最近 60 自然日内 ratio=2.0，更早 0.5
        dates = pd.bdate_range(end=pd.Timestamp.now().normalize(), periods=260)
        cal = [d.strftime("%Y%m%d") for d in dates]

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
                    ratio = 2.0 if td >= cutoff else 0.5
                    return pd.DataFrame({"ts_code": ["10004567.SH", "10004568.SH"],
                                         "vol": [100.0, 100.0 * ratio]})
                return pd.DataFrame()

        r = _ms_fetch_put_call_ratio(FakeTC())
        assert r is not None
        assert r["ratio"] == 2.0
        # 最近 60 自然日全分辨率取数 → 拉取点数超过 5 年降采样上限
        assert r["sample_points"] > _PCR_MAX_DAILY_QUERIES
        assert r["sampled"] is True
        # 60d 窗口内全部为 2.0 → 分位 0.0（修复前混入旧 0.5 样本 → ~33）
        assert r["percentile_60d"] == 0.0
        # 5y 窗口包含 0.5 样本 → 分位显著高于 60d 窗口
        assert r["percentile_5y"] > r["percentile_60d"]
        # staleness 标识：最新日查询成功 → current_date == 最新交易日
        assert r["current_date"] == cal[-1]


class TestNewHighSampling:
    """F6: 创新高占比必须全市场等步长抽样，而非恒取前 30 行 SZ。"""

    def test_sample_spread_across_exchanges(self, monkeypatch):
        from lib.collector._orchestrate import _ms_fetch_new_high_ratio
        import pandas as pd

        codes = [f"000{i:03d}.SZ" for i in range(100)] \
              + [f"300{i:03d}.SZ" for i in range(20)] \
              + [f"600{i:03d}.SH" for i in range(60)] \
              + [f"688{i:03d}.SH" for i in range(10)] \
              + [f"920{i:03d}.BJ" for i in range(10)]
        captured: dict = {}

        class FakeTC:
            def query(self, api, **kw):
                if api == "stock_basic":
                    return pd.DataFrame({"ts_code": codes})
                if api == "daily":
                    return pd.DataFrame({"trade_date": ["20260730", "20260731", "20260801"],
                                         "close": [10.0, 10.5, 11.0],
                                         "high": [10.2, 10.7, 11.2]})
                return pd.DataFrame()

        def fake_panel(panel):
            captured["codes"] = set(panel.keys())
            return 10.0

        monkeypatch.setattr(
            "lib.collector._orchestrate._ms_new_high_ratio_from_panel", fake_panel)
        r = _ms_fetch_new_high_ratio(FakeTC())
        assert r is not None
        assert r["sample_requested"] == 30
        assert r["sample_size"] == 30
        # 修复前恒为 000xxx.SZ 前 30 只；修复后种子随机抽样跨交易所
        assert len(captured["codes"]) == 30
        suffixes = {c.split(".")[-1] for c in captured["codes"]}
        assert len(suffixes) >= 2  # 至少覆盖两个交易所


class TestEnvMaxWorkers:
    """F16: INVEST_MAX_WORKERS 钳制下限 1（0/负值会挂死 Semaphore）。"""

    def test_clamps_lower_bound(self, monkeypatch):
        from lib.collector._base import _env_max_workers

        monkeypatch.setenv("INVEST_MAX_WORKERS", "0")
        assert _env_max_workers() == 1
        monkeypatch.setenv("INVEST_MAX_WORKERS", "-3")
        assert _env_max_workers() == 1
        monkeypatch.setenv("INVEST_MAX_WORKERS", "abc")
        assert _env_max_workers() == 8
        monkeypatch.delenv("INVEST_MAX_WORKERS")
        assert _env_max_workers() == 8

    def test_respects_value(self, monkeypatch):
        from lib.collector._base import _env_max_workers

        monkeypatch.setenv("INVEST_MAX_WORKERS", "4")
        assert _env_max_workers() == 4


class TestPcrNanVolDefense:
    """A2: NaN 成交量不得污染认沽认购比（NaN<=0 守卫会放行 NaN）。"""

    def test_nan_vol_row_skipped(self):
        import math

        import pandas as pd

        from lib.collector._orchestrate import _ms_pcr_on_date

        class FakeTC:
            def query(self, api, **kw):
                return pd.DataFrame({
                    "ts_code": ["10004567.SH", "10004568.SH", "20004567.SH", "20004568.SH"],
                    "vol": [float("nan"), 200.0, 300.0, 100.0],
                })

        r = _ms_pcr_on_date(FakeTC(), "20260803", {"10004567.SH", "10004568.SH"},
                            {"20004567.SH", "20004568.SH"})
        assert r is not None
        assert not math.isnan(r)
        # NaN 行（put 侧）被跳过：0.5 = 200/(300+100)，而非 NaN
        assert r == 0.5


class TestPutCallRatioStaleOutsideWindow:
    """A3: 最新采样日失败 → current 回退滑出 60 日窗口 → percentile_60d 置 None。"""

    def test_current_outside_window_percentile_none(self):
        import pandas as pd

        from lib.collector._orchestrate import (
            _PCR_MAX_DAILY_QUERIES, _days_ago, _ms_fetch_put_call_ratio,
            _ms_subsample_trade_dates,
        )

        cutoff = _days_ago(60)
        # 1230 个交易日（~5 年）：降采样 step≈15
        dates = pd.bdate_range(end=pd.Timestamp.now().normalize(), periods=1230)
        cal = [d.strftime("%Y%m%d") for d in dates]
        sampled = _ms_subsample_trade_dates(cal, _PCR_MAX_DAILY_QUERIES)
        # 最新 5 个采样日查询失败（窗口内 3 个 + 窗口外 2 个）→ current 回退
        # 到 sampled[-6]（≈103 自然日前）——远超 60 日窗口
        fail_dates = set(sampled[-5:])
        assert sampled[-6] < cutoff  # 场景自检：回退目标确实在窗口外

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

        r = _ms_fetch_put_call_ratio(FakeTC())
        assert r is not None
        assert r["ratio"] == 0.5
        assert r["current_date"] == sampled[-6]
        assert r["current_date"] < cutoff
        assert r["partial"] is True  # stale 计入 partial
        # 修复点：current 在窗口外 → 60 日分位无意义，置 None 而非伪造"低位"
        assert r["percentile_60d"] is None
        assert isinstance(r["percentile_5y"], float)


class TestApplyQfqNewestRawFallback:
    """A6: 盘中最新日缺 adj_factor → 去掉最新日重试 + 最新日 raw 保留。"""

    @staticmethod
    def _rows():
        return [
            {"trade_date": "20260710", "open": 10.0, "high": 10.5, "low": 9.5, "close": 10.0},
            {"trade_date": "20260711", "open": 11.0, "high": 11.5, "low": 10.5, "close": 11.0},
            {"trade_date": "20260712", "open": 12.0, "high": 12.5, "low": 11.5, "close": 12.0},
        ]

    def test_missing_newest_factor_keeps_qfq_history(self):
        from lib.collector._orchestrate import _apply_qfq_with_newest_raw_fallback

        rows = self._rows()
        factors = {"20260710": 1.2, "20260711": 1.1}  # 最新日缺失（盘中常态）
        out = _apply_qfq_with_newest_raw_fallback(rows, factors)
        assert out is not None
        assert out[-1]["trade_date"] == "20260712"
        assert out[-1]["close"] == 12.0  # 最新日 raw 原样（自身锚定下 qfq==raw）
        r10 = [r for r in out if r["trade_date"] == "20260710"][0]
        assert r10["close"] == round(10.0 * 1.2 / 1.1, 4)  # 锚 20260711 的 qfq
        r11 = [r for r in out if r["trade_date"] == "20260711"][0]
        assert r11["close"] == 11.0

    def test_all_factors_present_full_qfq(self):
        from lib.collector._orchestrate import _apply_qfq_with_newest_raw_fallback

        rows = self._rows()
        factors = {"20260710": 1.2, "20260711": 1.1, "20260712": 1.0}
        out = _apply_qfq_with_newest_raw_fallback(rows, factors)
        assert out is not None
        assert len(out) == 3
        r12 = [r for r in out if r["trade_date"] == "20260712"][0]
        assert r12["close"] == 12.0  # 锚定日 identity

    def test_middle_factor_missing_rejects(self):
        from lib.collector._orchestrate import _apply_qfq_with_newest_raw_fallback

        assert _apply_qfq_with_newest_raw_fallback(
            self._rows(), {"20260710": 1.2, "20260712": 1.0}) is None

    def test_single_row_or_no_factors_returns_none(self):
        from lib.collector._orchestrate import _apply_qfq_with_newest_raw_fallback

        rows = self._rows()
        assert _apply_qfq_with_newest_raw_fallback(rows, {}) is None
        # 单行且缺自身因子：去掉最新日后无剩余行 → None（调用方 raw 回退）
        assert _apply_qfq_with_newest_raw_fallback([rows[0]], {"20260711": 1.1}) is None


class TestBuildSummary:
    """A7: summary 计数分区完整（data=[] 不再无归属）+ sources_responded。"""

    def test_empty_data_dim_counts_missing_not_invisible(self):
        from lib.collector._orchestrate import _build_summary

        dims = [
            {"status": "available", "data": []},  # 非交易日 quote
            {"status": "missing", "data": None},
            {"status": "missing", "data": None},
        ]
        s = _build_summary(dims)
        assert s["total"] == 3
        assert s["available"] == 0
        assert s["missing"] == 3  # 修复前 data=[] 维度不计入任何计数器
        assert s["sources_responded"] == 1  # quote 源有响应 → 不触发全部失败中止
        assert s["all_partial"] is False

    def test_all_data_bearing_partial_is_all_partial(self):
        from lib.collector._orchestrate import _build_summary

        s = _build_summary([
            {"status": "partial", "data": [1]},
            {"status": "partial", "data": [2]},
        ])
        assert s["available"] == 2
        assert s["all_partial"] is True

    def test_empty_partial_dim_breaks_all_partial(self):
        from lib.collector._orchestrate import _build_summary

        s = _build_summary([
            {"status": "available", "data": [1]},
            {"status": "partial", "data": []},  # 无数据的 partial 维度
        ])
        assert s["available"] == 1
        assert s["degraded"] == 1
        assert s["missing"] == 0  # partial 维度归入 partial 计数，分区完整
        assert s["all_partial"] is False  # 有数据维度（available）非 partial

    def test_all_sources_failed_responded_zero(self):
        from lib.collector._orchestrate import _build_summary

        s = _build_summary([
            {"status": "missing", "data": None},
            {"status": "missing"},
        ])
        assert s["sources_responded"] == 0
        assert s["missing"] == 2
        assert s["available"] == 0


class TestIncomeRevenueFallback:
    """R12b: income 表兜底 revenue/net_profit（fina_indicator 字段被积分过滤时）。"""

    def test_revenue_fallback_from_income(self):
        from lib.collector._sources import _merge_income_into_financials

        fin = [
            {"end_date": "20260331", "roe": -1.85},  # 无 revenue/net_profit（积分过滤）
            {"end_date": "20251231", "revenue": 1.0, "net_profit": 0.2},  # 已有值不被覆盖
        ]
        income = [
            {"end_date": "20260331", "revenue": 355088600.0,
             "total_revenue": 355088600.0, "n_income_attr_p": -19178060.55},
            {"end_date": "20251231", "revenue": 999.0, "n_income_attr_p": 999.0},
        ]
        out = _merge_income_into_financials(fin, income)
        by_date = {r["end_date"]: r for r in out}
        q1 = by_date["20260331"]
        assert q1["revenue"] == 355088600.0  # income 表兜底
        assert q1["net_profit"] == -19178060.55  # n_income_attr_p 兜底
        q4 = by_date["20251231"]
        assert q4["revenue"] == 1.0  # 已有值不被覆盖
        assert q4["net_profit"] == 0.2

    def test_revenue_fallback_absent_income_keeps_original(self):
        from lib.collector._sources import _merge_income_into_financials

        fin = [{"end_date": "20260331", "roe": -1.85}]
        out = _merge_income_into_financials(fin, [])  # income 空
        assert out[0].get("revenue") is None  # 无兜底来源则保持原样
        assert out[0]["roe"] == -1.85
