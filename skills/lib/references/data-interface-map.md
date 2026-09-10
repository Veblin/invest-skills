# 数据接口地图（Data Interface Map）

> 全量盘点 skill 代码**实际调用**的外部数据接口：能力簇 / 使用方 / 风险 / 实测状态。
> 与 [source-guide.md](../../invest-a-stock/references/source-guide.md)（选源策略/优先级/积分降级）互补：本文件是**「清单 + 归属」字典**，source-guide 是**「怎么选源、怎么降级」**。
>
> **更新约定**：每次接口实测结论变化时，更新下方「实测日期 + 版本」并修订对应行；季度冒烟由 `scripts/smoke_interfaces.py` 驱动（L1 存在性检查零网络、L2 精选实探）。**版本号是接口存在性的关键变量**——akshare 接口随版本漂移（2026-09-08 实证：个股乐咕接口 `stock_a_lg_indicator` 已在当前版本移除）。

## 环境基线（2026-09-08 实测）

| 项 | 值 |
|----|----|
| akshare | 1.18.64 |
| tushare | 1.4.29（HTTP 轻量客户端，非官方 SDK） |
| Python | 3.12（项目 .venv） |
| 网络 | macOS + Clash 常见；东财源需 `DOMAIN-SUFFIX,eastmoney.com,DIRECT`（见 source-guide §代理） |

---

## A. akshare 接口（50 行 / 62 个接口名，按能力簇；2026-09-10 脚本核对）

> 「使用方」= 首个引用该接口的 skill（跨 skill 共用以逗号列全）；「风险」：🔥=高漂移/反爬易失效，⚠️=中，—=低。
> 全部接口均在本仓库代码内被调用；另有少量**技能内联使用**的接口见 D 节。

### A1. 行情 / K线 / 交易日历

| 接口 | 使用方 | 风险 | 注记 |
|------|--------|------|------|
| `stock_zh_a_hist` | invest-a-stock | 🔥 | 东财个股日线；反爬在多数环境不可用（source-guide 已标注） |
| `stock_zh_a_spot_em` | invest-a-stock | 🔥 | 东财全市场快照；2026-09-08 实测 ProxyError（环境相关，非版本） |
| `stock_individual_info_em` | invest-a-stock | 🔥 | 东财个股基本信息；反爬环境不可用 |
| `stock_zh_index_daily` | invest-a-etf, invest-a-stock | ⚠️ | 新浪指数日线（兜底/交叉验证） |
| `stock_zh_a_daily` | invest-a-futures-link | ⚠️ | 新浪个股日线（futures-link 股票层主源；连续请求触发限流 SSL EOF → 脚本含 tushare daily 兜底，2026-09-08 实测） |
| `stock_zh_index_daily_em` | invest-a-stock | 🔥 | 东财指数日线 |
| `stock_zh_index_value_csindex` | invest-a-etf, invest-a-journal | — | 中证指数官方估值（权威源） |
| `tool_trade_date_hist_sina` | invest-a-journal, lib/dates | — | 新浪交易日历（日期工具依赖） |
| `index_stock_cons` / `index_stock_cons_sina` | invest-a-gap-scan | — | 指数成分股（沪深300/A500/科创50 池） |

### A2. 板块 / 行业（申万 + 东财）

| 接口 | 使用方 | 风险 | 注记 |
|------|--------|------|------|
| `index_hist_sw` | invest-a-stock | — | 申万行业日线（sw_daily 5000 分不足时降级目标） |
| `index_analysis_weekly_sw` | invest-a-etf | — | 申万周度行业分析 |
| `sw_index_first_info` / `sw_index_second_info` / `sw_index_third_info` | invest-a-stock | — | 申万一/二/三级行业信息 |
| `stock_board_industry_name_em` / `cons_em` / `hist_em` | invest-a-stock | 🔥 | 东财板块行业成分/历史 |
| `stock_board_industry_pe_ratio_cninfo` | invest-a-stock | 🔥 | 巨潮行业 PE；**akshare 1.18.64 已改名 `stock_industry_pe_ratio_cninfo`**（L1 冒烟 2026-09-08 首发捕获）；新接口实测抛 pandas 列错误（上游未适配巨潮页面），该维已静默降级 → 见 E 节 |

