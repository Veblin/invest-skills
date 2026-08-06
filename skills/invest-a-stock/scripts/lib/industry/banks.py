"""银行行业特异性分析模块。

银行业核心特点：
  - 高杠杆经营，PE 受拨备/坏账周期影响大，PB 是更可靠的估值锚
  - 盈利能力看净息差(NIM)和 ROA/ROE，资产质量看不良率和拨备覆盖率
  - 资本充足率决定增长天花板
  - 利润可操纵性强（拨备调节），OCF/净利润等通用质量指标不适用
"""

from __future__ import annotations

from .base import IndustryProfile, register_profile

# ---------------------------------------------------------------------------
# 银行行业 Profile
# ---------------------------------------------------------------------------

_BANK_KEYWORDS = ["银行"]

bank_profile = IndustryProfile(
    sector_group="financial",
    sw_name="银行",

    # 估值：银行优先用 PB（资产定价），PE 辅助
    primary_valuation_metrics=["pb", "pe"],
    secondary_valuation_metrics=["dv_ratio", "ps"],

    # 银行特有运营指标
    operational_metrics={
        "roe": {
            "field": "roe", "threshold": 10.0, "direction": "higher_better",
            "display": "ROE (%)",
            "note": "银行 ROE >10% 为良好，>15% 为优秀（受杠杆率约束）",
        },
        "roa": {
            "field": "roa", "threshold": 0.8, "direction": "higher_better",
            "display": "ROA (%)",
            "note": "ROA 比 ROE 更能反映银行真实盈利能力（不受杠杆扭曲）",
        },
        "nim": {
            "field": "net_interest_margin", "threshold": 2.0,
            "direction": "higher_better", "display": "净息差 NIM (%)",
            "note": "NIM <1.5% 为承压，>2.5% 为优秀。受LPR和存款成本双重影响",
        },
        "npl_ratio": {
            "field": "npl_ratio", "threshold": 1.5, "direction": "lower_better",
            "display": "不良贷款率 (%)",
            "note": "<1% 优秀，1-2% 正常，>2% 关注，>3% 高风险",
        },
        "provision_coverage": {
            "field": "provision_coverage_ratio", "threshold": 200.0,
            "direction": "higher_better", "display": "拨备覆盖率 (%)",
            "note": ">200% 充足，150-200% 正常，<150% 不足（监管红线）",
        },
        "cet1_ratio": {
            "field": "cet1_capital_adequacy_ratio", "threshold": 10.0,
            "direction": "higher_better", "display": "核心一级资本充足率 (%)",
            "note": "监管底线 5%（系统重要性银行更高），越高增长空间越大",
        },
        "cost_income_ratio": {
            "field": "cost_income_ratio", "threshold": 35.0,
            "direction": "lower_better", "display": "成本收入比 (%)",
            "note": "反映经营效率，<30% 优秀，>40% 效率偏低",
        },
    },

    # 质量检查覆盖：银行不适用毛利率/OCF/存货等通用指标
    quality_overrides={
        "gross_margin": "skip",          # 银行无毛利率概念
        "gross_margin_volatility": "skip",
        "ocf_to_np": "skip",             # 银行 OCF 概念不同
        "ocf_negative": "skip",
        "inventory_turnover": "skip",     # 银行无存货
        "receivables_turnover": "skip",   # 银行应收概念不同
        "roic": "skip",                  # ROIC 对银行意义不大
        "debt_ratio": "skip",            # 银行天然高杠杆，用资本充足率替代
        "interest_coverage": "skip",      # 银行不适用
    },

    # Known Unknowns — 银行特有
    unknown_rules=[
        (
            "资产质量真实性：不良贷款率是否真实反映资产质量？"
            "关注逾期90天以上贷款/不良贷款偏离度、关注类贷款迁徙率、"
            "重组贷款占比。拨备覆盖率是否充足？",
            "银行利润极易通过拨备调节。不良认定宽松 + 拨备不足 = "
            "利润虚高。这是银行分析中最重要的单一问题。",
        ),
        (
            "利率风险敞口：资产负债期限错配程度如何？"
            "利率下降对 NIM 的冲击有多大？重定价缺口分布如何？",
            "利率市场化 + LPR 改革持续压缩 NIM。存贷款重定价周期错配"
            "决定了利率变动对利润的传导速度和幅度。",
        ),
        (
            "房地产/城投敞口：对公贷款中房地产和城投占比多少？"
            "抵押物充足率如何？是否有隐性坏账未被确认为不良？",
            "房地产下行周期 + 地方债务化解是银行最大的潜在风险源。"
            "财报披露通常不充分，需产业链调研补充。",
        ),
    ],

    # 行业特有风险信号
    risk_signals={
        "npl_rising": {
            "name": "不良率连续上升",
            "category": "financial",
            "severity": "high",
            "detail_template": (
                "不良率连续{periods}期上升（{from_val}% → {to_val}%），"
                "关注资产质量恶化趋势"
            ),
        },
        "provision_insufficient": {
            "name": "拨备覆盖率不足",
            "category": "financial",
            "severity": "critical",
            "detail_template": (
                "拨备覆盖率 {val}%，低于 150% 监管红线，"
                "未来利润可能被拨备计提侵蚀"
            ),
        },
        "nim_compression": {
            "name": "净息差持续收窄",
            "category": "business",
            "severity": "medium",
            "detail_template": (
                "NIM 连续{periods}期收窄（{from_val}% → {to_val}%），"
                "利率下行 + 竞争加剧压缩盈利空间"
            ),
        },
    },

    # 快速否决不适用的项
    fast_veto_skips=[
        "negative_ocf",           # 银行 OCF 概念不同
        "high_goodwill_ratio",    # 银行通常无商誉
    ],

    # 行业成功关键因素（R4）— 报告先答这 3 问，再进通用 12 题
    success_factors=[
        {
            "question": "净息差（NIM）是否稳定？LPR 下行环境下，息差收窄是被规模增长抵消，还是正在侵蚀利润？",
            "data_fields": ["net_interest_margin", "roa"],
            "sources": ["tushare fina_indicator: net_interest_margin/roa", "akshare 财务摘要"],
            "answer_template": "引用最新期 NIM 与 ROA → 判断息差方向与驱动（LPR/存款成本）→ 与同业对比 → 结论：盈利核心是否承压",
        },
        {
            "question": "不良率与拨备覆盖率是否真实反映资产质量（逾期 90 天偏离度、关注类迁徙）？",
            "data_fields": ["npl_ratio", "provision_coverage_ratio"],
            "sources": ["tushare fina_indicator: npl_ratio/provision_coverage_ratio", "年报附注（逾期/迁徙数据，需 AI 补查）"],
            "answer_template": "引用不良率/拨备覆盖率 → 判断拨备缓冲是否充足 → 逾期偏离度需 AI 补查年报附注 → 结论：资产质量真实性评估",
        },
        {
            "question": "核心一级资本充足率是否支撑资产扩张？内源资本积累 vs 再融资压力如何？",
            "data_fields": ["cet1_capital_adequacy_ratio"],
            "sources": ["tushare fina_indicator: cet1_capital_adequacy_ratio", "公司再融资公告（需 AI 补查）"],
            "answer_template": "引用核心一级资本充足率 → 判断距监管底线缓冲 → 再融资计划需 AI 补查公告 → 结论：增长天花板评估",
        },
    ],
)

# 注册
register_profile(_BANK_KEYWORDS, bank_profile)
