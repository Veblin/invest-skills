# CLAUDE.md — invest-skills 仓库开发说明

> 本文件是**仓库内 Claude 的开发操作说明**：本地命令、排障、开发约定、公开发布细则。
> - 跨平台**长期边界与变更门禁**（四条硬约束、扩张门禁）见 [AGENTS.md](AGENTS.md)
> - **运行时规则**（报告质量、数字纪律、复检流程、措辞/分位）的**唯一详细定义**在随包规范 [`skills/lib/references/`](skills/lib/references/)——本文件只留链接与本地命令例子，不复制条文

## 底座准则（复杂度控制）

本仓库是多个下游项目的底座（skillhub 镜像、WorkBuddy 分发、跨 harness 兼容等均依赖其既有约定）。因此：

- **兼容性不可轻动**：frontmatter 格式、SKILL.md 结构、多 harness 兼容性是对下游项目的承诺，任何改动须先评估对全部下游项目的影响
- **目录结构不可轻动**：`skills/` 布局与路径约定是既有契约
- **复杂度受控**：新能力优先落在下游项目（如 invest-skills-skillhub），底座只收共享核心；改动从简，不叠架构
- **替换优先**：新增 Skill / 一级命令 / 默认报告栏目 / 强制数据源或 Agent 步骤前，按 [AGENTS.md](AGENTS.md)「扩张门禁」先交变更卡

## 运行命令（本仓库本地约定）

```bash
# 本仓库 Python CLI 一律 uv run python，确保从项目根 .venv 加载依赖
uv run python skills/invest-a-stock/scripts/invest.py <subcommand> <symbol> [--flags]
```

> 该约定**只约束本仓库 Python CLI 的本地运行方式**；MCP / 无 Python 的分发路径（WorkBuddy 等）另有执行方式（随包 `bootstrap.sh` 建包根 `.venv`），不被此句排除。完整理由与 `pip` 禁令见 [development-rules.md D14](skills/lib/references/development-rules.md)。

常用子命令（完整清单以 `--help` 为准）：

```bash
diagnose     # 检查数据源可用性
collect      # 采集数据（--with-news-pack 新闻三层架构）
report       # 生成报告
validate-analysis  # 校验分析 JSON 槽位（--draft 同时检查首版 MD 占位）
compare      # 双标的对比
diff         # 对比两次快照
store list   # 历史采集记录
rigor        # 财务验算（市值/估值/跨源）
audit        # 报告审计 extract/verdict
check        # 单标的质地检查（7 指标）
portfolio    # 组合风险特征
thesis       # 投资假设追踪
shock        # 价格冲击插值比例
notice-body  # 取公告正文（art_code 或详情页 url；原文不改写 + 截断三态，不做结构化抽取）
```

## 运行时规则的落点（索引）

**规则条文不在此维护**——按下表查随包规范（SkillHub / WorkBuddy / release 三种分发都携带）：

| 规则 | 唯一详细定义 |
|------|------|
| P0 数字铁律、引擎 `derived` 字段清单 | [delivery-qc.md §1](skills/lib/references/delivery-qc.md) |
| 报告复检流程（第 0 层机器准出 + 三层人工复检） | [delivery-qc.md §2–§6](skills/lib/references/delivery-qc.md) |
| 措辞规范 / 已知违规模式 / 事实边界 | [report-conventions.md §2–§3](skills/lib/references/report-conventions.md) |
| 点位引用证据等级（L1–L4） | report-conventions.md §2.4 |
| 分析标记（[事实]/[分析] + SOP-EV 四维） | report-conventions.md §5 |
| 多情景参考与交易结构分析 | report-conventions.md §6 |
| 宏观情景（指标清单 + 两段式输出合同） | report-conventions.md §9.1 |
| 估值分位使用规则（含亏损期判据口径） | report-conventions.md §9.2 |
| 开发规范 D1–D14（`/code-review` 审查标准） | [development-rules.md](skills/lib/references/development-rules.md) |
| 开发执行、角色交接与目标验收 | [development-workflow.md](docs/development-workflow.md)（开发流程，非报告运行时规则） |
| 源策略原则、代理与 stderr 过滤 | [data-interface-map.md §G](skills/lib/references/data-interface-map.md) |
| 九模块结构、技术指标规范 | [modules.md](skills/invest-a-stock/references/modules.md) |

## 对外内容红线（公开渠道）

