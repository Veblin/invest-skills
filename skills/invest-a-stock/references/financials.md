# 财报深度研究专项

> 受 [SKILL.md](../SKILL.md) LAW 1–17 约束。`plan --intent financials_deep` 时加载本文件。

## 采集工作流

```bash
uv run python skills/invest-a-stock/scripts/invest.py plan 600176 --intent financials_deep > /tmp/plan.json
uv run python skills/invest-a-stock/scripts/invest.py collect 600176 --plan /tmp/plan.json
uv run python skills/invest-a-stock/scripts/invest.py evidence 600176 --plan /tmp/plan.json
uv run python skills/invest-a-stock/scripts/invest.py report 600176 --plan /tmp/plan.json --mode full
```

采集维度：`financials`（deep）、`valuation`（deep）、`quote`、`holder_changes`、`basic_info`。

## F-1 估值位置判断规范

Claude 在 `financials_deep` 分析估值段落时须遵守以下规则（引擎 `_section_snapshot` 估值位置段为参考模板）：

### 适用范围

- **仅模块 1（当前状态快照）的「估值位置」段**使用「分位」一词
- 其余模块（动态驱动、市场结构、Bull/Bear、风险等）改用「历史位置」或「区间位置」，避免「分位」一词泛滥

### 分位数字不得单独出现

每个分位数字必须伴随对应中位数或均值：

- ✅ `PE 93.11x，近4年 98.3% 分位（中位数 37.52x）`
- ❌ `PE 处于历史 98.3% 分位`

### 亏损期强制标注

若 PE 历史序列中亏损期（负数 PE 已剔除）占比 >30%，须在分位旁标注：

> 该标的历史大部分时间微利/亏损，PE 分位数仅作位置参考，不反映估值贵贱

### 多情景估值（LAW 6）

- 允许多情景估值参考价（乐观/中性/悲观），须标注各情景假设前提与概率权重
- 须注明「仅供参考，不构成投资建议」
- **禁止**无假设前提的单一目标价数字（如「目标价 XX 元」）

### 禁用表述

| 禁止 | 替换为 |
|------|--------|
| 「极度高估/极度低估」 | 数值比较，如 `PE 65x vs 历史中位数 37x，溢价约 1.8 倍` |
| 「买入/卖出/持有/建仓」 | 删除；仅陈述估值位置与假设 |
| 无来源的「往往/通常」 | 标注「待补」或转为有出处的案例 |

### 数据引用

估值分位、PE/PB 当前值须标注 `[来源: valuation / daily_basic / 日期]` 或引擎已渲染的来源行；推测性判断标注 `[推测，待验证]`。

## F-2 Bull-Bear 撰写规范

- **空方篇幅**：bear 论点数量 ≥ bull 论点数量 - 1
- **数字链条**：每条款附带数值传导路径
- **禁止收敛为共识**：不得出现"综合来看""整体偏多""总体偏空"

## F-3 快速否决 8 条判断规范

`_check_fast_veto()` 自动检测可量化项；其余由 Claude 判断。

| # | 否决项 | 检测方式 |
|:--:|------|------|
| 1 | 连续 5 年累计 FCF 为负 | financials 自动，**硬触发** |
| 2 | 连续 3 年经营性现金流为负 | financials 自动，软触发 |
| 3 | 审计意见非标准无保留 | 需 WebSearch/公告（v0.2.0 自动化） |
| 4 | 业务描述无法理解 | Claude 判断 |
| 5 | 控股股东/管理层严重诚信问题 | 需公开记录（v0.2.0 自动化） |
| 6 | 资产负债率 > 90% 且无改善 | financials 自动，**硬触发** |
| 7 | 近 3 年 ROE 连续 < 5% | financials 自动，软触发 |
| 8 | 商誉/净资产 > 50% | balancesheet 自动，**硬触发** |

### F-3 决策树（报告结构分支）

```
_check_fast_veto() 结果
├── 硬触发（#1/#6/#8）
│   ├── DCF 估值段跳过，标注「研究终止条件触发，估值段落已跳过」
│   └── 仍输出 F-3 触发条目 + 风险模块
├── 仅软触发（#2/#7）
│   ├── 完整输出 DCF + 估值段
│   └── 估值段前插入软触发预警框
└── 无自动触发
    ├── #3/#5：Claude 检索公告后判断，标注 [推测，待验证] 或 [来源: ...]
    └── #4：业务描述段标注数据不足或壳公司风险
```

## F-4 六关评分速览规范

| 关口 | 评分引擎 | 评分含义 |
|------|---------|------|
| 生意 | A-4 画布维度综合 | 规模效应/增长驱动/周期性/资本密集度均值 |
| 护城河 | scoring.revenue_quality + customer_lockin | 收入模式质量 + 客户锁定 |
| 管理层 | scoring.management_ability_proxy | ROIC/CAPEX/利润率/内部人信号（置信度中） |
| 财务 | scoring 子信号 | 毛利率稳定性 + OCF 覆盖 |
| 估值 | valuation 历史位置 | 历史位置越高表示定价位于自身历史更高区间 |
| 风险 | risk_scanner 触发数 | 触发越多风险越高 |

### F-4 与 `_section_six_gates_scorecard` 对应

引擎 `_section_six_gates_scorecard()` 渲染六关表格；Claude 在 `financials_deep` 分析时：

- 每关须引用引擎分数或标注「数据不足，本关跳过」
- 财务关由引擎在 note 列渲染 `revenue_acceleration_flag` / `ocf_np_divergence_flag` 软信号（有数据则展示）

**数据不足标准句式**：

> 「{关口名}：数据不足（缺少 {字段名}），本关仅作定性参考，置信度 ❓ 弱。」

**禁止事项**：

- 不得给"通过/不通过"二元判决
- 不得映射到仓位动作
- 管理层软维度须标注"置信度中等，需定性补充"

## A-4/A-5 置信度标注要求

- **商业模式画布 7 维度**：5/7 可量化（scoring.py），2/7 标注"数据不足，定性推断，置信度低"
- **管理层评估**：定量代理评分须附带："Demerjian, Lev & McVay (2012) 方法仅覆盖运营效率维度，企业文化/诚信/战略远见等软维度需人工定性判断，不构成信任/不信任的二元结论"

## 分析输出结构（financials_deep 简报）

Claude 内简报聚焦：

1. F-3 触发状态（硬/软/无）
2. 12 题核心矛盾（数据不足项列出）
3. 六关评分极端值 + 下一验证节点
4. 详细表格写入 `reports/{symbol}-{name}/{YYYY-MM-DD-HH-MM-SS}.md`
