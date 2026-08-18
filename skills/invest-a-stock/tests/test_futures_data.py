"""futures_data 数据层测试 — mock TushareClient，不联网。"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from lib import store  # noqa: E402
from lib import futures_data as fd  # noqa: E402


class _FakeClient:
    def __init__(self):
        self.queries: list[tuple] = []

    def query(self, api_name, **kwargs):
        self.queries.append((api_name, kwargs))
        if api_name == "fut_basic":
            return pd.DataFrame({
                "ts_code": ["IF2608.CFX", "IF2609.CFX", "IC2608.CFX", "IM2608.CFX"],
                "list_date": ["20260701"] * 4,
                "delist_date": ["20260831"] * 4,
                "last_trade_date": ["20260821", "20260918", "20260821", "20260821"],
            })
        if api_name == "fut_daily":
            code = kwargs["ts_code"]
            return pd.DataFrame({
                "trade_date": ["20260814", "20260813"],
                "settle": [4652.4, 4650.0],
                "open": [1, 1], "high": [1, 1], "low": [1, 1], "close": [4648.4, 4646.0],
                "oi": [33117.0, 34433.0],
                "oi_chg": [-1316.0, -900.0],
            })
        return pd.DataFrame()


class _WindowFakeClient(_FakeClient):
    def query(self, api_name, **kwargs):
        if api_name == "fut_daily":
            return pd.DataFrame({
                "trade_date": ["20260825", "20260814", "20260725", "20260710"],
                "settle": [4652.4, 4650.0, 4600.0, 4590.0],
                "open": [1, 1, 1, 1], "high": [1, 1, 1, 1], "low": [1, 1, 1, 1],
                "close": [4648.4, 4646.0, 4596.0, 4586.0],
                "oi": [33117.0] * 4, "oi_chg": [-100.0] * 4,
            })
        return super().query(api_name, **kwargs)


class _FullFakeClient(_FakeClient):
    """每合约返回 2026-06-01..2026-09-30 全部工作日行（模拟完整生命周期）。"""
    def query(self, api_name, **kwargs):
        if api_name == "fut_daily":
            import datetime
            dates = []
            d0 = datetime.date(2026, 6, 1)
            while d0 <= datetime.date(2026, 9, 30):
                if d0.weekday() < 5:
                    dates.append(d0.isoformat().replace("-", ""))
                d0 += datetime.timedelta(days=1)
            n = len(dates)
            return pd.DataFrame({
                "trade_date": dates,
                "settle": [4600.0] * n, "open": [1.0] * n, "high": [1.0] * n,
                "low": [1.0] * n, "close": [4596.0] * n, "oi": [30000.0] * n,
                "oi_chg": [0.0] * n,
            })
        return super().query(api_name, **kwargs)


class TestContractSeries:
    def test_series_from_codes(self):
        client = _FakeClient()
        series = fd.contract_series(client)
        assert series["IF"] == [("IF2608.CFX", "2026-08-21"), ("IF2609.CFX", "2026-09-18")]
        assert "IC" in series and "IM" in series
        assert "T1" not in series

    def test_expiry_fallback_third_friday(self):
        # fut_basic 无 last_trade_date/last_ddate → 兜底计算该月第三个周五
        assert fd._third_friday("2608") == "2026-08-21"
        assert fd._third_friday("1504") == "2015-04-17"  # IF1504 真实到期日


class TestComputeBasis:
    def test_basis_and_oi_change(self):
        rows = [{
            "date": "2026-08-14", "symbol": "IF", "contract": "IF2608.CFX",
            "settle": 4652.4, "close": 4648.4, "oi": 33117.0, "oi_chg": -1316.0,
            "source": "tushare",
        }]
        out = fd.compute_basis(rows, {"2026-08-14": 4665.881})
        assert out[0]["basis_pts"] == pytest.approx(-13.48, abs=0.01)
        assert out[0]["basis_pct"] == pytest.approx(-0.2889, abs=0.001)
        assert out[0]["oi_change_pct"] == pytest.approx(-1316 / 34433 * 100, abs=0.01)

    def test_missing_index_dropped(self):
        rows = [{"date": "2026-08-14", "symbol": "IF", "contract": "IF2608.CFX",
                 "settle": 4652.4, "close": 4648.4, "oi": 1.0, "oi_chg": None,
                 "source": "tushare"}]
        assert fd.compute_basis(rows, {}) == []


class TestFetchContractWindow:
    def test_window_partition(self):
        rows = fd.fetch_contract(_FakeClient(), "IF2608.CFX", "2026-07-17", "2026-08-21")
        assert all("2026-07-17" < r["date"] <= "2026-08-21" for r in rows)
        assert all(r["symbol"] == "IF" for r in rows)
        assert len(rows) == 2

    def test_window_excludes_outside_rows(self):
        # fetch_contract 不排序（保留供应商行序）——断言窗口内容而非行序，
        # 避免与 tushare DESC-by-trade_date 约定耦合
        rows = fd.fetch_contract(_WindowFakeClient(), "IF2608.CFX", "2026-07-17", "2026-08-21")
        assert sorted(r["date"] for r in rows) == ["2026-07-25", "2026-08-14"]

    def test_front_month_series_no_gap(self):
        """回归（finding #1）：月内到期日→月末的交易日必须由下一合约补齐。"""
        all_rows = []
        contracts = [("IF2607.CFX", "2026-07-17"), ("IF2608.CFX", "2026-08-21"),
                     ("IF2609.CFX", "2026-09-18")]
        client = _FullFakeClient()
        prev = "2026-05-31"
        for code, expiry in contracts:
            rows = fd.fetch_contract(client, code, prev, expiry)
            all_rows.extend(rows)
            prev = expiry
        dates = sorted(r["date"] for r in all_rows)
        # 相邻合约重叠日只归一份：日期集合去重后长度不变（sorted 不改变
        # 长度，len(dates)==len(all_rows) 恒真——重复日必须用 set 判定）
        assert len(dates) == len(set(dates))
        assert any("2026-07-18" <= d <= "2026-07-31" for d in dates)  # 旧实现此处为洞


