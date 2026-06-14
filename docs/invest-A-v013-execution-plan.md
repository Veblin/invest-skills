# invest-A v0.1.3 执行计划

> 基于 `docs/invest-A-blueprint-v013-final.md` 生成
> 日期: 2026-06-14（v4：对齐蓝图四阶段划分）
> 当前代码基线: v0.1.2 (commit `430479f`)
>
> **阶段命名对照：** 本计划 P0/P1/P2/P3/P4 ↔ 蓝图 Phase 1/2/3/4（蓝图 §十）

---

## 一、执行概览

```
总工期: P0–P2 约 6 周；P3（Markdown 体验）可并行；HTML 可视化延后至结构稳定后
新增代码: ~960 行（不含 SKILL.md 文档）
含文档总量: ~1,160 行（P0 代码 ~560 行 + SKILL ~200 行 + P1 ~430 行）
修改文件: 7 个现有文件 + 2 个新建文件（LAW 文档 + risk_scanner）
核心转变: 从"静态基本面扫描"升级为"多因子交叉验证的动态投研助手"
```

### 行数口径说明

| 口径 | 数值 | 说明 |
|------|------|------|
| 蓝图 Phase 0 代码 | ~530 行 | 不含 SKILL.md 文档 |
| 本计划 P0 代码 | ~560 行 | 新增 snapshot / bull_bear 占位 / 交叉验证对落地 |
| 本计划 P0 含文档 | ~760 行 | 加上 SKILL.md ~200 行 |
| Phase 0 + Phase 1 代码 | ~960 行 | 与蓝图 §十一 一致 |

### 关键里程碑（对齐蓝图 §十 四阶段）

| 阶段 | 蓝图 | 本计划 | 工期 | 可验证标准 |
|------|------|--------|------|-----------|
| **1** | Phase 1 动态投研内核 | P0 | 1-2 周 | 模块 0/1/2/3/6 完整；能回答「现在为什么在动」 |
| **2** | Phase 2 基本面与估值 | P1 | ~2 周 | 模块 4 完整；12 核心题 + LAW 15 |
| **3** | Phase 3 风险与分歧 | P2 | ~2 周 | 模块 5/7 完整；九模块无占位 |
| **4** | Phase 4 跨时点与体验 | P3 | 1-2 周 | diff/watchlist + Markdown UX |
| **—** | HTML backlog | 稳定后 | TBD | 结构冻结后再投入 |

### 输出格式策略（v0.1.3 硬性约束）

> **迭代期只维护 Markdown 渲染器。** HTML 与 Markdown 双轨维护成本高，且九模块结构仍在变化；等项目稳定后再恢复 HTML 为一等公民。

| 输出格式 | CLI | v0.1.3 范围 | 说明 |
|---------|-----|------------|------|
| **Markdown** | `report SYMBOL`（**默认**）或 `--emit md` | ✅ 完整 9 模块 | **唯一主交付物**；stdout 或 `--outdir` 写 `.md` 文件 |
| **compact / json** | `--emit compact` / `--emit json` | ✅ 跟随新结构 | 机器可读摘要 |
| **HTML** | `--emit html`（**须显式指定**） | ⚠️ 冻结 v0.1.2 旧模板 | **不随 v0.1.3 九模块演进**；调用时打印弃用提示，指向 Markdown |

**CLI 变更（Task 0.5）：**
- `pr.add_argument("--emit", default="md", ...)` — 默认从 `html` 改为 `md`
- 默认行为：stdout 输出九模块 Markdown（`render_report_v3()`）
- `--outdir`：写入 `{ts}-{symbol}-{name}.md`（不再默认写 HTML）
- `--emit html`：仍可调旧版 `render_html()`，但打印：`⚠️ HTML 为 v0.1.2 旧版，迭代期请使用默认 Markdown 输出`

**P0–P2 不做的事：**
- 不升级 `render_html()` 对齐九模块
- 不为 HTML 模板写回归测试
- 不在 P0/P1/P2 里程碑中验收 HTML 结构

---

## 二、Phase 0：动态驱动能力（1-2 周，代码 ~560 行）

> **核心原则：先让"解释当下"的能力成立，再扩展静态分析深度。**

### 2.0 前置任务（开工前）

#### Task 0.pre — 产出 LAW 10-16 源文档

