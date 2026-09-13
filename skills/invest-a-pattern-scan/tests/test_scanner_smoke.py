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
    # 规则矩阵：2 形态 × 3 带宽 × 3 窗口 = 18，加 R-B01 三特征 × 3 窗口 = 9 → **27**
    # （规则宇宙须全量物化，不随数据漂移——RC 口径要求）
    assert len(rule_matrix) == 27, f"规则宇宙应 27 条，实际 {len(rule_matrix)}"
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


# ── 轮末评审修复（2026-09-13）─────────────────────────────────────────────

def test_limit_up_pct_is_board_specific():
    """涨停阈值须按板块推导（权威表在 `technical.limit_pct_for_symbol`）。

    老实现全池套主板 9.8 → 创业板/科创板（20%）的 10%+ 正常波动被当涨停，
    伪事件还会进入 RC 规则矩阵 `limit_up_above_ma_+h`。
    """
    assert ps._limit_up_pct_for("600176.SH") == 9.8      # 主板
    assert ps._limit_up_pct_for("300750.SZ") == 19.8     # 创业板
    assert ps._limit_up_pct_for("688981.SH") == 19.8     # 科创板
    assert ps._limit_up_pct_for("830799.BJ") == 29.8     # 北交所
    assert ps._limit_up_pct_for("600176.SH", "*ST 某某") == 4.8   # 主板 ST


def test_centered_keeps_legitimate_zero_return():
    """**合法的 0.0 收益**不得被当成缺失（D1 falsy 陷阱）。

    `(v or baseline) - baseline` 会把停牌/平收这类 0.0 命中记成「与基线持平」，
    少扣基线 → `reality_check` 置换 p 值系统性偏移。
    """
    assert ps._centered(0.0, 0.05) == -0.05
    assert ps._centered(0.10, 0.05) == 0.05
    assert ps._centered(None, 0.05) == 0.0               # 缺数据 → 中性
    assert ps._centered(0.05, 0.05) == 0.0


def test_hit_to_dict_emits_evidence_note():
    """C11 证据注记必须写进 JSON。

    `evidence_note` 是 `detail` 的**兄弟键**（只在 ScanHit 字段里）；原序列化只展开
    `**h.detail` → 注记永远进不了 `pattern_scan_result.json`，而 SKILL.md 承诺
    「命中带 evidence_note」且要求报告只引 JSON 字段 → 合规注记在产出物里缺席。
    """
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "pattern_scan_cli_under_test", _SCRIPT_DIR / "scan.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    hit = ps.ScanHit(ts_code="600176.SH", pattern="shrink_pullback", endpoint_idx=10,
                     bandwidth=None, detail={"kind": "shrink_pullback", "shrink_ratio": 0.4},
                     evidence_note="⚠️ 只作筛选，须自家样本后验")
    row = mod._hit_to_dict(hit)
    assert row["evidence_note"] == "⚠️ 只作筛选，须自家样本后验"
    assert row["kind"] == "shrink_pullback", "detail 展开仍须在位"
    # detail 同名键不得覆盖显式字段
    hit2 = ps.ScanHit(ts_code="X", pattern="p", endpoint_idx=0, bandwidth=None,
                      detail={"evidence_note": "伪造"}, evidence_note="真实")
    assert mod._hit_to_dict(hit2)["evidence_note"] == "真实"
