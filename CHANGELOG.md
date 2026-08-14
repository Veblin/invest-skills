# Changelog — invest skills

## v0.2.6 (2026-08-14)

8.11 直播量化指标体系调研（ABCD）P0 + P1/P2 全量落地。

### 第二阶段：code-review 修复 + P1/P2（同日续）

**code-review 修复（PR #19 审查）**：CLAUDE.md 违规模式计数统一（11 条）；H5 JSON 入库 docs/data/ 恢复可复现引用；journal/预案库数字按 Python 复算修正；lint 规则 3 处正则缺陷（不必然回补豁免/整数关口补「将」/斐波否定式豁免）；北向标签 days 感知（D4）；backtest_calendar 末段年份剔除 + 双源 fail loud；hsgt 缓存加锁（D8）；ps1 删除保护；docstring 批修。

**M1 全市场分位数据层**：market_daily 表（date×ts_code，2021-01 起 1361 交易日 × 5544 只回填）+ backfill CLI（断点续跑）+ market_pctile 横截面分位 + journal query_data distances 透传/分位注入（compute 保持纯函数）。

**M2 SPA/FDR**：multiple_testing.py（White 2000 Reality Check 块 bootstrap + H0 重定心 + BH-FDR + bootstrap CI）；修复 CBS 单起点退化与 RC 未重定心两个统计缺陷。

**M3 H4 金价 beta + H3 材料设备 RS**：backtest.py 扩展（OLS/Newey-West/regime/RS/binomial，stdlib-only）；H4：9 只黄金股 5 年——日频 β≈0、月度 β 全负（GC -1.23 / AU0 -1.58），样本期与 Tufano/Baur 正向先验相反；H3：RS 动量不成立，L60 显著反转（-3.07）——与 A 股动量弱反转强一致。

**M4 H2 大跌低吸 + H1 见底日**：H2：32177 起全市场大跌事件分层（封死/开板/未触及）× 成交假设双口径——事件级 tradable +1 +0.23%（t=3.11）但 **calendar-time 校正后全层全窗口显著为负**（tradable +5 -2.78% t=-8.58），低吸假设被拒绝；H1：三见底日分组「7/20 最强」未获支持（8/3 组 +5 最强 p<0.001，强时序混杂，描述性）。

**M5 形态扫描器 MVP**：新 skill invest-a-pattern-scan + skills/lib/lmw.py（因果核平滑/双底 1.5% 容差/三角底 5 极值模板）+ 18 规则宇宙 Reality Check 防护；真数据 csi300 扫描命中 21，RC p=0.9498（无统计增量信息，命中仅观察清单）。

**M6 journal §4.3 结构化字段**：6 个 DB 列（止损位/预期亏损/卖出去向/止损移动与触发计数/提取金额）+ 聚合审计函数（stop_audit_stats/extracted_amount_mtd 含冷静期违规）+ evaluation_json 5 个新键 + evaluation-criteria 卖出三维→四维债修复。

### 补漏（2026-08-14 续）：H6 回测 + 回踩分类 + 候选预案基线

- **H6 做T 支撑位反弹**（ABCD §3.2 H6 行，排期表遗漏补录）：新增 `technical.adx`（Wilder 14 日，含 DX 有效窗口与均值初始化两处数值修复）+ `scripts/backtest_h6.py`（抽样 800+沪深300 共 1060 只，MA20/BOLL 下轨/缺口回探三类支撑事件 × ADX 震荡/趋势分层）。**裁决：预注册预期（仅震荡市正超额）方向相反**——震荡市支撑位触及后超额显著为负（-0.11%~-0.40%），趋势市 BOLL 下轨为唯一正超额层（+0.07%~+0.17%）；缺口回探两类均为负（与 JBEF 2020 延续一致）
- **回踩状态分类落地**：`lmw.classify_retest`（no_retest/clean_retest/deep_retest + retest_day，窗口 3-10 日）+ 扫描器 ScanHit 字段 + SKILL.md C 级标注；csi300 实测 21 命中 → no_retest 11/clean 1/deep 9
- **E-002~E-007 候选预案基线**：`scripts/scenario_baselines.py`（六条触发定义写死，E-001 同口径）+ scenario-plans 候选表补基线栏。关键发现：E-002~E-004 绝对点位在 35 年历史中处常态区（触达 8000+ 次，基线≈全样本、信息含量低）；E-006 BOLL 上轨触达 +5 日 +2.11%（胜率 62%）显著强于无条件基线（追势延续）
- 测试新增 14 例（ADX 手算对照/H6 事件检测/回踩三态/触发判定）；报告 1 份 + JSON 存档 docs/data/

### 第一阶段：P0 落地

8.11 直播量化指标体系调研（ABCD）P0 落地：H5 日历效应回测裁决、D 类引擎字段、A 类点位红线、Windows 技能链接重建脚本、情景预案库。

### H5 日历效应回测（8 月中旬谨慎裁决）

