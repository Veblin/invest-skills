---

name: invest-a-event-calendar
version: "0.2.9"
description: "限售解禁压力日历 v2 — 池模式（清单池个股解禁日下钻 + 变化提醒，排雷首选）+ 市场模式（全市场日级分位，低频参考）。研究工具非决策。触发词：解禁日历/解禁压力/限售股解禁"
whenToUse: "持仓/自选池排雷（'我的票哪天解禁'）、或低频查看全市场解禁压力分布（解禁≠减持，与减持公告联动）"
argument-hint: "/invest-a-event-calendar --pool-file pool.txt"
allowed-tools: Bash, Read, Write
user-invocable: true
metadata:
  requires:
    bins: [uv, python3]
---

# invest-a-event-calendar 限售解禁压力日历（v2）

> **工具约束说明**：frontmatter 的 `allowed-tools` 是 Claude Code 约定；在 DSH 等不读取该字段的 harness 下不生效，实际可用工具由平台自身沙箱控制。本技能全部操作为本地数据采集与计算（Bash/Python 引擎）。

## 概述（双模式）

**① 池模式（首选）**：对清单内的个股逐一下钻个股解禁队列，产出：

- **🔔 提醒段**：未来 `--alert-days`（默认 30 自然日）内有解禁批次的标的（解禁日/距今**交易日**/数量/股东数/类型）
- **🆕 变化段**：相较上次运行的新增/消失/字段变化（状态存私有文件；首跑建基线不告警）——**只推新增与临近，不做每日全表**
- **📋 全池明细**：含逐标的「无解禁记录」与「取数失败」区分标注
- 落盘 `reports/event-calendar/{YYYYMMDD}-pool.md`

**② 市场模式（低频参考）**：全市场解禁日汇总（东财 summary_em），回看近 120 日 + 展望未来 30 日，未来解禁日按市值相对近 120 日序列分位标注（≥80% 高压 / ≥90% 极高压力）。**注明：市场级日历不含个股名单**，用于给时段分配注意力，不能用于个股排雷——个股排雷用池模式。

**解禁 ≠ 减持**：解禁是供给事件提示，非方向信号。控股股东/实际控制人解禁后多数不立即减持（锁定期另有承诺）；真减持需看减持预披露公告（invest-a-stock 公告采集已有）。压力/提醒的作用 = 提示「何时去看供给面事件」，配合减持公告与 journal 评估使用。

**v2 边界（如实标注）**：池来源本期 = **清单文件**；journal 持仓推导与 portfolio holdings.json 直读为二期（扩展位见 `_run_pool` 的单一入口）。股东户数/高管持股/分红实施日历仍为按股票接口，未接入。

## 运行

```bash
# 池模式（首选）：清单文件每行一个 6 位代码（# 注释）
cd "${INVEST_SKILLS_ROOT:-.}" && uv run python skills/invest-a-event-calendar/scripts/unlock_calendar.py --pool-file /path/to/pool.txt

# 市场模式（低频）
cd "${INVEST_SKILLS_ROOT:-.}" && uv run python skills/invest-a-event-calendar/scripts/unlock_calendar.py
```

| 参数 | 默认 | 说明 |
|------|------|------|
| `--pool-file PATH` | — | **池模式开关**：清单文件路径（每行一个 6 位代码；`#` 注释；坏行跳过并告警） |
| `--alert-days N` | 30 | 提醒窗（自然日；池模式） |
| `--lookahead N` | 90 | 拉取展望（自然日；池模式） |
| `--state-file PATH` | `~/.local/share/investment/event_calendar_state.json` | 变化检测状态（私有） |
| `--no-state` | false | 不读写状态（调试） |
| `--days-past` | 120 | 分位回看窗口（自然日；市场模式） |
| `--days-future` | 30 | 展望窗口（自然日；市场模式） |
| `--no-out` | false | 不落盘 |
| `--out-dir` | `reports/event-calendar/` | 输出目录 |

落盘：池模式 `{YYYYMMDD}-pool.md`；市场模式 `{YYYYMMDD}.md`。
退出码：0 正常（含部分标的失败，报告逐行标注）；2 参数/池文件错误；3 不可得（市场空返回且窗口含交易日，或池内全部标的失败）。

## 数据源与口径

- 个股源（池模式）：`stock_restricted_release_queue_em(symbol=...)`（东财，单标的；共享实现 `skills/lib/unlock_source.py`）——失败以 `(rows, error)` **显式返回**，报告逐标的标注「取数失败：原因」，**不得当作「无解禁」**
- 市场源（市场模式）：`stock_restricted_release_summary_em(symbol="全部股票")`（东财数据中心；ProxyError → 「数据不可得」退出码 3，不硬编）
- 原始单位：解禁数量 = 股（脚本换算为亿）；市场模式实际解禁市值 = 元（换算为亿）
- 距今天数 = **近场（≤365 自然日）交易日口径**（经 `skills/lib/freshness.py`，长假后不误报）；远场或日历不可用 → 自然日 + 标注「自然日粗判」（交易日历不覆盖远端，防截断计数）
- 分位 = bisect_left 口径（同 pulse），基准 = 近 120 日**有解禁日**的市值序列
- 状态文件：私有 JSON（`~/.local/share/investment/`），只存批次与运行日；失败标的保留旧状态不覆盖

## 分析纪律

1. **解禁 ≠ 必然减持**：提醒/压力日只提示事件集中度；个股层面须查减持预披露（若大股东计划减持会先公告）——提醒 + 减持公告双信号才构成供给面关注（仍非买卖信号）
2. **分位/提醒是横向提示**：市值分位高、临近解禁 ≠ 利空成立——需看解禁股东类型（控股 vs 财务投资者）、承诺与当前股价位置，本工具只给日历与分位
3. **不要用回看段的沪深300表现做因果**：那是参考坐标，不是「解禁日必跌」的证据（样本与混杂因素未控制）
4. **数据不可得时如实降级**：取数失败逐行标注，不以「无解禁记录」冒充；市场模式 ProxyError → 明确「数据不可得」
5. **变化段是运行间差异**：不是「新增解禁事件」的公告确认——同一标的首次纳入池时只建基线；把变化段当排雷信号使用前，先核对原始公告

## 参考输出层

| 参考类型 | 内容 | 来源 |
|------|------|------|
| 池提醒（事件参考） | 提醒窗内有解禁批次的标的（日/距今交易日/数量/股东数/类型） | [来源: unlock_source 引擎输出] |
| 池变化（对照参考） | 相较上次运行的新增/消失/字段变化 | [来源: 状态文件 diff 引擎输出] |
| 市场日历（事件参考） | 未来 30 日逐日解禁家数/市值 + 高压日标注（无个股名单） | [来源: unlock_calendar 引擎输出] |

> 只描述供给事件事实，不含任何动作建议；执行由你依据自身纪律决定。

> **机器层准出（报告类产出必跑，非可选）**：`uv run python skills/lib/report_qc.py <产出文件> --fail-on error` → 无 error 级发现（退出码 0/1）方可交付；sourcing warning（F2 派生词缺来源 / F4 §N 引用不存在）须人工复核后消除或说明。
