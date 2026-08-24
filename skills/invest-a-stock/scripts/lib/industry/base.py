"""行业特异性分析框架 — 基类、注册表与路由。

每个行业模块实现 IndustryProfile，通过关键词匹配路由到对应模块。
无匹配的行业使用 default_profile（当前通用 PE/PB/ROE 框架）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# 核心数据类
# ---------------------------------------------------------------------------

@dataclass
class IndustryProfile:
    """行业特异性分析配置。

    每个行业模块实例化一个 IndustryProfile，注册到 _REGISTRY。
    """

    # 行业标识
    sector_group: str  # "financial" | "tech" | "consumer" | "industrial" | "healthcare"

    # 估值方法 — 该行业应优先使用的估值指标
    primary_valuation_metrics: list[str] = field(default_factory=lambda: ["pe", "pb"])
    secondary_valuation_metrics: list[str] = field(default_factory=lambda: ["ps", "dv_ratio"])

    # 行业特有关键运营指标 {指标名: {field, threshold, direction, display}}
    operational_metrics: dict[str, dict[str, Any]] = field(default_factory=dict)

    # 质量检查覆盖 — 哪些通用质量检查项不适用于本行业
    # {"通用指标ID": "skip" | "替代方法名"}
    quality_overrides: dict[str, str] = field(default_factory=dict)

    # Known Unknowns — 行业特有的待验证问题 [(问题, 为什么重要), ...]
    unknown_rules: list[tuple[str, str]] = field(default_factory=list)

    # 行业特有风险信号 — {signal_id: {name, severity, detail_template}}
    risk_signals: dict[str, dict[str, Any]] = field(default_factory=dict)

    # 行业适用/不适用的快速否决项
    fast_veto_skips: list[str] = field(default_factory=list)

    # 源数据字段 — 该行业需要额外采集的财务字段
    extra_financial_fields: list[str] = field(default_factory=list)

    # 行业名（SW2021 分类）
    sw_name: str = ""

    # 行业成功关键因素（R4）— 每项: {question, data_fields, sources, answer_template}
    # 报告先答这些再进通用 12 题；无数据字段支撑的项 data_fields 可为空（引擎外，需 AI 补查）
    success_factors: list[dict] = field(default_factory=list)


# ---------------------------------------------------------------------------
# 默认 Profile — 当前通用框架（行业中性）
# ---------------------------------------------------------------------------

default_profile = IndustryProfile(
    sector_group="general",
    primary_valuation_metrics=["pe", "pb"],
    secondary_valuation_metrics=["ps", "dv_ratio"],
    operational_metrics={
        "roe": {"field": "roe", "threshold": 15.0, "direction": "higher_better",
                "display": "ROE (%)"},
        "gross_margin": {"field": "grossprofit_margin", "threshold": 30.0,
                         "direction": "higher_better", "display": "毛利率 (%)"},
        "net_margin": {"field": "netprofit_margin", "threshold": 10.0,
                       "direction": "higher_better", "display": "净利率 (%)"},
        "ocf_to_np": {"field": "ocf_to_np", "threshold": 0.8,
                      "direction": "higher_better", "display": "OCF/净利润"},
        "debt_ratio": {"field": "debt_to_assets", "threshold": 60.0,
                       "direction": "lower_better", "display": "资产负债率 (%)"},
    },
    quality_overrides={},
    unknown_rules=[],
    risk_signals={},
    fast_veto_skips=[],
)


# ---------------------------------------------------------------------------
# 行业注册表（关键词 → 模块名）
# 按长度降序匹配，避免"新能源汽车"误命中"汽车"
# ---------------------------------------------------------------------------

_REGISTRY: dict[str, IndustryProfile] = {}


def register_profile(keywords: list[str], profile: IndustryProfile) -> None:
    """注册一个行业 Profile 到关键词列表。"""
    for kw in keywords:
        _REGISTRY[kw] = profile


def resolve_industry_profile(industry: str) -> IndustryProfile:
    """根据申万行业名称解析对应的 IndustryProfile。

    按关键词长度降序匹配，无匹配返回 default_profile。

    Args:
        industry: 申万行业名称，如 "股份制银行"、"半导体"、"白酒"

    Returns:
        IndustryProfile 实例
    """
    if not industry or not industry.strip():
        return default_profile

    name = industry.strip()

    # 按关键字长度降序匹配（避免"新能源汽车"误命中"汽车"）
    sorted_keywords = sorted(_REGISTRY.keys(), key=len, reverse=True)
    for keyword in sorted_keywords:
        if keyword in name:
            return _REGISTRY[keyword]

    return default_profile


def get_sector_group(industry: str) -> str:
    """快捷方法：获取行业的 sector_group。"""
    return resolve_industry_profile(industry).sector_group


def get_quality_overrides(industry: str) -> dict[str, str]:
    """快捷方法：获取质量检查覆盖规则。"""
    return resolve_industry_profile(industry).quality_overrides


def get_unknown_rules(industry: str) -> list[tuple[str, str]]:
    """快捷方法：获取行业 Known Unknowns 问题模板。"""
    return resolve_industry_profile(industry).unknown_rules


# --- 行业成功关键因素（R4） ---

_SUCCESS_FACTOR_FIELDS = ("question", "data_fields", "sources", "answer_template")


def validate_success_factors(profile: IndustryProfile) -> list[str]:
    """校验成功关键因素结构的完整性。

    每项必须含 question / data_fields / sources / answer_template 四字段，
    缺任一 → 返回该因素的问题清单（供测试与开发期自检，运行期不阻断）。
    """
    errors: list[str] = []
    for idx, factor in enumerate(profile.success_factors):
        if not isinstance(factor, dict):
            errors.append(f"[{profile.sw_name or profile.sector_group}] 成功因素 #{idx} 非 dict")
            continue
        missing = [f for f in _SUCCESS_FACTOR_FIELDS if f not in factor]
        if missing:
            errors.append(
                f"[{profile.sw_name or profile.sector_group}] 成功因素 #{idx} "
                f"缺字段: {', '.join(missing)}"
            )
    return errors


def get_success_factors(industry: str) -> list[dict]:
    """获取行业成功关键因素清单（R4）。

    未覆盖行业 → 返回空表（渲染层输出「无行业成功因素定义」标注，回退通用 12 题）。
    """
    profile = resolve_industry_profile(industry)
    if profile is default_profile:
        return []
    return list(profile.success_factors)


# ---------------------------------------------------------------------------
# 预加载子模块（延迟导入以避免循环依赖）
# 行业子模块注册：由 industry/__init__.py 的 `from . import banks/tech_hardware`
# import 副作用调用 register_profile 填充 _REGISTRY，无需额外加载钩子。
