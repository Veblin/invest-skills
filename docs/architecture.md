# invest-skills 功能与实现逻辑总览

> 版本 v0.2.7 · 更新日期 2026-08-23
> 本文档总结仓库已完成的主要功能及实现逻辑，面向使用者与贡献者。运行时规格以各 SKILL.md / references 为准。

---

## 1. 项目定位

**A 股投研助手**：数据采集 + Python 引擎计算 + Claude 编排，产出带来源追溯的 Markdown 研究备忘录。**研究工具，非决策工具**（不提供买卖/仓位建议）。

| 维度 | 说明 |
|------|------|
| 产品形态 | Claude Code 插件（skills 集合）+ Python 数据引擎 + SQLite 持久化 |
| 核心价值 | 数小时手工研究压缩到几分钟；**所有数字经 Python 引擎计算，Claude 只引用不加工（P0 铁律）** |
| 报告标准 | 学术级引用（References 表 + 追溯路径）、证据四维标注、多情景估值参考价 |
| 多 harness | Claude Code（✅ 主开发）、WorkBuddy（✅ 零终端分发真机验证）、Hermes / Gemini CLI（📦 打包就绪待验证） |

## 2. 总体架构

三层结构：

```
┌─ Skill 层（编排）───────────────────────────────────────────┐
│  6 个 skills，每个 = SKILL.md（LAWs 规则 + SOP + CLI 路由表） │
│  触发词路由 → 对话简报 / Markdown 备忘录 / concise 三层输出   │
└──────────────┬──────────────────────────────────────────────┘
               │ 调用 CLI（uv run python .../invest.py）
┌─ 引擎层（invest-a-stock scripts/lib = 基础设施包）───────────┐
│  collector（多源降级采集）→ fusion/rerank → store（SQLite）   │
│  → render_markdown（九模块组装）→ lint（合规扫描）            │
└──────────────┬──────────────────────────────────────────────┘
               │ 跨 skill 经 _invest_path bootstrap 复用
┌─ 共享层（skills/lib）───────────────────────────────────────┐
│  20 个纯函数/缓存模块 + references 规范（report-conventions 等）│
└─────────────────────────────────────────────────────────────┘
```

**主数据链路**（invest-a-stock）：

```
diagnose → collect（跨维度并行扇出 → 维度内多源 cascade/parallel → SourceResult 归一
  → RRF 融合 → rerank 可信度评分 → 宏观/事件/新闻包/manifest 挂载 → SQLite 落库）
→ report（render_report_v3 按 --mode 组装九模块 → lint 合规扫描 → reports/ 写入）
```

## 3. Skill 一览

仓库共 **6 个 skills**（Python 计数 `len()` 验证），另有 `.claude/skills/verify/` 为内部验证工具。`invest-a-limit-up` 已于 v0.2.5 移除（无代码调用方，涨停池逻辑并入 journal/pulse 的 market_microstructure）。

