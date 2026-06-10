# 数据源快速参考

## A 股免费数据源优先级

```
efinance → akshare (新浪源) → akshare (东财源) → baostock → yfinance → curl fallback → 标注不可得
```

| 数据源 | 接口 | 稳定性 | 需要Token | 代理绕过 |
|--------|------|--------|-----------|----------|
| efinance | `stock.get_base_info` / `stock.get_quote_history` | ★★★★ | 否 | 自动 `trust_env=False` |
| akshare (新浪) | `stock_financial_abstract` | ★★★★ | 否 | 自动 `trust_env=False` |
| akshare (东财) | `stock_individual_info_em` / `stock_zh_a_hist` | ★★★ | 否 | 需 curl fallback |
| baostock | `query_history_k_data_plus` | ★★★ | 否 | 否 |
| yfinance | `Ticker.history()` | ★★ | 否 | 否 |
| curl 回退 | `push2.eastmoney.com` 直接 HTTP | ★★★★ | 否 | 天然绕过代理 |

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
| 代理阻断东财API | `requests` 报 `ConnectionError` | 自动 `trust_env=False` → curl fallback |
| efinance 版本不兼容 | 函数不存在 | 自动降级至 akshare / baostock |
| 新浪API不可用 | 返回空数据 | 降级至东财API → baostock |
