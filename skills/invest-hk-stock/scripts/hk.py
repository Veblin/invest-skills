#!/usr/bin/env python3
"""invest-hk-stock CLI — 港股数据引入与初步分析（v0.2.9 v1；完整九模块功能归 0.3.0）。

子命令：
  diagnose               数据源连通性（腾讯 r_hk / 腾讯 K 线 / 东财财务 / 百度估值）
  snapshot SYMBOL        实时快照（腾讯 r_hk 字段，2026-09-06 实测定稿）
  report SYMBOL          初步分析报告（快照/估值分位/财务摘要/技术结构/港股风险层）

数据源：腾讯 qt.gtimg.cn（r_hk 快照 + ifzq fqkline K 线）｜东财 datacenter
（财务指标，需直连上下文）｜百度股市通（估值历史序列，末值滞后注记）。
币种纪律：一切价格/财务均为 HKD（报表用 CNY 的公司显式标注）。

运行：cd code && uv run python skills/invest-hk-stock/scripts/hk.py <子命令> <代码>
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

_THIS_DIR = Path(__file__).resolve().parent
_LIB = _THIS_DIR / "lib"
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

from _invest_path import ensure_invest_a_scripts_on_path, ensure_skills_lib_on_path  # noqa: E402

ensure_skills_lib_on_path()
ensure_invest_a_scripts_on_path()

import hk_ah  # noqa: E402
import hk_codes  # noqa: E402
import hk_compare  # noqa: E402
import hk_financials  # noqa: E402
import hk_kline  # noqa: E402
import hk_quote  # noqa: E402
import hk_southbound  # noqa: E402
import hk_tushare  # noqa: E402
import hk_valuation  # noqa: E402
import hk_yfinance  # noqa: E402


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _snapshot_row(sym: str) -> dict:
    """腾讯 r_hk 快照；失败 → 返回含 error 的 dict（LAW 5 三态由渲染层处理）。"""
    try:
        q = hk_quote.fetch_quote(sym)
    except Exception as exc:  # 网络/解析失败——显式降级标注
        return {"error": f"腾讯快照不可得: {type(exc).__name__}"}
    return q


def _today() -> str:
    """交易日（上海历——港股同 UTC+8，review2 HK-6：本地钟 UTC+8 以西 00:00-08:00 差一天）。
    输出 ISO YYYY-MM-DD（shared shanghai_today 产出紧凑 YYYYMMDD，经 yyyymmdd_to_iso 转换）。"""
    from dates import shanghai_today, yyyymmdd_to_iso
    return yyyymmdd_to_iso(shanghai_today())


def _now_shanghai() -> str:
    """报告文件时间戳（北京时间，invest.py:787 F2-4 口径：路径时间戳统一北京时间）。"""
    from dates import shanghai_now
    return shanghai_now().strftime("%Y-%m-%d-%H-%M-%S")


# ---------------------------------------------------------------------------
# cmd_diagnose
# ---------------------------------------------------------------------------

def cmd_diagnose(args: argparse.Namespace) -> int:
    sym = args.symbol or "00700"
    print(f"# invest-hk-stock diagnose — 数据源连通性（标的 {sym}）\n")
    ok = True

    try:
        q = hk_quote.fetch_quote(sym)
        print(f"✅ 腾讯 r_hk       {q.get('name')} 现价 {q.get('price')} HKD PE(TTM) {q.get('pe_ttm')}")
    except Exception as exc:
        ok = False
        print(f"❌ 腾讯 r_hk       {type(exc).__name__}: {exc}")

    try:
        k = hk_kline.fetch_kline(sym, days=30)
        n = len(k.get("data", []))
        print(f"✅ 腾讯 fqkline    {n} 行 K 线（qfq）" if n else "⚠️ 腾讯 fqkline    返回空")
        ok = ok and n > 0
    except Exception as exc:
        ok = False
        print(f"❌ 腾讯 fqkline    {type(exc).__name__}: {exc}")

    try:
        from lib.proxy import akshare_direct_session
        with akshare_direct_session():
            rows = hk_financials.fetch_financials(sym)
        print(f"✅ 东财财务        {len(rows)} 期（最新 {rows[0]['report_date'] if rows else '—'}）")
        ok = ok and bool(rows)
    except Exception as exc:
        ok = False
        print(f"❌ 东财财务        {type(exc).__name__}: {exc}")

    try:
        s = hk_valuation.fetch_valuation_series(sym, period="近一年")
        print(f"✅ 百度估值序列    {len(s)} 行（截至 {s[-1]['date'] if s else '—'}）")
        ok = ok and bool(s)
    except Exception as exc:
        ok = False
        print(f"❌ 百度估值序列    {type(exc).__name__}: {exc}")

    b = hk_tushare.fetch_basic(sym)
    if b:
        print(f"✅ tushare hk_basic {b.get('name') or '不可得'}（上市 {b.get('list_date') or '—'}，币种 {b.get('currency') or '—'}）")
    else:
        # review #5：fetch_basic 内部吞异常永不 raise——空返回即失败，须计故障
        # （HK-5 分级结论注释 129-132 将 hk_basic 列为关键源）
        ok = False
        print("⚠️ tushare hk_basic 空返回——fetch_basic 内部吞异常，仅能三态标注；关键源缺失，计入故障")
    import datetime as _dt
    _end = _dt.date.today().strftime("%Y%m%d")
    _start = (_dt.date.today() - _dt.timedelta(days=45)).strftime("%Y%m%d")
    try:
        d = hk_tushare.fetch_daily_kline(sym, start_date=_start, end_date=_end)
        note = f"（最新 {d[0]['trade_date']}" if d else "（空返回——2026-09-07 原始 HTTP 实锤 code 40203 硬限频 1 次/分钟：60s 内已调过则必空，非权限/数据问题；作交叉源以非空为准"
        print(f"{'✅' if d else '⚠️'} tushare hk_daily {len(d)} 行 {note}）")
    except Exception as exc:
        ok = False
        print(f"❌ tushare hk_daily {type(exc).__name__}: {exc}")

    y = hk_yfinance.fetch_info(sym)
    if y.get("price"):
        print(f"✅ yfinance        {y.get('name')} 价 {y.get('price')} {y.get('currency')} "
              f"PB {y.get('pb')} 股息率 {y.get('div_yield_pct')}%")
    else:
        ok = False
        print("❌ yfinance        不可得（境外源需代理可达）")

    # review2 HK-5：分级结论（硬故障 vs 已知降级不混为一谈）——关键源（腾讯快照/K线、
    # 东财财务、百度序列、yfinance、hk_basic）任一硬失败才算故障；
    # hk_daily 间歇性空返回为已知降级（⚠️ 已单独标注），不计入失败
    print(f"\n结论: {'全部连通 ✅' if ok else '存在硬故障 ❌（详见上表；⚠️ 行属已知降级不判死）'}")
    print("\n注：东财 push2his 域（stock_hk_hist/spot_em）在当前网络环境不可达，v1 不依赖。")
    return 0 if ok else 1


# ---------------------------------------------------------------------------
# cmd_snapshot
# ---------------------------------------------------------------------------

def cmd_snapshot(args: argparse.Namespace) -> int:
    code = hk_codes.parse_hk_symbol(args.symbol)
    q = _snapshot_row(code)
    # review2 HK-4：腾讯对死代码/停牌标的返回 v_pv_none_match 或缺字段——
    # parse 后关键键为 None，LAW 5 三态必须在此生效，禁止 f"{None:+.2f}" 裸崩
    if "error" in q or q.get("price") is None:
        print(f"⚠️ {code} 快照不可得（{q.get('error', '字段缺失——可能标的已退市/停牌/代码无效')}）"
              "（LAW 5：未获取到任何有效数据，无法判断）")
        return 1
    print(f"# {q.get('name') or code} ({code}) — {str(q.get('ts') or '')[:10]} 港股快照（币种 HKD）")
    print(f"- 现价 {q['price']}（昨收 {_fmt_num(q.get('prev_close'))}，{_pct(q.get('chg_pct'))}）")
    print(f"- 区间 今 {_fmt_num(q.get('low'))}~{_fmt_num(q.get('high'))} | "
          f"52 周 {_fmt_num(q.get('low_52w'))}~{_fmt_num(q.get('high_52w'))}")
    if q.get("amount") is not None:
        print(f"- 成交额 {q['amount'] / 1e8:.1f} 亿 HKD（量 {_fmt_num(q.get('volume'), 0)} 股）")
    print(f"- PE(TTM) {_fmt_num(q.get('pe_ttm'))} | 总市值 {_fmt_num(q.get('mcap_hkd_yi'), 0)} 亿 HKD")
    print(f"[来源: tencent.r_hk qt.gtimg.cn/q=r_hk{code} / {q.get('ts')}]")
    y = hk_yfinance.fetch_info(code)
    if y.get("pb") is not None or y.get("div_yield_pct") is not None:
        print(f"- PB {y.get('pb')} | 股息率 {y.get('div_yield_pct')}%（yfinance 口径，税后见报告注）"
              f" [来源: yfinance {code}.HK info]")
        if y.get("pe_ttm") is not None and q.get("pe_ttm") is not None:
            y_pe, q_pe = float(y["pe_ttm"]), float(q["pe_ttm"])
            if y_pe > 0 and q_pe > 0:
                diff = (y_pe / q_pe - 1) * 100
                print(f"- PE 交叉：yfinance {y_pe:.2f} vs 腾讯 {q_pe:.2f}"
                      f"（差 {diff:+.1f}%——口径差异，不裁决）")
            else:
                # review #8b：亏损期负 PE 相除得误导性符号差——只并列不裁决
                print(f"- PE 交叉：yfinance {y_pe:.2f} vs 腾讯 {q_pe:.2f}"
                      f"（含非正 PE（亏损期），口径差异，不裁决）")
        elif y.get("pe_ttm") is not None:
            print(f"- PE 交叉：yfinance {y['pe_ttm']:.2f}（腾讯不可得）")
    return 0


# ---------------------------------------------------------------------------
# cmd_report（初步分析 v1）
# ---------------------------------------------------------------------------

# (模块号, 名称, 状态, 依据, 落地锚点)  状态 ∈ {已覆盖, 部分, 声明未接入}
# 判定口径（r3 §3 T11-3(a)）：「可映射」= **不需要新建数据源即可交付**的维度。
# anchor = 报告里对应的真实节标题（「无静默缺节」的机器可核验锚点）；None = 无引擎节。
_MODULE_COVERAGE: tuple[tuple[str, str, str, str, str | None], ...] = (
    ("0", "研究问题卡", "已覆盖",
     "Claude 侧流程（LAW 11）：研究问题在会话内明确，非引擎产出", None),
    ("1", "当前状态快照", "已覆盖",
     "快照 + 估值位置 + 财务摘要 + 技术结构（本报告各节）", "## 估值位置"),
    ("2", "动态驱动分析", "声明未接入",
     "HK 无新闻/公告采集层（v1 起即无），本期不新建数据源", None),
    ("3", "市场结构分析", "已覆盖",
     "南向资金 + 恒指/成交额对照；ERP 类不可得走三态", "## 模块 3 市场结构"),
    ("3b", "机构观点与盈利预测", "声明未接入",
     "HK 一致预期源未接入（report_rc 门槛 10000 分且为 A 股口径）", None),
    ("3c", "参与者行为扫描", "声明未接入",
     "CCASS 持仓/沽空源未接入（公开可查，本期未做）", None),
    ("4", "静态基本面", "部分",
     "财务摘要近 4 期；HK 无季报制度 → 期间口径不同（年报 + 中报）", None),
    ("5", "市场分歧", "已覆盖",
     "Bull/Bear 假设 → 传导 → 条件句（纯框架，无新数据源）", "## 模块 5 市场分歧"),
    ("6", "左侧/右侧概率结构", "已覆盖",
     "LAW 16 条件概率结构（纯框架，不作方向断言）", "## 模块 6 左侧/右侧概率结构"),
    ("7", "风险与不确定性", "已覆盖",
     "市场规则静态条目 + 数据驱动项三态", "## 模块 7 风险与不确定性"),
    ("8", "附录", "已覆盖",
     "数据来源与口径清单", "## 模块 8 附录"),
)
MAPPABLE_MODULES = ("0", "1", "3", "5", "6", "7", "8")


def coverage_summary() -> dict:
    """模块覆盖率（分母 = 可映射维度 7 项）。

    ``ratio`` 由 Python 计算（P0）——报告渲染该字段而非口头断言。
    """
    items = [{"module": m, "name": n, "status": s, "basis": b, "anchor": a}
             for m, n, s, b, a in _MODULE_COVERAGE]
    mappable = [i for i in items if i["module"] in MAPPABLE_MODULES]
    covered = [i for i in mappable if i["status"] == "已覆盖"]
    return {"mappable": len(mappable), "covered": len(covered),
            "ratio": (len(covered) / len(mappable)) if mappable else 0.0,
            "items": items}


_MODULE5 = """
## 模块 5 市场分歧（Bull/Bear 假设与传导）

