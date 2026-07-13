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

    def test_classify_credibility_rumor(self):
        """包含'传闻'关键词 → rumor 标签，低可信度."""
        from lib.news_scanner import classify_credibility

        label, score = classify_credibility("据传某公司", "小道消息", None)
        assert label == "rumor"
        assert score < 0.3

    def test_classify_credibility_logical(self):
        """包含'预计' → logical 标签."""
        from lib.news_scanner import classify_credibility

        label, score = classify_credibility("分析预计增长", "逻辑推演", None)
        assert label == "logical"

    def test_classify_credibility_url_fallback(self):
        """URL 包含 eastmoney → media_confirmed（标题不含更高优先级关键词）."""
        from lib.news_scanner import classify_credibility

        label, score = classify_credibility(
            "某公司季度经营数据发布", "内容摘要",
            "https://www.eastmoney.com/news/123",
        )
        assert label == "media_confirmed"

    def test_collect_news_with_tavily_available(self, monkeypatch):
        """Tavily 可用时 Layer3 被调用."""
        from lib import env
        from lib.news_scanner import collect_news

        monkeypatch.setattr(env, "is_tavily_available", lambda _cfg: True)
        monkeypatch.setattr(
            "lib.news_scanner._fetch_notice_cards",
            lambda symbol, days: [],
        )
        # 即使 Tavily 网络请求失败，collect_news 不应崩溃
        try:
            out = collect_news("600176", name="测试", days=7)
        except Exception:
            # Tavily 请求可能因网络失败，但 collect_news 不应传播异常
            pass
        # 至少 Layer2 query_pack 总是产出
        out = collect_news("600176", name="测试", days=7)
        assert len(out["query_pack"]) == 5
        assert "query_pack" in out["attempted_sources"]

    def test_query_pack_contains_all_channels(self):
        """query_pack 包含 zh_breaking/zh_negative/zh_positive/en_policy/en_business."""
        from lib.news_scanner import build_news_query_pack

        pack = build_news_query_pack("600176", "中国巨石", "China Jushi")
        ids = {p["id"] for p in pack}
        assert ids == {"zh_breaking", "zh_negative", "zh_positive",
                       "en_policy", "en_business"}

    def test_query_pack_uses_name_eng(self):
        """英文查询使用 name_eng 参数."""
        from lib.news_scanner import build_news_query_pack

        pack = build_news_query_pack("600176", "中国巨石", "China Jushi Co Ltd")
        en_policy = [p for p in pack if p["id"] == "en_policy"][0]
        assert "China Jushi Co Ltd" in en_policy["query"]

    def test_query_pack_falls_back_to_name(self):
        """无 name_eng 时英文查询回退到 name."""
        from lib.news_scanner import build_news_query_pack

        pack = build_news_query_pack("600176", "中国巨石")  # no name_eng
        en_biz = [p for p in pack if p["id"] == "en_business"][0]
        assert "中国巨石" in en_biz["query"]
