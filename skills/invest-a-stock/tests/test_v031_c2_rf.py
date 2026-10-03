"""v0.3.1 C2-a：无风险利率解析（resolve_rf）与币种降级回归。

反例：600519 报告 `2026-10-02-22-32-48.final.md` L839/L841（美债 10Y 5.29%
作 A 股折现率且未标币种）与 L195（同报告 value 路径用中债 1.68%）。
"""

from __future__ import annotations

from lib.financials import resolve_rf


def test_cn10y_preferred_over_dgs10():
    rf = resolve_rf({
        "cn10y": 1.68, "cn10y_source": "akshare.bond_zh_us_rate(CN10Y)",
        "dgs10": 5.29, "rf_currency": "USD", "y10_source": "FRED.DGS10",
    })
    assert rf["rate_pct"] == 1.68
    assert rf["currency"] == "CNY"
    assert rf["is_default"] is False
    assert rf["is_wrong_currency"] is False
    assert "中国 10Y 国债" in rf["label"]


def test_usd_only_flagged_wrong_currency():
    rf = resolve_rf({"dgs10": 5.29, "rf_currency": "USD", "y10_source": "FRED.DGS10"})
    assert rf["rate_pct"] == 5.29
    assert rf["is_wrong_currency"] is True
    assert "美债 10Y" in rf["label"] and "FRED.DGS10" in rf["label"]


def test_legacy_snapshot_source_inference():
    """旧封存快照无 rf_currency：按来源字符串推断币种。"""
    rf = resolve_rf({
        "dgs10": 5.29,
        "source": "tushare.index_dailybasic+FRED.DGS10",
    })
    assert rf["currency"] == "USD" and rf["is_wrong_currency"] is True

    rf_cn = resolve_rf({
        "dgs10": 1.68,
        "source": "tushare.index_dailybasic+akshare.bond_zh_us_rate",
    })
    assert rf_cn["currency"] == "CNY" and rf_cn["is_wrong_currency"] is False
    assert "中国 10Y 国债" in rf_cn["label"]


def test_missing_everything_is_default():
    rf = resolve_rf({})
    assert rf["rate_pct"] is None and rf["is_default"] is True
    assert rf["is_wrong_currency"] is False


def test_unknown_source_not_flagged():
    """无任何来源/币种线索的 dgs10 值不作错币种判定（保持既有夹具兼容）。"""
    rf = resolve_rf({"dgs10": 2.65})
    assert rf["rate_pct"] == 2.65
    assert rf["is_wrong_currency"] is False
    assert rf["is_default"] is False
