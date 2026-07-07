"""Tests for lib.participant_scan — 参与者行为扫描。"""

from __future__ import annotations

import pytest

from lib.participant_scan import build_participant_behavior_section


def _dims(**kwargs):
  base = {
      "quote": {"data": {"change_pct": 3.5}},
      "holder_changes": {"data": [
          {"holder_name": "A", "direction": "增持", "ann_date": "20260101"},
          {"holder_name": "B", "direction": "增持", "ann_date": "20260201"},
          {"holder_name": "C", "direction": "增持", "ann_date": "20260301"},
      ]},
  }
  base.update(kwargs)
  return base


class TestParticipantBehaviorScan:
    def test_section_nonempty_with_market_structure(self):
        ms = {
            "northbound": {"net_sum_10d": 50_000_000, "days": 10, "source": "tushare.hsgt_top10"},
            "moneyflow": {"net_sum_10d": 30_000_000, "source": "tushare.moneyflow"},
            "margin": {"change_pct": 2.5, "source": "tushare.margin_detail"},
        }
        text = build_participant_behavior_section({}, "600176", ms, _dims())
        assert "参与者行为扫描" in text
        assert "北向" in text
        assert "主力" in text
        assert "杠杆资金" in text

    def test_all_missing_shows_law5_message(self):
        text = build_participant_behavior_section({}, "600176", {}, {})
        assert "未获取到任何有效数据" in text

    def test_no_trading_advice_words(self):
        ms = {
            "northbound": {"net_sum_10d": -80_000_000, "days": 10, "source": "akshare"},
            "moneyflow": {"net_sum_10d": 40_000_000, "source": "tushare.moneyflow"},
        }
        text = build_participant_behavior_section({}, "600176", ms, _dims())
        forbidden = ("建议买入", "建议卖出", "建仓", "目标仓位")
        for word in forbidden:
            assert word not in text

    def test_cv_divergence_note_when_nb_mf_opposite(self):
        ms = {
            "northbound": {"net_sum_10d": 10_000_000, "days": 10, "source": "tushare"},
            "moneyflow": {"net_sum_10d": -5_000_000, "source": "tushare.moneyflow"},
        }
        text = build_participant_behavior_section({}, "600176", ms, _dims())
        assert "方向相反" in text


class TestParticipantScanRenderIntegration:
    def test_full_report_includes_section(self):
        from conftest import make_store_collection
        from lib.render import render_report_v3

        c = make_store_collection("600176")
        c["market_structure"] = {
            "northbound": {"net_sum_10d": 1e8, "days": 10, "source": "tushare"},
            "moneyflow": {"net_sum_10d": 5e7, "source": "tushare.moneyflow"},
            "availability": {},
        }
        text = render_report_v3(c, "600176", mode="full")
        assert "参与者行为扫描" in text

    def test_brief_report_skips_section(self):
        from conftest import make_store_collection
        from lib.render import render_report_v3

        c = make_store_collection("600176")
        c["market_structure"] = {
            "northbound": {"net_sum_10d": 1e8, "days": 10, "source": "tushare"},
            "availability": {},
        }
        text = render_report_v3(c, "600176", mode="brief")
        assert "参与者行为扫描" not in text
