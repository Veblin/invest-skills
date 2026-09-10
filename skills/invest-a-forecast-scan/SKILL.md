---

name: invest-a-forecast-scan
version: "0.2.9"
description: "业绩预告雷达 — tushare forecast 全市场业绩预告扫描（预增≥阈值/扭亏/首亏负面清单），研究信号非决策。触发词：业绩预告/预增扫描/预告雷达"
whenToUse: "业绩预告窗口期（1/4/7/10 月）或用户询问'最近哪些公司预告大增/扭亏/暴雷'"
argument-hint: "/invest-a-forecast-scan [--days 10] [--min-gain 30]"
allowed-tools: Bash, Read, Write
user-invocable: true
metadata:
  requires:
    bins: [uv, python3]
  optionalEnv:
    - TUSHARE_TOKEN
---

# invest-a-forecast-scan 业绩预告雷达

> **工具约束说明**：frontmatter 的 `allowed-tools` 是 Claude Code 约定；在 DSH 等不读取该字段的 harness 下不生效，实际可用工具由平台自身沙箱控制。本技能全部操作为本地数据采集与计算（Bash/Python 引擎），无外部检索。

## 概述

扫描最近 N 个自然日内**全市场新披露的业绩预告**（tushare `forecast`，2000 分），输出三类清单：

1. **预增 ≥ 阈值**（默认增幅上限 ≥30%，按增幅上限降序）——候选研究池
2. **扭亏**（按净利上限降序）——基本面反转信号
3. **负面关注**（首亏/预减 Top30，按降幅绝对值）——风险雷达

**口径边界（重要）**：本工具展示的是「公司自披露预告相对上年同期的增减幅」；无一致预期数据（tushare `report_rc` 需 10000 分，本项目不足），**不做「超预期/低于预期」断言**。预告数字未经审计，与最终财报可能有出入。

## 运行

```bash
cd "${INVEST_SKILLS_ROOT:-.}" && uv run python skills/invest-a-forecast-scan/scripts/forecast_scan.py
```

| 参数 | 默认 | 说明 |
|------|------|------|
| `--days` | 10 | 扫描窗口（自然日） |
| `--ann-date YYYYMMDD` | — | 指定单日补扫（如窗口期漏跑） |
| `--min-gain` | 30 | 预增过滤：增幅上限 ≥ 此值 |
| `--no-out` | false | 不落盘，仅打印 |
| `--out-dir` | `reports/forecast-scan/` | 输出目录 |

落盘：`reports/forecast-scan/{最早披露日}-{最晚披露日}.md`（单日则单日期；**每次运行默认必须落盘**，`--no-out` 仅限调试——对话输出须附报告路径）

## 数据源说明

- **接口**：tushare `forecast`（业绩预告）。⚠️ 实测（2026-09-08）：`ann_date` 仅接受**单日**（范围形式静默返回 0/校验失败），全市场扫描 = 窗口内逐日调用——预告窗口期每日一次，配额友好
- **type 枚举**：预增/略增/扭亏/续盈/减亏（正面）；预减/首亏/续亏/略减/增亏（负面）
- **单位**：`net_profit_*` 万元；`p_change_*` 百分比
- 明细字段：`summary`（公司自述原因，值得逐条阅读——区分一次性损益与主业驱动）

## 分析纪律

1. **数字纪律（P0）**：统计/排序/占比全部由 `analyze()`（pandas）完成；报告引用引擎输出，禁止目视计数与心算
2. **预增 ≠ 买入信号**：增幅高可能来自低基数（上年亏损/微利）或一次性收益——`summary` 列是区分关键；结合 `change_reason` 逐条读
3. **负面清单同权重要**：首亏/预减是持有池的排雷输入（配合 journal 卖出评估）
4. **报告期注意**：`end_date` 指明预告所属报告期；窗口期（1/4/7/10 月）才有密集披露，淡季运行会输出「窗口内无新披露」（退出码 3，属正常）
5. **退出码语义**：0 正常；1 = 部分日期取数失败（报告已落盘、头部含「数据缺口」警示，结论按缺口折减）；2 = 数据源/权限异常无法鉴别（不落盘；含**窗口零披露但存在失败日**——不得冒充已核验淡季）；3 = 真实淡季空窗（全部日期成功且确无披露）。forecast 权限被拒（40203/配额）显式报 2
6. **与下游衔接**：候选池 → invest-a-stock 深研；持有池雷情 → journal；市场广度背景 → invest-a-pulse

## 参考输出层

| 参考类型 | 内容 | 来源 |
|------|------|------|
| 候选研究池 | 预增 ≥ 阈值清单（增幅区间/净利区间/自述原因） | [来源: forecast 引擎输出] |
| 反转观察 | 扭亏清单 | [来源: forecast 引擎输出] |
| 风险雷达 | 首亏/预减 Top30 | [来源: forecast 引擎输出] |

> 只描述客观披露事实，不含任何动作建议；执行由你依据自身纪律决定。

> **机器层准出（报告类产出必跑，非可选）**：`uv run python skills/lib/report_qc.py <产出文件> --fail-on error` → 无 error 级发现（退出码 0/1）方可交付；sourcing warning（F2 派生词缺来源 / F4 §N 引用不存在）须人工复核后消除或说明。
