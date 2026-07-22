---


name: invest-a-gap-scan
version: "0.2.1"
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

扫描**沪深300 + 中证A500 + 科创50 成分股并集**（去重后约 450~500 只），找出同时满足：

1. 近 60 个交易日内有**向上跳空缺口**（`low[i] > high[i-1]`，幅度 >= 1%）
2. 自缺口日起至今**收盘始终在 MA60 上方**（`close[t] >= MA60[t]` for all t >= gap_idx）
3. 缺口形成后**最低价从未回补至上沿及以下**（`min(low[gap_idx+1:]) > gap_high`）

### 缺口定义

```
  gap_high  ─────── low[i]         (缺口上沿 = 跳空日最低价)
                      |
                   gap_pct = low[i] / high[i-1] - 1
                      |
  gap_low   ─────── high[i-1]      (缺口下沿 = 前日最高价)
```

- **未回补**: 缺口形成后所有交易日的最低价均 > 缺口上沿
- **跨停牌**: 缺口前一交易日为停牌日 -> 单独列出，不参与常规排序

### 筛选漏斗

478 只成分股经过多层筛选的典型分布：

```
478 只成分股
 |-- ~200 (43%)  无缺口          -- 60 日内无 low[i] > high[i-1]
 |-- ~110 (23%)  低于阈值        -- 有缺口但幅度 <1%
 |-- ~130 (27%)  MA60 破         -- 缺口后收盘跌破 MA60 (最大淘汰项)
 |--  ~17 (4%)   缺口回补        -- 最低价回补至上沿
 |--   ~6 (1%)   低流动性        -- 20 日均额 <1 亿
 |--   ~1        获取失败
-------------------------------------
     ~5 (1%)     命中
```

## 运行

### 基本用法

```bash
uv run python skills/invest-a-gap-scan/scripts/scan.py
```

### 参数表

| 参数 | 默认 | 说明 |
|------|------|------|
| `--universe` | `csi300,a500,star50` | 指数池，逗号分隔，支持空格 |
| `--gap-min-pct` | 1.0 | 缺口幅度阈值 %。`low[i]/high[i-1]-1`，<1% 归为"低于阈值"淘汰 |
| `--gap-lookback` | 60 | 缺口回溯交易日数。只搜索最近 N 根 K 线中的缺口 |
| `--gap-min-vol-ratio` | 1.0 | 缺口日成交额 / 20 日均额下限。1.0=不过滤，>1.0=要求放量 |
| `--min-avg-amount` | 100000000 | 20 日均额门槛（元）。<1 亿归为"低流动性"排除 |
| `--min-list-days` | 60 | 最少 K 线根数。不足的标的归为"上市不足"排除 |
| `--top` | 30 | stdout 命中表格行数（md 报告始终全量） |
| `--source` | `auto` | 数据源：`auto`(优先 Tushare)/`tushare`/`baostock` |
| `--no-cache` | off | 强制刷新 K 线缓存 + 成分股缓存 |
| `--universe-limit N` | 无 | 只扫前 N 只（开发调试用） |
| `--no-save-report` | off | 不写入 `reports/gap-scan/` 目录 |
| `--json` | off | stdout 输出 JSON 格式 |

### 示例

```bash
# 提高缺口阈值
uv run python skills/invest-a-gap-scan/scripts/scan.py --gap-min-pct 2.0

# 要求放量缺口
uv run python skills/invest-a-gap-scan/scripts/scan.py --gap-min-vol-ratio 1.5

# 强制 baostock (无 Tushare token)
uv run python skills/invest-a-gap-scan/scripts/scan.py --source baostock

# 调试: 只扫前 30 只
uv run python skills/invest-a-gap-scan/scripts/scan.py --universe-limit 30

# JSON 输出 (供脚本消费)
uv run python skills/invest-a-gap-scan/scripts/scan.py --json

# 全量刷新
uv run python skills/invest-a-gap-scan/scripts/scan.py --no-cache
```

## 算法详解

### 容忍规则 (Tolerance Rule)

缺口按**从新到旧**排序，遇到第一个满足全部条件的即命中：

