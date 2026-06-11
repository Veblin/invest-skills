---
name: invest-A
version: "0.1.0"
description: "A股个股调研 AI 学习技能 — 数据采集 + 学术级引用，产出带来源追溯的 Markdown 学习报告。这是一个学习工具，不是决策工具。"
argument-hint: "/invest-A 600176 | /invest-A 600176 --with-macro | /invest-A 600176 --deep | /invest-A 600176 --compare 000858"
allowed-tools: Bash, Read, Write, WebSearch, WebFetch
user-invocable: true
metadata:
  requires:
    bins: [python3]
  optionalEnv:
    - TUSHARE_TOKEN
    - FRED_API_KEY
    - TAVILY_API_KEY
---

# 投资学习 Skill

## 核心定位

**这是一个学习工具，不是决策工具。** 不提供买卖建议、目标价或仓位建议。

### 数据采集哲学：不是"兜底"，是"并行取证"

```
❌ 旧模式（v0.2）：Tushare → 失败 → akshare → 失败 → 腾讯 → 标注不可得
   ↳ 串行降级，单源数据无法交叉验证

✅ 新模式（v0.3+）：全部可用源并行查询 → 各渠道独立记录 → 汇总为证
   ↳ 每个维度对所有可用源同时发起请求，各渠道独立记录
   ↳ 所有渠道均失败 → 结论标注"未获取到任何有效数据，无法判断"
   ↳ 多源都有数据 → 标注各渠道内容、以哪个为主
   ↳ 类似论文的"多来源相互印证"，不是"一个不行换另一个"
```

## 📡 数据来源配置

当前已配置的数据源：
- **Tushare Pro** ✅ — 基础信息、日线、财务指标、十大股东、资金流向
- **FRED** ✅ — US 10Y/2Y/VIX/CPI/美元指数 全部可用
- **腾讯行情** ✅ — 实时报价兜底
- **akshare** ✅ — 免费数据源，Tushare 不可用时的兜底（部分接口因东方财富反爬不可用）
- **baostock** ✅ — 免费交易所 K 线数据，无需 token
- **efinance / yfinance** 🔜 — 计划在未来版本接入（详见 `docs/TODO.md`）

## CLI 命令（核心交互入口）

所有数据采集和分析通过 `invest.py` 完成。Claude 根据用户意图构建对应命令：

```bash
# 采集并展示
python3 skills/invest-A/scripts/invest.py collect 600176

# 生成 JSON 格式报告
python3 skills/invest-A/scripts/invest.py report 600176 --emit=json

# 生成 Markdown 分析报告
python3 skills/invest-A/scripts/invest.py report 600176 --emit=md

# 深度模式（扩大K线范围 + 行业/舆情分析）
python3 skills/invest-A/scripts/invest.py collect 600176 --deep
python3 skills/invest-A/scripts/invest.py report 600176 --deep

# 对比双标的财务数据
python3 skills/invest-A/scripts/invest.py compare 600176 000858

# 检查各数据源可用性
python3 skills/invest-A/scripts/invest.py diagnose

# 查看历史采集记录
python3 skills/invest-A/scripts/invest.py store list

# 保存本次采集到持久化存储
python3 skills/invest-A/scripts/invest.py collect 600176 --store
```

> **注意**：`invest.py` 在 `skills/invest-A/scripts/` 下。运行前确保终端在 `code/` 目录。
> 所有子命令支持 `--help` 查看参数详情。

## 采集顺序

1. **`diagnose`** — 先检查数据源是否可用
2. **`collect`** — 采集指定股票的六维度数据（基本信息、财务、行情、股东、资金流、日K线）
3. **`report`** — 对采集结果进行分析并生成报告（Claude 手动分析 + 渲染）
4. **`store`** — 将高质量采集结果持久化（可选）

---

## 输出契约（9 条 LAWs）

以下法则约束所有输出。违反即为 Bug。

**LAW 1** — 每条分析论述必须引用数据来源。
**LAW 2** — 报告使用统一七维度结构：基本信息 → 财务报告 → 行业 → 估值/市场 → 机构 → 情绪 → 宏观。
**LAW 3** — 区分"事实陈述"与"分析判断"。
**LAW 4** — 风险提示出现首部和尾部。
**LAW 5** — **并行取证，汇总为证。** 采集器对所有可用源并行查询（非串行降级），各渠道独立记录。如果所有渠道均无法获取某一维度的有效数据，报告必须在该维度明确标注 **"未获取到任何有效数据，无法判断"**，而非默认跳过或让读者自行推断。多源均有数据时，标注各渠道内容摘要及以哪个为主。
**LAW 6** — 禁止买卖建议、目标价、仓位建议。
**LAW 7** — 每个数字标注可审查的追溯路径（函数调用、查询语句、公告链接）。**最终数据来源清单表必须包含检索关键字/API 调用参数列**——仅写来源名称不满足 LAW 7。
**LAW 8** — 每个维度末尾要求"🔍 待独立验证项"。
**LAW 9** — 无数据源支撑的分析不输出。

