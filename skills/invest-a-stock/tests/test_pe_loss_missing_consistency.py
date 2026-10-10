"""A05-3：PE 亏损/缺失占比 MD（valuation_summary）与 HTML（分位带）共用定义。

反例锚点（A 基础 §3.2 复现的四组不可证等价差异 → 本文件锁定收敛后行为）：
① ±Inf：旧正文不计亏损、旧 HTML 计亏损 → 现两侧同为 **missing**（不称亏损）；
② 字符串数值：旧正文 `v > 0` 抛 TypeError → 现 `safe_float` 归一；
③ 无日期行：旧正文分母含全量、旧 HTML 先滤日期 → 现两侧同用**有效日期窗口**；
④ 阈值舍入：30.04% 旧正文/旧 HTML 双结论 → 现按**未舍入**判定、显示再舍入。
"""

from __future__ import annotations

import math

import pytest


def _rows(n_dated: int = 60, undated: int = 5):
    """构造含 None/±Inf/NaN/字符串/≤0/>0 的行；另加无 trade_date 行。"""
    rows = []
    for i in range(n_dated):
        r: dict = {"trade_date": f"2026{i // 25 + 1:02d}{i % 25 + 1:02d}", "pb": 2.0}
        if i % 10 == 0:
            r["pe_ttm"] = None
        elif i % 10 == 1:
            r["pe_ttm"] = -3.5
        elif i % 10 == 2:
            r["pe_ttm"] = float("nan")
        elif i % 10 == 3:
            r["pe_ttm"] = float("inf")
        elif i % 10 == 4:
            r["pe_ttm"] = float("-inf")
        elif i % 10 == 5:
            r["pe_ttm"] = "30.5"
        elif i % 10 == 6:
            r["pe_ttm"] = "not-a-number"
        else:
            r["pe_ttm"] = 20.0 + i
        rows.append(r)
    for j in range(undated):  # 无日期行（旧正文计入分母 / 旧 HTML 滤掉）
        rows.append({"pb": 2.0, "pe_ttm": None if j % 2 else -1.0})
    return rows


def test_md_html_share_window_counts_and_threshold():
    from lib.html_charts import build_valuation_band_options
    from lib.valuation import valuation_summary

    rows = _rows()
    dated = [r for r in rows if r.get("trade_date")]
    summary = valuation_summary(
        [r.get("pe_ttm") for r in rows], [r.get("pb") for r in rows],
        pe_loss_window=[r.get("pe_ttm") for r in dated],
    )
    opts = build_valuation_band_options(rows)
    assert opts is not None
    a = opts["annotation_payload"]
    pe = summary["pe"]

    # ③ 同一有效日期窗口（无日期行不进分母）
    assert pe["pe_loss_window_n"] == a["n_window"] == len(dated)
    # 亏损（确证 PE≤0）与缺失（None/NaN/±Inf/非数值）分列且两侧一致
    assert pe["loss_days"] == a["loss_days_confirmed"]
    assert pe["missing_days"] == a["missing_days"]
    assert pe["loss_days"] + pe["missing_days"] < len(dated)  # 正值/字符串数值有效
    # 显示精度：MD 4dp 比率 ↔ HTML 1dp 百分数（同一未舍入值）
    assert round(pe["nonpos_or_missing_ratio"] * 100, 1) == a["nonpos_or_missing_ratio_pct"]
    # ④ 阈值判定两侧一致（未舍入）
    assert pe["loss_ratio_elevated"] == a["percentile_distortion"]


def test_undated_rows_do_not_change_ratio():
    """③ 反例：追加无日期行不得改变占比（旧正文会稀释分母）。"""
    from lib.valuation import valuation_summary

    rows = _rows(n_dated=40, undated=0)
    base = valuation_summary(
        [r["pe_ttm"] for r in rows], [r["pb"] for r in rows],
        pe_loss_window=[r["pe_ttm"] for r in rows if r["trade_date"]],
    )["pe"]
    rows2 = rows + [{"pe_ttm": None, "pb": 2.0}] * 40
    with_undated = valuation_summary(
        [r["pe_ttm"] for r in rows2], [r["pb"] for r in rows2],
        pe_loss_window=[r["pe_ttm"] for r in rows2 if r.get("trade_date")],
    )["pe"]
    assert with_undated["pe_loss_window_n"] == base["pe_loss_window_n"]
    assert with_undated["nonpos_or_missing_ratio"] == base["nonpos_or_missing_ratio"]


def test_inf_and_strings_classified_as_missing_not_loss():
    """①② ±Inf/字符串 → missing（safe_float 归一），不抛错、不计入亏损。"""
    from lib.valuation import pe_loss_missing_stats

    vals = [float("inf"), float("-inf"), float("nan"), None, "abc", "30.5", -2.0, 25.0]
    stats = pe_loss_missing_stats(vals, min_samples=0)
    assert stats is not None
    assert stats["n_missing"] == 5   # inf、-inf、nan、None、"abc"
    assert stats["n_loss"] == 1      # 仅 -2.0 确证 ≤0
    assert stats["n_valid"] == 2     # "30.5" 经 safe_float 可解析为正值 + 25.0
    assert stats["nonpos_or_missing_days"] == 6  # 5 缺失 + 1 确证亏损


def test_30_04_percent_single_conclusion():
    """④ 3004/10000 = 30.04% → 两侧同判「超阈值」，显示 30.0% 不改变判定。"""
    from lib.valuation import pe_loss_missing_stats, valuation_summary

    window = [None] * 3004 + [20.0] * 6996
    stats = pe_loss_missing_stats(window)
    assert stats["nonpos_or_missing_ratio_pct"] == 30.0  # 显示舍入
    assert stats["elevated"] is True                     # 未舍入判定
    summary = valuation_summary(
        [v for v in window if v is not None], [2.0] * len(window),
        pe_loss_window=window,
    )
    assert summary["pe"]["loss_ratio_elevated"] is True


