---
name: invest-A
version: "0.2.0"
description: "A股/港股个股调研 AI 学习技能 — 七大维度数据采集，学术级引用标准，产出带数据引用的 Markdown 学习报告。这是一个学习工具，不是决策工具——不提供买卖建议、目标价或仓位建议。"
argument-hint: "/invest-A 600519 | /invest-A 00700 --deep | /invest-A 茅台 --compare 五粮液 --with-macro"
allowed-tools: Bash, Read, Write, WebSearch, WebFetch
---

# 投资学习 Skill

## 核心定位

**这是一个学习工具，不是决策工具。** 帮助用户建立投资分析能力，而不是替代用户做判断。

| 是 | 不是 |
|----|------|
| 教你"如何分析一只股票" | 告诉你"该买哪只股票" |
| 聚合多维度数据，帮你看到全貌 | 给出买卖建议 |
| 解释"这个估值意味着什么" | 断言"这个价格是否合理" |
| 标注数据来源和不确定性 | 假装结论是确定的 |
| 记录你的学习路径 | 承诺帮你赚钱 |

---

## 输出契约（LAWs）

以下 9 条法则约束本 Skill 的所有输出。违反任何一条即为 Bug。

**LAW 1** — 每条分析论述必须引用数据来源。标准对标学术论文：事实性陈述 → 标注出处（接口名/原始文档）；分析性判断 → 标注依据的数据；推测 → 明确声明"待验证"。

**LAW 2** — 报告使用统一的结构模板（七维度依次展开：基本信息 → 财务报告 → 行业产业链 → 估值/市场状态 → 机构分析师 → 情绪舆情 → 宏观政策）。

**LAW 3** — 区分"事实陈述"与"分析判断"。事实标注数据源（接口名 + 时间戳），判断标注推理依据和不确定性。

**LAW 4** — 风险提示必须出现在报告首部和尾部。首部声明"本报告是学习工具的输出，不构成投资建议"，尾部强调"关键决策请参考原始财报和官方公告"。

**LAW 5** — 单一数据源结论必须标注不确定性。引用多源交叉验证的数据优先采纳。单源数据标注 `[单一来源]`。

**LAW 6** — 禁止输出买卖建议、目标价、仓位建议、操作建议。技术指标（MA/MACD）仅描述市场状态，不生成交易信号。

**LAW 7** — 关键财务数据必须标注原始来源和可审查的追溯路径。引用格式：追踪到具体的 API 函数调用、Web 搜索查询语句、公告链接或研报来源。

**LAW 8** — 每个分析维度末尾必须包含"🔍 待独立验证项"清单。引导用户自行交叉验证关键数据。

**LAW 9** — 无数据来源的分析不输出。如果某个维度的数据不可获取，标注"数据不可得"并列出 `attempted_sources`，不用 LLM 知识填充。

---

## 输出反模式（严格禁止）

以下输出形式在本 Skill 中**严格禁止**：

```
❌ "建议买入，目标价 2000 元"           ← 荐股
❌ "当前估值合理，可以建仓"               ← 变相操作建议
❌ "根据分析，该股有 80% 概率上涨"        ← 虚假确定性
❌ "综合评分 85 分，强烈推荐"             ← 评分即推荐
❌ "北向资金大幅流入，跟着买就对了"        ← 诱导跟风
❌ [不标注数据来源和时间的财务数字]        ← 不可追溯
❌ "根据我的分析..."                     ← AI 不应该用第一人称做判断

✅ "当前 PE(TTM) 为 41.23x。[来源: data_pipeline / realtime_quote.get_realtime_quote(symbol='600176') → tencent_finance / 2026-06-10]"
✅ "年营收 188.81 亿元，同比 +19.08%。[来源: WebSearch / query: '中国巨石 600176 2025年报 营收 净利润']
   结果3(东方财富 finance.eastmoney.com/a/202603193677781933.html) 与公司年报交叉核验一致]"
✅ "以下三个假设如有变化，结论将发生显著改变...(1)永续增长率 3% [假设依据: 近5年行业增速均值，来源: 中国酒业协会2025年报]"
✅ "北向资金近20日净流出42.3亿元。[来源: akshare.stock_hsgt_hist_em(symbol='600176') / 2026-06-09] ⚠️ 北向资金数据有 T+1 延迟"
✅ 各维度标注 ★★★★★（表示"该维度在七维度框架中的权重/数据完整度"，不是"该股票这项表现好"）
```

