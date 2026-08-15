# /code-review max 10 项修复设计（2026-08-15）

**背景**：`/code-review max` 对 `7cc0ad6..HEAD`（E 系列基线 / H6 / F 系列期货数据层 / ADX / LMV 回踩分类 / 修复提交，38 文件 ~3,263 行）审出 10 项 CONFIRMED finding。本文档为修复设计，已获用户批准。

**已定决策**：
- 范围：10 项一轮修完（含数据层重建 + 全部回测重跑 + JSON/CHANGELOG 同步）
- OI 20 日变化指标：**从用户可见标签移除**（journal/pulse/ETF），数据层字段与共享 helper 保留
- 关联修复纳入：F1/F2 分位 look-ahead（expanding-window 化）

**版本规则**：同版本内修订，不 bump（`v0.2.6` 内日期条目区分），分支 `feat/v0.2.6` 直接提交，commit 前缀 `fix:`。

---

## A. 数据层重建（finding #1）

**根因**：`skills/invest-a-stock/scripts/lib/futures_data.py:74-75` — `fetch_contract` 按 `d[:6] != exp_month` 过滤，每合约只保留到期月内行。到期日（当月第三个周五）至月末的当月合约行属下月合约，被过滤丢弃 → 每月缺 ~40% 交易日（实测 IC 每月中位数 13 行，完整月应 21-23）。

**修法**：按月划分 → 按"前合约到期日"划分（front-month window）：

1. `contract_series(client)` 返回 `{sym: [(code, last_trade_date), ...]}` — 从 `fut_basic` 解析 `last_trade_date` 字段；缺失时兜底计算该合约月份第三个周五（CFFEX 规则）
2. `fetch_contract(client, contract, window_start, window_end)` — 过滤条件 `window_start < d <= window_end`，其中 `window_end = 本合约 last_trade_date`，`window_start = 前一合约 last_trade_date`（到期日归属到期合约，次日归属新当月合约）；序列首合约含起始月全部交易日（`window_start` 取 `start_month` 月初前一日，使月初含入）
3. `ensure_futures_daily(start_month, max_contracts, force=False)` — `force=True` 时先 `store.clear_futures_daily()`（新增：`DELETE FROM futures_daily`）再全量拉取，绕过 `existing` 跳过判定
4. `scripts/backfill_futures_daily.py` 增加 `--force` 参数

**回填**：`uv run python scripts/backfill_futures_daily.py --force`（~460 合约，tushare 80/min 限速 → ~10 分钟；若 `TUSHARE_DAILY_CALL_LIMIT` 不足则用 `--max` 分批）。
回填后 Python 完整性验证（重跑前必做）：每月交易日数分布、`>3 日缺口数 == 0`、总行数 ≈ 4 品种 × 完整交易日。

## B. OI 口径统一与消费方移除（finding #2/#10）

1. **共享 helper**（消除三处复制粘贴 + 语义漂移）：`futures_data.py` 新增
   `compound_oi_change(vals: list[float | None], window=20, min_valid=18) -> float | None` —
   取尾部 `window` 个日环比，`None` 或 `<= -99`（到期塌缩掩码）不计入，有效因子数 `< min_valid` 返回 `None`，否则 `(∏(1+v/100) - 1) * 100`。
2. **消费方移除**（用户已定）：
   - `market_microstructure._fetch_futures`（journal）：删除 `futures_oi_change_pct` 计算；`_compute_labels_v2`（line ~504-509）与 `_compute_labels` 兼容层（line ~614-619）的 `label_capital_flow` 删除 "IC 持仓 20 日" 段（保留基差段）
   - `futures_basis.query_futures_basis`（ETF）：删除 `oi_20d_chg_pct` 输出；`skills/invest-a-etf/references/report-template.md` 模块 7.5 删除 OI 行
   - `skills/invest-a-pulse/SKILL.md`、journal SKILL.md：删除 "持仓量 20 日变化" 文案
   - `data_bridge.get_futures_basis`：保留字段但 docstring 注明 `oi_change_pct` 为**日环比**（非 20 日变化），防未来语义错配
3. **run_f3**（backtest 侧保留该指标，定位为展期节奏度量）：rolling 版改为逐窗口调 `compound_oi_change`（口径与消费方一致：`<18` 有效 → None → 事件判定跳过）；早期行（<18 行窗口）自然返回 None，替代原 `min_periods=20`

## C. E 系列基线（finding #3/#4/#5）

`scripts/scenario_baselines.py` `trigger_flags`：

1. **#4 首行幻影事件**：`close_below` 的 `below & ~below.shift(1, fill_value=False)` → `fill_value=True`（数据起点前状态视为已存在，1990-12-19 首行不再计为"跌破首日"）
2. **#3 重叠前向窗口**：`close_near` / `boll_position` 统一"状态段首日"去重：
   `x & ~x.shift(1, fill_value=True)`（与 close_below/close_above_3d 同语义；close_above_3d 不变——rolling 首两行 NaN 天然保护）
3. **#5 窗口 off-by-one**：`lo = min(i + 60, n)` → `min(i + 61, n)`（含第 60 日）；不完整窗口（`i + 61 > n`）事件从 touched 比例分母剔除（对齐 lmw truncated 语义；当前 12 事件均完整，不影响现有数字）

重跑 `scenario_baselines.py` → 新 JSON。E-006 的 n 将从 795 大幅下降 → `scenario-plans.md` 表更新，E-006 "显著强于无条件基线"声明按新数字裁决（预期降级为观察级，与 E-002~E-005 同标准）。

## D. H6 缺口扫描（finding #6）

