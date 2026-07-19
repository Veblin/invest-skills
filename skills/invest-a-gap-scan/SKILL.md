---
name: invest-a-gap-scan
version: "0.2.0"
description: "跳空缺口扫描 — 向上缺口 + MA60 上方 + 未回补，指数成分股池（沪深300+中证A500+科创50）"
argument-hint: "/invest-a-gap-scan [--gap-min-pct 1.5] [--gap-min-vol-ratio 1.5]"
allowed-tools: Bash, Read, Write, WebSearch, WebFetch
user-invocable: true
metadata:
  requires:
    bins: [uv, python3]
  optionalEnv:
    - TUSHARE_TOKEN
---

# invest-a-gap-scan 跳空缺口扫描

## 概述

扫描**沪深300 + 中证A500 + 科创50 成分股并集**（去重后约 700~850 只），找出同时满足：
1. 近 60 个交易日内有**向上跳空缺口**（`low[i] > high[i-1]`，幅度 ≥1%）
2. 自缺口日起至今**始终在 MA60 上方**收盘
3. 缺口形成后**最低价从未回补至上沿及以下**

## 工作流

### Phase 1 — 扫描

```bash
uv run python skills/invest-a-gap-scan/scripts/scan.py
```

超时保护（SKILL.md 运行建议）：
```bash
timeout 600 uv run python skills/invest-a-gap-scan/scripts/scan.py
```

常用参数：
```bash
# 提高缺口阈值
uv run python skills/invest-a-gap-scan/scripts/scan.py --gap-min-pct 2.0

# 要求放量缺口
uv run python skills/invest-a-gap-scan/scripts/scan.py --gap-min-vol-ratio 1.5

# 强制使用 baostock（无 token 时）
uv run python skills/invest-a-gap-scan/scripts/scan.py --source baostock

# 开发调试（只扫前 30 只）
uv run python skills/invest-a-gap-scan/scripts/scan.py --universe-limit 30

# JSON 输出
uv run python skills/invest-a-gap-scan/scripts/scan.py --json
```

| 参数 | 默认 | 说明 |
|------|------|------|
| `--universe` | `csi300,a500,star50` | 指数池（逗号分隔） |
| `--gap-min-pct` | 1.0 | 缺口幅度阈值 % |
| `--gap-lookback` | 60 | 缺口回溯交易日 |
| `--gap-min-vol-ratio` | 1.0 | 缺口日成交额 / 20 日均额下限 |
| `--min-avg-amount` | 100000000 | 20 日均额门槛（元） |
| `--min-list-days` | 120 | 最少 K 线根数 |
| `--top` | 30 | stdout 行数（md 报告全量） |
| `--source` | `auto` | `auto` / `tushare` / `baostock` |
| `--no-cache` | off | 强制刷新 |
| `--universe-limit N` | 无 | 只扫前 N 只（调试） |
| `--no-save-report` | off | 不写 reports/ |
| `--json` | off | stdout 输出 JSON |

### Phase 2 — 联动深研

对命中的标的，接 `/invest:a-stock {symbol}` 做深度基本面研究。

### Phase 3 — 合规

- 禁止买卖/仓位建议（LAW 6）
- 禁止单一目标价
- 用「缺口区间 / 上沿回补位」等中性表述
- 输出末尾标注"仅供研究，不构成投资建议"

## 输出

**stdout**：摘要 + 命中表格（缺口幅度降序）

**详文档** `reports/gap-scan/{yyyymmdd}.md`：全量命中表、逐股简析、排除/未命中统计、运行参数

## 数据源

- 主路径：Tushare Pro（前复权自算，需 TUSHARE_TOKEN）
- 兜底：baostock（`adjustflag="2"` 前复权）
- 指数成分股：akshare → tushare index_weight → sina
