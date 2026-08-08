# invest-skills

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-blue.svg" alt="License: MIT" /></a>
  <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.12+-blue.svg" alt="Python 3.12+" /></a>
  <a href="https://github.com/Veblin/invest-skills/actions/workflows/validate.yml"><img src="https://img.shields.io/github/actions/workflow/status/Veblin/invest-skills/validate.yml?label=validate" alt="Validate" /></a>
  <a href="https://github.com/Veblin/invest-skills/releases"><img src="https://img.shields.io/github/v/release/Veblin/invest-skills?include_prereleases&label=v0.2.4" alt="Release" /></a>
</p>

A 股投研技能集，面向 **Claude Code** 和 **Hermes Agent**。输入代码，自动采集多维数据，产出带来源追溯的结构化研究备忘录。学习工具，非决策工具。

---

## 项目故事

第一次让 AI 帮我写个股研究时，它把 NAV 和均线一比，写下"约 15%"，手算的。后来引擎算出来是 15.36%，差得不多，但那一刻我明白：让 AI 在脑内做数学，就像我在开盘时间凭感觉下单，都是赌。项目第一条铁律由此诞生——P0：AI 禁止做数学计算，一切数字由 Python 引擎产出，AI 只负责引用，不负责算。

类似的坑一个接一个。看份额流时我盯着打印出的最近几行，断言"7/30 是峰值"，真实峰值在 7/20——子集会骗人，AI 也会跟着被骗，于是"极值断言必须全量核验"写进规则。AI 检索不到某数据，就推断"数据不存在"，后来被证伪，于是事实边界 §2.3 规定：检索无结果只能标"公开不可独立验证"，不得推断为不存在。588000 报告审计一口气查出三个错——子集断言、摘要归因、目视计数——全部进了"已知错误实例（不得重犯）"清单，并演化成发报告前的三层复检流程。

慢慢地我发现，这个项目收藏的不是代码，是我的学费。多轮 /code-review 把反复出现的缺陷固化成开发规则 D1-D13；连措辞都被约束——"崩盘"不许说，要说"剧烈回调"+条件描述。AI 的自由发挥太贵了，贵在每次都要重新交一遍学费。v0.2.4 是个转折：方法论引擎 R1-R12h 让 AI 不再"采集数据→自由发挥"，而是先匹配该用哪套框架——行业景气卡、成长股分流、趋势价值双路径——再按框架产出，像一个有 SOP 的分析师，而不是一个兴奋的实习生。

这个项目是学习工具，不是决策工具。它不会替我赚钱，但它把我踩过的每一个坑固化成规则，让 AI 下次绕开。坑只踩一次，剩下的交给引擎。

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
/invest-a-pulse                     # 市场情绪全景（杠杆/广度/情绪/资金/估值）
/invest-a-limit-up                  # 涨停扫描（已废弃，核心功能合入 pulse）
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

# 市场情绪
uv run python skills/invest-a-stock/scripts/invest.py market-status       # 当日市场快照
uv run python skills/invest-a-stock/scripts/invest.py market-status --save  # 采集并保存
uv run python skills/invest-a-stock/scripts/invest.py market-status --industry  # 行业景气状态卡（R5）

# 涨停扫描
uv run python skills/invest-a-limit-up/scripts/scan.py --quality-filter
uv run python skills/invest-a-limit-up/scripts/scan.py --sector 半导体

# 跳空缺口扫描
uv run python skills/invest-a-gap-scan/scripts/scan.py
uv run python skills/invest-a-gap-scan/scripts/scan.py --gap-min-pct 2.0
uv run python skills/invest-a-gap-scan/scripts/scan.py --gap-min-vol-ratio 1.5

# ETF 研究
uv run python skills/invest-a-etf/scripts/etf.py report 563300
uv run python skills/invest-a-etf/scripts/etf.py report 588000 --history --playbook   # 历史深度 + 情景预案（R11）
uv run python skills/invest-a-etf/scripts/etf.py diagnose
uv run python skills/invest-a-etf/scripts/etf.py industry-pe              # 31 行业 PE 排名
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
| K 线 | 日线 + MA/MACD/RSI（统一前复权） | Tushare(adj_factor 自算) ∥ akshare ∥ baostock(无 Token 时兜底) [+tickflow 可选] |
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

### 方法论引擎（v0.2.4）

报告不再一套模板打天下：R1-R12h 先做框架匹配——行业景气状态卡（R5）、行业关键因素（R4）、成长股四分类分流（R7）、趋势/价值双路径分流（R12g）、数据源降级链（R12h）。AI 先确认「该用哪套框架」，再按框架产出，最后以事实边界 §2.3 约束（禁止猜测/推断，冲突并列不裁决）。

---

## 项目结构

```
skills/
  lib/                     ← 共用层（nums/stats/technical/cache/data_bridge）
    references/            ← 共享规范（report-conventions.md）
  invest-a-stock/          ← 个股研究
    SKILL.md               ← 核心规格
    references/            ← 专项（modules/financials/sentiment/game-theory）
    scripts/
      invest.py            ← CLI（19+ 子命令，含 market-status）
      valuation_calc.py    ← 科学估值
      lib/                 ← collector/store/valuation/risk_scanner/...
    tests/
  invest-a-etf/            ← ETF 研究（数据层供 journal 共用）
  invest-a-journal/        ← 交易方案评估
    scripts/lib/
      market_microstructure.py  ← 市场微观结构管道（17 指标）
  invest-a-pulse/          ← 市场情绪全景（杠杆/广度/情绪/资金/估值 + 行业轮动/跷跷板观察）
  invest-a-limit-up/       ← 涨停数据管道（已废弃用户入口）
  invest-a-gap-scan/       ← 跳空缺口扫描
.claude-plugin/            ← Claude Code 插件
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
