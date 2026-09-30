# 数据源快速参考

## A 股数据源优先级

```
有 Token: Tushare ∥ akshare → baostock(仅无 Token 时) → 标注不可得
无 Token: akshare ∥ baostock → 标注不可得
```

**K 线源开关（v0.2.3 起）**：tickflow **默认关闭**（`INVEST_ENABLE_TICKFLOW=1` 启用）— 免费 tier 慢且其前复权价值已由 tushare adj_factor 自算覆盖；baostock 默认 **auto**（仅无 Tushare token 时启用，`INVEST_ENABLE_BAOSTOCK=1/0` 强制启停）。

**K 线复权语义（v0.2.3 起统一前复权）**：tushare 用 `daily + adj_factor` 自算（`_q_tushare_daily_qfq`，公式与 gap-scan `qfq.py` 一致）、akshare `adjust="qfq"`、baostock `adjustflag="2"`、tickflow `adjust="forward"`。不复权价格在除权日跳变会污染 MA/BOLL/RSI 等技术指标；前复权使历史价格连续，且跨源校验不再因复权语义错配产生 divergence 噪音。

| 数据源 | 接口 | 稳定性 | 需要Token | 说明 |
|--------|------|--------|-----------|------|
| **Tushare Pro** | `stock_basic` / `daily` / `fina_indicator` / `top10_floatholders` / `moneyflow` / `sw_daily` | ★★★★ | 是 | **主数据源**；2000 分解锁财务/资金/普通指数；**`sw_daily` 申万行业日线需 5000 分**（[文档](https://tushare.pro/document/2?doc_id=327)），不足时回退 akshare `index_hist_sw`；`adj_factor` 是唯一可靠复权来源 |
| **akshare** | `stock_financial_abstract_ths` / `stock_hsgt_individual_em` / `stock_zh_a_hist` / `stock_individual_info_em` | ★★ | 否 | 多源验证 + 免费事件/股东/成分股覆盖。⚠️ `stock_zh_a_hist` 和 `stock_individual_info_em` 因东方财富反爬在多数环境下不可用 |
| **腾讯行情** | `qt.gtimg.cn` HTTP | ★★★★ | 否 | 实时报价（价格/成交量/PE/市值） |
| **baostock** | `query_history_k_data_plus` | ★★★★ | 否 | K 线兜底（仅无 Tushare token 时默认启用），免费稳定，需网络直连 |
| **TickFlow** | `TickFlow.free().klines.get` | ★★★★ | 否 | 可选 K 线源（默认关闭），独立数据管道（非东方财富），`TickFlow.free()` 零配置 |
| **FRED** | `fredapi` (`fred.series.get_series`) | ★★★★ | 是 | 美宏观数据：US 10Y/2Y 国债、VIX、CPI、美元指数。需 `FRED_API_KEY`，免费注册 |
| **Yahoo Finance** | `query1.finance.yahoo.com/v8/finance/chart` | ★★★ | 否 | 费城半导体指数 SOX（`^SOX`）。`urllib.request` 直连，免 yfinance 依赖。AI/半导体需求领先指标 |
| **公告事件** | `akshare.stock_individual_notice_report` / `stock_notice_report` | ★★ | 否 | 上市公司公告采集 + 结构化分类（事件引擎，v0.1.6 新增）。依赖东方财富接口，代理环境下可能不可用 |

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
| 复权支持 | 前/后/不复权（前复用 adjustflag=2） | forward/backward/none |
| 并发安全 | 需 `_BAOSTOCK_LOCK` 串行化 | 线程安全 |
| 默认启用 | auto（仅无 Tushare token 时） | 否（`INVEST_ENABLE_TICKFLOW=1` 启用） |

### 交叉验证价值

因 TickFlow 使用独立数据管道，与 akshare（东方财富）不同源，两者 K 线数据出现系统性偏差的概率极低。若 TickFlow 与 akshare/baostock 同时返回一致数据，可视为高可信度信号。⚠️ **注意复权语义一致性**：所有源须同为前复权（v0.2.3 起统一），否则跨源校验会因语义错配误报 divergence（实测曾出现 98% 假差异）。

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
- **龙虎榜 / 大宗交易** — 游资席位与大宗成交（v0.2.0，见 [game-theory.md](game-theory.md)）
- **互动易 / 社媒 API** — L3 公开舆情自动采集（v0.2.0，见 [sentiment.md](sentiment.md)）

## 舆情数据源

| 层级 | 维度 | 接口 | 稳定性 | 说明 |
|------|------|------|--------|------|
| L1 卖方研报 | `research` | Tushare `report_rc` / akshare `stock_research_report_em` | ★★★ | 研报情绪卡（`SentimentCard`），非社媒舆情 |
| L2 公告事件 | `events` | akshare `stock_individual_notice_report` | ★★ | `attach_events` 自动采集 + 事件分类 |
| L3 公开舆情 | — | WebSearch / Tavily | ❓ | Claude 执行；须 query + 来源标注。见 [sentiment.md](sentiment.md) |

L3 为 fallback，可信度标注 ❓ 弱，推测须标 `[推测，待验证]`。

### v0.1.9 新闻三层架构（`collect --with-news-pack`）

| 层 | 实现 | 依赖 | 无 Key 行为 |
|----|------|------|------------|
| Layer 1 公告 | `news_scanner` → akshare `stock_individual_notice_report` | akshare 直连 | 始终尝试 |
| Layer 2 查询包 | `build_news_query_pack()` 纯 Python | 无 | 始终产出 ≥3 条 |
| Layer 3 Tavily | REST `api.tavily.com` | `TAVILY_API_KEY` | 静默跳过，标注 `skipped (no key)` |

- Channel B/C（指定源/社区热度）v0.2.0 占位，抛 `NotImplementedError` 并写入 `attempted_sources`
- `news_query_pack_{symbol}_{ts}.json` 写入 `~/.local/share/investment/`
- Harness WebSearch 不是 Python API：Layer 2 查询包由 Claude report 阶段执行

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

## Tushare 积分要点（invest:a-stock 常用）

| 接口 | 最低积分 | 说明 | 积分不足时 |
|------|---------|------|-----------|
| `daily` / `stock_basic` | 120 | 行情、基本信息 | — |
| `fina_indicator` / `moneyflow` / `margin_detail` / `index_daily` | 2000 | 财务、资金、普通指数 | 因子跳过 |
| `index_classify` | 2000 | 申万分类（**不含**行业日线） | 因子跳过 |
| **`forecast`** | **2000** | **业绩预告（公司自披露）** | 降级至 akshare → 跳过 |
| `index_dailybasic` | 4000 | 沪深300 PE（ERP） | 部分可得/标注 partial |
| **`sw_daily`** | **5000** | 申万行业日线 | 降级至 akshare `index_hist_sw` |
| `opt_daily` | 5000 | 50ETF 期权（认沽认购比；Tushare 每日约 17:00 更新，盘中以最近已发布交易日为准） | 因子跳过 |
| **`report_rc`** | **10000**（特色大数据） | **研报评级+目标价+盈利预测** | 降级至 forecast → akshare → 跳过 |

`new_high_ratio` 从 `stock_basic` 的在市股票中按日随机取 30 只，再查 `daily`。若实际样本不足 30，只保留比例和覆盖数供审计；60 日分位不计算，也不作市场广度判断。额度不足时按即时余量缩减请求，避免把客户端限流等待误记为网络超时。

> v0.1.4 起 `collect_research()` 按此表顺序降级（高阶成功则跳过低阶 API）：`report_rc(10000) → forecast(2000) → akshare → 跳过`。
> 默认 `collect`/`report` **不**包含 `research` 维度；需显式 `--dims=...,research`。
> 上表即随包口径；仓库内另有根 `CONFIGURATION.md` 的完整对照，但**该文件不随独立包分发**（同根 `CLAUDE.md`），包内以上表与引擎为准。故此处用文件名标注而非相对链接，避免包内出现死链。

## 港股数据源（invest-hk-stock，v0.2.9 v1）

| 源 | 接口 | 单位/口径 | 状态（2026-09-06 实测） |
|---|---|---|---|
| 腾讯实时 | `qt.gtimg.cn/q=r_hk00700` | HKD；量=股；额=元；市值亿 HKD | ✅（r_hk 字段下标实测见 invest-hk-stock/SKILL.md） |
| 腾讯日 K | `ifzq.gtimg.cn/appstock/app/fqkline/get?param=hk00700,day,,,N,qfq` | qfq 累计因子系，行序 date/open/close/high/low/vol | ✅ |
| 东财港股财务 | `stock_financial_hk_analysis_indicator_em` | 需 akshare_direct_session 直连（datacenter 域）；YOY 列含异常占位值→同比自算 | ✅（push2his 域不可达但不依赖） |
| 百度估值序列 | `stock_hk_valuation_baidu` | PE-TTM/PB 日序列；末值滞后数日 | ✅（分位注记滞后） |
| tushare hk_* | hk_basic/hk_daily | 需 2000 积分档 | 未接入（0.3 候选） |

**代理规则**：东财/腾讯港股域名与 A 股同批 DIRECT（eastmoney.com/gtimg.cn 已在 CLASH_DIRECT_RULES）；若未来接 Yahoo（yfinance）港股，**应走代理**（境外源，方向相反）。
**币种**：东财港股财务 CURRENCY 字段对 A+H 公司不可靠（比亚迪 H 实测数字为 CNY 报表值但字段标 HKD）——跨币换算前核对年报。
