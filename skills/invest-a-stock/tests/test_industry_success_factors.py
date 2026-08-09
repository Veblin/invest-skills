"""R4 行业成功关键因素 — 结构校验 / 路由 / 渲染集成测试。

覆盖验收点：
- 结构测试：SUCCESS_FACTORS 每项含 question/data_fields/sources/answer_template 四字段
- 未覆盖行业 → 空表 + 渲染「无行业成功因素定义」标注
- 渲染：已采字段输出数值，引擎外字段输出「需 AI 补查」
"""

from __future__ import annotations

from fixtures.collections import collection_v2_minimal

from lib.industry.base import (
    IndustryProfile,
    get_success_factors,
    validate_success_factors,
)


class TestSuccessFactorsStructure:
    def test_both_profiles_have_three_factors_with_full_fields(self):
        """① 银行/科技硬件各 3 项、四项字段齐全。"""
        from lib.industry import banks, tech_hardware

        for profile in (banks.bank_profile, tech_hardware.tech_hardware_profile):
            assert len(profile.success_factors) == 3
            for factor in profile.success_factors:
                assert set(factor) >= {"question", "data_fields", "sources", "answer_template"}
                assert isinstance(factor["data_fields"], list)
        assert validate_success_factors(banks.bank_profile) == []
        assert validate_success_factors(tech_hardware.tech_hardware_profile) == []

    def test_validate_reports_missing_fields(self):
        """② 校验对缺字段项报错。"""
        profile = IndustryProfile(
            sector_group="test",
            sw_name="测试行业",
            success_factors=[
                {"question": "缺三个字段", "data_fields": []},
                {"question": "q", "data_fields": [], "sources": [], "answer_template": "t"},
            ],
        )
        errors = validate_success_factors(profile)
        assert len(errors) == 1
        assert "sources" in errors[0]
        assert "answer_template" in errors[0]

    def test_non_dict_factor_reported(self):
        profile = IndustryProfile(sector_group="test", success_factors=[42])
        errors = validate_success_factors(profile)
        assert len(errors) == 1
        assert "非 dict" in errors[0]


class TestSuccessFactorsRouting:
    def test_keyword_routing_banks(self):
        """④ get_success_factors 关键词路由正确（银行/股份制银行 → 净息差）。"""
        factors = get_success_factors("股份制银行")
        assert len(factors) == 3
        assert "净息差" in factors[0]["question"]
        assert factors[0]["data_fields"] == ["net_interest_margin", "roa"]

    def test_keyword_routing_tech_hardware(self):
        factors = get_success_factors("半导体")
        assert len(factors) == 3
        assert "产能周期" in factors[0]["question"]
        # 客户结构为引擎外字段 → data_fields 空
        assert factors[2]["data_fields"] == []

    def test_unregistered_industry_returns_empty(self):
        """③ 未注册行业（如白酒）→ 空表。"""
        assert get_success_factors("白酒") == []
        assert get_success_factors("") == []


class TestRenderSuccessFactors:
    def _bank_collection(self) -> dict:
        coll = collection_v2_minimal()
        for dim in coll["dimensions"]:
            if dim["dimension"] == "basic_info":
                dim["data"]["industry"] = "银行"
            elif dim["dimension"] == "financials":
                dim["data"] = [
                    {"end_date": "20251231", "net_interest_margin": 1.82,
                     "npl_ratio": 1.25, "provision_coverage_ratio": 220.5,
                     "cet1_capital_adequacy_ratio": 11.3},
                    {"end_date": "20261231", "net_interest_margin": 1.75,
                     "npl_ratio": 1.20, "provision_coverage_ratio": 225.0,
                     "cet1_capital_adequacy_ratio": 11.5},
                ]
        coll["success_factors"] = {
            "industry": "银行",
            "covered": True,
            "factors": get_success_factors("银行"),
        }
        return coll

    def test_renders_questions_with_data_values(self):
        """⑤ 构造 collection（银行行业 + 已采字段）→ 输出含 3 项问题且每项有数据值。"""
        from lib.render_markdown._base import _render_success_factors

        lines = _render_success_factors(self._bank_collection())
        joined = "\n".join(lines)
        assert "**[行业成功关键因素（R4）]** 银行" in joined
        for factor in get_success_factors("银行"):
            assert factor["question"][:8] in joined
        # 最新期（20261231）字段值被引用
        assert "net_interest_margin: 1.75" in joined
        assert "npl_ratio: 1.20" in joined
        assert "cet1_capital_adequacy_ratio: 11.50" in joined
        # 来源标注存在
        assert "[来源:" in joined

    def test_engine_external_field_flagged_needs_ai(self):
        """客户结构（引擎外字段）→ 「需 AI 补查（引擎外字段）」。"""
        from lib.industry import tech_hardware
        from lib.render_markdown._base import _render_success_factors

        coll = collection_v2_minimal()
        coll["success_factors"] = {
            "industry": "半导体",
            "covered": True,
            "factors": tech_hardware.tech_hardware_profile.success_factors,
        }
        joined = "\n".join(_render_success_factors(coll))
        assert "需 AI 补查" in joined
        assert "客户集中度" in joined

    def test_unregistered_industry_annotation(self):
        """未覆盖行业 → 「无行业成功因素定义」一行，不渲染因素列表。"""
        from lib.render_markdown._base import _render_success_factors

        coll = collection_v2_minimal()
        coll["success_factors"] = {"industry": "白酒", "covered": False, "factors": []}
        lines = _render_success_factors(coll)
        assert any("无行业成功因素定义" in line for line in lines)
        assert any("白酒" in line for line in lines)

    def test_missing_config_renders_nothing(self):
        from lib.render_markdown._base import _render_success_factors

        assert _render_success_factors(collection_v2_minimal()) == []
