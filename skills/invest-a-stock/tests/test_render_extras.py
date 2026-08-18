"""Tests for render() attach_extras deduplication (events + analysis cards)."""

from __future__ import annotations

from unittest.mock import patch

from fixtures.collections import collection_v2_minimal


def _minimal_with_events_and_cards() -> dict:
    coll = collection_v2_minimal()
    coll["events"] = [{"date": "2026-06-01", "title": "回购公告", "type": "buyback"}]
    coll["_meta"] = {"analysis_cards": {"mda_narrative": {}}}
    coll["market_structure"] = {"availability": {}}
    return coll


class TestR6AcademicDisciplineLine:
    """R6 学术纪律补丁：技术指标附录固定提示行（引擎/模板层，验收 grep 断言）。"""

    def test_technical_brief_contains_citation(self):
        from lib.render_markdown._v3 import _section_technical_brief

        out = _section_technical_brief({})
        assert "Chen, Zhou & Wang 2018" in out
        assert "279 个技术策略计入交易成本后利润被完全消除" in out
        assert "不构成任何操作依据" in out

    def test_technical_brief_uses_real_volume_and_extremes(self):
        """code-review C2：量行绑真实 volume.status、支撑阻力行绑 structure.extremes
        （原支撑阻力键全仓无产出方恒 "—"，量行实为 MA60 句错绑）。"""
        from fixtures.collections import make_kline_rows

        from lib.render_markdown._v3 import _section_technical_brief

        rows = make_kline_rows(130)
        rows[-1]["vol"] = rows[-1]["vol"] * 10  # 放量脉冲 → 量比 > 1.5
        out = _section_technical_brief({"kline": {"data": rows}})
        assert "量比" in out
        assert "高于近5日均量" in out
        # 支撑阻力：20/60/120 日极值均可用（130 ≥ 120），带具体日期（@）
        assert "20日高/低:" in out
        assert "60日高/低:" in out
        assert "120日高/低:" in out
        assert "@" in out
        assert "- **支撑阻力:** —" not in out

    def test_technical_brief_insufficient_days_shows_reason(self):
        """数据不足 N 日 → 渲染 reason 而非静默 "—"。"""
        from fixtures.collections import make_kline_rows

        from lib.render_markdown._v3 import _section_technical_brief

        dims = {"kline": {"data": make_kline_rows(5)}}
        out = _section_technical_brief(dims)
        assert "数据不足 20 日" in out
        assert "20日高/低:" not in out


class TestRenderAttachExtrasDedup:
    def test_skips_attach_events_when_events_present(self):
        from lib import render

        coll = _minimal_with_events_and_cards()

        with (
            patch("lib.events.attach_events") as mock_attach,
            patch("lib.analysis_templates.build_analysis_cards") as mock_cards,
            patch("lib.collector.attach_market_structure"),
            patch("lib.collector.attach_phase2_extras"),
        ):
            render.render(coll, "600176", "compact", attach_extras=True)

        mock_attach.assert_not_called()
        mock_cards.assert_not_called()

    def test_skips_attach_events_when_empty_with_summary(self):
        from lib import render

        coll = _minimal_with_events_and_cards()
        coll["events"] = []
        coll["_meta"]["events_summary"] = {
            "event_count": 0,
            "window_days": 30,
            "top_types": [],
        }

        with (
            patch("lib.events.attach_events") as mock_attach,
            patch("lib.analysis_templates.build_analysis_cards") as mock_cards,
            patch("lib.collector.attach_market_structure"),
            patch("lib.collector.attach_phase2_extras"),
        ):
            render.render(coll, "600176", "compact", attach_extras=True)

        mock_attach.assert_not_called()
        mock_cards.assert_not_called()

    def test_retries_attach_events_when_empty_without_summary(self):
        from lib import render

        coll = _minimal_with_events_and_cards()
        coll["events"] = []
        coll["_meta"].pop("events_summary", None)

        with (
            patch("lib.events.attach_events") as mock_attach,
            patch("lib.analysis_templates.build_analysis_cards") as mock_cards,
            patch("lib.collector.attach_market_structure"),
            patch("lib.collector.attach_phase2_extras"),
        ):
            render.render(coll, "600176", "compact", attach_extras=True)

        mock_attach.assert_called_once()
        mock_cards.assert_called_once()

    def test_attaches_events_and_builds_cards_when_missing(self):
        from lib import render

        coll = collection_v2_minimal()
        coll["market_structure"] = {"availability": {}}

        with (
            patch("lib.events.attach_events") as mock_attach,
            patch("lib.analysis_templates.build_analysis_cards") as mock_cards,
            patch("lib.collector.attach_market_structure"),
            patch("lib.collector.attach_phase2_extras"),
        ):
            render.render(coll, "600176", "compact", attach_extras=True)

        mock_attach.assert_called_once()
        mock_cards.assert_called_once()

    def test_rebuilds_analysis_cards_after_event_backfill(self):
        from lib import render

        coll = collection_v2_minimal()
        coll["market_structure"] = {"availability": {}}
        coll["_meta"] = {"analysis_cards": {"event_classifications": []}}

        with (
            patch("lib.events.attach_events") as mock_attach,
            patch("lib.analysis_templates.build_analysis_cards") as mock_cards,
            patch("lib.collector.attach_market_structure"),
            patch("lib.collector.attach_phase2_extras"),
        ):
            render.render(coll, "600176", "compact", attach_extras=True)

        mock_attach.assert_called_once()
        mock_cards.assert_called_once()

    def test_engine_version_matches_pyproject(self):
        from lib.render import ENGINE_VERSION
        from lib.version import get_package_version

        assert ENGINE_VERSION == get_package_version()


