---
name: invest-A
version: "0.3.0"
description: "A股个股调研 AI 学习技能 — 数据采集 + 学术级引用，产出带来源追溯的 Markdown 学习报告。这是一个学习工具，不是决策工具。"
argument-hint: "/invest-A 600176 | /invest-A 600176 --with-macro | /invest-A 600176 --compare 000858"
allowed-tools: Bash, Read, Write, WebSearch, WebFetch
user-invocable: true
metadata:
  requires:
    bins: [python3]
  optionalEnv:
    - TUSHARE_TOKEN
    - FRED_API_KEY
    - TAVILY_API_KEY
---

# 投资学习 Skill

## 核心定位

**这是一个学习工具，不是决策工具。** 不提供买卖建议、目标价或仓位建议。

## 📡 数据来源配置

当前已配置的数据源：
- **Tushare Pro** ✅ — 基础信息、日线、财务指标、十大股东、资金流向
- **FRED** ✅ — US 10Y/2Y/VIX/CPI/美元指数 全部可用
- **腾讯行情** ✅ — 实时报价兜底
- **akshare/efinance** ✅ — 已安装，EastMoney API 当前不可用

## CLI 命令（核心交互入口）

所有数据采集和分析通过 `invest.py` 完成。Claude 根据用户意图构建对应命令：

```bash
# 采集并展示
python3 skills/invest-A/scripts/invest.py collect 600176

# 生成 JSON 格式报告
python3 skills/invest-A/scripts/invest.py report 600176 --emit=json

# 生成 Markdown 分析报告
python3 skills/invest-A/scripts/invest.py report 600176 --emit=md

# 对比双标的财务数据
python3 skills/invest-A/scripts/invest.py compare 600176 000858

# 检查各数据源可用性
python3 skills/invest-A/scripts/invest.py diagnose

# 查看历史采集记录
python3 skills/invest-A/scripts/invest.py store list

# 保存本次采集到持久化存储
python3 skills/invest-A/scripts/invest.py collect 600176 --store
```

> **注意**：`inest.py` 在 `code/skills/invest-A/scripts/` 下。运行前确保在 `code/` 目录。
> 所有子命令支持 `--help` 查看参数详情。

## 采集顺序

1. **`diagnose`** — 先检查数据源是否可用
2. **`collect`** — 采集指定股票的六维度数据（基本信息、财务、行情、股东、资金流、日K线）
3. **`report`** — 对采集结果进行分析并生成报告（Claude 手动分析 + 渲染）
4. **`store`** — 将高质量采集结果持久化（可选）

---

## 输出契约（9 条 LAWs）

以下法则约束所有输出。违反即为 Bug。

**LAW 1** — 每条分析论述必须引用数据来源。
**LAW 2** — 报告使用统一七维度结构：基本信息 → 财务报告 → 行业 → 估值/市场 → 机构 → 情绪 → 宏观。
**LAW 3** — 区分"事实陈述"与"分析判断"。
**LAW 4** — 风险提示出现首部和尾部。
**LAW 5** — 单源数据标注不确定性，多源交叉验证优先。
**LAW 6** — 禁止买卖建议、目标价、仓位建议。
**LAW 7** — 每个数字标注可审查的追溯路径（函数调用、查询语句、公告链接）。
**LAW 8** — 每个维度末尾要求"🔍 待独立验证项"。
**LAW 9** — 无数据源支撑的分析不输出。

## 引用格式规范

```
事实性陈述： "数值" [来源: {追溯路径} / {获取日期}]
  ✅ "营收 188.81 亿元 [来源: WebSearch / query: '中国巨石 600176 2025年报'
     结果3(东方财富 finance.eastmoney.com/a/...) / 2026-06-10]"
  ✅ "当前 PE(TTM) 41.23x [来源: collector.collect_quote(symbol='600176') → tencent_finance / 2026-06-10]"

分析性判断： "判断" [依据: {数据来源}； 逻辑链: {推理}]
推测： "推测" [推测，待验证]
无数据： "该维度数据不可得。[尝试了 {源1}、{源2}]"
```

## 技术指标规则

均线（MA）和 MACD 仅用于**描述市场状态**，不生成交易信号。

---

## 七维度指引（分析阶段使用）

| 维度 | 分析要点 | 数据来源 |
|------|---------|---------|
| 基本信息 | 公司概况、股权结构、治理信号 | collect.basic_info |
| 财务报告 | ROE趋势、毛利率、现金流、成长性 | collect.financials |
| 行业产业链 | 竞争格局、壁垒、上下游 | Claude 分析（WebSearch 补充） |
| 估值/市场 | PE/PB水位、历史分位、融资余额 | collect.quote + collect.kline |
| 机构分析师 | 股东结构、北向资金、机构评级 | collect.shareholders + collect.northbound |
| 情绪舆情 | 近期事件、媒体报道 | Claude 分析（WebSearch 补充） |
| 宏观政策 | 利率、汇率、行业政策 | `--with-macro` 时 Claude 分析 |

> ⚠️ "行业产业链"和"情绪舆情"两个维度无稳定 API 支撑，依赖 WebSearch 和 Claude 分析，须标注数据来源并提示不确定性。
>
> **⚠️ 项目根目录的 `archive/` 是旧代码归档（v0.2 遗留），不被 Skill 引用。**
> **不要从 `archive/` 中导入任何 Python 模块或引用配置。** 该目录仅保留用于历史追溯。