| 项目 | 说明 |
|------|------|
| 文件 | `docs/invest-A-SKILL-LAW10-16.md`（**新建**） |
| 来源 | 以蓝图 §七 + §八 为唯一权威；**不得**直接粘贴 `invest-A-SKILL-LAW10-15.md` |
| 与旧稿差异 | ① 新增 LAW 16；②「学习提示卡」→「分析提示」；③「初学者」→「分析者/研究者」 |
| 依赖 | 无 |
| 阻塞 | Task 0.0 依赖本任务完成 |

**验收：** 文档含 LAW 10-16 全文 + 12 核心题 + 扩展激活条件表；与蓝图 §七 逐条对照无遗漏。

---

### 2.1 任务分解

#### Task 0.0 — SKILL.md 升级 + 版本同步（文档）

| 项目 | 说明 |
|------|------|
| 文件 | `skills/invest-A/SKILL.md` 及版本联动文件 |
| 内容 | 写入 LAW 10-16 + 分层激活框架问题集 + 身份声明更新 |
| 工作量 | SKILL.md ~200 行 + 版本文件 ~10 行 |
| 依赖 | Task 0.pre |

**SKILL.md 变更点：**
- metadata 版本号改为 `0.1.3`
- description 改为"多因子交叉验证的结构化投研助手"
- 新增"身份声明"章节（做什么/不做什么）
- 从 `docs/invest-A-SKILL-LAW10-16.md` 粘贴 LAW 10-16 + 分层激活问题集
- 更新报告结构为 9 模块
- 删除一切"学习器/初学者/学习闭环"语境

**版本同步清单（AGENTS.md 发布规范）：**

| 文件 | 字段 |
|------|------|
| `skills/invest-A/SKILL.md` | frontmatter `version: "0.1.3"` |
| `pyproject.toml` | `version = "0.1.3"` |
| `.claude-plugin/plugin.json` | `"version": "0.1.3"` |
| `gemini-extension.json` | `"version": "0.1.3"` |
| `.claude-plugin/marketplace.json` | 同步插件版本 |
| `.agents/plugins/marketplace.json` | 同步插件版本 |
| `CHANGELOG.md` | 新增 v0.1.3 条目 |
| `skills/invest-A/scripts/lib/render.py` | `ENGINE_VERSION = "0.1.3"` |

**免责声明语气迁移（与 Task 0.4 同步）：**
- `_header_v2()` / `_risk_footer()` 中"学习研究参考" → "研究备忘录，仅供信息整理与多因子分析参考"
- `render_html()` **本轮不改动**（HTML 冻结；稳定后再统一迁移）

---

#### Task 0.1 — `lib/schema.py` 新增数据结构（~30 行）

| 项目 | 说明 |
|------|------|
| 文件 | `skills/invest-A/scripts/lib/schema.py` |
| 新增 | `DriverFactor`, `CrossValidation`, `ProbabilityStructure` dataclass |
| 依赖 | 无 |

```python
@dataclass
class DriverFactor:
    """8 因子矩阵中的单个因子"""
    category: str        # 基本面/行业景气/资金/情绪/技术/事件
    signal: str          # 具体信号描述
    direction: str       # ↑正向 / ↓负向 / →中性
    strength: str        # ✅ / ⚠️ / ❓
    source: str          # 数据来源

@dataclass
class CrossValidation:
    """多数据源交叉验证结果"""
    status: str          # convergence / divergence / gap
    data_pair: str       # 验证的数据对（蓝图 §5.3 编号）
    detail: str          # 验证结论
    reliability: str     # 可靠度评估

@dataclass
class ProbabilityStructure:
    """左侧/右侧概率结构"""
    left_supports: list   # 左侧依据列表
    right_supports: list  # 右侧依据列表
    triggers: list        # 走势转变触发条件
    trend_position: str   # 当前趋势位置描述（描述性，非结论）
```

---

#### Task 0.2 — `lib/collector.py` 新增 `collect_market_structure()`（~90 行）

| 项目 | 说明 |
|------|------|
| 文件 | `skills/invest-A/scripts/lib/collector.py` |
| 新增 | `collect_market_structure()` 函数 |
| 依赖 | Tushare 2000 积分（moneyflow, hsgt_top10, margin, daily_basic, sw_daily） |
| 降级 | 权限不足时标注 `[数据源不可用，该因子跳过]`，不阻塞 |

**采集内容：**