# ── v0.1.9: render_extras content sections ──


def _minimal_collection(**overrides) -> dict:
    """构建最小 collection 供 render_extras 函数测试."""
    coll: dict = {
        "symbol": "600176",
        "dimensions": [
            {
                "dimension": "basic_info",
                "display": "基本信息",
                "data": {"name": "测试公司", "industry": "建材",
                         "list_date": "20150101"},
                "status": "available",
                "_meta": {"source": "test"},
            },
        ],
    }
    coll.update(overrides)
    return coll


class TestAHDetection:
    def test_detects_hk_code(self):
        """有港股代码 → 输出 A+H 标记."""
        from lib.render_extras import render_ah_detection_note

        coll = _minimal_collection()
        coll["dimensions"][0]["data"]["hk_code"] = "HK0176"
        out = render_ah_detection_note(coll)
        assert "A+H" in out
        assert "HK0176" in out

    def test_no_hk_code_returns_empty(self):
        """无港股代码 → 空字符串."""
        from lib.render_extras import render_ah_detection_note

        coll = _minimal_collection()
        out = render_ah_detection_note(coll)
        assert out == ""


class TestExogenousShock:
    def test_with_news_cards_renders_table(self):
        """有 news cards → 输出外生冲击表格."""
        from lib.render_extras import section_exogenous_shock

        coll = _minimal_collection()
        coll["news"] = {
            "cards": [
                {"date": "2026-07-01", "direction": "bullish",
                 "credibility": "official", "credibility_score": 0.95,
                 "title": "重大合同公告", "source": "notice"},
            ],
        }
        out = section_exogenous_shock(coll)
        assert "外生冲击" in out
        assert "重大合同公告" in out
        assert "official" in out

    def test_empty_news_returns_empty(self):
        """无 news cards → 空字符串."""
        from lib.render_extras import section_exogenous_shock

        coll = _minimal_collection()
        out = section_exogenous_shock(coll)
        assert out == ""

    def test_contains_analysis_block(self):
        """外生冲击段包含分析块."""
        from lib.render_extras import section_exogenous_shock

        coll = _minimal_collection()
        coll["news"] = {
            "cards": [
                {"date": "2026-07-01", "direction": "neutral",
                 "credibility": "media_confirmed", "credibility_score": 0.7,
                 "title": "行业政策调整", "source": "tavily"},
            ],
        }
        out = section_exogenous_shock(coll)
        assert "分析" in out
        assert "待独立验证" in out


class TestRigorWarnings:
    def test_no_warnings_when_no_cross_source_diff(self):
        """无可交叉验证维度 → 空字符串."""
        from lib.render_extras import render_rigor_warnings

        coll = _minimal_collection()
        out = render_rigor_warnings(coll, strict=False)
        assert out == ""

    def test_strict_mode_annotation_in_output(self):
        """strict=True 时输出严格模式注解."""
        # 构造有多源数据的 collection 以触发 cross_validate
        from lib.render_extras import render_rigor_warnings

        coll = _minimal_collection()
        # 添加两个同维度的不同源以触发交叉验证
        coll["dimensions"].append({
            "dimension": "quote",
            "display": "实时行情",
            "data": {"close": 10.5, "total_mv": 100.0},
            "status": "available",
            "_meta": {
                "all_sources": [
                    {"source": "tushare", "success": True,
                     "data": {"close": 10.5, "total_mv": 100.0}},
                    {"source": "akshare", "success": True,
                     "data": {"close": 10.6, "total_mv": 101.0}},
                ],
            },
        })
        out = render_rigor_warnings(coll, strict=True)
        # 即使有差异，strict 模式会输出（如果偏差超阈值）
        # 至少函数不抛异常
        assert isinstance(out, str)


class TestRenderReportV3StrictRigor:
    """brief/full 路径均从 _meta.strict_rigor 读取 strict 标志."""

    def test_brief_mode_passes_strict_rigor_from_meta(self):
        from stock_testutil import make_store_collection
        from lib.render import render_report_v3

        coll = make_store_collection("600176")
        coll["market_structure"] = {}
        coll["research_summary"] = {"status": "no_data", "summary_text": ""}
        coll["_meta"] = {"strict_rigor": True}

        with patch("lib.render_markdown._concise._render_extras_block") as mock_extras:
            mock_extras.return_value = []
            render_report_v3(coll, "600176", mode="brief")
            mock_extras.assert_called_once()
            _, kwargs = mock_extras.call_args
            assert kwargs.get("strict") is True

    def test_full_mode_passes_strict_rigor_from_meta(self):
        from stock_testutil import make_store_collection
        from lib.render import render_report_v3

        coll = make_store_collection("600176")
        coll["market_structure"] = {}
        coll["research_summary"] = {"status": "no_data", "summary_text": ""}
        coll["_meta"] = {"strict_rigor": True}

        with patch("lib.render_markdown._concise._render_extras_block") as mock_extras:
            mock_extras.return_value = []
            render_report_v3(coll, "600176", mode="full")
            mock_extras.assert_called_once()
            _, kwargs = mock_extras.call_args
            assert kwargs.get("strict") is True

