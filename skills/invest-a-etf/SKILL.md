---

name: invest-a-etf
version: "0.3.0"
description: "A股 ETF 结构化研究 — 指数估值/折溢价/AUM/跟踪质量/对冲覆盖，产出带来源追溯的研究备忘录。研究工具，非决策工具。共用数据层供 invest-a-journal ETF 路径调用。触发词：ETF/指数基金"
whenToUse: "ETF/指数基金类问题：指数估值、折溢价、AUM、跟踪质量、对冲覆盖的结构化研究"
argument-hint: "/invest-a-etf 563300 | /invest-a-etf 515790"
allowed-tools: Bash, Read, Write, WebSearch
user-invocable: true
metadata:
  requires:
    bins: [uv, python3]
---

# invest-a-etf — ETF 研究助手

> **工具约束**：`allowed-tools` 是 Claude Code 约定，跨 harness 语义不同（部分平台不读该字段，可用工具由平台沙箱决定）。本技能主体为本地 Bash/Python 采集与计算，部分维度经 WebSearch 补充。

## 概述

你是 ETF 研究助手。用户通过 `/invest-a-etf {代码}` 请求对单只 ETF 做结构化研究。你的职责：

1. **采集**：调用共用数据引擎 `etf_data.py`（指数 PE、折溢价、AUM、净值波动、对冲覆盖）
2. **合成**：按 [references/report-template.md](references/report-template.md) 产出 Markdown 研究备忘录
3. **标注**：每个数字带来源；推测标注「待验证」；遵守 LAW 6 / 6a

**研究工具，非决策工具。** 不做买卖/仓位建议。需要评估「我要买/卖这只 ETF 的方案」时，引导用户用 `/invest-a-journal`。

本 Skill 是 **ETF 数据模块的 canonical 拥有者**。`invest-a-journal` 在 ETF 评估路径上复用同一模块（journal 侧为 thin shim）。

运行时经 path bootstrap（`skills/lib/invest_path.py` → skill-local `_invest_path` shim）依赖 invest-a-stock 的 `lib.nums` / `lib.proxy` / `lib.technical`。

---

## 硬约束

> **共享规范**：[report-conventions.md](../../../skills/lib/references/report-conventions.md) §2 硬约束 + §3 措辞规范 + §6 多情景参考。

1. **禁止买卖建议、仓位建议**
2. **允许多情景估值参考价**（须假设前提 + 概率权重 +「仅供参考，不构成投资建议」）
3. **禁止无假设的单一目标价**
4. **允许交易结构分析**：情景锚定入场区间、假设失效触发、操作纪律（非「建议买入/止损」指令）
5. **ETF 用指数 PE**，不用个股 PE 套路分析 ETF
6. **技术指标仅描述状态**（价格相对 MA、RSI 区间位置），不输出交易信号；RSI 须标注 `rsi_period`
7. **措辞规范**详见共享规范 §3（禁止词替换表 + 已知违规模式）
8. **证据强度标注**详见共享规范 §5（SOP-EV 四维标注 + [事实]/[分析] 块格式）
9. **事实边界（共享规范 §2.3，最高优先级）**：禁止猜测/推断/幻觉。引擎没有的字段写「未知/不可得」，不推断；「检索不到」不得断言「数据不存在」（可能被付费墙/权限遮挡），只允许「公开不可独立验证」；数据冲突并列报告不自行裁决；每个数字必须带来源（引擎字段 / `[来源: Python calc: formula]` / 一手源）；无法核实的数字标注三态（可验证 / 公开不可独立验证 / 未知）

---

## 工作流