- **新增 `skills/lib/backtest.py`**：回测纯函数库（Welch t / permutation 标签洗牌 / 逐年效应 / 滚动 5 年窗 AMH / 统一显著性分级 ✅ t≥3 ⚠️ 2≤t<3 ❌ t<2），供后续 H1/H2 复用
- **新增 `scripts/backtest_calendar.py`**：上证指数 1990-2026 全历史（akshare sina 主源 → baostock 降级链），8/15-8/31 主窗口 + 8/11-8/31 附窗口 × 全历史/2006+ 双样本
- **裁决 ❌ 不显著**：4 组合 Welch t 全部 |t|<2、permutation p 全部 >0.05（方向负差一致但效应量小、滚动 5 年符号翻转）→「8 月中旬-8 月底谨慎」**降级为建议**（journal SKILL.md 日历效应建议节），不设硬约束；8/31 中报结构性风险提示保留但标注 ❓ 弱证据
- 产出：`host-docs/v0.2.6/H5日历效应回测报告_20260814.md` + `H5_backtest_result.json`

### D 类引擎字段（四不原则可计算化）

- **`skills/lib/technical.py`**：extreme 窗口扩 (20,60,120,250)；新增 `_ytd_low` 年内低点；ATR14 暴露 `pct`；`compute()` 输出 `distances`（dist_to_52w_high/low_pct、dist_to_ytd_low_pct，数据不足 None + reason）
- **`etf_data.py compute_history_stats`**：新增 `dist_to_ytd_low_pct`/`ytd_low`/`atr14`/`atr14_pct`（纯 NAV 链路无 OHLC → None + note）；`current_vs_high_pct` 与 52 周距离等价不重复造
- **`report_qc.py`**：新字段进 derived 白名单与中文标签
- **决策（D12 WONTFIX）**：`amount_pctile_20d`/`turnover_pctile_20d` 全市场分位数据层本轮不建——schema 占位 None + note，P1 排期

### A 类点位红线（证据等级 + 自动拦截）

- **report-conventions §2.4**：L1-L4 点位证据等级定义 + 禁止断言表（将回踩 MA/缺口必然回补/BOLL 上轨将回调/斐波支撑/整数关口必然/X 浪将止于）+ 允许表述形式
- **compliance_rules.yaml 6 条 error 级规则**（wording-level-*）：只拦断言式，不误伤"已回补/未回补/回补非必然"事实句
- CLAUDE.md 点位引用规范节 + journal SKILL.md 日历效应裁决节

### Windows 技能链接重建（WorkBuddy 兼容）

- **新增 `scripts/setup_workbuddy_windows.ps1`**：仓库 14 条技能链接的 Windows 重建（9 个目录 junction + 5 个 commands 文件硬链接，均免管理员/开发者模式，幂等）；修正方案文档"9 条"计数遗漏（`.claude/commands/*.md` 5 条文件级链接）
- README WorkBuddy 安装节加 Windows 重建指引；T1-T5 真机验收由用户后置执行

### 情景预案库（references）

- **新增 `skills/lib/references/scenario-plans.md`**：预案模板 + E-001（4050 关口带，基线数字 Python 复跑确认）+ 候选 E-002~E-007 + 闭环迭代机制（触发即记录/季度命中率/版本化）+ LAW 6/6a 边界
- journal SKILL.md 情景预案闭环节（研究流程规则，非交易指令）

### 测试

- 新增 `skills/lib/tests/test_backtest.py`（19 例）、`test_v026_doc_checks.py`（16 例文档级断言 + 红线双向行为）；test_technical.py/test_etf_timeline.py 补 v0.2.6 字段用例

## v0.2.5 (2026-08-10)

交易纪律框架（资金视角方法论）D1-D8 + WorkBuddy 平台兼容 + invest-a-limit-up 移除 + code-review 15 项修复。

v0.2.5 把用户交易理念固化为 skill 纪律需求：资金选择优先、决策不受持仓盈亏影响、带血筹码分批买入、参考点独立性。纪律执行层（触发线/条件单/执行核对）经评审**不进入 skill 动作域**——skill 统一收敛为趋势/区间/状态/核对四类客观参考输出，决策动作全部留在用户侧。配套新增 WorkBuddy 平台兼容层（规则层本版落地，真机验证由用户后置执行）。

### 交易纪律框架（D1-D8）

- **统一参考输出层**：report-conventions §8 + pulse/journal 模板，四类参考（趋势/区间/状态/核对），无动作词
- **journal 卖出评估四维度**：新增参考点独立性核对（浮盈目标/回本心理/亏损不甘/成本价锚定四问 + 关键问题 + 独立依据）；Q3 错误条件选项限定逻辑失效类（含估值触发）；浮盈目标类理由触发 Odean 1998 改述提示
- **pulse 筹码出清度四信号**：去杠杆幅度/换手温度/割肉盘代理/磨底时长+企稳确认（含今日窗口），新增 `references/chip-clearance.md`
- **trade-structure 入场区间 3 段参考**：悲观锚区/中性-悲观区/中性锚区 + 状态含义，不设触发条件/比例
- **主线确认资金流优先**：两融趋势/板块轮动/ETF 份额为主证据，价格走势辅助（A 股无动量，Chui et al. 2022）
- **止损定位与话术规范**：禁止"止损提高收益"话术，定位防呆风控（Kaminski & Lo 2014）
- **观念修正内置**（C3/C5）："无低估价值股"类断言禁写；带血筹码信号强制含企稳确认字段
- **新增 `references/capital-mechanisms.md`**：资金机制参考（反弹赎回/破净赎回/量化行为/散户割肉）