| Skill | 定位 | 入口 | 关键实现 |
|-------|------|------|---------|
| **invest-a-stock** | 个股多因子交叉验证结构化投研（核心） | `scripts/invest.py`（26 子命令） | 多源降级链、九模块渲染、质控工具链，详见 §4 |
| **invest-a-etf** | ETF 结构化研究（指数估值/折溢价/AUM/跟踪质量/对冲覆盖） | `scripts/etf.py`（9 子命令） | ETF 数据层 canonical（`etf_data.py`）；NAV/指数 K 线与 derived 字段；赛道资金流（同花顺独立源 + 四象限窗口分解）；期货基差历史分位；回撤档位 playbook |
| **invest-a-journal** | 交易日志 v2：买入/卖出方案质量四维评估 | `scripts/`（lib 库 + Claude 驱动） | Q&A 引导 + 数据引擎（Tier1-3 市场微观结构 + env_label 环境标签）+ 四维评级（逻辑/盲点/仓位匹配/风险收益）；§4.3 结构化字段（止损/提取/卖出去向/可证伪条件）；卖出自动关联入场记录 |
| **invest-a-gap-scan** | 跳空缺口扫描（向上缺口 + MA60 上方 + 未回补） | `scripts/scan.py` | 指数成分股并集（沪深300+中证A500+科创50，去重约 478 只）；Tushare 批量 → baostock 降级，K 线缓存按源隔离；容忍规则（新缺口回补则看更老缺口）+ 跨停牌检测 |
| **invest-a-pattern-scan** | LMW 双底/三角形底形态扫描 | `scripts/pattern_scan.py` | Lo-Mamaysky-Wang (2000) 核平滑 + 5 极值模板；因果滚动防 look-ahead；White (2000) Reality Check 数据窥探防护（p<0.05 才声称统计增量） |
| **invest-a-pulse** | 市场情绪脉搏（杠杆/广度/情绪/资金/估值五维 + 综合环境标签） | 纯编排（无自有脚本） | 完全复用 journal 的 `market_microstructure` 数据管道（经 data_bridge 缓存避免重复采集）；筹码出清度四信号；涨停行业轮动 + 跷跷板检验 |
| verify（内部） | 运行时验证三个 CLI 真实运行（非 mock） | `.claude/skills/verify/` | `store._db_override` 隔离真实 DB + `runpy.run_path` 执行真实命令行 |

**注册与分发**：`skills.yaml`（antfu/skills-cli 清单，注册 5 个用户 skill）→ `sync_version.py` 同步生成 `.claude-plugin/`、`.agents/plugins/`、`gemini-extension.json` 三处 marketplace 清单。

## 4. invest-a-stock 核心实现逻辑

### 4.1 CLI：26 个子命令（`scripts/invest.py`，约 2400 行 argparse 分发器）

业务逻辑全部在 `scripts/lib/`，invest.py 仅做分发。四组：

| 组 | 子命令 |
|----|--------|
| 研究主线 | `plan`（意图→采集计划）、`collect`（8 维采集，默认自动入库）、`analyze`、`evidence`（结构化证据表，`--from-store` 复用快照防双重采集）、`synthesize`、`report`（三模式输出） |
| 验算与质控 | `value`（科学估值七步多方法）、`rigor`（财务验算）、`audit`（报告审计 extract/verdict）、`check`（质地 7 指标）、`peer`（行业横向对比）、`classify`（R1 收益驱动四分类）、`risk-reward`（DCF 三情景盈亏比）、`ic`（投资委员会决策）、`lint`（合规扫描） |
| 对比与回溯 | `compare`（双标的）、`diff`（两次快照变化 + 数据源 manifest 指纹）、`watchlist`、`store`（list/stats/clear/valuations）、`etf-flow`（ETF 份额趋势） |
| 市场与决策辅助 | `market-status`（市场微观结构 / R5 行业景气卡）、`portfolio`（组合风险）、`thesis`（投资假设追踪）、`shock`（价格冲击插值）、`catalyst`（催化剂日历）、`diagnose`（数据源诊断） |

### 4.2 多源降级链（R12h）

- **L3 行情类**（kline/quote/basic_info/shareholders/northbound）：`_run_sources_cascade` — 首选源单发，失败按序降级；**首选源成功 → 后续源标记「未尝试」**（不计降级统计）
- **L2 财务类**（financials/valuation）：`_run_sources_parallel` — 双源并行先到先用（daemon 线程 + 信号量，避免 ThreadPoolExecutor 退出 join 拖死挂起源）
- **`always_attempt` 集合**（如 quote 的腾讯实时快照）：无论链内成败都独立尝试，其成功不标记链完成（防止跳过 akshare K 线回退）
- 所有源独立记录（SourceResult.error）；维度全失败时 `_no_sources_responded()` 中止；数据源响应但返回空（非交易日）不算失败

