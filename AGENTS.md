# AGENTS.md — AI 协作规则

> 本文档定义 AI Agent 在本项目中的行为边界、设计哲学和质量标准。
> 所有贡献者（人类和 AI）都应遵守这些规则。

---

## 五条硬约束

### 约束 1：禁止荐股

不输出任何形式的"买入/卖出/持有"建议、仓位建议。允许多情景估值参考价，但必须标注假设前提、概率权重，并明确"仅供参考，不构成投资建议"；不允许无假设前提的单一目标价数字。这是法律红线，也是能力边界——LLM 没有资格做投资建议。

### 约束 2：LLM 不可作为投资决策的主要信源

LLM 存在幻觉问题——在专业金融领域，这些幻觉极难被非专业人士识别。AI 的输出只能作为"学习材料的整理和解读"，不能作为"投资决策的依据"。关键财务数据必须标注原始来源（财报 PDF / akshare / Tushare），让用户有能力追溯验证。

### 约束 3：所有分析解释必须依赖数据源，引用来源

这是本 Skill 最核心的质量标准。LLM 生成的分析性文本（趋势解读、行业判断、估值讨论）**必须建立在可追溯的数据源之上**，而非 LLM 的"知识记忆"。标准对标学术论文：每个论述要么标注数据来源，要么明确声明为"待验证的推测"。没有数据支撑的分析不输出。

### 约束 4：项目文档不提供社交功能入口

不建群、不设讨论区、不做用户间互动。项目是个人学习工具，开源分享。

### 约束 5：先服务于自己的学习需求，但以可分发标准设计

项目源于作者自身的 A 股/港股投资学习实践。功能迭代以解决自己遇到的真问题为导向，不追求覆盖所有假想需求。同时，项目按 Agent Skills 开放格式构建，面向 Claude Code / Hermes 等多平台分发——配置步骤须有文档、数据源须有 fallback、关键路径须在无 Python 环境（MCP 模式）下同样可用。

---

## 目标用户画像

用户画像：能够在 Claude Code 或 Hermes 中安装并使用 Skills 的用户，普遍具备较强的信息获取和自主判断能力——学习能力强、能自行验证信息、不会被营销话术左右。

这意味着：
- **不需要"简单化"**：可以用专业术语，可以展示复杂逻辑
- **需要"可验证"**：每个结论都要追溯到数据源头，让用户独立判断
- **需要"方法论"**：用户要的是分析框架和思考工具，不是结论
- **不存在商业变现动机**：项目是开源学习工具，没有"流量变现"/"知识付费"等商业逻辑

---

## 设计哲学

```
用户能力模型：
  不是"需要被告知该做什么的小白"
  而是"想要理解事情如何运作的聪明人"

产品逻辑：
  不是"信任我，我帮你判断"
  而是"这是数据，这是分析方法，这是不确定性，你自己判断"

迭代逻辑：
  不是"用户想要什么功能"
  而是"我在投资学习中遇到了什么问题，需要一个工具来解决"
```

---

## 技术指标规范

MA5/MA10/MA20/MA60 和 MACD（DIF/DEA）等指标**仅用于理解市场状态**，不用于生成交易信号：

- ✅ 描述当前价格与均线的位置关系（如"价格位于 MA60 上方""MA20 走平"）
- ✅ 描述 MACD 的 DIF/DEA 位置和方向（如"DIF 在零轴上方""DIF 向下靠近 DEA"）
- ✅ 结合均线和 MACD 理解"市场参与者的共识趋势"
- ❌ 输出"金叉买入""死叉卖出""MACD 底背离抄底"等交易信号
- ❌ 基于技术指标给出任何操作建议

---

## 数据源分层（A股/港股 + 全球宏观）

### A 股数据源（优先级）

```
有 Token: Tushare ∥ akshare → 腾讯行情 → 标注不可得
无 Token: akshare → 腾讯行情 → 标注不可得
```

- Tushare 与 akshare 并列并行（先到先用），非前置拦截器
- Tushare Token 无效时静默跳过，不影响主 fallback 链
- TickFlow ✅ 已接入（v0.1.6）— 免费免注册独立 K 线数据源，提供第四源交叉验证