### WorkBuddy 兼容

- **W1 规则层适配（本版落地）**：5 个 SKILL.md description 中文触发词、引擎调用统一 `${INVEST_SKILLS_ROOT:-.}` cd 前缀、journal Q&A 分屏（≤4 选项）
- **W2/W3 文档化交付**：`.env.example` 9 token 模板、`hooks/scripts/check-config.sh`、`.workbuddy/skills` symlink 副本、README WorkBuddy 章节（macOS + Windows）
- 真机验证（T1-T12）与 hooks 官方背书验证由用户后置执行，不阻塞本版

### invest-a-limit-up 移除

- 全仓库 grep 实证无代码调用方（`_fetch_limit_pools` 自实现直调 akshare），skill 整体移除；marketplace/skills.yaml/sync_version/test fixtures 同步清理

### code-review 修复

- **引擎 6 项**（compute_chip_clearance/_auto_persist）：阶段判定急跌口径（峰值近 5 日 + 急跌 → 去杠杆中）、I-2 守卫补全（margin 缺失不得断言磨底）、信号③④窗口含今日、_auto_persist merge 防抹 Tier-2/env_label、days_since_margin_peak 口径统一（0=峰值在今天）、窗口收缩 calc_notes 标注
- **pulse SKILL.md**：SSE 降级命令上海时区 + 长度守卫、step 1 走 data_bridge 缓存（消除 8 源双采）、规则 8 主证据路径可落地
- **文档一致性**：CLAUDE.md 盈亏比表述与 trade-structure 3 段规范对齐、D 编号引用改描述性措辞、journal 卖出维度编号 1,2,3,4
- **发布层**：CHANGELOG 补 v0.2.5 节、release tarball 补 docs/ + .workbuddy/、README 徽章纳入 sync_version

### 2026-08-11 修订

- **WorkBuddy 安装支持**：README 补官方安装流程（下载渠道 codebuddy.cn/work、系统要求 Windows 10+ / macOS 12+、Mac 芯片版本选择 ARM64/X64、安装与登录步骤、首次文件夹授权、新人积分礼包）；本机前置条件核对——`~/.config/investment/.env`（9 token）与 WorkBuddy 桌面版均未就位，T1-T12 真机验收仍为用户后置执行
- **new_high_ratio 双包装**：`_fetch_daily_panel_row` 返回 `(ts_code, records)` 元组，与 `_map_parallel` 契约（`(item, result)`）双重包装使 panel 值为元组、`rows[0]=str(ts_code)`，`_ms_new_high_ratio_from_panel` 对其 `.get()` 抛 AttributeError（报告采集中实证）；修复为 fetcher 只返回 records + 非 dict 行防御过滤
- **valuation fusion 口径混合**：legacy 重建路径对非主源注入 scalar_value（默认键序 pe_ttm 先命中=PE），`_extract_scalar` 裸标量短路绕过显式市值键 → 市值 445.71 与 PE 140.16 混合融合（322.78、max_diff 104% 不可用）；修复为 valuation 维度改用 `_extract_l2_scalar`（仅认白名单字段），口径一致性优先宁可单源
- 回归测试 2 项（真实复现路径，全 mock）；全量 1267 passed

### 2026-08-12 修订

- **valuation 口径混合收口（/code-review #1-#4）**：审查实证 08-11 修复仅覆盖融合槽——legacy 重建的 `DimensionResult.cross_validation` 裸标量短路仍将 PE 140.16 与市值 445.71 混合（divergence 104.3%）。三处收口：`SourceResult.to_dict` 对 L2 维度（financials/valuation）按白名单键提取（根因，兼修证据表 PE/市值混排）、`dimension_results_from_legacy` 非主源只接受白名单数据（不注入旧 PE scalar）、`fuse_from_legacy_dicts` 优先白名单提取（无 data 才回退 scalar_value 兼容旧快照）；无市值数据宁可单源
- **new_high_ratio 面板守卫补测**：空 df → None → `if records:` 过滤、`_map_parallel` on_error 占位 `(item, None)` 过滤两条路径（停牌/异常不崩溃）
- 回归测试 9 项（to_dict 4 + 重建 cross_validation 2 + legacy 融合 2 + 面板 2，含 1 项改写，全 mock）；全量 1276 passed

## v0.2.4 (2026-08-08)

方法论引擎 R1-R12h 落地 + 事实边界规范 + 三轮 /code-review 修复 + 全域数值/口径安全加固。

v0.2.4 是「从数据采集工具到研究方法论引擎」的转折版本：如果说 v0.2.3 是让 AI 跑得更快更稳（数据桥接、缓存、超时防挂死），v0.2.4 是让 AI **先想清楚该用什么方法，再动手分析**——把"我该怎么分析这只股票"从每次临场发挥，变成结构化决策。配套的还有一条铁律：**AI 不能猜**（事实边界 §2.3）。

### 方法论引擎（R 系列，核心）

