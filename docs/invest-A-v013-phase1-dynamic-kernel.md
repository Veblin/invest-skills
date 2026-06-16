# Phase 1：动态投研内核

> v0.1.3 子计划 1/4 · 目标版本 `v0.1.3-alpha`
> 权威来源：`docs/invest-A-blueprint-v013-final.md` §十 Phase 1
> 工期：1–2 周 · 新增代码约 ~560 行 + 测试 ~80 行

---

## 阶段命题（验收一句话）

**能回答：「这家公司现在为什么在动、左/右概率结构如何、什么条件会让走势转变？」**

---

## 前置条件

- 代码基线：v0.1.2
- 无硬性外部依赖；Tushare 2000 积分可提升资金/情绪因子覆盖，但非阻塞

---

## 本阶段交付范围

### 报告九模块状态

| 模块 | 内容 | 状态 |
|------|------|------|
| 0 研究问题卡 | 四类触发源 + 问题树（LAW 11） | ✅ 完整 |
| 1 当前状态快照 | 价格/估值/财务摘要 + 多源一致性 | ✅ 完整 |
| 2 动态驱动分析 | 候选解释 + 8 因子矩阵 + 主导因子（LAW 13） | ✅ 完整 |
| 3 市场结构分析 | 行业情绪 + 资金态度 + ERP/换手（LAW 12） | ✅ 完整 |
| 4 静态基本面 | 分层激活 12 题 | ⏸ 占位（沿用 v0.1.2 `_section_quality`） |
| 5 市场分歧 | Bull vs Bear | ⏸ 占位 |
| 6 左/右概率判断 | 概率结构 + 触发条件（LAW 16） | ✅ 完整 |
| 7 风险与不确定性 | 17 信号扫描 | ⏸ 占位 |
| 8 附录 | 技术精简 + 来源追溯 | ✅ 完整 |

### 本阶段落地的 LAW

| LAW | 要点 |
|-----|------|
| LAW 10 | 分析提示格式（占位节可简略，完整落地见 Phase 2） |
| LAW 11 | 研究问题卡四类触发源 + 问题树 |
| LAW 12 | 证据强度三级（✅⚠️❓）+ `_evidence_conclusion_block()` |
| LAW 13 | 候选解释上限 5 条 + 主导因子声明 |
| LAW 16 | 左/右概率结构，禁止单一结论 |

### 本阶段落地的交叉验证（CV）

| 编号 | 数据对 | 位置 |
|------|--------|------|
| CV-1 | 净利润 vs 经营现金流 | 模块 1/4 摘要 |
| CV-3 | PE 分位 vs PB 分位 | 模块 1 快照 |
| CV-4 | 北向 vs 主力大单 | 模块 3 |
| CV-5 | 申万板块 vs 个股相对强弱 | 模块 3 |
| CV-6 | MA 趋势 vs 近期业绩方向 | 模块 2 |
| CV-7 | PE 低位 vs 资金流出 | 模块 3/6 |

---

## 任务分解

### Task 1.pre — LAW 10–16 源文档（阻塞项）

| 项目 | 说明 |
|------|------|
| 文件 | `docs/invest-A-SKILL-LAW10-16.md`（新建） |
| 来源 | 蓝图 §七 + §八，**不得**直接粘贴 `invest-A-SKILL-LAW10-15.md` |
| 验收 | 含 LAW 10–16 全文 + 12 核心题 + 扩展激活条件表 |

### Task 1.0 — SKILL.md 升级 + 版本同步

| 文件 | 变更 |
|------|------|
| `skills/invest-A/SKILL.md` | LAW 10–16、身份声明、9 模块结构、删除「学习器」语境 |
| `pyproject.toml` / `.claude-plugin/plugin.json` / `gemini-extension.json` | `version: 0.1.3` |
| `CHANGELOG.md` | v0.1.3-alpha 条目 |
| `lib/render.py` | `ENGINE_VERSION = "0.1.3"` |

### Task 1.1 — `lib/schema.py` 数据结构（~30 行）

新增 dataclass：`DriverFactor`、`CrossValidation`、`ProbabilityStructure`。

### Task 1.2 — `collect_market_structure()`（~90 行）

**采集项：**