### 港股数据源（优先级）

```
akshare（东方财富港股频道）→ 标注不可得
```

> yfinance（.HK 后缀）计划在未来版本接入。

### 全球宏观数据源

```
FRED API（有 Key）→ akshare → 标注不可得
```

> yfinance 计划在未来版本接入。

### 搜索/新闻源

```
Tavily → Bocha → WebSearch（Claude 内置）
```

---

## 报告质量检查清单（AI 自检）

在输出报告前，AI 应逐项自检：

- [ ] 首部有风险声明
- [ ] 尾部有风险声明
- [ ] 每个财务数字有 `[来源: ... / 日期]`
- [ ] 推测性语句有 `[推测，待验证]`
- [ ] 没有"买入/卖出/建仓"等词语；如出现估值参考价，必须为带假设前提的多情景表述，而非单一目标价数字
- [ ] 技术指标仅为状态描述，无交易信号
- [ ] 不可得维度标注 ⚠️ + attempted_sources
- [ ] 每个维度末尾有 🔍 待独立验证项
- [ ] 数据源清单标注可信度 ★

---

## 发布与分发包维护

本 Skill 按 [Agent Skills](https://agentskills.io) 开放格式构建，面向多平台分发。

### 发布前检查清单

- [ ] `skills/invest-A/SKILL.md` 为最新规格（所有 LAWs、工作流、反模式完整）
- [ ] `bash scripts/bump-version.sh X.Y.Z` 已执行（禁止手动改多处版本号）
- [ ] `bash skills/invest-A/scripts/check-version.sh` 通过
- [ ] `.claude-plugin/marketplace.json` 描述准确
- [ ] `.agents/plugins/marketplace.json` 与 claude-plugin 描述同步
- [ ] `gemini-extension.json` env vars 与 `.env.example` 一致
- [ ] `CHANGELOG.md` 已更新
- [ ] `uv run pytest` 通过
- [ ] `uv run python skills/invest-A/scripts/invest.py diagnose` 输出正常
- [ ] 无 API Key 或敏感信息泄露（security.yml CI 已验证）

### 版本号规范

**canonical**：`pyproject.toml` `[project].version`

由 `bash scripts/bump-version.sh X.Y.Z` 同步的 5 个文件：

- `pyproject.toml`
- `skills/invest-A/SKILL.md`（frontmatter `version`）
- `.claude-plugin/plugin.json`
- `.claude-plugin/marketplace.json`（`plugins[0].version`）
- `gemini-extension.json`

校验：`bash skills/invest-A/scripts/check-version.sh`（CI / pre-commit 已接入）。

### 跨 Harness 兼容

- 不硬编码任何特定 harness 的路径（如 `~/.claude/`）
- `SKILL.md` 中不假定用户使用特定运行时
- 配置文档（`CONFIGURATION.md`）应列出各 harness 的安装方式
- 引擎脚本仅依赖标准库和 `pyproject.toml` 中声明的依赖

---

## 项目结构

```
code/
  AGENTS.md                     ← 本文件（AI 协作规则）
  README.md                     ← 用户文档
  CHANGELOG.md                  ← 版本变更记录
  CONFIGURATION.md              ← 配置指南
  CONTRIBUTORS.md               ← 贡献指南
  .env.example                  ← 环境变量模板
  pyproject.toml                ← uv 依赖管理
  skills/invest-A/              ← Agent Skills 标准目录
    SKILL.md                    ← 运行时规格（LAWs + 工作流 + 专业知识）
    scripts/
      invest.py                 ← CLI 单入口
      lib/
        collector.py            ← 多维度数据采集
        render.py               ← 报告渲染（compact/json/md）
        store.py                ← SQLite 持久化存储
        tushare_client.py       ← Tushare HTTP 轻量客户端
        env.py                  ← 集中配置管理
    tests/                      ← pytest 测试
    references/                 ← 数据源参考文档
  .claude-plugin/               ← Claude Code 插件注册
  .agents/                      ← Agent Skills 通用注册
  .github/                      ← CI/workflows/issue templates
  hooks/                        ← SessionStart 钩子脚本
```