- **R1/R2/R3/R12a/R12d 方法论匹配引擎核心**：报告先做框架匹配，不再一套模板打天下
- **R12b/R12c 财务深度补全 + 准确性硬化**：income 表 revenue/net_profit 兜底、CPI 口径归一 + 合理性校验、亏损期 PE 分位标题层强制标注、`--material-gap` 数据缺口门
- **R4 行业成功关键因素**：先答行业关键问题，再进通用 12 题
- **R5 行业景气状态卡**：五维规则引擎 + `market-status --industry`
- **R6/R7**：学术纪律补丁（技术面定位提示）+ 成长股四分类分流
- **R8 机会成本行**：盈利收益率 vs 10Y 利差
- **R9**：投委会两问 + 复盘归因（环境/能力/运气）
- **R11a ETF 历史行情深度**：baostock 双源回退 + 历史统计（年度高低点/最大回撤/±5% 交易日）
- **R11b/R11c**：事件-价格对照表 + 情景预案模板（LAW 6a，回撤档位 σ 分级）
- **R12e 近端价格结构检测**：连板识别进报告
- **R12f 分析深化契约**：简报铁律
- **R12g 双路径分流矩阵**：趋势路径引擎补强（均线系统表 + 连板结构触发）+ 开场四问 + 风格-标的匹配三态
- **R12h 数据源降级链**：字段级首选源 + L2 抽查 + 分治降级 + 限流

### 规范

- **事实边界 §2.3**（随 skill 安装生效）：禁止猜测/推断/幻觉；数据冲突并列不裁决；三态标注（可验证 / 公开不可独立验证 / 未知）；「检索不到」不得断言「数据不存在」
- R2 措辞修订：周期高点低 PE 表述去断言化

### 修复（多轮 /code-review）

- **数值安全**：`0.0` 合法值不再被 `or` 吞、rigor 除零、双估值引擎 delegate
- **指标口径**：ROIC 趋势可比口径、5 年 FCF 年报去重叠、EV/EBITDA 强制年报期、TTM EPS 连续期校验、应收信号同报告期同比、SZSE 冻结日期、上海时区统一
- **采集域**：价格冲击排序、跨源取最新财季、store 上海时区、PCR partial 恒真、margin 窗口分歧、qfq 假跳变、股东缓存、cascade deadline、北交所路由
- **渲染层**：render 去隐性网络、audit FAIL 判定序、CV7 对称、val_cache 共享、facade 惰性解析、R12g TOC 注册表、幻数消除、participant_scan 换手 key 对齐
- **报告/估值渲染域**：report `--mode`/`--plan` 断链（argparse 未注册）、`_safe_float` NaN 守卫回归、亏损期估值负区间（PE 法/盈利收益法产 None + N/A 标注）、隐含市值改市值比例法、entry_check 单 K 线越界崩溃、RSI 全涨 NaN、MA20/BOLL 共享引擎去重、资金流 NaN 安全
- **proxy/limit-up 域**：proxy logger 缺失（代理环境变量不恢复）、tushare 无 token 周末当交易日兜底、limit_up_store 唯一键 `scan_date`→`(scan_date, filter_key)`
- **跨 skill**：涨跌停表统一、ST 名称补判、市值门槛、gap 最新 bar + 数据缺口误报停牌、报告路径、ETF None 处理、max_drawdown 峰谷配对、杠杆 60 日分位剔除今日持久化行
- **时区/事件域**：catalyst NaN 整批丢失、RS 近 120 交易日窗口、`collections.kind` 列（collect/report 分离）
- **R12h 数据层**：L2 提取行序升序 + 单位归一化（total_mv 万元→亿元）、quote 腾讯实时快照独立并行尝试
- **宏观日快照 / 指数 PE 历史 / 默认落库**
- **发布域**：marketplace.json 纳入版本同步统一、shim 显式加载（消除 sys.path 顺序依赖）
- **冗余/死代码清理**（B/C 类 + code-review 第三轮 #1–#8）

### 市场情绪增强（invest-a-pulse）

- **涨停行业轮动** `zt_industry_flow(days)`：东财涨停池按行业聚合，Top5 + N 日趋势 + 前后半段拆分（`return_daily=True` 输出每日矩阵）
- **跷跷板观察** `zt_seesaw(days)`：板块簇占比 Pearson 相关（10 簇映射 + n 样本 p<0.05 临界值）+ 显著负相关对（跷跷板候选）/正相关对（同步资金池）+ 前后半段 Δpp；标注「参考，不构成投资决策」
- invest-a-pulse 报告新增「行业维度」与「⚖️ 跷跷板观察」章节，输出分析结论而非数据表

### 工程

- 版本号同步机制：pyproject.toml canonical → 派生文件（bump-version.sh / sync_version.py）
- 版本验收：四类样例 + 复测记录 + fixtures 冻结

## v0.2.3 (2026-08-04)

数据桥接层（data_bridge）落地 + 巨型模块拆分 + 多轮 /code-review 修复 + 采集管线性能与健壮性优化 + 数据源必要性落地（4 源评估：tushare+akshare 必要 / baostock 兜底 / tickflow 可移除）。

### 数据桥接层

