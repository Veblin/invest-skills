"""v0.1.3-alpha Phase 1 测试：市场结构采集、v3 九模块报告、LAW 16 合规。"""

from __future__ import annotations

import re
from unittest.mock import MagicMock, patch

import pytest

from conftest import FORBIDDEN_SIGNAL_WORDS
from fixtures.collections import collection_v2_minimal, make_kline_rows


def _analysis_body_without_legal_disclaimers(text: str) -> str:
    """剔除风险/免责声明行后再做禁词检查。"""
    kept: list[str] = []
    for line in text.splitlines():
        if line.startswith(">") and any(
            p in line for p in ("不构成", "免责声明", "风险提示", "非交易信号")
        ):
            continue
        kept.append(line)
    return "\n".join(kept)


def _collection_v3() -> dict:
    c = collection_v2_minimal()
    c["market_structure"] = {
        "sw_index": {
            "index_code": "851121.SI",
            "return_20d_pct": 3.5,
            "stock_return_20d_pct": 4.7,
            "relative_vs_benchmark_pct": 2.1,
            "stock_vs_industry_pct": 1.2,
            "source": "tushare.sw_daily",
        },
        "northbound": {
            "net_sum_10d": 1_000_000.0,
            "days": 10,
            "source": "tushare.hsgt_top10",
        },
        "moneyflow": {"net_sum_5d": -500_000.0, "source": "tushare.moneyflow"},
        "margin": {"change_pct": 1.5, "source": "tushare.margin_detail"},
        "turnover": {
            "avg_5d": 2.1,
            "avg_60d": 1.8,
            "ratio_5_60": 1.17,
            "percentile_60d": 55.0,
            "source": "tushare.daily_basic",
        },
        "erp": {"raw": 4.2, "percentile_5y": 72.0, "source": "tushare.index_dailybasic+FRED.DGS10"},
        "availability": {
            "sw_index": "available",
            "northbound": "available",
            "moneyflow": "available",
        },
    }
    return c


class TestCollectMarketStructure:
    # collect_market_structure 中走 Tushare tc.query 的子源（pmi 为 akshare 独立源）
    _TUSHARE_MS_KEYS = (
        "sw_index", "northbound", "margin", "moneyflow", "turnover", "erp",
        "put_call_ratio", "short_margin", "new_high_ratio", "etf_flow",
    )

    def test_collect_market_structure_degrades_gracefully(self):
        from lib import collector

        mock_tc = MagicMock()
        mock_tc.query.side_effect = RuntimeError("permission denied")

        with patch.object(collector.env, "is_tushare_available", return_value=True), patch.object(
            collector.env, "get_config", return_value={"TUSHARE_TOKEN": "x" * 32}
        ), patch.object(collector, "_tushare_client", return_value=mock_tc), patch.object(
            collector, "_q_akshare_northbound", return_value=None
        ):
            result = collector.collect_market_structure("600176", industry="电气设备")

        assert result["sw_index"] is None
        assert result["northbound"] is None
        assert all(
            result["availability"][k].startswith("unavailable")
            for k in self._TUSHARE_MS_KEYS
        )

    def test_collect_market_structure_no_token(self):
        from lib import collector

        with patch.object(collector.env, "is_tushare_available", return_value=False):
            result = collector.collect_market_structure("600176")

        assert result["availability"]["sw_index"].startswith("unavailable")


