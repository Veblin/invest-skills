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
        """回归（finding #8）：事件日不在指数收盘日历 → 跳过而非 KeyError。"""
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
        assert entry["n_events"]["deep_discount"] == 1
        assert "+5" not in entry["deep_discount"]  # 唯一事件日被跳过 → 无 forward 统计
