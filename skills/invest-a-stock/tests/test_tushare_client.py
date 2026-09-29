"""Tushare 轻量客户端测试。"""

from __future__ import annotations

import logging
from datetime import datetime
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

BEIJING = ZoneInfo("Asia/Shanghai")
UTC = ZoneInfo("UTC")


class TestTusharePermissionDenied:
    def test_permission_denied_logged_at_debug_and_cached(self, caplog):
        from lib.tushare_client import TushareClient

        client = TushareClient(token="a" * 32)
        responses = [
            {"code": 40203, "msg": "抱歉，您没有接口(sw_daily)访问权限"},
            {"code": 0, "data": {"fields": ["close"], "items": [[1.0]]}},
        ]

        def _fake_post(*_a, **_kw):
            class _R:
                def raise_for_status(self):
                    return None

                def json(self):
                    return responses.pop(0) if responses else {"code": 0, "data": {}}

            return _R()

        client._session.post = _fake_post  # type: ignore[method-assign]

        with caplog.at_level(logging.DEBUG, logger="lib.tushare_client"):
            first = client.query("sw_daily", ts_code="851024.SI")
            second = client.query("sw_daily", ts_code="851024.SI")

        assert first.empty
        assert second.empty
        assert "sw_daily" in client._permission_denied_apis
        assert not any(r.levelno >= logging.WARNING for r in caplog.records)

    def test_sw_index_availability_labels_akshare_fallback(self):
        from lib.collector import _ms_sw_index_availability_label

        label = _ms_sw_index_availability_label({"source": "akshare.index_hist_sw"})
        assert "5000" in label
        assert "akshare fallback" in label
        assert _ms_sw_index_availability_label({"source": "tushare.sw_daily"}) == "available"

    def test_sw_index_falls_back_to_akshare_when_tushare_empty(self, monkeypatch):
        from lib import collector
        from lib.collector import _orchestrate as collector_orch

        mock_tc = MagicMock()
        mock_tc.query.side_effect = lambda api, **kw: (
            pd.DataFrame({"industry_name": ["通信设备"], "index_code": ["851024.SI"]})
            if api == "index_classify"
            else pd.DataFrame()
        )

        fake_sw = {
            "index_code": "851024.SI",
            "industry": "通信设备",
            "return_20d_pct": 5.0,
            "source": "akshare.index_hist_sw",
        }

        with patch.object(collector.env, "is_tushare_available", return_value=True), patch.object(
            collector.env, "get_config", return_value={"TUSHARE_TOKEN": "x" * 32},
        ), patch.object(collector_orch, "_tushare_client", return_value=mock_tc), patch.object(
            collector_orch, "_ms_fetch_sw_index_akshare", return_value=fake_sw,
        ):
            result = collector._ms_fetch_sw_index(mock_tc, "300308", "通信设备")

        assert result is not None
        assert result["source"] == "akshare.index_hist_sw"

    def test_sw_index_akshare_prefers_industry_lookup_over_tushare_code(self, monkeypatch):
        from lib import collector
        from lib.collector import _orchestrate as collector_orch

        # _ms_fetch_sw_index_akshare 从 _orchestrate 命名空间解析这三个名字
        monkeypatch.setattr(
            collector_orch, "_ms_lookup_akshare_sw_code", lambda industry: "801093",
        )
        monkeypatch.setattr(
            collector_orch, "_akshare_closes_from_hist_sw",
            lambda code, **kw: [100.0, 105.0] if code == "801093" else [],
        )
        monkeypatch.setattr(
            collector_orch, "_akshare_hs300_closes", lambda **kw: [3000.0, 3010.0],
        )

        result = collector._ms_fetch_sw_index_akshare(
            "300308", "通信设备", index_code="851024.SI", tc=None,
        )
        assert result is not None
        assert result["index_code"] == "801093"
        assert result["source"] == "akshare.index_hist_sw"


class TestTushareInstanceRateLimits:
    def test_custom_limits_do_not_mutate_module_defaults(self):
        from lib import tushare_client as tc

        default_rpm = tc.RATE_LIMIT_PER_MINUTE
        default_daily = tc.DAILY_CALL_LIMIT

        client = tc.TushareClient(
            token="a" * 32,
            rate_limit_per_minute=180,
            daily_call_limit=5000,
        )

        assert client._rate_limit_per_minute == 180
        assert client._daily_call_limit == 5000
        assert tc.RATE_LIMIT_PER_MINUTE == default_rpm
        assert tc.DAILY_CALL_LIMIT == default_daily
        assert client.remaining_calls_today() == 5000

    def test_default_limits_match_module_constants(self):
        from lib import tushare_client as tc

        client = tc.TushareClient(token="a" * 32)
        assert client._rate_limit_per_minute == tc.RATE_LIMIT_PER_MINUTE
        assert client._daily_call_limit == tc.DAILY_CALL_LIMIT


