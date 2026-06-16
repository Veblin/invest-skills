# Phase 3：风险与分歧闭环

> v0.1.3 子计划 3/4 · 目标版本 `v0.1.3-rc`
> 权威来源：`docs/invest-A-blueprint-v013-final.md` §十 Phase 3、§九
> 工期：~2 周 · 新增代码约 ~280 行 + 测试 ~60 行

---

## 阶段命题（验收一句话）

**能回答：「多空分歧在哪、有哪些定量风险信号、情绪是否极端？」**

---

## 前置条件

- [Phase 2](./invest-A-v013-phase2-fundamentals-valuation.md) 完成：模块 4 完整
- `test_v013_phase1.py` + `test_v013_phase2.py` 全绿

---

## 本阶段交付范围

### 报告模块变更

| 模块 | 变更 |
|------|------|
| 5 市场分歧 | `_section_bull_bear()` 替换占位 |
| 7 风险与不确定性 | `risk_scanner.py` 替换占位 |
| 3 情绪增强 | 50ETF 认沽认购比、融券余额增速、创新高占比 |
| 3b ETF 资金 | ETF 资金流向补全 |

**里程碑：** 九模块报告**无占位节** — 功能完整，可对外试用。

### 模块 5 输出结构（蓝图 §四）

```
5a. 多头逻辑链
5b. 空头逻辑链
5c. 关键分歧点（1-2 个变量）
5d. 预期差（LAW 15 约束，复用 Phase 2 计算）
```

### 模块 7 输出结构

- 三层风险触发信号表（报表 / 商业 / 市场）
- Known Unknowns 列表
- 17 信号中 ≥15 个可自动判定

### 交叉验证

| 编号 | 数据对 | 位置 |
|------|--------|------|
| CV-8 | ERP 分位 vs 认沽认购比 vs 融券增速 | 模块 6 左侧依据 |

---

## 风险引擎：17 个定量触发条件（蓝图 §九）

### 第一类：报表风险（7 信号，引擎自动）

| 信号 | 触发条件 | 严重度 |
|------|---------|--------|
| 现金流持续为负 | 经营现金流连续 2 季度为负 | 高 |
| 利润质量低 | 经营现金流/净利润 < 0.6 连续 2 年 | 中 |
| 应收扩张异常 | 应收增速 > 营收增速 × 1.5 | 中 |
| 存货扩张异常 | 存货增速 > 营收增速 × 1.5 | 中 |
| 扣非大幅背离 | 扣非/净利润 < 0.7 | 中 |
| 负债率升高 | 资产负债率连续 2 年上升且超行业中位数 10pp | 中 |
| 利息保障弱 | 利息保障倍数 < 2 | 高 |

### 第二类：商业风险（4 信号，引擎触发 + Agent 定性）

| 信号 | 引擎触发 | Agent 补充 |
|------|---------|-----------|
| 毛利率下降 | 连续 2 年下降且累计 > 3pp | 行业性 vs 公司特异性 |
| 竞争加剧 | 同行毛利率普降 | 价格战持续时间 |
| 技术替代 | WebSearch 检测到路线变化 | 替代威胁时间维度 |
| 客户集中 | 前 5 大客户占比 > 50% | 议价权弱化 |

### 第三类：市场风险（6 信号，引擎自动）

| 信号 | 触发条件 | 严重度 |
|------|---------|--------|
| 估值极端高 | PE 历史 90% 分位以上 | 中 |
| 估值极端低 | PE 历史 10% 分位以下 | 参考 |
| 北向持续流出 | 近 10 日净流出 > 5 亿 | 低 |
| 技术面破位 | 跌破 MA120 且空头排列 | 低 |
| 动量超卖 | RSI(14) < 20 连续 3 日 | 参考 |
| 动量超买 | RSI(14) > 80 连续 3 日 | 参考 |

---

## 情绪指标增强（蓝图 §6.2）

