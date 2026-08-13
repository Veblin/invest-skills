"""K 线复权双实现契约测试（code-review 清理 B1）。

gap-scan（skills/invest-a-gap-scan/scripts/lib/kline_source.py）与 invest-a-stock
（lib/collector/_sources._apply_qfq）各自委托共享 qfq.py 的两个入口：

- ``apply_qfq``（DataFrame 版，gap 批量扫描语义）：不 rounding、整股 isna 拒绝
- ``apply_qfq_rows``（list[dict] 版，stock 单标的语义）：round 4 位、check_finite

两实现公式一致（qfq = raw × factor / latest），契约测试锁定：干净数据序列
等价（round 4 位误差内）、输入顺序无关、因子缺失双双拒绝；NaN 行按各自既定
语义分别断言（拒绝 vs 保留空行是设计差异，勿互走）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

_SKILLS_LIB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SKILLS_LIB))  # 无条件插 0：防其他 skill 目录先行入 path 遮蔽同名模块

from qfq import apply_qfq, apply_qfq_rows  # noqa: E402

_DAYS = ["20260601", "20260602", "20260603", "20260604", "20260605"]
_FACTORS = {"20260601": 1.5, "20260602": 1.4, "20260603": 1.3,
            "20260604": 1.2, "20260605": 1.1}  # 最新日因子 1.1 为锚

_CLOSE = [10.2, 10.4, 10.1, 10.6, 10.3]


def _rows() -> list[dict]:
    return [
        {"trade_date": d, "open": c + 0.3, "high": c + 0.8, "low": c - 0.4,
         "close": c, "vol": 100.0}
        for d, c in zip(_DAYS, _CLOSE)
    ]


def _adapter_gap(rows: list[dict], factors: dict[str, float]) -> list[float] | None:
    """gap-scan 调用面：apply_qfq(DataFrame, adj_df)，取 close_qfq 升序序列。"""
    df = pd.DataFrame(rows)
    adj = pd.DataFrame([{"trade_date": d, "adj_factor": f} for d, f in factors.items()])
    merged = apply_qfq(df, adj)
    if merged is None:
        return None
    return merged.sort_values("trade_date")["close_qfq"].tolist()


def _adapter_stock(rows: list[dict], factors: dict[str, float]) -> list[float] | None:
    """invest-a-stock 调用面：apply_qfq_rows(rows, factors, round_prices=True,
    check_finite=True)（即 _sources._apply_qfq 的真实参数），取 close 升序序列。"""
    out = apply_qfq_rows(rows, factors, round_prices=True, check_finite=True)
    if out is None:
        return None
    return [r["close"] for r in sorted(out, key=lambda r: str(r["trade_date"]))]


def test_clean_data_sequences_agree_within_rounding():
    """干净数据：两路径 close 序列逐元素一致（round 4 位误差上界内）。"""
    gap = _adapter_gap(_rows(), _FACTORS)
    stock = _adapter_stock(_rows(), _FACTORS)
    assert gap is not None and stock is not None
    assert len(gap) == len(stock) == len(_DAYS)
    for g, s in zip(gap, stock):
        assert g == pytest.approx(s, abs=5e-4)


def test_descending_input_anchors_latest_day():
    """输入顺序无关：tushare 降序风格输入与升序输入产出同一序列（锚最新日）。"""
    asc = _rows()
    desc = list(reversed(asc))
    gap_asc, gap_desc = _adapter_gap(asc, _FACTORS), _adapter_gap(desc, _FACTORS)
    stock_asc, stock_desc = _adapter_stock(asc, _FACTORS), _adapter_stock(desc, _FACTORS)
    assert gap_asc == pytest.approx(gap_desc, abs=5e-4)
    assert stock_asc == pytest.approx(stock_desc, abs=5e-4)
    # 且两路径各自与升序基准一致（锚定语义 = 最新日因子，非输入末行）
    assert stock_asc == pytest.approx(gap_asc, abs=5e-4)


def test_missing_factor_day_both_reject():
    """因子缺失某日 → 两路径均整体拒绝（None），不产出部分复权序列。"""
    factors = dict(_FACTORS)
    del factors["20260603"]
    assert _adapter_gap(_rows(), factors) is None
    assert _adapter_stock(_rows(), factors) is None


def test_nan_price_row_uses_respective_semantics():
    """NaN 价格行（None 值）：gap 整股拒绝 vs stock 保留空行——既定设计差异。"""
    rows = _rows()
    rows[2] = {**rows[2], "close": None}
    # gap：OHLC 任一 NaN → 整股 None
    assert _adapter_gap(rows, _FACTORS) is None
    # stock：该行 close 为 None，其余行为数值，序列中无 nan float
    stock = _adapter_stock(rows, _FACTORS)
    assert stock is not None
    assert len(stock) == len(_DAYS)
    assert stock[2] is None
    assert not any(v != v for v in stock if v is not None)  # 无 NaN float
