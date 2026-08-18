# SOP-DEEP 四视角并行（--deep 完整报告专用）

> 从 invest-a-stock SKILL.md 拆出（上下文精简）。仅 `report --deep` 时由主编 Claude 加载本文件；非 deep 流程不读。

完整 `--deep` 报告时分两阶段并行：**采集（3 Agent）→ 分析（4 Agent）**。

---

**Phase 1 — 并行采集 + 交叉验证（3 Agent 同时启动）：**

```
┌─ Collector A: 财务主线（Tushare）─────────────────────┐
│  invest.py collect SYMBOL --deep                       │
│    --dims "financials,valuation,basic_info"            │
│  产出: /tmp/{symbol}_collect_A.json                    │
└───────────────────────────────────────────────────────┘

┌─ Collector B: 行情+资金+财务交叉验证（akshare主）─────┐
│  invest.py collect SYMBOL --deep                       │
│    --dims "financials,quote,kline,valuation"           │
│  financials 用 akshare 做主源，与 A 的 Tushare 交叉    │
│  产出: /tmp/{symbol}_collect_B.json                    │
└───────────────────────────────────────────────────────┘

┌─ Collector C: 补充数据（股东/研报/事件/行业）─────────┐
│  invest.py collect SYMBOL --deep                       │
│    --dims "shareholders,research,events"               │
│  产出: /tmp/{symbol}_collect_C.json                    │
└───────────────────────────────────────────────────────┘
```

**验证规则：**
- financials 维度：A（Tushare）vs B（akshare），关键字段（ROE/EPS/毛利率）差异 <5% → 通过
- 差异 ≥5% → 触发 Tie-breaker（第三源三方投票）：另采一份第三方 collection
  `invest.py collect SYMBOL --dims "financials" --deep`（换批次/时点），
  与 A/B 一起跑 `skills/invest-a-stock/scripts/merge_collections.py`
- 三取二投票决定最终值，无法决定则保留差异并标注"跨源分歧"
- 合并 3-4 份 JSON → 完整 collection（用 `skills/invest-a-stock/scripts/merge_collections.py`）

**Phase 1 耗时：** 3 Agent 并行 ≈ 30-40s（vs 串行 80s）

---

**Phase 2 — 四视角并行分析（4 Agent 同时启动）：**

```
同时启动 4 个 Agent（用 references/agent-prompts.md 的模板，替换变量）：
  Agent A: 生意质量 → section_1_business.md
  Agent B: 财务与估值 → section_2_financials.md
  Agent C: 行业与竞争 → section_3_industry.md
  Agent D: 风险与治理 → section_4_risk.md

每个 Agent 的参数: {collection_json_path} = 合并后的 JSON 路径,
  {symbol} = 标的代码, {output_dir} = reports/{symbol}-{name}/
```

---

**Phase 3 — 合成（主编 Claude）：**
1. 等待 4 个分析 Agent 全部完成
2. 读取 4 个 section 文件 + 合并后的 collection JSON
3. 运行 `valuation_calc.py SYMBOL` 嵌入估值数据（DCF/多情景/预期差）
4. 合成完整报告 → `reports/{symbol}-{name}/{YYYY-MM-DD-HH-MM-SS}.md`，采用以下 LAW 17 金字塔结构。
> **模板使用说明：** 以下 `##` 标题为结构占位符，实际输出时须替换为传递信息量的完整判断句（LAW 17）。段首必须加粗主旨句。

```markdown
# {name} ({symbol}) — 深度研究备忘录 {date}

## 核心结论
（从 4 Agent section 提炼，≤5 句，每条携带数据+逻辑链）
[证据强度]

## 位置感
周期位置 / 估值位置 / 市场态度 — 三句话定位当前状态

## 模块结论速览
| 维度 | 结论 | 关键数据 | 逻辑链 | 置信度 |
|------|------|---------|--------|:------:|

## 论证展开
（每节标题为完整判断句，段首加粗主旨句，数据→推理→判断一行闭环）

## 多情景参考
（表格：情景 | 假设 | 传导 | 估值区间 | 概率）

## 观察节点
（表格：时间 | 事件 | 验证什么 | 如何修正判断）

## 主要风险
（3-5 条，标注严重度 + 缓解因素）

## 致命风险
> 若 [X 可观测条件] 发生，当前核心判断 [Y] 失效。

## 对抗性假设检验
| 关键假设 | 可证伪条件 | 观测窗口 | 状态 |
|----------|----------|:---:|:---:|
| ... | ... | ... | ✅/⚠️/❓ |

## 盲点扫描
🔍 盲点发现:
- [盲点 1] — 当前: [未知/数据不可得/未覆盖]
- [盲点 2] — 当前: [未知/数据不可得/未覆盖]

## References
（保持现有 LAW 7 格式）
```

3b. 执行分析合成三步（见 SKILL.md「分析合成三步」章节）：
   - 对抗性假设检验：从 4 Agent section 提炼 ≥5 个关键假设，逐一找可证伪条件
   - 致命一击：一句话条件句归纳最大风险
   - 盲点扫描：检查四视角是否有遗漏维度

5. 输出 Claude 对话简报（按第一层模板，一屏内）
6. QC 自检：LAW 1-17 逐条验证 + 分析合成三步完整性（对抗性假设检验 ≥5 假设、致命一击条件句、盲点 ≥2 条），尤其 LAW 17（标题是否传递信息量、段首是否有主旨句、简报首屏是否有核心结论）

---

四视角覆盖内容：

1. **生意质量**：商业模式 / 护城河 / 管理层 / 价值链
2. **财务与估值**：DCF 三情景 / 财务健康 / 盈利质量 / 估值位置
3. **行业与竞争**：波特五力 / 竞争格局 / 产业链利润池
4. **风险与治理**：快速否决 / 风险信号 / 公司治理 / Known Unknowns

> Agent prompt 模板详见 [references/agent-prompts.md](references/agent-prompts.md)。
> 采集/分析阶段的所有 Agent 只调 Bash（invest.py / skills/invest-a-stock/scripts/merge_collections.py），不调 Tushare/akshare API — 不触发限流。