> **框架性陈述**：本节是结构模板，具体分支由使用者按标的填入；未填部分一律标注
> 「框架性陈述/待验证」。HK 无一致预期采集层（见模块覆盖声明 3b），故本节不含卖方分歧数据。

逐分支填写要求（每条须齐备，缺项即标「待填」而非留空）：

| 项 | 要求 |
|---|---|
| 假设 | 一句话可证伪的命题（禁止「长期看好」类不可证伪表述） |
| 传导路径 | 假设 → 经营/资金 → 价格的逐环节链条；**逐环节标注证据等级（A/B/C/D），最弱环节不得作核心论证** |
| 证伪观察 | 条件句：「若 {观察} 出现 → 该分支被削弱」 |
| 状态 | 默认「框架性陈述/待验证」；被数据支持后方可升格并注明样本 |
"""

_MODULE6 = """
## 模块 6 左侧/右侧概率结构（LAW 16）

> 本节只给**条件概率结构**，**不作「当前处于左侧/右侧」的单边结论**（LAW 16）。
> 未经回测的条件不给概率值——「未回测」不得写成「概率低」。

条件化结构（每条条件满足后才有数值，数值须由 Python 回测产出并带样本量与窗口）：

- **价格条件**：{价格相对本报告估值位置/技术结构的观察}
- **资金条件**：可用本报告模块 3 的南向净买额（含方向与量级）
- **估值条件**：可用本报告估值位置的序列分位（须伴随中位数）

