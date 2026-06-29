"""Tests for lib/analysis_templates.py — analysis card builders.

Run: cd /Users/veblin/Study/claude-bigA-financial/code && uv run python -m pytest skills/invest-A/tests/test_analysis_templates.py -v
"""

from __future__ import annotations

import pytest

from lib.analysis_templates import (
    build_analysis_cards,
    _build_mda_card,
    _build_event_classification_cards,
    _build_sentiment_card,
    _load_taxonomy,
    _card_to_dict,
    MDANarrativeCard,
    EventClassificationCard,
    SentimentCard,
    _DIRECTION_DISCLAIMER,
)


# ── Helper factories ──


def _make_financials_dimension(records: list[dict]) -> dict:
    """Create a financials dimension dict as it would appear in collection."""
    return {
        "dimension": "financials",
        "display": "财务报告",
        "data": records,
        "status": "available",
        "_meta": {"source": "tushare.fina_indicator", "success": True},
    }


def _make_research_dimension(summary_override: dict | None = None) -> dict:
    """Create a research dimension dict with realistic default data."""
    default_summary = {
        "latest_ratings": [
            {"org": "A证券", "rating": "买入", "report_date": "20260601"},
            {"org": "B证券", "rating": "增持", "report_date": "20260515"},
            {"org": "C证券", "rating": "买入", "report_date": "20260420"},
            {"org": "D证券", "rating": "中性", "report_date": "20260301"},
        ],
        "target_price_range": {
            "min": 42.0, "max": 50.0,
            "avg_upper": 48.0, "avg_lower": 43.0,
        },
        "eps_forecasts": [
            {"quarter": "2026Q1", "avg_eps": 1.25, "n_analysts": 3},
            {"quarter": "2026Q2", "avg_eps": 1.35, "n_analysts": 2},
        ],
        "source": "tushare.report_rc",
        "status": "ok",
        "summary_text": "近半年 4 条机构评级（3 条偏多），卖方预期价位 42.0–50.0 元",
    }
    return {
        "dimension": "research",
        "display": "机构研报",
        "data": [{"report_title": "深度研报：公司基本面分析..."}],
        "status": "available",
        "_meta": {"source": "tushare.report_rc", "success": True},
        "research_summary": summary_override if summary_override is not None else default_summary,
    }


# ══════════════════════════════════════════════════════════════════════
# Template A: MDANarrativeCard
# ══════════════════════════════════════════════════════════════════════


