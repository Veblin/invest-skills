"""market_pctile 纯函数测试 — 合成横截面，不联网。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_SKILLS_LIB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SKILLS_LIB))

from market_pctile import (  # noqa: E402
    build_cross_section,
    inject_distances_pctiles,
    pctile_20d,
)


def _rows(pairs: list[tuple[str, float, float | None]]) -> list[dict]:
    """[(ts_code, amount, turnover)] → 单日 market_daily 行。"""
    return [
        {
            "date": "2026-08-13", "ts_code": c,
            "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0,
            "pre_close": 1.0, "pct_chg": 0.0, "vol": 1.0,
            "amount": a, "turnover_rate": t,
        }
        for c, a, t in pairs
    ]


class TestCrossSection:
    def test_avg_and_missing(self):
        cross = build_cross_section(_rows([
            ("600000.SH", 100.0, 1.0),
            ("600001.SH", 200.0, None),
            ("600002.SH", None, 3.0),
            ("600003.SH", None, None),  # 全缺失 → 不进横截面
        ]))
        assert set(cross) == {"600000.SH", "600001.SH", "600002.SH"}
        assert cross["600000.SH"]["avg_amount"] == 100.0
        assert cross["600001.SH"]["avg_turnover"] is None
        assert cross["600002.SH"]["avg_amount"] is None
        assert cross["600002.SH"]["avg_turnover"] == 3.0

    def test_window_truncation(self):
        """仅取最近 N 日均值（rows 按日期升序拼接）。"""
        rows = _rows([("600000.SH", 100.0, 1.0)]) + _rows([("600000.SH", 300.0, 3.0)])
        cross = build_cross_section(rows, days=1)
        assert cross["600000.SH"]["avg_amount"] == 300.0


class TestPctile:
    def test_rank_boundaries(self):
        cross = build_cross_section(_rows([
            ("600000.SH", 100.0, 1.0),
            ("600001.SH", 200.0, 2.0),
            ("600002.SH", 300.0, 3.0),
        ]))
        assert pctile_20d(cross, "600000.SH")["amount_pctile"] == pytest.approx(33.33, abs=0.01)
        assert pctile_20d(cross, "600002.SH")["amount_pctile"] == pytest.approx(100.0)
        assert pctile_20d(cross, "600002.SH")["available"] is True

    def test_symbol_suffix_normalize(self):
        cross = build_cross_section(_rows([("600176.SH", 100.0, 1.0)]))
        p = pctile_20d(cross, "600176")
        assert p["available"] is True
        assert p["amount_pctile"] == pytest.approx(100.0)

    def test_missing_symbol(self):
        cross = build_cross_section(_rows([("600000.SH", 100.0, 1.0)]))
        p = pctile_20d(cross, "000999")
        assert p["available"] is False
        assert p["amount_pctile"] is None
        assert "不在全市场横截面" in p["reason"]

    def test_ambiguous_suffix(self):
        """6 位代码匹配到多个 ts_code → 视为缺失（不猜）。"""
        cross = build_cross_section(_rows([
            ("600176.SH", 100.0, 1.0),
            ("600176.BJ", 200.0, 2.0),
        ]))
        p = pctile_20d(cross, "600176")
        assert p["available"] is False


class TestInject:
    def test_inject_values(self):
        tech = {"distances": {"dist_to_52w_high_pct": -5.0}}
        cross = build_cross_section(_rows([("600176.SH", 100.0, 2.5)]))
        inject_distances_pctiles(tech, "600176", cross)
        d = tech["distances"]
        assert d["amount_pctile_20d"] == pytest.approx(100.0)
        assert d["turnover_pctile_20d"] == pytest.approx(100.0)
        assert "横截面" in d["pctile_note"]
        # 不改动其他键
        assert d["dist_to_52w_high_pct"] == -5.0

    def test_inject_none_cross(self):
        tech = {"distances": {}}
        inject_distances_pctiles(tech, "600176", None)
        d = tech["distances"]
        assert d["amount_pctile_20d"] is None
        assert d["turnover_pctile_20d"] is None
        assert "不可得" in d["pctile_note"]
