# invest-A — A股投资学习 Skill

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-blue.svg" alt="License: MIT" /></a>
  <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.12+-blue.svg" alt="Python 3.12+" /></a>
  <a href="https://github.com/Veblin/claude-invest-A/actions/workflows/security.yml"><img src="https://img.shields.io/github/actions/workflow/status/Veblin/claude-invest-A/security.yml?label=security" alt="Security Scan" /></a>
  <a href="https://github.com/Veblin/claude-invest-A/actions/workflows/validate.yml"><img src="https://img.shields.io/github/actions/workflow/status/Veblin/claude-invest-A/validate.yml?label=validate" alt="Validate" /></a>
  <a href="https://github.com/Veblin/claude-invest-A/releases"><img src="https://img.shields.io/github/v/release/Veblin/claude-invest-A?include_prereleases&label=release" alt="Release" /></a>
  <a href="https://github.com/Veblin/claude-invest-A/blob/main/CONTRIBUTORS.md"><img src="https://img.shields.io/badge/contributions-welcome-brightgreen.svg" alt="PRs Welcome" /></a>
</p>

**学习工具，非决策工具。**

通过 Tushare Pro + FRED 采集六大维度数据，产出带来源追溯的 Markdown 学习报告。每条事实标注来源，每个判断标注依据，不提供买卖建议。

**Claude Code（推荐 — 通过 plugin marketplace 自动更新）：**
```
/plugin marketplace add Veblin/claude-invest-A
```

