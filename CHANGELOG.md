# Changelog

## Unreleased

## v0.1.9 (2026-07-07)

v0.1.9 将 SKILL 拆分为「核心 + 专项 references」架构，扩展 `plan --intent`，并新增参与者行为扫描 render 层。

### Skill 架构

- **SKILL.md 瘦身**（~173 行）：LAWs + 路由表 + CLI；九模块/F 规范/引用格式迁至 `references/`
- **新增专项文档**：`modules.md`、`references-format.md`、`financials.md`、`sentiment.md`、`game-theory.md`
- **`plan --intent`** 新增：`sentiment_deep`、`financials_deep`、`game_theory`

### 参与者行为扫描（P0）

- 新模块 `lib/participant_scan.py` + render 节「参与者行为扫描」（full 模式，位于市场结构之后）
- 基于 northbound / moneyflow / margin / holder_changes / turnover / PCR 现有数据
- 元分析层，非策略建议；龙虎榜/大宗留 v0.2.0

### 舆情专项（P1）

- `sentiment.md`：L1 卖方研报 / L2 公告 / L3 WebSearch 三层 SOP
- `SentimentCard` 文档澄清：卖方一致预期，非社媒舆情
- `source-guide.md` 新增舆情数据源章节

### 财报专项（P2）

- `financials.md`：F-1~F-4 + F-3 决策树 + F-4 六关深化指引
- `risk_scanner` 新增软信号：`revenue_acceleration_flag`、`ocf_np_divergence_flag`

### 测试

- `test_participant_scan.py`：行为扫描 + full/brief 集成
- `test_v015_fixes`：3 个新 intent preset
- `test_financials`：财务软信号

## v0.1.8 (2026-07-07)

v0.1.8 交付 DCF 三情景估值模型、量化评分引擎、分析框架模板和 AI 分析置信度矩阵。

### 策略调整

- **LAW 6 放宽**：移除"禁止目标价"限制。允许多情景估值参考价（乐观/中性/悲观），须标注各情景的假设前提与概率权重，且注明"仅供参考，不构成投资建议"。不标注假设前提的单一目标价数字仍然禁止。涉及 CLAUDE.md 和 SKILL.md。

### DCF 估值模型（V-1~V-6，`valuation.py`）

- **`dcf_two_stage`**：两阶段 FCFF 折现模型（显式预测期 + 永续增长终值），`math.isfinite()` NaN/inf 输入校验
- **`dcf_sensitivity`**：WACC × 终值增长率 5×5 敏感性矩阵
- **`scenario_fcff`**：Bear/Base/Bull 三情景 FCFF 预测（营收增速/利润率/capex 强度）
- **`triangle_check`**：自研 DCF 隐含增速 vs 机构一致预期 vs 历史 CAGR 三角对照表
- **`_section_dcf_valuation` (D-4/D-5/D-6)**：三情景估值区间 + 三角对照 + 敏感性矩阵渲染

### 量化评分引擎（S-1，`scoring.py` 新建）

- **`revenue_quality_score`**：收入模式质量评分（Zha Giedt 2018 三组件应计模型 + 毛利率稳定性 + OCF 覆盖）
- **`customer_lockin_score`**：客户锁定评分（Shy 2002 转换成本 + CFA 护城河框架）
- **`management_ability_proxy`**：管理层能力代理评分（Demerjian et al. 2012 DEA+Tobit）
- **`insider_signal`**：内部人买卖一致性信号聚合（≥3 主体同向 → 强信号）
- **`confidence_matrix`**：AI 分析置信度矩阵（8 模块 × 数据覆盖率/来源丰富度/时效性/交叉验证）

### 分析深度增强（A-1~A-6，`render.py`）

- **A-1 内部人增强**：言行对照 + 红旗标注 + `insider_signal()` 聚合
- **A-2 AI 置信度矩阵**：引擎自动计算，非 LLM 判断；估值判断/周期拐点固定中/低
- **A-3 待验证问题清单**：行业/估值/事件特征 → 定制化问题模板
- **A-4 商业模式画布**：7 维度评分（5/7 可量化，2/7 数据不足标注）
- **A-5 管理层完整评估**：决策时间线 + 资本配置能力 5 维度 + 股东利益一致性
- **A-6 价值链位置**：ASCII 价值链图 + 利润池分布