class TestStoreRoundtrip:
    def test_save_load(self, isolated_store):
        rows = [{
            "date": "2026-08-14", "symbol": "IF", "contract": "IF2608.CFX",
            "open": 1, "high": 1, "low": 1, "close": 4648.4, "settle": 4652.4,
            "oi": 33117.0, "oi_chg": -1316.0,
            "basis_pts": -13.48, "basis_pct": -0.2889, "oi_change_pct": -3.82,
            "source": "tushare",
        }]
        n = store.save_futures_daily(rows)
        assert n == 1
        loaded = store.load_futures_daily(symbol="IF")
        assert loaded[0]["basis_pct"] == pytest.approx(-0.2889)
        assert store.latest_futures_date() == "2026-08-14"
        assert store.futures_contracts() == {"IF2608.CFX"}


class TestClearFuturesDaily:
    def test_clear(self, isolated_store):
        rows = [{
            "date": "2026-08-14", "symbol": "IF", "contract": "IF2608.CFX",
            "open": 1, "high": 1, "low": 1, "close": 4648.4, "settle": 4652.4,
            "oi": 33117.0, "oi_chg": -1316.0,
            "basis_pts": -13.48, "basis_pct": -0.2889, "oi_change_pct": -3.82,
            "source": "tushare",
        }]
        store.save_futures_daily(rows)
        assert store.clear_futures_daily() == 1
        assert store.load_futures_daily(symbol="IF") == []


class TestCompoundOiChange:
    def test_all_valid_20(self):
        assert fd.compound_oi_change([1.0] * 20) == pytest.approx(22.019004, abs=1e-4)

    def test_masked_and_none_excluded_from_count_and_product(self):
        # 18 个 +1% + 1 个 -100（到期塌缩掩码）+ 1 个 None → 有效 18 ≥ 18 → 18 日复利
        assert fd.compound_oi_change([1.0] * 18 + [-100.0, None]) == pytest.approx(19.614748, abs=1e-4)

    def test_below_min_valid_returns_none(self):
        assert fd.compound_oi_change([1.0] * 17 + [None] * 3) is None

    def test_window_trims_to_tail(self):
        assert fd.compound_oi_change([5.0] * 5 + [1.0] * 15) == pytest.approx(48.172327, abs=1e-4)

    def test_nan_excluded(self):
        # DB 经 DataFrame 读取时 None 变 NaN——NaN 与 None 同等待遇：
        # 2 个 NaN 不计入 → 18 个有效 +1% 因子 → 18 日复利
        assert fd.compound_oi_change([float("nan")] * 2 + [1.0] * 18) == pytest.approx(19.614748, abs=1e-4)


def _seed_row(date: str, symbol: str, contract: str) -> dict:
    return {"date": date, "symbol": symbol, "contract": contract,
            "open": 1, "high": 1, "low": 1, "close": 100.0, "settle": 101.0,
            "oi": 1000.0, "oi_chg": 10.0, "source": "tushare",
            "basis_pts": 1.0, "basis_pct": 1.0, "oi_change_pct": 1.0}


def _idx_map() -> dict[str, dict[str, float]]:
    """compute_basis 用现货收盘：08-13/08-14/08-17 全品种。"""
    closes = {"2026-08-13": 100.0, "2026-08-14": 100.0, "2026-08-17": 100.0}
    return {sym: dict(closes) for sym in ("IF", "IH", "IC", "IM")}


def _daily_row(trade_date: str) -> dict:
    return {"trade_date": trade_date, "settle": 101.0, "open": 1, "high": 1,
            "low": 1, "close": 100.0, "oi": 1000.0, "oi_chg": 10.0}


class _EnsureClient(_FakeClient):
    """ensure_futures_daily 专用：fut_daily 按 ts_code 返回，可指定失败合约。"""
    def __init__(self, daily: dict[str, list[dict]] | None = None,
                 fail: set[str] | None = None):
        super().__init__()
        self.daily = daily or {}
        self.fail = fail or set()

    def query(self, api_name, **kwargs):
        self.queries.append((api_name, kwargs))
        if api_name == "fut_daily":
            code = kwargs["ts_code"]
            if code in self.fail:
                raise RuntimeError(f"boom {code}")
            rows = self.daily.get(code, [])
            return pd.DataFrame(rows) if rows else pd.DataFrame()
        return super().query(api_name, **kwargs)