### A3. 涨停 / 情绪 / 市场概况

| 接口 | 使用方 | 风险 | 注记 |
|------|--------|------|------|
| `stock_zt_pool_em` / `stock_zt_pool_dtgc_em` | invest-a-journal, invest-a-stock | 🔥 | 东财涨停池/跌停池（pulse 行业轮动数据源） |
| `stock_market_activity_legu` | invest-a-journal | — | 乐咕赚钱效应/市场活跃度 |
| `stock_sse_summary` / `stock_szse_summary` | invest-a-journal | — | 沪深交易所市场概况 |
| `stock_margin_account_info` | lib/market_pulse | — | 两融账户数 |

### A4. 杠杆 / 资金（两融、北向、板块资金流）

| 接口 | 使用方 | 风险 | 注记 |
|------|--------|------|------|
| `stock_margin_sse` | invest-a-journal, invest-a-gap-scan | — | 上交所两融日历史（pulse 长序列） |
| `stock_margin_szse`* | invest-a-pulse（内联） | — | 深交所两融（*技能内联，见 D） |
| `stock_hsgt_hist_em` | invest-a-journal | 🔥 | 北向历史（东财；2024-08 起日频净买入停披，季度口径） |
| `stock_hsgt_individual_em` | invest-a-stock | 🔥 | 北向个股持股 |
| `stock_fund_flow_industry` | invest-a-etf, invest-a-stock | ⚠️ | 行业资金流 |

### A5. 龙虎榜

| 接口 | 使用方 | 风险 | 注记 |
|------|--------|------|------|
| `stock_lhb_detail_em` | invest-a-stock | 🔥 | 东财龙虎榜明细 |
| `stock_lhb_detail_daily_sina` | invest-a-stock | — | 新浪龙虎榜（兜底） |
| `stock_lhb_stock_detail_em` | invest-a-stock | 🔥 | 个股龙虎榜 |

### A6. 股东 / 高管 / 解禁

| 接口 | 使用方 | 风险 | 注记 |
|------|--------|------|------|
| `stock_shareholder_change_ths` | invest-a-stock | — | 同花顺股东户数变化（筹码集中度代理） |
| `stock_gdfx_top_10_em` | invest-a-stock | 🔥 | 高管持股 Top10 |
| `stock_hold_management_detail_cninfo` | invest-a-stock | — | 巨潮高管持股变动 |
| `stock_restricted_release_queue_em` | invest-a-stock | 🔥 | 解禁队列（按股票） |
| `stock_restricted_release_summary_em` | invest-a-event-calendar | 🔥 | 东财全市场解禁日汇总（symbol=全部股票；唯一全市场型解禁日历源，unlock_calendar 主源） |
| `stock_info_a_code_name` | invest-a-gap-scan | — | A 股代码/名称全表 |

### A7. 分红

| 接口 | 使用方 | 风险 | 注记 |
|------|--------|------|------|
| `stock_dividend_cninfo` | invest-a-stock | — | 巨潮分红送配（权威） |
| `stock_history_dividend_detail` | invest-a-stock | — | 历史分红明细（股息率口径） |

### A8. 财务 / 基本面

| 接口 | 使用方 | 风险 | 注记 |
|------|--------|------|------|
| `stock_financial_abstract_ths` | invest-a-stock | — | 同花顺财务摘要（多源验证） |

### A9. 宏观（国内）

| 接口 | 使用方 | 风险 | 注记 |
|------|--------|------|------|
| `macro_china_pmi` / `macro_china_cpi` / `macro_china_ppi` / `macro_china_lpr` / `macro_china_money_supply` / `macro_rmb_loan` | invest-a-stock | — | 宏观标签锚点（pulse 消费） |
| `bond_china_yield` | invest-a-stock | — | 中债收益率 |
| `bond_zh_us_rate` | invest-a-journal, invest-a-stock | — | 中美利率对比（ERP 原料） |