**数据维度与源配置**（`lib/collector/_orchestrate.py`）：

| 维度 | 源链 |
|------|------|
| basic_info | tushare.stock_basic → akshare.stock_individual_info_em |
| financials | tushare.fina_indicator ∥ akshare.stock_financial_abstract_ths（并行） |
| kline | tushare（adj_factor 自算前复权）→ baostock → tickflow（可选）→ akshare；默认 400 日 / deep 730 日 |
| valuation | tushare.daily_basic → 腾讯快照降级 |
| shareholders | tushare.top10_floatholders → akshare 十大股东 |
| northbound | tushare.hsgt_top10 → akshare 个股沪深港通 |
| research（默认关） | tushare report_rc(10000 分) → forecast(2000 分) → akshare 三级降级 |
| holder_changes | tushare.holdertrade + akshare/同花顺股东变动并行互补 |

跨源一致性：`schema._auto_cross_validate` 对关键字段自动交叉验证（5% 阈值），差异保留于 `_meta.all_sources` 供分析阶段标注。

### 4.3 新闻三层架构与宏观注入

- **新闻**（`--with-news-pack`）：L1 akshare 公告（恒尝试）→ L2 声明式 `query_pack`（零网络生成 5 条查询，供 Claude WebSearch 执行）→ L3 Tavily REST（无 Key 静默跳过）。每条新闻经关键词规则可信度分级（交易所/证监会 0.95 → 财联社 0.85 → 雪球 0.55 → 传闻 0.25）
- **宏观**（`--with-macro`）：中国 akshare 五指标（PMI/CPI/PPI/LPR/货币供应，序列最新在前取首行）+ FRED（VIX/美债，需 Key）+ Yahoo `^SOX` 费城半导体（免费无 Key）。akshare 不可用不阻塞 VIX/SOX

### 4.4 技术指标与 derived 字段

共享库 `skills/lib/technical.py` 的 `compute()` 输出全指标聚合：trend（MA 5-250 + 排列 + 斜率）、momentum（MACD + 交叉）、overbought_oversold（RSI/KDJ）、volatility（BOLL + ATR 值及占价百分比）、volume（量比/放缩量计数）、structure（20/60/120/250 日极值 + 回撤）、**distances（距 52 周高点/低点/年内低点 %，学术锚定 George & Hwang 2004 / De Bondt & Thaler 1985）**、ichimoku、波动率锥、连板检测（按板块定涨停幅）。ETF 侧 derived（NAV vs MA20/MA60、BOLL 位置 %、日均波动率等）在 invest-a-etf 的 `query_etf_kline` 输出。

### 4.5 SQLite 存储（`lib/store.py`，WAL）

路径 `~/.local/share/investment/research.db`（测试经 `_db_override` 隔离）。表：`collections`（整份采集 JSON + kind 标记，diff 自动配对）、`pipeline_states`（五步流程状态，支持 `--resume` 断点恢复）、`thesis`（假设/红线 + 健康度四状态）、`valuations`（多情景区间 + 分位）、`market_snapshots` / `macro_snapshots`（快照序列）、`index_pe_history`、`etf_share_snapshots`、`industry_weekly`、`market_daily`（全市场分位）、`futures_daily`（股指期货）、`trade_journals`（journal skill 写入）。

### 4.6 报告渲染（`lib/render_markdown/`）

- **九模块结构**（`render_report_v3`，`_concise.py`）：问题卡 → 状态快照 → 动态驱动 → 市场结构 → 参与者行为扫描 → 事件时间线 → 机构观点 → 静态基本面 12 题 → DCF 估值（F-3 硬触发时跳过）→ 核心矛盾 → Bull/Bear → 左/右概率 → 风险 → 六关评分卡 → References
- **三模式**：`brief`（决策摘要）/ `full`（完整备忘录）/ `concise`（Hermes 等对话场景，5 段速览 + `<details>` 展开）
- **ReportEnhancer 条件增强器**（涨价信号确认、PE 分位 ≥80% 估值高位预警等）+ val_cache（5 年 PE/PB/PS 分位只算一次）
- 报告落盘 `reports/{symbol}-{name}/{YYYY-MM-DD-HH-MM-SS}.md`，文件名用北京时