class TestEnsureFuturesDaily:
    """ensure_futures_daily 重构回归（findings #1/#2/#5/#6）。"""

    def test_force_tushare_failure_preserves_existing(self, isolated_store, monkeypatch):
        """回归（finding #2）：force 先清库后验源 → tushare 全挂时 9258 行
        settle 口径数据被毁。修复：取数成功前不清空，旧数据保留。"""
        store.save_futures_daily([_seed_row("2026-08-14", "IF", "IF2608.CFX")])

        def boom():
            raise RuntimeError("TUSHARE_TOKEN 未配置")

        monkeypatch.setattr(fd, "_make_client", boom)
        monkeypatch.setattr(fd, "fetch_sina_fallback", lambda: [])  # 免联网
        result = fd.ensure_futures_daily(force=True)
        assert result["failed"]
        assert store.load_futures_daily()  # 旧数据未被清空

    def test_force_cap_below_needed_aborts_without_clear(self, isolated_store, monkeypatch):
        """回归（finding #1）：force + max_contracts 不足 → 旧实现静默截断
        （尾部品种表已清空却 0 合约入库，failed={} 退出码 0）。修复：清空前
        报 error 中止。"""
        store.save_futures_daily([_seed_row("2026-08-14", "IF", "IF2608.CFX")])
        monkeypatch.setattr(fd, "_make_client", lambda: _FakeClient())
        monkeypatch.setattr(fd, "fetch_sina_fallback", lambda: [])
        result = fd.ensure_futures_daily(force=True, max_contracts=3)  # 需要 4
        assert result.get("error")
        assert store.load_futures_daily()  # 未清空

    def test_force_success_clears_and_writes(self, isolated_store, monkeypatch):
        """force 成功路径：取数暂存 → clear → 写回；旧脏行清除。"""
        store.save_futures_daily([_seed_row("2026-01-01", "IF", "IF2608.CFX")])
        monkeypatch.setattr(fd, "_make_client", lambda: _FakeClient())
        monkeypatch.setattr(fd, "fetch_index_close_map", lambda: _idx_map())
        result = fd.ensure_futures_daily(force=True, max_contracts=10)
        assert result["failed"] == {}
        dates = {r["date"] for r in store.load_futures_daily(limit=100)}
        assert "2026-01-01" not in dates  # 旧行已清
        assert "2026-08-14" in dates  # 新行已写

    def test_incremental_backfills_front_contract_tail(self, isolated_store, monkeypatch):
        """回归（finding #6）：已入库前端合约被整体跳过 → 到期日前新增
        交易日永久缺失。修复：existing 合约仅回填尾部窗口（入库最新日, 到期日]。"""
        store.save_futures_daily([_seed_row("2026-08-14", "IF", "IF2608.CFX")])
        client = _EnsureClient(daily={"IF2608.CFX": [_daily_row("20260817")]})
        monkeypatch.setattr(fd, "_make_client", lambda: client)
        monkeypatch.setattr(fd, "fetch_index_close_map", lambda: _idx_map())
        fd.ensure_futures_daily(max_contracts=10)
        dates = [r["date"] for r in store.load_futures_daily(symbol="IF", limit=100)]
        assert "2026-08-17" in dates  # 尾部回填

    def test_incremental_skips_complete_existing_contracts(self, isolated_store, monkeypatch):
        """设计约束：已入库至到期日的合约不再发 fut_daily 请求（增量快速路径）。"""
        store.save_futures_daily([
            _seed_row("2026-08-14", "IF", "IF2608.CFX"),
            _seed_row("2026-08-21", "IF", "IF2608.CFX"),
        ])
        client = _EnsureClient()
        monkeypatch.setattr(fd, "_make_client", lambda: client)
        monkeypatch.setattr(fd, "fetch_index_close_map", lambda: _idx_map())
        fd.ensure_futures_daily(max_contracts=10)
        called = [q[1].get("ts_code") for q in client.queries if q[0] == "fut_daily"]
        assert "IF2608.CFX" not in called

    def test_force_failed_contract_gap_covered_by_next(self, isolated_store, monkeypatch):
        """回归（finding #5）：逐合约失败仍推进 prev_lt → 失败合约窗口成洞。
        修复：不推进 prev_lt，下一合约窗口覆盖失败合约缺口。"""
        client = _EnsureClient(
            daily={"IF2609.CFX": [_daily_row("20260814"), _daily_row("20260813")]},
            fail={"IF2608.CFX"})
        monkeypatch.setattr(fd, "_make_client", lambda: client)
        monkeypatch.setattr(fd, "fetch_index_close_map", lambda: _idx_map())
        result = fd.ensure_futures_daily(force=True, max_contracts=10)
        assert "IF2608.CFX" in result["failed"]
        dates = {r["date"] for r in store.load_futures_daily(symbol="IF", limit=100)}
        assert "2026-08-14" in dates  # 失败合约窗口由 IF2609 覆盖
