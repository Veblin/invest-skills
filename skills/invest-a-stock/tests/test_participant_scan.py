"""Tests for lib.participant_scan — 参与者行为扫描。"""

from __future__ import annotations

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
            "moneyflow": {"net_sum_5d": 30_000_000, "source": "tushare.moneyflow"},
            "margin": {"change_pct": 2.5, "source": "tushare.margin_detail"},
        }
        text = build_participant_behavior_section({}, "600176", ms, _dims())
        assert "参与者行为扫描" in text
        assert "北向" in text
        assert "主力" in text
        assert "近5日主力净额" in text
        assert "杠杆资金" in text

    def test_all_missing_shows_law5_message(self):
        text = build_participant_behavior_section({}, "600176", {}, {})
        assert "未获取到任何有效数据" in text

    def test_no_trading_advice_words(self):
        ms = {
            "northbound": {"net_sum_10d": -80_000_000, "days": 10, "source": "akshare"},
            "moneyflow": {"net_sum_5d": 40_000_000, "source": "tushare.moneyflow"},
        }
        text = build_participant_behavior_section({}, "600176", ms, _dims())
        forbidden = ("建议买入", "建议卖出", "建仓", "目标仓位")
        for word in forbidden:
            assert word not in text

    def test_cv_divergence_note_when_nb_mf_opposite(self):
        ms = {
            "northbound": {"net_sum_10d": 10_000_000, "days": 10, "source": "tushare"},
            "moneyflow": {"net_sum_5d": -5_000_000, "source": "tushare.moneyflow"},
        }
        text = build_participant_behavior_section({}, "600176", ms, _dims())
        assert "交叉验证（参与者行为）" in text
        assert "方向相反" in text
        assert "北向近10日 vs 主力近5日" in text

    def test_cv_convergence_uses_neutral_heading(self):
        ms = {
            "northbound": {"net_sum_10d": 10_000_000, "days": 10, "source": "tushare"},
            "moneyflow": {"net_sum_5d": 5_000_000, "source": "tushare.moneyflow"},
        }
        text = build_participant_behavior_section({}, "600176", ms, _dims())
        assert "交叉验证（参与者行为）" in text
        assert "行为分歧" not in text
        assert "方向一致" in text

    def test_cv_marks_gap_when_moneyflow_zero(self):
        ms = {
            "northbound": {"net_sum_10d": 10_000_000, "days": 10, "source": "tushare"},
            "moneyflow": {"net_sum_5d": 0, "source": "tushare.moneyflow"},
        }
        text = build_participant_behavior_section({}, "600176", ms, _dims())
        assert "资金数据不完整" in text

    def test_cv_both_zero_reports_convergence(self):
        ms = {
            "northbound": {"net_sum_10d": 0, "days": 10, "source": "tushare"},
            "moneyflow": {"net_sum_5d": 0, "source": "tushare.moneyflow"},
        }
        text = build_participant_behavior_section({}, "600176", ms, _dims())
        assert "方向一致" in text
        assert "资金数据不完整" not in text

    def test_moneyflow_fallback_uses_10d_label(self):
        ms = {
            "moneyflow": {"net_sum_10d": 30_000_000, "source": "tushare.moneyflow"},
        }
        text = build_participant_behavior_section({}, "600176", ms, _dims())
        assert "近10日主力净额" in text
        assert "近5日主力净额" not in text

    def test_cv_quote_divergence_note_when_northbound_conflicts_with_price(self):
        ms = {
            "northbound": {"net_sum_10d": 10_000_000, "days": 10, "source": "tushare"},
            "moneyflow": {"net_sum_5d": 5_000_000, "source": "tushare.moneyflow"},
        }
        dims = _dims(quote={"data": {"change_pct": -3.2}})
        text = build_participant_behavior_section({}, "600176", ms, dims)
        assert "北向净流入与股价 -3.2% 背离" in text

    def test_fallback_row_when_shareholders_present_without_holder_changes(self):
        ms = {}
        dims = _dims(
            holder_changes={"data": []},
            shareholders={"data": [{"holder_name": "大股东A"}, {"holder_name": "大股东B"}]},
        )
        text = build_participant_behavior_section({}, "600176", ms, dims)
        assert "股东结构" in text
        assert "前十大流通股东记录 2 条" in text

    def test_turnover_margin_and_pcr_rows_render(self):
        ms = {
            "margin": {"change_pct": 2.5, "source": "tushare.margin_detail"},
            "turnover": {"percentile_1y": 87.4, "source": "market.turnover"},
            "put_call_ratio": {"ratio": 1.18, "source": "market.pcr"},
        }
        text = build_participant_behavior_section({}, "600176", ms, _dims(holder_changes={"data": []}))
        assert "杠杆资金" in text
        assert "换手（散户活跃度代理）" in text
        assert "期权情绪代理（PCR）" in text


class TestParticipantScanRenderIntegration:
    def test_full_report_includes_section(self):
        from conftest import make_store_collection
        from lib.render import render_report_v3

        c = make_store_collection("600176")
        c["market_structure"] = {
            "northbound": {"net_sum_10d": 1e8, "days": 10, "source": "tushare"},
            "moneyflow": {"net_sum_5d": 5e7, "source": "tushare.moneyflow"},
            "availability": {},
        }
        text = render_report_v3(c, "600176", mode="full")
        assert "参与者行为扫描" in text
        assert "近5日主力净额" in text

    def test_render_shows_moneyflow_when_only_net_sum_10d(self):
        from conftest import make_store_collection
        from lib.render import render_report_v3

        c = make_store_collection("600176")
        c["market_structure"] = {
            "moneyflow": {"net_sum_10d": 30_000_000, "source": "tushare.moneyflow"},
            "availability": {},
        }
        text = render_report_v3(c, "600176", mode="full")
        assert "近10日主力净额" in text
        assert "近5日主力净额" not in text
        section2_start = text.find("## 2. 动态驱动分析")
        section2_end = text.find("## 3.", section2_start)
        section2 = text[section2_start:section2_end]
        mf_rows = [line for line in section2.splitlines() if "资金（主力）" in line]
        assert mf_rows, "missing 资金（主力） driver row"
        assert "近10日主力净额" in mf_rows[0]
        assert "[数据源不可用，该因子跳过]" not in mf_rows[0]

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