### 4.7 质控与辅助工具实现要点

| 子命令 | 实现逻辑 |
|--------|---------|
| rigor | 市值验算（股价×总股本 vs 报告值）、估值验算（PE/PB/ROE，按 end_date 取最新年报行）、跨源交叉验证（5% 阈值）；`--strict` 阻断 |
| audit | `--extract` 正则抽取 9 类数据点（固定种子采样 15%）生成核对清单 → 人工填值后 `--verdict` 偏差 >5% 判 FAIL |
| check | 7 指标否决/警告制（ROIC/累计 FCF/利息覆盖/毛利率波动/OCF净利比/净利率趋势/股本膨胀）+ 行业豁免（银行/科技硬件） |
| portfolio | 行业集中度 + 120 日相关性矩阵 + `--stress` 指数 -10/-20/-30% 市值估算（纯风险特征描述） |
| thesis | 假设/红线模板 → `--invalidate`/`--trigger-redline` → 健康度 = 有效假设占比×0.6 + 未触发红线占比×0.4 |
| shock | 线性插值比例（非概率）：`(post − eps_base×pe_stressed) / (eps_hit×pe_normal − eps_base×pe_stressed)` 钳位 [0,1] |

### 4.8 lint 合规体系

规则外置在 `scripts/references/compliance_rules.yaml`（**55 条**，Python 计数）：LAW 6 买卖建议/目标价、LAW 16 左右侧断言、禁止词（崩盘/极度高估/「往往」）、估值分位规范（分位必伴中位数、亏损标的标注）、[事实]/[分析] 结构与证据标签缺失、LAW 17 标题规范、点位引用红线（wording-level-* 6 条 error 级）等。三 profile：`claude`（全量）/ `precommit`（钩子阻断）/ `engine`（措辞+文件名）。

## 5. 共享层（skills/lib）

### 5.1 Python 模块（20 个）

| 分组 | 模块 |
|------|------|
| 技术分析 | `technical`（MA/MACD/RSI/KDJ/BOLL/ATR/ADX/连板检测，全指标聚合 `compute`） |
| 形态与统计 | `lmw`（双底/三角底检出）、`backtest`（welch_t/permutation/HAC/二项检验等事件研究纯函数）、`multiple_testing`（White 2000 RC 块 bootstrap + BH-FDR + bootstrap CI） |
| 数据访问 | `data_bridge`（TTL 缓存维度访问层，被 stock/etf/journal/pulse 四方消费）、`cache`（JSON TTL 缓存，空结果不缓存）、`kline_cache`（pickle 缓存按源隔离）、`market_pctile`（全市场分位读路径） |
| 基础设施 | `invest_path`（跨 skill 路径 bootstrap canonical）、`codes`/`dates`/`trade_cal`/`stats`/`nums`/`qfq`/`db_util`/`data_util`、`version`（pyproject 读版本）、`market_pulse`、`report_qc`（统一研报质量检查器） |

关键约定：`nums.coalesce_field` 防 falsy-zero（D1）；`qfq` 双路径语义不同勿互走；invest-a-stock 的 `scripts/lib` 经 `_invest_path` 成为全仓 `import lib.*` 的命中目标。

### 5.2 references 规范体系（canonical 文档）