> 适用于一切**面向不特定对象**的公开发布：GitHub 仓库与 README、`docs/demos/`、技术社区文章、分发平台描述与截图。
> 与 A1（原 LAW 6）互补——**A1 管报告写什么，本节管什么可以公开发**。合规判断的关键不是「免费」，而是「是否面向不特定对象发布个股倾向性内容」。

| 红线 | 规则 |
|------|------|
| **个股产出不进公开仓库** | 日常研究产出一律留在本地 `reports/`（已 gitignore）。公开仓库只保留经脱敏审查的 demo，新增 demo 须走本节检查 |
| **公开 demo 三项强制** | ① 首行头部免责声明 ② 无方向性倾向表述（「一致看多」「强烈信号」「安全边际吃尽」等）③ 明示为历史快照、不代表当前状况 |
| **对外文章只讲方法论** | 技术社区/周刊/分发平台的宣传素材只讲工程实现与方法论（P0 计算铁律、三层复检、多源降级链），**不以个股分析作为宣传素材** |
| **不导流、不变现个股观点** | 「间接有偿」会使「免费」这道防线失效：禁止开户导流/返佣、付费荐股、打赏换观点。**任何变现方案启动前须重做合规评估** |

变更本节前须重新评估合规影响。背景分析见 `host-docs/v0.2.7/directions-research-2026-08-18.md` §5.4。

## 版本规则与同步

- 最多三位：`v{major}.{minor}.{patch}`；小幅迭代递增末位；同版本内多次修订用日期区分，不自创四位版本号
- Git 分支：`feat/v{version}`；运行 bump 后务必重命名分支（如 `feat/v0.1.5` → `feat/v0.1.6`）
- **canonical 源**：`pyproject.toml` 的 `[project].version`（运行时经 `version.py` 读取）。**禁止手动改多处版本号**

```bash
bash scripts/bump-version.sh X.Y.Z   # 或: uv run python scripts/sync_version.py bump X.Y.Z
uv run python scripts/sync_version.py check
```

版本一致性仅在发布/CI 时校验（`sync_version.py check`），**不在 Skill 运行时或 SessionStart 钩子中执行**。发布前检查清单见 [CONTRIBUTORS.md](CONTRIBUTORS.md)。

## 关键架构

- **多源降级链（R12h）**：L3 行情类（kline/quote/basic_info/shareholders/northbound）经 `_run_sources_cascade` 首选源单发、失败按序降级（防东财限流/首选源挂死）；L2 财务类（financials/valuation）经 `_run_sources_parallel` 并行双源先到先用；industry/holder_changes 互补数据并行
- **所有源独立记录**：失败不阻塞，全失败标注"未获取到任何有效数据"
- **合规边界**：禁止买卖建议、仓位建议（A1／原 LAW 6）；允许多情景估值参考价（须假设前提 + 概率权重 + "仅供参考，不构成投资建议"），**不允许无假设前提的单一目标价数字**；允许「交易结构分析」（A2／原 LAW 6a）——条件性估值区间 ≠ 买入建议，详见 [trade-structure.md](skills/invest-a-stock/references/trade-structure.md)
- **archive/ 目录**是 v0.2 遗留，不要引用

## 报告路径

- 数据源扩展方案：`reports/A股数据源扩展研究报告_v0.1.3.md`
- 个股报告：`reports/{symbol}-{name}/{YYYY-MM-DD-HH-MM-SS}.md`
- 财报 F 规范：`skills/invest-a-stock/references/financials.md`
- 九模块结构：`skills/invest-a-stock/references/modules.md`

## 排障速查

| 症状 | 处置 |
|------|------|
| 东财 API 连接被重置 / ProxyError | 需**直连**：Clash/VPN 下配 `DOMAIN-SUFFIX,eastmoney.com,DIRECT`。详见 [CONFIGURATION.md](CONFIGURATION.md)「代理与东方财富」 |
| akshare 输出混入进度条 | 进度条在 **stderr**，直接 `2>/dev/null` 丢弃；需看错误时用 `grep -vE '^[0-9]+%\|'`。详见 [data-interface-map.md §G](skills/lib/references/data-interface-map.md) |
| `import` 失败 / 依赖缺失 | 用 `uv sync`（**不要** `pip install`，会污染 Homebrew 全局 Python）；见 [development-rules.md D14](skills/lib/references/development-rules.md) |
| 报告被 QC 拦下 | 先跑 `uv run python skills/lib/report_qc.py <报告> --fail-on error`，退出码 2 即不得交付；流程见 [delivery-qc.md](skills/lib/references/delivery-qc.md) |
