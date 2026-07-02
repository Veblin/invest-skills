# 数据源快速参考

## A 股数据源优先级

```
有 Token: Tushare ∥ akshare → 腾讯/baostock → 标注不可得
无 Token: akshare → 腾讯/baostock → 标注不可得
```

| 数据源 | 接口 | 稳定性 | 需要Token | 说明 |
|--------|------|--------|-----------|------|
| **Tushare Pro** | `stock_basic` / `daily` / `fina_indicator` / `top10_floatholders` / `moneyflow` / `sw_daily` | ★★★★ | 是 | **主数据源**；2000 分解锁财务/资金/普通指数；**`sw_daily` 申万行业日线需 5000 分**（[文档](https://tushare.pro/document/2?doc_id=327)），不足时回退 akshare `index_hist_sw` |
| **akshare** | `stock_financial_abstract_ths` / `stock_hsgt_individual_em` / `stock_zh_a_hist` / `stock_individual_info_em` | ★★ | 否 | 多源验证用。⚠️ `stock_zh_a_hist` 和 `stock_individual_info_em` 因东方财富反爬在多数环境下不可用 |
| **腾讯行情** | `qt.gtimg.cn` HTTP | ★★★★ | 否 | 实时报价（价格/成交量/PE/市值） |
| **baostock** | `query_history_k_data_plus` | ★★★★ | 否 | K 线历史数据，免费稳定，需网络直连 |
| **TickFlow** | `TickFlow.free().klines.get` | ★★★★ | 否 | 第四 K 线源，独立数据管道（非东方财富），`TickFlow.free()` 零配置

## TickFlow 使用说明

**TickFlow** ([GitHub 3.4k stars](https://github.com/weilinxie/tickflow)) 是由 efinance 作者开发的免费 A 股行情库，提供独立数据管道（非东方财富爬虫），用于 K 线交叉验证。

### 用法

```python
import tickflow as tf
client = tf.TickFlow.free()                          # 零配置，无需注册
df = client.klines.get("600519.SH",                   # 代码格式: Tushare 风格
    period="1d",
    start_time=start_ms, end_time=end_ms,             # 毫秒时间戳
    adjust="forward",                                 # 前复权（默认）
    as_dataframe=True)
```

### Baostock 对比

| 特性 | Baostock | TickFlow |
|------|----------|----------|
| 注册 | 无需 | 无需（free tier） |
| 数据管道 | baostock.com 直连 | tickflow.org API |
| 代码格式 | `sh.600176` / `sz.000001` | `600176.SH` / `000001.SZ` |
| 时间参数 | `YYYY-MM-DD` 字符串 | 毫秒级 Unix 时间戳 |
| 复权支持 | 前/后/不复权（adjustflag=3） | forward/backward/none |
| 并发安全 | 需 `_BAOSTOCK_LOCK` 串行化 | 线程安全 |

### 交叉验证价值

因 TickFlow 使用独立数据管道，与 akshare（东方财富）不同源，两者 K 线数据出现系统性偏差的概率极低。若 TickFlow 与 akshare/baostock 同时返回一致数据，可视为高可信度信号。

## 代理 / VPN 问题（Clash、V2Ray 等）

大陆用户常开 VPN，国内数据源（东方财富、腾讯行情、baostock）应**直连**，走代理会失败或被识别为境外 IP。

### 检测与提示（不强制绕过）

`lib/proxy.py` 检测 env 代理与 macOS 系统代理。`collect` / `report` 启动时若检测到代理，会提示将以下域名加入 Clash **DIRECT** 规则：

```yaml
rules:
  - DOMAIN-SUFFIX,eastmoney.com,DIRECT
  - DOMAIN-SUFFIX,gtimg.cn,DIRECT
  - DOMAIN-SUFFIX,baostock.com,DIRECT
  - DOMAIN-SUFFIX,tickflow.org,DIRECT
  - MATCH,PROXY
```

运行 `invest.py diagnose` 可查看 `proxy_detected` 与 `clash_rules_hint`。

Tushare 客户端使用 `trust_env=False` 并显式传入代理配置，与 akshare 并行采集互不干扰。

### Clash TUN 模式

TUN 在网卡层劫持流量，需在 Clash 规则中将国内金融域名设为 **DIRECT**，或采集时暂时关闭 TUN / 使用「规则模式」而非「全局模式」。

## 待接入数据源

以下数据源已规划但尚未接入，详见 `docs/roadmap.md`：
- **yfinance** — 美股/港股数据，稳定性 ★★

## Web 搜索可信度分级

| 标签 | 源类型 | 示例 |
|------|--------|------|
| 🔵 official | 官方确认 | cninfo.com.cn 公告、互动易回复、交易所公告 |
| 🟡 analyst | 分析师/媒体 | 郭明錤、证券时报、财联社、券商研报 |
| 🔴 rumor | 市场传闻 | 雪球帖子、股吧、未署名消息 |
| ⚪ unknown | 无法判断 | 未识别域名/来源 |

## 常见降级场景

| 场景 | 表现 | 应对 |
|------|------|------|
| Tushare Token 未配置或无效 | `is_tushare_available()` 返回 False | 静默跳过，降级至 akshare |
| Tushare 配额耗尽 | API 返回 code=-2001 | 降级至 akshare |
| Tushare 接口权限不足（如 `sw_daily` 40203） | 2000 分无法调 5000 分接口 | 申万行业指数回退 **akshare `index_hist_sw`**；其他因子标注跳过 |
| akshare 网络不通 | `requests.ConnectionError` | 降级至腾讯行情（仅行情维度） |
| 腾讯行情不可用 | 请求超时 | 标注"行情数据不可得" |

## Tushare 积分要点（invest-A 常用）

| 接口 | 最低积分 | 说明 | 积分不足时 |
|------|---------|------|-----------|
| `daily` / `stock_basic` | 120 | 行情、基本信息 | — |
| `fina_indicator` / `moneyflow` / `margin_detail` / `index_daily` | 2000 | 财务、资金、普通指数 | 因子跳过 |
| `index_classify` | 2000 | 申万分类（**不含**行业日线） | 因子跳过 |
| **`forecast`** | **2000** | **业绩预告（公司自披露）** | 降级至 akshare → 跳过 |
| `index_dailybasic` | 4000 | 沪深300 PE（ERP） | 部分可得/标注 partial |
| **`sw_daily`** | **5000** | 申万行业日线 | 降级至 akshare `index_hist_sw` |
| `opt_daily` | 5000 | 50ETF 期权（认沽认购比） | 因子跳过 |
| **`report_rc`** | **10000**（特色大数据） | **研报评级+目标价+盈利预测** | 降级至 forecast → akshare → 跳过 |

> v0.1.4 起 `collect_research()` 按此表顺序降级（高阶成功则跳过低阶 API）：`report_rc(10000) → forecast(2000) → akshare → 跳过`。
> 默认 `collect`/`report` **不**包含 `research` 维度；需显式 `--dims=...,research`。
> 完整对照见项目根目录 [CONFIGURATION.md](../../../CONFIGURATION.md)。
