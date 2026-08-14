"""scenario_baselines 触发判定纯函数测试 — fixture，不联网。"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pandas as pd
import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO_ROOT / "skills"))

_spec = importlib.util.spec_from_file_location(
    "scenario_baselines", _REPO_ROOT / "scripts" / "scenario_baselines.py")
sb = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(sb)


def _frame(closes: list[float]) -> pd.DataFrame:
    return pd.DataFrame({
        "date": [f"2026-01-{i + 1:02d}" for i in range(len(closes))],
        "close": closes,
    })


class TestTriggers:
    def test_close_below(self):
        df = _frame([100.0, 99.0, 98.5, 101.0])
        flags = sb.trigger_flags(df, {"kind": "close_below", "level": 99.0})
        assert flags.tolist() == [False, False, True, False]

    def test_close_above_3d(self):
        # 连续 3 日 > level → 第 3 日 True；中断后 False
        df = _frame([10.0, 11.0, 12.0, 13.0, 9.0])
        flags = sb.trigger_flags(df, {"kind": "close_above_3d", "level": 10.5})
        # >10.5 标志 [F,T,T,T,F]；rolling(3).sum()==3 仅在 11,12,13 齐全的 idx3
        assert flags.tolist() == [False, False, False, True, False]

    def test_close_near(self):
        # tol 0.3% × 3960.26 ≈ ±11.88：3940 差 20.26（0.51%）→ False；3990 差 29.74（0.75%）→ False
        df = _frame([100.0, 3960.26, 3940.0, 3990.0])
        flags = sb.trigger_flags(df, {"kind": "close_near", "level": 3960.26, "tol_pct": 0.3})
        assert flags.tolist() == [False, True, False, False]

    def test_boll_position(self):
        # 构造上轨触达：平稳段后急涨至 2σ 上轨外
        closes = [100.0] * 20 + [130.0] * 5
        df = _frame(closes)
        flags = sb.trigger_flags(df, {"kind": "boll_position", "level": 95.0})
        # 急涨段 BOLL 位置应 ≥95（上轨附近）
        assert any(flags.tolist()[20:])
