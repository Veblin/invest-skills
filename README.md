# invest-A — A股投研助手

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-blue.svg" alt="License: MIT" /></a>
  <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.12+-blue.svg" alt="Python 3.12+" /></a>
  <a href="https://github.com/Veblin/invest-A-skill/actions/workflows/security.yml"><img src="https://img.shields.io/github/actions/workflow/status/Veblin/invest-A-skill/security.yml?label=security" alt="Security Scan" /></a>
  <a href="https://github.com/Veblin/invest-A-skill/actions/workflows/validate.yml"><img src="https://img.shields.io/github/actions/workflow/status/Veblin/invest-A-skill/validate.yml?label=validate" alt="Validate" /></a>
  <a href="https://github.com/Veblin/invest-A-skill/releases"><img src="https://img.shields.io/github/v/release/Veblin/invest-A-skill?include_prereleases&label=v0.1.3" alt="Release" /></a>
</p>

> **学习工具，非决策工具。** 多源采集 A 股数据 → 结构化研究备忘录 → 你独立判断。

[//]: # (插图1: 架构全景图 — 六数据源 → 九模块报告 → 用户决策。建议尺寸 800×300，格式 PNG/SVG。展示 Tushare/akshare/baostock/腾讯/FRED/WebSearch 六源并行采集，汇聚为九模块 Markdown 报告，最终标注"用户独立判断"。)

---

## 一句话

输入股票代码，自动采集财务、行情、估值、股东、北向资金、K 线六大维度数据，产出带来源追溯的 Markdown 研究备忘录。每条事实标注来源，每个判断标注依据。

---

## 快速开始

### 安装（2 分钟）

```bash
git clone git@github.com:Veblin/invest-A-skill.git && cd invest-A-skill/code
uv sync
uv run python skills/invest-A/scripts/invest.py diagnose  # 检查数据源
```

### 配置（可选，不配也能跑）

```bash
cp .env.example .env   # 编辑填入 Key，全部可选
```

| Key | 作用 | 获取 |
|-----|------|------|
| `TUSHARE_TOKEN` | A 股主力源（≥2000 分主要接口；`sw_daily` 等需 5000 分） | [tushare.pro](https://tushare.pro) |
| `FRED_API_KEY` | 美国 10Y 国债（ERP 计算） | [fred.stlouisfed.org](https://fred.stlouisfed.org/docs/api/api_key.html) |

> 不配 Tushare：腾讯行情 + baostock 免费兜底，基础 K 线和报价可用。积分详情见 [CONFIGURATION.md](CONFIGURATION.md)。

### 使用

**Claude Code（推荐，通过 plugin marketplace 自动更新）：**

```
/plugin marketplace add Veblin/invest-A-skill
/invest-A 600176                 # 单标的研究
/invest-A 600176 --compare 000858  # 双标的对比
/invest-A 600176 --deep          # 深度模式（730 日 K 线 + 行业分析）
```

**其他 Agent Skills 运行时（Codex / Cursor / Gemini CLI）：**

```bash
npx skills add Veblin/invest-A-skill -g
```

**CLI 直接调用：**

```bash
uv run python skills/invest-A/scripts/invest.py report 600176     # Markdown 报告
uv run python skills/invest-A/scripts/invest.py compare 600176 000858  # 对比
uv run python skills/invest-A/scripts/invest.py store list        # 历史记录
```

---

## 数据采集

六维度并行采集，多源交叉验证，单源失败不阻塞。

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
| — | 宏观/行业 | 美债、行业舆情（`--deep`） | FRED + WebSearch |

> v0.1.3 起默认产出九模块研究备忘录。数据源均可选，未配置时自动降级并标注。

[//]: # (插图2: 报告截图 — 展示九模块报告的实际渲染效果。建议截取模块1（现状快照）和模块2（动态驱动分析）两段，体现 [事实]/[分析] 标记、来源追溯、估值分位等特征。尺寸 700×500。)

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
code/
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
| [AGENTS.md](AGENTS.md) | AI 协作规则、设计约束 |
| [SKILL.md](skills/invest-A/SKILL.md) | 运行时规格、LAWs 完整定义 |
| [CONFIGURATION.md](CONFIGURATION.md) | 配置指南、Tushare 积分对照、常见问题 |
| [CHANGELOG.md](CHANGELOG.md) | 版本变更记录 |
| [CONTRIBUTORS.md](CONTRIBUTORS.md) | 贡献指南 |

---

## 开发

```bash
uv sync && uv run pytest           # 安装 + 测试
uv run python skills/invest-A/scripts/invest.py diagnose
```

提交前确保 `uv run pytest` 通过，无 API Key 泄露。

---

## License

MIT · 不跟踪 · 不上报 · 数据仅存本地

技术栈：Python 3.12+ · Tushare Pro · FRED API · akshare · baostock · SQLite

设计参考：[last30days-skill](https://github.com/mvanhorn/last30days-skill)（Skill 架构）· [daily_stock_analysis](https://github.com/ben1234560/daily_stock_analysis)（数据源）
