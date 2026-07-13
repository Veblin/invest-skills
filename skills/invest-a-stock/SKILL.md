---
name: invest:a-stock
version: "0.2.0"
description: "A股多因子交叉验证的结构化投研助手 — 数据采集 + 学术级引用，产出带来源追溯的 Markdown 研究备忘录。研究工具，非决策工具。"
argument-hint: "/invest:a-stock 600176 | /invest:a-stock 600176 --deep | /invest:a-stock 600176 --intent game_theory"
allowed-tools: Bash, Read, Write, WebSearch, WebFetch
user-invocable: true
metadata:
  requires:
    bins: [uv, python3]
  optionalEnv:
    - TUSHARE_TOKEN
    - FRED_API_KEY
    - TAVILY_API_KEY
---

# invest:a-stock 投研助手

## OUTPUT CONTRACT（LAWs）

以下法则约束所有输出。违反即为 Bug。

### LAW 1–9：核心输出规则

**LAW 1** — 每条分析论述必须引用数据来源。

**LAW 2** — 报告使用统一研究流程结构：公司画像 → 经营质量 → 估值位置 → 资金与筹码 → 技术结构 → 事件催化 → 核心矛盾；每节末尾附待验证项。

**LAW 3** — 区分"事实陈述"与"分析判断"。

**LAW 4** — 风险提示出现首部和尾部。

**LAW 5** — **并行取证，汇总为证。** 各渠道独立记录；全失败须标注 **"未获取到任何有效数据，无法判断"**。

**LAW 6** — 禁止买卖建议、仓位建议。允许多情景估值参考价（须假设前提+概率权重+免责声明）。禁止无假设的单一目标价。

**LAW 7** — 每个数字标注追溯路径；References 表须含 API/query 参数。

**LAW 8** — 每个维度末尾要求"🔍 待独立验证项"。

**LAW 9** — 无数据源支撑的分析不输出。

### LAW 10–16：方法论

**LAW 10** — 分析提示须含：定价传导路径、本次数据常见误区、1–3 个交叉验证动作。

**LAW 11** — 问题卡来自四类结构化触发，不得凭空总结：
- **A 变化驱动**：近 20 日涨跌超 ±10%、核心指标环比 ±5pp、重大公告
- **B 估值位置**：PE/PB 历史分位 ≥80% 或 ≤20%
- **C 行业结构**：申万板块相对大盘超 ±5%、行业政策
- **D 趋势结构**：52 周高低区间极端、MA60 附近盘整

格式：核心问题 → 子问题 ①②③ →「为什么这是好问题」。详情见 [references/modules.md](references/modules.md)。

**LAW 12** — 结论附带证据强度：✅ 强 / ⚠️ 中 / ❓ 弱。

**LAW 13** — 动态驱动：最多 5 条候选解释 + 声明主导因子。

**LAW 14** — 静态基本面 12 题 + 分层激活（见 [references/modules.md](references/modules.md)）。

**LAW 15** — Bull/Bear 须含数值场景化链条。

**LAW 16** — 左/右概率并列呈现，禁止单一「左侧/右侧」结论。

### 常见违规模式

| # | 违规 | 规则 | 正确写法 |
|---|------|------|---------|
| 1 | "当前处于左侧/右侧" | LAW 16 | "左侧特征更强：…；右侧支撑：…" |
| 2 | "建议买入/卖出/持有" | LAW 6 | 永远不给买卖建议 |
| 3 | 无假设的"目标价 XX 元" | LAW 6 | 多情景估值参考价+假设 |
| 4 | 无来源的"往往/通常" | LAW 3 | 标注待补或案例 |
| 5 | 亏损期 PE 分位当贵贱 | P0-2 | 标注仅作位置参考 |
| 6 | "极度高估/低估" | 措辞 | 用数值比较 |

### 措辞规范

禁止：买入/卖出/持有/建仓/加仓/减仓/止损/止盈。报告路径：`reports/{symbol}-{name}/{date}.md`，Claude 内只输出简报。

### 数据源策略

v0.3+ **多源并行**：全部可用源同时采集，差异保留于 `_meta.all_sources`，由分析阶段标注。

---

## Skill 身份声明

**研究工具，非决策工具。** 多因子交叉验证、归因讨论、概率结构；不做买卖/仓位建议。

### 并行取证哲学

全部可用源并行查询 → 各渠道独立记录 → 汇总为证。全失败 → LAW 5 标注；多源有数据 → 标注以何为主。

## 输出格式（层叠输出）

**第一层（Claude 简报）**：归因先行、核心矛盾、关键速览、主导因子+观察节点。禁止展开完整九模块。

**第二层（.md 文件）**：完整备忘录 + References（见 [references/references-format.md](references/references-format.md)）。

### SOP-QC 自检

措辞（LAW 6/16/3）、结构（简报 3–5 段、风险提示首尾、LAW 7）、证据（SOP-EV、分位伴中位数、Bull/Bear 数值化）。

### SOP-EV 证据强度

可靠性 ✅/⚠️/❓ | 丰富度 🌐/📡/🔮 | 时效 🕐/📅/🗄️ | 交叉验证 ✓✓/✓✗/—

---

## 专项加载与 intent 路由

执行前用 `Read` 加载对应专项（完整 `report --deep` 加载全部）：

