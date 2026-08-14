# 筹码出清度 — 四信号规格与使用纪律

> 版本：v0.2.6
>
> 「筹码出清度」是市场层面的**状态描述**（客观报告出清进度），非择时信号。
> 本文档为 canonical 规格：四信号计算口径、实证支撑、使用边界与输出模板。
> **通篇标注**：从业者惯例成分（换手阈值、磨底时长）非学术验证；输出只描述市场客观状态，
> 不构成任何动作依据；入场时机决策由用户依据自身纪律作出。

## 1. 定位

- 只描述**市场是什么样**：去杠杆进行到哪一步、换手处于什么温度、割肉盘释放了多少、
  是否出现企稳确认——不描述「你应该做什么」（与 pulse「研究工具，非择时工具」定位一致）
- 学术支持集中在指数/聚合层面；带血筹码逻辑用于宽基/ETF 判断的可靠性远高于个股
- 从业者惯例成分在报告中必须标注「从业者惯例，非学术验证」

## 2. 四信号规格

| # | 信号 | 计算口径 | 数据来源 | 实证锚 |
|---|------|---------|---------|--------|
| ① | 去杠杆幅度 | 融资余额距近 120 日峰值回撤 %：`(margin_peak - margin_now) / margin_peak * 100` | `market_snapshots.margin_balance`（全市场口径）；历史 <20 行降级 akshare `stock_margin_sse`（SSE 口径，calc_notes 注明） | Bian et al. 2018（强平超跌修复）；Deuskar et al. 2020（杠杆高位负收益） |
| ② | 换手温度 | 全市场成交额 60 日分位：`percentile_rank_inclusive(近60日窗口, 今日)` | `market_snapshots.total_turnover`（⚠️ 深交所口径，决策 D3-1） | Lee & Swaminathan 2000（长周期高换手 → 低未来收益）——**方法论借用**，见 §3.1 |
| ③ | 割肉盘代理 | 近 30 日「`ad_ratio < 1.0` 且成交额 ≥ 同窗口中位数」日计数；跌停 20 日分位复用 snap 已有值或按 `_compute_tier2` 步骤 11 逻辑计算 | `ad_ratio` / `total_turnover` / `limit_down_count`（历史快照，剔除今日行防双计） | Campbell-Grossman-Wang 1993（放量下跌后反转）；Desmond 2002（恐慌放量日普遍存在） |
| ④ | 磨底时长 + 企稳确认 | `days_since_margin_peak` = 距最近一次 margin 峰值天数（剔今日历史，取末次峰值，最新一行 = 0）；`confirmation` = 最近 5 日存在 `ad_ratio ≥ 2.0` 且成交额 ≥ 30 日中位数的放量上涨日 | 历史快照时间戳（窗口边界：第 6 日不算） | Lunde & Timmermann 2004（熊市正时长依赖）；Desmond 2002（须恐慌买入日确认）；Zeng & Bec 2015（反弹幅度与深度相关） |

### 阶段判定（状态描述，无动作词）

- 关键数据全缺失 → `"数据不足"`
- `confirmation=True` → `"企稳确认"`
- `margin_20d_change < -1`，或 margin 低于峰值且近 5 日未回升 → `"去杠杆中"`
- 其余 → `"磨底中"`

**不落库**（避免 schema 迁移，登记 v0.3.0 候选）。

## 3. 方法学边界

### 3.1 信号②换手温度 — 方法论借用标注（必读）

Lee & Swaminathan 2000 是个股**横截面、长周期**研究，本处转用于**全市场成交额分位**
这一市场层面状态信号，属**方法论借用**而非该文献原始验证场景。
须与 Gervais et al. 2001 的**短期**关注效应区分时间尺度：Gervais 2001 显示短期异常放量后
个股倾向下跌，Lee & Swaminathan 2000 显示长周期高换手对应低未来收益——两方向相反，
引用时必须区分时间尺度，禁止混用。

### 3.2 信号④企稳确认 — 从业者惯例代理