| 文档 | 内容 |
|------|------|
| `report-conventions.md` | §2 硬约束（LAW 6/6a）；§2.3 事实边界（三态标注、来源标注仅两种合法形式、清单计数必须 `len()`）；§2.4 点位证据 L1-L4 + 禁止断言表；§3 禁止词替换表 + 已知违规模式 11 条；§5 SOP-EV 四维标注；§8 四类参考输出 |
| `development-rules.md` | D1-D13 缺陷模式规则（来自 /code-review 反复出现的缺陷） |
| `capital-mechanisms.md` | 五条资金行为机制的事实边界与引用纪律（22 篇文献二次核验，含「净值破 1 强制减仓」误传纠偏） |
| `chip-clearance.md` | 筹码出清度四信号 canonical 规格 |
| `scenario-plans.md` | 情景预案库（E-001 已激活 + E-002~E-007 候选，闭环降级机制） |
| `backtest_prereg/` | 回测预注册机制（F1-F3 期货基差、H1-H6 事件假设，状态定义/样本/检验冻结于刻画前） |

## 6. 方法论与合规体系

- **LAW 1-17**：L1-L9 输出规则（每条论述有来源、统一结构、禁买卖建议、数字可追溯）；L10-L17 方法论（问题卡四类触发、证据强度分级、Bull/Bear 数值场景化、左右概率并列禁单边结论、结论先行金字塔）
- **P0 计算铁律**：一切数字经 Python 计算，LLM 禁止心算/目视计数；来源标注仅两种：`[来源: 引擎字段名]` 或 `[来源: Python calc: formula]`
- **事实边界**：三态标注（可验证/公开不可独立验证/未知）；数据冲突并列不裁决；「检索不到」≠「不存在」
- **交易结构分析（LAW 6a）**：多情景估值入场区间（标注假设前提+概率权重）+ 假设失效触发（= 启动重评估，非离场）+ 操作纪律；区分标准 =「是否替用户做决定」
- **回测纪律**：预注册 → 回测 → 裁决（t≥3.0 显著）→ 不显著降级为建议。实例：H5 日历效应（8 月中旬谨慎）裁决不显著已降级；H2 大跌低吸假设被拒绝（calendar-time 校正后为负）
- **三层输出**：对话简报（严格顺序模板）→ Markdown 备忘录 → concise 块（跨 harness）

## 7. 版本演进

| 版本 | 日期 | 核心变化 |
|------|------|---------|
| v0.1.0 | 06-10 | 单入口 CLI（5 子命令）、Tushare 直连、SQLite WAL、7 条测试 |
| v0.1.2 | 06-12 | 技术指标库（MA/MACD/RSI/KDJ/BOLL/ATR）、估值历史分位、八段报告、HTML 报告 |
| v0.1.3 | 06-15 | 九模块动态研究备忘录、LAW 10-16、风险扫描器 17 信号 |
| v0.1.6 | 07-02 | 事件驱动引擎、Peer 对标、TickFlow、合规 lint 引擎、manifest 指纹 |
| v0.1.8 | 07-07 | DCF 三情景估值模型、量化评分引擎、LAW 6 放宽（多情景参考价） |
| v0.1.9 | 07-10 | 质控工具链（rigor/audit/check/portfolio/thesis）、新闻三层架构 |
| v0.2.0 | 07-13 | 单 skill → 多 skill 组合、科学估值计算器、多 Agent 并行（~14min→~6min） |
| v0.2.1 | 07-23 | 新增 etf/journal/gap-scan、slash 统一连字符、宏观 VIX/SOX |
| v0.2.2 | 07-28 | 市场微观结构 17 指标 + env_label、新增 pulse、skills/lib 共享层 + TTL 缓存 |
| v0.2.3 | 08-04 | data_bridge 缓存、采集管线优化（同日 K 线 76.8s→0.4s）、巨型模块拆分、P0 AI 计算禁令 |
| v0.2.4 | 08-08 | 方法论引擎 R1-R12h（框架匹配/景气状态卡/多源降级链）、事实边界 §2.3 |
| v0.2.5 | 08-10 | 交易纪律框架 D1-D8 + trade-structure、WorkBuddy 兼容层、移除 limit-up skill |
| v0.2.6 | 08-14~17 | ABCD P0（H5 回测裁决、D 类引擎字段、点位红线 L1-L4）+ M 系列（market_daily 全市场分位 1361 交易日×5544 只、SPA/FDR 框架、pattern-scan、journal §4.3）+ F 系列（futures_daily 股指期货数据层）+ WorkBuddy 零终端分发 zip |

