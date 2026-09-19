---
name: invest-a-discover-scan
version: "0.3.0"
description: "低估发现扫描 — 多透镜粗筛（L1 横截面便宜 + L3 定价-盈利 gap + 质量中过滤）→ 研究观察短清单（≤15 只，逐只理由 + 下钻命令）→ 私有快照留档供事后回填。研究信号，非决策。触发词：低估/发现/便宜/估值扫描/找机会/筛股"
whenToUse: "低估/发现/便宜/估值扫描：从全 A（主板+创业+科创）粗筛出低估值候选短清单，再下钻 invest-a-stock 深判"
argument-hint: "/invest-a-discover-scan [--top 15] [--with-bj] [--no-out]"
allowed-tools: Bash, Read, Write
user-invocable: true
metadata:
  requires:
    bins: [uv, python3]
  optionalEnv:
    - TUSHARE_TOKEN
---

# invest-a-discover-scan — 低估发现扫描（v0.1）

> **定位：研究信号，非决策。** 检出 = 「该标的符合**预注册规则定义**的客观条件」，
> 不构成任何交易建议（LAW 6）。短清单是**观察起点**，深判走下钻命令进入
> invest-a-stock 既有流水线（基本面安全边际 + 多情景估值）。

## 状态与边界（v0.1）

- **范围**：多透镜粗筛 → 短清单 → 快照落盘。**不含**：回填结论（v0.2）、
  自身历史分位 L2 透镜（v0.2）、定时/提醒（归 app）。
- **数据面全程 tushare**（东财域在部分网络环境不可达；skill 内零东财依赖）。
  无 `TUSHARE_TOKEN` → 退出码 4。
- **个体产出仅本地**：md 落 `reports/discover-scan/`（已 gitignore），
  快照落私有 `~/.local/share/investment/discovery/{YYYY}.jsonl`——不进公开仓库。

## CLI

```bash
cd "${INVEST_SKILLS_ROOT:-.}"
uv run python skills/invest-a-discover-scan/scripts/discover_scan.py                 # 短清单 + md + 快照
uv run python skills/invest-a-discover-scan/scripts/discover_scan.py --top 15         # 上限（默认 15）
uv run python skills/invest-a-discover-scan/scripts/discover_scan.py --with-bj        # 纳入北交所（默认排除）
uv run python skills/invest-a-discover-scan/scripts/discover_scan.py --no-out         # 只出 stdout + 快照，不落 md
uv run python skills/invest-a-discover-scan/scripts/discover_scan.py --pool hk        # 港股池（透镜可用性表置顶）
uv run python skills/invest-a-discover-scan/scripts/discover_fillback.py --horizon 90 # v0.2 桩 → 退出码 2
```

**港股池（`--pool hk`）**：universe = tushare `hk_basic` **全部上市港股（超集）**（口径偏离声明见
`references/sources.md` §五）；报告落 `{date}-hk.md`（与 A 股报告**分开**，不互相覆盖）。
港股**无行业字段** → `ind_rk` 透镜不可得、**不做行业分散**（`--per-industry` 对其无效）；
L3 利差口径改 **US 10Y**；无业绩预告 → 增速子项恒 0。每透镜可用性逐条标注（空透镜不冒充）。

**退出码**：`0` 正常 ｜ `3` 数据不可得（**不产空清单**——空清单会被读成
「市场无机会」这一事实断言）｜ `4` 缺 token/权限 ｜ `2` 参数错误或功能未实现。

## 规则（预注册，改动须 bump 规则版本并记录原因）

| 透镜 | 度量（全部 Python 计算） | v0.1 阈值 |
|---|---|---|
| **L1 横截面便宜**（命中门槛） | `EY = 100/PE_TTM`；全 A 正 PE 子总体分位；行业内 PE 排名 | `pe_grank ≤ 15%` ∧ `ind_rk/ind_n ≤ 25%` |
| **L3 定价-盈利 gap**（加分项，非门槛） | `gap = EY − (rf + 2pp)`；`g_implied` vs 预告净利增速上限 | 各记 0/1，用于排序 |
| **L4 市场语境**（**不进规则**） | pulse `market_form` 快照 | 仅报告头注记 |

- **中过滤（硬闸门，四门）**：市场（默认剔北交所）→ 剔 ST/*ST/退市 →
  扣非归母净利 ≥ 0 → ROE（年化）≥ 8%。
- **排序（确定性）**：`pe_grank` 升 → `gap_flags` 之和降 → `ey_pct` 降 →
  **`ts_code` 兜底**（并列不随机）→ 同行业最多 3 只 → Top N。
- 阈值以**预注册草案**身份入库，`rules_version` 随快照落盘；未记录原因**禁止改参**
  （设计 §8-4）。细则见 [references/rules.md](references/rules.md)。

## 口径与已知局限（读清单前必看）

- **ROE 用 `roe_yearly`（年化）**：`fina_indicator` **无 `roe_ttm` 字段**（108 列实测）；
  `roe_yearly` 跨期同口径（H1 的 `roe` 是半年值，不可直接与年度阈值比）。
- **净利用扣非归母净利 `profit_dedt`**：接口**无原始 `netprofit` 列** →
  本闸门比「归母净利 ≥ 0」**更严格**（剔除非经常性损益）。
- **行业内排名对「小行业」系统性偏严**：`rk/n ≤ 25%` 在 `n < 4` 的行业**恒不成立**
  （n=3 时最便宜者也是 33%）。这是预注册公式的字面结果，本期**不改阈值**，
  仅在 `references/rules.md` 记录该局限（列为反馈窗议题）。
- **股票池 = 机构覆盖池的 v0 代理**：主板 + 创业板 + 科创板（剔 ST/北交所），
  非真实机构覆盖名单。
- **L1 分位口径**：与 pulse 同用 `skills/lib/stats.percentile_rank_inclusive`
  （含边界 `<=`，返回百分数后 `/100`）。

## 快照与回填（设计 §8）

- 每次扫描落一行 JSONL：`snapshot_ts / rules_version / trade_date / pool / params / hits / warnings`。
  **空清单也落盘**——「本次无命中」是有信息量的结果。
- v0.2 回填：对 hits 拉 T+90/180 后复权收益，基准双行（池内等权 / 沪深300）
  → 写回 `fillback` 字段 → 季度聚合（Python，禁目视）裁决阈值。
  本期 `discover_fillback.py` 为桩（退出码 2），**不产出任何回填结论**。

## 合规（LAW 6/10）

- 无买卖/仓位建议、无目标价；输出为**并列观察清单 + 客观规则说明**。
- 报告头固定含：生成时间 / 数据日 / 池口径 / 规则版本 / 降级清单 / 免责声明。
- 降级项**逐项标注**（源级失败、质量过滤降级、L3 降级），禁止静默跳过冒充完整。

## 数据源

tushare：`stock_basic`（缓存 7d）/ `daily_basic`（全市场当日）/ `fina_indicator`（按 ts_code，
无批量形态）/ `forecast`（按 ts_code，降级档用）；akshare `bond_zh_us_rate`（中国 10Y，
L3 利差用）。详见 [references/sources.md](references/sources.md) 与
`skills/lib/references/data-interface-map.md`（登记义务）。
