# Phase 2：基本面与估值体系

> v0.1.3 子计划 2/4 · 目标版本 `v0.1.3-beta`
> 权威来源：`docs/invest-A-blueprint-v013-final.md` §十 Phase 2、§八
> 工期：~2 周 · 新增代码约 ~430 行 + 测试 ~60 行

---

## 阶段命题（验收一句话）

**能回答：「公司质地如何、估值隐含什么预期、在行业什么位置？」**

---

## 前置条件

- [Phase 1](./invest-A-v013-phase1-dynamic-kernel.md) 完成：`render_report_v3()` 九模块骨架可用
- `test_v013_phase1.py` 全绿

---

## 本阶段交付范围

### 报告模块变更

| 模块 | 变更 |
|------|------|
| 4 静态基本面 | `_section_fundamentals_layered()` 替换占位；12 核心题 + 扩展激活（LAW 14） |
| 4d 估值与预期 | `implied_growth()` 戈登反推（LAW 15） |
| 4 同行对比 | `collect_industry_peers()` 分位排名 |
| 估值数据层 | `pe_band_series()` 供 Phase 4 文本表消费 |

**不变模块：** 0/1/2/3/5(占位)/6/7(占位)/8 保持 Phase 1 状态。

### 本阶段落地的 LAW

| LAW | 要点 |
|-----|------|
| LAW 10 | 每题末尾分析提示（为什么重要 / 误区 / 下一步验证） |
| LAW 14 | 12 道核心必答题逐项出现，数据不足写 `数据不足：[缺少什么]` |
| LAW 15 | 隐性预期差 `g_implied ≈ r - 1/PE`；PE>50 附免责 |

### 交叉验证

| 编号 | 数据对 | 位置 |
|------|--------|------|
| CV-2 | 营收增长 vs 应收账款增长 | 模块 4 财务质量 |

---

## 12 道核心必答题（蓝图 §八）

### 4a 行业位置（3 题）

- A-① 行业景气度（申万板块 + PMI/产量）
- A-② 竞争位置（龙头/挑战者/追赶者）
- A-③ 毛利率 vs 行业中位数

**扩展激活：** 触发源 C → 竞争格局；行业政策 → 政策传导路径

### 4b 商业质量（3 题）

- B-① 护城河来源 + 近 3 年变化
- B-② 增长驱动力 + 持续性
- B-③ 现金流模式（利润 vs 现金流）

**扩展激活：** 现金流覆盖比 < 0.8 → 收入确认质量；业绩预告 → 驱动力转换

### 4c 财务质量（4 题）

- C-① 近 3 年营收 CAGR + 近一年趋势
- C-② 杜邦拆解 ROE
- C-③ 经营现金流/净利润 + 应收/存货增速对比
- C-④ 扣非/净利润

**扩展激活：** 报表风险信号；ROE 变化超 ±5pp → 完整杜邦

### 4d 估值与预期（3 题 + LAW 15）

- D-① PE/PB 5 年历史分位
- D-② PE vs 行业中位数溢价/折价
- D-③ LAW 15 隐性预期差计算

**扩展激活：** PE/PB 处于 80%/20% 分位 → 完整预期差分析

---

## 任务分解

### Task 2.0 — `_section_fundamentals_layered()`（~150 行）

| 项目 | 说明 |
|------|------|
| 文件 | `lib/render.py` |
| 替换 | `render_report_v3()` 模块 4 从 `_section_quality()` 切换 |
| 格式 | 每题独立小标题 + LAW 10 分析提示块 |

```python
# LAW 10 每题末尾格式
[分析提示]
- 为什么重要：[定价传导路径，引用本次数据]
- 常见分析误区：[引用本次至少一个数据点]
- 下一步交叉验证：[1-3 个具体动作]
```

### Task 2.1 — `lib/valuation.py` 增强（~80 行）

**`implied_growth(pe_ttm, risk_free_rate, erp=0.06)`**

```
g_implied ≈ r - 1/PE
r = 10Y 国债收益率 + ERP（默认 6%）
PE > 50 → 返回 warning 字段
```

**`pe_band_series(daily_basic_rows, years=5)`**

返回各日 PE 及 ±1σ/±2σ 轨道；**本阶段仅实现数据层**，Markdown 文本表渲染留 Phase 4。

### Task 2.2 — `collect_industry_peers()`（~80 行）

| 项目 | 说明 |
|------|------|
| 文件 | `lib/collector.py` |
| 内容 | 申万三级同行池，PE/PB/ROE/营收增速分位排名（上限 10 家） |
| 展示 | ≥3 家同行时输出可比公司表 |

### Task 2.3 — pytest（~60 行）

文件：`tests/test_v013_phase2.py`

| 用例 | 验证 |
|------|------|
| `test_fundamentals_twelve_core_questions` | 12 题均以标题出现 |
| `test_fundamentals_data_insufficient_format` | 数据不足格式正确 |
| `test_implied_growth_output` | PE、国债、ERP、g_implied、CAGR 对比 |
| `test_implied_growth_high_pe_warning` | PE>50 含免责 |
| `test_industry_peers_percentile` | ≥3 同行时分位排名 |
| `test_cv2_revenue_vs_receivables` | CV-2 在模块 4 出现 |
| `test_analysis_hint_law10` | 分析提示三块齐全 |

---

## 验收命令

```bash
uv run python skills/invest-A/scripts/invest.py report 600519 --outdir ./out
uv run pytest skills/invest-A/scripts/tests/test_v013_phase2.py -v
uv run pytest
```

## 验收清单

- [ ] 12 道核心题均以标题出现；数据不足写 `数据不足：[缺少什么]`
- [ ] LAW 15 输出：PE、国债收益率、ERP 假设、`g_implied`、与实际 CAGR 对比
- [ ] PE > 50 公司附带简化模型免责
- [ ] 可比公司表含分位排名（≥3 家同行时）
- [ ] 每题末尾含分析提示（为什么重要 / 误区 / 下一步验证）
- [ ] CV-2 在基本面模块落地
- [ ] `pe_band_series()` 数据可序列化输出（文本表渲染见 Phase 4）
- [ ] 模块 5/7 仍为占位（不变）
- [ ] pytest 全绿

---

## 文件变更总览

| 文件 | 变更 | 行数 |
|------|------|------|
| `lib/render.py` | `_section_fundamentals_layered()` | +150 |
| `lib/valuation.py` | `implied_growth()` + `pe_band_series()` | +80 |
| `lib/collector.py` | `collect_industry_peers()` | +80 |
| `tests/test_v013_phase2.py` | 新建 | +60 |

---

## 风险与缓解

| 风险 | 缓解 |
|------|------|
| LAW 15 对高成长公司失真 | PE>50 强制免责说明 |
| 同行数据不可得 | 标注 `数据不足`，不伪造同行 |
| 12 题被段落隐式覆盖 | LAW 14 要求每题独立小标题；pytest 标题扫描 |

---

## 下一阶段

完成本阶段后进入 [Phase 3：风险与分歧闭环](./invest-A-v013-phase3-risk-divergence.md)。