| 数据项 | Tushare 接口 | 说明 |
|--------|------------|------|
| 申万行业指数 | `sw_daily` | 近 60 日 + 相对大盘强弱 |
| 北向（个股） | `hsgt_top10` | 2000 积分 |
| 融资/融券 | `margin` | 2000 积分 |
| 主力资金 | `moneyflow` | 2000 积分 |
| 换手率 | `daily_basic` | 5 日 vs 60 日均值 + 分位数 |
| ERP | `index_dailybasic` + FRED `DGS10` | 5 年分位数 |

**P1 暂不采集（后续阶段）：** 50ETF 认沽认购比、创新高占比、ETF 折溢价、ETF 资金流向。

输出 `availability` 字段；权限不足写 `[数据源不可用，该因子跳过]`。

### Task 1.3 — `lib/render.py` section 函数（~400 行）

| 函数 | 模块 | 说明 |
|------|------|------|
| `_section_research_question()` | 0 | LAW 11 四类触发源 |
| `_section_snapshot()` | 1 | 多源一致性 🟢🟡🔴 |
| `_section_dynamic_drivers()` | 2 | 候选解释 + 8 因子 + 主导因子 |
| `_section_market_structure()` | 3 | 行业情绪 + 资金 + ERP/换手 |
| `_section_left_right_probability()` | 6 | LAW 16 概率结构 |
| `_section_bull_bear_placeholder()` | 5 | 占位 |
| `_section_risk_scanner_placeholder()` | 7 | 占位 |
| `_cross_validation_block()` | 跨模块 | 印证/分歧/缺口 |
| `_evidence_conclusion_block()` | 跨模块 | LAW 12 |

### Task 1.4 — `render_report_v3()`（~150 行）

- 保留 `render_report_v2()` 为 legacy
- 新建 `render_report_v3()` 组装九模块 Markdown
- `render()` 默认路由 v3

### Task 1.5 — `invest.py` 适配（~40 行）

- `--emit` 默认 `md`
- `cmd_report()` 挂钩 `collect_market_structure()` + `render_report_v3()`
- `--emit html` 打印弃用警告，仍输出 v0.1.2 旧模板

### Task 1.6 — pytest（~80 行）

文件：`tests/test_v013_phase1.py`

| 用例 | 验证 |
|------|------|
| `test_collect_market_structure_degrades_gracefully` | 权限失败 → availability |
| `test_cross_validation_convergence` | CV 块 🟢/🟡/🔴 |
| `test_left_right_no_single_conclusion` | 无「当前是左侧/右侧」 |
| `test_dynamic_drivers_matrix_rows` | 8 因子表 ≥6 行 |
| `test_render_report_v3_nine_modules` | 9 个 `##` 二级标题 |
| `test_report_default_emit_is_md` | argparse 默认 md |

---

## 验收命令

```bash
uv run python skills/invest-A/scripts/invest.py report 000001
uv run python skills/invest-A/scripts/invest.py report 000001 --outdir ./out
uv run pytest skills/invest-A/scripts/tests/test_v013_phase1.py -v
uv run pytest   # 全量回归
```

## 验收清单

- [ ] `docs/invest-A-SKILL-LAW10-16.md` 存在且含 LAW 16
- [ ] 四处版本号 + CHANGELOG 已同步 `0.1.3`
- [ ] 模块 0/1/2/3/6/8 内容完整；模块 4/5/7 有明确占位说明
- [ ] 含候选解释 + 8 因子矩阵 + 主导因子声明
- [ ] 左/右章节无确定性「当前是左侧/右侧」结论
- [ ] 数据不可用正确标注，不阻断生成
- [ ] `--emit` 默认 `md`；`--emit html` 有弃用警告
- [ ] CV-1/3/4/5/6/7 在对应模块出现
- [ ] pytest 全绿

---

## 风险与缓解

| 风险 | 缓解 |
|------|------|
| Tushare 2000 积分不足 | `availability` 降级，跳过该因子 |
| LAW 16 被 prompt 破坏 | SKILL.md + render 模板双层约束 + pytest 正则 |
| ERP 依赖 FRED 不可用 | 降级 akshare 宏观国债数据 |
| render 改动面大 | 保留 `render_report_v2()` 可回滚 |

---

## 下一阶段

完成本阶段后进入 [Phase 2：基本面与估值体系](./invest-A-v013-phase2-fundamentals-valuation.md)。
