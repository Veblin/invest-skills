"""港股池数据层 — 离线单测（T11-5 / HK-4）。

本轮重点：`annualized_roe` 的**口径年化与三态剔除**。
港股 `ROE_AVG` 是**期间值**（中报 = 半年口径），A 侧的 8% 阈值配的是 `roe_yearly`（年化）
——不年化直接比会**严约一倍**（实测踩坑）；NaN/Inf 不剔除则分别造成闸门恒 False / 恒放行。
"""
from __future__ import annotations

import math

import sources_hk


def _row(**over):
    r = {"report_date": "2026-06-30", "roe": 8.0, "net_profit": 1.0e9}
    r.update(over)
    return r


def test_annualized_roe_midreport_doubled():
    """中报（06-30）为半年口径 → ×2 年化。"""
    assert sources_hk.annualized_roe(_row(report_date="2026-06-30", roe=8.0)) == 16.0


def test_annualized_roe_annual_report_unchanged():
    """年报（12-31）已是年度口径 → 原值。"""
    assert sources_hk.annualized_roe(_row(report_date="2026-12-31", roe=8.0)) == 8.0


def test_annualized_roe_rejects_none_nan_and_inf():
    """三态：None / 非数值 / NaN / ±Inf → None。

    ⚠️ Inf 必须剔除——`inf < 8.0` 为 False，会**直接通过质量闸门**（年化后仍是 inf）。
    """
    for bad in (None, "abc", float("nan"), float("inf"), float("-inf")):
        assert sources_hk.annualized_roe(_row(roe=bad)) is None, bad


def test_annualized_roe_missing_report_date_not_doubled():
    """报告期缺失/格式异常 → 不猜口径，按原值（宁可不年化，也不无依据放大）。"""
    for bad in (None, "", "2026-06"):
        assert sources_hk.annualized_roe(_row(report_date=bad, roe=8.0)) == 8.0


def test_annualized_roe_non_dict_returns_none():
    assert sources_hk.annualized_roe(None) is None
    assert sources_hk.annualized_roe(["x"]) is None


def test_lens_table_states_roe_is_annualized():
    """可用性表与实现同口径（契约不实检查：表称「已年化」则代码须真年化）。"""
    row = [r for r in sources_hk.lens_availability() if r["lens"].startswith("质量门 ROE")]
    assert row, "透镜表须含 ROE 质量门"
    assert "年化" in row[0]["status"]
    assert math.isclose(sources_hk.annualized_roe(_row(roe=8.0)), 16.0)
