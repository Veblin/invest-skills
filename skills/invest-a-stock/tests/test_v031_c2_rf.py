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
    assert rf["is_currency_unconfirmed"] is False
    assert rf["rf_usable"] is True
    assert "中国 10Y 国债" in rf["label"]


def test_usd_only_flagged_wrong_currency():
    rf = resolve_rf({"dgs10": 5.29, "rf_currency": "USD", "y10_source": "FRED.DGS10"})
    assert rf["rate_pct"] == 5.29
    assert rf["is_wrong_currency"] is True
    assert rf["rf_usable"] is False
    assert "美债 10Y" in rf["label"] and "FRED.DGS10" in rf["label"]


def test_legacy_snapshot_source_inference():
    """旧封存快照无 rf_currency：按来源字符串推断币种。"""
    rf = resolve_rf({
        "dgs10": 5.29,
        "source": "tushare.index_dailybasic+FRED.DGS10",
    })
    assert rf["currency"] == "USD" and rf["is_wrong_currency"] is True
    assert rf["rf_usable"] is False

    rf_cn = resolve_rf({
        "dgs10": 1.68,
        "source": "tushare.index_dailybasic+akshare.bond_zh_us_rate",
    })
    assert rf_cn["currency"] == "CNY" and rf_cn["is_wrong_currency"] is False
    assert rf_cn["rf_usable"] is True
    assert "中国 10Y 国债" in rf_cn["label"]


def test_missing_everything_is_default():
    rf = resolve_rf({})
    assert rf["rate_pct"] is None and rf["is_default"] is True
    assert rf["is_wrong_currency"] is False
    assert rf["rf_usable"] is False


def test_unknown_source_paused_not_admitted():
    """R1（2026-10-04 独立复检）：无任何来源/币种线索的 dgs10 值曾按「可用」
    放行（旧测试 test_unknown_source_not_flagged 锁定了该行为）——同一报告因
    此可同时出现 g_implied 缺口与「暂停方向解读」。修正为未确认币种 →
    与美元口径同等暂停（只有确认同币种才准入）。"""
    rf = resolve_rf({"dgs10": 2.65})
    assert rf["rate_pct"] == 2.65
    assert rf["is_default"] is False
    assert rf["is_wrong_currency"] is False
    assert rf["is_currency_unconfirmed"] is True
    assert rf["rf_usable"] is False


def test_known_cny_zero_rate_is_usable():
    """零值利率是合法读数（非缺失），CNY 已知来源下准入。"""
    rf = resolve_rf({"cn10y": 0.0, "cn10y_source": "akshare.bond_zh_us_rate(CN10Y)"})
    assert rf["rate_pct"] == 0.0
    assert rf["is_default"] is False
    assert rf["rf_usable"] is True
    assert rf["is_currency_unconfirmed"] is False


# ── R11（三轮）：非有限 / 不可解析输入的准入边界 ──

import math as _math  # noqa: E402

import pytest as _pytest  # noqa: E402


@_pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf"),
                                  "not-a-rate", "", "  ", True])
def test_cn10y_invalid_falls_back_to_dgs10(bad):
    """cn10y 存在但非有限/不可解析/布尔 → 不得 rf_usable=True，也不抛异常；
    有可用 dgs10 时降级到 dgs10（CNY 来源）并如实记录降级原因。"""
    rf = resolve_rf({"cn10y": bad, "dgs10": 1.68, "rf_currency": "CNY",
                     "y10_source": "akshare.bond_zh_us_rate"})
    assert rf["rate_pct"] == 1.68
    assert rf["rf_usable"] is True
    assert rf["invalid_inputs"] == ["cn10y"]
    assert "cn10y 无效已忽略" in rf["degraded_note"]
    assert "cn10y 无效已忽略" in rf["label"]


@_pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf"),
                                  "not-a-rate", True])
def test_cn10y_invalid_no_dgs10_is_default_with_note(bad):
    rf = resolve_rf({"cn10y": bad})
    assert rf["is_default"] is True and rf["rf_usable"] is False
    assert rf["rate_pct"] is None
    assert rf["invalid_inputs"] == ["cn10y"]
    assert "无效已忽略" in rf["degraded_note"]


@_pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf"),
                                  "not-a-rate"])
def test_dgs10_invalid_not_usable(bad):
    rf = resolve_rf({"dgs10": bad, "rf_currency": "USD", "y10_source": "FRED.DGS10"})
    assert rf["is_default"] is True and rf["rf_usable"] is False
    assert rf["invalid_inputs"] == ["dgs10"] and rf["degraded_note"]


def test_both_invalid_default_note_lists_both():
    rf = resolve_rf({"cn10y": float("nan"), "dgs10": float("inf")})
    assert rf["is_default"] is True and rf["rf_usable"] is False
    assert rf["invalid_inputs"] == ["cn10y", "dgs10"]
    assert "cn10y" in rf["degraded_note"] and "dgs10" in rf["degraded_note"]


def test_zero_and_negative_rates_stay_legal():
    """合法 0 与负利率不得按无效处理（负利率是真实市场状态）。"""
    zero = resolve_rf({"cn10y": 0.0})
    assert zero["rate_pct"] == 0.0 and zero["rf_usable"] is True
    assert zero["invalid_inputs"] == [] and zero["degraded_note"] == ""
    neg = resolve_rf({"cn10y": -0.5, "cn10y_source": "akshare.bond_zh_us_rate"})
    assert neg["rate_pct"] == -0.5 and neg["rf_usable"] is True


def test_numeric_string_rate_parseable():
    rf = resolve_rf({"cn10y": "1.68"})
    assert rf["rate_pct"] == 1.68 and rf["rf_usable"] is True