```
用户: /invest-a-etf 563300
       ↓
Claude: 确认 6 位代码
       ↓
采集（并行；**适用条件与产出见「条件采集表」**——此处只列命令）:
  cd "${INVEST_SKILLS_ROOT:-.}" && uv run python skills/invest-a-etf/scripts/etf.py report SYMBOL --json
  cd "${INVEST_SKILLS_ROOT:-.}" && uv run python skills/invest-a-etf/scripts/etf.py industry-pe
  cd "${INVEST_SKILLS_ROOT:-.}" && uv run python skills/invest-a-etf/scripts/etf.py holdings SYMBOL --json     （行业/主题 ETF）
  cd "${INVEST_SKILLS_ROOT:-.}" && uv run python skills/invest-a-etf/scripts/etf.py peers SYMBOL --json        （行业 ETF；未映射加 --peers "代码,代码"）
  cd "${INVEST_SKILLS_ROOT:-.}" && uv run python skills/invest-a-etf/scripts/etf.py sector-flow SYMBOL --json  （行业 ETF）
  cd "${INVEST_SKILLS_ROOT:-.}" && uv run python skills/invest-a-etf/scripts/etf.py futures-basis SYMBOL --json（映射到可用期货时）
       ↓
Claude: 合成分析（见下方「分析合成」节）→ 写入 reports/{symbol}-{name}/{timestamp}.md
       ↓
机器层准出（**先于 HTML**）: cd "${INVEST_SKILLS_ROOT:-.}" && uv run python skills/lib/report_qc.py <刚写的 md> --fail-on error
       ↓
复盘原料 sidecar（**必做**——无假设的报告也要落盘，否则「有/没有 sidecar」不可机器区分）:
  cd "${INVEST_SKILLS_ROOT:-.}" && uv run python skills/invest-a-etf/scripts/etf.py decision SYMBOL --init > /tmp/{symbol}.decision.json   # ① 取最小 schema 模板
  （② 填写 scenarios / falsifiers —— 情景假设与可证伪条件是合成段产物，只有你能写）
  cd "${INVEST_SKILLS_ROOT:-.}" && uv run python skills/invest-a-etf/scripts/etf.py decision SYMBOL --from /tmp/{symbol}.decision.json   # ③ 校验并落盘（fail-loud 退出 2）
       ↓
默认（复检通过后必做）: etf.py html SYMBOL --md <已过复检的 md> --no-open  → 同目录交互式 HTML（详见「HTML 产物」节）

**盘后预采集（不在本工作流内，报告只读其产物）**：
  cd "${INVEST_SKILLS_ROOT:-.}" && uv run python skills/invest-a-etf/scripts/etf.py collect-sector-flow   # 行业资金流快照（同花顺 90 行业×4 窗口，幂等，非交易日跳过）
  cd "${INVEST_SKILLS_ROOT:-.}" && uv run python skills/invest-a-stock/scripts/invest.py etf-flow SYMBOL --save  # 份额快照（**invest-a-stock 命令**，需逐日积累）
  两者都**写**快照表，不在报告流程内并行调用——否则读侧可能拿到旧快照（`sector-flow` 的积累序列 ≥6 日才成立）

**报告文件命名规则**：
- `{timestamp}` = 报告生成时的实际时间，格式 `YYYY-MM-DD-HH-MM-SS`（北京时间）
- `{name}` = ETF 简称（如 `科创50ETF`、`通信ETF`、`卫星ETF`）
- 示例：`reports/588000-科创50ETF/2026-07-27-19-40-00.md`
- 写入文件前必须获取当前实际时间，禁止使用硬编码时间戳
- **同 symbol 只允许一个报告目录**：名称以 hedge-map `ETF_HEDGE_MAP[symbol]["index"]` 为准——
  512660 → `512660-军工ETF`（历史目录名 `512660-军工ETF国泰/` 勿再使用）
       ↓
引导: 若用户有仓位方案要评估 → /invest-a-journal
```

**重要**：你不只是数据搬运工。你的核心价值是**连接数据点、发现矛盾、锁定关键变量**。每个数字都要追问"这意味着什么？对投资者的决策有什么影响？"

### CLI