### 数据源策略（v0.2 → v0.3 变更）

| 版本 | 策略 | 缺点 |
|------|------|------|
| v0.2（旧） | 串行降级：Tushare → 失败 → akshare → 失败 → 腾讯 | 单源数据无法交叉验证 |
| v0.3（当前） | **多源并行**：全部可用源同时采集，选最优为主数据，全部结果附于 `_meta.all_sources` | 需辨别各源差异 |

> 当 Tushare 和 akshare 对同一指标给出不同数值时，采集器**保留所有值**，由 Claude 在分析阶段判断并标注差异。

## 引用格式规范

```
事实性陈述： "数值" [来源: {追溯路径} / {获取日期}]
  ✅ "营收 188.81 亿元 [来源: WebSearch / query: '中国巨石 600176 2025年报'
     结果3(东方财富 finance.eastmoney.com/a/...) / 2026-06-10]"
  ✅ "当前 PE(TTM) 41.23x [来源: collector.collect_quote(symbol='600176') → tencent_finance / 2026-06-10]"

分析性判断： "判断" [依据: {数据来源}； 逻辑链: {推理}]
推测： "推测" [推测，待验证]
无数据（全部渠道失败）： "未获取到任何有效数据，无法判断。[尝试了 {源1}、{源2}，均失败]"
```

## 附录 — 引用来源表规范

报告末尾必须包含一个"引用来源（References）"附录，类似论文的参考文献章节。表格列出**每个渠道的独立结果**，表头固定为：

```
| 维度 | 渠道 | 追溯路径 | 数据状态 |
```

- **数据状态** 列必须清晰标注：
  - `✅ 有数据` — 该渠道成功获取数据
  - `❌ {错误原因}` — 该渠道失败
  - `⏭️ 未尝试` — 该渠道因依赖缺失未执行
- **追溯路径** 必须包含可复现的查询参数，示例如下：

| 维度 | 渠道 | 追溯路径 | 数据状态 |
|------|------|----------|---------|
| 日K线 | tushare.daily | `pro.daily(ts_code='300328.SZ', start_date='20260501')` | ✅ 有数据 |
| 日K线 | akshare.stock_zh_a_hist | `ak.stock_zh_a_hist(symbol='300328', period='daily')` | ❌ 连接被拒绝 |
| 日K线 | baostock.kline | `bs.query_history_k_data_plus(...)` | ✅ 有数据 |
| 行业动态 | WebSearch | `query: "宜安科技 液态金属 2026"` | ✅ 有数据 |

- Tushare 类：标注 `pro.{api_name}()` + 调用参数（symbol、日期范围等）
- WebSearch：标注 `query: "..."` 原字符串
- akshare：标注 `ak.{函数名}(参数...)`
- 腾讯行情：标注 `qt.gtimg.cn` + 请求 URL
- baostock：标注 `bs.query_history_k_data_plus(...)`

## 技术指标规则

均线（MA）和 MACD 仅用于**描述市场状态**，不生成交易信号。

---

## 七维度指引（分析阶段使用）

| 维度 | 分析要点 | 数据来源 |
|------|---------|---------|
| 基本信息 | 公司概况、股权结构、治理信号 | collect.basic_info |
| 财务报告 | ROE趋势、毛利率、现金流、成长性 | collect.financials |
| 行业产业链 | 竞争格局、壁垒、上下游 | Claude 分析（WebSearch 补充） |
| 估值/市场 | PE/PB水位、历史分位、融资余额 | collect.quote + collect.kline |
| 机构分析师 | 股东结构、北向资金、机构评级 | collect.shareholders + collect.northbound |
| 情绪舆情 | 近期事件、媒体报道 | Claude 分析（WebSearch 补充） |
| 宏观政策 | 利率、汇率、行业政策 | `--with-macro` 时 Claude 分析 |

> ⚠️ "行业产业链"和"情绪舆情"两个维度无稳定 API 支撑，依赖 WebSearch 和 Claude 分析，须标注数据来源并提示不确定性。
>
> **⚠️ 项目根目录的 `archive/` 是旧代码归档（v0.2 遗留），不被 Skill 引用。**
> **不要从 `archive/` 中导入任何 Python 模块或引用配置。** 该目录仅保留用于历史追溯。
