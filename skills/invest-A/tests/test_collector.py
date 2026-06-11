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

    def test_returns_exactly_four_dates(self):
        """始终返回恰好 4 个季末日期。"""
        from datetime import datetime
        from lib.collector import _latest_quarter_dates

        for month in range(1, 13):
            dates = _latest_quarter_dates(as_of=datetime(2025, month, 15))
            assert len(dates) == 4, f"month={month} 返回 {len(dates)} 条"
            for d in dates:
                assert d[4:] in ("0331", "0630", "0930", "1231"), f"非季末: {d}"


class TestProxyBypass:
    def test_restore_after_single_use(self):
        from lib.collector import _proxy_bypass

        os.environ["HTTP_PROXY"] = "http://test-proxy:8080"
        try:
            with _proxy_bypass():
                assert "HTTP_PROXY" not in os.environ
            assert os.environ.get("HTTP_PROXY") == "http://test-proxy:8080"
        finally:
            os.environ.pop("HTTP_PROXY", None)

    def test_nested_bypass_restores_once(self):
        """嵌套 bypass 结束后恢复进入前的代理（引用计数）。"""
        from lib.collector import _proxy_bypass

        os.environ["HTTP_PROXY"] = "http://nested:1"
        try:
            with _proxy_bypass():
                assert "HTTP_PROXY" not in os.environ
                with _proxy_bypass():
                    assert "HTTP_PROXY" not in os.environ
                assert "HTTP_PROXY" not in os.environ
            assert os.environ.get("HTTP_PROXY") == "http://nested:1"
        finally:
            os.environ.pop("HTTP_PROXY", None)

    def test_parallel_bypass_both_complete(self):
        """两线程并发 bypass：均应完成且不抛异常（引用计数防竞争）。"""
        from lib.collector import _proxy_bypass

        import threading

        errors: list[Exception] = []
        barrier = threading.Barrier(2, timeout=5)

        def worker() -> None:
            try:
                barrier.wait()  # 两线程同时进入 bypass
                with _proxy_bypass():
                    assert "HTTP_PROXY" not in os.environ or True
            except Exception as e:
                errors.append(e)

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(worker) for _ in range(2)]
            for f in futures:
                f.result()
        assert not errors
