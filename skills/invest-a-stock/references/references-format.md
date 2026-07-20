# 引用来源表与引用格式规范

> 受 [SKILL.md](../SKILL.md) LAW 7 约束。

## 行内引用格式

```
事实性陈述： "数值" [来源: {追溯路径} / {获取日期}]
分析性判断： "判断" [依据: {数据来源}； 逻辑链: {推理}]
推测： "推测" [推测，待验证]
无数据： "未获取到任何有效数据，无法判断。[尝试了 {源1}、{源2}，均失败]"
```

示例：

- `"营收 188.81 亿元 [来源: WebSearch / query: '中国巨石 600176 2025年报' / 2026-06-10]"`
- `"当前 PE(TTM) 41.23x [来源: collector.collect_quote(symbol='600176') → tencent_finance / 2026-06-10]"`

## 报告附录 — References 表

报告末尾必须包含「引用来源（References）」附录。表头固定：

```
| 维度 | 渠道 | 追溯路径 | 数据状态 |
```

**数据状态** 列：

- `✅ 有数据` — 该渠道成功获取
- `❌ {错误原因}` — 该渠道失败
- `⏭️ 未尝试` — 因依赖缺失未执行

**追溯路径** 须可复现：

| 维度 | 渠道 | 追溯路径 | 数据状态 |
|------|------|----------|---------|
| 日K线 | tushare.daily | `pro.daily(ts_code='300328.SZ', start_date='20260501')` | ✅ 有数据 |
| 日K线 | akshare.stock_zh_a_hist | `ak.stock_zh_a_hist(symbol='300328', period='daily')` | ❌ 连接被拒绝 |
| 行业动态 | WebSearch | `query: "宜安科技 液态金属 2026"` | ✅ 有数据 |

渠道标注规则：

- Tushare：`pro.{api_name}()` + 参数
- WebSearch：`query: "..."` 原字符串
- akshare：`ak.{函数名}(参数...)`
- 腾讯行情：`qt.gtimg.cn` + URL
- baostock：`bs.query_history_k_data_plus(...)`

## WebSearch 白名单（涨价信号触发）

当 `industry_pricing` 维度涨价信号为「确认」时，深搜优先限定（完整列表见 `env.PRICE_NEWS_WHITELIST`）：

```
site:stcn.com OR site:cnstock.com OR site:cs.com.cn OR site:21jingji.com
OR site:eeo.com.cn OR site:finance.sina.com.cn OR site:10jqka.com.cn OR site:cls.cn
```

> ⚠️ 东方财富 (eastmoney.com) 因代理问题暂不列入白名单。

舆情专项 WebSearch 指引见 [sentiment.md](sentiment.md)。