| 数据项 | Tushare 接口 | 说明 |
|--------|------------|------|
| 申万行业指数 | `sw_daily` | 近 60 日板块走势 + 相对大盘强弱 |
| 北向资金（个股） | `hsgt_top10` | 近 10 日个股北向成交净额（2000 积分）|
| 融资/融券余额 | `margin` | 近 10 日融资余额变化 + 融券余额增速（2000 积分）|
| 主力资金流向 | `moneyflow` | 近 5 日大单净额（2000 积分）|
| 换手率 | `daily_basic` | 近 5 日 vs 60 日均值（2000 积分）|
| ERP 计算 | `index_dailybasic` + FRED `DGS10` | 沪深300 EP - 10Y 国债；输出 **5 年分位数** |

**接口取舍说明（蓝图内部口径统一）：**

| 场景 | 选用接口 | 理由 |
|------|---------|------|
| 个股北向（8 因子矩阵、资金态度） | `hsgt_top10` | 个股粒度，与 `moneyflow` 可交叉验证 |
| 全市场北向情绪（左侧指标，P2） | `moneyflow_hsgt` | 宏观情绪，非个股因子 |

**P0 暂不采集（后续阶段）：**

| 数据项 | 阶段 | 接口 |
|--------|------|------|
| ETF 资金流向 | P2 | `fund_daily` / 行业 ETF 持仓变化 |
| 50ETF 认沽认购比 | P2 | `opt_daily`（蓝图 §十二-5） |
| 创新高个股占比 | P2 | 基于 `daily` 全市场计算 |
| ETF 折溢价率 | P2 | `etf_nav` + `daily` |

**输出规范：**
- 情绪类指标（换手率、ERP、融资余额增速）输出 **分位数**（如"近 5 年第 82 分位"），非仅绝对值（蓝图 §6.2）
- `availability` 字段记录每个子源的成功/失败/权限不足状态
- 技术指标（RSI/MACD/MA）**复用** `technical.py` 对 kline 的计算，不重复调 `stk_factor`

```python
def collect_market_structure(symbol: str, *, industry: str | None = None) -> dict:
    """采集市场结构数据，权限不足时部分降级"""
    results = {
        "sw_index": None,
        "northbound": None,       # hsgt_top10 个股
        "margin": None,           # 含 margin_balance + short_balance 增速
        "moneyflow": None,
        "turnover": None,         # 含 5日/60日均值 + 分位数
        "erp": None,              # 含 raw_value + percentile_5y
        "availability": {},
    }
    # 每个数据源独立 try/except，失败写入 availability
    return results
```

---

#### Task 0.3 — `lib/render.py` 新增 section 函数（~400 行）

| 项目 | 说明 |
|------|------|
| 文件 | `skills/invest-A/scripts/lib/render.py` |
| 新增 | 8 个 section/工具函数 |
| 依赖 | Task 0.1（schema）, Task 0.2（collector）|

**新增函数清单：**

| 函数 | 行数 | 模块 | 说明 |
|------|------|------|------|
| `_section_research_question()` | ~80 | 0 | 四类触发源检测 + 问题树（LAW 11） |
| `_section_snapshot()` | ~50 | 1 | 价格/估值/财务/公告摘要 + **多源一致性标注** |
| `_section_dynamic_drivers()` | ~120 | 2 | 候选解释 + 8 因子矩阵 + 主导因子（LAW 13） |
| `_section_market_structure()` | ~60 | 3 | 行业情绪 + 资金态度 + 情绪指标 |
| `_section_bull_bear_placeholder()` | ~15 | 5 | P0 占位："模块 5 完整辩论逻辑将于 P2 实现" |
| `_section_left_right_probability()` | ~100 | 6 | 趋势位置 + 概率依据 + 触发条件（LAW 16） |
| `_section_risk_scanner_placeholder()` | ~20 | 7 | P0 占位，P1 替换为 `risk_scanner` 输出 |
| `_cross_validation_block()` | ~60 | 跨模块 | 印证/分歧/缺口标注 |
| `_evidence_conclusion_block()` | ~40 | 跨模块 | 统一证据强度（LAW 12） |

**`_section_snapshot()` 核心逻辑（蓝图 §四 模块 1）：**