`scripts/backtest_h6.py:109`：`range(i - 2, max(0, i - GAP_LOOKBACK) - 1, -1)` → `max(1, ...)`，g≥1 后 `highs[g-1]` 不再负索引读序列末行（未来数据）。

回归测试：构造 `lows[0] > highs[-1]` 的阴跌序列，断言 i=60 处不产生 gap 事件。

重跑 H6 → 新 JSON（幽灵缺口从样本消失，事件数与 CHANGELOG 数字同步更新）。

## E. F2/F3 守卫与窗口对齐（finding #7/#8）+ look-ahead（已纳入）

1. **#8**：`backtest_futures.run_f2` 事件循环加 `if d not in closes: continue`（对齐 run_f1 的 `dates` 过滤与 run_f3 的守卫）
2. **#7**：`run_f3` 基差窗口改用指数日历统一"20 日"：构建 `basis_map = {date: basis_pct}`；对事件 `d`，`tgt = all_dates[idx0 + 20]`，`tgt` 不在 `basis_map` 或 `basis_map[d]/basis_map[tgt]` 为 NaN → 跳过该事件；converge/diverge 用 `basis_map[tgt]` vs `basis_map[d]`。删除被 `len(keys) <= 20` 前置覆盖的死守卫（`idx0 is None`、`d not in fut_dates`、三元 else 分支）
3. **look-ahead（关联修复）**：`run_f1`（line 82-87）与 `run_f2`（line 149-150）的分位改 expanding-window：`percentile_rank_inclusive(basis.iloc[:i+1].tolist(), v)`（截至当日），消除事件标签中的未来信息

重跑 F1/F2/F3 → 新 JSON。F3 结论预期仍为"降级不可刻画"（月度节奏主导在完整序列上同样成立），数字与表述同步。

## F. ADX 测试独立 oracle（finding #9）

生产实现不动（cumulative-mean 种子为选定约定）。`skills/lib/tests/test_technical_adx.py`：

1. **新增 golden fixture**：小型固定序列（n=3、8-10 根），期望值以与生产代码结构完全不同的方式独立推导（分步手算，推导过程写入测试注释），逐条 `==` 断言
2. 现有 `_adx_ref` mirror 测试保留，但 docstring/注释从"独立参考实现"改为"约定锁定（防回退）"——同构代码不构成 oracle，仅防 seed/递推回退
3. property 测试保留（稳态=100、短序列全 None、发布范围 2n−1）

## G. 文档同步

- `CHANGELOG.md`：新增 2026-08-15 日期 fix 条目（10 项修复 + look-ahead + 重跑后数字变化）
- `skills/lib/references/scenario-plans.md`：E 系列表重写（新 n/统计 + E-006 降级 + E-005 口径注记）
- `host-docs/v0.2.6/股指期货数据融入ETF分析_调研方案_20260814.md:147`：F2 分位描述纠正（现写"分位 >90%/<10%"为反向顺序；F1/F2/F3 结论段落按新 JSON 同步）
- pulse / journal / ETF 文案：OI 行删除（见 B2）

## H. 回归测试清单

| 修复 | 测试文件（实施时定位现有文件） | 用例 |
|------|------|------|
| #1 窗口划分 | test_futures_data.py | 合成 stub client：跨月连续无洞、窗口边界（到期日归属）、季月合约、首合约窗口起点 |
| #2/#10 helper | test_futures_data.py（或新） | 掩码 ≤−99、min_valid 阈值、空窗口 None、复利数值 |
| #4 首行幻影 | scenario 测试 | 序列起点即 below → 0 事件；起点上方后跌破 → 1 事件 |
| #3 去重 | scenario 测试 | close_near/boll_position 连续段只计首日 |
| #5 60 日窗口 | scenario 测试 | 第 60 日触达被计入；截断窗口事件不入分母 |
| #6 g=0 | H6 测试 | `lows[0] > highs[-1]` → i=60 无幽灵缺口 |
| #8 守卫 | F 系列测试 | 期货日期不在指数日历 → 跳过不崩溃 |
| #7 对齐 | F 系列测试 | basis_map 取 `all_dates[idx0+20]`；目标日缺失 → 跳过 |
| look-ahead | F 系列测试 | 分位只依赖截至当日序列（构造带未来极值的序列验证） |
| #9 golden | test_technical_adx.py | 手算 fixture 逐条断言 |
| #2 移除 | journal/etf 测试 | `label_capital_flow` 无 OI 段；`query_futures_basis` 无 `oi_20d_chg_pct` 键 |

## I. 执行顺序

1. 代码修复（A→F）+ 全部回归测试绿
2. `uv run python scripts/backfill_futures_daily.py --force`（数据层重建）
3. Python 完整性验证脚本（每月交易日数 / 缺口数 / 行数）
4. 重跑 `backtest_futures.py --hypothesis F1/F2/F3` → 新 JSON
5. 重跑 `scenario_baselines.py` → 新 JSON → scenario-plans.md
6. 重跑 `backtest_h6.py` → 新 JSON
7. CHANGELOG + 文档同步（G）
8. 全量 pytest
9. commit（`fix: /code-review max 10 项修复...` 仿 8dabc01）

## 风险

- **tushare 日调用上限**：~460 合约 ≈ 460+ 次调用，不足则 `--max` 分批多轮
- **E-006 "显著"结论被推翻**：预期内（去重后样本自相关消除），文档降级即修复目的
- **H6/F1/F2/F3 数字全部变化**：CHANGELOG/调研方案/report 引用须全部同步，禁止残留旧数字
- **回填耗时**：限速下 ~10 分钟量级；akshare 指数收盘调用（东财端点当前不可达，若 `stock_zh_index_daily` 走东财源则需代理直连配置，届时用诊断输出核对）
