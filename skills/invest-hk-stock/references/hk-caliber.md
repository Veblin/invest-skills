# 港股口径备忘（invest-hk-stock v0.2.9）

> 2026-09-06 实测记录。所有差异以实测/官方规则为准，社区流传口径须复测。

## 1. 代码与路由

| 形态 | 归一 | 说明 |
|---|---|---|
| `00700` / `700` | `00700` | 5 位补零 |
| `00700.HK` / `hk00700` / `r_hk00700` | `00700` | 剥前缀后缀 |
| `600176`（6 位）/ `00700.SH` | **拒绝** | A 股防错路由（共享 codes.py 的 zfill(6) 会把 00700 → 000700 误判 SZ——港股路由独立，不改共享代码） |

## 2. 数据源与单位（实测）

| 源 | 接口形态 | 单位/口径 | 状态 |
|---|---|---|---|
| 腾讯实时 | `qt.gtimg.cn/q=r_hk00700` | 价 HKD；量=股；额=元；市值=[44] 亿 HKD；PE=[39] | ✅ 可用（字段 2026-09-06 双标的实测，见 SKILL.md） |
| 腾讯日 K | `ifzq.gtimg.cn/appstock/app/fqkline/get?param=hk00700,day,,,N,qfq` | 行序 date/open/**close**/high/low/vol；qfq 累计因子系；含复权注释（回购/除权） | ✅ 可用 |
| 东财财务 | `stock_financial_hk_analysis_indicator_em` | 年报(001)+中报(002)；**需 akshare_direct_session 直连**；YOY 列含异常占位值（如 -99.4%）→ 同比一律自算 | ✅ 可用（datacenter 域；push2his 域不可达——v1 不依赖） |
| 百度估值 | `stock_hk_valuation_baidu` | PE(TTM)/PE(静)/PB/PCF/总市值日序列；period 近一年/三/五/十年/全部；**末值滞后数日**（9/4-9/6 实测同值）→ 分位注记 | ✅ 可用 |
| tushare hk_* | hk_basic/hk_daily | 需 2000 积分档；hk_daily 硬限频 1 次/分钟（40203 实锤，见 §8） | ✅ 已接入（交叉源） |

## 3. 币种（重要）

- 行情/价格 = HKD（腾讯接口无歧义）。
- **东财港股财务 CURRENCY/IS_CNY_CODE 字段不可靠**：比亚迪 H（01211）实测——数字为 CNY 报表值（2025 营收 8039.6 亿 = A/H 合并报表 CNY 口径），但 CURRENCY=HKD、IS_CNY_CODE=0。腾讯（报表 HKD）字段恰好正确。**规律：A+H 公司以东财接口数字核对年报披露币种后再用。**
- 股息税：港股通 20%（红筹最高 28%）——股息率报税后口径。

## 4. 复权与估值

- 腾讯 qfq = 累计因子乘法系（避免东财固定金额扣减的负价问题）。
- PE 为负/序列非正：分位仅"位置参考"标注（沿用 A 股非正/缺失 PE 纪律；PE≤0 不确证财务亏损）。
- 港股无申万行业/北向/涨跌停/龙虎榜/股东户数——报告相关维度不出现或标注不可得。

## 5. 交易日历

v0.2.9：自然日近似（港股与 A 股同 UTC+8，日期本身无时区差；台风/圣诞/佛诞休市未建模）。完整化（港股通日历 + 指数日线推导）归 0.3.0。

## 6. 报告节奏（港股规则）

年报：年结后 3 个月内公告（12 月年结 → 次年 3/31 前）；中报：半年后 2 个月内。无季报、无强制业绩预告。多公司非 12 月年结——以接口 REPORT_DATE 为准。

## 7. 增补源实测（2026-09-06）

- **tushare hk_basic**：✅ 可靠（ts_code `00700.HK`；name/market/list_date/curr_type=交易币种全 HKD/trade_unit）。注意 curr_type 是**交易币种**（全 HKD），非报表货币。
- **tushare hk_daily**：⚠️ **间歇性空返回**——首调成功（4 行）后同参数重调 0 行，疑限频/权限抖动；作交叉源使用时以非空为准，勿判死。> 注：上为本节观测日（2026-09-06）记录，根因已由 §8 实锤（40203 硬限频 1 次/分钟），归因以 §8 为准。
- **yfinance（Yahoo）**：✅ 走代理可达。代码形态 **4 位补零**（`00700`→`0700.HK`；`700.HK` 实测 404）。`dividendYield` 实测为百分数单位（0700=1.2、01211=0.48）；早年版本曾为小数——**>25% 判脏值丢弃**（归一 `_norm_div_yield`）。trailingPE 与腾讯/百度存在口径差（0700：14.90 vs 16.19 vs 14.87）——差异并列不裁决。

## 8. 增补源实测（2026-09-07，探针脚本直跑 + 原始 HTTP 验证）

- **东财三表 `ak.stock_financial_hk_report_em(stock, symbol, indicator)`**：✅ 可用（datacenter 域，**须 akshare_direct_session 直连**；同指标接口域名）。长表逐科目：`REPORT_DATE/FISCAL_YEAR/START_DATE/STD_ITEM_CODE/STD_ITEM_NAME/AMOUNT`，`symbol ∈ 资产负债表/利润表/现金流量表`、`indicator ∈ 年度/报告期`。00700 利润表-年度 585 行全史（2001→2025）；**无 CURRENCY 字段，AMOUNT 为报表货币原值（元）**——01211 FY2025 营业额 `8.039650e+11` 元 = 8039.65 亿元，与 §3 记录的 CNY 合并报表口径一致 → **A+H 币种纪律同样适用**（数字以年报披露币种核对）。科目名是中文口径（营业额/营运收入），与指标接口的英文列（OPERATE_INCOME）不同——两接口字段映射不可直接复用。
- **东财 K 线 `ak.stock_hk_hist`**：❌ 不可达——`ConnectionError: RemoteDisconnected`（push2his 域在当前网络环境被拒，与 §2 记录一致）。K 线维持腾讯 qfq，**akshare 港股 K 线勿选东财源**。
- **新浪 K 线 `ak.stock_hk_daily(symbol, adjust)`**：✅ 可用（**须 proxy_bypass 国内源上下文**；qqf 全史 5463 行，列名英文 `date/open/high/low/close/volume/amount`，最新 2026-09-04）。作东财 hist 不可达时的降级/交叉源候选；⚠️ 其 qfq 与腾讯累计因子乘法系**口径未比对**——交叉前须对齐，勿直接互换。
- **tushare 权限实锤（绕过 TushareClient 直打 HTTP 的 code/msg）**：
  - `hk_basic` ✅ code=0（1 行）。
  - **`hk_daily` = code 40203 频率超限「1 次/分钟」**——§7（2026-09-06 观测记录）「间歇性空返回/权限抖动」的**根因实锤**：不是权限抖动，是**硬限频 1 次/分钟**（同一分钟内重复调用必空返回）。作交叉源时每次调用前须距上次 ≥60s。
  - **`ccass_hold` = code 40203 无访问权限**——现账号积分档拿不到 CCASS 接口；免费路径只剩 www3.hkexnews.hk 官网（近 12 个月窗口，未实测）。
  - **`trade_cal(exchange='HKEX')` code=0 但恒空**（带/不带日期窗口均 0 行；SSE 同参对照 7 行正常）→ HKEX 日历**不走 tushare**；0.3.0 交易日历选 pandas_market_calendars（XHKG）或指数日线推导。
