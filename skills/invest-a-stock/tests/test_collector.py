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

