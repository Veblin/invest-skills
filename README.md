# invest-A — A股投研助手

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-blue.svg" alt="License: MIT" /></a>
  <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.12+-blue.svg" alt="Python 3.12+" /></a>
  <a href="https://github.com/Veblin/invest-A-skill/actions/workflows/security.yml"><img src="https://img.shields.io/github/actions/workflow/status/Veblin/invest-A-skill/security.yml?label=security" alt="Security Scan" /></a>
  <a href="https://github.com/Veblin/invest-A-skill/actions/workflows/validate.yml"><img src="https://img.shields.io/github/actions/workflow/status/Veblin/invest-A-skill/validate.yml?label=validate" alt="Validate" /></a>
  <a href="https://github.com/Veblin/invest-A-skill/releases"><img src="https://img.shields.io/github/v/release/Veblin/invest-A-skill?include_prereleases&label=v0.1.4" alt="Release" /></a>
</p>

> **学习工具，非决策工具。** 多源采集 A 股数据 → 结构化研究备忘录 → 你独立判断。

[//]: # (插图1: 架构全景图 — 六数据源 → 九模块报告 → 用户决策。建议尺寸 800×300，格式 PNG/SVG。)

---

## 一句话

输入股票代码，自动采集财务、行情、估值、股东、北向资金、K 线等维度数据，产出带来源追溯的 Markdown 研究备忘录。每条事实标注来源，每个判断标注依据。

---

## 快速开始

invest-A 分两层：**Skill**（告诉 Agent 怎么调研）+ **Python 引擎**（实际拉数据、渲染报告）。多数用户先装 Skill，再一次性配置引擎。

### 1. 安装 Skill（按你的环境选一）

| 环境 | 安装方式 | 说明 |
|------|----------|------|
| **Claude Code** | `/plugin marketplace add Veblin/invest-A-skill` | 推荐；含插件钩子，marketplace 可自动更新 |
| **Cursor** | `npx skills add Veblin/invest-A-skill --skill invest-A -g -a cursor -y` | 安装到 Cursor Skills 目录 |
| **OpenClaw** | `npx skills add Veblin/invest-A-skill --skill invest-A -g -a openclaw -y` | 或 `openclaw skills install git:Veblin/invest-A-skill`（需本仓库已 clone） |
| **Hermes** | `npx skills add Veblin/invest-A-skill --skill invest-A -g -y` | Agent Skills 开放格式，与 Claude Code / Cursor 同源 |
| **Codex / Gemini CLI 等** | `npx skills add Veblin/invest-A-skill --skill invest-A -g -y` | 可用 `-a <agent>` 指定目标，见 [agentskills.io](https://agentskills.io) |

<details>
<summary>各环境安装后怎么用</summary>

**Claude Code**

```
/invest-A 600176                  # 单标的研究
/invest-A 600176 --compare 000858 # 双标的对比
/invest-A 600176 --deep           # 深度模式（730 日 K 线 + 行业分析）
```

**Cursor / Hermes / OpenClaw / 其他 Agent**

在对话中直接说明意图即可，例如：

> 用 invest-A 研究 600176，生成九模块 Markdown 备忘录。

Agent 会按 `skills/invest-A/SKILL.md` 调用 `invest.py` 采集与渲染。

</details>

> Skill 安装只注册工作流与规范；**数据采集仍依赖本机 Python 引擎**（下一步）。Claude Code 插件会把仓库脚本挂载到插件目录，其余环境需 clone 一次。

### 2. 配置数据采集引擎（一次性，约 2 分钟）

```bash
git clone https://github.com/Veblin/invest-A-skill.git && cd invest-A-skill
uv sync
uv run python skills/invest-A/scripts/invest.py diagnose   # 检查数据源是否可用
```

`diagnose` 通过后即可在 Agent 或 CLI 中正常出报告。开发模式（本地改 Skill 即时生效）：

```bash
ln -sfn "$PWD/skills/invest-A" ~/.agents/skills/invest-A   # 可选：symlink 到 Agent Skills 目录
```

### 3. 配置 API Key（可选，不配也能跑基础功能）

```bash
cp .env.example .env   # 编辑填入 Key，全部可选
```

| Key | 作用 | 获取 |
|-----|------|------|
| `TUSHARE_TOKEN` | A 股主力源（≥2000 分主要接口；`sw_daily` 等需 5000 分） | [tushare.pro](https://tushare.pro) |
| `FRED_API_KEY` | 美国 10Y 国债（ERP 计算） | [fred.stlouisfed.org](https://fred.stlouisfed.org/docs/api/api_key.html) |

不配 Tushare 时：腾讯行情 + baostock 免费兜底，基础 K 线与报价仍可用。积分与接口对照见 [CONFIGURATION.md](CONFIGURATION.md)。

### 4. CLI 直接调用（不经过 Agent）

```bash
uv run python skills/invest-A/scripts/invest.py report 600176          # Markdown 九模块报告
uv run python skills/invest-A/scripts/invest.py compare 600176 000858    # 双标的对比
uv run python skills/invest-A/scripts/invest.py store list               # 历史采集记录
```

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

> v0.1.4 起默认产出九模块研究备忘录。数据源均可选，未配置时自动降级并标注。

[//]: # (插图2: 报告截图 — 九模块报告渲染效果。)

---

## 产出法则

所有输出由 9 条法则约束，违反即为 Bug。详见 [SKILL.md](skills/invest-A/SKILL.md)。

| LAW | 规则 |
|-----|------|
| 1 | 每条分析论述引用数据来源 |
| 3 | 区分 [事实] 与 [分析] |
| 5 | 多源交叉验证优先，单源标注不确定性 |
| 6 | **禁止买卖建议、目标价、仓位建议** |
| 7 | 每个数字标注可审查的追溯路径 |
| 9 | 无数据源支撑的分析不输出 |

---

## 项目结构

```
skills/invest-A/
  SKILL.md                  ← 运行时规格（LAWs + 工作流）
  scripts/
    invest.py               ← CLI 单入口
    lib/                    ← collector / render / store / technical / valuation
  tests/                    ← pytest
.claude-plugin/             ← Claude Code 插件注册
hooks/                      ← SessionStart 钩子
pyproject.toml              ← uv 依赖
```

---

## 文档

| 文档 | 内容 |
|------|------|
| [docs/README.md](docs/README.md) | 对外文档索引 |
| [docs/roadmap.md](docs/roadmap.md) | 路线图（待接入数据源） |
| [CONFIGURATION.md](CONFIGURATION.md) | 各 Harness 安装细节、Tushare 积分、常见问题 |
| [SKILL.md](skills/invest-A/SKILL.md) | 运行时规格、LAWs 完整定义 |
| [AGENTS.md](AGENTS.md) | AI 协作规则、设计约束 |
| [CHANGELOG.md](CHANGELOG.md) | 版本变更记录 |
| [CONTRIBUTORS.md](CONTRIBUTORS.md) | 贡献指南 |

---

## 开发

```bash
uv sync && uv run pytest
uv run python skills/invest-A/scripts/invest.py diagnose
bash skills/invest-A/scripts/check-version.sh   # 版本四件套一致性
```

提交前确保测试通过，无 API Key 泄露。

---

## License

MIT · 不跟踪 · 不上报 · 数据仅存本地

技术栈：Python 3.12+ · Tushare Pro · FRED API · akshare · baostock · SQLite

设计参考：[last30days-skill](https://github.com/mvanhorn/last30days-skill) · [daily_stock_analysis](https://github.com/ben1234560/daily_stock_analysis)