- **data_bridge 新增 8 个 ETF 缓存维度**：`etf_spot` / `index_pe` / `nav` / `index_daily` / `adj_factor` / `share_history` / `industry_alloc` / `category_sina`
- **canonical etf_data 全部网络调用接入 data_bridge L2 缓存**
- **journal 消费侧接入 data_bridge**：microstructure 5min 缓存生效；shim re-export 8 个 `fetch_*`（data_bridge 上下文解析关键）

### 采集管线 4 项优化（挂死根因修复 + 提速）

- **全局 socket 超时**（`INVEST_SOCKET_TIMEOUT` 默认 30s，`0`=不设置）：修复 akshare/baostock 无 timeout 接口无限挂死（实测曾挂死 20 分钟），已显式传 timeout 的调用不受影响
- **同日 K 线缓存**（`collect_kline_cache`，TTL 1 天，`INVEST_KLINE_CACHE=0` 禁用）：键含 source + 查询窗口 + 复权语义，默认 400 日与 `--deep` 730 日互不误用；**kline 维度实测 76.8s → 0.4s**
- **开发模式日志**（`INVEST_DEV=1` → stderr INFO + 轮转文件，release 零文件 I/O）：逐源耗时 + 维度耗时 + 慢源（>60s）告警，挂死类问题可秒级定位
- **慢源降级**（`INVEST_SOURCE_TIMEOUT` 默认 60s deadline）：`_run_sources_parallel` 重写为 daemon 线程 + 信号量限流，超时源返回 timeout 占位 → 维度 partial 优雅降级；`_run_with_timeout` 同病同治。弃用 `ThreadPoolExecutor`（其 `__exit__` join 挂起线程会拖死整个维度）

### 数据源必要性落地

- **tickflow 默认关闭**（`INVEST_ENABLE_TICKFLOW=1` 启用）：免费 tier 慢，其前复权价值已由 tushare adj_factor 自算覆盖
- **baostock 默认 auto**（`INVEST_ENABLE_BAOSTOCK=1/0` 覆盖）：仅无 Tushare token 时启用（与 gap-scan `create_source("auto")` 语义一致）
- **K 线统一前复权**：tushare `daily + adj_factor` 自算（`_q_tushare_daily_qfq`，公式对齐 gap-scan `qfq.py`）、akshare `adjust="qfq"`、baostock `adjustflag="2"` — 除权日假跳变消除（301165 转增史 5 个 -16.7% 假跳变日全部消失，最大单日波动 40.26% → 20.87%），技术指标不再被除权污染，跨源校验不再因语义错配误报 divergence
- 缓存键 `__qfq` 标记：复权语义变更后新键生效，旧缓存自动失效

### 代码结构与质量

- **共用库提升**：6 个 skill 去重（`skills/lib/` 纯函数层 + shim）
- **巨型模块拆分**：`render_markdown` 5110 行 → 4 模块、`collector` 4234 行 → 3 模块
- **多轮 /code-review max 修复**（30 + 15 + 8 + 8 项）：复权启发式 / 空信封缓存 / 缓存陈旧 / 复权因子除权日错位（拆分 ETF 假跳变）/ cache TOCTOU 等
- **行业分析框架** + 引擎衍生指标（`derived` 字段）+ AI 计算禁令（P0）

## v0.2.2 (2026-07-28)

v0.2.2 建设市场微观结构指标体系与平台化基础设施：新增市场情绪全景 Skill、共用函数层、TTL 缓存层；ETF 模块补齐行业基础设施（类型分类/PE快照/持仓代理/对冲映射），并修复 8 项设计问题。

### 市场微观结构

- **`market_microstructure.py`**：17 指标统一采集管道（Tier 1 涨跌比/涨跌停比/两融/北向/成交额 + Tier 2 衍生指标/分位 + Tier 3 ERP/PCR/破净率）
- **`market_snapshots` 表**：每日快照持久化，支持历史分位计算与趋势对比
- **`market-status` CLI**：`invest.py market-status [--save] [--days N] [--json]`，一键查看当日杠杆/广度/情绪/估值温度
- **环境标签 v2**：历史分位 + 趋势 + 多指标交叉验证规则，`env_label` JSON 输出供 journal 自动注入
- **北向资金**：季度持股市值变动推算净流向（日频 2024-08-19 起停更后的替代方案）
- **两融 akshare 降级**：`collector._ms_fetch_margin()` 添加 `stock_margin_account_info` 降级路径（Tushare margin_detail 不可用时自动切换全市场汇总）

### invest-a-pulse（新 Skill）

- 市场情绪全景分析 Skill：5 章节结构化报告（杠杆周期/市场广度/极端情绪/资金面/估值温度）
- 数据来源：`market_microstructure.snapshot()` + `load_history(60)`
- 综合环境标签自动生成（正常/偏谨慎/⚠️ 警告），journal 评估流程自动注入

### 平台化基础设施