### A10. 新闻 / 公告 / 研报

| 接口 | 使用方 | 风险 | 注记 |
|------|--------|------|------|
| `stock_notice_report` / `stock_individual_notice_report` | invest-a-stock | 🔥 | 东财公告（news-pack L1） |
| `stock_news_em` | invest-a-stock | 🔥 | 东财个股新闻 |
| `stock_research_report_em` | invest-a-stock | 🔥 | 东财研报（机构研报三层降级链） |

### A11. ETF / 基金

| 接口 | 使用方 | 风险 | 注记 |
|------|--------|------|------|
| `fund_etf_spot_em` | invest-a-etf | 🔥 | 东财 ETF 快照 |
| `fund_etf_category_sina` | invest-a-etf | — | 新浪 ETF 分类 |
| `fund_etf_fund_info_em` | invest-a-etf | 🔥 | 东财 ETF 详情 |
| `fund_open_fund_info_em` | invest-a-etf | 🔥 | 开放式基金详情 |
| `fund_portfolio_industry_allocation_em` | invest-a-etf | 🔥 | 基金行业配置 |

### A12. 期货

| 接口 | 使用方 | 风险 | 注记 |
|------|--------|------|------|
| `futures_main_sina` | invest-a-stock（股指基差 F 系列）, invest-a-futures-link（15 链商品主力主消费者） | — | 主力连续；**商品主力亦可取**（2026-09-08 实测 SR0/AU0/SC0/RB0 全通，含当日） |
| `futures_spot_price` | invest-a-stock | — | 期货现货价格 |

### A13. 港股

| 接口 | 使用方 | 风险 | 注记 |
|------|--------|------|------|
| `stock_financial_hk_analysis_indicator_em` | invest-hk-stock | 🔥 | 东财港股财务指标 |
| `stock_hk_valuation_baidu` | invest-hk-stock | — | 百度港股估值序列 |

---

## B. tushare 接口（19 行 / 28 个接口名，实际调用；2026-09-10 脚本核对）

> 门槛积分来自 `lib/tushare_client.py` 的 `TUSHARE_API_MIN_POINTS` 与 source-guide 积分表；积分不足时客户端静默降级（is_available=False 或降级链）。
> **Tushare Pro API 面稳定（版本化），漂移风险低；真正会变的是账户积分档位。**

| 接口 | 门槛积分 | 使用方 / 用途 |
|------|---------|--------------|
| `daily` | 120 | K 线主源（+ `adj_factor` 自算前复权） |
| `adj_factor` | — | 复权因子（唯一可靠复权来源） |
| `stock_basic` | 120 | 股票基础信息 |
| `daily_basic` | 2000 | **逐股逐日 PE/PB/股息率/市值**（个股估值历史；2026-09-08 实测 600737 311 行可用；T+1，当日盘中不可得） |
| `fina_indicator` | 2000 | 财务指标 |
| `moneyflow` | 2000 | 资金流 |
| `margin_detail` | 2000 | 个股两融明细 |
| `margin` | — | 两融汇总 |
| `forecast` | 2000 | **业绩预告（全市场可拉，业绩雷达原料）** |
| `index_daily` / `index_classify` / `index_weight` / `index_member` | 2000 | 指数行情/分类/权重/成分 |
| `index_dailybasic` | 4000 | 指数每日指标（沪深300 PE→ERP；不足时 partial） |
| `sw_daily` | 5000 | 申万行业日线（不足降级 akshare `index_hist_sw`） |
| `opt_daily` / `opt_basic` | 5000 | 50ETF 期权（认沽认购比 pcr；本项目当前不可用→snapshot `_errors`） |
| `report_rc` | 10000 | 研报评级/目标价/盈利预测（降级 forecast → akshare → 跳过） |
| `income` | — | 利润表 |
| `fund_share` / `fund_daily` / `fund_adj` | — | ETF 份额/净值/复权 |
| `hk_daily` / `hk_basic` | — | 港股行情/基础 |
| `fut_daily` / `fut_basic` | — | 期货日线/合约（股指 F 系列） |
| `hsgt_top10` | — | 沪深港通十大成交 |