```bash
cd "${INVEST_SKILLS_ROOT:-.}" && uv run python skills/invest-a-etf/scripts/etf.py report 563300        # 单 ETF 数据快照
cd "${INVEST_SKILLS_ROOT:-.}" && uv run python skills/invest-a-etf/scripts/etf.py report 563300 --json
cd "${INVEST_SKILLS_ROOT:-.}" && uv run python skills/invest-a-etf/scripts/etf.py report 588000 --history --playbook   # 历史深度 + 情景预案
cd "${INVEST_SKILLS_ROOT:-.}" && uv run python skills/invest-a-etf/scripts/etf.py report 588000 --events events/588000.json --history  # 指定事件文件（须同带 --history 才有价格对齐）
cd "${INVEST_SKILLS_ROOT:-.}" && uv run python skills/invest-a-etf/scripts/etf.py diagnose
cd "${INVEST_SKILLS_ROOT:-.}" && uv run python skills/invest-a-etf/scripts/etf.py industry-pe          # 31 行业 PE 排名
cd "${INVEST_SKILLS_ROOT:-.}" && uv run python skills/invest-a-etf/scripts/etf.py collect-weekly       # 手动触发行业 PE 采集
cd "${INVEST_SKILLS_ROOT:-.}" && uv run python skills/invest-a-etf/scripts/etf.py holdings 159206 --json      # 前十大持仓 + 集中度
cd "${INVEST_SKILLS_ROOT:-.}" && uv run python skills/invest-a-etf/scripts/etf.py peers 159206 --json          # 赛道资金流对比 + RS（自动发现）
cd "${INVEST_SKILLS_ROOT:-.}" && uv run python skills/invest-a-etf/scripts/etf.py peers 159206 --peers "512660,512760"   # 显式赛道清单
cd "${INVEST_SKILLS_ROOT:-.}" && uv run python skills/invest-a-etf/scripts/etf.py sector-flow 159206 --json   # 行业资金流 + 趋势（同花顺）
cd "${INVEST_SKILLS_ROOT:-.}" && uv run python skills/invest-a-etf/scripts/etf.py collect-sector-flow         # 每日采集（盘后，幂等）
cd "${INVEST_SKILLS_ROOT:-.}" && uv run python skills/invest-a-etf/scripts/etf.py html 515050 --md <报告md路径> --no-open   # 交互式 HTML 报告（研究流程中固定加 --no-open，不自动开浏览器）
cd "${INVEST_SKILLS_ROOT:-.}" && uv run python skills/invest-a-etf/scripts/etf.py decision 515050 --init          # 复盘原料：输出最小 schema 模板
cd "${INVEST_SKILLS_ROOT:-.}" && uv run python skills/invest-a-etf/scripts/etf.py decision 515050 --from <填好的.json>   # 校验并落盘 sidecar
cd "${INVEST_SKILLS_ROOT:-.}" && uv run python skills/invest-a-etf/scripts/etf.py review 515050                   # 复盘纪要（只对照假设状态）
```

`report` 输出引擎数据快照（供 Claude 合成）；完整叙事由 Claude 按模板撰写。

### 复盘原料 sidecar（`decision` / `review`）

设计：`host-docs/v0.3.0/review-material-design.md`。

报告只写一次就冻结，没有机器可读的东西记录「当时假设了什么、什么条件下算错、何时该回看」。
sidecar 把这三样结构化落盘，使复盘可批量、到期可核验：

- **落点**：与报告 md **同目录同 ts**——`reports/{symbol}-{name}/{ts}.decision.json`（**必须能配到某一份报告**；无报告时显式失败，不落无主的 sidecar）
- **谁写**：**Claude 写**（情景假设与证伪条件是合成段），引擎只做**校验 + 消费**
- **最小 schema**：只填 `schema_version/symbol/report_ts/as_of/disclaimer` 五键，`scenarios`/`falsifiers` 留空——无假设的报告**也要落盘**，否则「有/没有 sidecar」不可机器区分
- **校验 fail-loud**（退出 2）：情景参考价**必须**带 `assumption`（假设前提）+ `weight`（概率权重），`disclaimer` 必填（LAW 6）；`falsifiers[].due` 必填且为 `YYYY-MM-DD`（到期清单靠它核验）
- **`review <symbol>`** 产出**三段式复盘纪要**：① 报告序列 ② **证伪条件状态（按到期日排序，可机器核验）** ③ 假设对照
  - 到期状态由 `due` 与生成日**推导**（`⏰ 已过期` / `🔔 临近 N 日`），**不依赖 sidecar 里手写的 status**
  - 存量报告**无 sidecar 时显式列出**（不静默跳过）；纪要自身不参与报告序列（文件名 `-review.md`，防自我污染）
  - **只对照假设状态，不产生建议**（LAW 6）；产出后须过 `report_qc.py --fail-on error`（纪要为独立产物类型 `review`，不套研报结构检查）

### HTML 产物

**交互式 HTML 报告**（`html` 子命令）：引擎数据仪表盘（概览/持仓/估值/跟踪/历史/资金流等 8 节 + Chart.js 交互图表）+ 已过复检的报告 md 分析全文原样嵌入。单文件自包含（图表库内联），file:// 离线可用。

