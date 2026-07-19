---

name: invest-a-limit-up
version: "0.2.0"
description: "A股涨停板全市场扫描 + 交互式深挖 — 盘面宽度 / 板块轮动 / 涨停归因"
argument-hint: "/invest-a-limit-up | /invest-a-limit-up --sector 半导体 | /invest-a-limit-up 600176 深挖"
allowed-tools: Bash, Read, Write, WebSearch, WebFetch
user-invocable: true
metadata:
  requires:
    bins: [uv, python3]
  optionalEnv:
    - TUSHARE_TOKEN
---

# invest-a-limit-up 涨停扫描 Skill

## 概述

两阶段工作流：**Phase 1** 全市场涨停扫描 + 市场宽度简报 → 交互筛选 → **Phase 2** 选定标的深挖归因。

数据源：akshare `stock_zt_pool_em`（L1 始终可用）+ Tushare Pro（L2 有 Token 时增强过滤）。

## Phase 1 — 扫描呈现

```bash
uv run python skills/invest-a-limit-up/scripts/scan.py --days 10
```

Claude 读取输出后，以简报形式呈现并引导用户筛选：

1. **市场宽度概览**：每日涨停趋势、连板分布、行业热度 Top 10
2. **连板龙头**（≥3板）：封板时间 + 封板资金
3. **涨停股票列表**：代码|名称|连板|行业|市值|换手|封板|炸板
4. **交互提问**：
   - "对哪个行业感兴趣？"
   - "想看连板股（≥2板）还是首板？"
   - "有具体标的代码想深挖吗？"

### 筛选子命令

```bash
uv run python skills/invest-a-limit-up/scripts/scan.py --sector 半导体 --min-board 2 --max-rows 20
uv run python skills/invest-a-limit-up/scripts/scan.py --quality-filter   # 六维质量过滤
uv run python skills/invest-a-limit-up/scripts/scan.py --min-price 8      # 传入质量参数亦自动启用
# --include-st 仅在质量过滤已启用时保留 ST，不单独触发过滤
```

### 理解盘面宽度

从简报中提取：
- **涨停家数趋势**：与近期对比，今日是升温还是降温
- **行业集中度**：涨停集中在某几个行业 → 板块轮动方向
- **连板高度**：最高连板数 → 市场风险偏好
- **封板质量**：早盘封板比例 + 炸板率 → 资金信心

## Phase 2 — 深挖归因

用户选择标的后，运行 invest-a-stock 采集深挖：

```bash
uv run python skills/invest-a-stock/scripts/invest.py collect SYMBOL --with-news-pack
```

### 涨停归因卡结构

```
## {symbol} {name} — 涨停归因卡

### [涨停事实]
- 日期/连板数/封板时间/封板资金/炸板次数/换手率

### [事件催化]
- 涨停日附近的公告/新闻（从 events/news 维度提取）
- WebSearch 查询 "{name} 涨停 {date}" 获取盘后解读

### [行业背景]
- 同行业涨停股数量 → 板块效应 or 独立行情
- 从 Phase 1 扫描结果中提取同行业标的

### [技术前兆]
- 涨停前 K 线形态、均线排列、量价关系
- 参考 invest-a-stock technical.py 分析

### [资金行为]
- 北向/主力/融资融券行为
- 参考 invest-a-stock participant_scan.py 分析

### [封板质量]
- 封板时间（越早越强）+ 炸板次数 + 封流比

### [归因判断]
- 最可能的主导因子
- [证据强度: ✅ 强 🌐 多源 🕐 近 30 日 ✓✓ 跨源可验证]
  （按实际：数据可靠性 ✅/⚠️/❓ · 来源 🌐/📡/🔮 · 时效 🕐/📅/🗄️ · 交叉 ✓✓/✓✗/—）
```

**不构成投资建议。** 涨停归因仅提供逻辑链路的可能性分析，不做买卖/仓位建议（LAW 6）。

## 交互关键词

| 用户输入 | Claude 行为 |
|----------|------------|
| `/invest-a-limit-up` | 全扫描 + 简报 + 引导提问 |
| `/invest-a-limit-up --sector 半导体` | CLI 加 `--sector 半导体` 重新扫描 |
| `连板/2板/3板` | CLI 加 `--min-board N` |
| `深挖 SYMBOL` | 运行 invest-a-stock collect + 输出归因卡 |
| `对比 SYMBOL_A SYMBOL_B` | 两个标的涨停归因对比 |
| `今日/昨天` | 指定日期查看昨日涨停池表现 |
| `行业轮动` | 分析 10 日行业热度变化趋势 |

## 数据源与降级

| 数据层 | 来源 | 可用性 |
|--------|------|--------|
| L1 涨停池 | akshare `stock_zt_pool_em` | 始终可用（EastMoney push2 proxy bypass） |
| L1 封板质量 | akshare（封板时间/封板资金/炸板次数） | 同上 |
| L2 交易日历 | Tushare `trade_cal` | 有 Token 时；降级到自然日覆盖 |
| L2 市场分类 | Tushare `stock_basic` | 有 Token 时；降级跳过 |
| L2 股价过滤 | Tushare `daily`（优先）/ L1 涨停池 `最新价` 回退 | 有阈值但无价格时剔除 |
| 题材聚合 | Claude WebSearch（涨停日公告/新闻） | 替代已不可用的 pywencai |

## 限制

- akshare 涨停数据仅保留 ~15 个交易日，建议每日运行以建立历史归档
- 题材聚合依赖 Claude WebSearch 质量，不如 pywencai 的机械标签完整
- Tushare Token 缺失时，股价/ST/市场分类过滤不可用（行业+连板+封板质量过滤仍可用）
