"""行业特异性分析模块包。

提供行业感知的估值指标选择、运营指标推荐、Known Unknowns 生成、
质量检查豁免和风险信号。

使用方式:
    from lib.industry import resolve_industry_profile

    profile = resolve_industry_profile("股份制银行")
    # profile.primary_valuation_metrics → ["pb", "pe"]
    # profile.operational_metrics → {"nim": {...}, "npl_ratio": {...}, ...}
"""

from __future__ import annotations

from .base import (
    # 数据类
    IndustryProfile,
    default_profile,

    # 注册与路由
    register_profile,
    resolve_industry_profile,

    # 快捷方法
    get_sector_group,
    get_valuation_metrics,
    get_operational_metrics,
    get_quality_overrides,
    get_unknown_rules,
    get_risk_signals,
    get_fast_veto_skips,
    validate_success_factors,
    get_success_factors,

    # 行业类型判断
    is_financial_sector,
    is_tech_sector,
    is_consumer_sector,
    is_cyclical_sector,

    # 工具
    list_registered_industries,
)

# 子模块（导入即注册到 _REGISTRY）
from . import banks          # noqa: F401
from . import tech_hardware  # noqa: F401

__all__ = [
    "IndustryProfile",
    "default_profile",
    "register_profile",
    "resolve_industry_profile",
    "get_sector_group",
    "get_valuation_metrics",
    "get_operational_metrics",
    "get_quality_overrides",
    "get_unknown_rules",
    "get_risk_signals",
    "get_fast_veto_skips",
    "validate_success_factors",
    "get_success_factors",
    "is_financial_sector",
    "is_tech_sector",
    "is_consumer_sector",
    "is_cyclical_sector",
    "list_registered_industries",
]
