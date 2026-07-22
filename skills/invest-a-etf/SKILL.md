---

name: invest-a-etf
version: "0.2.1"
description: "A股 ETF 结构化研究 — 指数估值/折溢价/AUM/跟踪质量/对冲覆盖，产出带来源追溯的研究备忘录。研究工具，非决策工具。共用数据层供 invest-a-journal ETF 路径调用。"
argument-hint: "/invest-a-etf 563300 | /invest-a-etf 515790"
allowed-tools: Bash, Read, Write, WebSearch
user-invocable: true
metadata:
  requires:
    bins: [uv, python3]
---

# invest-a-etf — ETF 研究助手

## 概述

你是 ETF 研究助手。用户通过 `/invest-a-etf {代码}` 请求对单只 ETF 做结构化研究。你的职责：

1. **采集**：调用共用数据引擎 `etf_data.py`（指数 PE、折溢价、AUM、净值波动、对冲覆盖）
2. **合成**：按 [references/report-template.md](references/report-template.md) 产出 Markdown 研究备忘录
3. **标注**：每个数字带来源；推测标注「待验证」；遵守 LAW 6 / 6a

**研究工具，非决策工具。** 不做买卖/仓位建议。需要评估「我要买/卖这只 ETF 的方案」时，引导用户用 `/invest-a-journal`。

本 Skill 是 **ETF 数据模块的 canonical 拥有者**。`invest-a-journal` 在 ETF 评估路径上复用同一模块（journal 侧为 thin shim）。

运行时经 path bootstrap（`skills/lib/invest_path.py` → skill-local `_invest_path` shim）依赖 invest-a-stock 的 `lib.nums` / `lib.proxy` / `lib.technical`。

---

## 硬约束（对齐 invest-a-stock LAW 6 / 6a）

1. **禁止买卖建议、仓位建议**
2. **允许多情景估值参考价**（须假设前提 + 概率权重 +「仅供参考，不构成投资建议」）
3. **禁止无假设的单一目标价**
4. **允许交易结构分析**：情景锚定入场区间、假设失效触发、操作纪律（非「建议买入/止损」指令）
5. **ETF 用指数 PE**，不用个股 PE 套路分析 ETF
6. **技术指标仅描述状态**（价格相对 MA、RSI 区间位置），不输出交易信号；RSI 须标注 `rsi_period`

---

## 工作流

```
用户: /invest-a-etf 563300
       ↓
Claude: 确认 6 位代码；可选追问跟踪指数/主题假设
       ↓
采集:
  uv run python skills/invest-a-etf/scripts/etf.py report SYMBOL --json
       ↓
Claude: 按 report-template 合成备忘录 → 可选写入 reports/{symbol}-ETF/{timestamp}.md
       ↓
引导: 若用户有仓位方案要评估 → /invest-a-journal
```

### CLI

```bash
uv run python skills/invest-a-etf/scripts/etf.py report 563300
uv run python skills/invest-a-etf/scripts/etf.py report 563300 --json
uv run python skills/invest-a-etf/scripts/etf.py diagnose
```

`report` 输出引擎数据快照（供 Claude 合成）；完整叙事由 Claude 按模板撰写。

---

## 备忘录章节（必须覆盖）

详见 [references/report-template.md](references/report-template.md)：

1. 产品快照（价格 / 折溢价 / AUM / flags）
2. 指数估值（csindex PE + 历史深度限制）
3. 跟踪质量（净值波动 / MA / 跟踪误差边界）
4. 对冲覆盖（hedge-map）
5. 因子/主题逻辑（须可追溯来源，否则「待验证」）
6. 多情景 / 交易结构（可选，LAW 6a）

---

## 数据引擎

| 函数 | 用途 |
|------|------|
| `query_etf_data(symbol)` | 指数 PE、折溢价、AUM、对冲、flags |
| `query_etf_quote(symbol)` | 现价、涨跌幅、成交 |
| `query_etf_kline(symbol)` | 净值序列、年化波动、MA20/MA60、RSI（含 `rsi_period`） |

