# discover-scan 数据源与已知局限（v0.1）

> 登记义务（D-D=A1）：本文件描述的所有外部接口均须在
> `skills/lib/references/data-interface-map.md` 有登记行；
> 新增接口须同步地图 + `scripts/smoke_interfaces.py` L1 清单
> （由 `skills/lib/tests/test_v030_doc_checks.py::test_data_interface_map_covers_smoke_l1` 守卫）。

## 一、数据链路

| 步骤 | 接口 | 调用量 | 缓存 | 失败处置 |
|---|---|---|---|---|
| ① 基础信息 | tushare `stock_basic`（`list_status=L`） | 1 次 | **7 天 TTL**（空结果**不写缓存**，D6） | 空返回 → raise（池无法构建） |
| ② 全市场估值 | tushare `daily_basic`（`trade_date`） | 1 次 / 全市场 | — | 空返回 → **退出码 3**（无降级：设计 §5） |
| ③ 质量字段 | tushare `fina_indicator`（**按 `ts_code`**） | 候选集逐个（实测冷缓存 403 只 ≈ 16s） | 建议后续加月更缓存 | 空返回 → `pass=None` + warning |
| ④ 预告（降级档） | tushare `forecast`（按 `ts_code`） | 仅无定期报告者 | — | 失败不阻断（可选增强） |
| ⑤ 无风险利率 | akshare `bond_zh_us_rate`（中国 10Y） | 1 次 | — | 不可得 → L3 利差项降级 + warning |
| ⑥ 市场语境（注记） | journal `market_microstructure.latest_snapshot()` | 本地读 | — | 不可得 → 报告头标「不可得」，不编造 |

**东财依赖**：**零**。东财 push2 域在部分网络环境不可达（v1 港股同因），
本 skill 全程 tushare + 一处 akshare（非东财域）。

## 二、T0 勘察实测（2026-09-12，实现依据）

| 接口 | 实测 |
|---|---|
| `daily_basic` | 5550 行 / 0.30s / 18 列；正 PE 3917 只 |
| `stock_basic` | 5562 行；主板 3195 / 创业板 1407 / 科创板 617 / 北交所 343 |
| `fina_indicator` | 2000 积分档**可用**（无权限拒绝）；**只能按 `ts_code`**（`period=` 与日期区间均返回 `50101 必填参数, ts_code`，**无全市场批量形态**）；单次 ~40 ms；20 标的样本 0 空返回 |
| `forecast` | 需 `ann_date` 或 `ts_code`；**日期区间查询被拒**（`50101`）→ 不做全市场扫描 |
| 管线体量 | 池 5010 → 正 PE 3603 → L1 命中 403（88 行业） |

## 三、已知局限（必须随结论一并说明）

1. **一致预期不可得**：`report_rc`（研报评级/目标价/盈利预测）需 **10000 积分**，
   当前档位不可用 → L3 的增速子项用**业绩预告口径**（`forecast`）替代，
   并明示「预告≠一致预期」；两阶段反解（V-2）待权限升级。
2. **`fina_indicator` 无批量形态**：候选集越大，调用越多次（冷缓存 403 次 ≈ 16s）。
   候选集规模由 L1 门槛决定，与全市场无关——这是设计 §4 选择「先 L1 后质量字段」的原因。
3. **无自身历史分位（L2）**：v0.1 不做「相对自身历史便宜」判断；
   原设计的乐咕长序列源已于 akshare 1.18.64 移除，v0.2 改用
   tushare `daily_basic` 单标的按日期段（decision D-F=F1）。
4. **行业字段质量**：`stock_basic.industry` 缺失时 L1 行业条件**跳过**并记 warning
   （不得静默剔除）。
5. **`fina_indicator` 重复行**：同 `end_date` 可能多行 → 取最近一期前必须去重
   （同期取 `ann_date` 最新者），否则数值不确定（已由 `quality.latest_report` 处理 + 单测钉住）。

## 四、降级链（设计 §5 复述）

| 数据 | 降级链 |
|---|---|
| 全市场 `daily_basic` | **无降级 → 退出码 3**（不产空清单） |
| 行业字段 | 缺失 → L1 行业条件跳过 + warning |
| `fina_indicator` | ① 预告口径（`forecast`）→ ② ROE 下限降级为「股息率>0 或预告净利>0」→ ③ 该项跳过并明示。三者均记 warning「质量过滤降级」 |
| 10Y 利率 | 不可得 → L3 仅保留 `g_implied` 子项 + warning |

## 五、港股池数据链路（`--pool hk`，T11-5 / HK-4）

| 步骤 | 接口 | 调用量（实测计数入快照） | 失败处置 |
|---|---|---|---|
| ① universe | tushare `hk_basic`（`list_status=L`） | **1 次**（2785 行） | 空返回 → raise（池无法构建） |
| ② 横截面 PE | 腾讯 `r_hk` **批量**（100 码/次） | **分批**（2785 只 ≈ 28 次） | 单批失败 → warning + 跳过该批 |
| ③ 质量字段 | 东财港股财务（`hk_financials`，按只） | **逐只**（L1 命中数，实测 227 只 ≈ 2-3 min） | 单只失败 → 计 `unassessable`，不冒充已过滤 |
| ④ 无风险利率 | FRED `DGS10`（US 10Y） | 1 次 | 不可得 → L3 利差子项恒 0 且**透镜表标「本次不成立」** |
| ⑤ 中国 10Y（**仅对比说明**） | akshare `bond_zh_us_rate` | 1 次 | 不可得 → 不渲染对比句 |

**calls / empty_returns**：快照 `pool_stats` 记录**实测计数**（`hk_basic` / `r_hk` /
`hk_financials` 三项 + 空返回数）——不得写死字面量（该字段是回填裁决的锚点）。

**universe 口径偏离**：requirements 建议的「港股通/恒指成分」两个源一个需 push2 域
（本 skill 刻意零东财依赖）、一个无源 → 改用 `hk_basic` **全部上市港股（超集）**，
覆盖不失。报告头显式声明该偏离。

**港股特有口径**（须随报告标注）：

- `ind_rk`：`hk_basic` **无 industry 字段** → 行业条件整体跳过 + warning
- L3 利差：HKD 钉住美元 → 口径改 **US 10Y**（远高于中国 10Y，故利差门槛更严、子项大概率恒 0）
- 质量门净利：东财 `HOLDER_PROFIT`（归母）；**港股无扣非概念** → 相对 A 侧口径放宽
- 质量门 ROE：`ROE_AVG` 实测即**年度 ROE**（该接口返回年报行）；**财年各异**
  （6 月财年 00016 等 / 3 月财年 09988）→ **不按日历后缀判中报**，判据见
  `scripts/lib/sources_hk.py::annualized_roe`
- 数据日：走**港股日历**（`hk_calendar`）；日历降级（周末近似）须显式标注