```
for gap in qualified_gaps (newest first):
    if MA60 从未跌破 since gap_day:
        if gap 从未回补:
            if 量比 >= gap_min_vol_ratio (如启用):
                -> 命中
            else:
                -> 继续检查更老的缺口 (量比不够)
        else:
            -> 继续检查更老的缺口 (已回补)
    else:
        -> 继续检查更老的缺口 (MA60 已破)
```

不回退到已回补的缺口。这意味着：如果最新缺口已回补但更老的缺口未回补且 MA60 完好，会命中老缺口。

### 跨停牌缺口

停牌检测基于交易日历 + 日线数据对比：某个交易日在 `trade_cal` 中存在但该标的不在 `daily_by_date` 中 -> 标记为停牌。缺口前一交易日为停牌日 -> 归入 `across_suspension_hits`，不参与常规排序。

**注意**: 无 Tushare token 时使用自然日估算交易日历（Mon-Fri），此时跳过停牌检测以避免假期误判。

### MA60 计算

使用 invest-a-stock 的 `lib.technical.sma`（60 日简单移动平均）。前 59 个位置 MA60 为 None，`_check_ma60_streak` 跳过 None 值。NaN close 触发 MA60 失败（利用 `not (NaN >= MA60)` 恒为 True 的特性）。

### 前复权 (QFQ)

- **Tushare 路径**: 原始价格 x `adj_factor / latest_adj_factor` -> 以最新日为基准的前复权价格
- **Baostock 路径**: `adjustflag="2"` 直接返回前复权价格，无需额外计算

两种路径的 QFQ 算法存在细微差异，因此缓存按数据源隔离 (`{date}/{source}/{ts_code}.pkl`)。

## 数据流

```
1. build_universe()        指数成分股并集 -> 去重 + ST/退市过滤 -> ~478 只
       |
2. create_source("auto")   有 Token -> TushareBulkSource (按日批量)
                          无 Token -> BaostockSource  (逐股查询)
       |
3. _fetch_trade_cal()      Tushare trade_cal API -> 真实交易日历
                           失败 -> 自然日估算 (Mon-Fri, 跳过停牌检测)
       |
4. kline_cache.load()      命中 -> 跳过拉取
                          未命中 -> fetch_daily_batch + build_stock_kline + cache.save
       |
5. detect_suspensions()    对比 trade_cal vs daily_by_date -> 停牌日期列表
       |
6. scan_all()              逐股: 流动性排除 -> 找缺口 -> 容忍规则 -> 命中/排除/未命中
       |
7. format_brief()          命中表 (缺口幅度降序) + 排除/未命中统计
   format_markdown_report() 详文档 -> reports/gap-scan/{yyyymmdd}.md
```

### 数据量

| 项目 | 数量 |
|------|------|
| 交易日范围 | ~180 自然日 (~120 交易日，`gap_lookback + 59` bar 确保 MA60 全覆盖) |
| 成分股 | ~478 只 (去重后) |
| K 线行数 | ~57,000 行 |
| 缓存磁盘 | ~25 MB (477 个 pickle 文件) |

## 性能

| 场景 | 耗时 | 说明 |
|------|------|------|
| 首次扫描 (Tushare) | ~4 min | 按日批量 API（~120 日），受限于 Tushare 限速 |
| 首次扫描 (Baostock) | ~5 min | 逐股查询，每只 ~0.6s，~120 日数据 |
| 同日二次扫描 | ~7s | 全部缓存命中，仅做缺口算法 |
| 部分缓存命中 | ~7s + 缺失部分拉取 | 只拉取未命中标的 |

## 输出

### stdout 摘要

```
========================================================================
  invest-a-gap-scan v0.2.0 -- 跳空缺口扫描
========================================================================
池构成: 沪深300(300) + 中证A500(500) + 科创50(50) -> 去重 478 只
数据源: baostock (前复权 adjustflag=2) (前复权)
覆盖率: 99.8% (477/478 有K线) | 命中: 5 只 | 跨停牌: 0 只
参数: 缺口>=1.0% | 回溯60日 | MA60 | 日均额>=1.00亿

排除: 获取失败 1 | 低流动性 6
未命中: 无缺口 207 | 低于阈值 111 | MA60破 131 | 缺口回补 17

| 代码 | 名称 | 指数 | 板块 | 缺口日 | 缺口% | 缺口区间 | 现价 | MA60 | MA60% | 距上沿% | 量比 | 20日均额 |
|-----|------|-----|-----|--------|------|---------|-----|------|------|--------|-----|---------|
| 300628.SZ | 亿联网络 | 300 | 创业板 | 20260715 | +4.62% | 33.96~35.53 | 36.71 | 35.64 | +3.00% | +3.32% | 2.15 | 4.40亿 |
```