**Codex, Cursor, Gemini CLI, 或其他 [Agent Skills](https://agentskills.io) 运行时：**
```bash
npx skills add Veblin/claude-invest-A -g
```

零配置即可开始采集基础数据（需配置 API Key 以解锁完整维度）。

---

## 目录

- [为什么有这个 Skill？](#为什么有这个-skill)
- [数据采集维度](#数据采集维度)
- [快速开始](#快速开始)
  - [前置要求](#前置要求)
  - [安装](#安装)
  - [配置 API Key](#配置-api-key)
  - [CLI 命令](#cli-命令)
  - [在 Claude Code 中使用](#在-claude-code-中使用)
- [示例输出](#示例输出)
- [9 条输出法则（LAWs）](#9-条输出法则-laws)
- [工作原理](#工作原理)
- [项目结构](#项目结构)
- [相关文档](#相关文档)
- [开发](#开发)
- [开源](#开源)
- [License](#license)

---

## 为什么有这个 Skill？

投资分析需要多维度信息，但散户面临两个困境：

- **信息过载** — 不知道看什么、信哪个
- **缺乏框架** — 不知道如何系统地分析一家公司

invest-A 解决的是"学习方法论"的问题。它帮你采集财务/行情/产业链/机构数据，标注来源和可信度，解释指标含义，但**最终判断由你做出**。

项目源于作者自身的 A 股学习实践。设计哲学详见 [AGENTS.md](AGENTS.md)。

---

## 数据采集维度

| # | 维度 | 采集内容 | 数据源 |
|---|------|---------|--------|
| 1 | **基本信息** | 公司概况、行业分类、上市日期 | Tushare `stock_basic` |
| 2 | **财务报告** | ROE、EPS、扣非净利润、营收 | Tushare `fina_indicator` |
| 3 | **实时行情** | 开高低收、成交量、成交额 | Tushare `daily` + 腾讯行情兜底 |
| 4 | **十大股东** | 前十大流通股东、持股比例 | Tushare `top10_floatholders` |
| 5 | **资金流向** | 北向资金逐日净流入 | Tushare `moneyflow` |
| 6 | **日K线** | 历史日线数据 | Tushare `daily` |
| — | **宏观数据** | US 10Y/2Y、VIX、CPI、美元指数 | FRED API |
| — | **行业/舆情** | 竞争格局、近期事件 | Claude + WebSearch 补充分析 |

> 所有数据源均为可选。未配置 API Key 时会自动使用免费数据源（腾讯行情）兜底，报告会标注降级情况。详细配置见 [CONFIGURATION.md](CONFIGURATION.md)。

---

## 快速开始

### 前置要求

- **Python 3.12+**
- **[uv](https://docs.astral.sh/uv/)**（依赖管理，推荐）或 `pip3`
- **Claude Code** 或兼容的 [Agent Skills](https://agentskills.io) 运行时

### 安装

```bash
# 1. 克隆仓库
git clone git@github.com:Veblin/claude-invest-A.git
cd claude-invest-A/code

# 2. 安装依赖
uv sync

# 3. 检查数据源
uv run python skills/invest-A/scripts/invest.py diagnose
```

### 配置 API Key

```bash
cp .env.example .env
# 编辑 .env 填入 Key（全部可选，不配则部分维度不可用）
```

| Key | 作用 | 注册地址 |
|-----|------|---------|
| `TUSHARE_TOKEN` | A股数据主力源 | https://tushare.pro |
| `FRED_API_KEY` | 美国宏观数据 | https://fred.stlouisfed.org/docs/api/api_key.html |
| `TAVILY_API_KEY` | 新闻搜索（可选） | https://tavily.com |
| `BOCHA_API_KEY` | 中文搜索（可选） | https://open.bocha.cn |

> 不配置 Tushare 时，实时行情可通过腾讯免费接口获取，但财务/股东等维度将不可用。

### CLI 命令

```bash
# 采集数据
uv run python skills/invest-A/scripts/invest.py collect 600176

# 生成 JSON 报告
uv run python skills/invest-A/scripts/invest.py report 600176 --emit=json

# 深度模式（扩大K线范围 + 行业/舆情分析）
uv run python skills/invest-A/scripts/invest.py collect 600176 --deep

# 双标的对比
uv run python skills/invest-A/scripts/invest.py compare 600176 000858

# 检查数据源
uv run python skills/invest-A/scripts/invest.py diagnose

# 查看/清空存储
uv run python skills/invest-A/scripts/invest.py store list
uv run python skills/invest-A/scripts/invest.py store stats

# 采集并存入持久化存储
uv run python skills/invest-A/scripts/invest.py collect 600176 --store
```

所有子命令支持 `--help` 查看参数详情。

### 本地数据存储

| 输出类型 | 默认路径 | 说明 |
|---------|---------|------|
| **SQLite 数据库** | `~/.local/share/investment/research.db` | `collect --store` 持久化采集结果；`store list/stats/clear` 管理 |
| **HTML 报告** | 当前工作目录 `{cwd}/` | `report --emit=html`（默认格式），文件名 `{时间戳}-{股票代码}-{股票名称}.html` |
| **HTML 输出目录** | `--outdir` 参数可指定 | 如 `report 600176 --outdir=./reports/` |

> SQLite 数据库遵循 [XDG Base Directory](https://specifications.freedesktop.org/basedir-spec/latest/) 规范（`$XDG_DATA_HOME` 默认 = `~/.local/share`）。
> 所有数据仅存本地，不追踪、不上报、无遥测。

### 在 Claude Code 中使用

```bash
/invest-A 600176
/invest-A 600176 --with-macro
/invest-A 600176 --deep
/invest-A 600176 --compare 000858
```

---

## 示例输出

以下是 `invest.py report 600176 --emit=md` 的简版输出示例：

```markdown
# 600519 贵州茅台 学习报告
> 生成于 2026-06-10 | ⚠️ 学习工具，非投资建议

## 公司概况
- 行业: 白酒 (SW二级)
- 上市日期: 2001-08-27 [来源: Tushare stock_basic / 2026-06-10]
- ⚠️ 股权结构较为集中，关注减持风险 [推测，待验证]

## 财务报告
| 指标 | 2024 | 2023 | 变动 |
|------|------|------|------|
| ROE  | 34.2% | 33.8% | +0.4pp |
| EPS  | 68.50 | 61.20 | +11.9% |

[来源: Tushare fina_indicator / 2026-06-10]

## 十大股东
- 前十大流通股东持股 75.2% [来源: Tushare top10_floatholders / 2026-06-10]
- 北向资金近 5 日净流入 +12.3 亿 [来源: Tushare moneyflow / 2026-06-10]

🔍 待验证：前十大股东中是否有新进机构？
```

每条数据都带有可追溯的来源路径和采集日期，分析性判断明确标注为推测。

---

## 9 条输出法则（LAWs）

所有输出由 9 条法则约束，违反即为 Bug。完整定义见 [SKILL.md](skills/invest-A/SKILL.md)。

| LAW | 内容 |
|-----|------|
| 1 | 每条分析论述必须引用数据来源 |
| 2 | 使用统一七维度结构模板 |
| 3 | 区分"事实陈述"与"分析判断" |
| 4 | 风险提示出现首部和尾部 |
| 5 | 单源数据标注不确定性，多源交叉验证优先 |
| 6 | **禁止买卖建议、目标价、仓位建议** |
| 7 | 每个数字标注可审查的追溯路径 |
| 8 | 每个维度末尾要求"🔍 待独立验证项" |
| 9 | 无数据源支撑的分析不输出 |

---

## 工作原理

1. **你输入股票代码。** 如 `600176`（中国巨石）或 `000858`（五粮液）。
2. **系统自动采集六大维度。** 并行请求 Tushare 获取基本信息、财务指标、实时行情、十大股东、北向资金、日K线。
3. **宏观数据补充。** 如已配置 FRED API，自动拉取 US 10Y/2Y、VIX、CPI、美元指数。
4. **行业/舆情兜底。** 通过 WebSearch 补充行业竞争格局和近期事件（`--deep` 模式）。
5. **生成结构化报告。** 按七维度模板渲染，每条数据标注来源路径和采集日期。
6. **你独立判断。** 报告提供分析框架和数据，不输出买卖建议。每个维度末尾有"待验证项"引导独立核查。
7. **可对比分析。** `compare` 子命令对比同行业两只股票的财务数据，辅助理解行业差异。

---

## 项目结构

```
invest-A/
  skills/invest-A/              ← Agent Skills 标准目录
    SKILL.md                    ← 运行时指令（LAWs + 工作流）
    scripts/
      invest.py                 ← CLI 单入口（collect/report/compare/diagnose/store）
      lib/
        env.py                  ← 配置管理器（多层 .env 加载）
        collector.py            ← 多维度数据采集
        render.py               ← 报告渲染（compact/json/md/html）
        tushare_client.py       ← Tushare HTTP 轻量客户端
        store.py                ← SQLite 持久化存储
        proxy.py                ← HTTP 代理检测与 Clash 规则提示
        schema.py               ← 数据结构定义
        technical.py            ← 技术指标计算
        valuation.py            ← 估值分位计算
        assets/                 ← 离线资源（Chart.js 等）
    tests/                      ← pytest 测试
    references/
      source-guide.md           ← 数据源参考
  .claude-plugin/               ← Claude Code 插件注册
  .agents/                      ← Agent Skills 通用注册
  .github/                      ← CI/workflows/issue templates
  hooks/                        ← SessionStart 钩子
  pyproject.toml                ← uv 依赖管理
```

---

## 相关文档

| 文档 | 内容 |
|------|------|
| [AGENTS.md](AGENTS.md) | AI 协作规则、设计哲学、五条硬约束 |
| [SKILL.md](skills/invest-A/SKILL.md) | 运行时规格、CLI 命令、LAWs 完整定义、引用格式 |
| [CONFIGURATION.md](CONFIGURATION.md) | 配置指南、Env 变量说明、各运行时安装方法 |
| [CONTRIBUTORS.md](CONTRIBUTORS.md) | 贡献指南、提交检查清单 |
| [CHANGELOG.md](CHANGELOG.md) | 版本变更记录 |

---

## 开发

```bash
uv sync                    # 安装依赖
uv run pytest              # 运行测试（当前 7 个测试）
uv run pytest --cov        # 含覆盖率报告
```

### 贡献

欢迎报告 Bug、新增数据源、改进知识库。请先阅读 [CONTRIBUTORS.md](CONTRIBUTORS.md)。

确保提交前：
- `uv run pytest` 通过
- `uv run python skills/invest-A/scripts/invest.py diagnose` 输出正常
- 无 API Key 或敏感信息泄露（CI 会自动检查）

---

## 开源

MIT 许可。不跟踪、不分析、无遥测。你的查询和采集数据只存于本地。

**技术栈：** Python 3.12+、Tushare Pro、FRED API、akshare、SQLite、pytest。

**设计参考：** 项目结构受 [last30days-skill](https://github.com/mvanhorn/last30days-skill) 启发，Tushare 客户端设计借鉴 [daily_stock_analysis](https://github.com/ben1234560/daily_stock_analysis)。

---

## License

MIT — 详见 [LICENSE](LICENSE)。