| 用户意图 / CLI | 读取专项 | plan --intent |
|----------------|----------|---------------|
| 默认完整分析 | [modules.md](references/modules.md) | `deep_analysis` |
| 舆情深挖 | [sentiment.md](references/sentiment.md) | `sentiment_deep` |
| 财报深研 | [financials.md](references/financials.md) | `financials_deep` |
| 资金行为扫描 | [game-theory.md](references/game-theory.md) | `game_theory` |
| 完整 report --deep | 全部专项 + modules.md | `deep_analysis` + `--deep` |

**规则**：专项单独运行仍须 `evidence`；完整分析用 `report --mode full`（`--mode` 仅 `brief`/`full`，不用 `--mode=sentiment`）。

九模块结构详见 [references/modules.md](references/modules.md)。财报 F 规范详见 [references/financials.md](references/financials.md)。

---

## 数据来源

详见 [references/source-guide.md](references/source-guide.md)。

## CLI 命令

```bash
# 生成采集计划（intent: deep_analysis | quick_check | catalyst_monitor | compare
#   | sentiment_deep | financials_deep | game_theory）
uv run python skills/invest-a-stock/scripts/invest.py plan 600176 --intent game_theory

# 按计划采集 + 证据表 + 报告
uv run python skills/invest-a-stock/scripts/invest.py collect 600176 --plan /tmp/plan.json
uv run python skills/invest-a-stock/scripts/invest.py evidence 600176 --plan /tmp/plan.json
uv run python skills/invest-a-stock/scripts/invest.py report 600176 --plan /tmp/plan.json --mode full

# 常用
uv run python skills/invest-a-stock/scripts/invest.py collect 600176
uv run python skills/invest-a-stock/scripts/invest.py report 600176
uv run python skills/invest-a-stock/scripts/invest.py report 600176 --outdir=./reports/
uv run python skills/invest-a-stock/scripts/invest.py report 600176 --deep
uv run python skills/invest-a-stock/scripts/invest.py compare 600176 000858
uv run python skills/invest-a-stock/scripts/invest.py diagnose
uv run python skills/invest-a-stock/scripts/invest.py diff 600176
uv run python skills/invest-a-stock/scripts/invest.py store list
uv run python skills/invest-a-stock/scripts/invest.py collect 600176 --store
```

> 运行目录：`code/`。必须用 `uv run python`。`--mode`：`brief` | `full`。

## 代理 / VPN

akshare/baostock 自动绕过 HTTP 代理；`diagnose` 可查 `proxy_detected`。规则与 TUN 说明见 [source-guide.md](references/source-guide.md)。

## 引用格式（精简）

事实：`"数值" [来源: {路径} / {日期}]` | 分析：`[依据: …；逻辑链: …]` | 推测：`[推测，待验证]`

完整规范：[references/references-format.md](references/references-format.md)

## 技术指标

MA/MACD 仅描述市场状态，不生成交易信号。

## 采集顺序

### SOP-M1 宏观情景（`--with-macro`）

简报首行：`[宏观情景] 增长（PMI）+ 通胀（CPI）+ 政策（LPR）→ 偏宽松/中性/偏紧`

1. `diagnose` → 2. `plan`/`collect` → 3. `evidence`（专项推荐）→ 4. `report` → 5. `store`（可选）

---

## v0.1.9 CLI 扩展

```bash
# 质量门
uv run python skills/invest-a-stock/scripts/invest.py rigor 600176 --verify-all [--strict]
uv run python skills/invest-a-stock/scripts/invest.py audit report.md --extract
uv run python skills/invest-a-stock/scripts/invest.py audit report.md --verdict

# 质地检查 / 组合 / 假设追踪
uv run python skills/invest-a-stock/scripts/invest.py check 600176
uv run python skills/invest-a-stock/scripts/invest.py portfolio holdings.json [--stress]
uv run python skills/invest-a-stock/scripts/invest.py thesis 600176 --init|--update|--status

# 价格冲击插值（非风险中性概率）
uv run python skills/invest-a-stock/scripts/invest.py shock 300274 \
  --pre-price 163.46 --post-price 140 --eps-base 6.55 --eps-hit 1.64 \
  --pe-normal 27 --pe-stressed 20

# 新闻包（公告 + 查询包 + 可选 Tavily）
uv run python skills/invest-a-stock/scripts/invest.py collect 600176 --with-news-pack
```

`TAVILY_API_KEY` 可选；无 Key 时 Layer3 静默跳过，Layer1+2 仍产出。

### SOP-DEEP（四视角并行）

完整 `--deep` 报告时，并行覆盖四视角（不引入品牌名）：

1. **生意质量**：商业模式 / 护城河 / 管理层 / 价值链
2. **财务与估值**：DCF 三情景 / 财务健康 / 盈利质量 / 估值位置
3. **行业与竞争**：波特五力 / 竞争格局 / 产业链利润池
4. **风险与治理**：快速否决 / 风险信号 / 公司治理 / Known Unknowns

### SOP earnings-review（季报/年报后）

- [ ] 对比指引 vs 实际（营收/净利/毛利率）
- [ ] OCF/净利润是否背离（阈值 0.6）
- [ ] 资本开支与产能叙事是否一致
- [ ] 更新 thesis `--update` 假设状态

### SOP industry-research / news-pulse

- [ ] `collect --with-news-pack` 获取公告 + 查询包
- [ ] 对 `query_pack` 执行 WebSearch/Tavily，回填 NewsCard
- [ ] 外生冲击假说⑥段：方向 + 可信度 + 来源
- [ ] 重大波动时用 `shock` CLI 计算价格冲击插值比例（附学术声明）