class TestTushareDailyQuotaReset:
    def test_init_reset_at_next_beijing_midnight(self):
        from lib.tushare_client import TushareClient

        # 2024-06-15 20:00 UTC = 2024-06-16 04:00 Beijing
        now = datetime(2024, 6, 15, 20, 0, 0, tzinfo=UTC).timestamp()
        expected = datetime(2024, 6, 17, 0, 0, 0, tzinfo=BEIJING).timestamp()

        with patch("lib.tushare_client.time.time", return_value=now):
            client = TushareClient(token="a" * 32)

        assert client._daily_reset_at == expected

    def test_counter_not_reset_before_beijing_midnight(self):
        from lib.tushare_client import TushareClient

        # 23:30 Beijing = 15:30 UTC — 30 min before Beijing midnight
        init_ts = datetime(2024, 1, 15, 15, 30, 0, tzinfo=UTC).timestamp()
        check_ts = datetime(2024, 1, 15, 15, 45, 0, tzinfo=UTC).timestamp()

        with patch("lib.tushare_client.time.time", return_value=init_ts):
            client = TushareClient(token="a" * 32)
        client._daily_calls = 42

        with patch("lib.tushare_client.time.time", return_value=check_ts):
            client._reset_daily_counter_if_needed()

        assert client._daily_calls == 42

    def test_counter_resets_after_beijing_midnight_not_utc(self):
        from lib.tushare_client import TushareClient

        # 23:30 Beijing = 15:30 UTC
        init_ts = datetime(2024, 1, 15, 15, 30, 0, tzinfo=UTC).timestamp()
        # 00:30 Beijing next day = 16:30 UTC — past Beijing midnight, before UTC midnight
        after_beijing_midnight = datetime(2024, 1, 15, 16, 30, 0, tzinfo=UTC).timestamp()
        expected_next_reset = datetime(2024, 1, 17, 0, 0, 0, tzinfo=BEIJING).timestamp()

        with patch("lib.tushare_client.time.time", return_value=init_ts):
            client = TushareClient(token="a" * 32)
        client._daily_calls = 42

        with patch("lib.tushare_client.time.time", return_value=after_beijing_midnight):
            client._reset_daily_counter_if_needed()

        assert client._daily_calls == 0
        assert client._daily_reset_at == expected_next_reset


class TestConcurrentRateLimitCounters:
    """A1: 并发 _record_call 不得丢更新（_map_parallel 8 线程共享实例）。"""

    def test_concurrent_record_call_no_lost_update(self):
        from concurrent.futures import ThreadPoolExecutor

        from lib.tushare_client import TushareClient

        client = TushareClient(token="a" * 32, rate_limit_per_minute=10000)
        with ThreadPoolExecutor(max_workers=8) as ex:
            list(ex.map(lambda _: client._record_call("daily"), range(200)))
        assert client._daily_calls == 200
        assert len(client._call_timestamps["daily"]) == 200

    def test_concurrent_wait_for_rate_limit_no_crash(self):
        from concurrent.futures import ThreadPoolExecutor

        from lib.tushare_client import TushareClient

        client = TushareClient(token="a" * 32, rate_limit_per_minute=10000)

        def _mixed(_):
            client._record_call("daily")
            client._wait_for_rate_limit("daily")

        with ThreadPoolExecutor(max_workers=8) as ex:
            list(ex.map(_mixed, range(64)))
        assert client._daily_calls == 64
        assert len(client._call_timestamps["daily"]) == 64


