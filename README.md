# invest-A — A股/港股投资学习 Skill

> **学习工具，非决策工具。**
>
> 覆盖个股调研七大维度，产出带数据引用的 Markdown 学习报告。
> 每条事实标注来源，每个判断标注依据，每个推测明确声明。
> 不提供买卖建议、目标价或仓位建议。

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)

---

## 为什么有这个 Skill？

投资分析需要多维度信息，但散户面临两个困境：
- **信息过载** — 不知道看什么、信哪个
- **缺乏框架** — 不知道如何系统地分析一家公司

invest-A 解决的是"学习方法论"的问题，不是"替你决策"的问题。它帮你采集财务/行情/产业链/舆情/机构数据，标注来源和可信度，解释指标含义，但最终判断由你做出。

---

## 七大维度

| # | 维度 | 采集内容 |
|---|------|---------|
| 1 | **基本信息** | 公司概况、股权结构、治理风险信号（质押/解禁/商誉） |
| 2 | **财务报告** | 三张报表关键指标 + 盈利能力/成长性/现金流/偿债能力 |
| 3 | **行业产业链** | 申万行业分类、上下游、竞争对手、供应链地位 |
| 4 | **估值/市场状态** | PE/PB/PS 估值 + K线技术状态描述（非交易信号） |
| 5 | **机构分析师** | 十大股东、机构调研、北向资金 |
| 6 | **情绪舆情** | 股吧情绪、新闻报道、Web 搜索交叉验证 |
| 6.5 | **Web 搜索** | 产业链/舆情/机构/催化剂 补充搜索 + 可信度分级 |
| 7 | **宏观政策** | 全球宏观快照 + 中国宏观专题 + 商品/汇率 |

---

## 快速开始

### 前置要求

- **Python 3.12+**
- **[uv](https://docs.astral.sh/uv/)**（推荐）或 pip
- **Claude Code** / **Gemini CLI** / **Codex** / **Cursor** 等 [Agent Skills](https://agentskills.io) 兼容运行时

### 安装

```bash
git clone https://github.com/veblin/invest-A.git
cd invest-A

# 创建隔离虚拟环境（不污染系统 Python）
uv sync

# 环境诊断
uv run python -m skills.invest-A.scripts.lib.env_check
```

### 配置 API Key（全部可选）

```bash
cp .env.example .env
# 编辑 .env 填入可选 Token
```

不配置任何 Token 也可使用免费数据源（akshare/efinance/yfinance/WebSearch），报告会标注降级情况。

### 在 Claude Code 中使用

```bash
# 安装 Skill
npx skills add . -g -y

# A 股分析
/invest-A 600519

# 港股分析
/invest-A 00700

# 对比分析
/invest-A 600519 --compare 000858

# 宏观快报
/invest-A macro

# 学习模式
/invest-A learn ROE
```

### 命令行测试

```bash
# 测试数据管道
uv run python -m skills.invest-A.scripts.data_pipeline --test 600519

# 环境诊断
uv run python -m skills.invest-A.scripts.lib.env_check
```

---

## 9 条输出法则（LAWs）

所有输出由 9 条法则约束，违反即为 Bug：

| LAW | 内容 |
|-----|------|
| 1 | 每条分析论述必须引用数据来源（接口名/原始文档） |
| 2 | 使用统一七维度结构模板 |
| 3 | 区分"事实陈述"与"分析判断" |
| 4 | 风险提示必须出现在报告首部和尾部 |
| 5 | 单一数据源结论必须标注不确定性 |
| 6 | 禁止买卖建议、目标价、仓位建议、操作建议 |
| 7 | 关键财务数据标注原始来源和时间戳 |
| 8 | 每个维度末尾包含"待独立验证项"清单 |
| 9 | 无数据来源的分析不输出 |

---

## 平台兼容性

| 功能 | Claude Code | Gemini CLI | Codex | Cursor | Copilot |
|------|-------------|------------|-------|--------|---------|
| A 股分析 | ✅ | ✅ | ✅ | ✅ | ✅ |
| 港股分析 | ✅ | ✅ | ✅ | ✅ | ✅ |
| 对比分析 | ✅ | ⚠️ | ⚠️ | ⚠️ | ⚠️ |
| 宏观仪表板 | ✅ | ✅ | ⚠️ | ⚠️ | ⚠️ |
| 教学模式 | ✅ | ✅ | ✅ | ✅ | ✅ |

---

## 项目结构

```
invest-A/
  skills/invest-A/          ← Agent Skills 标准目录
    SKILL.md                ← 运行时规格（Canonical Skill Definition）
    scripts/                ← Python 数据采集引擎
      data_pipeline.py      ← 主编排（collect_all, build_collection_plan）
      lib/                  ← 16 个采集模块
    knowledge/              ← 投资知识库（7 篇 Markdown）
    strategies/             ← 分析方法库（9 个 YAML）
    config/                 ← 数据源可信度/维度权重/交叉验证规则
    references/             ← 参考文档
    docs/                   ← 设计与执行文档
  .claude-plugin/           ← Claude Code 插件注册
  .agents/                  ← Agent Skills 通用注册
  .github/                  ← CI/CD + Issue/PR 模板
  pyproject.toml            ← uv 依赖管理
  README.md                 ← 本文件
  AGENTS.md                 ← AI 协作规则
  CONFIGURATION.md          ← 配置指南
  CHANGELOG.md              ← 版本历史
  CONTRIBUTORS.md           ← 贡献者
  LICENSE                   ← MIT
```

---

## 开发

```bash
uv sync                  # 安装依赖
uv run pytest            # 运行测试
```

详细配置见 [CONFIGURATION.md](CONFIGURATION.md)。

---

## 不做

- ❌ 买卖建议 / 目标价预测
- ❌ 投资价值综合评分
- ❌ 雪球/微信公众号爬虫（违反 ToS）
- ❌ 社交功能入口
- ❌ 定时推送（个人 Cron 除外）

---

## License

MIT — 详见 [LICENSE](LICENSE)。

---

## Inspired by

- [last30days-skill](https://github.com/mvanhorn/last30days-skill) — Agent Skills 发布模式、`uv` 虚拟环境隔离、多平台 metadata 结构