- **默认用法**：报告 md 写入后执行（默认动作），显式传 `--md` 指向刚写的文件——本工作流下你掌握确切路径；不同 harness 下报告目录位置不必假设一致

```bash
cd "${INVEST_SKILLS_ROOT:-.}" && uv run python skills/invest-a-etf/scripts/etf.py html 515050 --md reports/515050-通信ETF/2026-08-28-22-47-25.md
```

- `--md` 缺省时引擎自动取 `reports/{symbol}-*/` 中时间戳最新的 md（仅为便捷路径）；显式传参更稳妥
- `--out PATH`：自定义 HTML 输出路径（缺省与 md 同目录同名 `.html`）
- `--no-open`：不自动打开浏览器（仅生成文件）
- 自动打开：引擎进程内调用标准库 `webbrowser.open`（harness 无关，不依赖 shell `open`）；打开失败仅提示文件路径并以 0 退出，可按路径手动打开
- md 分析全文原样嵌入（不重写不摘要）；渲染失败（md 语法超出子集）会 fail-loud 并提示行号，此时检查报告是否含表格/引用块之外的语法

**历史与预案相关旗标**（`report` 子命令）：
- `--history`：历史行情深度（nav 链路优先，失败自动回退 baostock `sh.{code}`）+ 年度高低点/最大回撤/±5% 交易日/MA20-60-120/偏离% 统计
- `--history-days N`：历史回溯交易日数（默认 250，约 1 年）
- `--events PATH`：事件文件（JSON Lines，`{date, event, source_url, published_date, confidence}`）；缺省自动读 `events/{symbol}.json`，无文件不阻断
- `--playbook`：情景预案（回撤档位 σ 分级 + 三步核查清单 + LAW 6a 声明）

---

## 备忘录章节（必须覆盖）

标题与顺序见 [references/report-template.md](references/report-template.md)（下表 § 号即模板 § 号）。
**数据口径与解读纪律不在本节重复**——采集见「条件采集表」，解读见「分析合成」。

| § | 章节 | 什么条件下必须 |
|---|---|---|
| 1 | 产品快照（价格 / 折溢价 / AUM / flags） | 必须 |
| 2 | 持仓透视 | 行业 / 主题 ETF 必须 |
| 3 | 指数估值（含 **3.1 估值框架**、**3.2 行业位置**） | §3 必须；§3.1 / §3.2 行业 ETF 必须 |
| 4 | 跟踪质量（净值波动 / NAV+指数 MA / BOLL / RSI / 跟踪误差） | 必须 |
| 5 | 历史演变 | `--history` 时必须有（事件-价格对照需拉历史行情：`--history` **或** `--playbook` 均可；仅传 `--events PATH` 时引擎不拉历史行情，对照为空） |
| 6 | 赛道资金流对比 | 行业 ETF 必须 |
| 7 | 资金流向与趋势 | 行业 ETF 必须 |
| 7.5 | 动态基差与持仓 | 映射到可用期货时必须有；无映射时**显式写「该 ETF 无对应期货合约」**，不得省略不表 |
| 8 | 对冲覆盖（hedge-map） | 必须 |
| 9 | 因子 / 主题逻辑 | 必须（无来源则「待验证」） |
| 10 | 多情景参考 | 可选（LAW 6a） |
| 11 | 情景预案 | `--playbook` 时必须有 |
| 12 | 对抗性假设检验 | 必须（做法见「分析合成」§3） |
| 13 | 「致命一击」归纳 | 必须（做法见「分析合成」§4） |
| 14 | 盲点扫描 | 必须（做法见「分析合成」§4） |

> 报告正文**不出现内部迭代编号**（引擎开发期的功能代号）与版本痕迹——一律用自然语言标题指代。

**行业 ETF vs 宽基 ETF 的分析差异**：
- 宽基 ETF：核心问题是"这个市场便宜吗？"→ 聚焦 PE 分位（如有）
- 行业 ETF：核心问题是"这个行业处于什么周期位置？"→ 必须展开估值框架（§3.1）+ 行业排名（§3.2）
- 如果 `pe_timing=false`，必须解释**为什么 PE 不能用来择时**，以及应该用什么替代指标

---

## 数据引擎

### 条件采集表（条件采集条目的**单一定义处**）

