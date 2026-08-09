"""科技硬件行业特异性分析模块（半导体/电子/集成电路）。

科技硬件核心特点：
  - 重资产、强周期，资本开支与产能周期决定盈利拐点
  - 技术迭代快，研发投入是关键护城河指标
  - 受地缘政治（出口管制）影响大
  - 估值看 PEG 和 EV/EBITDA 比 PE 更有意义（高资本开支折旧扭曲利润）
"""

from __future__ import annotations

from .base import IndustryProfile, register_profile

# ---------------------------------------------------------------------------
# 科技硬件行业 Profile
# ---------------------------------------------------------------------------

_TECH_HARDWARE_KEYWORDS = ["半导体", "芯片", "集成电路", "电子"]

tech_hardware_profile = IndustryProfile(
    sector_group="tech",
    sw_name="电子/半导体",

    # 估值：科技硬件高 Capex → EV/EBITDA 更合理；高增长看 PEG
    primary_valuation_metrics=["pe", "peg", "ev_ebitda"],
    secondary_valuation_metrics=["pb", "ps"],

    # 科技硬件特有运营指标
    operational_metrics={
        "rd_ratio": {
            "field": "rd_expense_ratio", "threshold": 8.0,
            "direction": "higher_better", "display": "研发费用率 (%)",
            "note": "半导体行业研发费用率通常 8-15%。<5% 缺乏技术投入，>20% 关注转化效率",
        },
        "gross_margin": {
            "field": "grossprofit_margin", "threshold": 35.0,
            "direction": "higher_better", "display": "毛利率 (%)",
            "note": ">40% 通常意味着强定价权或技术壁垒，<25% 竞争激烈或处于低端环节",
        },
        "capex_to_revenue": {
            "field": "capex_to_revenue", "threshold": 15.0,
            "direction": "neutral", "display": "资本开支/营收 (%)",
            "note": "高 Capex 意味着扩产（看需求是否匹配）。>25% 重资产模式，关注折旧对利润的侵蚀",
        },
        "depreciation_to_revenue": {
            "field": "depreciation_to_revenue", "threshold": 10.0,
            "direction": "lower_better", "display": "折旧/营收 (%)",
            "note": "与 Capex 联动看产能周期：Capex/折旧>1.5 扩产中，<1 收缩中",
        },
        "inventory_turnover": {
            "field": "inventory_turnover", "threshold": 4.0,
            "direction": "higher_better", "display": "存货周转率 (次)",
            "note": "半导体库存周期 3-4 个季度。周转率下降 + 存货上升 = 可能进入下行通道",
        },
        "ocf_to_np": {
            "field": "ocf_to_np", "threshold": 0.8,
            "direction": "higher_better", "display": "OCF/净利润",
            "note": "高 Capex 企业 OCF 必须远高于净利润才有真正的自由现金流",
        },
    },

    # 质量检查覆盖：科技硬件通常高资本开支，自由现金流为负是正常的
    quality_overrides={
        # 不跳过毛利率检查（科技硬件毛利率很重要）
        # 不跳过 OCF 检查（但阈值适当放宽）
        # 不跳过存货检查（芯片库存周期是关键指标）
    },

    # Known Unknowns — 科技硬件特有
    unknown_rules=[
        (
            "国产化率与出口管制：核心设备/材料/EDA工具在国产化率如何？"
            "美国出口管制升级对公司的实际影响程度？是否有替代方案？"
            "先进制程产能是否受限？",
            "半导体产业链高度依赖进口设备与关键材料，出口管制升级或"
            "国产替代加速均可能显著改变公司中期收入/成本结构，且这类"
            "信息通常无法从财报直接获得，需产业链调研或公司公告补充。",
        ),
        (
            "产能周期位置：当前全球/国内半导体产能处于扩张期还是出清期？"
            "公司新增产能何时投产？下游需求能否消化？"
            "价格（如存储芯片、MCU、模拟芯片）处于周期什么位置？",
            "半导体是强周期行业，产能投放与需求错配导致价格大幅波动。"
            "当前 Capex/折旧比、行业库存水位、代工厂产能利用率是判断周期位置的"
            "最佳先行指标。",
        ),
        (
            "技术路线风险：公司所处技术路线（如 SiC/GaN vs 传统硅基、"
            "Chiplet vs 单芯片、RISC-V vs ARM）是否面临颠覆性替代风险？"
            "下一代技术节点的研发进度与竞争对手对比如何？",
            "技术路线选择错误是科技硬件最大的长期风险。短期财报无法反映"
            "技术替代的潜在影响，需关注行业技术会议、客户认证进度和研发人员流动。",
        ),
    ],

    # 行业特有风险信号
    risk_signals={
        "inventory_buildup": {
            "name": "存货积压 + 周转放缓",
            "category": "business",
            "severity": "high",
            "detail_template": (
                "存货连续{periods}期上升而周转率下滑（周转率 {turnover} 次，"
                "同比 {yoy_pct}%），警惕需求走弱与存货减值风险"
            ),
        },
        "rd_cutback": {
            "name": "研发费用率持续下降",
            "category": "business",
            "severity": "medium",
            "detail_template": (
                "研发费用率从 {from_val}% 降至 {to_val}%，"
                "可能牺牲长期竞争力换取短期利润"
            ),
        },
        "capex_decline_with_high_depreciation": {
            "name": "资本开支收缩 + 高折旧",
            "category": "financial",
            "severity": "medium",
            "detail_template": (
                "Capex/折旧比 {capex_dep_ratio}（<1 为收缩），"
                "关注是否错失下一轮产能扩张窗口"
            ),
        },
    },

    # 快速否决不适用的项
    fast_veto_skips=[],

    # 行业成功关键因素（R4）— 报告先答这 3 问，再进通用 12 题
    success_factors=[
        {
            "question": "当前处于产能周期什么位置？Capex 扩张与折旧的匹配度（Capex/折旧比）如何？",
            "data_fields": ["capex_to_revenue", "depreciation_to_revenue"],
            "sources": ["tushare cashflow: cap_ex（R12b 接入）", "tushare fina_indicator: 折旧科目"],
            "answer_template": "引用 Capex/营收 与 折旧/营收 → 判断扩产期（Capex/折旧>1.5）还是收缩期（<1）→ 下游需求能否消化产能 → 结论：产能周期定位",
        },
        {
            "question": "所处技术路线（SiC/GaN/Chiplet/RISC-V 等）是否面临替代风险？研发投入的转化效率如何？",
            "data_fields": ["rd_expense_ratio"],
            "sources": ["tushare fina_indicator: rd_expense_ratio", "行业技术会议/客户认证（引擎外，需 AI 补查）"],
            "answer_template": "引用研发费用率 → 判断投入强度 vs 转化（新品收入占比需 AI 补查）→ 技术替代风险需 AI 补查行业动态 → 结论：技术护城河评估",
        },
        {
            "question": "客户集中度与下游需求结构如何？大客户依赖是否存在风险？",
            "data_fields": [],
            "sources": ["年报/公告（引擎外字段，需 AI 补查）"],
            "answer_template": "前五大客户占比需 AI 补查年报 → 判断客户集中度风险 → 下游景气传导路径 → 结论：客户结构评估",
        },
    ],
)

# 注册
register_profile(_TECH_HARDWARE_KEYWORDS, tech_hardware_profile)
