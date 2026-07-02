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