### 框架模板（F-1~F-6，`render.py`）

- **F-1 理解陈述**：5 句模板（生意/护城河/管理层/估值/不确定性）
- **F-2 Bull/Bear 增强**：空方论点 ≥ 多方-1、每条款附带数字链条、禁止收敛为共识；估值/行业竞争自动补齐模板
- **F-3 快速否决**：`_check_fast_veto` 硬/软触发分层（FCFF/负债率/商誉 → 硬触发跳 DCF；OCF/ROE → 软触发预警）
- **F-4 六关评分速览**：生意/护城河/管理层/财务/估值/风险，无二元判决，无仓位映射
- **F-5 偏误自查表**：叙事/锚定/幸存者/近因/确认偏误槽位
- **F-6 交叉验证记录表**：各维度 cross_validation 已有结果占位

### 工具链

- **版本号收敛**：新增 `scripts/version_sync.py`；`bump-version.sh` / `check-version.sh` 以 `pyproject.toml` 为 canonical，一键同步 5 个分发 manifest
- **移除运行时版本自检**：删除 SKILL.md Step 0 与 SessionStart 钩子中的 `check-version.sh`（保留 CI / pre-commit 校验）

### Code Review 修复（第三轮 + v0.1.8）

- NaN 守卫：`dcf_two_stage` 新增 `math.isfinite()` 输入校验，防止静默 NaN 传播
- 措辞规范："分位" → "历史位置"（F-1/F-4/A-3，模块 1 外）
- CAGR 复用：`_section_dcf_valuation` 优先使用 `scenario_fcff` 内置 CAGR，避免双算法分歧
- `FORBIDDEN_TARGET_PRICE_RE` 正则覆盖冒号变体
- `_norm_date` 移除不可达第三正则
- `_has_price_signal` 非 dict 安全；`calc_beta` epsilon 零方差；`cmd_bump` 失败回滚
- CI: `test_extract_release_notes.py` 版本号动态读取 `pyproject.toml`

### 文档

- `host-docs/v0.1.8/`：scope.md、dcf-valuation-design.md、implementation-plan.md、补充资料.md
- `host-docs/开发文档评审流程.md`：六维评审方法论（待沉淀为 skill）
- `SKILL.md`：新增 F-1~F-4 SOP 规范 + A-4/A-5 置信度标注要求
- `CLAUDE.md`：LAW 6 放宽措辞同步

### 测试

- `test_v018.py`：100 tests（scoring 5 函数 + DCF 4 函数 + render 12 节 + 合规 grep）
- `test_v017_e2e.py`：`INVEST_RUN_E2E=1` 时四标的 collect/report 冒烟

## v0.1.7 (2026-07-04)

v0.1.7 扩展 Tushare 三表 DCF 字段、新增股东增减持与行业定价采集维度，并为 v0.1.8 DCF 模型预埋估值预处理函数。

### 核心新增

- **P0-1 Tushare 三表扩字段** (`collector.py`)：income/cashflow/balancesheet 补齐 EBIT、CapEx、净债务等 DCF 所需字段
- **P0-2 holder_changes 维度**：三源（Tushare + akshare ths/cninfo）股东增减持采集、去重合并与报告渲染
- **P1-1 chain.py 期货映射**：`get_futures_for_industry()` 复用长度降序关键词匹配
- **P1-2 industry_pricing 维度**：期货现货价格 + 公司新闻涨价信号，挂载至 `attach_phase2_extras`
- **P2 估值预处理** (`valuation.py`)：`calc_wacc` / `calc_fcff` / `calc_net_debt` / `calc_ev_to_equity` / `calc_beta`
- **P3 WebSearch 白名单** (`env.py` `PRICE_NEWS_WHITELIST`) + `ReportEnhancer` 触发器统一
- **P3-3 价格异常检测原型** (`_detect_price_shock`)：近 60 日涨跌停检测，接入 `attach_phase2_extras`

### 审查修复