class TestCollectorHelpers:
    def test_extract_industry_from_basic_info_akshare_key(self):
        from lib.collector import extract_industry_from_basic_info

        assert extract_industry_from_basic_info({"行业": "玻璃玻纤"}) == "玻璃玻纤"
        assert extract_industry_from_basic_info({"industry": "电气设备"}) == "电气设备"
        assert extract_industry_from_basic_info({}) is None

    def test_fred_date_format(self):
        from lib.collector import _fred_date

        assert _fred_date("20260614") == "2026-06-14"

    def test_ms_lookup_sw_index_prefers_exact_match(self):
        from lib.collector import _ms_lookup_sw_index_code

        mock_tc = MagicMock()
        mock_tc.query.return_value = MagicMock(
            empty=False,
            iterrows=lambda: iter([
                (0, {"industry_name": "设备", "index_code": "BAD.SI"}),
                (1, {"industry_name": "电气设备", "index_code": "851121.SI"}),
            ]),
        )
        assert _ms_lookup_sw_index_code(mock_tc, "电气设备") == "851121.SI"

    def test_ms_lookup_sw_index_no_substring_false_positive(self):
        from lib.collector import _ms_lookup_sw_index_code

        mock_tc = MagicMock()
        mock_tc.query.return_value = MagicMock(
            empty=False,
            iterrows=lambda: iter([
                (0, {"industry_name": "设备", "index_code": "BAD.SI"}),
            ]),
        )
        assert _ms_lookup_sw_index_code(mock_tc, "电气设备") is None

    def test_ms_lookup_sw_index_rejects_empty_code(self):
        from lib.collector import _ms_lookup_sw_index_code_at_level

        mock_tc = MagicMock()
        mock_tc.query.return_value = MagicMock(
            empty=False,
            iterrows=lambda: iter([
                (0, {"industry_name": "电气设备", "index_code": ""}),
            ]),
        )
        assert _ms_lookup_sw_index_code_at_level(mock_tc, "电气设备", "L2") is None

    def test_ms_fetch_margin_uses_margin_detail(self):
        from lib import collector

        mock_tc = MagicMock()
        mock_tc.query.return_value = MagicMock(
            empty=False,
            sort_values=lambda *_a, **_k: mock_tc.query.return_value,
        )
        mock_tc.query.return_value.to_dict.return_value = [
            {"trade_date": "20260601", "rzye": 100.0},
            {"trade_date": "20260610", "rzye": 110.0},
        ]

        result = collector._ms_fetch_margin(mock_tc, "600176")
        assert result is not None
        assert result["source"] == "tushare.margin_detail"
        assert result["change_pct"] == 10.0
        mock_tc.query.assert_called_once()
        assert mock_tc.query.call_args[0][0] == "margin_detail"

    def test_attach_market_structure_writes_collection(self):
        from lib import collector

        c = collection_v2_minimal()
        with patch.object(
            collector, "collect_market_structure", return_value={"availability": {}}
        ) as mock_ms:
            collector.attach_market_structure(c, "600176")
        mock_ms.assert_called_once_with("600176", industry="电气设备")
        assert "market_structure" in c

    def test_moneyflow_uses_net_mf_amount(self):
        from lib.collector import _normalize_northbound_records, _ms_fetch_moneyflow

        rows = [{"trade_date": "20260101", "net_mf_amount": 100.0}]
        out = _normalize_northbound_records(rows, "tushare.moneyflow")
        assert out[0]["net_mf_amount"] == 1_000_000.0

        with patch("lib.collector._q_tushare_moneyflow") as mock_q:
            mock_q.return_value = [
                {"trade_date": "20260101", "net_mf_amount": 100.0},
                {"trade_date": "20260102", "net_mf_amount": 200.0},
                {"trade_date": "20260103", "net_mf_amount": 300.0},
                {"trade_date": "20260104", "net_mf_amount": 400.0},
                {"trade_date": "20260105", "net_mf_amount": 500.0},
                {"trade_date": "20260110", "net_mf_amount": 1000.0},
            ]
            result = _ms_fetch_moneyflow(MagicMock(), "600176")
        assert result["net_sum_5d"] == 2400.0  # 最近 5 日：01-10, 01-05..01-02

    def test_ms_fetch_northbound_uses_hsgt_top10(self):
        from lib import collector

        mock_records = [
            {"trade_date": f"202606{10 - i:02d}", "net_mf_amount": float(100 - i * 10)}
            for i in range(6)
        ]

        with patch("lib.collector._q_tushare_hsgt_top10") as mock_q, patch(
            "lib.collector._q_akshare_northbound", return_value=None
        ):
            mock_q.return_value = mock_records
            result = collector._ms_fetch_northbound_stock(MagicMock(), "600176")

        assert result is not None
        assert result["source"] == "tushare.hsgt_top10"
        assert result["net_sum_10d"] == sum(100 - i * 10 for i in range(6))
        mock_q.assert_called_once_with("600176")

    def test_ms_fetch_northbound_sparse_hsgt_falls_back_to_akshare(self):
        from lib import collector

        sparse = [{"trade_date": "20260610", "net_mf_amount": 100.0}]
        akshare_records = [
            {"trade_date": f"202606{10 - i:02d}", "net_mf_vol": float(50 + i)}
            for i in range(6)
        ]

        with patch("lib.collector._q_tushare_hsgt_top10", return_value=sparse), patch(
            "lib.collector._q_akshare_northbound", return_value=akshare_records
        ):
            result = collector._ms_fetch_northbound_stock(MagicMock(), "600176")

        assert result is not None
        assert result["source"] == "akshare.stock_hsgt_individual_em"

    def test_northbound_and_moneyflow_sources_distinct(self):
        from lib import collector

        with patch("lib.collector._q_tushare_hsgt_top10") as mock_nb, patch(
            "lib.collector._q_tushare_moneyflow"
        ) as mock_mf, patch.object(collector.env, "is_tushare_available", return_value=True), patch.object(
            collector.env, "get_config", return_value={"TUSHARE_TOKEN": "x" * 32}
        ), patch.object(collector, "_tushare_client", return_value=MagicMock()):
            mock_nb.return_value = [
                {"trade_date": f"202606{10 - i:02d}", "net_mf_amount": float(i + 1)}
                for i in range(6)
            ]
            mock_mf.return_value = [{"trade_date": "20260610", "net_mf_amount": 10000.0}]
            result = collector.collect_market_structure("600176", industry="电气设备")

        assert result["northbound"]["source"] == "tushare.hsgt_top10"
        assert result["moneyflow"]["source"] == "tushare.moneyflow"
        assert result["northbound"]["source"] != result["moneyflow"]["source"]

    def test_merge_cashflow_into_financials(self):
        from lib.collector import _merge_cashflow_into_financials

        fin = [{"end_date": "20241231", "net_profit": 100.0}]
        cf = [{"end_date": "20241231", "n_cashflow_act": 80.0}]
        merged = _merge_cashflow_into_financials(fin, cf)
        assert merged[0]["ocf"] == 80.0
        assert merged[0]["n_cashflow_act"] == 80.0

    def test_erp_uses_akshare_when_fred_unavailable(self):
        from lib import collector

        mock_df = MagicMock()
        mock_df.empty = False
        mock_df.sort_values.return_value = mock_df
        mock_df.to_dict.return_value = [
            {"trade_date": "20260101", "pe_ttm": 10.0},
            {"trade_date": "20260102", "pe_ttm": 12.0},
        ]
        mock_tc = MagicMock()
        mock_tc.query.return_value = mock_df

        with patch("lib.collector._ms_fetch_fred_dgs10_series", return_value=[]), patch(
            "lib.collector._ms_fetch_akshare_cn10y_series",
            return_value=[("2026-01-01", 2.5), ("2026-01-02", 2.6)],
        ):
            result = collector._ms_fetch_erp(mock_tc, {})

        assert result is not None
        assert "akshare.bond_zh_us_rate" in result["source"]
        assert result["dgs10"] == 2.6

    def test_dgs10_for_trade_date_lookback(self):
        from lib.collector import _dgs10_for_trade_date

        by_date = {"2026-06-12": 4.5, "2026-06-11": 4.4}
        assert _dgs10_for_trade_date(by_date, "2026-06-13") == 4.5
        assert _dgs10_for_trade_date(by_date, "2026-06-01", lookback_days=5) is None

    def test_ms_lookup_sw_falls_back_to_l1(self):
        from lib.collector import _ms_lookup_sw_index_code

        mock_tc = MagicMock()

        def _query(api, **kwargs):
            level = kwargs.get("level")
            m = MagicMock()
            m.empty = False
            if level == "L2":
                m.iterrows = lambda: iter([])
            else:
                m.iterrows = lambda: iter([
                    (0, {"industry_name": "电气设备", "index_code": "L1.SI"}),
                ])
            return m

        mock_tc.query.side_effect = _query
        assert _ms_lookup_sw_index_code(mock_tc, "电气设备") == "L1.SI"

    def test_v3_price_window_label_short_kline(self):
        from lib.render import _v3_price_window_label, render_report_v3
        from fixtures.collections import collection_kline_insufficient

        assert "不足 20 日" in _v3_price_window_label(14)
        c = collection_kline_insufficient()
        c["market_structure"] = {"availability": {}}
        text = render_report_v3(c, "600176")
        assert "K 线不足 20 日" in text