- **`skills/lib/` 共用层**：从 invest-a-stock 抽离纯函数（`nums.py`/`stats.py`/`technical.py`/`dates.py`/`codes.py`），各 Skill 经 `_invest_path` shim 引用
- **TTL 缓存层**（`cache.py`）：JSON 文件缓存，维度级 TTL，盘中/盘后差异化过期策略
- **`data_bridge.py`**：跨源数据桥接，带重试与降级
- **共享报告规范**（`report-conventions.md`）：措辞规范/证据强度/分析合成三步/self-check — stock/etf/journal/pulse 统一引用
- **`industry_snapshot.py`**：申万行业 PE/PB 周频采集，`industry_weekly` 表持久化

### invest-a-etf 行业基础设施（G1-G9）

- **G1 ETF 类型分类**：`query_etf_category()` — 硬编码映射 → fund_etf_category_sina → 名称关键词推断，三级降级
- **G2 行业 PE 快照**：`collect_industry_weekly()` + `industry_weekly` 表，31 申万行业 PE/PB 排名
- **G4 行业 ETF PE 映射**：`_attach_industry_pe()`，ETF→SW 行业→行业 PE 代理
- **G6 持仓代理**：`_attach_industry_allocation()`，`stock_board_industry_cons_em` 获取前 5 大行业暴露
- **G7 对冲映射扩展**：`ETF_HEDGE_MAP` 16→39 只，覆盖宽基/跨境/商品/主要行业 ETF
- **G9 行业估值指引**：`query_sector_valuation_guide()` + `_attach_valuation_guide()`，行业特定估值指标与 pe_timing 判断
- **ETF 份额流跟踪**：`save_etf_share_snapshot()` + `etf_share_flow()`，份额变动 + 估算资金流

### 设计问题修复（D1-D8）

- **D1** auto-flags 按 ETF 类型区分阈值：`_TYPE_THRESHOLDS` 区分跨境(5%)/债券(2%)/商品(3%)，消除 QDII 误报
- **D2** RSI 注释修正：注明 Wilder RSI 默认周期 24（非 standard 14），删除冗余 `rsi_24` 字段
- **D3** 折溢价符号验证：确认 EM `基金折价率` + = 折价，`-em` 取反逻辑正确，补充注释
- **D4** MA 增加指数价格 MA：`_fetch_index_ma()`，NAV MA 重命名为 `ma20`/`ma60`，新增 `index_ma20`/`index_ma60`
- **D5** `etf_share_flow` 文档修正：`days` 参数语义从"自然日"更正为"行数"
- **D6** 死代码删除：移除 `from lib import env as _env` 未使用导入
- **D7** 波动率固定 60 日窗口：跨 ETF 可比性
- **D8** BOLL 接入 kline：`boll_upper`/`boll_mid`/`boll_lower`，基于 NAV 20 日窗口

### invest-a-limit-up 废弃

- 用户入口下线：移除 `.claude/commands/invest-a-limit-up.md` 符号链接
- SKILL.md 精简为数据管道说明（134→53 行），`user-invocable: false`
- `limit_up_scanner.py` + `scan.py` CLI 保留为 `market_microstructure._fetch_limit_pools()` 的数据源（**v0.2.5 已整体移除**：grep 实证 `_fetch_limit_pools` 自实现直调 akshare，无任何代码调用方；本条为 v0.2.4 时点记录）

### Bug 修复

- 修复 `market_microstructure` 6 个数据抓取缺陷（Tushare 导入、PCR 查询、裸 except 等）
- 修复 `query_data._median` 替换为 `statistics.median`
- 移除 4 个未使用导入（`os`/`math`/`as_completed`/`List`）
- 去重 `_conn`/`_safe_close` 数据库连接工具函数
- 修复 `data_bridge` 裸导入：添加相对导入 fallback
- 修复 etf-flow JSON 序列化、days 参数语义、capital_flow 显示
- `fund_adj` 前复权集成：消除 NAV 序列分红/拆分断点
- 报告文件名强制实际时间戳（禁止硬编码）

### 文档

- `host-docs/v0.2.2/`：requirements.md / gap-verification-and-design-flaws.md / fix-execution-plan.md / architecture-cache-layer.md / architecture-shared-lib.md / deep-research/（9 篇调研文档）
- README 更新：版本号 v0.2.2、新增 invest-a-pulse / market-status、项目结构更新

## Unreleased

## v0.2.1 (2026-07-23)

v0.2.1 扩展多 Skill 组合：新增 ETF 研究与交易日志评估，补齐跳空缺口扫描；统一用户 slash 为连字符；并收敛一批估值/涨停/数据源正确性修复。

### Slash 与 Skill 入口

- **用户 slash 一律连字符**：`/invest-a-stock`、`/invest-a-etf`、`/invest-a-journal` 等；`.claude/commands/` 文件名改为 `invest-a-*.md`（放弃 `/invest:a-*` 用户入口）
- 插件 marketplace **包名**仍可使用 `invest:a-*`（与 slash 分层）

### invest-a-etf（新 Skill）

- 新增 ETF 独立研究 Skill：指数估值 / 折溢价 / AUM / 跟踪质量 / 对冲覆盖 → 结构化备忘录
- CLI：`uv run python skills/invest-a-etf/scripts/etf.py report|diagnose`
- `etf_data.py` + `etf-hedge-map.md` 自 journal 迁出为本 Skill canonical；journal 经 thin shim 复用

### invest-a-journal

