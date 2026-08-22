"""pattern-scan 扫描器冒烟测试 — 合成双底 fixture，不联网。"""

from __future__ import annotations

import datetime
import sys
from pathlib import Path

import pandas as pd

_SCRIPT_DIR = Path(__file__).resolve().parent.parent / "scripts"
_LIB_DIR = _SCRIPT_DIR / "lib"
for _p in (str(_LIB_DIR), str(_SCRIPT_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)
_ROOT = Path(__file__).resolve().parent.parent.parent.parent
for _p in (str(_ROOT / "skills"), str(_ROOT / "skills" / "lib")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pattern_scanner as ps  # noqa: E402


def _double_bottom_closes(tail_close: float | None = None) -> list[float]:
    """合成双底序列（同 test_lmw 口径：平台底 + 突破）。

    tail_close 非 None 时在突破后追加 10 根「回踩区」（close=tail_close）：
    延长序列使 classify_retest 窗口完整走完（否则 ep≈188 + 窗口 (3,10)
    超出序列尾部 → 恒 truncated），并让盘中低点（close×0.97）击穿
    reference → clean_retest（tail_close > reference）或 deep_retest
    （tail_close < reference）分支可达（code-review #5）。
    """
    out: list[float] = []
    for i in range(80):
        out.append(100 - 30 * i / 79)
    out.extend([70.0] * 30)          # 底 1 平台
    for i in range(15):
        out.append(70 + 10 * (i + 1) / 15)
    for i in range(15):
        out.append(80 - 9.3 * (i + 1) / 15)
    out.extend([70.7] * 30)          # 底 2 平台
    for i in range(25):
        out.append(70.7 + 14.3 * (i + 1) / 25)
    if tail_close is not None:
        out.extend([tail_close] * 10)
    return out


def _fake_kline_module(closes_map: dict[str, list[float]]):
    """构造假 kline_source 模块（create_source/group/build_stock_kline）。"""

    class _Fake:
        @staticmethod
        def create_source(source: str = "auto"):
            return object()

        @staticmethod
        def fetch_daily_batch(trade_dates):
            return pd.DataFrame({"ts_code": ["600176.SH"] * len(trade_dates)})

        @staticmethod
        def fetch_adj_factor_batch(trade_dates):
            return pd.DataFrame({"ts_code": ["600176.SH"] * len(trade_dates)})

        @staticmethod
        def group_daily_by_ts_code(daily_df):
            return {"600176.SH": daily_df}

        @staticmethod
        def build_stock_kline(daily, adj, ts_code, min_bars=120, daily_by_ts=None, **kw):
            closes = closes_map.get(ts_code)
            if closes is None:
                return None
            df = pd.DataFrame({
                "trade_date": [f"2026{1 + i // 250:02d}{(i % 250) + 1:02d}" for i in range(len(closes))],
                "close_qfq": closes,
                # 8dabc01 起 scanner 回踩口径读 low_qfq。合成低点 = close×0.97
                # （盘中低略低于收盘），避免 low==close 镜像使 classify_retest
                # 只剩 truncated 单分支（code-review #5）
                "low_qfq": [c * 0.97 for c in closes],
            })
            return df if len(df) >= min_bars else None

    return _Fake()


def test_scanner_detects_synthetic_double_bottom(monkeypatch):
    closes = _double_bottom_closes(tail_close=78.5)
    fake = _fake_kline_module({"600176.SH": closes})

    # 假 fetch 层（scan_universe 内部 load_gap_scan_module("kline_source") 的替代）
    def _fake_load(mod):
        if mod == "kline_source":
            return fake
        raise ImportError(mod)

    monkeypatch.setattr(ps, "load_gap_scan_module", _fake_load)
    monkeypatch.setattr(ps, "fetch_daily_and_adj", lambda dates: (fake, None, None))

    # 合法 YYYYMMDD 占位日（review #15：旧写法 f"2026{i:04d}" 产生 20260001/20260199
    # 等非法日期，仅因 fetch 被 monkeypatch 才通过）
    _start = datetime.date(2026, 1, 1)
    dates = [(_start + datetime.timedelta(days=i)).strftime("%Y%m%d") for i in range(199)]
    hits, rule_matrix = ps.scan_universe(["600176.SH", "000001.SZ"], dates)

    assert len(hits) >= 1, f"合成双底必检出，实际 {len(hits)}"
    assert hits[0].pattern == "double_bottom"
    assert hits[0].ts_code == "600176.SH"
    # #5: 集成路径须触达 clean_retest 分支（修复前 low==close 镜像 + 窗口截断恒 truncated）
    statuses = {h.retest_status for h in hits}
    assert "clean_retest" in statuses, f"回踩分类应含 clean_retest，实际 {statuses}"
    # 规则矩阵：2 形态 × 3 带宽 × 3 窗口 = 18 规则
    assert len(rule_matrix) == 18, f"规则宇宙应 18 条，实际 {len(rule_matrix)}"
    for key, vals in rule_matrix.items():
        assert len(vals) == 2  # 每规则覆盖 2 只股票


def test_scanner_retest_deep_branch_reachable(monkeypatch):
    """#5: 回踩区收盘跌破 reference → deep_retest 分支在集成路径可达。"""
    closes = _double_bottom_closes(tail_close=76.0)
    fake = _fake_kline_module({"600176.SH": closes})

    def _fake_load(mod):
        if mod == "kline_source":
            return fake
        raise ImportError(mod)

    monkeypatch.setattr(ps, "load_gap_scan_module", _fake_load)
    monkeypatch.setattr(ps, "fetch_daily_and_adj", lambda dates: (fake, None, None))

    dates = [f"2026{i:04d}" for i in range(1, 200)]
    hits, _ = ps.scan_universe(["600176.SH"], dates)

    statuses = {h.retest_status for h in hits}
    assert "deep_retest" in statuses, f"回踩分类应含 deep_retest，实际 {statuses}"


def test_reality_check_on_matrix():
    """空矩阵 fail loud 路径 + 正常矩阵产出 p。"""
    rc = ps.reality_check_report({})
    assert "error" in rc

    import random as _r

    rng = _r.Random(3)
    mat = {f"rule_{i}": [rng.gauss(0, 1) for _ in range(50)] for i in range(4)}
    rc = ps.reality_check_report(mat)
    assert "p_value" in rc and "best_rule_name" in rc
    assert rc["n_rules"] == 4


def test_scan_hit_retest_placeholder():
    h = ps.ScanHit(ts_code="600176.SH", pattern="double_bottom",
                   endpoint_idx=10, bandwidth=0.5)
    assert h.retest_status is None  # P2 占位