```
输入: collection（含 dimensions + _meta.all_sources）
输出: 当前状态快照

步骤:
1. 汇总 quote / valuation / financials 关键字段
2. 近 N 日涨跌幅 + 估值快照（PE/PB 分位）
3. 近期公告摘要（若有 events 维度）
4. 多源一致性：对同一字段比对 all_sources 中各渠道值
   - 一致 → 🟢 印证
   - 矛盾 → 🟡 分歧（注明哪两个源、差值）
   - 仅单源 → 🔴 缺口
```

**P0 交叉验证数据对落地（蓝图 §5.3）：**

| 编号 | 数据对 | 实现阶段 | 调用位置 |
|------|--------|---------|---------|
| CV-1 | 净利润 vs 经营现金流 | P0 | 模块 4（沿用 `_section_quality`）/ 模块 1 摘要 |
| CV-2 | 营收增长 vs 应收账款增长 | P1 | `_section_fundamentals_layered()` |
| CV-3 | PE 历史分位 vs PB 历史分位 | P0 | 模块 1 快照 / 模块 4d |
| CV-4 | 北向净流入 vs 主力大单净额 | P0 | 模块 3 `_section_market_structure()` |
| CV-5 | 申万板块指数 vs 个股相对强弱 | P0 | 模块 3 |
| CV-6 | MA 趋势 vs 近期业绩方向 | P0 | 模块 2 因子交叉验证 |
| CV-7 | PE 历史低位 vs 资金持续流出 | P0 | 模块 3 / 模块 6 左侧依据 |
| CV-8 | ERP 分位 vs 融券余额增速 | P2 | 模块 6（认沽认购比 P2 补入） |

**候选解释分工（引擎 vs Agent）：**

| 内容 | 责任方 | 说明 |
|------|--------|------|
| 候选解释骨架（5 条上限、证据槽位） | 引擎 `render.py` | 模板 + 数据填充 |
| 新闻/公告类解释 | Agent（SKILL.md 工作流） | 引擎输出 `events` 占位 + WebSearch 指引 |
| 商业风险「技术替代」 | P1 `risk_scanner` 触发 + Agent 定性 | 引擎只标"待 WebSearch 验证" |

**`_section_left_right_probability()` 约束：**
- 复用 `technical.py` 的 RSI/MACD/MA 输出
- 左侧指标输出分位数（ERP、估值分位、RSI 区间）
- **禁止**输出"当前是左侧"或"当前是右侧"字样（P0 测试用正则扫描）

---

#### Task 0.4 — `render_report_v3()` 新渲染器（~150 行）

| 项目 | 说明 |
|------|------|
| 文件 | `skills/invest-A/scripts/lib/render.py` |
| 策略 | **保留** `render_report_v2()` 为 legacy；新建 `render_report_v3()` |
| 内容 | 9 模块 Markdown 组装 |

**迁移方案：**

```python
def render_report_v2(collection, symbol):
    """v0.1.2 八段模板（legacy，供回滚）。"""
    ...

def render_report_v3(collection, symbol):
    """v0.1.3 九模块研究备忘录。"""
    ...

def render(collection, symbol, *, version: str = "v3"):
  if version == "v2":
    return render_report_v2(collection, symbol)
  return render_report_v3(collection, symbol)
```

**`render_report_v3()` 模块调用顺序：**

| # | 模块 | 函数 | P0 状态 |
|---|------|------|---------|
| 0 | 研究问题卡 | `_section_research_question()` | 完整 |
| 1 | 当前状态快照 | `_section_snapshot()` | 完整 |
| 2 | 动态驱动分析 | `_section_dynamic_drivers()` | 完整 |
| 3 | 市场结构分析 | `_section_market_structure()` | 完整 |
| 4 | 静态基本面 | `_section_quality()` + 估值节 | P0 沿用 v0.1.2；P1 换 `_section_fundamentals_layered()` |
| 5 | 市场分歧 | `_section_bull_bear_placeholder()` | 占位 |
| 6 | 左侧/右侧概率 | `_section_left_right_probability()` | 完整 |
| 7 | 风险与不确定性 | `_section_risk_scanner_placeholder()` | 占位 |
| 8 | 附录 | `_references_appendix()` + 技术精简 3 行 | 完整 |

**跨模块调用：**
- 各节结论经 `_evidence_conclusion_block()` 标注强度
- CV-1/3/4/5/6/7 在对应模块内调用 `_cross_validation_block()`
- P0 长节可用 Markdown `<details>` 折叠（纯 MD 兼容，不依赖 HTML 渲染器）

**`render()` 路由更新：** `invest.py` 及 `render()` 默认路由到 `render_report_v3()`。