> 「什么条件下采集什么、产出什么、怎么解读」的唯一真源。工作流、备忘录章节、
> 分析合成、Self-Check 只引用本表，不重复其细节。
> 「内部编号」列是引擎开发期的功能代号，**仅供维护侧追溯，不写入报告**。

| 内部编号 | 条目 | 命令 | 适用条件 | 关键产出 | 解读纪律 |
|---|---|---|---|---|---|
| R12 | 持仓透视 | `holdings SYMBOL --json` | 行业 / 主题 ETF | 前十大 + 集中度 top1/top5/top10 + 子环节聚类 `clusters` | 「分析合成」§0 |
| R13 | 赛道资金流对比 | `peers SYMBOL --json`（未映射加 `--peers "代码,代码"`） | 行业 ETF | 同赛道份额流 + RS（基准 = 同赛道等权均值） | 「分析合成」§0b |
| R15 | 行业资金流与趋势 | `sector-flow SYMBOL --json` | 行业 ETF | THS 3/5/10 日净额 + 窗口分解 + 积累序列（≥6 日） | 「分析合成」§0c |
| — | 行业 PE 排名 | `industry-pe` | 行业 ETF | 31 申万行业 PE/PB 排名 | §3.2 两档顺序；代理值须标注 |
| — | 动态基差与持仓 | `futures-basis SYMBOL --json` | 映射到可用期货时 | 基差 / 历史分位 + 持仓变化 | 状态度量非预测 |
| R11a/b | 历史深度 + 事件 | `report --history`（有事件文件再加 `--events PATH`） | 出 §5 时 | 年度高低点 / 最大回撤 / MA / ±5% 交易日；事件-价格对照（**对齐依赖历史行情**：未传 `--history`/`--playbook` 或历史行情源不可用时 `aligned` 为空） | 阶段划分由 AI 合成、数字引用引擎 |
| R11c | 情景预案 | `report --playbook` | 出 §11 时 | 回撤档位 σ 分级 = 触发核验深度 | 非操作阈值；禁用「无动作/如何应对/建议卖出/止损」 |
| — | 份额趋势 | `report --json` 的 `share_history` | 全部 ETF | 近 20 日份额 + 资金流估算 + OHLCV | 有数据则展示，无则「积累中」 |

### 引擎函数

| 函数 | 用途 |
|------|------|
| `query_etf_data(symbol)` | 指数 PE、行业 PE、分类、估值指引、折溢价、AUM、对冲、flags |
| `query_etf_quote(symbol)` | 现价、涨跌幅、成交 |
| `query_etf_kline(symbol)` | 净值序列、年化波动、NAV MA20/MA60、指数 MA20/MA60、BOLL、RSI（含 `rsi_period`） |
| `query_etf_kline_history(symbol, days)` | 历史行情深度（nav 链路优先，失败回退 baostock；`source: nav/baostock`） |
| `compute_history_stats(rows)` | 历史统计：年度高低点+日期、最大回撤（峰/谷日期）、±5% 交易日清单、MA20/60/120、当前 vs 高低点偏离% |
| `list_industry_snapshot()` | 31 个申万行业 PE/PB 排名 |
| `etf_share_flow(symbol, days)` | 读**本地快照表**的份额序列 + 估算资金流（T+1 lag 语义）。快照由 `invest-a-stock etf-flow SYMBOL --save` 逐日积累 → **不在本 skill 工作流内**；本 skill 的份额趋势走 `report --json` 的 `share_history` 字段（Tushare `fund_share`+`fund_daily`） |
| `query_etf_category(symbol)` | ETF 类型标签 |
| `query_sector_valuation_guide(sw_name)` | 行业特定估值指标指引 |
| `query_etf_holdings(symbol)` | 前十大持仓（裸 HTTP 天天基金 jjcc 页，季度报告期）+ 集中度 top1/top5/top10（引擎计算）+ 子环节聚类合计 clusters（HOLDINGS_CLUSTER_MAP 聚合，未映射归「未归类」） |
| `query_etf_peers(symbol, peers)` | 赛道资金流对比（Tushare 份额 20 日窗口）+ RS（基准=同赛道等权均值）；`--peers` 显式或 ETF_TO_SW_INDUSTRY 自动发现 |
| `etf_peer_rs(closes, bench, dates, window)` | RS 序列（同共享 relative_strength 口径：RS_t=(main/bench)×100×(b0/s0)），输出 rs_latest / rs_window_start / rs_change（三数字自洽）+ 末 20 点序列 |
| `fetch_sector_flow_snapshot()` | 同花顺行业资金流四窗口信封（即时/3/5/10 日，90 申万细分行业，大单口径亿元；东财断连独立源） |
| `decompose_flow(d3, d5, d10)` | 单时点窗口分解（近端 1-3 日 vs 中段 4-10 日，四象限标签：持续净流入/近端回流/近端退潮/持续净流出 + 强度；证据非信号） |
| `query_sector_flow(symbol)` | ETF→THS 行业资金流 + 趋势（3/5/10 日净额 + 窗口分解 + 积累序列 5 日变化率/转向，≥6 日；未映射提示） |