企稳确认采用**从业者惯例代理**：`ad_ratio ≥ 2.0` 且放量上涨日（5 日窗口），
非严格 90% Upside Day（Desmond 2002 精神）。严格口径需精确日内数据，
A 股数据源可行性存疑（决策 D3-3）。calc_notes 须标注
「从业者惯例代理，非 90% Upside Day 精确口径」。

## 4. 使用纪律（6 条）

1. **禁「放量即买」**：短期放量与后续收益方向相反（Gervais et al. 2001 短期关注效应与中期收益方向相反），放量下跌只描述恐慌，不构成买入信号
2. **禁单边等割肉盘**：Desmond 2002 表明恐慌日不足以致底——恐慌日必须配合恐慌买入日确认（企稳确认环节），不可只等割肉盘
3. **个股价值陷阱风险标注**：深度下跌个股多数不恢复（De Bondt & Thaler 1985）；带血筹码逻辑用于宽基/ETF 判断的可靠性远高于个股
4. **阴跌熊市失效预警**：缩量阴跌中信号②换手温度可能失真，应标注「信号不适用」而非硬编
5. **缩量磨底只作状态描述**：不构成任何动作依据，不预测方向
6. **「磨得久→涨得多」无文献支持**：反弹幅度与深度成比例而非时长（Zeng & Bec 2015 摘要未载明，**待核，非定论**；见 verification-report.md §4 待核清单第 4 条）

## 5. 输出模板（canonical，与 pulse 输出模板一致）

```markdown
## 🩸 筹码出清度（状态描述，非择时信号）

**[事实]**
- 阶段判定：{stage — 数据不足 / 去杠杆中 / 磨底中 / 企稳确认} [来源: compute_chip_clearance.stage]
- 去杠杆幅度：融资余额距近 120 日峰值回撤 {deleveraging_pct}% [来源: compute_chip_clearance.signals.deleveraging_pct]
- 换手温度：成交额 60 日分位 {turnover_60d_pct}%（缺失时标注原因） [来源: compute_chip_clearance.signals.turnover_60d_pct]
- 割肉盘代理：近 30 日放量下跌日 {down_volume_days_30d} 日 | 跌停 20 日分位 {limit_down_20d_pct}% [来源: compute_chip_clearance.signals]
- 磨底时长：距杠杆峰值 {days_since_margin_peak} 个交易日 [来源: compute_chip_clearance.signals.days_since_margin_peak]
- 企稳确认：{confirmation — True / False / None} [来源: compute_chip_clearance.signals.confirmation]
- 引擎标注：{calc_notes 关键项 — 降级口径 / 数据不足} [来源: compute_chip_clearance.calc_notes]

**[分析]**
出清阶段定位（描述性，非预测）：{去杠杆中 / 磨底中 / 企稳确认；四信号间关系与背离；确认字段缺席（None/False）时的含义；证据强度标注学术支持成分（杠杆/恐慌反转）vs 从业者惯例成分（换手阈值/磨底时长）}
[证据强度: ...]
```

## 6. 合规边界

- 本指标集输出**出清状态描述**（如「去杠杆 -18%、成交额 60 日分位 32%、企稳确认未出现」），
  不输出「现在该买/该等」结论
- 从业者惯例成分（换手阈值、磨底时长与行情大小）在报告中必须标注「从业者惯例，非学术验证」
- 与现有 pulse 定位一致：研究工具，非择时工具；无买卖建议

## 7. 参考

- `skills/invest-a-journal/scripts/lib/market_microstructure.py`：`compute_chip_clearance()` 引擎实现
- `host-docs/v0.2.5/deep-research/blood-chips-crowding.md`：四信号设计依据（实证锚 + 使用纪律来源）
- `host-docs/v0.2.5/deep-research/verification-report.md` §4：待核清单（Zeng & Bec 2015 摘要未载明）
- `host-docs/v0.2.5/execution-plan.md` §D3：本规格来源
