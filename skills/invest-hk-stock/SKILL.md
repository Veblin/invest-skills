---
name: invest-hk-stock
version: "0.2.9"
description: 港股研究（v2）——腾讯 r_hk 快照 / qfq K 线 / 东财财务 / 百度估值序列 / 南向资金 / A/H 比价 / 双标的 compare；九模块可映射维度 7/7，未接入维度显式声明。研究工具，非决策工具。触发词：港股/港股估值/AH比价/南向资金/港股对比
whenToUse: 港股标的（00700/01211 等 5 位代码）的研究报告、快照、估值位置、A/H 比价与双标的对照
argument-hint: "00700 | compare 00700 09988 | ah 600036 03968"
allowed-tools: Bash, Read, WebSearch, WebFetch, Write
user-invocable: true
metadata:
  requires:
    bins: [uv, python3]
  optionalEnv:
    - TUSHARE_TOKEN
---

# invest-hk-stock — 港股数据引入与初步分析

> **工具约束说明**：frontmatter 的 `allowed-tools` 是 Claude Code 约定；在 DSH 等不读取该字段的 harness 下不生效，实际可用工具由平台自身沙箱控制。本技能主体操作为本地数据采集与计算（`uv run python` CLI）。

## 状态与边界（v2）

- **模块覆盖（九模块映射，逐项可核）**：
  **已覆盖 7/7 可映射维度** = 模块 0 研究问题卡（Claude 侧流程）/ 1 当前状态快照 / 3 市场结构（南向资金）/
  5 市场分歧 / 6 左侧-右侧概率结构 / 7 风险与不确定性 / 8 附录。
  **部分覆盖 1 项**：模块 4 静态基本面（财务摘要近 4 期；⚠️ **HK 无季报制度** → 期间口径为年报 + 中报，
  与 A 股季频不可直接对齐）。
  **声明未接入 3 项**（给原因，不静默缺节）：模块 2 动态驱动分析（HK 无新闻/公告采集层）/
  3b 机构观点与盈利预测（HK 一致预期源未接入）/ 3c 参与者行为扫描（CCASS 持仓、沽空源未接入）。
  > 「可映射」判定口径 = **不需要新建数据源即可交付**的维度。每次 `report` 的正文最前输出
  > 「模块覆盖声明」表，覆盖率由 `coverage_summary()` Python 计算（P0）。
  > **无静默缺节**：标「已覆盖」的模块在报告内必有对应节（测试逐模块断言锚点）。
- **数据源**：腾讯 `qt.gtimg.cn`（r_hk 实时 + ifzq fqkline qfq K 线）｜东财港股财务指标（需直连上下文；push2his 域在当前网络环境不可达，v1 不依赖）｜百度股市通估值历史序列（**末值滞后注记**）｜**tushare hk_basic/hk_daily**（名称/上市日/币种 + 日线交叉；hk_daily **硬限频 1 次/分钟**——40203 实锤（hk-caliber §8）：60s 内二次调用必空返回，作交叉源须间隔 ≥60s，以非空为准）｜**yfinance（Yahoo）**（当前 PB/股息率/PE 交叉；`0700.HK` 4 位补零形态；**境外源须走代理**——与东财/腾讯 DIRECT 方向相反）。
- **代码纪律**：A 股 6 位码/`.SH/.SZ` 后缀**拒绝**（防 zfill 错路由）；港股码 5 位（`00700`/`00700.HK`/`hk00700` 均接受）。
- **币种纪律**：行情/价格均为 HKD；财务数值为东财接口原值——**东财 CURRENCY 字段对 A+H 公司不可靠**（比亚迪 H 实测数字为 CNY 报表值而字段标 HKD），跨币种换算前核对公司年报披露币种。
- **LAW 红线**（与 invest-a-stock 同源）：无买卖建议、无无假设单一目标价、数字全部 Python 计算、不可得三态标注。

## CLI