- `_merge_holder_records`：修正 source_rank key、同源同日多笔不合并、cross_check 仅计 distinct 源
- `calc_wacc`：debt_weight 缺失时不再输出假 50/50 权重，退化为 cost_of_equity 并附 warning
- 渲染：NaN avg_price 显示为 `—`；章节编号改为 3d/3e 避免与市场结构 3b/3c 冲突
- 期货趋势：双日期 spot 对比实现 `trend_30d`（替代恒为"数据不足"的占位）
- `ReportEnhancer` 输出可操作建议至报告头部（涨价 WebSearch / 估值分位 / 价格 shock）
- akshare ths `change_vol` 文本解析为数值
- 移除未使用依赖 `pypdf` / `pycryptodome`（PDF 能力留待 v0.1.9 report_audit）

### 审查修复（第二轮）

- `_is_valuation_extreme` 改为从 `dimensions` 读取估值分位（修复触发器死代码）
- 涨价新闻增加近 30 日日期过滤
- `industry_pricing` 渲染拆分：期货→模块 1、涨价信号→模块 2
- `brief` 模式补充股东增减持章节
- SKILL.md 补充 WebSearch 白名单指引

## v0.1.6 (2026-07-02)

v0.1.6 引入事件驱动引擎、Peer 对标 CLI、合规 Lint 引擎、TickFlow K-line 数据源及 Manifest 指纹系统。

### 核心新增（P1）

- **事件驱动引擎** (`lib/events.py`)：事件总线架构，支持事件发布/订阅、条件触发、优先级排序
- **Peer CLI**：对标/同行比较命令行工具，支持多标的对比
- **TickFlow K-Line 数据源**：免注册的独立数据管道
- **合规 Lint 引擎** (`lib/lint.py`)：基于 `compliance_rules.yaml` 的报告自动审查
- **Manifest 指纹模块** (`lib/manifest.py`)：报告元数据生成与指纹校验
- **分析模板库**：结构化分析模板 + 事件分类体系 (`event_type_taxonomy.yaml`)

### 集成与优化

- 事件引擎双路径集成到 collector + invest + store 流程
- Render 引擎 v3 挂载点新增事件与模板支持
- EPS 预测范围回退至 `target_price_range`（Template C）
- 代码审查反馈修复

### 测试覆盖

- 新增 8 个测试文件，覆盖事件引擎、模板、Lint、Manifest、Peer CLI 等核心模块
- 总计新增 ~5,200 行变更，30 文件

### 版本同步

- `SKILL.md` / `CLAUDE.md` / `pyproject.toml` 统一更新至 v0.1.6

## v0.1.4 (2026-06-17)

v0.1.4 将模块 4/5/7 从占位升级为 70 分可用模板，并加固 SKILL 架构与发布门禁。

### 报告模板（P0）

- **模块 4**（`_section_fundamentals_layered`）：核心判断摘要（盈利/现金流/负债）、业绩全景表（含 EPS）、12 题回答状态表
- **模块 5**（`_section_bull_bear`）：假设→传导→数字链条；5c 关键分歧点按 PE 历史区间位置分支；PE 中位数场景化取自 valuation 维度
- **模块 7**（`_section_risk_uncertainty`）：三层分组风险表 + Known Unknowns 标准槽位
- **核心矛盾小结**（`_section_core_tension`）：模块 4–5 之间可选段落
- **模块 6**：右侧趋势延续信号组合（P1d）

### 架构与工具（P0-7/8/9）

- **SKILL.md**：OUTPUT CONTRACT 前置；九模块表与 LAW 14/15 更新；P2c 措辞自查项
- **CLAUDE.md**：措辞规范、分位规则、[事实]/[分析] 标记规范
- **check-version.sh** / **check_report.sh**；CI 版本检查；`.pre-commit-config.yaml`
- **版本四件套**同步至 0.1.4（SKILL / CLAUDE / pyproject / plugin）

### 数据层

- **`research` 维度**：`collect_research()` 按积分顺序降级；LAW 6 合规表述；`schema.RESEARCH_SUMMARY_KEYS`
- collector：卖方价位区间反转修正；业绩预告无同比区间时的文案兜底

### Fixed / Docs