class TestRenderV3:
    def test_cv1_with_ocf_not_gap(self):
        from lib.render import render_report_v3

        text = render_report_v3(_collection_v3(), "600176")
        cv1_block = text.split("CV-1")[1].split("CV-3")[0]
        assert "经营现金流字段不可得" not in cv1_block
        assert "净利润" in cv1_block

    def test_cv6_labels_net_profit_not_roe(self):
        from lib.render import render_report_v3

        text = render_report_v3(_collection_v3(), "600176")
        assert "净利润环比方向" in text
        assert "ROE 方向" not in text

    def test_cross_validation_convergence(self):
        from lib.schema import CrossValidation

        text = CrossValidation(
            "convergence", "CV-1", "净利润与现金流同向", "同向", "中",
        ).to_markdown()
        assert "🟢" in text
        assert "印证" in text

        div = CrossValidation("divergence", "CV-3", "PE与PB分歧", "分歧", "低").to_markdown()
        assert "🟡" in div

        gap = CrossValidation("gap", "CV-7", "数据缺口", "缺口", "低").to_markdown()
        assert "🔴" in gap

    def test_v3_forbidden_signal_words(self):
        from lib.render import render_report_v3

        text = render_report_v3(_collection_v3(), "600176")
        body = _analysis_body_without_legal_disclaimers(text)
        for word in FORBIDDEN_SIGNAL_WORDS:
            assert word not in body, f"v3 report must not contain forbidden word: {word}"

    def test_northbound_hsgt_label_uses_listing_days(self):
        from lib.render import render_report_v3

        c = _collection_v3()
        c["market_structure"]["northbound"] = {
            "net_sum_10d": 1_000_000.0,
            "days": 2,
            "source": "tushare.hsgt_top10",
        }
        text = render_report_v3(c, "600176")
        drivers = text.split("## 2.")[1].split("## 3.")[0]
        assert "上榜日累计" in drivers
        assert "2 个上榜日" in drivers
        assert "近 10 日净额" not in drivers

    def test_erp_partial_rendering(self):
        from lib.render import render_report_v3

        c = _collection_v3()
        c["market_structure"]["erp"] = {
            "raw": 3.1,
            "percentile_5y": 55.0,
            "erp_days": 30,
            "partial": True,
            "source": "tushare.index_dailybasic+akshare.bond_zh_us_rate",
        }
        text = render_report_v3(c, "600176")
        mod3 = text.split("## 3.")[1].split("## 4.")[0]
        assert "样本日不足，分位仅供参考" in mod3
        assert "10Y 国债来源: akshare.bond_zh_us_rate" in mod3

    def test_market_structure_has_law12_evidence_block(self):
        from lib.render import render_report_v3

        text = render_report_v3(_collection_v3(), "600176")
        mod3 = text.split("## 3.")[1].split("## 4.")[0]
        assert "综合证据强度" in mod3
        assert "市场结构呈现行业相对强弱" in mod3

    def test_law11_trigger_a_requires_20d_window(self):
        from lib.render import _section_research_question

        c = collection_v2_minimal()
        c["market_structure"] = {"availability": {}}
        for dim in c["dimensions"]:
            if dim["dimension"] == "kline":
                rows = make_kline_rows(10)
                rows[0]["close"] = 100.0
                rows[-1]["close"] = 120.0
                dim["data"] = rows
        text = _section_research_question(c, "600176")
        assert "A 变化驱动" not in text

    def test_law11_trigger_d_52w_extreme(self):
        from lib.render import _section_research_question

        c = collection_v2_minimal()
        c["market_structure"] = {"availability": {}}
        for dim in c["dimensions"]:
            if dim["dimension"] == "kline":
                dim["data"] = make_kline_rows(250)
        text = _section_research_question(c, "600176")
        assert "D 趋势结构驱动" in text

    def test_left_right_no_single_conclusion(self):
        from lib.render import render_report_v3

        text = render_report_v3(_collection_v3(), "600176")
        assert not re.search(r"当前是左侧|当前是右侧", text)
        assert "☑" not in text.split("## 6.")[1].split("## 7.")[0]
        assert "阶段对照" in text

    def test_left_right_module6_has_cv7(self):
        from lib.render import render_report_v3

        c = _collection_v3()
        text = render_report_v3(c, "600176")
        mod6 = text.split("## 6.")[1].split("## 7.")[0]
        assert "CV-7" in mod6

    def test_snapshot_multi_source_consistency(self):
        from lib.render import render_report_v3

        c = _collection_v3()
        for dim in c["dimensions"]:
            if dim["dimension"] in ("quote", "valuation"):
                dim["_meta"] = {
                    **dim.get("_meta", {}),
                    "multi_source": True,
                    "all_sources": [
                        {"source": "tushare.daily", "data_available": True},
                        {"source": "tencent_finance", "data_available": True},
                    ],
                }
        text = render_report_v3(c, "600176")
        snap = text.split("## 1.")[1].split("## 2.")[0]
        assert "### 多源一致性" in snap
        assert re.search(r"[🟢🟡🔴]", snap)

    def test_dynamic_drivers_multiple_explanations(self):
        from lib.render import render_report_v3

        text = render_report_v3(_collection_v3(), "600176")
        drivers = text.split("## 2.")[1].split("## 3.")[0]
        labels = re.findall(r"→ 解释 ([A-E])：", drivers)
        assert len(labels) >= 2
        assert "主导因子（声明）:" in drivers
        assert "净利润环比" in drivers or "基本面" in drivers

    def test_render_md_attaches_market_structure(self):
        from lib import collector
        from lib.render import render

        c = collection_v2_minimal()
        assert "market_structure" not in c
        with patch.object(collector, "attach_market_structure") as mock_ms:
            mock_ms.side_effect = lambda col, sym: col.update(
                market_structure={"availability": {}}
            ) or col["market_structure"]
            render(c, "600176", "md")
        mock_ms.assert_called_once_with(c, "600176")
        assert "market_structure" in c

    def test_schema_v013_dataclasses(self):
        from lib.schema import CrossValidation, DriverFactor, ProbabilityStructure

        df = DriverFactor("基本面", "净利润环比", "↑正向", "⚠️", "financials")
        assert df.category == "基本面"
        assert "| 基本面 |" in df.to_matrix_row()
        cv = CrossValidation("convergence", "CV-1", "净利润 vs 现金流", "同向", "中")
        assert cv.status == "convergence"
        assert "🟢" in cv.to_markdown()
        ps = ProbabilityStructure(left_items=["① PE 低"], right_items=["① MA 多头"])
        assert len(ps.left_items) == 1

    def test_dynamic_drivers_matrix_rows(self):
        from lib.render import render_report_v3

        text = render_report_v3(_collection_v3(), "600176")
        drivers = text.split("## 2.")[1].split("## 3.")[0]
        matrix_lines = [
            ln for ln in drivers.splitlines()
            if ln.startswith("|") and "因子类别" not in ln and "---" not in ln
        ]
        assert len(matrix_lines) >= 8

    def test_render_report_v3_nine_modules(self):
        from lib.render import render_report_v3

        text = render_report_v3(_collection_v3(), "600176")
        # LAW 17: 标题现含动态数据，改用前缀 + 关键词验证
        checks = [
            ("## 0.", "标题含 section 0"),
            ("## 1.", "标题含 section 1"),
            ("## 2.", "标题含 section 2"),
            ("## 3.", "标题含 section 3"),
            ("## 4.", "标题含 section 4"),
            ("## 5.", "标题含 section 5"),
            ("## 6.", "标题含 section 6"),
            ("## 7.", "标题含 section 7"),
            ("## 8.", "标题含 section 8"),
            ("**结论：**", "段首主旨句"),
        ]
        for prefix, desc in checks:
            assert prefix in text, f"缺失: {desc}"

    def test_cv5_uses_stock_vs_industry_direction(self):
        from lib.render import render_report_v3

        c = _collection_v3()
        text = render_report_v3(c, "600176")
        assert "CV-5" in text
        assert "同向" in text
        assert "🟢" in text

        c["market_structure"]["sw_index"]["stock_return_20d_pct"] = -2.0
        text_div = render_report_v3(c, "600176")
        assert "反向" in text_div
        assert "🟡" in text_div


class TestInvestReportCLI:
    def test_report_default_emit_is_md(self):
        from invest import build_parser

        parser = build_parser()
        args = parser.parse_args(["report", "000001"])
        assert args.emit == "md"
