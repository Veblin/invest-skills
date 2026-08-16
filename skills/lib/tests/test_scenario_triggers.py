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

    def test_close_above_3d_once_per_streak(self):
        # 10 日连破 → 仅第 3 日计一次事件（滑窗不重复计，避免 forward 重叠）
        df = _frame([9.0] + [11.0] * 10 + [9.0])
        flags = sb.trigger_flags(df, {"kind": "close_above_3d", "level": 10.5})
        assert flags.sum() == 1
        assert flags.tolist().index(True) == 3

    def test_close_below_once_per_state(self):
        # 连续 3 日跌破 → 仅状态首日计事件
        df = _frame([100.0, 98.0, 97.0, 96.0, 101.0])
        flags = sb.trigger_flags(df, {"kind": "close_below", "level": 99.0})
        assert flags.tolist() == [False, True, False, False, False]

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

    def test_close_below_no_phantom_at_series_start(self):
        # 回归（finding #4）：序列起点即处于跌破状态 → 首行不得计"跌破首日"
        # below=[T,T,F,F,T]；起点前状态视为已存在 → idx0 抑制；idx4 再入计事件
        df = _frame([98.0, 97.0, 99.0, 101.0, 98.5])
        flags = sb.trigger_flags(df, {"kind": "close_below", "level": 99.0})
        assert flags.tolist() == [False, False, False, False, True]

    def test_close_near_once_per_state(self):
        # 回归（finding #3）：连续在带内 → 仅段首日计事件
        # near=[F,T,T,F,T] → 去重后 [F,T,F,F,T]
        df = _frame([4000.0, 3960.0, 3959.0, 4000.0, 3960.0])
        flags = sb.trigger_flags(df, {"kind": "close_near", "level": 3960.26, "tol_pct": 0.3})
        assert flags.tolist() == [False, True, False, False, True]

    def test_boll_position_once_per_state(self):
        # 回归（finding #3）：急涨段连续 4 日 pos>=95 → 仅段首日计事件
        df = _frame([100.0] * 20 + [130.0] * 5)
        flags = sb.trigger_flags(df, {"kind": "boll_position", "level": 95.0})
        assert flags.sum() == 1
        assert flags.tolist().index(True) == 20

    def test_touch_window_includes_day_60(self):
        # 回归（finding #5）：第 60 日（i+60）触达目标位必须计入（旧切片漏第 60 日）
        closes = [100.0] * 80
        closes[10] = 90.0   # 事件日 i=10
        closes[70] = 50.0   # i+60 触达目标 60
        r = sb.touch_within(closes, [10], [60.0])
        assert r == {"touched": 1, "ratio": 1.0}

    def test_touch_truncated_window_excluded(self):
        # 窗口被序列末尾截断（不足 60 日）→ 事件不入分母（对齐 lmw truncated 语义）
        r = sb.touch_within([100.0] * 30, [10], [60.0])
        assert r is None
