# invest-skills — 可复核的 A 股研究技能集

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-blue.svg" alt="License: MIT" /></a>
  <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.12+-blue.svg" alt="Python 3.12+" /></a>
  <a href="https://github.com/Veblin/invest-skills/actions/workflows/validate.yml"><img src="https://img.shields.io/github/actions/workflow/status/Veblin/invest-skills/validate.yml?label=validate" alt="Validate" /></a>
  <a href="https://github.com/Veblin/invest-skills/releases"><img src="https://img.shields.io/github/v/release/Veblin/invest-skills?include_prereleases&label=v0.3.1" alt="Release" /></a>
  <a href="#workbuddy"><img src="https://img.shields.io/badge/WorkBuddy-零终端安装-2b7fff.svg" alt="WorkBuddy 零终端安装" /></a>
</p>

![invest-skills：数据形成可追溯、可重算、可复检的证据链](docs/images/readme-candidates/01-evidence-chain.png)

**把 AI 辅助研究做成一条可追溯、可重算、可复检的证据链。** 输入代码，程序采集公开数据并计算指标，AI 根据证据组织分析；冲突与缺口明确保留，交付前接受检查。产出用于个人学习与研究，帮助你核验依据、方法与不确定性。

> **定位声明**：本项目是开源软件工具，不从事证券投资咨询业务，未取得证券投资咨询业务资格；不推荐个股、不提供买卖或仓位建议、不承诺收益。完整边界见[免责声明](#免责声明)。

<details>
<summary><b>English</b></summary>

**invest-skills** provides eight open-source Agent Skills for A-share and Hong Kong stock research, ETFs, market context and screening. It builds a traceable, recalculable and reviewable evidence chain: Python collects data and computes metrics; AI writes evidence-linked analysis; conflicts and missing data remain visible; reports undergo automated checks and human review.

The engine and research snapshots run locally. What is sent to a model service depends on your platform and settings. Research and learning tool; not investment advice.

</details>

## 证据链如何落地

| 能力 | 项目怎样实现 | 你能核验什么 |
|:---|:---|:---|
| **可追溯** | 事实保留来源、日期与口径，分析关联证据；取数失败、跨源冲突与待验证推测显式标注 | 数字来自哪里，判断依据是什么，哪些信息仍有缺口 |
| **可重算** | 衍生指标由 Python 计算，公式与实现可审计；个股固定快照流程封存报告输入，按快照 ID 只读重渲染 | 计算是否成立；重渲染使用同一份数据，便于区分输入变化与分析变化 |
| **可复检** | 个股固定快照流程将分析中的数字事实与封存数据逐项对账；交付前做机器检查，再复检数字、合规与逻辑 | 引用是否与数据一致，来源和风险说明是否齐备，推理是否自洽 |

**可复核不等于数据必然正确。** 上游数据可能延迟、缺失或口径不一致；项目保留核验路径与不确定性，由使用者回到公司公告、交易所和原始资料验证。机器检查通过后仍须人工复检。

引擎与 SQLite 研究快照在本地运行和保存，项目不收集使用遥测；研究内容是否发送至模型服务，取决于你使用的平台及配置。

## 看一份真实产出

[潍柴动力研究备忘录（2026-07-21）](docs/demos/000338-潍柴动力-2026-07-21.md) 中，Tushare 与 AkShare 财务数据差异为 **102.4%**：报告保留差异、标注待核验，并提示回到正式财报验证，没有将冲突包装成确定结论。[来源：历史报告中的跨源差异说明](docs/demos/000338-潍柴动力-2026-07-21.md)

其他样例：[ETF](docs/demos/515050-通信ETF-2026-09-19.md) · [港股](docs/demos/00700-腾讯控股-2026-09-12.md) · [市场脉搏](docs/demos/market-pulse-2026-08-03.md) · [多报告复盘](docs/demos/300308-中际旭创-复盘-2026-08-04.md)。这些均为**历史快照，仅展示格式与方法，不代表当前市场状态，不构成投资建议**。

## 开始使用

完整安装、Token 可选项与故障排查见 [CONFIGURATION.md](CONFIGURATION.md)。

| 平台 | 入口 |
|:---|:---|
| Claude Code | `/plugin marketplace add Veblin/invest-skills`，随后使用对应技能入口 |
| Agent Skills 兼容平台 | `npx skills add Veblin/invest-skills` |
| DSH / Qoder / Trae / Kimi CLI | 克隆仓库，使用内置 `.agents/skills/` 技能链接；详见配置说明 |
| 本地命令行 | 克隆仓库后运行 `uv sync`，再运行 `uv run python skills/invest-a-stock/scripts/invest.py diagnose` |
| Hermes / Gemini CLI | 对应 marketplace 或 extension 的安装方式见配置说明 |

### WorkBuddy

从 [GitHub Release](https://github.com/Veblin/invest-skills/releases) 下载 `invest-skills-wb-vX.Y.Z.zip`，在「专家·技能·连接器 > 技能 > 添加技能 > 上传技能」导入，无需打开终端。Token 配置见 [WorkBuddy 安装说明](docs/workbuddy/)。

Windows 使用 `git clone` 时，若技能链接被物化为文本文件，运行重建脚本：

```powershell
.\scripts\setup_workbuddy_windows.ps1
```

脚本重建 **32 条技能链接**（**24 个目录链接** + **8 个 commands 文件**）。运行 `cmd /c dir .workbuddy\skills`，应显示 `<JUNCTION>`；权限与执行策略问题见配置说明。

## 能做什么

目前公开提供 **8 个技能**，共用数据层与研究规范。

| 技能 | 研究内容 | 调用示例 |
|:---|:---|:---|
| invest-a-stock | 个股公司、财务、估值、资金、技术状态、事件与风险 | `/invest-a-stock 600176` |
| invest-a-etf | 指数估值、折溢价、持仓、跟踪质量与对冲覆盖 | `/invest-a-etf 563300` |
| invest-hk-stock | 港股快照、财务、南向资金与 A/H 比价 | `/invest-hk-stock 00700` |
| invest-a-pulse | 市场杠杆、广度、情绪、资金与估值 | `/invest-a-pulse` |
| invest-a-journal | 既有交易方案的逻辑、盲点与风险收益检查 | `/invest-a-journal` |
| invest-a-discover-scan | 多透镜粗筛研究观察短清单，附理由与下钻入口 | `/invest-a-discover-scan` |
| invest-a-gap-scan | 指数成分股的客观跳空缺口筛查 | `/invest-a-gap-scan` |
| invest-a-pattern-scan | 双底与三角形底检出及数据窥探防护 | `/invest-a-pattern-scan` |

技术指标与扫描结果用于描述市场状态，不生成交易信号或操作建议。

**暂停维护**：`invest-a-event-calendar` 自 2026-10-02 起退出公开入口与分发；源码、离线测试保留，共享解禁数据层继续维护。见[历史说明](skills/invest-a-event-calendar/README.md)。

## 深入了解与开发

- [报告质量规范](skills/lib/references/delivery-qc.md)：机器检查与三层人工复检。
- [个股研究流程](skills/invest-a-stock/SKILL.md) · [命令行参考](docs/cli.md)。
- [架构说明](docs/architecture.md) · [报告生成流程图](docs/diagram/report-generation-flow-v2.svg)。
- [贡献规范](CONTRIBUTORS.md) · [版本记录](CHANGELOG.md)。开发校验：`uv sync && uv run pytest`。

项目按 `SKILL.md` 开放格式分发，核心代码与测试位于 `skills/`，采用 MIT 许可。

## 免责声明

- **工具定位**：仅提供公开数据的采集、计算、整理与学习框架，不提供投资意见，不从事证券投资咨询业务。
- **不构成投资建议**：包括基于明示假设的多情景估值在内，任何输出均不是买卖建议、目标价预测、要约或收益承诺；使用者应独立核验和决策。
- **数据与风险**：公开数据可能延迟、缺失或错误；请以公司公告、交易所和原始资料为准。投资有风险，项目不推荐个股、不提供仓位建议、不承诺收益。

## License

[MIT](LICENSE)