### 命中表列说明

| 列 | 含义 |
|----|------|
| 代码/名称 | 标的 |
| 指数 | 所属指数 (300=沪深300, 500=A500, 50=科创50) |
| 缺口日 | 缺口形成日期 |
| 缺口% | 跳空幅度 |
| 缺口区间 | 缺口下沿~上沿 (即回补位参考区间) |
| MA60% | `(现价 - MA60) / MA60`，正值=MA60 上方 |
| 距上沿% | `(现价 - 缺口上沿) / 缺口上沿`，正值=远离缺口，负值=已回补 |
| 量比 | 缺口日成交额 / 20 日均额，>1=放量 |
| 20日均额 | 近 20 日平均日成交额 |

### 详文档

`reports/gap-scan/{yyyymmdd}.md`: 全量命中表 + 逐股简析（含催化待查提示）+ 排除/未命中统计 + 运行参数。

## 缓存

### 位置

```
~/.local/share/investment/gap_scan_cache/
|-- universe_20260719.pkl          (成分股列表, 28 KB)
|-- 20260719/                       (按扫描日期)
|   |-- tushare/                    (按数据源隔离)
|   |   |-- 000001.SZ.pkl
|   |   `-- ...
|   `-- baostock/
|       |-- 000001.SZ.pkl
|       `-- ...
`-- 20260718/                       (历史缓存, >3 天自动清理)
```

### 缓存策略

- **TTL**: 3 天 (基于文件 mtime + 目录级 `cleanup_old()`)
- **Scope**: 按日期 + 数据源隔离，tushare/baostock 缓存互不污染
- **格式**: Python pickle (pandas DataFrame)
- **强制刷新**: `--no-cache` 跳过加载，重新拉取并覆盖

## 数据源

| 来源 | 需要 | 用途 |
|------|------|------|
| Tushare Pro | `TUSHARE_TOKEN` (>=120 积分) | K 线 (批量按日) + 复权因子 + 交易日历 |
| baostock | 无 (默认兜底) | K 线 (逐股, 前复权 `adjustflag=2`) |
| akshare | 无 | 指数成分股 (`index_stock_cons`) |

### 降级策略

```
auto:
  1. 检查 TUSHARE_TOKEN -> 有 -> TushareBulkSource (快速批量)
  2. 无 -> BaostockSource (逐股兜底)

交易日历:
  1. Tushare trade_cal API -> 真实日历 + 停牌检测
  2. 失败 -> 自然日估算 (Mon-Fri) + 跳过停牌检测
```

## 排除/未命中原因

### 排除 (扫描前剔除)

| 原因 | 含义 |
|------|------|
| 获取失败 | API 拉取失败或网络错误 |
| 低流动性 | 20 日均额 < `--min-avg-amount` |
| 上市不足 | K 线根数 < `--min-list-days` |
| 数据缺失 | 缺少复权因子 (仅 Tushare 路径) |
| ST | ST 标的 (名称含 "ST") |
| 退市 | 退市标的 (名称含 "退") |

### 未命中 (扫描后无结果)

| 原因 | 含义 |
|------|------|
| 无缺口 | 回溯窗口内无 `low[i] > high[i-1]` |
| 低于阈值 | 有缺口但幅度 < `--gap-min-pct` |
| MA60破 | 缺口后某日收盘 < MA60 |
| 缺口回补 | 缺口后最低价触及或跌破缺口上沿 |
| 量比低 | 缺口日量比 < `--gap-min-vol-ratio` (仅 >1.0 时生效) |

## 联动

对命中的标的，接 `/invest-a-stock {symbol}` 做深度基本面研究。

## 合规

- 禁止买卖/仓位建议
- 禁止单一目标价
- 用「缺口区间 / 上沿回补位」等中性表述
- 输出末尾标注"仅供研究，不构成投资建议"
