"""code-review 清理 D1：_sources run 级覆盖缓存（quote 10 日 ⊂ kline 400 日）。

FakeTC 计数断言：同 run 内宽窗口先拉后，窄窗口切片命中不再发网络调用。
"""

from __future__ import annotations

import sys
from contextlib import nullcontext
from pathlib import Path

import pandas as pd
import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from lib.collector import _sources as src  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_run_cache(monkeypatch):
    """pytest 同进程同日：模块级缓存跨测试污染必须显式重置。"""
    monkeypatch.setattr(src, "_run_kline_quote_cache", {})
    monkeypatch.setattr(src, "_run_kline_quote_cache_day", "")
    yield


@pytest.fixture
def _install_fake_tc(monkeypatch):
    def _install(tc):
        monkeypatch.setattr(src, "_require_tushare", lambda: (None, tc))
        return tc
    return _install


class _CountingTC:
    """按 api 计数 + 按窗口过滤行（模拟 tushare 行为）。"""

    def __init__(self, rows_by_api: dict[str, pd.DataFrame]):
        self.rows_by_api = rows_by_api
        self.calls: list[tuple[str, str, str]] = []  # (api, sd, ed)

    def query(self, api, **kw):
        df = self.rows_by_api.get(api)
        sd, ed = str(kw.get("start_date", "")), str(kw.get("end_date", ""))
        self.calls.append((api, sd, ed))
        if df is None or df.empty:
            return df
        out = df
        if sd:
            out = out[out["trade_date"].astype(str) >= sd]
        if ed:
            out = out[out["trade_date"].astype(str) <= ed]
        return out if not out.empty else pd.DataFrame()


def _daily_rows(n: int = 20) -> pd.DataFrame:
    return pd.DataFrame([
        {"trade_date": f"202606{1 + i:02d}", "open": 10.0, "high": 10.5,
         "low": 9.8, "close": 10.2, "vol": 100.0}
        for i in range(n)
    ])


class TestTushareDailyCoverage:
    def test_wide_then_narrow_single_fetch(self, _install_fake_tc):
        tc = _CountingTC({"daily": _daily_rows()})
        _install_fake_tc(tc)
        wide = src._q_tushare_daily("600176", start_date="20260201", end_date="20260620")
        assert wide is not None and len(wide) == 20
        narrow = src._q_tushare_daily("600176", start_date="20260610", end_date="20260620")
        assert len(tc.calls) == 1  # 命中缓存，未再发网络
        assert narrow is not None
        assert narrow[0]["trade_date"] == "20260610"
        assert narrow[-1]["trade_date"] == "20260620"
        assert len(narrow) == 11

    def test_narrow_then_wide_replaces_then_narrow_hits(self, _install_fake_tc):
        tc = _CountingTC({"daily": _daily_rows()})
        _install_fake_tc(tc)
        n1 = src._q_tushare_daily("600176", start_date="20260610", end_date="20260620")
        assert n1 is not None and len(n1) == 11
        wide = src._q_tushare_daily("600176", start_date="20260201", end_date="20260620")
        assert len(wide) == 20  # 缓存不覆盖 → 二次 fetch 并替换为更宽窗口
        n2 = src._q_tushare_daily("600176", start_date="20260610", end_date="20260620")
        assert len(tc.calls) == 2  # 第三次命中
        assert n2[0]["trade_date"] == n1[0]["trade_date"]

    def test_covered_but_no_rows_returns_none(self, _install_fake_tc):
        """覆盖窗口内 0 行（停牌/假期）：切片 [] 必须返回 None 而非 [] ——
        [] 会被 _run_one_source 判为成功，级联降级链不再触发（code-review）。"""
        tc = _CountingTC({"daily": pd.DataFrame([
            {"trade_date": f"202606{1 + i:02d}", "open": 10.0, "high": 10.5,
             "low": 9.8, "close": 10.2, "vol": 100.0}
            for i in range(5)
        ])})
        _install_fake_tc(tc)
        wide = src._q_tushare_daily("600176", start_date="20260501", end_date="20260620")
        assert wide is not None and len(wide) == 5
        gap = src._q_tushare_daily("600176", start_date="20260610", end_date="20260620")
        assert gap is None  # 修复前返回 []（判成功）

    def test_overlap_replacement_only_on_superset(self, _install_fake_tc):
        """替换仅当新窗口为缓存窗口超集：起始更早但结束更早的历史窗口不得
        驱逐含最新日的缓存（code-review：任一边更宽就替换会反复重拉）。"""
        tc = _CountingTC({"daily": _daily_rows()})
        _install_fake_tc(tc)
        wide = src._q_tushare_daily("600176", start_date="20260601", end_date="20260620")
        assert wide is not None and len(wide) == 20
        hist = src._q_tushare_daily("600176", start_date="20260501", end_date="20260610")
        assert hist is not None and len(hist) == 10  # 缓存不覆盖 → fetch，但不得替换
        again = src._q_tushare_daily("600176", start_date="20260601", end_date="20260620")
        assert again is not None and len(again) == 20
        assert len(tc.calls) == 2  # 修复前：hist 替换缓存 → again 第三次 fetch

    def test_none_result_not_cached(self, _install_fake_tc):
        tc = _CountingTC({"daily": pd.DataFrame()})
        _install_fake_tc(tc)
        assert src._q_tushare_daily("600176", start_date="20260601", end_date="20260620") is None
        assert src._q_tushare_daily("600176", start_date="20260601", end_date="20260620") is None
        assert len(tc.calls) == 2  # None 不落缓存 → 每次都重新 fetch

    def test_empty_string_window_not_cached(self, _install_fake_tc):
        """_q_tushare_daily_qfq 默认传 ""（无窗口语义）→ 直连不缓存。"""
        tc = _CountingTC({"daily": _daily_rows()})
        _install_fake_tc(tc)
        assert src._q_tushare_daily("600176", start_date="", end_date="") is not None
        assert src._q_tushare_daily("600176", start_date="", end_date="") is not None
        assert len(tc.calls) == 2

    def test_day_rollover_invalidates(self, _install_fake_tc, monkeypatch):
        tc = _CountingTC({"daily": _daily_rows()})
        _install_fake_tc(tc)
        monkeypatch.setattr(src, "_today", lambda: "20260601")
        src._q_tushare_daily("600176", start_date="20260601", end_date="20260620")
        monkeypatch.setattr(src, "_today", lambda: "20260602")
        src._q_tushare_daily("600176", start_date="20260601", end_date="20260620")
        assert len(tc.calls) == 2  # 跨日清空 → 重新 fetch

    def test_disabled_flag_fetches_every_time(self, _install_fake_tc, monkeypatch):
        monkeypatch.setenv("INVEST_QUOTE_CACHE", "0")
        tc = _CountingTC({"daily": _daily_rows()})
        _install_fake_tc(tc)
        src._q_tushare_daily("600176", start_date="20260601", end_date="20260620")
        src._q_tushare_daily("600176", start_date="20260601", end_date="20260620")
        assert len(tc.calls) == 2

    def test_slice_and_fetch_copy_do_not_pollute_cache(self, _install_fake_tc):
        tc = _CountingTC({"daily": _daily_rows()})
        _install_fake_tc(tc)
        wide = src._q_tushare_daily("600176", start_date="20260201", end_date="20260620")
        wide[0]["close"] = 999.0  # mutate 首次返回（应为副本）
        narrow = src._q_tushare_daily("600176", start_date="20260610", end_date="20260620")
        narrow[0]["close"] = 888.0  # mutate 切片（应为副本）
        again = src._q_tushare_daily("600176", start_date="20260610", end_date="20260620")
        assert again[0]["close"] == 10.2
        assert again[-1]["close"] == 10.2