def test_nan_never_produces_nan_ratio():
    """全 NaN 窗口 → 计数/比率有限（不产出 NaN/Inf 比率）。"""
    from lib.valuation import pe_loss_missing_stats

    stats = pe_loss_missing_stats([float("nan")] * 30)
    assert stats["nonpos_or_missing_ratio"] == 1.0
    assert stats["loss_ratio"] == 0.0
    assert not math.isnan(stats["nonpos_or_missing_ratio"])
    assert stats["elevated"] is True


def test_valuation_calc_unrounded_gate(tmp_path):
    """valuation_calc 侧：阈值用未舍入合计占比（pe_nonpos_or_missing_ratio）。"""
    import sys
    from pathlib import Path

    scripts = Path(__file__).resolve().parent.parent / "scripts"
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    from valuation_calc import calc_historical_percentile

    rows = [{"trade_date": f"2026{i:06d}", "pe_ttm": None, "pb": 1.0}
            for i in range(1, 3005)]
    rows += [{"trade_date": f"2027{i:06d}", "pe_ttm": 20.0, "pb": 2.0}
             for i in range(1, 6997)]
    result = calc_historical_percentile(rows)
    assert result["pe_neg_pct"] == pytest.approx(0.3004, abs=1e-6)
    assert result["pe_nonpos_or_missing_ratio"] == pytest.approx(0.3004, abs=1e-12)
    assert any("仅作位置参考" in w for w in result["warnings"])
    assert any("PE 缺失" in w for w in result["warnings"])  # 分列陈述，不统称亏损

@pytest.mark.parametrize("value,nonpositive,missing", [
    (None, 0, 30), (float("nan"), 0, 30), (-1.0, 30, 0), (0.0, 30, 0),
])
def test_window_statistics_survive_no_positive_pe(value, nonpositive, missing):
    from lib.valuation import valuation_summary
    pe = valuation_summary([value] * 30, [2.0] * 30)["pe"]
    assert pe["current"] is None and pe["pct"] is None
    assert pe["pe_loss_window_n"] == 30
    assert pe["loss_days"] == nonpositive
    assert pe["missing_days"] == missing
    assert pe["nonpos_or_missing_ratio"] == 1.0
    assert pe["nonpos_or_missing_ratio_raw"] == 1.0
    assert pe["loss_ratio_elevated"] is True


@pytest.mark.parametrize("with_fallback", [False, True])
def test_valuation_calc_zero_pe_keeps_primary_field(with_fallback):
    from valuation_calc import calc_historical_percentile
    rows = [{"trade_date": f"202609{i:02d}", "pe_ttm": 0.0, "pb": 2.0,
             **({"pe": 80.0} if with_fallback else {})} for i in range(1, 11)]
    rows += [{"trade_date": f"202609{i:02d}", "pe_ttm": 20.0, "pb": 2.0}
             for i in range(11, 31)]
    result = calc_historical_percentile(rows)
    assert result["pe_valid"] == 20
    assert result["pe_loss_confirmed_days"] == 10
    assert result["pe_missing_days"] == 0
    assert result["pe_nonpos_or_missing_ratio"] == pytest.approx(1 / 3)


def test_valuation_consumers_use_same_dated_window():
    from lib.render_utils import _v3_load_valuation_summary
    from lib.render_html import _extract_valuation_data
    from lib.store import extract_key_snapshot
    rows = [{"trade_date": f"202609{i:02d}", "pe_ttm": float(i), "pb": 2.0}
            for i in range(1, 31)]
    undated = {"pe_ttm": 9999.0, "pb": 500.0}
    dims = {"valuation": {"data": rows + [undated]}}
    summary = _v3_load_valuation_summary(dims)
    assert summary["pe"]["n_valid"] == summary["pe"]["pe_loss_window_n"] == 30
    assert summary["pe"]["median"] == 15.5
    html = _extract_valuation_data(dims)
    assert float(html["pe_median"]) == 15.5
    from valuation_calc import calc_historical_percentile
    calc = calc_historical_percentile(rows + [undated])
    assert calc["pe_valid"] == 30 and calc["pe_median"] == 15.5
    snap = extract_key_snapshot({"symbol": "600000", "dimensions": [
        {"dimension": "valuation", "data": rows + [undated]}]})
    base = extract_key_snapshot({"symbol": "600000", "dimensions": [
        {"dimension": "valuation", "data": rows}]})
    assert snap["valuation"] == base["valuation"]


def test_undated_legacy_valuation_consumers_keep_same_sample_window():
    from lib.render_utils import _v3_load_valuation_summary
    from lib.render_html import _extract_valuation_data
    from lib.store import extract_key_snapshot
    from valuation_calc import calc_historical_percentile
    rows = [{'pe_ttm': float(i), 'pb': 2.0} for i in range(1, 31)]
    dims = {'valuation': {'data': rows}}
    pe = _v3_load_valuation_summary(dims)['pe']
    assert pe['n_valid'] == pe['pe_loss_window_n'] == 30
    assert pe['median'] == calc_historical_percentile(rows)['pe_median'] == 15.5
    assert float(_extract_valuation_data(dims)['pe_median']) == 15.5
    snap = extract_key_snapshot({'symbol': '600000', 'dimensions': [
        {'dimension': 'valuation', 'data': rows}]})
    assert snap['valuation']['pe_ttm'] == 30.0
    assert snap['valuation']['pe_pct'] == pe['pct']
