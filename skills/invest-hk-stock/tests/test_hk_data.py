"""hk_kline / hk_financials / hk_valuation 映射单测（fixture 离线，不联网）。"""

from __future__ import annotations

import pytest

import hk_financials as fin
import hk_valuation as val


# ---- hk_financials：东财行 → 引擎键映射（2026-09-06 实测列）----

def test_financial_row_mapping_percent_and_currency():
    """东财指标列实测为百分数原值（腾讯毛利率 56.21；薄利 0.7005 = 0.70%）——透传不放大。"""
    raw = {
        "REPORT_DATE": "2025-12-31 00:00:00",
        "OPERATE_INCOME": 751766000000,
        "OPERATE_INCOME_YOY": 13.86,
        "HOLDER_PROFIT": 215000000000,
        "GROSS_PROFIT_RATIO": 56.21,       # 百分数原值
        "NET_PROFIT_RATIO": 0.7005,        # 薄利公司 0.70%——不得 ×100（review2 HK-2）
        "ROE_AVG": 21.13,
        "BASIC_EPS": 24.749,
        "BPS": 126.786,
        "CURRENCY": "HKD",
    }
    row = fin._norm_row(raw)
    assert row["report_date"] == "2025-12-31"
    assert row["revenue"] == 751766000000
    assert row["gross_margin"] == pytest.approx(56.21)
    assert row["net_margin"] == pytest.approx(0.7005)    # 0.70% 原样
    assert row["roe"] == pytest.approx(21.13)
    assert row["currency"] == "HKD"


def test_financial_rows_sorted_desc_and_cny_flagged():
    rows = [
        {"REPORT_DATE": "2025-12-31 00:00:00", "HOLDER_PROFIT": 1, "CURRENCY": "HKD"},
        {"REPORT_DATE": "2024-12-31 00:00:00", "HOLDER_PROFIT": 2, "CURRENCY": "HKD"},
        {"REPORT_DATE": "2026-06-30 00:00:00", "HOLDER_PROFIT": 3, "CURRENCY": "CNY"},
    ]
    out = sorted((fin._norm_row(r) for r in rows), key=lambda r: r["report_date"], reverse=True)
    assert out[0]["report_date"] == "2026-06-30" and out[0]["currency"] == "CNY"
    assert out[2]["report_date"] == "2024-12-31"


@pytest.mark.parametrize("currency", [None, "", "  ", float("nan"), "NaN", "--"])
def test_missing_financial_currency_is_not_assumed_hkd(currency):
    assert fin._norm_row({"CURRENCY": currency})["currency"] is None


# ---- hk_valuation：分位/中位（复用 skills/lib/stats）----

def _mk_series(vals, start="2021-01-01"):
    return [{"date": f"{start[:4]}-{(i // 28) + 1:02d}-01", "value": v}
            for i, v in enumerate(vals)]


def test_percentile_position_uses_positive_only():
    s = _mk_series([10, 20, 30, 40, 50, -5, 60])
    out = val.percentile_position(s, cur=25.0)
    assert out["median"] == pytest.approx(35.0)     # 正数中位（30/40 中值）
    # percentile_rank 输出 0-100（与 A 股引擎分位口径一致：25 在 [10,20,30,40,50,60] → 33.3）
    assert out["pct"] == pytest.approx(100 * 2 / 6, abs=0.5)
    assert out["n"] == 7
    assert "滞后" in out["note"]


def test_percentile_negative_current_position_only():
    s = _mk_series([10, 20, 30])
    out = val.percentile_position(s, cur=-3.0)
    assert out["pct"] is None and "位置参考" in out["note"]


def test_percentile_empty_series():
    out = val.percentile_position([], cur=10.0)
    assert out["n"] == 0 and out["median"] is None


# ---- hk_yfinance：代码形态与股息率脏值防线（HK-4 增补）----

def test_yahoo_sym_four_digit_padding():
    import hk_yfinance as yf
    assert yf._yahoo_sym("00700") == "0700.HK"     # 700 → 4 位补零
    assert yf._yahoo_sym("1211") == "1211.HK"      # 4 位原样
    assert yf._yahoo_sym("01211") == "1211.HK"     # 去多余前导零
    assert yf._yahoo_sym("00941") == "0941.HK"


def test_div_yield_norm_pure():
    """Yahoo dividendYield 单位漂移防线纯函数：>25% 判脏值丢弃（01211 曾 48%）。"""
    import hk_yfinance as yf
    assert yf._norm_div_yield(1.2) == 1.2          # 0700 百分数直用
    assert yf._norm_div_yield(0.48) == 0.48        # 01211 百分数直用
    assert yf._norm_div_yield(48.0) is None        # 脏值（旧 ×100 残留）
    assert yf._norm_div_yield(-1.0) is None
    assert yf._norm_div_yield(0) is None