class TestTushareAdjFactorCoverage:
    def test_wide_then_narrow_single_fetch(self, _install_fake_tc):
        rows = pd.DataFrame([
            {"trade_date": f"202606{1 + i:02d}", "adj_factor": 1.0 + i * 0.01}
            for i in range(20)
        ])
        tc = _CountingTC({"adj_factor": rows})
        _install_fake_tc(tc)
        wide = src._q_tushare_adj_factor("600176", start_date="20260201", end_date="20260620")
        assert wide is not None and len(wide) == 20
        narrow = src._q_tushare_adj_factor("600176", start_date="20260610", end_date="20260620")
        assert len(tc.calls) == 1
        assert set(narrow.keys()) == {f"202606{i:02d}" for i in range(10, 21)}


class TestDate8Normalization:
    """_date8 只去横杠：斜杠/带时间戳/float-str 日期会词法比较出错、丢边界行。"""

    def test_slash_and_timestamp_formats(self):
        assert src._date8("2026/06/01") == "20260601"
        assert src._date8("2026-06-01 00:00:00") == "20260601"
        assert src._date8("20260813.0") == "20260813"
        assert src._date8(20260601) == "20260601"
        assert src._date8(None) == ""
        assert src._date8("") == ""

    def test_slice_includes_slash_dated_boundary_rows(self):
        rows = [{"trade_date": "2026/06/01", "close": 1.0},
                {"trade_date": "2026-06-01 00:00:00", "close": 2.0}]
        sliced = src._slice_cached_rows(rows, "20260601", "20260601")
        assert len(sliced) == 2  # 修复前 0（边界行被词法比较排除）


class TestAkshareKlineCoverage:
    def _install_fake_ak(self, monkeypatch):
        class _FakeAk:
            calls = 0

            @classmethod
            def stock_zh_a_hist(cls, **kw):
                cls.calls += 1
                rows = [
                    {"日期": f"2026-06-{1 + i:02d}", "开盘": 10.0, "最高": 10.5,
                     "最低": 9.8, "收盘": 10.2, "成交量": 100.0}
                    for i in range(20)
                ]
                sd, ed = kw.get("start_date"), kw.get("end_date")
                if sd:
                    rows = [r for r in rows if r["日期"] >= sd]
                if ed:
                    rows = [r for r in rows if r["日期"] <= ed]
                return pd.DataFrame(rows)

        monkeypatch.setitem(sys.modules, "akshare", _FakeAk)
        monkeypatch.setattr(src, "akshare_direct_session", lambda: nullcontext())
        return _FakeAk

    def test_iso_dates_slice_and_require_ed_equal(self, monkeypatch):
        fake_ak = self._install_fake_ak(monkeypatch)
        wide = src._q_akshare_kline("600176", start_date="20260201", end_date="20260620")
        assert wide is not None and len(wide) == 20
        assert wide[0]["trade_date"] == "2026-06-01"
        narrow = src._q_akshare_kline("600176", start_date="20260610", end_date="20260620")
        assert fake_ak.calls == 1  # 同结束日 → 切片命中
        assert narrow is not None
        assert narrow[0]["trade_date"] == "2026-06-10"
        assert narrow[-1]["trade_date"] == "2026-06-20"
        # 结束日不同：qfq 锚定日不同 → require_ed_equal 拒绝切片 → 重新 fetch
        other = src._q_akshare_kline("600176", start_date="20260601", end_date="20260610")
        assert fake_ak.calls == 2
        assert other is not None and len(other) == 10