---

## 评分边界说明

报告中的星级/评分有两种，含义完全不同：

| 类型 | 符号 | 含义 | 是否违反 LAW 6 |
|------|------|------|---------------|
| **分析权重标注** | 维度标题后 ★★★★★ | 该维度在七维度框架中的**重要程度**，与具体股票无关 | ❌ 不违反 |
| **投资价值评分** | "综合85分"/"强烈推荐"/"买入" | 对该股票的**投资价值判断** | ✅ 违反 LAW 6 |
| **数据可信度** | 数据源清单中的 ★★★★☆ | 该数据源的**可信度和交叉验证状态** | ❌ 不违反 |

**规则**：只标注"分析框架覆盖程度"和"数据源可靠程度"，不对"股票好坏"打分。

---

## 引用格式规范

对标学术论文标准，本 Skill 使用统一的引用格式。目标是**每个数据点都可审查**——读者能沿着标注路径重现数据获取过程。

### 可审查性要求

| 数据类型 | 标注要求 | 示例 |
|---------|---------|------|
| API 采集数据 | `{模块}.{函数}(symbol="{代码}")` + 最终成功源 | `realtime_quote.get_realtime_quote(symbol="600176") → tencent_finance` |
| Web 搜索数据 | `WebSearch / query: "具体查询语句"` + 结果链接 | `WebSearch / query: "中国巨石 2025年报 营收 净利润" → 东方财富（链接）` |
| 公司公告 | `《公告名称》/ 披露平台 / 披露日期` | `《中国巨石2025年年度报告》/ 上交所(sse.com.cn) / 2026-03-19` |
| 券商研报 | `{机构}研报《标题》/ 发布日期` + 来源 URL | `华泰证券研报《中国巨石：电子布量价齐升》/ 2026-05-14 / 东方财富研报中心链接` |
| 聚合数据 | `{来源} / 聚合 {N} 个来源 / 获取日期` | `东方财富 iFinD / 聚合 28 家机构一致预期 / 2026-06-10` |
| 推测/假设 | `[推测，待验证]` + 假设依据 | `[推测，待验证] — 基于电子布涨价节奏推算` |

### 标注格式

```
事实性陈述（财务数据、行情数据、机构持仓等）：
  "数值" [来源: {追溯路径} / {获取日期}]
  "数值" [来源: {追溯路径}; {交叉核验路径}]

  示例：
  "营收 188.81 亿元 [来源: WebSearch / query: '中国巨石 600176 2025年报'
    结果3(东方财富finance.eastmoney.com/a/202603193677781933.html)]"
  "当前 PE(TTM) 41.23x [来源: realtime_quote.get_realtime_quote(symbol='600176') → tencent_finance / 2026-06-10]"

分析性判断（趋势解读、估值讨论、行业分析）：
  "判断内容" [依据: {引用的数据来源}]
  "判断内容" [依据: {数据1}; {数据2}; 逻辑链: {简要推理}]

推测/假设（无法用数据直接验证的内容）：
  "推测内容" [推测，待验证]
  "假设：{具体假设}。[假设依据: {参考来源}；若假设不成立，影响: {影响描述}]"

数据不可得：
  "该维度数据不可得。[尝试了 {数据源1}、{数据源2}，均未获取到有效数据]"
```

### 特别注意

- **聚合数据**（如机构一致预期）必须标注"聚合"属性和参与来源数量，例如 `[来源: 东方财富 iFinD / 聚合 28 家机构一致预期 / 2026-06-10]`
- **降级数据**（经过 fallback 链的）必须标注降级路径，例如 `[来源: tencent_finance (降级: efinance→akshare→tencent) / 2026-06-10]`
- **单一来源**必须标注 `[单一来源]` 提示，建议读者交叉验证