class TestBuildMDACard:
    """Template A — 财报MD&A叙事卡片."""

    @pytest.fixture
    def realistic_collection(self):
        records = [
            {
                "end_date": "20251231",
                "revenue": 15_800_000_000.0,
                "net_profit": 2_300_000_000.0,
                "grossprofit_margin": 28.5,
                "netprofit_margin": 14.56,
                "n_cashflow_act": 2_800_000_000.0,
                "roe": 15.82,
                "debt_to_assets": 48.5,
                "assets_turn": 0.82,
            },
            {
                "end_date": "20241231",
                "revenue": 12_500_000_000.0,
                "net_profit": 1_800_000_000.0,
                "grossprofit_margin": 25.3,
                "netprofit_margin": 14.40,
                "n_cashflow_act": 1_700_000_000.0,
                "roe": 14.10,
                "debt_to_assets": 50.2,
                "assets_turn": 0.78,
            },
        ]
        return {"dimensions": [_make_financials_dimension(records)]}

    def test_builds_card_with_realistic_data(self, realistic_collection):
        card = _build_mda_card(realistic_collection)
        assert card is not None

        # Revenue growth: (15.8 - 12.5) / 12.5 = 26.4%
        assert card.revenue_growth_yoy == pytest.approx(26.4, rel=0.1)
        # Profit growth: (2.3 - 1.8) / 1.8 ≅ 27.78%
        assert card.profit_growth_yoy == pytest.approx(27.78, rel=0.1)
        # Gross margin
        assert card.gross_margin == 28.5
        # Gross margin Δ = 28.5 - 25.3 = 3.2
        assert card.gross_margin_change == 3.2
        # Net margin
        assert card.net_margin == 14.56
        assert card.net_margin_change == pytest.approx(0.16, rel=0.01)
        # Cashflow quality: 2.8B > 1.1 * 2.3B → "良好"
        assert card.cashflow_quality_hint == "良好"
        # ROE
        assert card.roe == 15.82
        assert card.roe_change == pytest.approx(1.72, rel=0.01)
        # Debt ratio Δ = 48.5 - 50.2 = -1.7
        assert card.debt_ratio == 48.5
        assert card.debt_ratio_change == pytest.approx(-1.7, rel=0.01)
        # Turnover Δ = 0.82 - 0.78 = 0.04
        assert card.asset_turnover == 0.82
        assert card.asset_turnover_change == pytest.approx(0.04, rel=0.01)
        # Slot
        assert card.narrative_slot == "[待 Claude 填充管理层论述解读]"
        assert "T" in card.generated_at

    def test_missing_financials_returns_none(self):
        assert _build_mda_card({}) is None
        assert _build_mda_card({"dimensions": []}) is None

    def test_financials_null_data_returns_none(self):
        collection = {"dimensions": [
            {"dimension": "financials", "data": None, "status": "missing"},
        ]}
        assert _build_mda_card(collection) is None

    def test_financials_empty_list_returns_none(self):
        collection = {"dimensions": [
            {"dimension": "financials", "data": [], "status": "available"},
        ]}
        assert _build_mda_card(collection) is None

    def test_cashflow_quality_general(self):
        """OCF roughly equals net_profit → "一般"."""
        records = [
            {"end_date": "20251231", "net_profit": 100.0, "n_cashflow_act": 95.0, "revenue": 1000.0},
        ]
        collection = {"dimensions": [_make_financials_dimension(records)]}
        card = _build_mda_card(collection)
        assert card is not None
        assert card.cashflow_quality_hint == "一般"

    def test_cashflow_quality_concerning(self):
        """OCF significantly less than net_profit → "需关注"."""
        records = [
            {"end_date": "20251231", "net_profit": 100.0, "n_cashflow_act": 50.0, "revenue": 1000.0},
        ]
        collection = {"dimensions": [_make_financials_dimension(records)]}
        card = _build_mda_card(collection)
        assert card.cashflow_quality_hint == "需关注"

    def test_cashflow_quality_missing_returns_empty_string(self):
        records = [
            {"end_date": "20251231", "net_profit": 100.0, "revenue": 1000.0},
        ]
        collection = {"dimensions": [_make_financials_dimension(records)]}
        card = _build_mda_card(collection)
        assert card.cashflow_quality_hint == ""

    def test_cashflow_quality_zero_profit_returns_empty_string(self):
        records = [
            {"end_date": "20251231", "net_profit": 0.0, "n_cashflow_act": 50.0, "revenue": 1000.0},
        ]
        collection = {"dimensions": [_make_financials_dimension(records)]}
        card = _build_mda_card(collection)
        assert card is not None
        assert card.cashflow_quality_hint == ""

    def test_single_year_no_yoy_returns_none_for_growth_rates(self):
        records = [
            {"end_date": "20251231", "revenue": 1000.0, "net_profit": 100.0},
        ]
        collection = {"dimensions": [_make_financials_dimension(records)]}
        card = _build_mda_card(collection)
        assert card is not None
        assert card.revenue_growth_yoy is None
        assert card.profit_growth_yoy is None
        assert card.gross_margin_change is None
        assert card.roe_change is None
        assert card.debt_ratio_change is None
        assert card.asset_turnover_change is None

    def test_no_financials_dimension_returns_none(self):
        collection = {"dimensions": [
            {"dimension": "quote", "data": {"close": 10.0}},
        ]}
        assert _build_mda_card(collection) is None

    def test_quarterly_data_yoy_match(self):
        """Q2 2025 vs Q2 2024 YoY matching works with quarterly dates."""
        records = [
            {"end_date": "20250630", "revenue": 6000.0, "net_profit": 800.0},
            {"end_date": "20250331", "revenue": 5500.0, "net_profit": 700.0},
            {"end_date": "20240630", "revenue": 5000.0, "net_profit": 600.0},
        ]
        collection = {"dimensions": [_make_financials_dimension(records)]}
        card = _build_mda_card(collection)
        assert card is not None
        # YoY: (6000 - 5000) / 5000 = 20%
        assert card.revenue_growth_yoy == pytest.approx(20.0, rel=0.1)
        # YoY profit: (800 - 600) / 600 = 33.33%
        assert card.profit_growth_yoy == pytest.approx(33.33, rel=0.1)


# ══════════════════════════════════════════════════════════════════════
# Template B: EventClassificationCard
# ══════════════════════════════════════════════════════════════════════