---

#### Task 0.5 — `invest.py` 适配（~40 行）

| 项目 | 说明 |
|------|------|
| 文件 | `skills/invest-A/scripts/invest.py` |
| 修改 | `cmd_report()` + `report` 子命令参数默认值 |
| 内容 | 默认 Markdown 输出 + `collect_market_structure()` + `render_report_v3()` |

**CLI 默认值变更：**

```python
# argparse：默认 emit 从 html → md
pr.add_argument("--emit", default="md", choices=["compact", "json", "md", "html"])
pr.add_argument("--outdir", default="", help="报告输出目录（默认仅 stdout；指定则写 .md 文件）")
```

**`cmd_report()` 主路径（默认 `--emit md`）：**

```python
result["market_structure"] = collector.collect_market_structure(
    args.symbol, industry=_extract_industry(result),
)

md = render.render_report_v3(result, args.symbol)

if args.outdir:
    # 写 {ts}-{symbol}-{name}.md
    ...
else:
    print(md)
```

**`--emit html` 分支（显式 opt-in，冻结维护）：**
- 仍调用 v0.1.2 `render_html()`，**不**接入 `render_report_v3()`
- 打印弃用警告，建议改用默认 Markdown
- 不在 v0.1.3 CHANGELOG 中宣传 HTML 能力

**深度模式策略：**
- v0.1.3 报告生成**默认**调用 `collect_market_structure()`（不依赖 `--deep`）
- `--deep` 仍控制 kline 730 日范围及行业/舆情扩展采集（保持现有语义）

---

#### Task 0.6 — P0 单元测试（~80 行）

| 项目 | 说明 |
|------|------|
| 文件 | `skills/invest-A/scripts/tests/test_v013_p0.py`（新建） |
| 框架 | pytest（已有 `uv run pytest`） |

| 测试用例 | 验证 |
|---------|------|
| `test_collect_market_structure_degrades_gracefully` | mock Tushare 权限失败 → `availability` 标注 |
| `test_cross_validation_convergence` | CV 块输出 🟢/🟡/🔴 |
| `test_left_right_no_single_conclusion` | 输出不含 `当前是左侧\|当前是右侧` |
| `test_dynamic_drivers_matrix_rows` | 8 因子表格 ≥6 行（允许部分跳过） |
| `test_render_report_v3_nine_modules` | 输出含 9 个 `##` 二级标题 |
| `test_report_default_emit_is_md` | `invest.py` argparse `--emit` 默认值为 `md` |
| `test_render_report_v2_legacy_unchanged` | legacy 仍可调用 |

---

### 2.2 P0 文件变更总览

| 文件 | 变更类型 | 行数 | 风险 |
|------|---------|------|------|
| `docs/invest-A-SKILL-LAW10-16.md` | **新建** | ~400 行文档 | 低（阻塞项） |
| `SKILL.md` + 版本文件 + CHANGELOG | 修改 | ~210 行 | 低 |
| `lib/schema.py` | 修改 | +30 行 | 低 |
| `lib/collector.py` | 修改 | +90 行 | 中（Tushare 积分） |
| `lib/render.py` | 修改 | +550 行 | 高（核心逻辑） |
| `invest.py` | 修改 | +40 行 | 低 |
| `tests/test_v013_p0.py` | **新建** | +80 行 | 低 |
| **P0 代码小计** | | **~775 行** | |
| **P0 含文档** | | **~1,185 行** | |

---

### 2.3 P0 测试策略

| 测试对象 | 测试方法 | 验证标准 |
|---------|---------|---------|
| `collect_market_structure()` | pytest + 000001 实跑 | 各字段有值或 `availability` 标注不可用 |
| `_section_snapshot()` | mock `all_sources` | 含多源一致性 🟢/🟡/🔴 |
| `_section_dynamic_drivers()` | mock 数据 | 候选解释 + 8 因子表 + 主导因子 |
| `_section_left_right_probability()` | mock + 正则 | 无确定性左侧/右侧结论 |
| `_evidence_conclusion_block()` | 单元测试 | ✅⚠️❓ 映射块完整 |
| 端到端（默认） | `uv run python invest.py report 000001` | 9 模块 Markdown 输出到 stdout |
| 端到端（写文件） | `... report 000001 --outdir ./reports` | 生成 `.md` 文件 |
| HTML 回归 | `... report 000001 --emit html` | 旧版 HTML 仍可生成 + 弃用警告 |
| 回归 | `uv run pytest` | 全量通过 |

