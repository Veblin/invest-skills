# invest-skills — A股投研技能集

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-blue.svg" alt="License: MIT" /></a>
  <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.12+-blue.svg" alt="Python 3.12+" /></a>
  <a href="https://github.com/Veblin/invest-skills/actions/workflows/validate.yml"><img src="https://img.shields.io/github/actions/workflow/status/Veblin/invest-skills/validate.yml?label=validate" alt="Validate" /></a>
  <a href="https://github.com/Veblin/invest-skills/releases"><img src="https://img.shields.io/github/v/release/Veblin/invest-skills?include_prereleases&label=v0.2.0" alt="Release" /></a>
</p>

> **学习工具，非决策工具。** 多源采集 A 股数据 → 结构化研究备忘录 + 全市场扫描 → 你独立判断。

---

## 一句话

**invest-a-stock** — 输入股票代码，自动采集财务/行情/估值/股东/北向/K线等 7 维度数据，四视角并行分析（生意质量/财务估值/行业竞争/风险治理），产出带来源追溯的九模块研究备忘录。**invest-a-limit-up** — 全市场涨停扫描 + 归因。每条事实标注来源，每条判断标注依据。所有结果自动存入 SQLite，支持历史回溯。

---

## 快速开始（3 步，~3 分钟）

### 第 1 步：安装环境

```bash
# 需要 Python 3.12+
python3 --version

# 安装 uv（Python 包管理器，比 pip 快 10-100 倍）
curl -LsSf https://astral.sh/uv/install.sh | sh
# 或 macOS: brew install uv
# 或 Windows: powershell -c "irm https://astral.sh/uv/install.ps1 | iex"

# 验证 uv 安装
uv --version
```

### 第 2 步：克隆 + 安装依赖

```bash
git clone https://github.com/Veblin/invest-skills.git && cd invest-skills
uv sync
```

### 第 3 步：配置 Token（2 种方式任选）

**方式 A — 对话式配置（推荐，无需编辑文件）：**

在 Claude Code 中直接说：
```
帮我配置 invest-a-stock 的 Token
```
Claude 会引导你填写 `TUSHARE_TOKEN`、`TAVILY_API_KEY` 等，自动写入 `.env` 文件。

**方式 B — 手动编辑 `.env`：**

```bash
cp .env.example .env
```

编辑 `.env`，至少填入一个 Token：