- ETF 评估路径改为调用 invest-a-etf 共用模块；深研引导 `/invest-a-etf {代码}`

### 报告精简

- 移除报告头部冗余段落：AI 偏见声明、逆向思考、Ichimoku/波动率锥、AI 置信度矩阵
- 移除占位符段落：F-1 理解陈述、F-5 偏误自查表、F-6 交叉验证记录表（占位）、九模块 mermaid 图
- 恢复报告头部 `[宏观情景]`、`[产业链]`、`[报告增强触发]`
- `--emit md` 不再自动生成 HTML 兼容文件（需 HTML 时显式 `--emit html --outdir`）

### 宏观扩展

- `--with-macro` 增加全球指标：VIX（FRED VIXCLS）+ SOX（Yahoo `^SOX`）；简报标签国内/全球用 `|` 分隔
- 修复 akshare 不可用时 early-return 跳过 VIX/SOX 的问题（中国与全球采集路径解耦）

### 数据融合

- `schema._extract_scalar` 按维度映射语义字段（`_DIM_SCALAR_KEYS`），`fusion.fuse_from_source_results` 传入 `dim_name` 避免跨源比较不同量纲

### invest-a-gap-scan

- 新增跳空缺口扫描 skill：沪深300 + 中证A500 + 科创50 并集，向上缺口 + MA60 + 未回补三重筛选
- **数据源降级**：Tushare Pro（批量按日）→ baostock（逐股前复权 `adjustflag="2"`）自动切换
- **缓存**：按日期 + 数据源隔离的 pickle 缓存（TTL 3 天），同日二次扫描 ~7s
- **停牌检测**：交易日历对比日线数据，跨停牌缺口单独列出
- 容忍规则：从新到旧遍历缺口，遇到首个满足 MA60 + 未回补 + 量比的即命中

### Bug 修复

- 修复 gap-scan `start_date` 硬编码 90 天导致 MA60 虚设：改为 `gap_lookback + 59` 交易 bar × 1.46 日历转换，默认 ~180 天
- 修复 gap-scan baostock 部分缓存命中时 `daily_by_date` 缺少缓存标的，导致跨停牌检测静默失效
- gap-scan `hasattr(source, "set_ts_codes")` → `isinstance(source, BaostockSource)`，消除鸭子类型隐患
- 新增 gap-scan 回归测试：短历史 MA60 虚设、`min_list_days=60` 默认值、混合停牌检测覆盖

- 修复 `valuation_calc.py` PE 亏损期警告死代码（`pe_negative_excluded` → `pe_none_or_neg`）
- 修复 ROE 年化乘数始终 ×4 的问题（根据报告期区分 0331×4/0630×2/0930×4/3/1231×1）；`roe_data.roe_quarterly` 重命名为 `roe_cumulative`（YTD 累计值）
- 修复 collector 业绩预告 "None%" 渲染（`p_min`/`p_max` 为 None 时省略同比）
- 修复 `cmd_report` HTML 路径缺少 `_ensure_render_ready()`
- 修复 `schema._extract_scalar` 按维度选字段时 quote/northbound 零值与无 dimension 回退路径回归（`change_pct`/`net_mf_vol` 提取失败导致融合与交叉验证跳过）

- **估值 OCF/EPS TTM**：按 `end_date` 对齐，且要求连续 4 个报告期（断档不冒充 TTM）
- **PE/PB 历史分位**：两侧独立计算，一侧缺失不再整段丢弃
- **涨停 Tushare enrich**：`amount`/`close` 为 None/0 时不得覆盖 akshare L1 有效值
- **gap-scan `create_source`**：短生命周期探活一次，避免双探活与失败路径 session 泄漏
- **journal**：`_percentile` 用 `<=`；个股空 K 线 → `missing`；`update`/`delete` 写路径补 `rollback`
- **gap-scan**：`gap_high` 近零容差与 MA60 一致，避免百分比爆炸

### Emoji 统一

- 新建 `render_icons.py` — 集中管理 CV 交叉验证与证据强度标签
- `schema._CV_ICONS` 与 `_evidence_strength_label` 迁移至 `render_icons`

## v0.2.0 (2026-07-13)

v0.2.0 将项目从单 skill 演进为多 skill 组合，统一命名空间 `invest:`。新增科学估值计算器、多 Agent 并行深度分析、估值持久化与回溯。

### 多 Skill 架构

- **invest:a-stock**（原 invest-A）— A股个股深度研究，九模块多因子分析
- **invest:a-limit-up**（原 limit-up）— A股涨停板全市场扫描 + 归因深挖
- 命名规范：`invest:{市场}-{标的}`（如 `invest:us-stock`、`invest:hk-stock` 未来可扩展）

### 项目重命名

- Skill 名称：`invest-A` → `invest:a-stock`，`limit-up` → `invest:a-limit-up`
- 目录重命名：`skills/invest-A/` → `skills/invest-a-stock/`，`skills/limit-up/` → `skills/invest-a-limit-up/`
- 项目身份：README / AGENTS / CLAUDE / CONFIGURATION 全面更新
- pyproject.toml 项目名：`investment-learning-skill` → `invest-skills`

### 科学估值计算器（`valuation_calc.py`）

