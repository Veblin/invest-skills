# invest-A — A股投资学习 Skill

> **学习工具，非决策工具。**
>
> 通过 Tushare Pro + FRED 采集六大维度数据，产出带来源追溯的 Markdown 学习报告。每条事实标注来源，每个判断标注依据，不提供买卖建议。

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)

---

## 为什么有这个 Skill？

投资分析需要多维度信息，但散户面临两个困境：
- **信息过载** — 不知道看什么、信哪个
- **缺乏框架** — 不知道如何系统地分析一家公司

invest-A 解决的是"学习方法论"的问题。它帮你采集财务/行情/产业链/机构数据，标注来源和可信度，解释指标含义，但**最终判断由你做出**。

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

---

## 快速开始

### 前置要求

- **Python 3.12+**
- **[uv](https://docs.astral.sh/uv/)**（依赖管理）
- **Claude Code** 或兼容的 Agent Skills 运行时

### 安装

```bash
git clone git@github.com:Veblin/claude-invest-A.git
cd invest-A

# 安装依赖
uv sync

# 检查数据源
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

### 在 Claude Code 中使用

```bash
/invest-A 600176
/invest-A 600176 --with-macro
/invest-A 600176 --deep
/invest-A 600176 --compare 000858
```

---

## 9 条输出法则（LAWs）

所有输出由 9 条法则约束：

| LAW | 内容 |
|-----|------|
| 1 | 每条分析论述必须引用数据来源 |
| 2 | 使用统一七维度结构模板 |
| 3 | 区分"事实陈述"与"分析判断" |
| 4 | 风险提示出现首部和尾部 |
| 5 | 单源数据标注不确定性，多源交叉验证优先 |
| 6 | 禁止买卖建议、目标价、仓位建议 |
| 7 | 每个数字标注可审查的追溯路径 |
| 8 | 每个维度末尾要求"🔍 待独立验证项" |
| 9 | 无数据源支撑的分析不输出 |

---

## 项目结构

```
invest-A/
  skills/invest-A/              ← Agent Skills 标准目录
    SKILL.md                    ← 运行时指令（~115行）
    scripts/
      invest.py                 ← CLI 单入口
      lib/
        env.py                  ← 配置管理器
        collector.py            ← 数据采集
        render.py               ← 报告渲染
        tushare_client.py       ← Tushare HTTP 客户端
        store.py                ← SQLite 持久化
        tushare_client.py       ← Tushare HTTP 客户端
    tests/
      conftest.py
      test_env.py               ← 7 个测试
    references/
      source-guide.md           ← 数据源参考
  .claude-plugin/               ← Claude Code 插件注册
  .agents/                      ← Agent Skills 通用注册
  .github/                      ← CI/workflows/issue templates
  hooks/                        ← SessionStart 钩子
  pyproject.toml                ← uv 依赖管理
```

---

## 开发

```bash
uv sync                    # 安装依赖
uv run pytest              # 运行测试
```

`archive/` 目录（repo 外）保存了 v0.2 旧代码，仅用于历史追溯，不被当前版本引用。

---

## License

MIT — 详见 [LICENSE](LICENSE)。
