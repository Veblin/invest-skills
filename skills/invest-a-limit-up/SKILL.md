---


name: invest-a-limit-up
version: "0.2.3"
description: "涨停板数据管道 — 供 market_microstructure / invest-a-pulse 调用，非用户入口"
argument-hint: "[已废弃用户入口]"
allowed-tools: Bash, Read, Write, WebSearch, WebFetch
user-invocable: false
metadata:
  requires:
    bins: [uv, python3]
  optionalEnv:
    - TUSHARE_TOKEN
---

> ⚠️ **此 Skill 已废弃用户入口。** 核心功能（涨停家数趋势、行业热度、连板分布、封板质量）已合入 `/invest-a-pulse` 市场情绪分析 Skill。
> `limit_up_scanner.py` 及 `scan.py` CLI 保留为数据管道组件，供 `market_microstructure.py` 调用。

# invest-a-limit-up — 数据管道（内部组件）

## 定位

涨停板数据管道，为以下模块提供数据源：

| 调用方 | 接口 | 用途 |
|--------|------|------|
| `market_microstructure.py` | `_fetch_limit_pools()` | 涨跌停比 + 跌停20日分位 |
| `/invest-a-pulse` | 通过 `market_microstructure` 间接调用 | 极端情绪标签 |
| `scan.py` CLI | 手动 debug / 历史数据归档 | 开发诊断 |

## 核心组件

| 组件 | 路径 | 状态 |
|------|------|:--:|
| 涨停扫描引擎 | `limit_up_scanner.py` (~856 行) | **保留** |
| CLI 工具 | `scan.py` (~172 行) | **保留**（debug 用） |
| L2 增强 | `tushare_enrich.py` | **保留** |
| 历史存储 | `limit_up_store.py` (SQLite) | **保留** |

## 数据源与降级

| 数据层 | 来源 | 可用性 |
|--------|------|--------|
| L1 涨停池 | akshare `stock_zt_pool_em` | 始终可用 |
| L1 跌停池 | akshare `stock_zt_pool_dtgc_em` | 始终可用 |
| L1 封板质量 | akshare（封板时间/封板资金/炸板次数） | 始终可用 |
| L2 交易日历 | Tushare `trade_cal` | 有 Token 时；降级到自然日覆盖 |
| L2 市场分类 | Tushare `stock_basic` | 有 Token 时；降级跳过 |

## 限制

- akshare 涨停数据仅保留 ~15 个交易日，建议通过 `market-status --save` 每日归档到 `market_snapshots` 表
- 用户入口已下线，请使用 `/invest-a-pulse` 获取市场情绪全景