---

## 三、Phase 1：静态基本面增强（2-4 周，~430 行）

### 3.1 任务分解

#### Task 1.0 — 分层激活基本面问题集（~150 行）

| 项目 | 说明 |
|------|------|
| 文件 | `skills/invest-A/scripts/lib/render.py` |
| 新增 | `_section_fundamentals_layered()` |
| 替换 | `render_report_v3()` 模块 4 从 `_section_quality()` 切换 |

**核心题（12 题）与扩展激活条件：** 见蓝图 §八（与 Task 0.0 SKILL.md 一致）。

**LAW 10 格式（每题末尾）：**
```
[分析提示]
- 为什么重要：[定价传导路径，引用本次数据]
- 常见分析误区：[引用本次至少一个数据点构造场景]
- 下一步交叉验证：[1-3 个具体动作]
```

**兼容策略：** 数据不足 → `数据不足：[缺少什么]`，不跳过题目（LAW 14）。

---

#### Task 1.1 — `lib/risk_scanner.py`（新建，~150 行）

| 项目 | 说明 |
|------|------|
| 文件 | `skills/invest-A/scripts/lib/risk_scanner.py`（新建） |
| 测试 | `tests/test_risk_scanner.py`（新建，~60 行） |
| 内容 | 三层风险 17 个定量触发条件（蓝图 §九） |

```python
def scan_financial_risks(financials: dict) -> list[dict]: ...   # 7 信号
def scan_business_risks(financials, industry) -> list[dict]: ... # 4 信号（技术替代→触发 Agent WebSearch）
def scan_market_risks(valuation, northbound, technical) -> list[dict]: ...  # 6 信号
def risk_report(...) -> dict: ...
```

**集成：** `render_report_v3()` 模块 7 替换 placeholder → 调用 `risk_report()` + Known Unknowns 列表。

---

#### Task 1.2 — `lib/valuation.py` 增强（~80 行）

| 项目 | 说明 |
|------|------|
| 文件 | `skills/invest-A/scripts/lib/valuation.py` |
| 新增 | `implied_growth()` + `pe_band_series()` |
| 测试 | `tests/test_implied_growth.py`（~40 行） |

**`implied_growth()`** — LAW 15 戈登反推（蓝图 §七）：

```python
def implied_growth(pe_ttm, risk_free_rate, erp=0.06) -> dict:
    """g_implied ≈ r - 1/PE；PE>50 返回 warning"""
```

**`pe_band_series()`** — PE Band **数据层**（蓝图 Phase 1 含 PE Band，交互图留 P3）：

```python
def pe_band_series(daily_basic_rows: list[dict], years: int = 5) -> dict:
    """返回各日 PE 及 ±1σ/±2σ 轨道，供 Markdown 文本表渲染（交互图留稳定后 HTML backlog）"""
```

> **阶段拆分说明：** 蓝图 §十一 Phase 1 将 PE Band 与 `implied_growth` 同批实现。
> 本计划将 **数据计算放 P1**、**Markdown 文本表放 P3**、**交互图表留稳定后 HTML backlog**。

---

#### Task 1.3 — `lib/collector.py` 新增 `collect_industry_peers()`（~80 行）

| 项目 | 说明 |
|------|------|
| 文件 | `skills/invest-A/scripts/lib/collector.py` |
| 新增 | `collect_industry_peers()` |
| 内容 | 申万三级同行池 + PE/PB/ROE/营收增速分位排名（上限 10 家） |

---

### 3.2 P1 文件变更总览

| 文件 | 变更类型 | 行数 |
|------|---------|------|
| `lib/render.py` | 修改 | +150 行 |
| `lib/risk_scanner.py` | **新建** | +150 行 |
| `lib/valuation.py` | 修改 | +80 行 |
| `lib/collector.py` | 修改 | +80 行 |
| `tests/test_risk_scanner.py` | **新建** | +60 行 |
| `tests/test_implied_growth.py` | **新建** | +40 行 |
| **P1 总计** | | **~560 行** |

---

## 四、Phase 2：分析深度与跨时点（4-6 周）

