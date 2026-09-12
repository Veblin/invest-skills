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