class TestPerApiRateLimitBudget:
    """按接口预算：官方档位表 × 代码已有的 TUSHARE_API_MIN_POINTS 机械映射。

    口径见官方《积分与频次权限对应表》(https://tushare.pro/document/1?doc_id=290)；
    取官方值的 1/4 作保守下限（官方页未明说限额按接口还是按账号），
    并**以实例地板为下限** → 只有高积分档接口放宽，其余一字不动。
    """

    def test_tier_mapping_raises_only_high_tier_apis(self):
        from lib import tushare_client as tc

        # 5000 档 → 官方 500/min → //4 = 125 > 地板 80 ⇒ 放宽
        assert tc.rate_limit_for_api("opt_daily") == 125
        assert tc.rate_limit_for_api("sw_daily") == 125
        # 2000 档 → 200//4 = 50 < 80 ⇒ 保持地板
        assert tc.rate_limit_for_api("daily_basic") == tc.RATE_LIMIT_PER_MINUTE
        assert tc.rate_limit_for_api("fina_indicator") == tc.RATE_LIMIT_PER_MINUTE
        # 120 档 → 50//4 = 12 < 80 ⇒ 保持地板
        assert tc.rate_limit_for_api("daily") == tc.RATE_LIMIT_PER_MINUTE
        # 未登记接口 ⇒ 地板
        assert tc.rate_limit_for_api("no_such_api") == tc.RATE_LIMIT_PER_MINUTE

    def test_floor_is_honoured(self):
        from lib import tushare_client as tc

        # 地板高于档位推导值 ⇒ 用地板（gap-scan 传 180 的场景）
        assert tc.rate_limit_for_api("opt_daily", floor=180) == 180
        assert tc.rate_limit_for_api("daily", floor=300) == 300

    def test_official_tier_table(self):
        from lib import tushare_client as tc

        assert tc.official_tier_rpm(5000) == 500
        assert tc.official_tier_rpm(9999) == 500
        assert tc.official_tier_rpm(2000) == 200
        assert tc.official_tier_rpm(120) == 50
        assert tc.official_tier_rpm(100) is None  # 低于最低档

    def test_windows_are_per_api(self):
        """分桶是必需的：否则放宽 opt_daily 预算仍会被全局计数卡在 80。"""
        from lib.tushare_client import TushareClient

        client = TushareClient(token="a" * 32)
        client._record_call("opt_daily")
        client._record_call("daily")
        client._record_call("daily")
        assert len(client._call_timestamps["opt_daily"]) == 1
        assert len(client._call_timestamps["daily"]) == 2

    def test_env_override_sets_floor(self, monkeypatch):
        from lib import tushare_client as tc

        monkeypatch.setenv("TUSHARE_RATE_LIMIT_PER_MINUTE", "300")
        client = tc.TushareClient(token="a" * 32)
        assert client._rate_limit_per_minute == 300
        # 地板抬高后，2000 档接口也用 300（不再受 80 限制）
        assert tc.rate_limit_for_api("daily_basic", floor=client._rate_limit_per_minute) == 300

    def test_env_override_ignores_garbage(self, monkeypatch):
        from lib import tushare_client as tc

        for bad in ("", "abc", "0", "-5", "  "):
            monkeypatch.setenv("TUSHARE_RATE_LIMIT_PER_MINUTE", bad)
            assert tc.TushareClient(token="a" * 32)._rate_limit_per_minute == tc.RATE_LIMIT_PER_MINUTE

    def test_explicit_arg_beats_env(self, monkeypatch):
        from lib import tushare_client as tc

        monkeypatch.setenv("TUSHARE_RATE_LIMIT_PER_MINUTE", "300")
        assert tc.TushareClient(token="a" * 32, rate_limit_per_minute=180)._rate_limit_per_minute == 180

    def test_low_explicit_limit_caps_high_tier_and_total(self, monkeypatch):
        from lib import tushare_client as tc

        monkeypatch.setenv("TUSHARE_RATE_LIMIT_PER_MINUTE", "40")
        client = tc.TushareClient(token="a" * 32)
        assert client._explicit_rate_limit is True
        assert tc.rate_limit_for_api("opt_daily", floor=40, ceiling=40) == 40
        for _ in range(20):
            client._wait_for_rate_limit("daily", reserve=True)
            client._wait_for_rate_limit("daily_basic", reserve=True)
        assert client._daily_calls == 40
        assert len(client._total_call_timestamps) == 40
        with patch.object(tc.time, "sleep", side_effect=RuntimeError("throttled")):
            with pytest.raises(RuntimeError, match="throttled"):
                client._wait_for_rate_limit("opt_daily", reserve=True)

    def test_total_budget_stays_at_floor_for_low_tier_apis(self, monkeypatch):
        """默认总桶不得因档位表首项而放宽（评审 P2）。

        40 次 daily + 41 次 daily_basic = 81 次混合调用，旧共享限流器在 80 停住；
        修复前总桶被无条件取 5000 档推导值（125）→ 81 次全部放行。
        """
        from lib import tushare_client as tc
        from lib.tushare_client import TushareClient

        monkeypatch.delenv("TUSHARE_RATE_LIMIT_PER_MINUTE", raising=False)
        client = TushareClient(token="a" * 32)
        assert client._proven_points == 0
        with patch.object(tc.time, "sleep", side_effect=RuntimeError("throttled")):
            for _ in range(40):
                client._wait_for_rate_limit("daily", reserve=True)
            for _ in range(40):
                client._wait_for_rate_limit("daily_basic", reserve=True)
            assert len(client._total_call_timestamps) == 80
            with pytest.raises(RuntimeError, match="throttled"):
                client._wait_for_rate_limit("daily_basic", reserve=True)

    def test_high_tier_success_raises_total_budget(self, monkeypatch):
        """账号档位**被证明**后才抬升总桶：opt_daily 成功响应 ⇒ ≥5000 档 ⇒ 125。"""
        from lib import tushare_client as tc
        from lib.tushare_client import TushareClient

        class _Resp:
            status_code = 200

            def json(self):
                return {"code": 0, "data": {"fields": ["a"], "items": [[1]]}}

            def raise_for_status(self):
                return None

        monkeypatch.delenv("TUSHARE_RATE_LIMIT_PER_MINUTE", raising=False)
        client = TushareClient(token="a" * 32)
        client._session.post = lambda *a, **k: _Resp()
        client.query("opt_daily")
        assert client._proven_points == 5000

        before = len(client._total_call_timestamps)  # query 已预占 1 个名额
        # 总桶已抬到 125：再放行 120 次混合低档调用，一个窗口内无需等待
        # （若仍是 80，第 81 次会 sleep → 触发下面的 RuntimeError）
        with patch.object(tc.time, "sleep", side_effect=RuntimeError("throttled")):
            for _ in range(60):
                client._wait_for_rate_limit("daily", reserve=True)
                client._wait_for_rate_limit("daily_basic", reserve=True)
        assert len(client._total_call_timestamps) == before + 120

    def test_low_tier_success_does_not_raise_total_budget(self, monkeypatch):
        """低档接口成功不构成高檔位证据：2000 档推导值 50 < 地板，总桶不动。"""
        from lib import tushare_client as tc
        from lib.tushare_client import TushareClient

        class _Resp:
            status_code = 200

            def json(self):
                return {"code": 0, "data": {"fields": ["a"], "items": [[1]]}}

            def raise_for_status(self):
                return None

        monkeypatch.delenv("TUSHARE_RATE_LIMIT_PER_MINUTE", raising=False)
        client = TushareClient(token="a" * 32)
        client._session.post = lambda *a, **k: _Resp()
        client.query("daily_basic")
        assert client._proven_points == 2000
        with patch.object(tc.time, "sleep", side_effect=RuntimeError("throttled")):
            for _ in range(40):
                client._wait_for_rate_limit("daily", reserve=True)
            for _ in range(39):
                client._wait_for_rate_limit("daily_basic", reserve=True)
            assert len(client._total_call_timestamps) == 80
            with pytest.raises(RuntimeError, match="throttled"):
                client._wait_for_rate_limit("daily", reserve=True)