填写纪律：每条条件 → 对应的历史条件概率必须来自 Python 计算并标注
`[来源: Python calc: …]`；无回测的条件标注「未回测 → 不给概率」。
"""


_MODULE8 = """
## 模块 8 附录：数据来源与口径

| 项 | 来源 | 时点 / 口径 |
|---|---|---|
| 快照与多源交叉 | 腾讯 r_hk（qt.gtimg.cn）+ yfinance 交叉 | 抓取时点 {ts} |
| 财务摘要 | 东财港股财务接口 | 近 4 期（HK 无季报制度，期间口径见模块覆盖声明 4） |
| 估值序列 | 百度股市通估值历史序列 | 近五年；分位窗口 = 序列可得区间 |
| 技术结构 | 腾讯 ifzq qfq K 线（前复权） | 250 个交易日 |
| 南向资金 | {sb_source} | 最新数据日 {sb_date} |

币种纪律：行情与估值均为 **HKD**（另注除外）；报表币种以公司年报披露为准
（东财 `CURRENCY` 字段对 A+H 公司不可靠，本报告不使用该字段）。
"""


def _render_module7(lines: list[str], *, quote: dict, pe_line: str | None,
                    sb: dict) -> None:
    """模块 7：静态市场规则条目 + 数据驱动项三态。"""
    lines.append(_RSK)
    lines.append("### 7.2 数据驱动项（本次实测；不可得一律三态标注）\n")
    amount = quote.get("amount")
    if amount is not None:
        lines.append(f"- 成交额 {amount / 1e8:.1f} 亿 HKD（快照口径）"
                     f"——≥1 亿 HKD 为实务关注线 [来源: tencent.r_hk]")
    else:
        lines.append("- 成交额：不可得（快照缺失该字段）——不推断流动性高低")
    lines.append(f"- 估值位置：{pe_line or '序列不可得——不推断贵贱'}")
    if sb.get("available"):
        rows = sb.get("rows") or []
        latest = rows[-1] if rows else {}
        lines.append(f"- 南向资金：可得（最新 {latest.get('date')}，"
                     f"合计 {_fmt_num(latest.get('total_yi'))} 亿）"
                     f"——资金面观察，非交易信号")
    else:
        lines.append(f"- 南向资金：不可得（{sb.get('reason')}）——不推断资金方向")
    lines.append("")


def _render_coverage(lines: list[str]) -> None:
    """模块覆盖声明（正文最前，「无静默缺节」一目可核）。"""
    cs = coverage_summary()
    lines.append("## 模块覆盖声明（v2）\n")
    lines.append("> 分母口径：**可映射维度** = 不需要新建数据源即可交付的维度。"
                 "标「已覆盖」者均在本报告内有对应节；「声明未接入」者给原因，不静默缺节。\n")
    lines.append("| 模块 | 名称 | 状态 | 依据 |")
    lines.append("|---|---|---|---|")
    icon = {"已覆盖": "✅ 已覆盖", "部分": "◐ 部分", "声明未接入": "⚠️ 声明未接入"}
    for it in cs["items"]:
        lines.append(f"| {it['module']} | {it['name']} | {icon[it['status']]} | {it['basis']} |")
    lines.append("")
    lines.append(f"**覆盖率：{cs['covered']}/{cs['mappable']} 可映射维度"
                 f"（{cs['ratio'] * 100:.1f}%）** "
                 f"[来源: Python calc: covered / mappable]；"
                 f"部分覆盖 1 项（模块 4），声明未接入 3 项（2 / 3b / 3c）。\n")


_RSK = """
## 模块 7 风险与不确定性

