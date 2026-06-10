# 数据源快速参考

## A 股数据源优先级

```
有 Token: Tushare ∥ akshare → 腾讯行情 → 标注不可得
无 Token: akshare → 腾讯行情 → 标注不可得
```

| 数据源 | 接口 | 稳定性 | 需要Token | 说明 |
|--------|------|--------|-----------|------|
| Tushare Pro | `stock_basic` / `daily` / `fina_indicator` / `top10_floatholders` / `moneyflow` | ★★★★ | 是 | 主数据源（2000 积分解锁主要接口） |
| akshare | `stock_zh_a_hist` / `stock_individual_info_em` / `stock_financial_abstract_ths` | ★★★ | 否 | Tushare 不可用时的首选兜底 |
| 腾讯行情 | `qt.gtimg.cn` HTTP | ★★★★ | 否 | 实时报价兜底（仅价格/成交量/PE/市值） |

## 待接入数据源

以下数据源已规划但尚未接入，详见 `docs/TODO.md`：
- **efinance** — 免费 A 股数据，稳定性 ★★★★
- **baostock** — 免费 A 股 K 线，稳定性 ★★★
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