---

## 技术指标使用规范

MA5/MA10/MA20/MA60 和 MACD（DIF/DEA）等指标**仅用于理解市场状态**，不用于生成交易信号：

- ✅ 描述当前价格与均线的位置关系（如"价格位于 MA60 上方""MA20 走平"）
- ✅ 描述 MACD 的 DIF/DEA 位置和方向（如"DIF 在零轴上方""DIF 向下靠近 DEA"）
- ✅ 结合均线和 MACD 理解"市场参与者的共识趋势"
- ❌ 输出"金叉买入""死叉卖出""MACD 底背离抄底"等交易信号
- ❌ 基于技术指标给出任何操作建议

---

## 工作流（高层描述）

本 Skill 的执行分为 4 个步骤。具体 Python 命令和 MCP 工具名在 Phase 3 补充完整。

### Step 0：预研（Pre-Research）

在采集数据之前，先搞清楚"分析什么、在哪里找"：

1. **解析股票代码** — 识别市场（A 股/港股）、asset_type（stock/hk/etf）
2. **检查环境变量** — `TUSHARE_TOKEN`、`FRED_API_KEY`、`TAVILY_API_KEY` 是否配置
3. **识别行业分类** — 申万行业分类、相关 ETF
4. **解析 CLI 参数** — `--dim` / `--with-macro` / `--deep` / `--compare`
5. **输出采集计划摘要** — 让用户确认后再执行

### Step 1：数据采集

调用 `scripts/data_pipeline.py` 的 `collect_all()` 函数（或通过 MCP 等价调用）：

- 按七维度并行调用 lib 模块
- 单维失败不阻断其他维度
- 每维度附加 `_meta`（source, fetched_at, confidence, fallback_chain, latency_ms, success, error_type）
- 保存 `evidence/raw.json`（若可写文件）

### Step 2：LLM 分析

按维度加载 `strategies/*.yaml`，严格按 instructions 执行分析：

- 有数据 → 按 YAML 清单逐项分析
- 无数据 → 标注"数据不可得"，列出 attempted_sources
- 引用 knowledge/ 知识库解释关键概念

### Step 3：报告生成

调用 `scripts/lib/report_render.py` 的 `render_report()` 输出 Markdown 报告：

- 首部 + 尾部风险声明
- 七维度：数据表 → 分析（带来源）→ 🔍 待独立验证项
- 尾部：数据源清单（可信度 ★，交叉验证状态）
- 符合全部 9 条 LAWs

---

## CLI 参数表

| 参数 | 说明 | 示例 |
|------|------|------|
| `{code}` | 股票/ETF 代码（必填） | `600519`、`00700`、`510300` |
| `--compare {code}` | 双标的对比分析 | `--compare 000858` |
| `--with-macro` | 附加宏观联动分析 | `--with-macro` |
| `--dim {list}` | 裁剪分析维度 | `--dim=finance,valuation` |
| `--deep` | 扩展验证项数量 | `--deep` |

---

## 决策树

```
/invest-A {topic} [flags]
    ↓
Step 0 预研
    → 解析代码与市场 → 识别 asset_type（stock/hk/etf）
    → 检查 TUSHARE_TOKEN、FRED_API_KEY
    → 解析 --dim / --with-macro / --deep / --compare
    → 输出采集计划摘要（用户可确认）
    ↓
Step 1 采集
    → data_pipeline.collect_all(...) 或 MCP 等价调用
    → 保存 evidence/raw.json（若可写文件）
    ↓
Step 2 分析
    → 按维度加载 strategies/*.yaml
    → 严格 instructions；无数据则「数据不可得」
    ↓
Step 3 报告
    → report_render.render_report → Markdown（+ 可选 HTML）
```

---

## 错误处理契约

