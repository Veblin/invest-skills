# AGENTS.md — AI 协作规则

> 本文件是**项目长期边界与变更门禁**，跨 harness 适用，所有贡献者（人类和 AI）都应遵守。
> **不在此维护**易变的运行细节——源矩阵、命令清单、报告规则、目录树见：各 `SKILL.md`、`skills/lib/references/`（随包携带）与确定性引擎。
> 约束 1–4 的编号是**代码契约**（多处源码与测试按号引用），不得重排或合并。

---

## 四条硬约束

### 约束 1：禁止荐股

不输出任何形式的"买入/卖出/持有"建议、仓位建议。允许多情景估值参考价，但必须标注假设前提、概率权重，并明确"仅供参考，不构成投资建议"；不允许无假设前提的单一目标价数字。这是法律红线，也是能力边界——LLM 没有资格做投资建议。

> **本机研究清单例外**
> 仅在本机显式开启（内容许可型 `INVEST_ENABLE_*` 门；该门未落地时本条不构成授权）时，
> 允许输出「ETF 研究候选清单」类内容，须带「研究候选，非推荐；不构成投资建议」。
> 仅限本机自用：不得进入任何对外材料（定义见 CLAUDE.md「对外内容红线」），亦不得用作宣传素材；
> 本条不改变本节禁令对例外之外输出的适用。

### 约束 2：LLM 不可作为投资决策的主要信源

LLM 存在幻觉问题——在专业金融领域，这些幻觉极难被非专业人士识别。AI 的输出只能作为"学习材料的整理和解读"，不能作为"投资决策的依据"。关键财务数据必须标注原始来源（财报 PDF / akshare / Tushare），让用户有能力追溯验证。

### 约束 3：所有分析解释必须依赖数据源，引用来源

这是本 Skill 最核心的质量标准。LLM 生成的分析性文本（趋势解读、行业判断、估值讨论）**必须建立在可追溯的数据源之上**，而非 LLM 的"知识记忆"。标准对标学术论文：每个论述要么标注数据来源，要么明确声明为"待验证的推测"。没有数据支撑的分析不输出。

### 约束 4：先服务于自己的学习需求，但以可分发标准设计

项目源于作者自身的 A 股/港股投资学习实践。功能迭代以解决自己遇到的真问题为导向，不追求覆盖所有假想需求。同时，项目按 Agent Skills 开放格式构建，面向 Claude Code / Hermes / WorkBuddy 等多平台分发——配置步骤须有文档、数据源须有 fallback、关键路径须在无 Python 环境（MCP 模式）下同样可用。WorkBuddy 分发通道（3 步安装 / token 配置 / 用户级 AGENTS 与 MEMORY 模板 / 真机验收表）见 [docs/workbuddy/](docs/workbuddy/) 与 README「WorkBuddy 安装」节。

---

## 产品原则

用户是**能自行验证信息、不会被营销话术左右**的聪明人——不需要"简单化"，需要"可验证"与"方法论"。因此产品逻辑是"这是数据，这是分析方法，这是不确定性，你自己判断"，而不是"信任我，我帮你判断"；迭代逻辑是"我在投资学习中遇到了什么问题"，而不是"用户想要什么功能"。项目是开源学习工具，无流量变现/知识付费等商业逻辑。

日常个股研究产出留在本机，不进入公开仓库；对外 demo 须经脱敏与内容审查，具体发布规则见 [CLAUDE.md「对外内容红线」](CLAUDE.md)。

## 技术指标规范

MA5/MA10/MA20/MA60 和 MACD（DIF/DEA）等指标**仅用于理解市场状态**，不用于生成交易信号：可描述价格与均线的位置关系、MACD 的 DIF/DEA 位置与方向、市场参与者的共识趋势；**禁止**输出"金叉买入""死叉卖出""MACD 底背离抄底"等信号，或基于技术指标给出任何操作建议。学术依据与模块呈现细则见 [modules.md](skills/invest-a-stock/references/modules.md)。

## 报告质量门（全 skill 适用）

