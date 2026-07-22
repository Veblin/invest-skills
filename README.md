# invest-skills

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-blue.svg" alt="License: MIT" /></a>
  <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.12+-blue.svg" alt="Python 3.12+" /></a>
  <a href="https://github.com/Veblin/invest-skills/actions/workflows/validate.yml"><img src="https://img.shields.io/github/actions/workflow/status/Veblin/invest-skills/validate.yml?label=validate" alt="Validate" /></a>
  <a href="https://github.com/Veblin/invest-skills/releases"><img src="https://img.shields.io/github/v/release/Veblin/invest-skills?include_prereleases&label=v0.2.0" alt="Release" /></a>
</p>

A 股投研技能集，面向 **Claude Code** 和 **Hermes Agent**。输入代码，自动采集多维数据，产出带来源追溯的结构化研究备忘录。学习工具，非决策工具。

---

## 安装

```bash
git clone https://github.com/Veblin/invest-skills.git && cd invest-skills
uv sync
```

配置 Token（至少一个）：

```bash
cp .env.example .env   # 编辑填入 TUSHARE_TOKEN 等
```

| Key | 作用 | 获取 |
|-----|------|------|
| `TUSHARE_TOKEN` | 财务/估值/资金/股东 | [tushare.pro](https://tushare.pro) 注册即送 |
| `FRED_API_KEY` | 美国 10Y 国债（DCF WACC） | [fred.stlouisfed.org](https://fred.stlouisfed.org/docs/api/api_key.html) 免费 |
| `TAVILY_API_KEY` | 新闻搜索补充 | [tavily.com](https://tavily.com) 免费 |

```bash
uv run python skills/invest-a-stock/scripts/invest.py diagnose   # 验证
```

Tushare 积分档位与功能对照见 [CONFIGURATION.md](CONFIGURATION.md)。

---

## 使用

### Claude Code

```
/plugin marketplace add Veblin/invest-skills
```

```
/invest-a-stock 600176              # 单标的研究（多 Agent 并行）
/invest-a-stock 600176 --with-macro # 含宏观情景
/invest-a-etf 563300                # ETF 结构化研究
/invest-a-journal                   # 交易方案四维评估（ETF/个股）
/invest-a-limit-up                  # 涨停扫描
/invest-a-gap-scan                  # 跳空缺口扫描
```

> **命名约定**：用户 slash 一律连字符（`/invest-a-*`）。Claude 插件 marketplace 包名可保留冒号（`invest:a-stock`），二者不是同一层。

### Hermes Agent

安装 `Veblin/invest-skills` 插件后，直接用自然语言调用：
```
用 invest-a-stock 研究 600176
```

### 命令行

```bash
# 个股研究
uv run python skills/invest-a-stock/scripts/invest.py report 600176
uv run python skills/invest-a-stock/scripts/invest.py report 600176 --with-macro
uv run python skills/invest-a-stock/scripts/invest.py value 600176       # 科学估值
uv run python skills/invest-a-stock/scripts/invest.py rigor 600176 --verify-all
uv run python skills/invest-a-stock/scripts/invest.py check 600176       # 质地检查

# 对比 / 回溯
uv run python skills/invest-a-stock/scripts/invest.py compare 600176 000858
uv run python skills/invest-a-stock/scripts/invest.py diff 600176

# 涨停扫描
uv run python skills/invest-a-limit-up/scripts/scan.py --quality-filter
uv run python skills/invest-a-limit-up/scripts/scan.py --sector 半导体

# 跳空缺口扫描
uv run python skills/invest-a-gap-scan/scripts/scan.py
uv run python skills/invest-a-gap-scan/scripts/scan.py --gap-min-pct 2.0
uv run python skills/invest-a-gap-scan/scripts/scan.py --gap-min-vol-ratio 1.5

# ETF 研究
uv run python skills/invest-a-etf/scripts/etf.py report 563300
uv run python skills/invest-a-etf/scripts/etf.py diagnose
```

---

## 多 Agent 并行分析

`/invest-a-stock` 默认两阶段多 Agent 架构：

```
Phase 1: 并行采集（3 Agent）
  Collector A (Tushare) ∥ Collector B (akshare交叉) ∥ Collector C (股东/研报)
  → merge + 交叉验证，差异 >20% 触发 tie-breaker

Phase 2: 并行分析（4 Agent）
  生意质量 ∥ 财务估值 ∥ 行业竞争 ∥ 风险治理

Phase 3: 主编合成 → .md 报告
```

模板见 [references/agent-prompts.md](skills/invest-a-stock/references/agent-prompts.md)。

---

## 数据

多源并行采集，单源失败不阻塞，差异标注跨源分歧。

| 维度 | 内容 | 源 |
|------|------|------|
| 基本信息 | 公司概况、行业 | Tushare ∥ akshare |
| 财务 | ROE/EPS/毛利率/OCF/杜邦 | Tushare ∥ akshare |
| 行情 | OHLCV | Tushare ∥ 腾讯 |
| 估值 | PE/PB 序列、分位、PE Band | Tushare |
| K 线 | 日线 + MA/MACD/RSI | Tushare ∥ baostock ∥ TickFlow |
| 股东 | 十大流通股东 + 增减持 | Tushare ∥ akshare |
| 资金 | 北向/主力/融资/融券 | Tushare ∥ akshare |
| 市场 | 行业指数、ERP、PCR | Tushare + FRED |

结果自动存入 SQLite（`~/.local/share/investment/research.db`），支持历史回溯。详见 [CONFIGURATION.md](CONFIGURATION.md)。

---

## 输出

16 条法则约束（[SKILL.md](skills/invest-a-stock/SKILL.md)），核心：

- 每条事实标注来源，每条判断标注依据
- 多源交叉验证，单源标注不确定性
- 禁止买卖建议；允许多情景估值（须假设前提 + 概率 + 免责）
- Bull/Bear 须含数值场景化传导链
- 左/右概率并列，禁止单一方向结论

---

## 项目结构

```
skills/
  invest-a-stock/         ← 个股研究
    SKILL.md              ← 核心规格
    references/           ← 专项（modules/financials/sentiment/game-theory）
    scripts/
      invest.py           ← CLI（19 子命令）
      valuation_calc.py   ← 科学估值
      lib/                ← collector/render*/store/valuation/risk_scanner/...
    tests/
  invest-a-etf/           ← ETF 研究（数据层供 journal 共用）
  invest-a-journal/       ← 交易方案评估
  invest-a-limit-up/      ← 涨停扫描
  invest-a-gap-scan/      ← 跳空缺口扫描
.claude-plugin/           ← Claude Code 插件
```

---

## 开发

```bash
uv sync && uv run pytest
bash scripts/bump-version.sh X.Y.Z
```

提交前确保测试通过，无 API Key 泄露。

---

MIT · 本地运行 · 不跟踪不上报