class TestTokenResolution:
    """#15：token 三级降级（显式 → os.environ → .env 文件）。"""

    def test_explicit_token_wins(self, monkeypatch):
        from lib.tushare_client import TushareClient

        monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
        client = TushareClient(token="b" * 32)
        assert client._token == "b" * 32

    def test_env_var_used_when_no_explicit_token(self, monkeypatch):
        from lib.tushare_client import TushareClient

        monkeypatch.setenv("TUSHARE_TOKEN", "c" * 32)
        client = TushareClient()
        assert client._token == "c" * 32

    def test_env_file_fallback_when_no_env_var(self, monkeypatch):
        """.env 文件 token（env.get_config 惰性加载）→ 裸 TushareClient() 不再静默缺 token。"""
        from lib.tushare_client import TushareClient

        monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
        # D13：patch 目标 = 消费方查找点（__init__ 内 `from lib import env` 经
        # 包命名空间解析 → patch lib.env.get_config 有效）
        with patch("lib.env.get_config", return_value={"TUSHARE_TOKEN": "d" * 32}):
            client = TushareClient()
        assert client._token == "d" * 32

    def test_no_token_anywhere_stays_none(self, monkeypatch):
        from lib.tushare_client import TushareClient

        monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
        with patch("lib.env.get_config", return_value={}):
            client = TushareClient()
        assert client._token is None
        assert client.is_available() is False  # 零网络快路径
