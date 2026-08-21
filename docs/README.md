# invest-skills 文档

面向使用者和贡献者的**精简文档索引**。详细运行时规格见仓库根目录与各 Skill 文件。

> **命名**：用户 slash 一律 `/invest-a-*`（连字符）。Claude 插件 marketplace 包名可为 `invest:a-*`（冒号），二者分层。

---

## 用户文档（仓库根目录）

| 文档 | 内容 |
|------|------|
| [README.md](../README.md) | 安装、快速开始、数据采集维度 |
| [CONFIGURATION.md](../CONFIGURATION.md) | API Key、Tushare 积分对照、CLI 参数 |
| [CHANGELOG.md](../CHANGELOG.md) | 版本变更记录 |
| [CONTRIBUTORS.md](../CONTRIBUTORS.md) | 贡献指南 |
| [AGENTS.md](../AGENTS.md) | AI 协作规则与设计约束 |

## 示例报告（docs/demos/）

真实运行产出，类型覆盖个股深度/复盘/ETF/市场情绪：

| Demo | 类型 | 来源 skill |
|------|------|-----------|
| [000338-潍柴动力-2026-07-21](demos/000338-潍柴动力-2026-07-21.md) | 个股九模块研究备忘录 | invest-a-stock |
| [300308-中际旭创-复盘-2026-08-04](demos/300308-中际旭创-复盘-2026-08-04.md) | 多报告对比复盘 | invest-a-stock（复盘） |
| [588000-科创50ETF-2026-08-08](demos/588000-科创50ETF-2026-08-08.md) | ETF 研究备忘录 | invest-a-etf |
| [market-pulse-2026-08-03](demos/market-pulse-2026-08-03.md) | 市场情绪脉搏 | invest-a-pulse |

> 精选示例，随版本维护；日常运行产出的全部报告保存在本地 `reports/`（不纳入版本库）。

## invest-a-stock 运行时规格

| 文档 | 内容 |
|------|------|
| [SKILL.md](../skills/invest-a-stock/SKILL.md) | LAWs、路由表、CLI（**canonical 核心**） |
| [modules.md](../skills/invest-a-stock/references/modules.md) | 九模块与八段 legacy 结构 |
| [financials.md](../skills/invest-a-stock/references/financials.md) | 财报深研专项（F-1~F-4） |
| [sentiment.md](../skills/invest-a-stock/references/sentiment.md) | 舆情深研专项（L1~L3） |
| [game-theory.md](../skills/invest-a-stock/references/game-theory.md) | 参与者行为扫描专项 |
| [references-format.md](../skills/invest-a-stock/references/references-format.md) | 引用来源表规范 |
| [source-guide.md](../skills/invest-a-stock/references/source-guide.md) | 数据源优先级、代理说明 |

## 架构与实现

| 文档 | 内容 |
|------|------|
| [architecture.md](architecture.md) | 已完成功能与实现逻辑总览（三层架构、6 skills、多源降级链、渲染管线、版本演进、工程设施） |

## 路线图

| 文档 | 内容 |
|------|------|
| [roadmap.md](roadmap.md) | 计划接入的数据源与版本方向 |

## 回测与基线数据（docs/data/）

事件研究回测的结果存档（JSON，随版本库发布）；生成脚本已归档至 `scripts/archive/`（登记表见其 README）：

| 数据文件 | 生成脚本 | 说明 |
|---------|---------|------|
| `F1/F2/F3_backtest_result.json` | `scripts/archive/backtest_futures.py`（+ `backfill_futures_daily.py` 回填数据） | 期货基差事件研究（F 系列） |
| `H1/H2/H3/H4/H6_backtest_result.json` | `scripts/archive/backtest_h1~h6.py` | 见底/低吸/金价 beta/缺口/日历事件研究（H 系列） |
| `H5_backtest_result.json` | 无对应脚本（v0.2.6 起仅入库恢复可复现引用，见 CHANGELOG） | H5 回测结果存档 |
| `scenario_baselines_E002_E007.json` | `scripts/archive/scenario_baselines.py` | E-002~E-007 预案基线 |
| `pattern_scan_result.json` | invest-a-pattern-scan skill（非 archive 脚本） | 形态扫描结果存档 |

各回测的假设冻结与预注册见 [architecture.md §references 的 backtest_prereg 条目](architecture.md)。

---

> 内部迭代蓝图、执行计划、评审稿等存放在本地 `host-docs/`（不纳入版本库），避免对外文档冗长。