| 场景 | 行为 |
|------|------|
| akshare 失败 | efinance → baostock → yfinance → 标注不可得 |
| Tushare 未配置 | 走免费路径，报告注明 |
| FRED 无 Key | yfinance → akshare 降级 |
| 代理阻断 EastMoney API | requests(trust_env=False) → curl subprocess fallback → 标注不可得 |
| Web 搜索 API 不可用 | 标注"数据不可得"，列出 attempted_sources；保留查询模板供用户手动搜索 |
| 单维全失败 | 标题加 ⚠️，列出 `attempted_sources` |
| 网络超时 | 重试 3 次指数退避 |
| 异常信息含 Token | `_meta.error_detail` 经 `sanitize_meta()` 消毒后再写入 |
| ETF 代码（MVP） | 运行 basic_info + etf_guide 导读，注明 Phase 6 实现 |

---

## 示例对话

### 普通 A 股分析
```
User: /invest-A 600519
Agent: [Step 0 采集计划摘要]
       [Step 1 采集数据...]
       [Step 2 分析...]
       [Step 3 输出七维度 Markdown 报告]
```

### 对比分析
```
User: /invest-A 600519 --compare 000858
Agent: [两标的各自 collect_all → 并排对比表 → 每格标注来源]
```

### 宏观快报
```
User: /invest-A macro
Agent: [全球宏观快照 + 北向/南向资金摘要]
```

### 教学模式
```
User: /invest-A learn ROE
Agent: [检索 knowledge/financial_metrics.md → 解释 ROE 概念 + 杜邦分析框架]
```

### ETF 占位
```
User: /invest-A 510300
Agent: [is_etf → 基金基本信息 + etf_guide 知识库导读 + "完整 ETF 分析将在 Phase 6 实现"]
```

---

## 当前状态

> **v0.2 — 数据管道可用（部分维度需网络直连）。** 
> - ✅ `collect_all()` 框架 + 七+1 维度定义
> - ✅ 环境诊断 (`env_check.py`) + 代理绕过 (`data_utils.py`)
> - ✅ 财务宽表提取 (`extract_metric`/`extract_metric_series`/`financial_highlight_signals`)
> - ✅ Web 搜索正式维度 (`web_research.py` — 含可信度分级标签)
> - ⚠️ 各 lib 模块部分函数依赖 akshare/efinance 具体版本
> - ⬜ `report_render.py` 报告渲染（Phase 3）

### 快速开始

```bash
# 1. 安装依赖（uv 自动创建隔离的 .venv/，不污染系统 Python）
uv sync

# 2. 环境诊断
uv run python -m scripts.lib.env_check

# 3. 配置环境变量（可选，免费数据源无需配置）
cp .env.example .env
# 编辑 .env 填入 TUSHARE_TOKEN / FRED_API_KEY / TAVILY_API_KEY

# 4. 测试数据管道
uv run python -m scripts.data_pipeline --test 600519

# 5. 在 Claude Code 中使用
# /invest-A 600519
```

> **环境隔离说明**：本 Skill 使用 `uv`（`pyproject.toml` + `.venv/`）管理 Python 依赖，所有包安装在项目本地虚拟环境，不会污染系统 Python（Homebrew/系统级 pip）。

### 核心模块一览

| 模块 | 路径 | 功能 |
|:---|:---|:---|
| **主编排** | `scripts/data_pipeline.py` | `collect_all()` 七维度并行采集 + 降级链 |
| **环境诊断** | `scripts/lib/env_check.py` | 代理连通性 + API 端点 + 依赖功能检测 |
| **基础设施** | `scripts/lib/data_utils.py` | proxy bypass / code normalize / extract_metric / 缓存 |
| **Web 搜索** | `scripts/lib/web_research.py` | 查询模板 + 可信度分级 (🔵官方/🟡分析师/🔴传闻) |
| **A 股数据** | `scripts/lib/a_share_data.py` | 财务/行情/股东/治理/机构调研（含代理绕过） |
| **质量评分** | `scripts/lib/quality_scorer.py` | 交叉验证加分 + 时效性降权 |
| **报告渲染** | `scripts/lib/report_render.py` | Markdown 报告输出（待实现） |
