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

## 代理问题

如果系统配置了 HTTP_PROXY/HTTPS_PROXY（如 Clash/V2Ray 等 VPN 代理），akshare 基于 `requests` 的接口会走代理失败，表现为 `ProxyError`。

**自动处理**：`collector.py` 通过 `_proxy_bypass()` 在调用 akshare、baostock、腾讯行情前清除代理环境变量（锁仅保护 `os.environ` 读写，不阻塞网络 I/O），完成后恢复。Tushare 客户端在初始化时已捕获代理配置并显式传入 `requests.Session`，不受 env 清除影响。用户无需手动干预。

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