| 任务 | 说明 | 文件 |
|------|------|------|
| Bull/Bear 辩论 | `_section_bull_bear()` 替换 placeholder（蓝图 §四 模块 5） | `render.py` |
| 快照 diff | 跨时点变化感知 | `store.py`, `invest.py` |
| Watchlist 批量运行 | `cmd_watchlist()` 批量变化摘要 | `invest.py` |
| 情绪指标增强 | 50ETF 认沽认购比、创新高占比、ETF 折溢价 | `collector.py` |
| ETF 资金流向 | 模块 3b 补全 | `collector.py` |
| CV-8 完整 | ERP + 认沽认购比 + 融券余额增速 | `render.py` |

**P2 启动条件：** P1 完成 + P0 pytest 全绿。

---

## 五、Phase 3：Markdown 体验增强（可与 P2 并行，不阻塞发布）

| 任务 | 说明 | 依赖 |
|------|------|------|
| PE Band 文本表 | `pe_band_series()` 渲染为 Markdown 表格 | P1 Task 1.2 |
| 研究框架流程图 | Mermaid 代码块嵌入 Markdown 报告 | SKILL.md |
| `<details>` 章节折叠 | 长报告阅读体验（纯 MD） | Task 0.4 |
| 报告目录（TOC） | Markdown 顶部锚点链接 | `render_report_v3()` |

> P3 **不包含 HTML**。HTML 可视化列入「稳定后 backlog」，见 §5.1。

### 5.1 稳定后 backlog：HTML 可视化（结构冻结后启动）

**启动条件（全部满足）：**
- [ ] 九模块 Markdown 结构连续 2 个小版本无 breaking change
- [ ] P0–P2 功能完整，pytest 全绿
- [ ] 团队确认愿意承担双轨维护成本

| 任务 | 说明 |
|------|------|
| `render_html_v3()` | 从 `render_report_v3()` 单一数据源生成 HTML（避免双轨漂移） |
| ECharts 图表 | K 线、均线、估值历史分位 |
| PE Band 交互图 | 消费 P1 `pe_band_series()` |
| 章节侧边栏导航 | 模块间跳转 |
| CLI 评估 | 是否恢复 `--emit html` 为默认（届时再议） |

---

## 六、风险与缓解

| 风险 | 影响 | 缓解措施 |
|------|------|---------|
| Tushare 积分不足（2000） | 资金/情绪因子缺失 | `availability` 检测 → `[数据源不可用，该因子跳过]` |
| LAW 16 在 prompt 层被破坏 | 单一左侧/右侧结论 | SKILL.md + `render.py` 模板双层约束 + pytest 正则 |
| ERP 依赖 FRED 不可用 | 左侧指标缺口 | 降级中国 10Y 国债（akshare macro） |
| 报告过长（800-1000 行） | 阅读体验差 | P3 Markdown TOC + `<details>` 折叠 |
| HTML 双轨维护成本 | 迭代期结构频繁变动 | **v0.1.3 起默认仅 MD**；HTML 冻结至稳定后 |
| 用户习惯默认 HTML | 升级感知 | CHANGELOG 说明；`--emit html` 保留旧行为 |
| `render_report_v2` 回归 | 破坏现有用户 | 保留 legacy + `render(version="v2")` |
| LAW10-15 旧稿误用 | 定位/法规回退 | Task 0.pre 阻塞 Task 0.0 |

---

## 七、执行顺序建议

```
第 0 天:   Task 0.pre（LAW10-16 源文档）          ← 阻塞项
第 1 天:   Task 0.0（SKILL + 版本同步）+ Task 0.1（schema）  并行
第 2-3 天: Task 0.2（collect_market_structure）
第 4-6 天: Task 0.3a（research_question + snapshot + dynamic_drivers）
第 7-8 天: Task 0.3b（market_structure + left_right + placeholders + CV/evidence）
第 9 天:   Task 0.4（render_report_v3）+ Task 0.5（invest.py）
第 10 天:  Task 0.6（pytest）+ 端到端 + 修 bug
第 11-14 天: P0 缓冲 + 代码审查
         ↓
======== P0 交付（Markdown 九模块）========
         ↓
第 3-4 周: Task 1.0 + Task 1.1（可并行）
第 5-6 周: Task 1.2 + Task 1.3（可并行）
         ↓
======== P1 交付 ========
         ↓
第 7-10 周: P2
第 7+ 周: P3 Markdown 体验（可与 P2 并行）
         ↓
======== v0.1.3 可发布（Markdown 完整）========
         ↓
结构稳定后: HTML backlog（§5.1）
```