对冲表：[references/etf-hedge-map.md](references/etf-hedge-map.md)

### 指数 PE 状态（`index_pe_status`）

| 值 | 含义 |
|----|------|
| `mapped` | 在 CSINDEX_MAP 中，已尝试拉取 csindex PE |
| `not_mapped` | 在对冲表中但无 csindex 码（常见于行业/主题 ETF，如 515790） |
| `unknown_etf` | 不在已知映射表，需手动核实跟踪指数 |

### 自动 flags

> ⚠️ 以下阈值为**本工具的筛查阈值**（工程取值，非来自监管标准或学术文献），
> 用途是提示「值得看一眼」，**不是**对客观风险的断言。报告引用时须注明其筛查性质。

- AUM < 2 亿 → ❌ 清盘/流动性风险筛查位
- 溢价 > 2% → ⚠️ 交易价相对 NAV 偏离较大（同一时点买入成本高于净值）
- 折价 < -2% → ⚠️ 交易价相对 NAV 偏离较大（方向相反）——偏离成因未知，不推断为「结构问题」
- 对冲 coverage `none` → ⚠️ 无期货/期权对冲

---

## 分析合成（必选四步）

> **共享框架**：[report-conventions.md §4](../../../skills/lib/references/report-conventions.md) 分析合成框架（对抗性假设 / 致命一击 / 盲点）。以下为 ETF 视角扩展（增加估值框架展开 + 行业位置解读两步）。

报告按模板撰写完成后，**必须**执行以下六步合成（0/0b/1-4）。这不是 checklist——这是你的核心分析工作。

### 0. 持仓透视解读（行业/主题 ETF 必选）

`holdings` 数据（前十大名单 + 集中度）的核心分析价值 = **修正「名义主题 vs 实际暴露」偏差**：

- 名义主题（如「卫星产业」）vs 实际暴露（前十大若 8 只集中于制造环节 → 实际是「军工制造」）
- 集中度数字（top1/top5/top10，引擎计算）引用引擎字段，**AI 不得心算**
- 聚类合计引用引擎 `clusters` 字段（HOLDINGS_CLUSTER_MAP 聚合，AI 不心算）；未映射股票归入「未归类」，报告层补充归类须标注「AI 归类」；行级细标签（光模块/存储芯片等）为 AI 标注，与聚合分组区分
- 权重股事件风险快速筛查：前十大中是否有停牌/解禁/暴雷风险标的（无需深研基本面）

### 0b. 赛道资金流解读（行业 ETF 必选）

`peers` 数据（同赛道份额资金流 + RS）的解读边界：

- 资金流（20 日/近 5 日，Tushare 份额×均价估算，T+1 延迟）为**资金流主证据之一**，与 invest-a-pulse 主线确认原则一致
- RS（基准=同赛道等权均值）仅作**状态参考**，非交易信号；20 日收益排名描述相对强弱，不构成「接棒」预测
- 赛道口径（peer_source）含宽口径成员（如军工龙头）时注明，AI 可解释
- 主标的份额流 vs 同行对比：背离（如同行流入、本标的流出）是值得展开的矛盾点

### 0c. 资金流趋势解读（行业 ETF 必选）

`sector-flow` 数据（THS 行业 3/5/10 日净额 + 趋势）的解读边界：

