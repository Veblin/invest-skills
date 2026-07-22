"""Unit tests for market_microstructure.apply_env_guardrail + labels.

No network — synthetic snapshots only.
"""

from __future__ import annotations

import copy

from market_microstructure import (
    _compute_labels,
    _is_extreme_sentiment_up,
    apply_env_guardrail,
)


def _base_eval() -> dict:
    return {
        "dimensions": {
            "logic": {"level": "✅", "notes": "ok"},
            "blind_spots": {"level": "⚠️", "notes": "partial"},
            "position_sizing": {"level": "✅", "notes": "ok"},
            "risk_reward": {"level": "⚠️", "notes": "asym"},
        },
        "blind_spots": [],
    }


class TestExtremeUpThresholds:
    def test_ratio_gt_5_triggers_up(self):
        ev = apply_env_guardrail(_base_eval(), {"lu_ld_ratio": 5.1, "ad_ratio": 1.0})
        rules = [b["rule"] for b in ev["blind_spots"]]
        assert "extreme_sentiment_up" in rules
        assert "extreme_sentiment_down" not in rules

    def test_ratio_eq_5_does_not_trigger_up(self):
        ev = apply_env_guardrail(_base_eval(), {"lu_ld_ratio": 5.0, "ad_ratio": 1.0})
        rules = [b["rule"] for b in ev["blind_spots"]]
        assert "extreme_sentiment_up" not in rules

    def test_no_limit_down_note_treated_as_extreme_up(self):
        snap = {
            "lu_ld_ratio": None,
            "lu_ld_note": "no_limit_down",
            "limit_up_count": 40,
            "limit_down_count": 0,
            "ad_ratio": 1.2,
        }
        assert _is_extreme_sentiment_up(snap) is True
        ev = apply_env_guardrail(_base_eval(), snap)
        rules = [b["rule"] for b in ev["blind_spots"]]
        assert "extreme_sentiment_up" in rules
        note = next(b["note"] for b in ev["blind_spots"] if b["rule"] == "extreme_sentiment_up")
        assert "无跌停" in note


class TestPanicThreshold:
    def test_ratio_lt_0_2_triggers_down(self):
        """Spec 涨跌停比 <1:5 → ratio < 0.2."""
        ev = apply_env_guardrail(_base_eval(), {"lu_ld_ratio": 0.19, "ad_ratio": 1.0})
        rules = [b["rule"] for b in ev["blind_spots"]]
        assert "extreme_sentiment_down" in rules

    def test_ratio_eq_0_2_does_not_trigger_down(self):
        ev = apply_env_guardrail(_base_eval(), {"lu_ld_ratio": 0.2, "ad_ratio": 1.0})
        rules = [b["rule"] for b in ev["blind_spots"]]
        assert "extreme_sentiment_down" not in rules

    def test_ratio_between_0_2_and_0_3_no_longer_panic(self):
        """Former bug: <0.3 treated as panic; now only <0.2."""
        ev = apply_env_guardrail(_base_eval(), {"lu_ld_ratio": 0.25, "ad_ratio": 1.0})
        rules = [b["rule"] for b in ev["blind_spots"]]
        assert "extreme_sentiment_down" not in rules


class TestDimensionsUnchanged:
    def test_dimension_levels_preserved(self):
        base = _base_eval()
        before = copy.deepcopy(base["dimensions"])
        snap = {
            "label_leverage": "中性 去杠杆",
            "lu_ld_ratio": 9.8,
            "ad_ratio": 0.5,
        }
        out = apply_env_guardrail(base, snap)
        assert out["dimensions"] == before
        assert len(out["blind_spots"]) >= 3  # deleveraging + extreme up + breadth


class TestAppendOnlyBlindSpots:
    def test_appends_to_existing(self):
        ev = _base_eval()
        ev["blind_spots"] = [{"rule": "preexisting", "note": "keep me"}]
        out = apply_env_guardrail(ev, {"lu_ld_ratio": 6.0, "ad_ratio": 1.0})
        rules = [b["rule"] for b in out["blind_spots"]]
        assert rules[0] == "preexisting"
        assert "extreme_sentiment_up" in rules

    def test_snap_none_returns_unchanged(self):
        ev = _base_eval()
        out = apply_env_guardrail(ev, None)
        assert out["blind_spots"] == []


class TestDeleveragingLabel:
    def test_mid_range_cold_buy_emits_quganggan(self):
        snap = {
            "margin_balance": 18000.0,  # 亿元，中性区间
            "margin_buy_amount": 400.0,  # pct ≈ 0.022 < 0.03 → 偏冷
        }
        _compute_labels(snap)
        assert snap["label_leverage"] is not None
        assert "去杠杆" in snap["label_leverage"]

    def test_guardrail_triggers_on_quganggan_label(self):
        ev = apply_env_guardrail(
            _base_eval(),
            {"label_leverage": "中性 去杠杆", "ad_ratio": 1.0},
        )
        rules = [b["rule"] for b in ev["blind_spots"]]
        assert "deleveraging" in rules

    def test_guardrail_triggers_on_pianleng_label(self):
        ev = apply_env_guardrail(
            _base_eval(),
            {"label_leverage": "高杠杆 偏冷", "ad_ratio": 1.0},
        )
        rules = [b["rule"] for b in ev["blind_spots"]]
        assert "deleveraging" in rules

    def test_sentiment_label_extreme_on_no_limit_down(self):
        snap = {
            "lu_ld_ratio": None,
            "lu_ld_note": "no_limit_down",
            "limit_down_count": 0,
        }
        _compute_labels(snap)
        assert snap["label_sentiment"] == "极端亢奋"


class TestMarketBreadth:
    def test_ad_ratio_lt_0_6_triggers_breadth(self):
        ev = apply_env_guardrail(_base_eval(), {"ad_ratio": 0.59, "lu_ld_ratio": 1.0})
        rules = [b["rule"] for b in ev["blind_spots"]]
        assert "market_breadth" in rules
        note = next(b["note"] for b in ev["blind_spots"] if b["rule"] == "market_breadth")
        assert "涨跌比" in note

    def test_ad_ratio_eq_0_6_does_not_trigger(self):
        ev = apply_env_guardrail(_base_eval(), {"ad_ratio": 0.6, "lu_ld_ratio": 1.0})
        rules = [b["rule"] for b in ev["blind_spots"]]
        assert "market_breadth" not in rules