---

## 八、成功标准

### P0 完成标准
- [ ] `docs/invest-A-SKILL-LAW10-16.md` 存在且含 LAW 16
- [ ] 四处版本号 + CHANGELOG 已同步为 `0.1.3`
- [ ] `uv run python invest.py report 000001`（无参数）生成 **9 模块** Markdown 到 stdout
- [ ] `--emit` 默认值为 `md`（非 `html`）
- [ ] 模块 1 含多源一致性标注（🟢/🟡/🔴）
- [ ] 模块 2 含候选解释 + 8 因子矩阵 + 主导因子声明
- [ ] 模块 5/7 为占位节（注明 P2/P1 实现）
- [ ] 模块 6 含概率结构 + 触发条件，**无**"当前是左侧/右侧"
- [ ] CV-1/3/4/5/6/7 至少在对应模块出现
- [ ] 数据不可用标注 `[数据源不可用，该因子跳过]`，不阻断生成
- [ ] `uv run pytest` 全绿（含 `test_v013_p0.py`）
- [ ] `--emit html` 仍可调用旧版 HTML，并打印弃用警告
- [ ] **不**验收 HTML 与九模块结构对齐（明确 out of scope）

### P1 完成标准
- [ ] 12 道核心必答题全部出现（数据不足时写明缺少什么）
- [ ] `risk_scanner.py` 覆盖 17 信号中 ≥15 个可自动判定项
- [ ] 模块 7 替换 placeholder，含 Known Unknowns
- [ ] LAW 15 `implied_growth()` 输出完整
- [ ] `pe_band_series()` 数据可输出文本表
- [ ] 可比公司分位排名正确显示
- [ ] CV-2 在基本面模块落地

---

## 九、蓝图对照自检表

| 蓝图章节 | 要求 | 本计划任务 | 状态 |
|---------|------|-----------|------|
| §四 模块 0 | 研究问题卡 | Task 0.3 | ✅ |
| §四 模块 1 | 当前状态快照 + 多源一致 | Task 0.3 `_section_snapshot` | ✅ |
| §四 模块 2 | 动态驱动 E | Task 0.3 | ✅ |
| §四 模块 3 | 市场结构 | Task 0.3 | ✅（ETF→P2） |
| §四 模块 4 | 分层激活基本面 | P1 Task 1.0 | ✅ |
| §四 模块 5 | Bull/Bear | P0 占位 → P2 完整 | ✅ |
| §四 模块 6 | 左/右概率 F | Task 0.3 | ✅ |
| §四 模块 7 | 风险 17 信号 | P0 占位 → P1 | ✅ |
| §五 交叉验证 8 对 | CV-1~8 | §2.1 Task 0.3 表 | ✅ |
| §七 LAW 10-16 | 方法论 | Task 0.pre + 0.0 | ✅ |
| §九 风险引擎 | 17 触发条件 | P1 Task 1.1 | ✅ |
| §十 PE Band | Phase 1 数据 | P1 数据 / P3 图表 | ✅（拆分） |
| §十二 报告长度 | TOC / 折叠 | P3 Markdown TOC + `<details>` | ✅ |
| §十 P3 HTML 可视化 | 蓝图原 P3 | **延后至稳定后 backlog** | ⚠️ 有意偏差 |
| CLI 默认输出 | — | 默认 `md`，HTML 须 `--emit html` | ✅ 本计划新增 |

---

## 十、参考文件

- `docs/invest-A-blueprint-v013-final.md` — 需求蓝图（权威输入）
- `docs/invest-A-SKILL-LAW10-16.md` — LAW 源文档（**Task 0.pre 新建**）
- `docs/invest-A-SKILL-LAW10-15.md` — 旧稿，**仅供参考，禁止直接粘贴**
- `skills/invest-A/scripts/lib/render.py` — 报告渲染引擎
- `skills/invest-A/scripts/lib/collector.py` — 数据采集
- `skills/invest-A/scripts/lib/valuation.py` — 估值分析
- `skills/invest-A/scripts/lib/technical.py` — RSI/MACD/MA（左/右模块复用）
- `skills/invest-A/scripts/lib/schema.py` — 数据结构
- `skills/invest-A/SKILL.md` — 技能定义（LAW 1-9，v0.1.2）
- `skills/invest-A/scripts/invest.py` — CLI 入口
- `AGENTS.md` — 版本发布与协作约束
