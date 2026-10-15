"""A1：BOLL 带宽口径 (上轨 − 下轨) / 中轨 与缺值/零防护（确定性，独立复算）。

反例来源：159920 原封存 payload（2026-10-10-08-19-28.payload.json）的
boll_upper/mid/lower 与 derived。旧公式 (上轨/下轨 − 1) 把下轨当分母 →
7.45%；正确口径 7.19%。测试不复制实现：带宽按数学定义独立写出，
用原 payload 其余派生值做交叉校验。
"""

from __future__ import annotations

import pytest

from etf_data import _nav_derived_metrics

# 159920 原 payload 现场值（kline.boll_upper/mid/lower 与 latest NAV）
_U, _M, _L = 1.5238, 1.471, 1.4181
_LATEST = 1.4301  # 由 nav_vs_boll_mid_pct=-2.78 反推并与 nav_to_boll_* 自洽


def test_bandwidth_mid_denominator_matches_original_payload():
    derived = _nav_derived_metrics(_LATEST, boll_upper=_U, boll_mid=_M, boll_lower=_L)
    # 数学定义独立复算（不是复制实现）：(u − l) / m × 100
    expected = round((_U - _L) / _M * 100, 2)
    assert expected == 7.19
    assert derived["boll_bandwidth_pct"] == expected
    # 旧错误公式（下轨作分母）必须不再出现
    assert round((_U / _L - 1) * 100, 2) == 7.45
    assert derived["boll_bandwidth_pct"] != 7.45
    # 同块其余派生值与原 payload 一致（交叉校验 latest 与上下轨口径）
    assert derived["nav_vs_boll_mid_pct"] == -2.78
    assert derived["boll_position_pct"] == 11.35
    assert derived["nav_to_boll_lower_pct"] == 0.85
    assert derived["nav_to_boll_upper_pct"] == -6.15


def test_bandwidth_missing_mid_no_key_no_crash():
    for kwargs in (
        {"boll_upper": _U, "boll_mid": None, "boll_lower": _L},
        {"boll_upper": _U, "boll_lower": _L},                      # mid 缺省
        {"boll_upper": None, "boll_mid": _M, "boll_lower": _L},    # 上轨缺
        {"boll_upper": _U, "boll_mid": _M, "boll_lower": None},    # 下轨缺
    ):
        derived = _nav_derived_metrics(_LATEST, **kwargs)
        assert "boll_bandwidth_pct" not in derived, kwargs


def test_bandwidth_zero_mid_guarded():
    derived = _nav_derived_metrics(_LATEST, boll_upper=_U, boll_mid=0.0, boll_lower=_L)
    assert "boll_bandwidth_pct" not in derived          # 零中轨不除
    assert "nav_vs_boll_mid_pct" not in derived
    assert derived["nav_to_boll_lower_pct"] == 0.85     # 其余键不受中轨影响


def test_bandwidth_zero_lower_and_upper_guarded():
    d1 = _nav_derived_metrics(_LATEST, boll_upper=_U, boll_mid=_M, boll_lower=0.0)
    assert "nav_to_boll_lower_pct" not in d1            # 零下轨不除
    assert "boll_bandwidth_pct" in d1                   # 带宽分母是中轨，仍合法
    d2 = _nav_derived_metrics(_LATEST, boll_upper=0.0, boll_mid=_M, boll_lower=_L)
    assert "nav_to_boll_upper_pct" not in d2


def test_collapsed_bands_bandwidth_zero_position_absent():
    # 上下轨重合：带宽=0%（合法），带内位置分母为零 → 不输出
    derived = _nav_derived_metrics(_LATEST, boll_upper=_M, boll_mid=_M, boll_lower=_M)
    assert derived["boll_bandwidth_pct"] == 0.0
    assert "boll_position_pct" not in derived


def test_latest_nav_missing_returns_empty():
    assert _nav_derived_metrics(None, boll_upper=_U, boll_mid=_M, boll_lower=_L) == {}


def test_ma_denominators_zero_guarded():
    derived = _nav_derived_metrics(_LATEST, ma20=0.0, ma60=0.0)
    assert "nav_vs_ma20_pct" not in derived
    assert "nav_vs_ma60_pct" not in derived
    ok = _nav_derived_metrics(_LATEST, ma20=1.4361, ma60=1.4937)
    assert ok["nav_vs_ma20_pct"] == pytest.approx(-0.42, abs=0.01)


def test_daily_volatility_uses_module_constant():
    derived = _nav_derived_metrics(_LATEST, volatility_annualized=16.3)
    # 16.3 / √252 = 1.0268 → 1.03（不得用 √365 → 0.85）
    assert derived["daily_volatility_pct"] == 1.03
    assert derived["daily_volatility_pct"] != 0.85
