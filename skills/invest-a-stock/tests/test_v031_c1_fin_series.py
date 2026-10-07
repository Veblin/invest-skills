"""v0.3.1 C1-a：财务序列口径与去重（ROE 变异/规模效应/HTML 出口）回归。

反例来源：600519 报告 `2026-10-02-22-32-48.final.md` L688「近 8 期 ROE
变异系数 0.48（≥0.35），波动大，周期性特征明显」——年报与半年/季累计混算
所致（collection 173 实测：20 行含 5 组重复 end_date、仅 5 行年报）。
修复口径：同报告期类型序列（年报 ≥3 期优先，否则同 MMDD ≥3 期）+ 按
end_date 去重（ann_date 最大者优先）。
"""

from __future__ import annotations

from lib.financials import dedupe_by_end_date
from lib.render_html import _extract_financials_data
from lib.render_markdown._v3 import _canvas_cyclicality, _canvas_scale_effect


def _fin(end_date, *, roe=None, revenue=None, gm=None, ann_date=None):
    r = {"end_date": end_date}
    if roe is not None:
        r["roe"] = roe
    if revenue is not None:
        r["revenue"] = revenue
    if gm is not None:
        r["grossprofit_margin"] = gm
    if ann_date is not None:
        r["ann_date"] = ann_date
    return r


# ---- 去重 helper（修订披露选择依据） ----

def test_dedupe_keeps_latest_ann_date():
    rows = [
        _fin("20241231", roe=36.0, ann_date="20250401"),
        _fin("20241231", roe=34.0, ann_date="20250410"),
    ]
    out = dedupe_by_end_date(rows)
    assert len(out) == 1
    assert out[0]["roe"] == 34.0


def test_dedupe_without_ann_date_keeps_first():
    rows = [_fin("20250331", roe=11.0), _fin("20250331", roe=12.0)]
    out = dedupe_by_end_date(rows)
    assert [r["roe"] for r in out] == [11.0]


def test_dedupe_one_side_has_ann_date_wins():
    rows = [_fin("20241231", roe=36.0), _fin("20241231", roe=34.0, ann_date="20250410")]
    out = dedupe_by_end_date(rows)
    assert out[0]["roe"] == 34.0


def test_dedupe_preserves_order_and_unparsable_rows():
    rows = [_fin("20231231", roe=1.0), _fin("", roe=2.0), _fin("20231231", roe=3.0)]
    out = dedupe_by_end_date(rows)
    assert [str(r["end_date"]) for r in out] == ["20231231", ""]
    assert out[0]["roe"] == 1.0


# ---- 周期性：反例与正例 ----

def test_cyclicality_no_mixed_period_counterexample_600519():
    """原反例：年报 38.4% 与季累计 ~18% 混算 CV=0.48 → 修复后按年报口径。"""
    rows = [
        _fin("20221231", roe=32.41),
        _fin("20230331", roe=15.1),
        _fin("20230630", roe=18.1),
        _fin("20230930", roe=22.5),
        _fin("20231231", roe=36.18),
        _fin("20240331", roe=16.8),
        _fin("20240630", roe=19.2),
        _fin("20240930", roe=24.0),
        _fin("20241231", roe=38.43),
        _fin("20250331", roe=19.2),
        _fin("20250630", roe=17.95),
        _fin("20251231", roe=34.46),
        _fin("20260630", roe=17.95),
    ]
    score, note, src = _canvas_cyclicality(rows)
    assert score == 90.0, note
    assert "近 4 期年报" in note
    assert "波动小" in note
    assert "周期性特征明显" not in note
    assert "0.48" not in note
    assert src == ["roe"]


def test_cyclicality_insufficient_same_period_series():
    """无任何同报告期类型 ≥3 期 → 数据不足，不做混期 CV。"""
    rows = [
        _fin("20231231", roe=30.0), _fin("20241231", roe=31.0),
        _fin("20230630", roe=15.0), _fin("20240630", roe=16.0),
        _fin("20250331", roe=8.0), _fin("20260331", roe=9.0),
    ]
    score, note, src = _canvas_cyclicality(rows)
    assert score is None
    assert "不足 3 期" in note
    assert src == []


def test_cyclicality_same_mmdd_fallback():
    """年报不足 3 期 → 与最新期同 MMDD 的序列（口径标签 = 中报）。"""
    rows = [
        _fin("20230630", roe=22.0), _fin("20240630", roe=18.0),
        _fin("20250630", roe=20.0), _fin("20260630", roe=19.0),
        _fin("20250331", roe=9.0), _fin("20251231", roe=30.0),
    ]
    score, note, src = _canvas_cyclicality(rows)
    assert score == 90.0, note
    assert "中报" in note
    assert "近 4 期中报" in note


def test_cyclicality_dedup_applied_before_cv():
    """修订行若不去重，错误旧值 76.8 会把 CV 炸到 ≥0.35；去重取 ann_date 最大。"""
    rows = [
        _fin("20221231", roe=32.4, ann_date="20230401"),
        _fin("20231231", roe=36.2, ann_date="20240401"),
        _fin("20241231", roe=76.8, ann_date="20250401"),   # 修订前错误值
        _fin("20241231", roe=38.4, ann_date="20250410"),   # 修订后
        _fin("20251231", roe=34.5, ann_date="20260401"),
    ]
    score, note, src = _canvas_cyclicality(rows)
    assert score == 90.0, note


# ---- 规模效应：同口径 ----

def test_scale_effect_uses_same_period_series():
    """旧实现取混期首尾（年报 1709 亿 vs 半年 880 亿 → 伪下滑）。"""
    rows = [
        _fin("20221231", revenue=1240.0, gm=91.0),
        _fin("20231231", revenue=1477.0, gm=92.0),
        _fin("20241231", revenue=1709.0, gm=91.5),
        _fin("20251231", revenue=1688.0, gm=91.8),
        _fin("20250630", revenue=890.0, gm=90.0),
        _fin("20260630", revenue=880.0, gm=90.5),
    ]
    score, note, src = _canvas_scale_effect(rows)
    assert score == 80.0, note
    assert "年报" in note
    assert "+36.1%" in note


def test_scale_effect_insufficient_same_period():
    rows = [
        _fin("20231231", revenue=1240.0, gm=91.0),
        _fin("20241231", revenue=1477.0, gm=92.0),
        _fin("20250630", revenue=890.0, gm=90.0),
        _fin("20260630", revenue=880.0, gm=90.5),
    ]
    score, note, src = _canvas_scale_effect(rows)
    assert score is None
    assert "不足 3 期" in note


# ---- HTML 出口：去重 + 同类型内高/低标记 ----

def test_html_financials_dedup_and_same_mmdd_class():
    rows = [
        _fin("20240630", roe=18.0),
        _fin("20241231", roe=38.4),
        _fin("20250630", roe=19.0),
        _fin("20250630", roe=18.5),   # 重复 end_date（无 ann_date → 保留先出现 19.0）
        _fin("20251231", roe=34.5),
        _fin("20260630", roe=26.0),   # 同 MMDD 组（18/19/26）内显著偏高
    ]
    labels, roe_data, _eps, _pf, table_html, _note = _extract_financials_data(
        {"financials": {"data": rows}}
    )
    assert len(labels) == 5, labels                      # 去重生效
    assert roe_data.count(19.0) == 1                     # 保留先出现行
    assert table_html.count("roe-hi") == 1               # 仅 26.0 高于同组均值 1.1×
    assert table_html.count("roe-lo") == 1               # 仅 18.0 低于同组均值 0.9×