---

## C. 直连 / 其他源（非 akshare/tushare）

| 源 | 端点 | 使用方 / 用途 | 注记 |
|----|------|--------------|------|
| 腾讯行情 | `qt.gtimg.cn` HTTP | 实时报价（价格/成交量/PE/市值） | 2026-09-08 实测可用（600737 盘中 +9.99%） |
| FRED | `fredapi` | 美 10Y/30Y/VIX/CPI/美元指数（宏观标签） | 需 FRED_API_KEY |
| Yahoo | `query1.finance.yahoo.com` | SOX 费城半导体指数 | urllib 直连 |
| baostock | `query_history_k_data_plus` | K 线兜底（无 tushare token 时 auto） | — |
| TickFlow | `TickFlow.free()` | 可选 K 线源（默认关闭） | — |
| 东方财富 REST | push2 API | zt 池/spot/龙虎榜底层 | 代理环境需 DIRECT 规则 |

---

## D. 技能内联使用（不入库代码，漂移风险点）

以下接口**只在 SKILL.md 的 python 片段中直接调用**（未进入 scripts/ 代码路径）；其中 akshare 类**已并入 L1 存在性清单**（smoke_interfaces.py 末段「D 技能内联」块，防漂移），其余需人工留意：

| 接口 | 使用方 | 注记 |
|------|--------|------|
| `stock_index_pe_lg` | invest-a-pulse（内联） | 乐咕沪深300 PE 长序列 |
| `stock_market_pb_lg` / `stock_market_pe_lg` / `stock_index_pb_lg` | invest-a-pulse（内联） | 乐咕市场 PB/PE |
| `stock_margin_szse` | invest-a-pulse（内联） | 深交所两融按日 |
| `stock_a_lg_indicator`（个股乐咕） | — | **已移除**（akshare 1.18.64 实测 AttributeError）；个股估值历史改用 tushare `daily_basic` |

---

## E. 已知失效 / 环境敏感项（截至 2026-09-08）

| 项 | 现象 | 判定 |
|----|------|------|
| 东财 push2 系（spot/龙虎榜等） | ProxyError | **环境**（Clash 需 DIRECT 规则），非版本——修复后即恢复 |
| `opt_daily`（pcr 原料） | `_errors: opt_daily empty` | **权限**（5000 分不足），非版本 |
| `daily_basic` 当日盘中查询 | 空 | **T+1 口径**，非故障——收盘后可得 |
| 个股乐咕估值接口 | AttributeError | **版本漂移**（已移除），以 tushare daily_basic 替代 |
| `stock_board_industry_pe_ratio_cninfo` | AttributeError（1.18.64） | **改名** `stock_industry_pe_ratio_cninfo`；但新接口实测崩溃（列名赋值错误，疑似上游未适配巨潮页面）→ invest-a-stock 行业 PE 中位数/相对位置维静默缺失（try/except 守卫吞错）；处置待办：akshare 升版后复测，或接受该维降级 |
| `stock_industry_pe_ratio_cninfo`（新名） | 实测 pandas 列赋值崩溃 | **上游 bug**（akshare 1.18.64），同上处置 |

---

## F. 冒烟约定

```bash
# L1：全部 akshare 接口存在性检查（零网络，秒级）——检测版本漂移
uv run python scripts/smoke_interfaces.py

# L2：精选接口实探（网络，~10 次调用）——检测环境/反爬/权限
uv run python scripts/smoke_interfaces.py --live
```

- 冒烟输出头部含 akshare/tushare 版本，**留存输出即可对照「版本 vs 可用性」**
- 建议节奏：季度一次；新增大版本升级（akshare minor 升版）后必跑
- 失败处置：报错型 → 改代码或更新本文件 E 节；静默语义型 → 依赖跨源交叉验证兜底
