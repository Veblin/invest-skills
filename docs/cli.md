# 命令行参考

> 面向开发者与命令行用户。**大多数用户通过 skill 对话即可获得全部能力**，无需直接调用脚本；本文档是脚本级操作的完整参考。

## 入口脚本

| 能力 | 脚本 | 子命令 |
|:---|:---|:---|
| 个股研究 | `skills/invest-a-stock/scripts/invest.py` | 27 个 |
| ETF 研究 | `skills/invest-a-etf/scripts/etf.py` | 4 个 |
| 跳空缺口扫描 | `skills/invest-a-gap-scan/scripts/scan.py` | 参数式 |

统一调用前缀（**必须用 `uv run python`**，确保从 `.venv` 加载依赖）：

```bash
uv run python skills/invest-a-stock/scripts/invest.py <子命令> <symbol> [--flags]
```

## 通用参数（invest.py）

| 参数 | 作用 |
|:---|:---|
| `--mode {brief,full,concise}` | 报告模式：简报 / 完整九模块 / 对话精简 |
| `--plan PLAN` | JSON 采集计划文件路径 |
| `--resume` | 从上次中断的步骤继续 |
| `--save-raw` | 保存原始采集 JSON 到 `~/.local/share/investment/raw/` |

## 子命令分组（invest.py，27 个）

### 研究主线

| 子命令 | 作用 |
|:---|:---|
| `plan` | 生成采集计划（`--intent` 指定研究路径，如 `game_theory`） |
| `collect` | 采集多维度数据（`--with-news-pack` 新闻三层架构） |
| `analyze` | 分析采集结果（输出中间分析 JSON） |
| `synthesize` | 合成最终研究报告 |
| `report` | 一键生成分析报告（collect + analyze + synthesize） |
| `evidence` | 生成结构化证据表 |

### 验算与质控

| 子命令 | 作用 |
|:---|:---|
| `value` | 科学估值：多方法交叉（PE/PB/盈利收益/隐含增长/ROE-PB 匹配） |
| `rigor` | 财务验算：市值/估值/跨源交叉验证（`--verify-all`） |
| `check` | 单标的质地检查（7 指标） |
| `peer` | 行业横向对比：同行业估值与财务对比表 |
| `classify` | R1 收益驱动假设分类（研究路径分流） |
| `risk-reward` | DCF 三情景盈亏比分析 |
| `ic` | 投资委员会决策框架 |
| `audit` | 报告审计：`extract` 抽取数据点 / `verdict` 准出判决 |
| `lint` | 合规扫描：措辞/结构/证据规范 |

### 对比与回溯

| 子命令 | 作用 |
|:---|:---|
| `compare` | 双标对比 |
| `diff` | 对比两次快照变化 |
| `watchlist` | 批量标的摘要（优先 store 快照；无快照时现场采集） |
| `store` | 管理存储（`store list` 历史采集记录） |
| `etf-flow` | ETF 份额变化趋势（需先 `--save` 积累历史） |

### 市场与决策辅助

| 子命令 | 作用 |
|:---|:---|
| `market-status` | 市场微观结构快照：杠杆/广度/情绪/估值温度；`--industry` 行业景气状态卡 |
| `portfolio` | 组合风险特征（行业集中度/相关性/压力测试） |
| `thesis` | 投资假设追踪 |
| `shock` | 价格冲击插值比例（非风险中性概率） |
| `catalyst` | 催化剂日历：分红/解禁/公告前瞻事件 |
| `diagnose` | 检查数据源可用性 |

## 常用示例

### 个股研究

```bash
uv run python skills/invest-a-stock/scripts/invest.py report 600176
uv run python skills/invest-a-stock/scripts/invest.py report 600176 --with-macro   # 含宏观情景
uv run python skills/invest-a-stock/scripts/invest.py report 600176 --mode brief   # 简报模式
uv run python skills/invest-a-stock/scripts/invest.py value 600176                 # 科学估值
uv run python skills/invest-a-stock/scripts/invest.py rigor 600176 --verify-all    # 财务验算
uv run python skills/invest-a-stock/scripts/invest.py check 600176                 # 质地检查
uv run python skills/invest-a-stock/scripts/invest.py peer 600176                  # 行业对比
uv run python skills/invest-a-stock/scripts/invest.py risk-reward 600176           # 盈亏比
uv run python skills/invest-a-stock/scripts/invest.py thesis 600176                # 假设追踪
uv run python skills/invest-a-stock/scripts/invest.py catalyst 600176              # 催化剂日历
```

### 对比 / 回溯

```bash
uv run python skills/invest-a-stock/scripts/invest.py compare 600176 000858
uv run python skills/invest-a-stock/scripts/invest.py diff 600176
uv run python skills/invest-a-stock/scripts/invest.py watchlist 600176 000858
uv run python skills/invest-a-stock/scripts/invest.py store list
```

### 市场

```bash
uv run python skills/invest-a-stock/scripts/invest.py market-status               # 当日市场快照
uv run python skills/invest-a-stock/scripts/invest.py market-status --save        # 采集并保存
uv run python skills/invest-a-stock/scripts/invest.py market-status --industry    # 行业景气状态卡
```

### ETF 研究

```bash
uv run python skills/invest-a-etf/scripts/etf.py report 563300
uv run python skills/invest-a-etf/scripts/etf.py report 588000 --history --playbook  # 历史深度 + 情景预案
uv run python skills/invest-a-etf/scripts/etf.py industry-pe                        # 31 行业 PE 排名
uv run python skills/invest-a-etf/scripts/etf.py collect-weekly                     # 行业 PE 周度采集
uv run python skills/invest-a-etf/scripts/etf.py diagnose                           # 检查依赖与映射表
```

### 跳空缺口扫描

```bash
uv run python skills/invest-a-gap-scan/scripts/scan.py
uv run python skills/invest-a-gap-scan/scripts/scan.py --gap-min-pct 2.0            # 缺口幅度阈值
uv run python skills/invest-a-gap-scan/scripts/scan.py --gap-min-vol-ratio 1.5      # 量比下限
uv run python skills/invest-a-gap-scan/scripts/scan.py --universe csi300,a500       # 自定义指数池
uv run python skills/invest-a-gap-scan/scripts/scan.py --json                       # JSON 输出
```

### 报告质控

```bash
uv run python skills/invest-a-stock/scripts/invest.py lint reports/xxx.md
uv run python skills/invest-a-stock/scripts/invest.py audit reports/xxx.md extract
uv run python skills/invest-a-stock/scripts/invest.py audit reports/xxx.md verdict
```

## 存储

| 位置 | 内容 |
|:---|:---|
| `~/.local/share/investment/research.db` | SQLite 研究数据库（快照/回溯） |
| `~/.local/share/investment/raw/` | 原始采集 JSON（`--save-raw`） |
