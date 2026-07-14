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

**invest:a-stock** — 输入股票代码，自动采集财务/行情/估值/股东/北向/K线等维度，产出带来源追溯的九模块研究备忘录。**invest:a-limit-up** — 全市场涨停扫描 + 归因。每条事实标注来源，每条判断标注依据。所有结果自动存入 SQLite，支持历史回溯。

---

## 快速开始（3 步，~3 分钟）

### 第 1 步：安装环境

```bash
# 需要 Python 3.12+
python3 --version

# 安装 uv（Python 包管理器）
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### 第 2 步：克隆 + 安装依赖

```bash
git clone https://github.com/Veblin/invest-skills.git && cd invest-skills
uv sync
```

### 第 3 步：配置 Token（2 分钟）

```bash
cp .env.example .env
```

编辑 `.env`，至少填入一个 Token：

| Key | 作用 | 不配的影响 | 获取 |
|-----|------|-----------|------|
| `TUSHARE_TOKEN` | A 股数据主力源 | 财务指标/估值历史/资金流向/股东不可用；K 线和行情走腾讯免费兜底 | [tushare.pro](https://tushare.pro) 注册即送 120 分 |
| `FRED_API_KEY` | 美国 10Y 国债（ERP/WACC） | DCF 估值中 WACC 用固定值 | [fred.stlouisfed.org](https://fred.stlouisfed.org/docs/api/api_key.html) 免费 |
| `TAVILY_API_KEY` | 新闻搜索 Layer 3 | 仍可用公告 + 查询包 | [tavily.com](https://tavily.com) 免费 1000 次/月 |

### 验证安装

```bash
uv run python skills/invest-a-stock/scripts/invest.py diagnose
```

看到 `✓ 数据采集引擎就绪` 即安装成功。各 Token 状态和积分要求见 [CONFIGURATION.md](CONFIGURATION.md)。

---

## 两种使用方式

### 方式 A：Claude Code Agent（推荐）

在 Claude Code 中安装插件：

```
/plugin marketplace add Veblin/invest-skills
```

然后用斜杠命令调用：

```
/invest:a-stock 600176                  # 单标的研究（九模块备忘录）
/invest:a-stock 600176 --deep           # 深度模式（730 日 K 线 + 行业分析）
/invest:a-stock 600176 --compare 000858 # 双标的对比

/invest:a-limit-up                      # 全市场涨停扫描
/invest:a-limit-up --sector 半导体       # 行业筛选
/invest:a-limit-up --quality-filter     # 六维质量过滤
```

<details>
<summary>其他 Agent 环境（Cursor / Codex / Gemini CLI 等）</summary>

```bash
# Cursor
npx skills add Veblin/invest-skills --skill invest:a-stock -g -a cursor -y

# 其他兼容 agentskills.io 的环境
npx skills add Veblin/invest-skills --skill invest:a-stock -g -y
```

安装后在对话中说 "用 invest:a-stock 研究 600176" 即可。
</details>

### 方式 B：命令行直接调用（无需 Agent）

<details>
<summary>invest:a-stock — 个股研究</summary>

```bash
# 采集 + 报告
uv run python skills/invest-a-stock/scripts/invest.py collect 600176
uv run python skills/invest-a-stock/scripts/invest.py report 600176

# 深度模式
uv run python skills/invest-a-stock/scripts/invest.py collect 600176 --deep

# 财务验算 / 质地检查
uv run python skills/invest-a-stock/scripts/invest.py rigor 600176 --verify-all
uv run python skills/invest-a-stock/scripts/invest.py check 600176

# 双标对比 / 历史 diff
uv run python skills/invest-a-stock/scripts/invest.py compare 600176 000858
uv run python skills/invest-a-stock/scripts/invest.py diff 600176

# 组合风险 / 投资假设追踪
uv run python skills/invest-a-stock/scripts/invest.py portfolio holdings.json
uv run python skills/invest-a-stock/scripts/invest.py thesis 600176 --init

# 历史采集记录
uv run python skills/invest-a-stock/scripts/invest.py store list
```
</details>

<details>
<summary>invest:a-limit-up — 涨停扫描</summary>

```bash
# 基础扫描（近 10 个交易日）
uv run python skills/invest-a-limit-up/scripts/scan.py

