"""Tests for deterministic rerank credibility scoring (R-09)."""
from lib.rerank import (
    PAID_SOURCE,
    score_evidence,
    score_from_dimension_meta,
    score_all_dimensions,
    to_credibility_label,
)


class TestScoreEvidence:
    def test_tushare_akshare_converge(self):
        """Tushare + akshare 一致 → 75-85 分"""
        score = score_evidence(
            multi_source=True,
            cross_validation_status="convergence",
            primary_source="tushare.daily_basic",
            source_count=2,
            data_age_days=10,
        )
        # BASE(50) + CROSS_SOURCE_MATCH(15) + MULTI_SOURCE_BONUS(5) + PAID_SOURCE(10) + RECENT_DATA(5) = 85
        assert 75 <= score <= 90

    def test_tushare_akshare_diverge(self):
        """Tushare + akshare 差异 → 55-65 分"""
        score = score_evidence(
            multi_source=True,
            cross_validation_status="divergence",
            primary_source="tushare.daily_basic",
            source_count=2,
            data_age_days=10,
        )
        # BASE(50) + DATA_CONFLICT(-10) + MULTI_SOURCE_BONUS(5) + PAID_SOURCE(10) + RECENT_DATA(5) = 60
        assert 55 <= score <= 70

    def test_single_websearch(self):
        """单源 WebSearch → 35-45 分"""
        score = score_evidence(
            multi_source=False,
            cross_validation_status=None,
            primary_source="websearch",
            source_count=1,
        )
        # BASE(50) + SINGLE_SOURCE(-10) + WEBSEARCH_SOURCE(-5) = 35
        assert 30 <= score <= 45

    def test_single_tushare_outdated(self):
        """单源 Tushare + 过期 2 年 → ~50 分"""
        score = score_evidence(
            multi_source=False,
            cross_validation_status=None,
            primary_source="tushare.daily_basic",
            source_count=1,
            data_age_days=730,  # 2 years
        )
        # BASE(50) + SINGLE_SOURCE(-10) + PAID_SOURCE(10) + OUTDATED_PER_YEAR * 1 = -5 → 45
        assert 40 <= score <= 55

    def test_clamped_to_0_100(self):
        """分数始终在 0-100 范围"""
        # Very bad scenario
        score = score_evidence(
            multi_source=False,
            primary_source="websearch",
            source_count=1,
            data_age_days=3650,  # 10 years
        )
        assert 0 <= score <= 100

    def test_perfect_scenario(self):
        """理想场景：高分"""
        score = score_evidence(
            multi_source=True,
            cross_validation_status="convergence",
            primary_source="tushare.daily_basic",
            source_count=3,
            data_age_days=1,
        )
        assert score >= 80


class TestScoreFromDimensionMeta:
    def test_tushare_akshare_converge_meta(self):
        meta = {
            "source": "tushare.daily_basic",
            "confidence": "high",
            "multi_source": True,
            "source_count": 2,
            "cross_validation": "convergence",
            "all_sources": [
                {"source": "tushare.daily_basic", "data_available": True},
                {"source": "tencent_finance", "data_available": True},
            ],
            "fetched_at": "2026-06-22T10:00:00+00:00",
        }
        score = score_from_dimension_meta(meta)
        assert score >= 75

    def test_single_websearch_meta(self):
        meta = {
            "source": "websearch",
            "confidence": "low",
            "multi_source": False,
            "source_count": 1,
            "cross_validation": None,
            "all_sources": [],
        }
        score = score_from_dimension_meta(meta)
        assert score <= 45

    def test_merged_quote_keeps_paid_source_bonus(self):
        """多源合并后 `source` 变 `merged:…`，付费源加分必须仍在。

        回归（2026-09-30 实测）：collector 把 quote 的 `_meta.source` 改写成
        `merged:tushare.daily+tencent_finance` 后，本函数的
        `primary_source.startswith("tushare")` 不再命中 → 付费源 +10 静默丢失
        → 附录「实时行情」可信度由 70 掉到 60（用户可见数值回归）。
        判据改读合并前 collector 另存的 `primary_source`；未合并且无该键时行为不变。
        """
        merged = {
            "source": "merged:tushare.daily+tencent_finance",
            "primary_source": "tushare.daily",
            "merged_sources": ["tushare.daily", "tencent_finance"],
            "confidence": "high",
            "multi_source": True,
            "source_count": 2,
            "cross_validation": "convergence",
            "all_sources": [
                {"source": "tushare.daily", "data_available": True},
                {"source": "tencent_finance", "data_available": True},
            ],
            "fetched_at": "2026-06-22T10:00:00+00:00",
        }
        without = {k: v for k, v in merged.items() if k != "primary_source"}
        # 去掉 `primary_source` 即复现旧写法下的丢分场景，差额恰为付费源加分
        assert score_from_dimension_meta(merged) - score_from_dimension_meta(without) \
            == PAID_SOURCE

    def test_unmerged_meta_ignores_absent_primary_source(self):
        """未合并维度没有 `primary_source` 键 → 判据回落到 `source`，行为不变。"""
        meta = {
            "source": "tushare.daily_basic",
            "confidence": "high",
            "multi_source": True,
            "source_count": 2,
            "cross_validation": "convergence",
            "all_sources": [
                {"source": "tushare.daily_basic", "data_available": True},
                {"source": "tencent_finance", "data_available": True},
            ],
            "fetched_at": "2026-06-22T10:00:00+00:00",
        }
        assert score_from_dimension_meta(meta) >= 75


class TestScoreAllDimensions:
    def test_multiple_dimensions(self):
        dims = [
            {
                "dimension": "valuation",
                "display": "估值分析",
                "data": {"pe_ttm": 15.5},
                "_meta": {
                    "source": "tushare.daily_basic",
                    "multi_source": True,
                    "source_count": 2,
                    "cross_validation": "convergence",
                    "all_sources": [
                        {"data_available": True},
                        {"data_available": True},
                    ],
                },
            },
            {
                "dimension": "research",
                "display": "机构研报",
                "data": None,
                "_meta": {
                    "source": "websearch",
                    "multi_source": False,
                    "source_count": 1,
                    "cross_validation": None,
                    "all_sources": [],
                },
            },
        ]
        scores = score_all_dimensions(dims)
        assert "估值分析" in scores
        assert "机构研报" in scores
        assert scores["估值分析"] > scores["机构研报"]

    def test_empty_dimensions(self):
        assert score_all_dimensions([]) == {}


class TestCredibilityLabel:
    def test_high(self):
        assert "高可信" in to_credibility_label(85)

    def test_medium(self):
        assert "中可信" in to_credibility_label(65)

    def test_low(self):
        assert "低可信" in to_credibility_label(45)

    def test_very_low(self):
        assert "极低可信" in to_credibility_label(25)

    def test_boundaries(self):
        assert "高可信" in to_credibility_label(80)
        assert "中可信" in to_credibility_label(60)
        assert "低可信" in to_credibility_label(40)
