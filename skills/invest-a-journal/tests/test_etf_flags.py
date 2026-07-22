"""Unit tests for ETF _auto_flags + 588000 hedge map (no network)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_LIB = Path(__file__).resolve().parent.parent / "scripts" / "lib"
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

from etf_data import (  # noqa: E402
    ETF_HEDGE_MAP,
    _auto_flags,
    _em_to_premium_discount,
)


def _base(**overrides) -> dict:
    r: dict = {
        "aum": 10.0,
        "premium_discount": 0.0,
        "hedge_coverage": {"coverage": "high"},
    }
    r.update(overrides)
    return r


class TestEmToPremiumDiscount:
    """EM 基金折价率 must be negated so + = 溢价 in premium_discount."""

    def test_em_positive_is_discount_normalized_negative(self):
        assert _em_to_premium_discount(0.11) == pytest.approx(-0.11)

    def test_em_negative_is_premium_normalized_positive(self):
        assert _em_to_premium_discount(-3.2) == pytest.approx(3.2)

    def test_em_none_stays_none(self):
        assert _em_to_premium_discount(None) is None


class TestAutoFlagsPremiumDiscount:
    """premium_discount is normalized: + = 溢价, - = 折价."""

    def test_premium_gt_2_triggers_yijia(self):
        r = _base(premium_discount=3.0)
        _auto_flags(r)
        assert any("溢价" in f and "3.0" in f for f in r["flags"])
        assert not any("折价" in f for f in r["flags"])

    def test_discount_lt_minus_2_triggers_zhejia(self):
        r = _base(premium_discount=-3.0)
        _auto_flags(r)
        assert any("折价" in f and "3.0" in f for f in r["flags"])
        assert not any("溢价" in f for f in r["flags"])

    def test_near_zero_no_pd_flag(self):
        r = _base(premium_discount=0.1)
        _auto_flags(r)
        assert not any("溢价" in f or "折价" in f for f in r["flags"])


class TestAutoFlagsAumAndHedge:
    def test_aum_below_2(self):
        r = _base(aum=1.5)
        _auto_flags(r)
        assert any("AUM" in f for f in r["flags"])

    def test_coverage_none(self):
        r = _base(hedge_coverage={"coverage": "none"})
        _auto_flags(r)
        assert any("无可用" in f for f in r["flags"])


class Test588000HedgeMap:
    def test_has_star50_options_and_high_coverage(self):
        entry = ETF_HEDGE_MAP["588000"]
        assert entry["options"] == "科创50ETF期权"
        assert entry["coverage"] == "high"
