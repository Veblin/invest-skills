# invest-A — A股投资学习 Skill

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-blue.svg" alt="License: MIT" /></a>
  <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.12+-blue.svg" alt="Python 3.12+" /></a>
  <a href="https://github.com/Veblin/invest-A-skill/actions/workflows/security.yml"><img src="https://img.shields.io/github/actions/workflow/status/Veblin/invest-A-skill/security.yml?label=security" alt="Security Scan" /></a>
  <a href="https://github.com/Veblin/invest-A-skill/actions/workflows/validate.yml"><img src="https://img.shields.io/github/actions/workflow/status/Veblin/invest-A-skill/validate.yml?label=validate" alt="Validate" /></a>
  <a href="https://github.com/Veblin/invest-A-skill/releases"><img src="https://img.shields.io/github/v/release/Veblin/invest-A-skill?include_prereleases&label=release" alt="Release" /></a>
  <a href="https://github.com/Veblin/invest-A-skill/blob/main/CONTRIBUTORS.md"><img src="https://img.shields.io/badge/contributions-welcome-brightgreen.svg" alt="PRs Welcome" /></a>
</p>

**学习工具，非决策工具。**

通过 Tushare Pro + FRED 采集六大维度数据，产出带来源追溯的 Markdown 学习报告。每条事实标注来源，每个判断标注依据，不提供买卖建议。

**Claude Code（推荐 — 通过 plugin marketplace 自动更新）：**
```
/plugin marketplace add Veblin/invest-A-skill
```