- **首部有风险声明 / 尾部有风险声明**——两条都须**完整可见**；首部 boilerplate 不得以"置尾"为由删去
- 每个财务数字带 `[来源: ...]`；推测性语句标 `[推测，待验证]`；不可得维度标 `⚠️` + `attempted_sources`
- 无"买入/卖出/建仓"等词；估值参考价必须是带假设前提的多情景表述，而非单一目标价数字
- 技术指标仅为状态描述，无交易信号
- **交付前必做**：机器准出 + 三层人工复检（数字/合规/逻辑），见 [delivery-qc.md](skills/lib/references/delivery-qc.md)；内容逐项自查见 [report-conventions.md §7](skills/lib/references/report-conventions.md)

> 完整的报告规则**不再本文件维护**——唯一详细定义在 `skills/lib/references/`（随包携带，独立包亦可读）。本文件只保留**代码按名引用的门槛项**。

## 数据源策略

**不在此维护静态源链**（源随版本漂移，且各维度链不同）。原则：

- 源策略按**维度**定义在确定性 collector 与各 Skill `references`；接口与源的实际清单见 [data-interface-map.md](skills/lib/references/data-interface-map.md)
- **降级必须透明**：失败记录 `attempted_sources` 与原因，不阻塞其余维度；全失败标注"未获取到任何有效数据"
- 代理与 stderr 约定见 [data-interface-map.md §G](skills/lib/references/data-interface-map.md)

## 扩张门禁（新增能力前必读）

**AI 不自行新增 Skill、模式或常驻扫描；先尝试改现有入口。** 新增公开 Skill、CLI 一级命令、默认报告栏目、强制数据源或 Agent 步骤前，须提交一页变更卡：

1. 最近**真实任务**与缺口证据（不是"以后可能有用"）
2. 现有入口/数据层为何不能承载
3. 本次新增的入口、依赖、联网请求、报告产物与分发包变化
4. 对主链耗时与 QC 的影响
5. 被替换/退出的旧路径（或无法替换的理由）
6. 两个真实样例与回退条件

证据未齐时**允许内部按需实验**，但不进入默认路径与公开分发。每次新增入口都设反馈窗口：没有真实调用或复盘结论就撤出默认入口/公开目录，窗口结束必须裁决——**不以"暂留以后可能用"为默认结论**。

## 发布与版本

- **版本号 canonical**：`pyproject.toml` `[project].version`（唯一手动维护处）；`scripts/bump-version.sh X.Y.Z` 同步全部派生文件
- 校验 `uv run python scripts/sync_version.py check`（CI / pre-commit 已接入）
- **发布前检查清单**与分发包维护步骤见 [CONTRIBUTORS.md「Cutting a release」](CONTRIBUTORS.md)（不再复制到本文件）

## 跨 Harness 兼容

- 不硬编码任何特定 harness 的路径（如 `~/.claude/`）
- `SKILL.md` 中不假定用户使用特定运行时
- 配置文档（`CONFIGURATION.md`）应列出各 harness 的安装方式
- 引擎脚本仅依赖标准库和 `pyproject.toml` 中声明的依赖
- **结果等价**：同一输入在不同 harness 下应产出等价结果；能力缺失须如实声明，不以文档承诺代替运行验证

## 项目结构（要点，非完整树）

```
code/skills/<skill>/   SKILL.md + scripts/ + references/ + tests/（各 skill 自身布局为准）
code/skills/lib/       跨 skill 共享实现 + references/（随包携带的共享规范）
code/scripts/          构建器与发布脚本（skillhub / WorkBuddy）
```

- **三个研究核心入口**：`invest-a-stock`、`invest-a-etf`、`invest-hk-stock`
- **按需专项入口**：`invest-a-journal`、`invest-a-event-calendar`、`invest-a-gap-scan`、`invest-a-pattern-scan`、`invest-a-pulse`、`invest-a-discover-scan`
- 各 harness 安装方式见 [CONFIGURATION.md](CONFIGURATION.md)；文档索引见 [docs/README.md](docs/README.md)
- **不要从 `archive/` 导入任何模块**（v0.2 遗留）
