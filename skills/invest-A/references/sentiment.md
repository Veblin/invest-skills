# 舆情深度研究专项

> 受 [SKILL.md](../SKILL.md) LAW 1–16 约束。`plan --intent sentiment_deep` 时加载本文件。

## 三层舆情（勿与 Template C 混淆）

| 层级 | 名称 | 数据源 | 引擎/采集 |
|------|------|--------|-----------|
| L1 | 卖方研报情绪 | `collect.research` | `SentimentCard`（代码类名不变；文档称「研报情绪卡」） |
| L2 | 公告/事件情绪 | `events`（`attach_events` 自动） | `EventClassificationCard` |
| L3 | 公开舆情 | WebSearch / Tavily | Claude 执行，须 query + 来源标注 |

**L1 ≠ 社媒舆情**：`SentimentCard` 汇总卖方 EPS 一致预期与评级分布，不是雪球/股吧情绪。

## 采集工作流

```bash
uv run python skills/invest-A/scripts/invest.py plan 600176 --intent sentiment_deep > /tmp/plan.json
uv run python skills/invest-A/scripts/invest.py collect 600176 --plan /tmp/plan.json --deep
uv run python skills/invest-A/scripts/invest.py evidence 600176 --plan /tmp/plan.json
```

`--deep` 确保 `research` 与 `industry` 维度纳入采集。

采集维度：`quote`、`research`、`basic_info`、`industry`（deep）、`kline`、`northbound`（资金情绪交叉）。

## L3 WebSearch SOP

### Query 模板

```
"{公司全称} {代码} 投资者关系 互动易 2026"
"{公司全称} 舆情 公告 澄清"
"{公司全称} {行业关键词} 政策 影响"
```

### 可信度标注（见 [source-guide.md](source-guide.md)）

- 🔵 official：cninfo、互动易、交易所
- 🟡 analyst：证券时报、财联社、券商媒体
- 🔴 rumor：股吧、未署名传闻 → 须标 [推测，待验证]
- ⚪ unknown：无法判断来源

每条 L3 结论格式：

```
[事实] {摘录或摘要} [来源: WebSearch / query: "..." / {日期}]
[分析] {语气与股价传导的讨论} [证据强度: ...]
```

## 情绪仪表盘输出格式（Claude 填写）

简报须含：

1. **事件时间线**（L2）：近 30 日公告分类摘要 + 方向提示（非买卖建议）
2. **研报情绪**（L1）：评级分布 + EPS 一致预期区间（引用引擎卡片）
3. **公开舆情信号**（L3）：2–3 条高可信来源 + 待验证传闻（如有）
4. **语气信号槽位**：`[待 Claude 标注：正面/中性/负面/混合 + 依据]`

不做 NLP 自动情感打分；所有判断须可追溯。

## 与九模块关系

- 模块 3b（机构观点）= L1
- 模块 3 行业情绪 ≠ L3 社媒舆情
- 完整 `report --deep` 时仍加载本文件以补充 L3

## 不可得处理（LAW 5）

L3 全部检索失败时：

> 「未获取到任何有效数据，无法判断公开舆情方向。[尝试了 WebSearch query: ...，均无有效结果]」