# 质量过滤模式
uv run python skills/invest-a-limit-up/scripts/scan.py --quality-filter

# 行业 + 连板筛选
uv run python skills/invest-a-limit-up/scripts/scan.py --sector 半导体 --min-board 2

# 自定义质量阈值
uv run python skills/invest-a-limit-up/scripts/scan.py --min-price 10 --min-float-mkt-cap 30e8

# JSON 输出（方便程序处理）
uv run python skills/invest-a-limit-up/scripts/scan.py --json

# 跳过数据库保存
uv run python skills/invest-a-limit-up/scripts/scan.py --no-save
```

扫描结果自动存入 `~/.local/share/investment/research.db`（`limit_up_scans` / `limit_up_stocks` 表），支持历史回溯查询。
</details>

---

## 数据持久化

所有采集和扫描结果自动存入 SQLite（`~/.local/share/investment/research.db`，WAL 模式）：

| 表 | 内容 | 用途 |
|----|------|------|
| `collections` + `findings` | 个股采集快照 | 历史对比（`invest.py diff 600176`） |
| `limit_up_scans` + `limit_up_stocks` | 涨停扫描记录 | 市场宽度趋势 / 标的回溯 |
| `pipeline_states` | 流水线状态 | 断点续跑 |
| `thesis` | 投资假设 + 红线 | 假设追踪（`invest.py thesis`） |

无需额外配置，首次运行自动建表。

---

## 数据采集

多维度并行采集，多源交叉验证，单源失败不阻塞。

| # | 维度 | 内容 | 数据源 |
|---|------|------|--------|
| 1 | 基本信息 | 公司概况、行业分类 | Tushare ∥ akshare |
| 2 | 财务报告 | ROE、EPS、扣非净利润 | Tushare ∥ akshare |
| 3 | 实时行情 | 开高低收、成交量 | Tushare ∥ 腾讯 |
| 4 | 十大股东 | 前十大流通股东 | Tushare ∥ akshare |
| 5 | 北向资金 | 个股北向持股 | Tushare ∥ akshare |
| 6 | 估值历史 | PE/PB 序列与分位 | Tushare ∥ 腾讯快照 |
| 7 | 日K线 | 历史日线 + 技术指标 | Tushare ∥ baostock |
| — | 市场结构 | 行业指数、资金流向、ERP | Tushare + FRED |
| — | 机构研报 | 卖方一致预期、业绩预告（`--dims=...,research`） | Tushare ∥ akshare |
| — | 宏观/行业 | 美债、行业舆情（`--deep`） | FRED + WebSearch |
| — | 新闻包 | 公告 + 查询包 + 可选 Tavily（`--with-news-pack`） | akshare ∥ Tavily |

> v0.1.4 起默认产出九模块研究备忘录。数据源均可选，未配置时自动降级并标注。

[//]: # (插图2: 报告截图 — 九模块报告渲染效果。)

---

## 产出法则

所有输出由 9 条法则约束，违反即为 Bug。详见 [SKILL.md](skills/invest-a-stock/SKILL.md)。

| LAW | 规则 |
|-----|------|
| 1 | 每条分析论述引用数据来源 |
| 3 | 区分 [事实] 与 [分析] |
| 5 | 多源交叉验证优先，单源标注不确定性 |
| 6 | **禁止买卖建议、仓位建议；允许带假设前提的多情景估值参考价，禁止无假设的单一目标价** |
| 7 | 每个数字标注可审查的追溯路径 |
| 9 | 无数据源支撑的分析不输出 |

---

## 项目结构

```
skills/
  invest-a-stock/              ← 个股深度研究
    SKILL.md                   ← 核心规格（LAWs + SOP）
    references/                ← 专项参考（modules / financials / sentiment / game-theory / source-guide）
    scripts/
      invest.py                ← CLI 统一入口
      lib/                     ← collector / render* / store / valuation / financial_rigor
                                 / news_scanner / quality_check / risk_scanner / technical
                                 / portfolio_review / report_audit / participant_scan / limit_up_store
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
| [SKILL.md](skills/invest-a-stock/SKILL.md) | invest:a-stock 核心规格（LAWs + 专项路由） |
| [SKILL.md](skills/invest-a-limit-up/SKILL.md) | invest:a-limit-up 涨停扫描规格 |
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