- **`research` 维度（机构研报）**：`collect_research()` 按 Tushare 积分顺序降级（`report_rc` → `forecast` → akshare）；高阶成功跳过低阶 API；报告以「卖方预期价位」等 LAW 6 合规表述展示第三方一致预期；需 `--dims=...,research` 显式启用
- **Tushare `sw_daily` 积分门槛更正为 5000**（[官方文档](https://tushare.pro/document/2?doc_id=327)）；2000 分档自动回退 akshare `index_hist_sw`，`availability` 标注回退原因
- **CONFIGURATION.md** 补充 Tushare 积分与功能对照表
- **`opt_daily`** 文档与提示同步为 5000 积分
- **GitHub Release** 发布说明自动从 `CHANGELOG.md` 提取；合并到 `main` 时同步 Draft Release

## v0.1.3 (2026-06-15)

v0.1.3 将投研报告从「数据摘要」升级为「九模块动态研究备忘录」，分四阶段交付。

### Phase 1 — 动态投研内核

- **九模块 Markdown 报告** (`render_report_v3()`)：研究问题卡 → 状态快照 → 动态驱动 → 市场结构 → 静态基本面 → 市场分歧 → 左/右概率 → 风险 → 附录
- **市场结构采集** (`collect_market_structure()`)：申万行业、北向、融资融券、主力资金、换手率、ERP；权限不足时标注 `[数据源不可用，该因子跳过]`
- **LAW 10–16** 方法论规范（见 `skills/invest-A/SKILL.md`）
- **数据结构** (`schema.py`)：`DriverFactor`、`CrossValidation`、`ProbabilityStructure` dataclass

### Phase 2 — 基本面与估值

- **12 道核心必答题** (`_section_fundamentals_layered()`)：行业位置 / 商业质量 / 财务质量 / 估值与预期；数据不足标注 `数据不足：[缺少什么]`（LAW 14）
- **隐性预期差** (`implied_growth()`)：戈登反推 `g_implied ≈ r - 1/PE`（LAW 15）
- **PE Band 序列** (`pe_band_series()`)：5 年 PE 分位带数据层
- **同行对比** (`collect_industry_peers()`)：行业 PE/PB 分位排名
- **交叉验证 CV-2**：营收增长 vs 应收账款增长

### Phase 3 — 风险与分歧闭环

- **风险扫描器** (`risk_scanner.py`)：17 个定量触发信号（报表 7 / 商业 4 / 市场 6），Known Unknowns 列表
- **多空分歧** (`_section_bull_bear()`)：多头/空头逻辑链、关键分歧点、预期差
- **情绪增强**：50ETF 认沽认购比、融券余额增速、创新高占比分位
- **左/右概率结构**：ERP + 情绪指标交叉验证（CV-8）；LAW 16 禁止确定性「左侧/右侧」结论
- 九模块报告无占位节，功能完整

### Phase 4 — 跨时点与阅读体验

- **快照 diff 增强** (`store.py` + `invest.py diff`)：对比估值/财务/资金/技术/风险关键字段变化，支持 `--emit md`
- **`watchlist` 命令**：多标的批量摘要，单只失败不阻断其余
- **报告 UX**：顶部 TOC 锚点目录、`<details>` 长节折叠、Mermaid 研究框架图、PE Band 文本表

### ⚠️ Breaking Changes

- **默认输出格式从 `html` 改为 `md`**：`report` 命令默认生成九模块 Markdown（stdout 或 `--outdir`）；HTML 须显式 `--emit html`（v0.1.2 模板，迭代期冻结）

### 合规

- LAW 16：左/右章节仅呈现概率结构，禁止「当前是左侧/右侧」确定性结论
- 免责声明语气：「研究备忘录」替代「学习研究」

## v0.1.3-alpha (2026-06-14)

> 预发布里程碑（Phase 1 only），内容已并入上方 v0.1.3。

## v0.1.2 (2026-06-12)

### 基础分析骨架 — 从"数据摘要"升级为"基础研究报告"

- **技术分析模块** (`lib/technical.py`)：MA/SMA、MACD、RSI(6/12/24)、KDJ、BOLL、ATR、量比、N日极值、回撤 — 纯计算无副作用
- **估值分析模块** (`lib/valuation.py`)：PE/PB/PS 历史分位计算、估值区间标签（30/70分位法）、次新股标注
- **估值采集** (`collect_valuation()`)：Tushare `daily_basic` 5年历史序列 + 腾讯快照降级（无 Token 时标注"历史分位不可得"）
- **快照 Diff** (`invest.py diff`)：对比同股票两次采集变化，支持 `--from/--to` 指定快照或自动取最近两次
- **报告模板 v2** (`render_report_v2()`)：八段结构（公司画像→经营质量→估值位置→资金与筹码→技术结构→事件催化→核心矛盾）
- **K 线窗口扩大**：默认 400 自然日（覆盖 MA250），`--deep` 扩大到 730 自然日
- **HTML 研究报告** (`render_html()`)：单文件自包含 HTML（内嵌 Chart.js + CSS）、暗/亮主题、侧边栏导航、交互图表；自动保存为 `YYYY-MM-DD-hh-mm-ss-股票代码-股票名称.html`
- **代理检测与 Clash 规则提示** (`lib/proxy.py`)：检测本机 HTTP/系统代理并提示 DIRECT 规则，不强制绕过；`diagnose` 输出 `proxy_detected` / `clash_rules_hint`
- **北向资金单位归一化**：Tushare `moneyflow.net_mf_vol`（万元）统一转换为元，与 akshare 对齐
- **HTML 修复**：首部风险声明、扣非净利润柱图按序列均值着色；股东结构仅展示最新报告期列表（不含历史对比图）
- **HTML JS 语法修复**：Chart.js 内联脚本从 f-string 拆出，修复 `{{` 导致的 `Unexpected token '{'` 浏览器报错

### ⚠️ Breaking Changes

- **默认输出格式从 `compact` 改为 `html`**：`invest.py report <symbol>` 默认生成 HTML 文件并保存到当前目录，stdout 输出紧凑摘要 + 文件路径
- **移除自动代理绕过**：v0.1.1 的 `proxy_bypass()` 会在采集时临时清除 `HTTP_PROXY` 等环境变量；v0.1.2 起改为检测本机代理并提示 Clash **DIRECT** 规则，不再自动绕过。akshare（东方财富）、baostock 需用户在代理规则中将 `eastmoney.com` / `baostock.com` 设为 DIRECT，否则在 VPN/全局代理下可能采集失败。例外：腾讯行情采集与 `diagnose` 探针仍强制直连（`no_proxy_session`）；Tushare 在客户端初始化时捕获代理配置，与 akshare 并行互不干扰
- 默认采集维度从 5 个增加到 7 个（新增 `valuation` + `kline`），Tushare 配额消耗略增
- `--emit=md` / `--emit=compact` 输出格式从旧七维度改为新八段模板

### 合规

- 技术指标输出使用"DIF 上穿 DEA"等描述性语言，不含"金叉/死叉/买入/卖出"等交易信号词汇
- 所有维度末尾附"🔍 待独立验证项"

## v0.1.1 (2026-06-11)
### Changes
- feat: update version to 0.1.1, add baostock dependency, and enhance data collection strategy

## v0.1.0 (2026-06-10)

### 初始版本

- **单入口 CLI** (`invest.py`)：collect / report / compare / diagnose / store 五子命令
- **集中配置** (`lib/env.py`)：多层 .env 加载，Tushare/FRED/Tencent 可用性检测
- **数据采集** (`lib/collector.py`)：基本信息、财务指标、实时行情、十大股东、北向资金、日K线
- **Tushare Pro** 为主力数据源（HTTP 直连，不依赖官方 SDK）
- **FRED 宏观数据**（US 10Y/2Y/VIX/CPI/美元指数）
- **腾讯行情**实时行情兜底
- **SQLite 持久化** (`lib/store.py`)：采集记录存储，WAL 模式并发
- **报告渲染** (`lib/render.py`)：compact / json / md 格式输出
- **7 条 pytest 测试**（env + store）
- **薄 SKILL.md**（~115 行），遵循 last30days-skill 架构模式

### 数据源状态

- ✅ Tushare Pro（2000 积分，三大报表 + 低频行情 + 宏观经济）
- ✅ FRED（免费注册）
- ✅ 腾讯行情（免费，实时兜底）
- ❌ EastMoney（akshare/efinance 底层，当前 API 502 不可用）