| 指标 | 接口 | 说明 |
|------|------|------|
| 50ETF 认沽认购比 | `opt_daily` | 2000 积分；左侧反转参考 |
| 融券余额增速 | `margin` | 与融资方向相反 |
| 创新高个股占比 | `daily` 全市场计算 | 过热/恐慌 |
| ETF 资金流向 | `fund_daily` 等 | 模块 3b 补全 |

**输出原则：** 情绪指标给出**分位数**（如「近 5 年第 82 分位」），非仅绝对值。

---

## 任务分解

### Task 3.0 — `lib/risk_scanner.py`（新建，~150 行）

```python
def scan_financial_risks(financials: dict) -> list[dict]: ...
def scan_business_risks(financials, industry) -> list[dict]: ...
def scan_market_risks(valuation, northbound, technical) -> list[dict]: ...
def risk_report(...) -> dict: ...
```

集成：`render_report_v3()` 模块 7 调用 `risk_report()` + Known Unknowns。

### Task 3.1 — `_section_bull_bear()`（~80 行）

| 项目 | 说明 |
|------|------|
| 文件 | `lib/render.py` |
| 替换 | `_section_bull_bear_placeholder()` |
| 输入 | 模块 2/4/6 已有数据 + 风险扫描结果 |
| 约束 | 不含买卖建议；分歧点引用具体数据 |

### Task 3.2 — 情绪指标采集扩展（~50 行）

| 项目 | 说明 |
|------|------|
| 文件 | `lib/collector.py` |
| 扩展 | `collect_market_structure()` 或独立 `collect_sentiment_extended()` |
| 降级 | 权限不足同样走 availability 机制 |

### Task 3.3 — pytest（~60 行）

文件：`tests/test_v013_phase3.py`

| 用例 | 验证 |
|------|------|
| `test_risk_scanner_financial_signals` | 报表类信号触发 |
| `test_risk_scanner_market_signals` | 市场类信号触发 |
| `test_risk_scanner_coverage` | ≥15/17 可自动判定 |
| `test_bull_bear_sections` | 多头链/空头链/分歧点齐全 |
| `test_no_placeholder_sections` | 报告无「将于 P2 实现」类占位 |
| `test_sentiment_percentile_output` | 情绪指标含分位数 |
| `test_cv8_erp_put_call_short` | CV-8 在模块 6 出现 |

---

## 验收命令

```bash
uv run python skills/invest-A/scripts/invest.py report 000001 --deep --outdir ./out
uv run pytest skills/invest-A/scripts/tests/test_v013_phase3.py -v
uv run pytest
```

## 验收清单

- [ ] 模块 5 含多头逻辑链、空头逻辑链、1-2 个关键分歧点
- [ ] `risk_scanner` 覆盖 17 信号中 ≥15 个可自动判定项
- [ ] 模块 7 含触发信号表 + Known Unknowns 列表
- [ ] 九模块报告**无占位节**
- [ ] 情绪指标输出分位数（非仅绝对值）
- [ ] CV-8 完整落地
- [ ] ETF 资金流向在模块 3b 有输出或明确标注不可得
- [ ] pytest 全绿

---

## 文件变更总览

| 文件 | 变更 | 行数 |
|------|------|------|
| `lib/risk_scanner.py` | 新建 | +150 |
| `lib/render.py` | `_section_bull_bear()` + 模块 7 集成 | +80 |
| `lib/collector.py` | 情绪指标 + ETF 资金 | +50 |
| `tests/test_v013_phase3.py` | 新建 | +60 |

---

## 风险与缓解

| 风险 | 缓解 |
|------|------|
| `opt_daily` 需 2000 积分 | availability 降级；CV-8 可部分缺失并标注缺口 |
| 创新高占比计算量大 | 可缓存全市场 daily；或采样近似并标注 |
| Bull/Bear 易滑向投资建议 | 模板固定「逻辑链」格式；禁止操作建议词 pytest |
| 技术替代需 WebSearch | 引擎标「待 Agent 验证」；不伪造结论 |

---

## 下一阶段

完成本阶段后进入 [Phase 4：跨时点与阅读体验](./invest-A-v013-phase4-cross-timepoint-ux.md)。