class TestEventClassificationCards:
    """Template B — 公告事件分类卡片."""

    SAMPLE_EVENTS = [
        {"type": "buyback", "title": "关于回购公司股份的公告", "date": "20260601"},
        {"type": "dividend", "title": "2025年度利润分配方案", "date": "20260610"},
        {"type": "buyback", "title": "回购进展公告", "date": "20260615"},
        {"type": "holder_decrease", "title": "股东减持计划", "date": "20260620"},
        {"type": "litigation", "title": "诉讼事项公告", "date": "20260501"},
        {"type": "major_contract", "title": "重大合同公告", "date": "20260625"},
        {"type": "other", "title": "其他公告", "date": "20260515"},
        {"type": "holder_increase", "title": "增持公告", "date": "20260628"},
        {"type": "st_risk", "title": "退市风险提示", "date": "20260520"},
    ]

    def test_groups_events_by_type(self):
        cards = _build_event_classification_cards({"events": self.SAMPLE_EVENTS})
        type_set = {c.event_type for c in cards}
        assert type_set == {
            "buyback", "dividend", "holder_decrease", "holder_increase",
            "litigation", "major_contract", "st_risk", "other",
        }

    def test_taxonomy_metadata_applied(self):
        cards = _build_event_classification_cards({"events": self.SAMPLE_EVENTS})
        by_type = {c.event_type: c for c in cards}
        card = by_type["buyback"]
        assert card.event_label == "回购"
        assert card.impact_dimension == "估值"
        assert card.default_duration_hint == "中长期变量"

    def test_high_confidence_direction_buyback(self):
        cards = _build_event_classification_cards({"events": self.SAMPLE_EVENTS})
        card = next(c for c in cards if c.event_type == "buyback")
        assert card.direction_hint == "正向"
        assert card.direction_confidence == "medium"
        assert _DIRECTION_DISCLAIMER in card.direction_note

    def test_high_confidence_direction_holder_decrease(self):
        cards = _build_event_classification_cards({"events": self.SAMPLE_EVENTS})
        card = next(c for c in cards if c.event_type == "holder_decrease")
        assert card.direction_hint == "负向"
        assert card.direction_confidence == "medium"

    def test_high_confidence_direction_holder_increase(self):
        cards = _build_event_classification_cards({"events": self.SAMPLE_EVENTS})
        card = next(c for c in cards if c.event_type == "holder_increase")
        assert card.direction_hint == "正向"

    def test_high_confidence_direction_dividend(self):
        cards = _build_event_classification_cards({"events": self.SAMPLE_EVENTS})
        card = next(c for c in cards if c.event_type == "dividend")
        assert card.direction_hint == "正向"

    def test_high_confidence_direction_st_risk(self):
        cards = _build_event_classification_cards({"events": self.SAMPLE_EVENTS})
        card = next(c for c in cards if c.event_type == "st_risk")
        assert card.direction_hint == "负向"

    def test_high_confidence_direction_litigation(self):
        cards = _build_event_classification_cards({"events": self.SAMPLE_EVENTS})
        card = next(c for c in cards if c.event_type == "litigation")
        assert card.direction_hint == "负向"

    def test_low_confidence_for_unlisted_types(self):
        cards = _build_event_classification_cards({"events": self.SAMPLE_EVENTS})
        card = next(c for c in cards if c.event_type == "major_contract")
        assert card.direction_hint == ""
        assert card.direction_confidence == "low"
        assert card.direction_note == ""

    def test_other_type_defaults(self):
        cards = _build_event_classification_cards({"events": self.SAMPLE_EVENTS})
        card = next(c for c in cards if c.event_type == "other")
        assert card.event_label == "其他公告"
        assert card.direction_hint == ""
        assert card.direction_confidence == "low"

    def test_empty_events_returns_empty_list(self):
        assert _build_event_classification_cards({"events": []}) == []
        assert _build_event_classification_cards({}) == []
        assert _build_event_classification_cards({"events": None}) == []

    def test_sentiment_slot_present(self):
        cards = _build_event_classification_cards({"events": self.SAMPLE_EVENTS})
        for card in cards:
            assert card.sentiment_impact_slot == "[待 Claude 填充语气信号]"

    def test_generated_at_timestamp(self):
        cards = _build_event_classification_cards({"events": self.SAMPLE_EVENTS})
        for card in cards:
            assert "T" in card.generated_at

    def test_sorting_priority(self):
        """Cards with direction_hint come first, then by descending event count."""
        # Only include types with different counts
        events = [
            {"type": "other", "title": "其他", "date": "20260601"},
            {"type": "other", "title": "其他2", "date": "20260602"},
            {"type": "buyback", "title": "回购", "date": "20260601"},
        ]
        cards = _build_event_classification_cards({"events": events})
        # buyback (has hint) should come before other (no hint)
        assert cards[0].event_type == "buyback"
        assert cards[1].event_type == "other"


