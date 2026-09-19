"""中过滤（质量闸门）— 离线单测（设计 §2.2 第 3/4 条 + §5 降级三档）。

口径以 T0 勘察实测为准（`round-plans/r4-20260912.md` §1.2）：
- `fina_indicator` **无 `roe_ttm` 字段** → 用 `roe_yearly`（年化，跨期同口径）
- **无原始 `netprofit` 列** → 用 `profit_dedt`（扣非归母净利，口径**加严**）
- 返回帧含**重复行** → 取最近一期前必须按 end_date 去重
"""
from __future__ import annotations

import quality


def _fina(end_date="20260630", ann_date="20260815", roe_yearly=15.0, profit_dedt=1.0e9):
    return {"end_date": end_date, "ann_date": ann_date,
            "roe_yearly": roe_yearly, "profit_dedt": profit_dedt}


# ── 期次选取 ─────────────────────────────────────────────────────────────

def test_latest_report_picks_newest_end_date():
    rows = [_fina("20251231", "20260417"), _fina("20260630", "20260815"), _fina("20250930", "20251030")]
    assert quality.latest_report(rows)["end_date"] == "20260630"


def test_latest_report_dedupes_same_end_date_keeping_latest_ann_date():
    """实测踩坑：`fina_indicator` 对同 end_date 会返回重复行——
    不去重则「最近一期」的 ROE/净利取值不确定。"""
    older = _fina("20250331", "20250430", roe_yearly=9.0)
    newer = _fina("20250331", "20250506", roe_yearly=11.0)
    got = quality.latest_report([older, newer])
    assert got["end_date"] == "20250331"
    assert got["ann_date"] == "20250506", "同期多条须取 ann_date 最新者"
    assert got["roe_yearly"] == 11.0, "去重后须收敛为单行（不得保留旧行数值）"


def test_latest_report_empty_is_none():
    assert quality.latest_report([]) is None
    assert quality.latest_report(None) is None


def test_latest_report_prefers_newest_period_over_ann_date():
    """期次优先于披露日：H1 比上一年年报「更新」，即便年报 ann_date 更晚（补披露）。"""
    rows = [_fina("20251231", "20260901"), _fina("20260630", "20260815")]
    assert quality.latest_report(rows)["end_date"] == "20260630"


# ── 三档 ─────────────────────────────────────────────────────────────────

def test_tier_fina_passes_healthy_company():
    out = quality.pass_quality({}, [_fina(roe_yearly=15.0, profit_dedt=1.0e9)], [],
                               tier="fina")
    assert out["pass"] is True and out["tier"] == "fina"
    assert out["roe_yearly"] == 15.0 and out["profit_dedt"] == 1.0e9


def test_tier_fina_rejects_low_roe_and_negative_profit():
    low = quality.pass_quality({}, [_fina(roe_yearly=5.0, profit_dedt=1.0e9)], [], tier="fina")
    assert low["pass"] is False and "ROE" in low["reason"]
    neg = quality.pass_quality({}, [_fina(roe_yearly=15.0, profit_dedt=-1.0)], [], tier="fina")
    assert neg["pass"] is False and "净利" in neg["reason"]


def test_roe_threshold_is_inclusive():
    assert quality.pass_quality({}, [_fina(roe_yearly=8.0)], [], tier="fina")["pass"] is True
    assert quality.pass_quality({}, [_fina(roe_yearly=7.99)], [], tier="fina")["pass"] is False


def test_tier_fina_no_data_is_unavailable_not_pass():
    """**不可评估 ≠ 通过**：无数据时须 pass=None + warning（设计 §5「不冒充完整过滤」）。"""
    out = quality.pass_quality({}, [], [], tier="fina")
    assert out["pass"] is None
    assert out["warning"], "不可评估须给 warning"


def test_tier_forecast_uses_forecast_net_profit():
    fc = [{"net_profit_max": 2.0e8, "net_profit_min": 1.0e8}]
    ok = quality.pass_quality({"dv_ratio": 0.5}, [], fc, tier="forecast")
    assert ok["pass"] is True and ok["tier"] == "forecast" and ok["warning"]
    bad = quality.pass_quality({}, [], [{"net_profit_max": -1.0, "net_profit_min": -2.0}],
                               tier="forecast")
    assert bad["pass"] is False


def test_tier_forecast_falls_back_to_dividend():
    """ROE 下限降级为「股息率 > 0 或预告净利 > 0」。"""
    out = quality.pass_quality({"dv_ratio": 3.2}, [], [], tier="forecast")
    assert out["pass"] is True


def test_tier_skipped_is_explicitly_not_evaluated():
    out = quality.pass_quality({}, [], [], tier="skipped")
    assert out["pass"] is None and out["tier"] == "skipped"
    assert "跳过" in out["warning"]


def test_unknown_tier_fails_loud():
    try:
        quality.pass_quality({}, [], [], tier="bogus")
    except ValueError:
        return
    raise AssertionError("未知档位须 fail loud（静默按通过处理会冒充完整过滤）")


def test_default_tier_is_fina():
    """T0 勘察结论：fina_indicator 2000 分档可用 → 默认首选档。"""
    assert quality.QUALITY_TIER == "fina"