- **七步估值流程：** 基础参数 → 核心财务（TTM EPS/BVPS/ROE/OCF质量） → 历史分位（PE Band + PB） → 盈利收益率 vs 要求回报率（Fed Model 变体） → 反推 market-implied g → ROE-PB 理论匹配 → 多情景 × 多方法综合区间
- **TTM EPS 精准计算**：`fina_indicator` 累计 EPS 差分为单季 EPS，再求最近 4 个单季之和
- **PE 失真检测**：亏损期占比 >30% 时自动切换 Gordon 模型合理 PE，避免被失真的历史 PE 中位数误导
- **三级数据降级**：akshare 东财 API（代理阻断）→ 腾讯行情兜底 → 报错
- **CLI 集成**：`invest.py value 002466 [--rf X] [--erp X] [--store] [--emit json]`

### 多 Agent 并行深度分析

- **两阶段并行架构**：Phase 1 — 3 Agent 并行采集 → merge + 交叉验证；Phase 2 — 4 Agent 四视角并行分析；Phase 3 — 主编合成
- **四视角 Agent prompt 模板**（`references/agent-prompts.md`）：生意质量 / 财务估值 / 行业竞争 / 风险治理，每个含数据提取命令、分析框架、输出规范、LAW 合规要求
- **交叉验证**（`merge_collections.py`）：关键字段 Tushare vs akshare 双源对比，差异 <5% 通过、5-20% 标注、>20% 触发 tie-breaker
- **性能提升**：深度报告耗时从 ~14 分钟（串行）压缩至 ~6 分钟（并行），采集 ~2min + 分析 ~3min + 合成 ~1min

### 估值持久化与回溯

- **新增 `valuations` 表**：双写结构化列（price/ttm_eps/bvps/ttm_pe/pb/rf/erp/roe/ocf/pe_pct/pb_pct/各情景区间） + 完整 `result_json`
- **`store valuations`**：列出历史估值记录，支持 `--symbol` 过滤
- **`compare_valuations(id1, id2)`**：两期估值快照对比（price/ttm_eps/ttm_pe/pb/base_mid 增量）

### CLI 新增

- `invest.py value` — 科学估值计算（七步多方法：PE/PB/盈利收益率/隐含增长/ROE-PB 匹配）
- `invest.py store valuations` — 估值历史列表
- `valuation_calc.py` — 独立脚本，可脱离 CLI 单独使用

### 文档更新

- **README.md**：统一命令格式为 `invest:a-stock` / `invest:a-limit-up`；Token 表格新增 Tushare 积分档位对照（120/2000/5000/10000+）；新增对话式配置 Token 指引；新增多 Agent 深度分析架构图；补充 `value` / `store valuations` 命令
- **SKILL.md**：SOP-DEEP 重写为三阶段多 Agent 流程；新增数据降级说明

### 基础设施

- `scripts/sync_version.py` 统一版本收敛：`pyproject.toml` → 2× SKILL.md + 3× JSON（由 `.json.in` 模板生成）
- CI / pre-commit / `bump-version.sh` 统一调用 `sync_version.py check|bump`
- CI/Release 工作流适配双 skill 目录
- 跨 skill 导入路径修复（limit-up → invest-a-stock）

## v0.1.9 (2026-07-10)

v0.1.9 交付质量门工具链、新闻三层架构、5 个新 CLI、渲染扩展与技术指标 P0–P1。

### 质量门（Phase 2）

- **`lib/financial_rigor.py`**：`verify-market-cap` / `verify-valuation` / `cross-validate` / `calc`
- **`lib/report_audit.py`**：`audit --extract` / `--verdict`
- **`lib/render_extras.py`**：>5% 跨源差异警示（`--strict-rigor` 严格模式）

### 新闻采集（Phase 3）

- **`lib/news_scanner.py`**：公告 + 声明式查询包 + 可选 Tavily
- `collect --with-news-pack`；`env.py` 加载 `TAVILY_API_KEY`
- **`events.calc_price_impact_interpolation`** + `shock` CLI

### CLI（Phase 4）

- `rigor` / `audit` / `check` / `portfolio` / `thesis`
- `lib/quality_check.py`：7 指标 + 3 豁免
- `lib/portfolio_review.py`：行业集中度 / 相关性 / 压力测试
- `store.py`：`thesis` 表

### 渲染与文档（Phase 5）

- AI 偏见声明 / 逆向思考 / A+H 检测标注 / 外生冲击段
- SKILL.md：SOP-DEEP、earnings-review、news-pulse
- source-guide：新闻三层架构

### 技术指标（Phase 6）

- Ichimoku / 波动率锥 / RS / 滚动 Beta（默认基准 000300.SH）

### 测试

- `test_financial_rigor` / `test_news_scanner` / `test_report_audit` / `test_quality_check` / `test_technical_v019`

### Phase 1（早期交付 2026-07-07）

- SKILL 拆分为核心 + references；`plan --intent` 扩展
- `participant_scan.py`；财务软信号 `revenue_acceleration_flag` / `ocf_np_divergence_flag`

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
- **LAW 10–16** 方法论规范（见 `skills/invest-a-stock/SKILL.md`）
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
