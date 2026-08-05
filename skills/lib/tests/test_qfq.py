"""Tests for skills/lib/qfq.py — 前复权双路径（DataFrame gap 语义 + rows stock 语义）。

行版覆盖 test_collector 的 F1 回归（降序输入按 trade_date 最大行锚定最新）。
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import pandas as pd
import pytest

_SKILLS_LIB = Path(__file__).resolve().parents[1]
if str(_SKILLS_LIB) not in sys.path:
    sys.path.insert(0, str(_SKILLS_LIB))

from qfq import PRICE_COLS, apply_qfq, apply_qfq_rows  # noqa: E402

_FACTORS = {
    "20260710": 1.2,
    "20260711": 1.1,
    "20260712": 1.0,
}


def _rows() -> list[dict]:
    return [
        {"trade_date": "20260710", "open": 10.0, "high": 10.5, "low": 9.7,
         "close": 10.0, "vol": 100},
        {"trade_date": "20260711", "open": 12.0, "high": 12.5, "low": 11.7,
         "close": 12.0, "vol": 200},
        {"trade_date": "20260712", "open": 15.0, "high": 15.5, "low": 14.7,
         "close": 15.0, "vol": 300},
    ]


class TestApplyQfqRows:
    def test_formula_round_4(self):
        out = apply_qfq_rows(_rows(), _FACTORS, round_prices=True)
        assert out is not None
        assert out[0]["close"] == pytest.approx(12.0)   # 10 * 1.2 / 1.0
        assert out[1]["close"] == pytest.approx(13.2)   # 12 * 1.1 / 1.0
        assert out[2]["close"] == 15.0                  # 最新 bar 不变
        # vol/amount 不调整，原值透传
        assert out[0]["vol"] == 100

    def test_descending_input_anchors_newest(self):
        """F1 回归：降序输入按 trade_date 最大行锚定，而非 rows[-1]。"""
        desc = list(reversed(_rows()))
        out = apply_qfq_rows(desc, _FACTORS, round_prices=True)
        assert out is not None
        by_date = {r["trade_date"]: r for r in out}
        assert by_date["20260712"]["close"] == 15.0
        assert by_date["20260710"]["close"] == pytest.approx(12.0)

    def test_empty_inputs_return_none(self):
        assert apply_qfq_rows([], _FACTORS) is None
        assert apply_qfq_rows(_rows(), {}) is None

    def test_missing_factor_rejects_all(self):
        factors = {k: v for k, v in _FACTORS.items() if k != "20260711"}
        assert apply_qfq_rows(_rows(), factors) is None

    def test_zero_negative_factor_rejected(self):
        bad = dict(_FACTORS, **{"20260711": 0.0})
        assert apply_qfq_rows(_rows(), bad) is None
        bad2 = dict(_FACTORS, **{"20260711": -1.0})
        assert apply_qfq_rows(_rows(), bad2) is None

    def test_epsilon_parameterized(self):
        # 默认 epsilon=0：正小因子合法；epsilon=1e-12 时不合法
        tiny = dict(_FACTORS, **{"20260711": 1e-13})
        assert apply_qfq_rows(_rows(), tiny) is not None
        assert apply_qfq_rows(_rows(), tiny, epsilon=1e-12) is None

    def test_none_price_passthrough(self):
        rows = [dict(r, open=None, close=None) for r in _rows()]
        out = apply_qfq_rows(rows, _FACTORS, round_prices=True)
        assert out is not None
        assert out[0]["open"] is None and out[0]["close"] is None
        assert out[0]["high"] is not None

    def test_input_rows_not_mutated(self):
        original = _rows()
        apply_qfq_rows(original, _FACTORS, round_prices=True)
        assert original == _rows()

    def test_missing_key_passthrough_none(self):
        """缺键行：r.get(col) → None → 该列输出 None（stock 原语义，不抛）。"""
        rows = [dict(r) for r in _rows()]
        del rows[1]["close"]
        out = apply_qfq_rows(rows, _FACTORS, round_prices=True)
        assert out is not None
        assert out[1]["close"] is None
        assert out[1]["open"] is not None

    def test_no_rounding_when_round_prices_false(self):
        out = apply_qfq_rows(_rows(), _FACTORS)
        assert out is not None
        assert out[0]["close"] == pytest.approx(12.0)  # 精确浮点，不 round


def _make_daily() -> pd.DataFrame:
    return pd.DataFrame({
        "trade_date": ["20260710", "20260711", "20260712"],
        "open": [10.0, 12.0, 15.0],
        "high": [10.5, 12.5, 15.5],
        "low": [9.7, 11.7, 14.7],
        "close": [10.0, 12.0, 15.0],
        "amount": [1e8, 1.1e8, 1.2e8],
    })


def _make_adj(factors=None) -> pd.DataFrame:
    return pd.DataFrame({
        "trade_date": ["20260710", "20260711", "20260712"],
        "adj_factor": factors or [1.2, 1.1, 1.0],
    })


class TestApplyQfqDataFrame:
    def test_formula_keeps_raw_appends_qfq_cols(self):
        out = apply_qfq(_make_daily(), _make_adj())
        assert out is not None
        assert out.loc[out["trade_date"] == "20260710", "close_qfq"].iloc[0] == pytest.approx(12.0)
        assert out.loc[out["trade_date"] == "20260712", "close_qfq"].iloc[0] == 15.0
        # 原列保留 + 追加 _qfq 列；amount 不动
        assert "close" in out.columns and "close_qfq" in out.columns
        assert out.loc[out["trade_date"] == "20260710", "amount"].iloc[0] == 1e8

    def test_none_or_empty_adj_returns_none(self):
        assert apply_qfq(_make_daily(), None) is None
        assert apply_qfq(_make_daily(), pd.DataFrame()) is None

    def test_factor_near_zero_rejected(self):
        adj = _make_adj([1.2, 1e-13, 1.0])
        assert apply_qfq(_make_daily(), adj) is None

    def test_factor_non_finite_rejected(self):
        adj = _make_adj([1.2, float("inf"), 1.0])
        assert apply_qfq(_make_daily(), adj) is None

    def test_na_n_ohlc_rejected(self):
        daily = _make_daily()
        daily.loc[1, "close"] = float("nan")
        assert apply_qfq(daily, _make_adj()) is None

    def test_missing_date_overlap_rejected(self):
        adj = _make_adj()
        adj["trade_date"] = ["20260801", "20260802", "20260803"]  # 无重叠 → merge NaN
        assert apply_qfq(_make_daily(), adj) is None

    def test_descending_input_anchors_newest(self):
        daily = _make_daily().iloc[::-1].reset_index(drop=True)
        out = apply_qfq(daily, _make_adj())
        assert out is not None
        assert out.loc[out["trade_date"] == "20260712", "close_qfq"].iloc[0] == 15.0


class TestSharedConstants:
    def test_price_cols(self):
        assert PRICE_COLS == ("open", "high", "low", "close")