# ══════════════════════════════════════════════════════════════════════
# Template C: SentimentCard
# ══════════════════════════════════════════════════════════════════════


class TestSentimentCard:
    """Template C — 业绩会/研报情绪卡片."""

    @pytest.fixture
    def research_collection(self):
        return {"dimensions": [_make_research_dimension()]}

    def test_builds_card_with_research_data(self, research_collection):
        card = _build_sentiment_card(research_collection)
        assert card is not None

        assert card.research_count == 4
        # A证券(买入) + C证券(买入) = 2, B证券(增持) = 1, D证券(中性) = 1
        assert card.rating_distribution == {"买入": 2, "增持": 1, "中性": 1, "减持": 0}
        assert card.eps_forecast_mean == 1.25
        assert card.eps_forecast_count == 3
        assert card.eps_forecast_high is None
        assert card.eps_forecast_low is None
        assert "近半年" in card.latest_summary
        assert "Tushare report_rc" in card.data_source_note

    def test_sentiment_slot(self, research_collection):
        card = _build_sentiment_card(research_collection)
        assert card.sentiment_slot == "[待 Claude 标注语气信号]"

    def test_missing_research_dimension_returns_none(self):
        assert _build_sentiment_card({}) is None
        assert _build_sentiment_card({"dimensions": []}) is None

    def test_research_without_summary_returns_none(self):
        dim = _make_research_dimension(None)
        dim["research_summary"] = None
        assert _build_sentiment_card({"dimensions": [dim]}) is None

    def test_research_forecast_source(self):
        summary = {
            "latest_ratings": [],
            "source": "tushare.forecast",
            "status": "ok_guidance_only",
            "summary_text": "公司业绩预告：净利润 5.0–6.0 亿元（同比 10%–20%）",
        }
        card = _build_sentiment_card({"dimensions": [_make_research_dimension(summary)]})
        assert card is not None
        assert card.research_count == 0
        assert card.eps_forecast_mean is None
        assert "公司业绩预告" in card.latest_summary
        assert "Tushare forecast" in card.data_source_note

    def test_akshare_research_source(self):
        summary = {
            "latest_ratings": [],
            "source": "akshare.research",
            "status": "ok_limited",
            "summary_text": "东方财富 10 条研报记录（无结构化评级摘要）",
        }
        card = _build_sentiment_card({"dimensions": [_make_research_dimension(summary)]})
        assert card is not None
        assert "akshare" in card.data_source_note

    def test_empty_ratings_handled(self):
        summary = {
            "latest_ratings": [],
            "source": "tushare.report_rc",
            "status": "ok",
            "summary_text": "",
        }
        card = _build_sentiment_card({"dimensions": [_make_research_dimension(summary)]})
        assert card is not None
        assert card.research_count == 0
        assert card.rating_distribution == {"买入": 0, "增持": 0, "中性": 0, "减持": 0}


# ══════════════════════════════════════════════════════════════════════
# Taxonomy loader
# ══════════════════════════════════════════════════════════════════════


class TestLoadTaxonomy:
    """YAML taxonomy loading with caching."""

    def test_loads_valid_taxonomy(self):
        tax = _load_taxonomy()
        assert isinstance(tax, dict)
        assert tax.get("schema_version") == "0.1"
        assert "event_types" in tax
        # Expect 14+ event types
        assert len(tax["event_types"]) >= 14
        assert tax["event_types"]["buyback"]["label"] == "回购"
        assert tax["event_types"]["dividend"]["label"] == "分红"
        assert tax["event_types"]["other"]["label"] == "其他公告"

    def test_taxonomy_event_type_keys(self):
        tax = _load_taxonomy()
        et = tax["event_types"]
        # Every entry has required fields
        for etype, meta in et.items():
            assert "label" in meta, f"{etype} missing label"
            assert "impact_dimension" in meta, f"{etype} missing impact_dimension"
            assert "default_duration_hint" in meta, f"{etype} missing default_duration_hint"
            assert "description" in meta, f"{etype} missing description"

    def test_caching(self):
        tax1 = _load_taxonomy()
        tax2 = _load_taxonomy()
        assert tax1 is tax2  # Same object from module cache


# ══════════════════════════════════════════════════════════════════════
# Main entry point: build_analysis_cards
# ══════════════════════════════════════════════════════════════════════


