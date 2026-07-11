"""Tests for lib.news_scanner (v0.1.9)."""

from __future__ import annotations

from datetime import datetime, timedelta


class TestNewsScanner:
    def test_query_pack_at_least_three(self):
        from lib.news_scanner import build_news_query_pack

        pack = build_news_query_pack("600176", "中国巨石")
        assert len(pack) >= 3
        assert all(p.get("query") for p in pack)

    def test_classify_credibility_official(self):
        from lib.news_scanner import classify_credibility

        label, score = classify_credibility("上交所公告", "重大事项", "http://www.sse.com.cn")
        assert label == "official"
        assert score >= 0.9

    def test_collect_news_no_key_skips_tavily(self, monkeypatch):
        from lib import env
        from lib.news_scanner import collect_news

        monkeypatch.setattr(env, "is_tavily_available", lambda _cfg: False)
        monkeypatch.setattr(
            "lib.news_scanner._fetch_notice_cards",
            lambda symbol, days: [],
        )
        out = collect_news("600176", name="测试", days=7)
        assert len(out["query_pack"]) >= 3
        assert out["attempted_sources"]["tavily"] == "skipped (no key)"
        assert out["attempted_sources"]["channel_b"] == "deferred v0.2.0"

    def test_channel_placeholders_not_implemented(self):
        from lib.news_scanner import collect_community_heat, collect_targeted_sites
        import pytest

        with pytest.raises(NotImplementedError):
            collect_targeted_sites("Test Co")
        with pytest.raises(NotImplementedError):
            collect_community_heat("600176")

    def test_notice_date_normalize_filters(self, monkeypatch):
        from lib.news_scanner import _fetch_notice_cards
        import lib.events as events_mod

        today = datetime.now()
        old = (today - timedelta(days=30)).strftime("%Y%m%d")
        recent = (today - timedelta(days=2)).strftime("%Y%m%d")

        monkeypatch.setattr(
            events_mod,
            "_fetch_notice_events",
            lambda symbol: [
                {"date": old, "title": "旧公告", "url": ""},
                {"date": recent, "title": "新公告", "url": ""},
            ],
        )
        cards = _fetch_notice_cards("600176", days=7)
        assert len(cards) == 1
        assert "新公告" in cards[0].title
        assert "-" in cards[0].date
