"""僵尸线程缓存回归：_run_in_thread 超时后，迟到完成的线程（zombie）不得
把结果写进同日 pickle 缓存（collect_kline_cache/{yyyymmdd}/...）。

缺陷（code-review 确认）：INVEST_SOURCE_TIMEOUT=30 时 akshare kline 重试梯
约 47s → cascade 30s 超时转 baostock（成功）；僵尸 tushare 线程约 47s 完成
并把 tushare.daily 写进同日缓存 → 当日第二次 collect 静默命中僵尸条目
（源已标记死但被缓存复活），且僵尸线程仍继续发东财网络调用。

修复：超时时 `_run_in_thread` 给线程对象置 `abandoned` 标记；`_kline_cache`
在落盘前（`if data:` 保存门）检查该标记并跳过。
"""
from __future__ import annotations

import threading
import time


class TestZombieThreadNoCacheWrite:
    def test_timed_out_thread_does_not_write_cache(self, tmp_path, monkeypatch):
        """超时后僵尸线程迟到完成：pickle 缓存不得被写入。"""
        from lib.collector import _kline_cache
        from lib.collector._base import _run_in_thread

        monkeypatch.setattr("lib.env.STORE_DIR", tmp_path)

        zombie_thread: list[threading.Thread] = []

        def _slow_fetch() -> list[dict] | None:
            zombie_thread.append(threading.current_thread())
            time.sleep(0.3)  # 慢于 timeout → 调用方超时放弃
            return [{"trade_date": "20260808", "close": 10.0}]

        def _cached_fn() -> list[dict] | None:
            return _kline_cache.load_or_fetch(
                "600176", "tushare.daily", "20260101", "20260808", _slow_fetch)

        data, err = _run_in_thread(_cached_fn, timeout_sec=0.05, label="test-zombie")
        assert err is not None
        assert "timeout" in str(err)
        assert data is None  # 调用方仍拿 timeout 占位（降级行为不变）

        # 等僵尸线程完整走完（fetch 迟到结束 + 落盘判断）再断言，消除竞态
        zombie_thread[0].join(timeout=5)
        assert not zombie_thread[0].is_alive()
        assert list(tmp_path.rglob("*.pkl")) == []  # 僵尸结果不得落盘

    def test_normal_thread_still_writes_cache(self, tmp_path, monkeypatch):
        """非超时路径（线程无 abandoned 标记）：缓存照常写入（回归护栏）。"""
        from lib.collector import _kline_cache

        monkeypatch.setattr("lib.env.STORE_DIR", tmp_path)

        data = _kline_cache.load_or_fetch(
            "600176", "tushare.daily", "20260101", "20260808",
            lambda: [{"trade_date": "20260808", "close": 10.0}])
        assert data is not None
        assert len(list(tmp_path.rglob("*.pkl"))) == 1