| Key | 作用 | 不配的影响 | 获取 |
|-----|------|-----------|------|
| `TUSHARE_TOKEN` | A 股数据主力源 | 财务指标/估值历史/资金流向/股东不可用；K 线和行情走腾讯免费兜底 | [tushare.pro](https://tushare.pro) 注册即送 120 分 |
| `FRED_API_KEY` | 美国 10Y 国债（ERP/WACC） | DCF 估值中 WACC 用固定值 | [fred.stlouisfed.org](https://fred.stlouisfed.org/docs/api/api_key.html) 免费 |
| `TAVILY_API_KEY` | 新闻搜索 Layer 3 | 仍可用公告 + 查询包 | [tavily.com](https://tavily.com) 免费 1000 次/月 |

#### Tushare 积分档位与功能对照

| 积分 | 获取方式 | 频率限制 | 新增能力 |
|:---:|------|:---:|------|
| **120**（免费注册） | 注册即送 | 50 次/分钟 | 股票列表、日/周/月线行情、三大财务报表 |
| **2000**（推荐） | 捐赠 200 元或贡献积分 | **200 次/分钟** | + 财务指标（`fina_indicator`：ROE/EPS/毛利率/杜邦拆解/OCF/FCFF） + 日线估值（`daily_basic`：PE/PB/PS 历史序列 + 分位） + 资金流向（`moneyflow`） + 融资融券（`margin_detail`） + 北向资金（`hsgt_top10`） + 十大股东（`top10_floatholders`） + 指数分类（`index_classify`） |
| **5000** | 捐赠 500 元或更多贡献 | 200 次/分钟 | + 申万行业（`sw_daily`，行业相对估值/利润池分析） + 期权数据（`opt_daily`，PCR 情绪指标） |
| **10000+** | 捐赠 1000 元+ 或大量贡献 | 200 次/分钟 | + 机构研报（`report_rc`，卖方评级/一致预期/目标价） + 业绩预告（`forecast`） |

> **推荐 2000 分** — 解锁完整估值分析 + 资金流向，覆盖 invest-a-stock 90% 功能。积分不足的接口自动降级（如 120 分时跳过高阶接口并标注"数据源不可用"）。

### 验证安装

```bash
uv run python skills/invest-a-stock/scripts/invest.py diagnose
```

看到 `可用: 6/7` 即安装成功。各 Token 状态和积分要求见 [CONFIGURATION.md](CONFIGURATION.md)。

---

## 两种使用方式

### 方式 A：Claude Code Agent（推荐）

在 Claude Code 中安装插件：

```
/plugin marketplace add Veblin/invest-skills
```

然后用斜杠命令调用：

```
/invest-a-stock 600176                  # 单标的研究（多 Agent 并行分析）
/invest-a-stock 600176 --compare 000858 # 双标的对比

/invest-a-limit-up                      # 全市场涨停扫描
/invest-a-limit-up --sector 半导体       # 行业筛选
/invest-a-limit-up --quality-filter     # 六维质量过滤
```

> `/invest-a-stock` 默认即深度模式 — 自动执行数据采集 + 四视角并行分析 + 估值计算，产出完整九模块备忘录。无需额外 `--deep` 参数。

<details>
<summary>其他 Agent 环境（Cursor / Codex / Gemini CLI 等）</summary>

```bash
# Cursor
npx skills add Veblin/invest-skills --skill invest-a-stock -g -a cursor -y

# 其他兼容 agentskills.io 的环境
npx skills add Veblin/invest-skills --skill invest-a-stock -g -y
```

安装后在对话中说 "用 invest-a-stock 研究 600176" 即可。
</details>

### 方式 B：命令行直接调用（无需 Agent）

```bash
# ---- invest-a-stock ----

# 采集 + 报告
uv run python skills/invest-a-stock/scripts/invest.py collect 600176
uv run python skills/invest-a-stock/scripts/invest.py report 600176

# 采集 + 报告（默认即深度模式）
uv run python skills/invest-a-stock/scripts/invest.py collect 600176
uv run python skills/invest-a-stock/scripts/invest.py report 600176

# 科学估值计算（PE/PB/盈利收益/隐含增长/ROE-PB 匹配）
uv run python skills/invest-a-stock/scripts/invest.py value 600176
uv run python skills/invest-a-stock/scripts/invest.py value 600176 --store   # 存入数据库

# 财务验算 / 质地检查
uv run python skills/invest-a-stock/scripts/invest.py rigor 600176 --verify-all
uv run python skills/invest-a-stock/scripts/invest.py check 600176

# 双标对比 / 历史 diff
uv run python skills/invest-a-stock/scripts/invest.py compare 600176 000858
uv run python skills/invest-a-stock/scripts/invest.py diff 600176

# 组合风险 / 投资假设追踪
uv run python skills/invest-a-stock/scripts/invest.py portfolio holdings.json
uv run python skills/invest-a-stock/scripts/invest.py thesis 600176 --init

# 历史采集 + 估值记录
uv run python skills/invest-a-stock/scripts/invest.py store list
uv run python skills/invest-a-stock/scripts/invest.py store valuations --symbol 600176

# ---- invest-a-limit-up ----

# 基础扫描（近 10 个交易日）
uv run python skills/invest-a-limit-up/scripts/scan.py

# 质量过滤模式
uv run python skills/invest-a-limit-up/scripts/scan.py --quality-filter

# 行业 + 连板筛选
uv run python skills/invest-a-limit-up/scripts/scan.py --sector 半导体 --min-board 2

# JSON 输出
uv run python skills/invest-a-limit-up/scripts/scan.py --json
```

---

## 多 Agent 深度分析（v0.2.0 新特性）

`/invest-a-stock` 默认即采用两阶段多 Agent 并行架构，将深度分析从 ~14 分钟压缩至 ~6 分钟：

```
Phase 1: 并行采集（3 Agent，~2min）
┌──────────────┐  ┌──────────────┐  ┌──────────────┐
│ Collector A  │  │ Collector B  │  │ Collector C  │
│ Tushare 主源 │  │ akshare 交叉  │  │ 股东+研报    │
└──────┬───────┘  └──────┬───────┘  └──────┬───────┘
       └────────┬─────────┘                 │
          merge + 交叉验证                    │
                │                            │
Phase 2: 并行分析（4 Agent，~3min）           │
┌────────┐ ┌────────┐ ┌────────┐ ┌────────┐  │
│生意质量│ │财务估值│ │行业竞争│ │风险治理│  │
└───┬────┘ └───┬────┘ └───┬────┘ └───┬────┘  │
    └───────────┴─────────┴───────────┘       │
                      │                       │
Phase 3: 主编 Claude 合成 → .md 报告           │
```

- **交叉验证**：financials/valuation 关键字段 Tushare vs akshare 双源对比，差异 >20% 自动触发第三源 tie-breaker
- **估值计算**：七步科学估值（PE/PB/盈利收益率/隐含增长/ROE-PB 匹配/多情景综合）
- **数据持久化**：采集快照和估值结果均存入 SQLite，支持历史回溯和两期对比

---

## 数据持久化

所有采集和扫描结果自动存入 SQLite（`~/.local/share/investment/research.db`，WAL 模式）：

| 表 | 内容 | 用途 |
|----|------|------|
| `collections` + `findings` | 个股采集快照 | 历史对比（`diff`） |
| `valuations` | 估值记录（含多情景区间） | 估值历史回溯（`store valuations`） |
| `limit_up_scans` + `limit_up_stocks` | 涨停扫描记录 | 市场宽度趋势 / 标的回溯 |
| `pipeline_states` | 流水线状态 | 断点续跑 |
| `thesis` | 投资假设 + 红线 | 假设追踪 |

无需额外配置，首次运行自动建表。

---

## 数据采集

多维度并行采集，多源交叉验证，单源失败不阻塞。

| # | 维度 | 内容 | 数据源 |
|---|------|------|--------|
| 1 | 基本信息 | 公司概况、行业分类 | Tushare ∥ akshare |
| 2 | 财务报告 | ROE、EPS、毛利率、OCF、杜邦拆解 | Tushare ∥ akshare |
| 3 | 实时行情 | 开高低收、成交量 | Tushare ∥ 腾讯 |
| 4 | 十大股东 | 前十大流通股东 | Tushare ∥ akshare |
| 5 | 北向资金 | 个股北向持股 | Tushare ∥ akshare |
| 6 | 估值历史 | PE/PB 序列与分位 + PE Band | Tushare ∥ 腾讯快照 |
| 7 | 日K线 | 历史日线 + 技术指标（MA/MACD/RSI/KDJ/Ichimoku） | Tushare ∥ baostock ∥ TickFlow |
| — | 市场结构 | 行业指数、资金流向、ERP、PCR | Tushare + FRED |
| — | 机构研报 | 卖方一致预期、业绩预告 | Tushare（10000+ 积分） |
| — | 新闻包 | 公告 + 查询包 + 可选 Tavily | akshare ∥ Tavily |

> v0.1.4 起默认产出九模块研究备忘录。数据源均可选，未配置时自动降级并标注。

---

## 产出法则

所有输出由 16 条法则约束，违反即为 Bug。详见 [SKILL.md](skills/invest-a-stock/SKILL.md)。

| LAW | 规则 |
|-----|------|
| 1 | 每条分析论述引用数据来源 |
| 3 | 区分 [事实] 与 [分析] |
| 5 | 多源交叉验证优先，单源标注不确定性 |
| 6 | **禁止买卖建议、仓位建议；允许多情景估值参考价（须假设前提+概率+免责），禁止无假设的单一目标价** |
| 7 | 每个数字标注可审查的追溯路径 |
| 9 | 无数据源支撑的分析不输出 |
| 11 | 问题卡来自四类结构化触发（变化/估值/行业/趋势） |
| 15 | Bull/Bear 须含数值场景化传导链条 |
| 16 | 左/右概率并列呈现，禁止单一结论 |

---

## 项目结构

```
skills/
  invest-a-stock/              ← 个股深度研究
    SKILL.md                   ← 核心规格（LAWs + SOP + 多 Agent 流程）
    references/                ← 专项参考（modules / financials / sentiment / game-theory / source-guide / agent-prompts）
    scripts/
      invest.py                ← CLI 统一入口（19 个子命令）
      valuation_calc.py        ← 科学估值计算器（七步多方法）
      merge_collections.py     ← 多源合并 + 交叉验证
      lib/                     ← collector / render* / store / valuation / financial_rigor
                                 / news_scanner / quality_check / risk_scanner / technical
                                 / portfolio_review / report_audit / participant_scan
    tests/                     ← pytest
  invest-a-limit-up/           ← 涨停扫描
    SKILL.md
    scripts/
      scan.py                  ← 扫描 CLI
      lib/                     ← limit_up_scanner / tushare_enrich
.claude-plugin/                ← Claude Code 插件注册
hooks/                         ← SessionStart 钩子
scripts/                       ← 运维脚本（sync_version / bump-version）
pyproject.toml                 ← uv 依赖管理
```

---

## 文档

| 文档 | 内容 |
|------|------|
| [docs/README.md](docs/README.md) | 对外文档索引 |
| [docs/roadmap.md](docs/roadmap.md) | 路线图（待接入数据源） |
| [CONFIGURATION.md](CONFIGURATION.md) | 各 Harness 安装细节、Tushare 积分、常见问题 |
| [SKILL.md](skills/invest-a-stock/SKILL.md) | invest-a-stock 核心规格（LAWs + 专项路由 + 多 Agent 流程） |
| [references/agent-prompts.md](skills/invest-a-stock/references/agent-prompts.md) | 四视角 Agent prompt 模板 |
| [SKILL.md](skills/invest-a-limit-up/SKILL.md) | invest-a-limit-up 涨停扫描规格 |
| [references/](skills/invest-a-stock/references/) | 九模块、财报/舆情/行为扫描专项 |
| [AGENTS.md](AGENTS.md) | AI 协作规则、设计约束 |
| [CHANGELOG.md](CHANGELOG.md) | 版本变更记录 |
| [CONTRIBUTORS.md](CONTRIBUTORS.md) | 贡献指南 |

---

## 开发

```bash
uv sync && uv run pytest
uv run python skills/invest-a-stock/scripts/invest.py diagnose
bash skills/invest-a-stock/scripts/check-version.sh   # 校验版本一致性
bash scripts/bump-version.sh X.Y.Z                    # 统一升级版本号
```

提交前确保测试通过，无 API Key 泄露。

---

## License

MIT · 不跟踪 · 不上报 · 数据仅存本地

技术栈：Python 3.12+ · Tushare Pro · FRED API · akshare · baostock · SQLite

设计参考：[last30days-skill](https://github.com/mvanhorn/last30days-skill) · [daily_stock_analysis](https://github.com/ben1234560/daily_stock_analysis)
