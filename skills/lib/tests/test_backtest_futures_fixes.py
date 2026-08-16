"""backtest_futures 修复回归测试 — fixture，不联网。

覆盖（finding #7/#8 + look-ahead）：F2 日期守卫（KeyError 修复）、
F3 日历对齐 helper（基差/收益共用指数交易日历）。
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pandas as pd
import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
for _p in (str(_REPO_ROOT / "skills"), str(_REPO_ROOT / "skills" / "lib"),
           str(_REPO_ROOT / "skills" / "invest-a-stock" / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# 'lib' 包名二义性（skills/lib vs invest-a-stock/scripts/lib）：pytest 按
# testpaths 导入本测试模块时可能先把 skills/lib 绑定为 'lib'（全量套件因
# invest-a-stock conftest 先行不受影响）——而 backtest_futures 的
# _oi_20d_series 惰性导入的是 invest-a-stock 的 lib.futures_data。
# 单文件运行时显式重绑定为 canonical invest-a-stock lib 包。
_stock_lib = _REPO_ROOT / "skills" / "invest-a-stock" / "scripts" / "lib"
_lib = sys.modules.get("lib")
if _lib is None or not hasattr(_lib, "__file__") \
        or _lib.__file__ != str(_stock_lib / "__init__.py"):
    _pkg_spec = importlib.util.spec_from_file_location("lib", _stock_lib / "__init__.py")
    _pkg = importlib.util.module_from_spec(_pkg_spec)
    sys.modules["lib"] = _pkg
    assert _pkg_spec.loader is not None
    _pkg_spec.loader.exec_module(_pkg)

_spec = importlib.util.spec_from_file_location(
    "backtest_futures", _REPO_ROOT / "scripts" / "backtest_futures.py")
bt = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(bt)


class TestBasisStateAfter:
    def test_converge_and_uptick(self):
        dates = [f"2026-01-{i + 1:02d}" for i in range(30)]
        closes = {d: 100.0 + i for i, d in enumerate(dates)}
        all_dates = sorted(closes)
        basis_map = {"2026-01-05": -10.0, "2026-01-25": -5.0}
        st = bt._basis_state_after(basis_map, closes, all_dates, "2026-01-05")
        assert st[0] == "converge"
        assert st[1] == pytest.approx(19.2308, abs=1e-4)

    def test_target_date_missing_in_futures_calendar(self):
        closes = {d: 100.0 for d in [f"2026-01-{i + 1:02d}" for i in range(30)]}
        all_dates = sorted(closes)
        basis_map = {"2026-01-05": -10.0}  # 目标日 01-25 不在期货日历
        assert bt._basis_state_after(basis_map, closes, all_dates, "2026-01-05") is None

    def test_event_date_outside_index_calendar(self):
        closes = {d: 100.0 for d in [f"2026-01-{i + 1:02d}" for i in range(30)]}
        all_dates = sorted(closes)
        assert bt._basis_state_after({}, closes, all_dates, "2025-12-31") is None

    def test_horizon_beyond_end(self):
        closes = {d: 100.0 for d in [f"2026-01-{i + 1:02d}" for i in range(10)]}
        all_dates = sorted(closes)
        assert bt._basis_state_after({}, closes, all_dates, "2026-01-05") is None


class TestRunF2Guard:
    def test_futures_date_missing_from_index_calendar_skipped(self, monkeypatch, tmp_path):
        """回归（finding #8 + #10）：事件日不在指数收盘日历 → 跳过而非 KeyError；
        n_events 只计入进入前向统计的事件（日历守卫跳过的不计，与 +5 n 一致）。"""
        n = 120
        dates = [f"2026-01-{i + 1:02d}" for i in range(n)]
        basis = [0.5] * n
        basis[60] = -5.0  # expanding 分位最低 → 唯一 deep_discount 事件日
        fdf = pd.DataFrame({"date": dates, "basis_pct": basis})
        monkeypatch.setattr(bt, "load_futures_df", lambda sym: fdf)
        idx_closes = {d: 100.0 + i for i, d in enumerate(dates) if d != dates[60]}
        monkeypatch.setattr(bt, "load_index_closes", lambda code: idx_closes)
        out = tmp_path / "f2.json"
        bt.run_f2(out)  # 修复前在此抛 KeyError
        assert out.exists()
        res = json.loads(out.read_text(encoding="utf-8"))
        entry = res["scenarios"]["IF"]
        assert entry["n_events"]["deep_discount"] == 0  # 唯一事件日被日历守卫跳过
        assert "+5" not in entry["deep_discount"]


class TestRunF2NoneGuard:
    def test_nan_basis_row_does_not_crash(self, monkeypatch, tmp_path):
        """回归（finding #4）：basis_pct NaN → expanding 分位输出 None，
        cond(None)（None < 10）修复前抛 TypeError → 整个 F2 中止。"""
        n = 45
        dates = [f"2026-01-{i + 1:02d}" for i in range(n)]
        basis = [0.5] * n
        basis[5] = float("nan")
        fdf = pd.DataFrame({"date": dates, "basis_pct": basis})
        monkeypatch.setattr(bt, "load_futures_df", lambda sym: fdf)
        idx_closes = {d: 100.0 + i for i, d in enumerate(dates)}
        monkeypatch.setattr(bt, "load_index_closes", lambda code: idx_closes)
        out = tmp_path / "f2.json"
        bt.run_f2(out)  # 修复前在此抛 TypeError
        assert out.exists()
        res = json.loads(out.read_text(encoding="utf-8"))
        entry = res["scenarios"]["IF"]
        # 暖机后平坦基差恒 p=100 → 首个升水事件在第 30 个有效样本（dates[30]）
        assert entry["n_events"]["premium"] == 1


class TestRunF2Warmup:
    def test_no_phantom_premium_at_series_start(self, monkeypatch, tmp_path):
        """回归（finding #3）：首行 inclusive 分位恒 100 → 幻影升水事件。
        修复（min_history=30 暖机）后首行无分位；判别用指数日历截断——
        修复前事件在 dates[0]（有前向窗口 → +5 有值），修复后事件在
        dates[30]（不在指数日历 → 跳过）→ n_events 0、无 +5。"""
        n = 40
        dates = [f"2026-01-{i + 1:02d}" for i in range(n)]
        basis = [100.0] + [50.0] * (n - 1)  # 首日极值：修复前唯一幻影事件源
        fdf = pd.DataFrame({"date": dates, "basis_pct": basis})
        monkeypatch.setattr(bt, "load_futures_df", lambda sym: fdf)
        idx_closes = {d: 100.0 + i for i, d in enumerate(dates[:25])}
        monkeypatch.setattr(bt, "load_index_closes", lambda code: idx_closes)
        out = tmp_path / "f2.json"
        bt.run_f2(out)
        res = json.loads(out.read_text(encoding="utf-8"))
        entry = res["scenarios"]["IF"]
        assert entry["n_events"]["premium"] == 0
        assert "+5" not in entry["premium"]


class TestRunF1QuartileGuard:
    def test_nan_basis_not_bucketed_and_warmup_excluded(self, monkeypatch, tmp_path):
        """回归（finding #7）：NaN 基差日 pd.cut 得 NaN → str 后入幻影 'nan' 桶；
        修复后无 quartile（跳过），暖机期（<30 有效样本）亦不入桶。"""
        n = 40
        dates = [f"2026-01-{i + 1:02d}" for i in range(n)]
        basis = [0.5] * n
        basis[10] = float("nan")
        fdf = pd.DataFrame({"date": dates, "basis_pct": basis})
        monkeypatch.setattr(bt, "load_futures_df", lambda sym: fdf)
        closes = {d: 100.0 + i for i, d in enumerate(dates)}
        closes["2026-02-10"] = 140.0  # dates[39] 的次日 → 末行亦有 +1 前向
        monkeypatch.setattr(bt, "load_etf_closes", lambda etf: closes)
        out = tmp_path / "f1.json"
        bt.run_f1(out)
        res = json.loads(out.read_text(encoding="utf-8"))
        quartiles = res["etfs"]["IC_510500"]["quartiles"]
        assert "nan" not in quartiles
        n_bucketed = sum(v["+1"]["n"] for v in quartiles.values() if "+1" in v)
        # 40 行 − 1 NaN 行 = 39 有效样本 → 前 29 个有效样本暖机无分位 → 10 行入桶
        assert n_bucketed == 10


class TestOi20dSeries:
    def test_first_19_rows_none(self):
        """回归（finding #9）：前 19 行无满 20 日窗口，旧 rolling(20, min_periods=20)
        返回 NaN——18-19 因子的短窗口不得冒充 20 日变化。"""
        got = bt._oi_20d_series([1.0] * 30)
        assert got[:19] == [None] * 19
        assert got[19] is not None

    def test_short_window_below_min_valid_none(self):
        # 满 20 元素窗口但有效因子不足（4 None → 16 有效 < 18）→ None
        vals = [None] * 4 + [1.0] * 30
        got = bt._oi_20d_series(vals)
        assert got[20] is None
        assert got[21] is not None
