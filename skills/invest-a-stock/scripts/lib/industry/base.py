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

# 关键词 → 模块名的映射（在子模块加载时填充）
_KEYWORD_MODULE_MAP: list[tuple[list[str], str]] = [
    (["银行"], "banks"),
    (["保险"], "insurance"),
    (["证券", "券商"], "securities"),
    (["房地产", "地产"], "real_estate"),
    (["半导体", "芯片", "集成电路", "电子"], "tech_hardware"),
    (["计算机", "软件", "IT服务", "互联网"], "tech_software"),
    (["医药", "生物", "制药", "医疗"], "pharma"),
    (["白酒", "食品", "饮料", "乳业", "调味品"], "consumer"),
    (["新能源汽车", "锂电", "光伏", "储能", "风电"], "autos_new_energy"),
    (["汽车"], "autos_new_energy"),
    (["化工", "钢铁", "有色", "煤炭", "石油", "金属", "建材"], "energy_materials"),
    (["电力", "公用"], "utilities"),
    (["军工", "国防"], "defense"),
    (["通信", "5G"], "telecom"),
]


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


def get_valuation_metrics(industry: str) -> list[str]:
    """快捷方法：获取行业应优先展示的估值指标。"""
    return resolve_industry_profile(industry).primary_valuation_metrics


def get_operational_metrics(industry: str) -> dict[str, dict[str, Any]]:
    """快捷方法：获取行业特有运营指标定义。"""
    return resolve_industry_profile(industry).operational_metrics


def get_quality_overrides(industry: str) -> dict[str, str]:
    """快捷方法：获取质量检查覆盖规则。"""
    return resolve_industry_profile(industry).quality_overrides


def get_unknown_rules(industry: str) -> list[tuple[str, str]]:
    """快捷方法：获取行业 Known Unknowns 问题模板。"""
    return resolve_industry_profile(industry).unknown_rules


def get_risk_signals(industry: str) -> dict[str, dict[str, Any]]:
    """快捷方法：获取行业特有风险信号。"""
    return resolve_industry_profile(industry).risk_signals


def get_fast_veto_skips(industry: str) -> list[str]:
    """快捷方法：获取不适用的快速否决项。"""
    return resolve_industry_profile(industry).fast_veto_skips


def is_financial_sector(industry: str) -> bool:
    """是否是金融行业（银行/保险/券商）。"""
    return get_sector_group(industry) == "financial"


def is_tech_sector(industry: str) -> bool:
    """是否是科技行业（硬件/软件）。"""
    return get_sector_group(industry) in ("tech", "tech_hardware", "tech_software")


def is_consumer_sector(industry: str) -> bool:
    """是否是消费品行业。"""
    return get_sector_group(industry) == "consumer"


def is_cyclical_sector(industry: str) -> bool:
    """是否是周期性行业（工业/能源/材料）。"""
    return get_sector_group(industry) == "industrial"


def list_registered_industries() -> list[str]:
    """列出所有已注册的行业关键词。"""
    return sorted(_REGISTRY.keys(), key=len, reverse=True)


# ---------------------------------------------------------------------------
# 预加载子模块（延迟导入以避免循环依赖）
# ---------------------------------------------------------------------------

_loaded = False


def _ensure_loaded() -> None:
    """确保所有行业子模块已加载到 _REGISTRY 中。"""
    global _loaded
    if _loaded:
        return
    # 按需导入子模块（副作用：调用 register_profile 填充 _REGISTRY）
    from . import banks  # noqa: F401
    from . import tech_hardware  # noqa: F401
    # 后续迭代中取消注释：
    # from . import insurance  # noqa: F401
    # from . import securities  # noqa: F401
    # from . import real_estate  # noqa: F401
    # from . import tech_software  # noqa: F401
    # from . import pharma  # noqa: F401
    # from . import consumer  # noqa: F401
    # from . import energy_materials  # noqa: F401
    # from . import autos_new_energy  # noqa: F401
    # from . import utilities  # noqa: F401
    _loaded = True


# 在 resolve_industry_profile 入口处确保已加载
# （通过在函数内调用 _ensure_loaded）
# 注：实际集成时在 resolve_industry_profile() 顶部加 _ensure_loaded()
