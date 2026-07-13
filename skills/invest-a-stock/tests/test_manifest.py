"""Tests for lib/manifest.py — multi-source fingerprinting."""

from __future__ import annotations

from lib.manifest import _extract_date_range, compare_manifests, generate_manifest


def _multi_source_collection() -> dict:
    return {
        "symbol": "600176",
        "name": "中国巨石",
        "dimensions": [
            {
                "dimension": "kline",
                "display": "K线",
                "data": [{"trade_date": "20260601", "close": 10.0}],
                "status": "available",
                "_meta": {
                    "source": "tushare.daily_basic",
                    "success": True,
                    "all_sources": [
                        {
                            "source": "tushare.daily_basic",
                            "success": True,
                            "data_available": True,
                            "data": [{"trade_date": "20260601", "close": 10.0}],
                        },
                        {
                            "source": "tickflow.kline",
                            "success": True,
                            "data_available": True,
                            "data": [{"trade_date": "20260601", "close": 10.1}],
                        },
                    ],
                },
            },
        ],
    }


class TestGenerateManifest:
    def test_records_all_parallel_sources(self):
        manifest = generate_manifest(_multi_source_collection())
        sources = manifest.get("sources", {})
        assert "tushare.daily_basic" in sources
        assert "tickflow.kline" in sources
        assert sources["tickflow.kline"]["status"] == "success"

    def test_legacy_primary_only_fallback(self):
        collection = {
            "symbol": "600176",
            "dimensions": [
                {
                    "dimension": "quote",
                    "data": {"close": 10.0},
                    "status": "available",
                    "_meta": {"source": "tencent.quote", "success": True},
                },
            ],
        }
        manifest = generate_manifest(collection)
        assert "tencent.quote" in manifest["sources"]
        assert "tickflow.kline" not in manifest["sources"]


class TestCompareManifests:
    def test_detects_secondary_source_added(self):
        old_manifest = generate_manifest({
            "symbol": "600176",
            "dimensions": [
                {
                    "dimension": "kline",
                    "data": [{"trade_date": "20260601", "close": 10.0}],
                    "status": "available",
                    "_meta": {
                        "source": "tushare.daily_basic",
                        "success": True,
                        "all_sources": [
                            {
                                "source": "tushare.daily_basic",
                                "success": True,
                                "data_available": True,
                                "data": [{"trade_date": "20260601", "close": 10.0}],
                            },
                        ],
                    },
                },
            ],
        })
        new_manifest = generate_manifest(_multi_source_collection())
        diff = compare_manifests(old_manifest, new_manifest)
        assert "tickflow.kline" in diff["sources_added"]


class TestExtractDateRange:
    def test_falls_back_to_next_candidate_when_first_unparseable(self):
        data = [
            {
                "trade_date": "2026年06月",
                "end_date": "2026-06-15",
            },
        ]
        assert _extract_date_range(data) == {
            "start": "2026-06-15",
            "end": "2026-06-15",
        }

    def test_uses_first_parseable_candidate(self):
        data = [
            {"trade_date": "20260601", "end_date": "2026-06-30"},
        ]
        assert _extract_date_range(data) == {
            "start": "2026-06-01",
            "end": "2026-06-01",
        }
