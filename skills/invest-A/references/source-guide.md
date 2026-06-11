# 数据源快速参考

## A 股数据源优先级

```
有 Token: Tushare ∥ akshare → 腾讯/baostock → 标注不可得
无 Token: akshare → 腾讯/baostock → 标注不可得
```

| 数据源 | 接口 | 稳定性 | 需要Token | 说明 |
|--------|------|--------|-----------|------|
| **Tushare Pro** | `stock_basic` / `daily` / `fina_indicator` / `top10_floatholders` / `moneyflow` | ★★★★ | 是 | **主数据源**（2000 积分解锁主要接口） |
| **akshare** | `stock_financial_abstract_ths` / `stock_hsgt_individual_em` / `stock_zh_a_hist` / `stock_individual_info_em` | ★★ | 否 | 多源验证用。⚠️ `stock_zh_a_hist` 和 `stock_individual_info_em` 因东方财富反爬在多数环境下不可用 |
| **腾讯行情** | `qt.gtimg.cn` HTTP | ★★★★ | 否 | 实时报价（价格/成交量/PE/市值） |
| **baostock** | `query_history_k_data_plus` | ★★★★ | 否 | K 线历史数据，免费稳定，需网络直连 |

## 代理 / VPN 问题（Clash、V2Ray 等）

大陆用户常开 VPN，国内数据源（东方财富、腾讯行情、baostock）应**直连**，走代理会失败或被识别为境外 IP。

### 检测与提示（不强制绕过）

`lib/proxy.py` 检测 env 代理与 macOS 系统代理。`collect` / `report` 启动时若检测到代理，会提示将以下域名加入 Clash **DIRECT** 规则：

```yaml
rules:
  - DOMAIN-SUFFIX,eastmoney.com,DIRECT
  - DOMAIN-SUFFIX,gtimg.cn,DIRECT
  - DOMAIN-SUFFIX,baostock.com,DIRECT
  - MATCH,PROXY
```

运行 `invest.py diagnose` 可查看 `proxy_detected` 与 `clash_rules_hint`。

Tushare 客户端使用 `trust_env=False` 并显式传入代理配置，与 akshare 并行采集互不干扰。

### Clash TUN 模式

TUN 在网卡层劫持流量，需在 Clash 规则中将国内金融域名设为 **DIRECT**，或采集时暂时关闭 TUN / 使用「规则模式」而非「全局模式」。

## 待接入数据源

以下数据源已规划但尚未接入，详见 `docs/TODO.md`：
- **efinance** — 免费 A 股数据，稳定性 ★★★★
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
| akshare 网络不通 | `requests.ConnectionError` | 降级至腾讯行情（仅行情维度） |
| 腾讯行情不可用 | 请求超时 | 标注"行情数据不可得" |