**Codex, Cursor, Gemini CLI, 或其他 [Agent Skills](https://agentskills.io) 运行时：**
```bash
npx skills add Veblin/invest-A-skill -g
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
  - [Tushare 积分与功能对照](#tushare-积分与功能对照)
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

> v0.1.3 起默认输出九模块研究备忘录（`report` 默认 `--emit md`）。Tushare 各接口积分要求见 [Tushare 积分与功能对照](#tushare-积分与功能对照)。

| # | 维度 | 采集内容 | 数据源 |
|---|------|---------|--------|
| 1 | **基本信息** | 公司概况、行业分类、上市日期 | Tushare `stock_basic`（≥120 分）∥ akshare |
| 2 | **财务报告** | ROE、EPS、扣非净利润、营收 | Tushare `fina_indicator`（≥2000 分）∥ akshare |
| 3 | **实时行情** | 开高低收、成交量、成交额 | Tushare `daily` + 腾讯行情兜底 |
| 4 | **十大股东** | 前十大流通股东、持股比例 | Tushare `top10_floatholders`（≥2000 分）∥ akshare |
| 5 | **北向资金** | 个股北向持股/增持序列 | Tushare `hsgt_top10`（≥2000 分，仅上榜日）∥ akshare |
| 6 | **估值** | PE/PB 历史序列与分位 | Tushare `daily_basic`（≥2000 分）∥ 腾讯快照 |
| 7 | **日K线** | 历史日线数据 | Tushare `daily` ∥ baostock |
| — | **市场结构**（v0.1.3） | 申万行业、主力/融资/北向、换手、ERP | 见积分对照表；`report` 附加采集 |
| — | **宏观数据** | US 10Y 国债（ERP）等 | FRED API（`--with-macro` 扩展指标） |
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
git clone git@github.com:Veblin/invest-A-skill.git
cd invest-A-skill/code

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

> 不配置 Tushare 时，实时行情可通过腾讯免费接口获取，但财务/股东/估值历史等维度将不可用。积分与功能对应关系见下表。

### Tushare 积分与功能对照

Tushare Pro 按**积分**控制接口权限（非本项目自定义）。积分以 [Tushare 官网](https://tushare.pro/document/1?doc_id=13) 当前规则为准；下表描述 **invest-A v0.1.3** 各功能实际调用的接口及无权限时的降级行为。

#### 按积分档次：你能用到什么？

| 积分档次 | 典型获取方式 | invest-A 可用能力概览 |
|---------|-------------|----------------------|
| **无 Token** | 不配置 `TUSHARE_TOKEN` | 腾讯行情（报价）、baostock（K 线）；akshare 部分维度（受网络/反爬影响）。可生成 v3 九模块报告，但财务、估值历史、市场结构高分接口因子会标注 `[数据源不可用，该因子跳过]` |
| **≥ 120** | 注册（100）+ 完善资料（20） | 在上一档基础上解锁：`stock_basic`（基本信息）、`daily`（日 K / 近 10 日行情）。**尚不能**调用财务指标、估值历史序列、资金流向、申万行业等 2000 分接口 |
| **≥ 2000** | 社区捐助约 200 元/年（以官网为准） | **推荐档位**：七维采集 + v0.1.3 市场结构（申万行业、主力/融资/北向、换手、沪深300 ERP 等）完整覆盖；权限不足的单项仍按因子独立降级，不阻断报告 |
| **≥ 5000** | 更高捐助档位 | 本项目 **当前版本未使用** 5000 分专属接口（如 `*_vip` 全市场批量财报）；未来 Phase 可能接入 |

#### 按功能模块：接口、积分与降级

| invest-A 功能 | CLI / 报告位置 | Tushare 接口 | 官方最低积分 | 积分不足时的降级 |
|--------------|---------------|-------------|-------------|-----------------|
| **基本信息** | `collect` / 模块 1 | `stock_basic` | ≥ 120 | akshare `stock_individual_info_em`（东方财富，需直连） |
| **日 K 线** | `collect` / 模块 2、8 | `daily` | ≥ 120 | baostock `query_history_k_data_plus` |
| **实时行情** | `collect` / 模块 1 | `daily`（近 10 日） | ≥ 120 | 腾讯 `qt.gtimg.cn` |
| **财务指标** | `collect` / 模块 4 | `fina_indicator` | ≥ 2000 | akshare `stock_financial_abstract_ths` |
| **十大股东** | `collect` | `top10_floatholders` | ≥ 2000 | akshare `stock_gdfx_top_10_em` |
| **估值历史分位** | `collect` / 模块 1、4 | `daily_basic` | ≥ 2000 | 腾讯快照仅当前 PE；报告标注「历史分位不可得」 |
| **北向资金（维度）** | `collect` 北向维度 | `hsgt_top10` | ≥ 2000 | akshare `stock_hsgt_individual_em` |
| **主力资金** | `report` 模块 2、3 | `moneyflow` | ≥ 2000 | 矩阵/资金态度标注跳过 |
| **北向个股资金流** | `report` 模块 3 | `hsgt_top10` | ≥ 2000 | 回退 akshare `stock_hsgt_individual_em` |
| **融资余额变化** | `report` 模块 2、3 | `margin_detail` | ≥ 2000 | 因子标注跳过 |
| **申万行业指数** | `report` 模块 2、3、CV-5 | `index_classify` + `sw_daily` + `index_daily` | ≥ 2000 | 行业景气因子跳过；触发源 C 可能不激活 |
| **换手率分位** | `report` 模块 3 | `daily_basic` | ≥ 2000 | ERP/换手小节部分不可得 |
| **股权风险溢价 ERP** | `report` 模块 3、6 | `index_dailybasic`（沪深300 PE）+ **FRED** `DGS10` 或 akshare 中国 10Y | Tushare ≥ 2000 | 缺国债序列或对齐样本不足时 ERP 标 `partial` / 不可用 |
| **技术指标 MA/MACD** | 模块 2、6、8 | —（引擎本地计算） | 仅需 K 线 | K 线不可得时技术因子跳过 |

说明：

- **并行取证**：Tushare 与 akshare / 腾讯 / baostock 同时请求，先到先用；某一源权限不足不影响其他源。
- **市场结构**（`collect_market_structure`）在 `report` 生成 Markdown 时附加；各子因子独立写 `availability`，报告内显示 `[数据源不可用，该因子跳过]`。
- **ERP** 优先使用 `FRED_API_KEY` 的 `DGS10`；无 FRED 时回退 akshare `bond_zh_us_rate`（中国 10Y）；仍须 Tushare `index_dailybasic` 提供沪深300 PE。
- 积分与接口门槛由 Tushare 调整时，以[接口文档](https://tushare.pro/document/2)各页「积分」栏为准；可用 `invest.py diagnose` 与 `report` 输出中的 `availability` 字段自查。

#### 推荐配置组合

| 目标 | 最低配置 |
|------|---------|
| 快速试跑、只看行情与 K 线 | 无 Token 或 120 分 + baostock/腾讯 |
| 完整七维学习报告（v0.1.2 八段） | Tushare **2000** 分 + 可选 FRED |
| v0.1.3 九模块 + 动态驱动矩阵 | Tushare **2000** 分 + **FRED 或 akshare 国债**（ERP）+ 稳定国内网络（akshare 兜底） |

### CLI 命令

```bash
# 采集数据
uv run python skills/invest-A/scripts/invest.py collect 600176

# 生成 Markdown 九模块报告（默认）
uv run python skills/invest-A/scripts/invest.py report 600176

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
| **HTML 报告** | 当前工作目录或 `--outdir` | `report --emit=html`（v0.1.2 旧版模板，须显式指定） |
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
- 北向资金近 10 日净流入 +12.3 亿 [来源: Tushare hsgt_top10 / 2026-06-10]

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
uv run pytest              # 运行测试
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