- **三源对照**：同花顺行业净额（大单口径）+ 同赛道 ETF 份额流（配置口径）+ pulse 涨停热度（游资口径）——三源同向时只作**相互印证的事实陈述**（口径不同、不构成因果确认）；背离时作为**待验证的矛盾点**展开，**不得**给出「题材短炒/主力出货/资金进场」类动机或性质判定
- **证据非信号**：趋势标签（持续流入/近端回流/近端退潮/持续流出）只描述引擎判定的方向/强度事实，**禁止**据此做方向性预测
- **趋势与日期配合**：方向/强度标签一律引用引擎字段（近端加速/减速为引擎输出，受量级守卫约束），不做引擎之外的强度断言；5 日变化率/转向需积累序列（≥6 日快照），积累不足标注「积累中」不硬编，快照跨度 trend_span_days ≠7 日须标注
- 口径声明：大单口径、日间净额噪声；THS 90 为该源**覆盖口径**（申万细分 90 行业），东财仅定性对照、差异名注明——两者口径不同，**不裁定谁更权威**

### 1. 估值框架展开（行业 ETF 必选，宽基 ETF 可选）

如果 `valuation_guide` 存在（行业 ETF），必须展开分析：

- 解释 `primary`/`secondary` 指标为什么适用这个行业
- **如果 `pe_timing=false`，必须明确说**：PE 不能用来判断这个 ETF 的买卖时机。给出替代判断框架（如看 CAPEX、看出口增速、看政策节点）
- 如果 `pe_timing=true`，说明 PE 分位在什么范围对应什么历史情景

格式（报告内段落）：
> **估值框架**：通信行业 `pe_timing=false`——PE 不能用来择时。通信 ETF 的正确估值框架是：① 跟踪运营商 CAPEX（钱在不在投）；② 跟踪光模块出口增速（收入端）；③ PE=25.69 本身不告诉你是贵还是便宜。

### 2. 行业位置解读（行业 ETF 必选）

如果 `industry_pe` 存在，必须引用 `industry-pe` 命令输出的 31 行业排名：

- 该行业 PE 在全市场排第几？同赛道相对位置按模板 §3.2 的**两档**取（① TMT（电子/计算机/通信/传媒）→ 报 TMT 子组内位置；② 其余行业 → 只报全市场 31 行业排名）——**非 TMT 行业不得套用 TMT 口径，也不得自行编组**（引擎未定义可比行业组，编组即无来源方法论）
- 这个位置的含义是什么？（如"TMT 中最便宜，但这不意味低估——通信天然比半导体估值低"）
- ⚠️ 行业 PE 是代理值，非 ETF 精确 PE，必须标注

### 3. 对抗性假设检验

对报告中每个关键假设，找出其**可证伪条件** — 未来什么可观测数据会让这个假设不成立。**重点攻击你自己报告中最核心的判断，而不是边角料。**

格式（报告内表格）：

| 关键假设 | 可证伪条件 | 观测窗口 |
|----------|----------|:---:|
| "CAPEX 结构转型利好通信 ETF" | 前十大权重中光模块/算力设备占比 <30% | 需核实持仓 |
| "跟踪指数近 5 日回落幅度大于同类" | 同类等权均值同期回落幅度反超本标的 | 下一期 `peers` 快照 |
| ... | ... | ... |

**硬约束**：
- 至少 3 个关键假设，每个必须有可观测的证伪条件
- 不可证伪的假设须标注「不可验证，置信度降级」
- 观测窗口必须是具体时间或事件节点，不能是「待观察」

### 4. 「致命一击」+ 盲点检查

**致命一击**：用一句话回答——**如果这个分析错了，最可能是因为什么？**

> **1 个月持有的最大风险**：[X 条件]。若 [Y 可观测触发]，当前分析框架的 [Z 方向性判断] 失效。

**盲点检查**（≥2 条）：
1. 有什么重要变量完全没有被讨论？
2. 当前共识最可能忽略什么风险？
3. 如果一个月后回头看，今天最明显的盲点会是什么？

格式：
```
🔍 盲点发现:
- [盲点 1] — 当前: [未知/数据不可得/未覆盖]
- [盲点 2] — 当前: [未知/数据不可得/未覆盖]
```

---

## Self-Check

> **共享清单**：[report-conventions.md §7](../../../skills/lib/references/report-conventions.md) Self-Check（通用 + etf 专项）。

发出备忘录前：