**当前状态**：分支 `feat/v0.2.7`，版本号已 bump 至 v0.2.7（本批 7 项 code-review 缺陷修复随 v0.2.7 发布）。未发布增量：release publish flow 增强（draft 幂等、release notes 提取器升级）、SkillHub 分发包构建（`scripts/build_skillhub_packages.py`，未跟踪）、一次性脚本归档。

## 8. 工程设施

### 8.1 版本同步

canonical 源 = `pyproject.toml [project].version`（运行时经 `skills/lib/version.py` 读取）。`scripts/sync_version.py` 同步全部派生文件：6 个 SKILL.md frontmatter version 行 + 4 个 JSON manifest（`*.json.in` 模板 `{{ VERSION }}` 占位生成）+ README 发布徽章。`check` 仅在发布/CI 校验。

### 8.2 打包分发

- **WorkBuddy**：`scripts/build_wb_package.sh` → `dist/invest-skills-wb-vX.Y.Z.zip`（自举 bootstrap.sh + 6 skills），Release 零终端安装（真机验证通过）
- **SkillHub**：`scripts/build_skillhub_packages.py` — 每 skill 一个自包含包（注入 frontmatter、合并 skills/lib 消除跨包依赖）
- **Claude 插件**：`.claude-plugin/marketplace.json`（4 个 plugin）+ `.agents/` + Gemini 扩展清单

### 8.3 CI（4 条流水线）

| 工作流 | 触发 | 内容 |
|--------|------|------|
| validate.yml | PR + push main | uv sync、diagnose 预检 + 版本一致性 check、全量 pytest、release notes 提取器测试、secrets 泄漏扫描 |
| release.yml | push tag v* | 源码 tarball + WB zip 构建（**tag 一致性安全闸**）+ 双资产发布 |
| release-draft.yml | push main（版本文件变更） | 幂等同步 Draft Release |
| ai-review.yml | PR | AI code review（review.md + verdict → APPROVE/REQUEST_CHANGES/COMMENT） |

### 8.4 测试体系

全仓 **132 个测试文件 / 2215 个用例**（Python 聚合计数，含 12 个 e2e 需 `INVEST_RUN_E2E=1`）。分布：invest-a-stock 68 / lib 25 / etf 16 / journal 14 / gap-scan 7 / pattern-scan 1 / 根目录 1。invest-a-stock 测试含 v013-v026 版本回归套件与离线 fixtures（无网络合成数据）；跨 skill 收敛模块在 lib/tests 覆盖。pulse（纯编排）与 verify（自身即验证工具）无测试。

### 8.5 hooks

SessionStart hook → `hooks/scripts/check-config.sh`：环境就绪检测（uv/.venv、4 个可选 token、修复指引）。版本自检不在运行时执行（仅 CI/pre-commit）。

## 9. 相关文档索引

- [docs/cli.md](cli.md) — 全部 CLI 子命令的脚本级操作参考
- [docs/README.md](README.md) — 文档总索引与示例报告
- [docs/roadmap.md](roadmap.md) — 计划接入的数据源与版本方向
- [CHANGELOG.md](../CHANGELOG.md) — 版本变更记录
- [SKILL.md](../skills/invest-a-stock/SKILL.md) — LAW 规则与 CLI 路由（canonical）
- [modules.md](../skills/invest-a-stock/references/modules.md) — 九模块结构
- [report-conventions.md](../skills/lib/references/report-conventions.md) — 措辞与合规 canonical 源
