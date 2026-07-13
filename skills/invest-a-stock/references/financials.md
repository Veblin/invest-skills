# 财报深度研究专项

> 受 [SKILL.md](../SKILL.md) LAW 1–16 约束。`plan --intent financials_deep` 时加载本文件。

## 采集工作流

```bash
uv run python skills/invest-a-stock/scripts/invest.py plan 600176 --intent financials_deep > /tmp/plan.json
uv run python skills/invest-a-stock/scripts/invest.py collect 600176 --plan /tmp/plan.json
uv run python skills/invest-a-stock/scripts/invest.py evidence 600176 --plan /tmp/plan.json
uv run python skills/invest-a-stock/scripts/invest.py report 600176 --plan /tmp/plan.json --mode full
```

采集维度：`financials`（deep）、`valuation`（deep）、`quote`、`holder_changes`、`basic_info`。

## F-1 理解陈述撰写规范

报告末尾固定输出 5 句模板，引擎填充已知字段，Claude 补充定性描述：

1. **生意本质**：这门生意的本质是___，所属行业为{industry}，核心变量是___
2. **护城河**：护城河来自___，当前在变宽/变窄/持平，依据是___
3. **管理层**：管理层在{决策类型}方面的公开记录显示___
4. **估值位置**：当前估值相当于历史{分位}位置，DCF 隐含假设是___
5. **不确定性**：最大的不确定性是___，下一观察节点是___

**禁用表述**：不得出现"我以 X 元买入""我会买入""值得投资"等第一人称投资动作。

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
4. 详细表格写入 `reports/{symbol}-{name}/{date}.md`