对冲表：[references/etf-hedge-map.md](references/etf-hedge-map.md)

### 指数 PE 状态（`index_pe_status`）

| 值 | 含义 |
|----|------|
| `mapped` | 在 CSINDEX_MAP 中，已尝试拉取 csindex PE |
| `not_mapped` | 在对冲表中但无 csindex 码（常见于行业/主题 ETF，如 515790） |
| `unknown_etf` | 不在已知映射表，需手动核实跟踪指数 |

### 自动 flags

- AUM < 2 亿 → ❌ 清盘/流动性风险
- 溢价 > 2% → ⚠️ 买入成本偏高
- 折价 < -2% → ⚠️ 可能存在结构问题
- 对冲 coverage `none` → ⚠️ 无期货/期权对冲

---

## 分析合成（必选三步）

报告按模板撰写完成后，**必须**执行以下三步合成，再发出备忘录。

### 1. 对抗性假设检验

对报告中每个关键假设，找出其**可证伪条件** — 未来什么可观测数据会让这个假设不成立。

格式（报告内表格）：

| 关键假设 | 可证伪条件 | 观测窗口 |
|----------|----------|:---:|
| "Q2 业绩为下方提供支撑" | 核心成分股预告 miss，或「预告好但股价跌」的利好出尽信号 | ~2 周 |
| "MA60 提供技术支撑" | 价格有效跌破 MA60（连续 3 日收盘低于） | 持续观测 |
| ... | ... | ... |

**硬约束**：
- 至少 3 个关键假设，每个必须有可观测的证伪条件
- 不可证伪的假设须标注「不可验证，置信度降级」
- 观测窗口必须是具体时间或事件节点，不能是「待观察」

### 2. 「致命一击」归纳

用一句话回答：**如果这个分析错了，最可能是因为什么？**

格式（报告内独立段落）：
> **1 个月持有的最大风险**：[X 条件]。若 [Y 可观测触发]，当前分析框架的 [Z 方向性判断] 失效。

**硬约束**：
- 必须是条件句（若…则…），不能是模糊的风险罗列
- 指向具体的、可观测的失效条件，不是「市场风险」「政策风险」等标签
- 与对抗性假设检验中风险最高的假设呼应，但不重复

### 3. 盲点检查

Final pass 自问三个问题：

1. 有什么重要变量完全没有被讨论？
2. 当前共识最可能忽略什么风险？
3. 如果一个月后回头看，今天最明显的盲点会是什么？

格式（报告内列表）：
```
🔍 盲点发现:
- [盲点 1 描述] — 当前: [未知/数据不可得/未覆盖]
- [盲点 2 描述] — 当前: [未知/数据不可得/未覆盖]
```

**硬约束**：至少 2 条盲点发现。不能输出空集。无数据的盲点恰是最重要的盲点。

---

## Self-Check

发出备忘录前：

- [ ] 无「建议买入/卖出/持有/加仓/减仓/止损」
- [ ] 无无假设的「目标价 XX」
- [ ] 每个关键数字有来源
- [ ] 用指数 PE，非个股 PE 叙事
- [ ] 首尾有风险声明
- [ ] 对抗性假设检验：≥3 个关键假设有可证伪条件
- [ ] 致命一击：一句话条件式风险归纳，指向可观测失效条件
- [ ] 盲点检查：≥2 条盲点发现

---

## 与其他 Skill 的关系

| Skill | 关系 |
|-------|------|
| **invest-a-journal** | 方案四维评估；ETF 数据经 shim 调用本模块 |
| **invest-a-stock** | 个股深研；本 Skill 不替代。主题逻辑可引用龙头个股报告 |
| **invest-a-gap-scan / limit-up** | 市场扫描；无关 |

---

## 参考

- [references/report-template.md](references/report-template.md)
- [references/etf-hedge-map.md](references/etf-hedge-map.md)