### 7.1 市场规则差异（静态条目）
- 无涨跌停：单日波动无上限（唯一机制 VCM：大型股 ±10%/中型 ±15%/小型 ±20% 触发 5 分钟冷静期）
- 停牌风险：主板连续停牌 18 个月触发强制除牌（GEM 12 个月）
- 流动性：港股交投分化极大——日均成交额 ≥1 亿 HKD 为实务关注线（见快照成交额）
- 汇率：HKD 钉 USD（7.75–7.85）；人民币视角存在 USD/CNY 敞口
- 股息税：港股通 20%（红筹最高 28%）——股息率须标注税后口径
- 无业绩预告/季报制度：披露节奏 = 年报（年结后 3 个月内）+ 中报
"""


def _render_southbound(lines: list[str]) -> dict:
    """南向资金子节（HK-3 / T11-1）→ 模块 3 市场结构；返回 payload 供模块 7 复用。

    三态：``available=False`` → 显式「不可得」+ 原因，**不出中位数/0 值**
    （0 会被读成「南向零净买入」这一事实断言，LAW 5）。
    """
    sb = hk_southbound.fetch_southbound(20)
    lines.append("## 模块 3 市场结构 — 南向资金（港股通沪/深）\n")
    if not sb.get("available"):
        lines.append(f"⚠️ 南向资金不可得（{sb.get('reason')}）——"
                     "LAW 5：未获取到任何有效数据即无法判断，**不得**读作「南向无净买入」。\n")
        return sb

    lines.append("| 日期 | 港股通(沪) 净买额(亿) | 港股通(深) 净买额(亿) | 合计(亿) | 恒生指数 | 涨跌幅 |")
    lines.append("|---|---|---|---|---|---|")
    for r in sb["rows"]:
        lines.append(
            f"| {r['date']} | {_fmt_num(r.get('sh_yi'))} | {_fmt_num(r.get('sz_yi'))} | "
            f"{_fmt_num(r.get('total_yi'))} | {_fmt_num(r.get('hsi'))} | {_pct(r.get('hsi_chg_pct'))} |"
        )
    lines.append(f"- 合计 = 港股通(沪) + 港股通(深) [来源: Python calc: sh_yi + sz_yi]；"
                 f"缺失单元格为「—」表示该侧不可得（不填 0）")
    lines.append(f"[来源: {sb['source']} / 最新 {sb['rows'][-1]['date']}]")
    if sb.get("caliber_note"):
        lines.append(f"> {sb['caliber_note']}")

    latest = sb["rows"][-1]
    if latest.get("buy_yi") is not None and latest.get("sell_yi") is not None:
        lines.append(f"- 最新交易日成交额（沪+深合计）：买入 {_fmt_num(latest['buy_yi'])} 亿 / "
                     f"卖出 {_fmt_num(latest['sell_yi'])} 亿 "
                     f"[来源: Python calc: 沪向 + 深向，源列 买入成交额/卖出成交额]")
    sm = sb.get("summary") or {}
    if sm.get("available") and sm.get("sh"):
        sh, sz = sm.get("sh") or {}, sm.get("sz") or {}
        same = (sh.get("up"), sh.get("down")) == (sz.get("up"), sz.get("down"))
        # 实测：源对沪/深两行返回**相同**的涨跌家数 → 是港股市场整体口径而非分通道，
        # 分开渲染会暗示不存在的分通道粒度（D4：聚合数据必须标注覆盖范围）
        scope = "港股市场整体（源对沪/深两行返回相同计数）" if same else "分通道"
        body_txt = (f"涨 {_fmt_num(sh.get('up'), 0)} / 平 {_fmt_num(sh.get('flat'), 0)} / "
                    f"跌 {_fmt_num(sh.get('down'), 0)}")
        if not same:
            body_txt = (f"沪通道 涨 {_fmt_num(sh.get('up'), 0)}/跌 {_fmt_num(sh.get('down'), 0)}；"
                        f"深通道 涨 {_fmt_num(sz.get('up'), 0)}/跌 {_fmt_num(sz.get('down'), 0)}")
        lines.append(f"- 当日涨跌家数（{scope}）：{body_txt} "
                     f"[来源: {sm.get('source')} / {sm.get('date')}]")
    cross = sb.get("cross") or {}
    if cross.get("comparable"):
        verdict = "一致" if cross.get("consistent") else "不一致（口径差异，不裁决）"
        lines.append(f"- 双源交叉（tushare 累计口径差分，同日）：{verdict}"
                     f"（差 {cross['delta_yi']:+.3f} 亿）[来源: Python calc: akshare 合计 − tushare 差分]")
    elif cross:
        lines.append(f"- 双源交叉：{cross.get('note')}")
    for w in sb.get("warnings") or []:
        lines.append(f"- ⚠️ {w}")
    lines.append("")
    return sb


def cmd_report(args: argparse.Namespace) -> int:
    from lib.technical import compute as tech_compute

    code = hk_codes.parse_hk_symbol(args.symbol)
    lines: list[str] = []

    # --- 快照与多源一致性 ---
    q = _snapshot_row(code)
    if "error" in q or q.get("price") is None:
        # review2 HK-4：关键字段 None（死代码/停牌/字段缺失）→ 三态文件落盘，不裸崩
        lines.append(f"# {code} 港股初步分析 — 快照不可得\n")
        lines.append(f"⚠️ {q.get('error') or '快照字段缺失（停牌/代码无效/死代码）'}（LAW 5：未获取到任何有效数据，无法判断）")
        _write_report(args, code, "数据不可得", "\n".join(lines))
        return 1
    lines.append(f"# {q.get('name') or code} ({code}) — {_today()} 港股初步分析（交易币种 HKD）\n")
    lines.append(
        f"**现价 {q['price']}（{_pct(q.get('chg_pct'))}），52 周 {_fmt_num(q.get('low_52w'))}"
        f"~{_fmt_num(q.get('high_52w'))}，PE(TTM) {_fmt_num(q.get('pe_ttm'))}，"
        f"总市值 {_fmt_num(q.get('mcap_hkd_yi'), 0)} 亿 HKD**"
    )
    lines.append(f"[来源: tencent.r_hk / {q.get('ts')}]")
    b = hk_tushare.fetch_basic(code)
    if b.get("list_date"):
        # review #8c：market 取自 tushare（主板/GEM），不硬编码
        lines.append(f"[来源: tushare.hk_basic] 上市 {b['list_date']}（{b.get('market') or '—'}/交易币种 {b.get('currency') or '—'}；"
                     f"报表货币以年报披露为准）\n")
    else:
        lines.append("")
    y = hk_yfinance.fetch_info(code)
    if y.get("price") and abs((y["price"] - q["price"]) / q["price"]) > 0.01:
        lines.append(
            f"> 🟡 多源交叉：yfinance 现价 {y['price']} vs 腾讯 {q['price']}"
            f"（差 {abs((y['price'] - q['price']) / q['price']) * 100:.1f}%——快照时点差，不裁决）\n"
        )

    # --- 模块覆盖声明（正文最前：无静默缺节一目可核） ---
    _render_coverage(lines)

    # --- 财务摘要（东财，直连上下文） ---
    fin_rows: list[dict] = []
    try:
        from lib.proxy import akshare_direct_session
        with akshare_direct_session():
            fin_rows = hk_financials.fetch_financials(code)
    except Exception:
        fin_rows = []
    if fin_rows:
        lines.append("## 财务摘要（数值为东财港股财务接口原值）\n")
        lines.append("| 报告期 | 营收(亿) | 同比 | 归母净利(亿) | 同比 | 毛利率 | 净利率 | ROE | EPS |")
        lines.append("|---|---|---|---|---|---|---|---|---|")
        for i, r in enumerate(fin_rows[:4]):
            # YoY 自算（不信任东财 *_YOY 列——实测含异常占位值如 -99.4%）[来源: Python calc]
            rev_yoy = _self_yoy(fin_rows, i, "revenue")
            np_yoy = _self_yoy(fin_rows, i, "net_profit")
            lines.append(
                f"| {r['report_date']} | {_yi(r.get('revenue'))} | {_pct(rev_yoy)} | "
                f"{_yi(r.get('net_profit'))} | {_pct(np_yoy)} | "
                f"{_pct(r.get('gross_margin'))} | {_pct(r.get('net_margin'))} | "
                f"{_pct(r.get('roe'))} | {_fmt_num(r.get('eps'), 2)} |"
            )
        lines.append(f"\n*同比为引擎自算（本期/上年同期−1，[来源: Python calc]），非东财 YOY 列。*\n")
        lines.append(
            "> ⚠️ 币种核对：东财 CURRENCY 字段对部分 A+H 公司不可靠（比亚迪 H 实测："
            "数值为 CNY 报表值但字段标 HKD）。跨币种换算/对比前须以公司年报披露币种为准。\n"
        )
    else:
        lines.append("## 财务摘要\n数据不可得（东财财务接口不可达或标的无财务数据）——三态标注。\n")

    # --- 估值位置（百度序列 + 现价 PE/PB 交叉：yfinance 提供当前 PB 与股息率） ---
    lines.append("## 估值位置（分位窗口=序列可得区间；港股口径注记）\n")
    # review #7：y 复用上文多源交叉已获取的 fetch_info——不二次 yf.Ticker .info（代理网络往返）
    pb_cur = y.get("pb")
    pe_line: str | None = None
    try:
        pe_s = hk_valuation.fetch_valuation_series(code, "市盈率(TTM)", "近五年")
        pe_pos = hk_valuation.percentile_position(pe_s, q.get("pe_ttm"))
        pb_s = hk_valuation.fetch_valuation_series(code, "市净率", "近五年")
        pb_pos = hk_valuation.percentile_position(pb_s, pb_cur)
        if pe_pos["median"] is not None:
            # review #8a：亏损标的腾讯 PE 为空（''/'-'）→ parse 得 None——渲染 — 而非字面 None
            pe_str = "—" if q.get("pe_ttm") is None else str(q.get("pe_ttm"))
            lines.append(
                f"- **PE(TTM) {pe_str}，序列分位 {pe_pos['pct']:.1f}%（中位 {pe_pos['median']:.1f}）**"
                if pe_pos["pct"] is not None else
                f"- PE(TTM) {pe_str}（序列 {pe_pos['n']} 日，中位 {pe_pos['median']:.1f}；当前值口径与序列末值有差）"
            )
            pe_line = (f"PE 序列分位 {pe_pos['pct']:.1f}%（中位 {pe_pos['median']:.1f}，"
                       f"{pe_pos['n']} 日）[来源: hk_valuation.percentile_position]"
                       if pe_pos["pct"] is not None else
                       f"PE 序列中位 {pe_pos['median']:.1f}（当前值口径与序列末值有差）"
                       f" [来源: hk_valuation.percentile_position]")
        if pb_pos["median"] is not None and pb_cur is not None:
            lines.append(
                f"- **PB {pb_cur:.2f}（yfinance 当前值），序列分位 {pb_pos['pct']:.1f}%"
                f"（中位 {pb_pos['median']:.2f}）**"
                if pb_pos["pct"] is not None else
                f"- PB {pb_cur:.2f} vs 序列中位 {pb_pos['median']:.2f}"
            )
        elif pb_pos["median"] is not None:
            lines.append(f"- PB 序列中位 {pb_pos['median']:.2f}（{pb_pos['n']} 日；当前 PB 不可得）")
        if y.get("div_yield_pct") is not None:
            lines.append(
                f"- 股息率 {y['div_yield_pct']:.2f}%（名义；港股通税后约 ×0.8）"
                f" [来源: yfinance {code}.HK]"
            )
        if pe_pos.get("note"):
            lines.append(f"- ⚠️ {pe_pos['note']}")
    except Exception as exc:
        lines.append(f"- 估值序列不可得: {type(exc).__name__}\n")
    lines.append("")

    # --- 技术结构（technical.compute 复用） ---
    try:
        k = hk_kline.fetch_kline(code, days=250)
        rows = k.get("data", [])
        if len(rows) >= 60:
            t = tech_compute(rows)
            ma = t["trend"]["ma"]
            def _ma_v(p):
                seq = ma.get(str(p), [])
                return seq[-1] if seq else None
            latest = t.get("latest_close")
            ma_vals = {p: _ma_v(p) for p in (5, 20, 60)}
            rel = " / ".join(f"MA{p}={v:.1f}" for p, v in ma_vals.items() if v)
            # 实测键位：MACD 在 momentum；RSI 在 overbought_oversold.rsi["12"].value
            macd = (t.get("momentum") or {}).get("macd") or {}
            rsi_box = t.get("overbought_oversold") or {}
            rsi12 = (rsi_box.get("rsi") or {}).get("12") or {}
            rsi = rsi12.get("value")
            lines.append("## 技术结构（状态描述，非交易信号）\n")
            lines.append(f"- 现价 {latest} vs {rel}")
            if macd.get("dif") is not None:
                lines.append(f"- MACD DIF {macd['dif']:.2f} / DEA {macd.get('dea')}")
            if rsi is not None:
                lines.append(f"- RSI(12) {rsi:.1f}（{rsi12.get('zone', '')}）")
            lines.append("")
        else:
            lines.append("## 技术结构\nK 线数据不足（<60 行）——标注不可得。\n")
    except Exception as exc:
        lines.append(f"## 技术结构\n计算失败: {type(exc).__name__}\n")

    # --- 模块 3 市场结构：南向资金（T11-1 / HK-3） ---
    sb = _render_southbound(lines)

    # --- 模块 5 / 6 / 7（框架性陈述 + 静态条目 + 数据驱动项） ---
    lines.append(_MODULE5)
    lines.append(_MODULE6)
    _render_module7(lines, quote=q, pe_line=pe_line, sb=sb)

    lines.append("## 待验证项\n")
    lines.append("- 财务口径（HKFRS vs CAS）跨市场对比须折算与准则注记")
    lines.append("- CCASS 持仓/沽空数据：公开可查但未接入（南向资金已接入——见模块 3；源见 data-interface-map A4/B 节）")
    lines.append("- 交易日历：已接入港股日历变体（`hk_calendar.py`，tushare hk_tradecal）；"
                 "节假日以交易日历为准，自然日仅作粗判\n")
    lines.append(_MODULE8.format(
        ts=q.get("ts"), sb_source=(sb.get("source") or "不可得"),
        sb_date=((sb.get("rows") or [{}])[-1].get("date") or "—") if sb.get("available") else "—"))
    lines.append("\n> ⚠️ 本报告由 invest-hk-stock v2 自动生成，覆盖九模块中的 7/7 可映射维度"
                 "（未接入维度见模块覆盖声明），")
    lines.append("> 不构成任何投资建议。数据来源见各行 [来源: ...]；币种 HKD（另注除外）。")

    body = "\n".join(lines)
    name = str(q.get("name") or code)
    path = _write_report(args, code, name, body)
    print(f"📝 报告: {path}\n")
    print(body[:4000])
    return 0


# ---------------------------------------------------------------------------
# cmd_ah（T11-4 / HK-2：A/H 比价）
# ---------------------------------------------------------------------------

def _parse_a_symbol(raw: str) -> str:
    """A 股代码 → 6 位纯数字（接受 `600036` / `600036.SH`）；否则 ValueError。"""
    digits = "".join(ch for ch in str(raw).strip() if ch.isdigit())
    if len(digits) != 6:
        raise ValueError(f"非法 A 股代码（需 6 位，可带 .SH/.SZ 后缀）：{raw!r}")
    return digits


def _a_quote_row(sym: str) -> dict:
    """腾讯 A 股快照 —— 与 H 侧**同 provider**，使两侧快照时点可比。

    失败 → 含 error 的 dict（三态由渲染层处理，与 `_snapshot_row` 同契约）。
    """
    try:
        from quote_tencent import fetch_tencent_quote

        return fetch_tencent_quote(sym) or {"error": "腾讯 A 股快照空返回"}
    except Exception as exc:  # noqa: BLE001 —— 网络/解析失败须显式降级
        return {"error": f"腾讯 A 股快照不可得: {type(exc).__name__}"}


def cmd_ah(args: argparse.Namespace) -> int:
    """A/H 比价（HK-2）：同公司两地价格关系 → 溢价率。

    研究视角参考，**非套利信号**（requirements §3.2）；溢价率**全部由 Python 计算**（P0）。
    三态：任一侧价格或汇率不可得 → 落盘说明并 return 1，**不出**伪造的中性值。
    """
    try:
        a_code = _parse_a_symbol(args.a_symbol)
        hk_code = hk_codes.parse_hk_symbol(args.hk_symbol)   # A 股码在此被拒（既有纪律）
    except hk_codes.HkSymbolError as exc:
        print(f"❌ {exc}", file=sys.stderr)
        return 2
    except ValueError as exc:
        print(f"❌ {exc}", file=sys.stderr)
        return 2

    lines = [f"# A/H 比价 — {a_code}(A) / {hk_code}(H) — {_today()}\n",
             "> 研究视角参考，**非套利信号**；不含买卖建议（LAW 6）。口径见下方三件套。\n"]

    h = _snapshot_row(hk_code)
    a = _a_quote_row(a_code)
    fx = hk_ah.fetch_fx_hkd_cny()
    h_px, a_px = h.get("price"), a.get("price")
    pct = hk_ah.premium_pct(a_px, h_px, fx.get("rate"))

    def _cell(v, fmt):
        return fmt(v) if v is not None else "—（不可得）"

    lines.append("| 项 | 值 | 来源 / 时点 |")
    lines.append("|---|---|---|")
    lines.append(f"| A 价（CNY） | {_cell(a_px, lambda v: f'{v}')} | "
                 f"{'腾讯 qt.gtimg.cn（抓取 ' + _now_shanghai() + ' 北京）' if a_px is not None else a.get('error') or '不可得'} |")
    lines.append(f"| H 价（HKD） | {_cell(h_px, lambda v: f'{v}')} | "
                 f"{'腾讯 r_hk ' + str(h.get('ts') or '') if h_px is not None else h.get('error') or '不可得'} |")
    lines.append(f"| 汇率（CNY/HKD） | {_cell(fx.get('rate'), lambda v: f'{v:.5f}')} | "
                 f"{fx.get('source') or '不可得'}{'（' + str(fx['date']) + '）' if fx.get('date') else ''} |")
    lines.append(f"| **A/H 溢价率** | "
                 f"{'**%+.2f%%**' % pct if pct is not None else '**—（不可得）**'} | "
                 f"{'[来源: Python calc: A价/(H价×汇率)−1]' if pct is not None else '输入不可得，未计算'} |")
    lines.append("")

    lines.append("## 口径三件套（强制显式）\n")
    lines.append("- **币种**：A 价 CNY（交易所本位币）/ H 价 HKD。东财 `CURRENCY` 字段对 A+H 公司"
                 "**不可靠**（比亚迪 H 实测为 CNY 报表值而字段标 HKD），故本表不使用该字段。")
    lines.append(f"- **汇率时点**：{fx.get('date') or '不可得'}，"
                 f"{fx.get('source') or '—'}（汇率为 CNY per HKD）")
    lines.append("- **复权**：两侧均为**行情快照现价（未复权）**，口径一致；"
                 "若改用历史序列对照，须另行对齐复权口径。\n")
    lines.append("> ⚠️ 两地交易时段不同（A 股 09:30–11:30 / 13:00–15:00 北京；"
                 "港股 09:30–12:00 / 13:00–16:00 香港），同一时刻取到的两个价格可能分属"
                 "不同时段或一方已收盘 → 属**快照时点差**，本表不裁决。")
    if fx.get("note"):
        lines.append(f"> ℹ️ 汇率口径：{fx['note']}")
    if pct is None:
        lines.append("> ⚠️ 本次**未得出溢价率**（上表标「不可得」）——"
                     "不得读作「两地平价」（LAW 5：未获取到有效数据即无法判断）。")
    lines.append("\n> 声明：本表为两地价格关系的研究视角记录，不构成投资建议，"
                 "亦不构成任何套利信号。")

    body = "\n".join(lines)
    path = _write_report(args, a_code, f"{hk_code}-AH比价", body)
    print(f"📝 报告: {path}\n")
    print(body)
    return 0 if pct is not None else 1


# ---------------------------------------------------------------------------
# cmd_compare（T11-3 / HK-1 v2：双港股对照）
# ---------------------------------------------------------------------------

_A_SHARE_RE = re.compile(r"^\d{6}(\.(SH|SZ|BJ))?$", re.IGNORECASE)


def _collect_side(code: str) -> dict:
    """单侧取数 → hk_compare 的 payload 形状。

    取数失败逐项记进 ``notes``（不静默吞错，D5）；缺失一律 ``None``（三态），
    由 ``hk_compare`` 渲染成「—」。
    """
    side: dict = {"code": code, "name": code, "snapshot": None,
                  "valuation_pctl": {"pe": None, "pb": None},
                  "financials": {"latest": None}, "technical": {}, "notes": []}
    q = _snapshot_row(code)
    if "error" in q or q.get("price") is None:
        side["notes"].append(f"快照不可得（{q.get('error') or '字段缺失'}）")
    else:
        side["name"] = str(q.get("name") or code)
        side["snapshot"] = {k: q.get(k) for k in
                            ("price", "chg_pct", "pe_ttm", "mcap_hkd_yi", "low_52w", "high_52w")}

    y = hk_yfinance.fetch_info(code)
    pb_cur = y.get("pb")
    if pb_cur is None:
        side["notes"].append("当前 PB 不可得（yfinance 需代理可达）→ PB 序列分位无法计算")
    for indicator, key, cur in (("市盈率(TTM)", "pe", q.get("pe_ttm")), ("市净率", "pb", pb_cur)):
        try:
            series = hk_valuation.fetch_valuation_series(code, indicator, "近五年")
            pos = hk_valuation.percentile_position(series, cur)
            side["valuation_pctl"][key] = {"pct": pos.get("pct"), "median": pos.get("median"),
                                           "n": pos.get("n")}
        except Exception as exc:  # noqa: BLE001 —— 单维降级，不阻断对照
            side["notes"].append(f"{indicator} 序列不可得（{type(exc).__name__}）")

    try:
        from lib.proxy import akshare_direct_session
        with akshare_direct_session():
            rows = hk_financials.fetch_financials(code)
    except Exception as exc:  # noqa: BLE001
        rows = []
        side["notes"].append(f"财务摘要不可得（{type(exc).__name__}）")
    if rows:
        r0 = rows[0]
        # 元 → 亿（P0：派生值带 calc 标签，见 hk_compare._ROW_SPECS）
        side["financials"]["latest"] = {
            "report_date": r0.get("report_date"),
            "revenue_yi": (r0["revenue"] / 1e8) if r0.get("revenue") is not None else None,
            "net_profit_yi": (r0["net_profit"] / 1e8) if r0.get("net_profit") is not None else None,
            "roe": r0.get("roe"),
        }
    else:
        side["notes"].append("财务摘要空返回（HK 无季报制度，或该标的无数据）")

    try:
        k = hk_kline.fetch_kline(code, days=250)
        rows = k.get("data", [])
        if len(rows) >= 60:
            from lib.technical import compute as tech_compute
            t = tech_compute(rows)
            ma = t["trend"]["ma"]

            def _ma_v(p):
                seq = ma.get(str(p), [])
                return seq[-1] if seq else None

            macd = (t.get("momentum") or {}).get("macd") or {}
            rsi12 = (((t.get("overbought_oversold") or {}).get("rsi") or {}).get("12") or {})
            side["technical"] = {"latest_close": t.get("latest_close"),
                                 "ma": {str(p): _ma_v(p) for p in (5, 20, 60)},
                                 "macd": {"dif": macd.get("dif"), "dea": macd.get("dea")},
                                 "rsi": rsi12.get("value")}
        else:
            side["notes"].append("K 线不足 60 行，技术结构不可得")
    except Exception as exc:  # noqa: BLE001
        side["notes"].append(f"技术结构计算失败（{type(exc).__name__}）")
    return side


def cmd_compare(args: argparse.Namespace) -> int:
    """双港股对照 → 落盘 ``reports/{c1}-{c2}-compare/{ts}.md``（与 report 同契约）。

    退出码：2 参数非法（A 股码 → 指路 `ah`；同码；符号非法）；
    1 任一关键维度不可得（**仍落盘**三态，不静默跳过）；0 正常。

    与 invest-a-stock 的 `compare`（print-only）**故意不同**：HK 报告的既有契约是落盘，
    便于留档与复检（勿"统一"成 print-only）。
    """
    raw = (str(args.left).strip(), str(args.right).strip())
    for r in raw:
        if _A_SHARE_RE.match(r):
            print(f"❌ {r} 是 A 股代码；A/H 比价请用："
                  f"uv run python skills/invest-hk-stock/scripts/hk.py ah {r} <港股码>",
                  file=sys.stderr)
            return 2
    try:
        codes = [hk_codes.parse_hk_symbol(r) for r in raw]
    except hk_codes.HkSymbolError as exc:
        print(f"❌ {exc}", file=sys.stderr)
        return 2
    if codes[0] == codes[1]:
        print("❌ 两个标的相同，无法对照", file=sys.stderr)
        return 2

    left, right = _collect_side(codes[0]), _collect_side(codes[1])
    cmp = hk_compare.build_compare(left, right)
    lines = [f"# 双标的对照 — {codes[0]} vs {codes[1]} — {_today()}\n",
             "> 并列展示各维度引擎字段，**不作优劣裁决、不含买卖建议**（LAW 6）。"
             "口径差异（快照时点/报表期间/序列窗口）见各行注记。\n"]
    lines.extend(hk_compare.render_compare_table(cmp))
    lines.append("")
    for label, side in (("左", left), ("右", right)):
        if side["notes"]:
            lines.append(f"- ⚠️ {label}侧（{side['code']}）：" + "；".join(side["notes"]))
    lines.append("")
    lines.append("> ⚠️ 快照为各自市场**抓取时点**价格（港股同一时段，仍存在秒级时点差）；"
                 "财务期间以各自披露节奏为准（HK 无季报 → 期间口径可能与 A 股不同）；"
                 "估值分位窗口 = 百度序列可得区间（近五年）。")
    lines.append("\n> 声明：本表为两标的关键指标并列记录，不构成投资建议，"
                 "也不构成任何相对价值判断。")

    body = "\n".join(lines)
    path = _write_report(args, f"{codes[0]}-{codes[1]}", "compare", body)
    print(f"📝 报告: {path}\n")
    print(body)
    return 1 if (left["snapshot"] is None or right["snapshot"] is None) else 0


def _pct(v):
    if v is None:
        return "—"
    try:
        f = float(v)
    except (TypeError, ValueError):
        return "—"
    return f"{f:+.1f}%"


def _yi(v):
    """元 → 亿单位字符串（财务表显示，避免 15 位长数字）。"""
    if v is None:
        return "—"
    try:
        f = float(v) / 1e8
    except (TypeError, ValueError):
        return "—"
    return f"{f:,.1f}"


def _fmt_num(v, nd=2):
    if v is None:
        return "—"
    try:
        return f"{float(v):,.{nd}f}"
    except (TypeError, ValueError):
        return "—"


def _self_yoy(rows: list[dict], idx: int, key: str) -> float | None:
    """同比自算——**上年同期**（同月日、上一年），非相邻报告期。

    review2 HK-1 修复：fin_rows 降序为 [2026-06-30 中报, 2025-12-31 年报, 2025-06-30
    中报, ...]，idx+1 是"上一报告期"（中报 vs 年报 = 口径错配 −45% 级）；
    上年同期 = 第一个 report_date 同 MM-DD 且更早的行。返回百分数单位（13.86 = +13.9%）。
    """
    cur_date = str(rows[idx].get("report_date") or "")
    if len(cur_date) < 7:
        return None
    mmdd = cur_date[5:]
    prev = None
    for j in range(idx + 1, len(rows)):
        d = str(rows[j].get("report_date") or "")
        if d[5:] == mmdd:                      # 同月日 = 上年同期（中报对中报/年报对年报）
            prev = rows[j].get(key)
            break
    if prev is None:
        return None
    c, p = rows[idx].get(key), prev
    try:
        cf, pf = float(c), float(p)
    except (TypeError, ValueError):
        return None
    if pf == 0:
        return None
    return (cf / pf - 1) * 100


def _write_report(args, code: str, name: str, body: str) -> Path:
    outdir = Path(args.outdir or (Path.cwd() / "reports"))
    sub = outdir / f"{code}-{name}"
    sub.mkdir(parents=True, exist_ok=True)
    path = sub / f"{_now_shanghai()}.md"
    path.write_text(body, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="invest-hk-stock — 港股数据引入与初步分析（v0.2.9 v1）")
    sub = p.add_subparsers(dest="command", required=True)

    pd = sub.add_parser("diagnose", help="数据源连通性诊断")
    pd.add_argument("symbol", nargs="?", default="00700", help="港股代码（默认 00700）")

    ps = sub.add_parser("snapshot", help="实时快照（腾讯 r_hk）")
    ps.add_argument("symbol")

    pr = sub.add_parser("report", help="初步分析报告（快照/估值/财务/技术/港股风险层）")
    pr.add_argument("symbol")
    pr.add_argument("--outdir", default="", help="报告输出目录（默认 code/reports）")

    pa = sub.add_parser("ah", help="A/H 比价（同公司两地价差 → 溢价率；研究视角，非套利信号）")
    pa.add_argument("a_symbol", help="A 股代码（600036 或 600036.SH）")
    pa.add_argument("hk_symbol", help="港股代码（5 位，如 03968）")
    pa.add_argument("--outdir", default="", help="报告输出目录（默认 code/reports）")

    pc = sub.add_parser("compare", help="双港股对照（快照/估值分位/财务/技术；落盘，不裁决）")
    pc.add_argument("left", help="港股代码（5 位，如 00700）")
    pc.add_argument("right", help="港股代码（5 位，如 09988）")
    pc.add_argument("--outdir", default="", help="报告输出目录（默认 code/reports）")
    return p


CMD_DISPATCH = {
    "diagnose": cmd_diagnose,
    "snapshot": cmd_snapshot,
    "report": cmd_report,
    "ah": cmd_ah,
    "compare": cmd_compare,
}


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if args.command not in CMD_DISPATCH:
        print(f"未注册 CMD_DISPATCH 分发表: {args.command}", file=sys.stderr)
        return 1
    return CMD_DISPATCH[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