class TestBuildAnalysisCards:
    """Main entry point idempotency and integration."""

    def test_idempotent_second_call_skips(self):
        collection = {
            "dimensions": [
                _make_financials_dimension([
                    {"end_date": "20251231", "revenue": 1000.0, "net_profit": 100.0},
                ]),
            ],
            "events": [{"type": "buyback", "title": "回购", "date": "20260601"}],
        }
        build_analysis_cards(collection)
        cards_ref = collection["_meta"]["analysis_cards"]

        build_analysis_cards(collection)
        assert collection["_meta"]["analysis_cards"] is cards_ref

    def test_builds_all_card_types(self):
        collection = {
            "dimensions": [
                _make_financials_dimension([
                    {"end_date": "20251231", "revenue": 1000.0, "net_profit": 100.0},
                ]),
                _make_research_dimension(),
            ],
            "events": [{"type": "buyback", "title": "回购", "date": "20260601"}],
        }
        build_analysis_cards(collection)
        cards = collection["_meta"]["analysis_cards"]

        assert cards["mda_narrative"] is not None
        assert len(cards["event_classifications"]) >= 1
        assert cards["sentiment"] is not None

    def test_empty_collection_graceful(self):
        collection: dict = {"_meta": {}}
        build_analysis_cards(collection)
        cards = collection["_meta"]["analysis_cards"]

        assert cards["mda_narrative"] is None
        assert cards["event_classifications"] == []
        assert cards["sentiment"] is None

    def test_only_mda_and_no_events_sentiment(self):
        """When only financials exist, only MDA card is non-None."""
        collection = {
            "dimensions": [
                _make_financials_dimension([
                    {"end_date": "20251231", "revenue": 1000.0, "net_profit": 100.0},
                ]),
            ],
        }
        build_analysis_cards(collection)
        cards = collection["_meta"]["analysis_cards"]
        assert cards["mda_narrative"] is not None
        assert cards["event_classifications"] == []
        assert cards["sentiment"] is None


# ══════════════════════════════════════════════════════════════════════
# Serialization: _card_to_dict
# ══════════════════════════════════════════════════════════════════════


class TestCardToDict:
    """_card_to_dict serialisation helper."""

    def test_dataclass_to_dict(self):
        card = MDANarrativeCard(
            revenue_growth_yoy=26.4, profit_growth_yoy=27.78,
            gross_margin=28.5, gross_margin_change=3.2,
            net_margin=14.56, net_margin_change=0.16,
            operating_cashflow=2.8e9, net_profit=2.3e9,
            cashflow_quality_hint="良好",
            roe=15.82, roe_change=1.72,
            debt_ratio=48.5, debt_ratio_change=-1.7,
            asset_turnover=0.82, asset_turnover_change=0.04,
            narrative_slot="[待 Claude 填充管理层论述解读]",
            generated_at="2026-06-29T00:00:00",
        )
        d = _card_to_dict(card)
        assert isinstance(d, dict)
        assert d["revenue_growth_yoy"] == 26.4
        assert d["cashflow_quality_hint"] == "良好"
        assert d["narrative_slot"] == "[待 Claude 填充管理层论述解读]"
        assert d["net_margin"] == 14.56

    def test_none_returns_none(self):
        assert _card_to_dict(None) is None

    def test_dict_passthrough(self):
        d = {"foo": 1}
        assert _card_to_dict(d) is d

    def test_event_card_round_trip(self):
        card = EventClassificationCard(
            event_type="buyback",
            event_label="回购",
            events=[{"title": "回购公告", "date": "20260601"}],
            impact_dimension="估值",
            default_duration_hint="中长期变量",
            direction_hint="正向",
            direction_confidence="medium",
            direction_note=_DIRECTION_DISCLAIMER,
            sentiment_impact_slot="[待 Claude 填充语气信号]",
            generated_at="2026-06-29T00:00:00",
        )
        d = _card_to_dict(card)
        assert isinstance(d, dict)
        assert d["event_type"] == "buyback"
        assert d["direction_hint"] == "正向"
        assert len(d["events"]) == 1

    def test_sentiment_card_round_trip(self):
        card = SentimentCard(
            research_count=4,
            rating_distribution={"买入": 2, "增持": 1, "中性": 1, "减持": 0},
            eps_forecast_mean=1.25,
            eps_forecast_high=None,
            eps_forecast_low=None,
            eps_forecast_count=3,
            latest_summary="近半年 4 条机构评级",
            sentiment_slot="[待 Claude 标注语气信号]",
            data_source_note="数据源: Tushare report_rc",
            generated_at="2026-06-29T00:00:00",
        )
        d = _card_to_dict(card)
        assert d["research_count"] == 4
        assert d["eps_forecast_mean"] == 1.25
        assert d["eps_forecast_high"] is None