- [ ] 无「建议买入/卖出/持有/加仓/减仓/止损」
- [ ] 无无假设的「目标价 XX」
- [ ] 每个关键数字有来源
- [ ] 用指数 PE / 行业 PE，非个股 PE 叙事
- [ ] 首尾有风险声明
- [ ] [事实]/[分析] 块带 SOP-EV 证据标签（共享规范 §5）
- [ ] 措辞无违规（共享规范 §3）
- [ ] [分析] 事实性前提带来源或「框架性陈述/待验证」标注，证据标签未覆盖无来源前提（共享规范 §2.3 强制行为 7）
- [ ] 派生数字（倍数/比例/百分点/点位差）带 `[来源: Python calc: formula]`，无「复算一致/自洽校验」类未实跑字样
- [ ] 正文 §N 交叉引用指向节含被引内容
- [ ] 行业 ETF：§3.1 估值框架 / §3.2 行业位置已展开（`valuation_guide` 非一行标签；同赛道位置按两档，非 TMT 未套用 TMT 口径、未自行编组）
- [ ] 份额趋势已取（`report --json` 的 `share_history`），无数据标注「积累中」
- [ ] §5 历史演变：采集已传 `--history`（事件-价格对照另需 `--events PATH`，且须拉到历史行情）；引擎 `aligned` 为空（未传 `--history`/`--playbook`，或历史行情源不可用）时该列写「未对齐（需 `report --history` 且历史行情可用）」，未由 AI 手工补数
- [ ] **「条件采集表」内条目已按各自适用条件执行**，解读纪律见「分析合成」§0 / §0b / §0c
- [ ] 集中度与聚类数字引用引擎字段（AI 未心算）；未映射归类标注「AI 归类」；「名义主题 vs 实际暴露」偏差已解读
- [ ] 盘面结合已对照 pulse `zt_industry_flow`（差异名已注明）；口径声明（大单 / 日间噪声 / 证据非信号）已写入
- [ ] 对抗性假设检验：≥3 个关键假设有可证伪条件，核心假设被检验
- [ ] 致命一击：一句话条件式风险归纳，指向可观测失效条件
- [ ] 盲点检查：≥2 条盲点发现
- [ ] 关键矛盾已识别（如 CAPEX 总量降 vs 算力增），不是数据点的罗列
- [ ] 文件名包含实际北京时间（非硬编码）
- [ ] 极值断言（峰值/最大/最低）基于全量序列 Python 聚合，非打印子集（R1）
- [ ] 无「Python calc 视角/复算一致/自洽校验」类未实跑标注——来源标注仅两种：引擎字段 / `[来源: Python calc: formula]`（共享规范 §2.3 强制行为 5-7）
- [ ] 检索/新闻口径数字带「检索摘要口径，出处待核实」标注，未归因到未读原文的媒体（R2）
- [ ] 计数经 Python（`len()`），无目视计数（R3）
- [ ] **机器层准出（写入后必跑，非可选）**：`uv run python skills/lib/report_qc.py <报告文件> --fail-on error` → 无 error 级发现方可交付；sourcing warning（F2 派生词缺来源 / F4 §N 引用不存在）须人工复核后消除或说明
- [ ] **复盘原料 sidecar 已落盘**：`decision SYMBOL --init` → 填写 `scenarios`/`falsifiers` → `--from`（退出 0）；**无假设的报告也要落盘**（最小 schema 五键），否则「有/没有 sidecar」不可机器区分
- [ ] **报告复检流程已执行**（CLAUDE.md「报告复检流程」三层：数字对照→合规核对→逻辑自洽），并向用户汇报复检结果

---

## 与其他 Skill 的关系

| Skill | 关系 |
|-------|------|
| **invest-a-journal** | 方案四维评估；ETF 数据经 shim 调用本模块 |
| **invest-a-stock** | 个股深研；本 Skill 不替代。主题逻辑可引用龙头个股报告 |
| **invest-a-gap-scan** | 市场扫描；无关 |
| **invest-a-pulse** | 涨停行业热度（zt_industry_flow）供行业资金流盘面结合对照（三源之一：同花顺净额 + ETF 份额 + 涨停热度）；报告层引用，引擎不跨 skill 耦合 |

---

## 参考

- [references/report-template.md](references/report-template.md)
- [references/etf-hedge-map.md](references/etf-hedge-map.md)
