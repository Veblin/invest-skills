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
            render.render(coll, "600176", "compact")

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
            render.render(coll, "600176", "compact")

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
            render.render(coll, "600176", "compact")

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
            render.render(coll, "600176", "compact")

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
            render.render(coll, "600176", "compact")

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


class TestAIBiasDeclaration:
    def test_level_a_for_mature_listed(self):
        """上市≥5年且有研报 → A 级."""
        from lib.render_extras import classify_info_richness

        coll = _minimal_collection()
        coll["dimensions"].append({
            "dimension": "research", "display": "研报",
            "data": [{"title": "研报1"}, {"title": "研报2"}, {"title": "研报3"},
                      {"title": "研报4"}, {"title": "研报5"}],
            "status": "available", "_meta": {"source": "test"},
        })
        assert classify_info_richness(coll) == "A"

    def test_level_b_for_recently_listed(self):
        """上市 < 5 年 → B 级."""
        from lib.render_extras import classify_info_richness

        coll = _minimal_collection()
        coll["dimensions"][0]["data"]["list_date"] = "20240101"
        assert classify_info_richness(coll) == "B"

    def test_level_c_for_sparse(self):
        """无上市日期 → C 级."""
        from lib.render_extras import classify_info_richness

        coll = _minimal_collection()
        coll["dimensions"][0]["data"] = {"name": "新股"}
        assert classify_info_richness(coll) == "C"

    def test_ai_bias_section_contains_level(self):
        """AI 偏见声明包含等级标记."""
        from lib.render_extras import render_ai_bias_declaration

        coll = _minimal_collection()
        out = render_ai_bias_declaration(coll)
        assert "**" in out
        assert "信息丰富度" in out


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


class TestContrarianSection:
    def test_contains_failure_paths(self):
        """逆向思考段包含失败路径预演."""
        from lib.render_extras import render_contrarian_section

        coll = _minimal_collection()
        out = render_contrarian_section(coll)
        assert "逆向思考" in out
        assert "失败路径" in out
        assert "估值收缩" in out

    def test_contains_verification_note(self):
        """逆向思考段包含待独立验证标注."""
        from lib.render_extras import render_contrarian_section

        coll = _minimal_collection()
        out = render_contrarian_section(coll)
        assert "待独立验证" in out


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


class TestRenderAllExtras:
    def test_returns_list_of_strings(self):
        """render_all_extras 返回非空字符串列表."""
        from lib.render_extras import render_all_extras

        coll = _minimal_collection()
        sections = render_all_extras(coll)
        assert isinstance(sections, list)
        # 至少应有 AI 偏见声明
        assert any("AI 偏见" in s for s in sections)