```bash
cd "${INVEST_SKILLS_ROOT:-.}"
uv run python skills/invest-hk-stock/scripts/hk.py diagnose [00700]   # 数据源连通性
uv run python skills/invest-hk-stock/scripts/hk.py snapshot 00700     # 实时快照（腾讯 r_hk）
uv run python skills/invest-hk-stock/scripts/hk.py report 00700       # 研究报告（九模块覆盖声明）→ reports/{code}-{name}/{ts}.md
uv run python skills/invest-hk-stock/scripts/hk.py ah 600036 03968    # A/H 比价（研究视角，非套利信号）→ 落盘
uv run python skills/invest-hk-stock/scripts/hk.py compare 00700 09988  # 双港股对照 → 落盘
```

- **`compare` 与 A 股 `compare` 语义故意不同**：A 股侧是 print-only，HK 侧**落盘**
  （HK 报告的既有契约是落盘，便于留档与复检）——**勿「统一」成 print-only**。
- **`compare` 不接 A 股代码**：6 位 A 股码（含 `.SH/.SZ/.BJ`）直接拒绝并指路 `ah` 子命令
  （退出码 2，校验先于任何取数）。
- **退出码**：`0` 正常 / `1` 关键维度不可得（**仍落盘**三态）/ `2` 参数非法。

## 南向资金口径（HK-3）

- 首选 akshare `stock_hsgt_hist_em(symbol="港股通沪"/"港股通深")`：日频，`当日成交净买额`（**亿元**）
  是**唯一可用净额列**；`当日资金流入` / `当日余额` 两列**实测恒 NaN**——不渲染、不填 0（三态）。
- 成交额取该接口的**买方列 / 卖方列**（源列原名含「买/卖 + 出成交额」，报告内改写为**买方/卖方**措辞——原词会命中 LAW 6 的 `law6-sell-standalone`（error 级）而它拦的是建议语义，
  此处只是成交结构事实；**改措辞消除歧义，不放宽规则**）。
- 降级 tushare `moneyflow_hsgt`：⚠️ **累计口径**，本技能的数值是**相邻可得行差分**
  （`32089.12 − 32057.2 = 31.92` ↔ akshare 同日 31.9191，2026-09-12 实测吻合）；
  **直接引用源值是 550 亿 vs 44 亿的量级错误**。跨停市日的差值为区间累计，仅作标注。
- 汇总帧的 `交易状态` / `资金净流入` / `当日资金余额` **语义未核实**（`资金净流入` 实测恒 420.0，
  疑为每日额度而非净流入）→ 登记进 `unused_fields`，不猜语义、不上报告。
- 涨跌家数：源对沪/深两行返回**相同**计数 → 渲染为**港股市场整体口径**并注明，
  不拆成「分通道」（避免暗示不存在的粒度）。

## 腾讯 r_hk 字段（2026-09-06 实测定稿，勿按社区整理修改）

`[3]现价 [4]昨收 [31]涨跌额 [32]涨跌幅% [33]高 [34]低 [35]收 [36]量(股) [37]额(元) [39]PE(TTM) [44]总市值(亿HKD) [48]52周高 [49]52周低`。**无 PB 字段**（[42] 与真实 PB 不符）；PB 走百度/东财源。

## 港股风险层（报告模块 7.1 固定节）

无涨跌停（VCM：大型 ±10%/中型 ±15%/小型 ±20% → 5 分钟冷静期）｜主板连续停牌 18 个月强制除牌（GEM 12 个月）｜日均成交额 ≥1 亿 HKD 为流动性实务线｜HKD 钉 USD（7.75–7.85），人民币视角存 USD/CNY 敞口｜股息税港股通 20%（红筹最高 28%）｜无业绩预告/季报（披露节奏 = 年报+中报）。

## 口径参考

[references/hk-caliber.md](references/hk-caliber.md)（复权/币种/报告期/单位差异）。
