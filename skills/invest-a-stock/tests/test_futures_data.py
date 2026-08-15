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
        rows = fd.fetch_contract(_WindowFakeClient(), "IF2608.CFX", "2026-07-17", "2026-08-21")
        assert [r["date"] for r in rows] == ["2026-08-14", "2026-07-25"]

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
        assert len(dates) == len(all_rows)  # 相邻合约重叠日只归一份（无重复）
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
