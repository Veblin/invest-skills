#!/usr/bin/env python3
"""`--deep` 四视角 Agent 的 Python facts 提取器（v0.3.1，SOP-DEEP P0 修复）。

## 为什么存在

SOP-DEEP 的四个 agent prompt（``references/agent-prompts.md``）原先各自内联一段
``json.load`` 切片，**不区分维度行序**。2026-09-17 实测（300750）确认两类缺陷：

1. **喂错数据**：``financials`` 维度是**降序**（首行 = 最新），而 Agent A 用
   ``data[-1]``、Agent B 用 ``data[-5:]`` → 取到的是 **2022–2023 年**的财报。
   Agent B 的最新期实际落在 2023Q1，而真实最新期是 2026H1——**落后 13 期 / 3.2 年**。
   Agent C/D 用 ``data[:3]`` / ``data[:5]``，方向恰好相反。
2. **要求超出输入**：prompt 要求「近 8 期趋势」「PE 历史分位 + 中位数」「申万行业指数
   20/60 日涨跌」「近 12 月内部人方向」，而内联切片只给 3–5 行、且不含对应维度 →
   LLM 只能自行推算或凭空补全（P0 违规）。

## 定位（不是新引擎）

本模块是**确定性事实提取器**：只做「按行序取数 + 机械聚合（计数/极值/分位/差分/
比值）」，每个数字带 ``[来源: Python calc: formula]``；**不实现任何领域模型**
（DCF/稳态估值等仍归 ``valuation_calc.py``）。取不到的项**显式输出「不可得」**，
绝不留给 Agent 猜。

## 用法

    uv run python skills/invest-a-stock/scripts/agent_facts.py <collection.json> --for business
    uv run python skills/invest-a-stock/scripts/agent_facts.py <collection.json> --for financial --json

role ∈ {business, financial, industry, risk}（对应 Agent A/B/C/D）。
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys

# 聚合所需的最小期数（趋势类）。**写死为常量**，避免 prompt 与实际输出漂移。
TREND_PERIODS = 8

_DATE_KEYS = ("end_date", "trade_date", "date", "ann_date")


# ---------------------------------------------------------------------------
# 行序归一化（本模块存在的第一理由）
# ---------------------------------------------------------------------------

def date_key(row: dict) -> str:
    """行的日期键 → 可比较字符串；无日期键 → ``""``（排最后）。"""
    for k in _DATE_KEYS:
        v = row.get(k)
        if v:
            return str(v)
    return ""


def normalized_period_key(row: dict) -> str:
    """报告期归一化为 ``YYYYMMDD``；不完整/不可识别日期返回空串。"""
    raw = date_key(row)
    digits = "".join(ch for ch in raw if ch.isdigit())
    if len(digits) != 8:
        return ""
    try:
        import datetime as _dt
        _dt.date(int(digits[:4]), int(digits[4:6]), int(digits[6:]))
    except ValueError:
        return ""
    return digits


def prior_year_period(row: dict) -> str | None:
    """给定报告期的精确上年同期；日期无效时明确不可得。"""
    period = normalized_period_key(row)
    if not period:
        return None
    return f"{int(period[:4]) - 1:04d}{period[4:]}"


def rows_asc(rows) -> list[dict]:
    """**无条件按日期升序**返回行副本——不假设源的行序。

    实测行序（2026-09-17，300750）：``financials``/``segments`` 降序，
    ``valuation``/``kline``/``shareholders`` 升序，``events`` 降序。
    **不排序就取 [-1] / [-5:] 是本类缺陷的唯一根因**（development-rules 的
    序列方向假设错误一类，会复发）。
    """
    if not rows:
        return []
    out = [r for r in rows if isinstance(r, dict)]
    out.sort(key=date_key)
    return out


def dedupe_by_date(rows: list[dict]) -> tuple[list[dict], int]:
    """同日期多行（双源合并残留）→ 保留首行。返回 ``(行, 被丢弃数)``。"""
    seen: dict[str, dict] = {}
    for r in rows:
        seen.setdefault(date_key(r), r)
    return list(seen.values()), max(0, len(rows) - len(seen))


def num(v):
    """数值化；None/不可解析 → None（不用 ``or`` 兜底，D1：0 是合法值）。"""
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f else None  # NaN 过滤


def pct(a, b):
    """``(a/b − 1) × 100``；任一缺失或 b == 0 → None。"""
    a, b = num(a), num(b)
    if a is None or b is None or b == 0:
        return None
    return (a / b - 1.0) * 100.0


def diff(a, b):
    """``a − b``（百分点差等）；任一缺失 → None。"""
    a, b = num(a), num(b)
    return None if a is None or b is None else a - b


def percentile_rank(series, value):
    """``value`` 在 ``series`` 中的分位（%）= 小于等于它的样本占比。"""
    xs = [x for x in (num(v) for v in series) if x is not None]
    v = num(value)
    if not xs or v is None:
        return None
    return sum(1 for x in xs if x <= v) / len(xs) * 100.0


# ---------------------------------------------------------------------------
# Fact 容器
# ---------------------------------------------------------------------------

class Facts:
    """收集带来源标签的事实；每条都必须能追到公式或引擎字段。"""

    def __init__(self) -> None:
        self.items: list[dict] = []

    def add(self, label: str, value, *, formula: str | None = None,
            field: str | None = None, unit: str = "") -> None:
        """加一条事实。``formula`` 与 ``field`` 二选一（都缺 → 记 ValueError）。"""
        if not formula and not field:
            raise ValueError(f"fact 缺来源标签（formula/field 均空）: {label}")
        src = f"[来源: Python calc: {formula}]" if formula else f"[来源: {field}]"
        shown = value if value is not None else "不可得"
        self.items.append({
            "id": f"F{len(self.items) + 1}",
            "label": label,
            "value": value,
            "unit": unit,
            "source": src,
            "text": f"- **[{len(self.items) + 1}] {label}**：{shown}{unit} {src}",
        })

    def unavailable(self, label: str, reason: str) -> None:
        """显式「不可得」——不留给 Agent 推测（LAW 5 / §2.3 事实边界）。"""
        self.items.append({
            "id": f"F{len(self.items) + 1}",
            "label": label,
            "value": None,
            "unit": "",
            "source": f"[不可得: {reason}]",
            "text": f"- **[{len(self.items) + 1}] {label}**：不可得（{reason}）",
        })

    def render(self) -> str:
        return "\n".join(it["text"] for it in self.items)


# ---------------------------------------------------------------------------
# 各 role 的 facts
# ---------------------------------------------------------------------------

def _dims(coll: dict) -> dict[str, dict]:
    return {d.get("dimension"): d for d in (coll.get("dimensions") or [])
            if isinstance(d, dict)}


def _data(coll: dict, name: str):
    d = _dims(coll).get(name) or {}
    return d.get("data")


def build_business(coll: dict) -> Facts:
    """Agent A（生意质量）：basic_info + financials 最新期（**升序后取末行**）。"""
    f = Facts()
    bi = _data(coll, "basic_info") or {}
    if isinstance(bi, dict) and bi:
        f.add("公司名称", bi.get("name") or None, field="basic_info.name")
        f.add("所属行业", bi.get("industry") or None, field="basic_info.industry")
        f.add("上市市场", bi.get("market") or None, field="basic_info.market")
        f.add("上市日期", bi.get("list_date") or None, field="basic_info.list_date")
    else:
        f.unavailable("基本信息", "collection 无 basic_info 维度数据")

    fin, _ = dedupe_by_date(rows_asc(_data(coll, "financials") or []))
    if not fin:
        f.unavailable("财务序列", "collection 无 financials 维度数据")
        return f
    latest = fin[-1]
    f.add("最新报告期", date_key(latest), field="financials.end_date（升序后末行）")
    for label, key in (("ROE(%)", "roe"), ("毛利率(%)", "grossprofit_margin"),
                       ("净利率(%)", "netprofit_margin"), ("资产负债率(%)", "debt_to_assets"),
                       ("权益乘数", "equity_multiplier"), ("资产周转率", "assets_turn"),
                       ("营收", "revenue"), ("净利润", "net_profit"),
                       ("经营现金流", "n_cashflow_act"), ("资本开支", "cap_ex")):
        f.add(f"最新期 {label}", num(latest.get(key)),
              field=f"financials.{key}（报告期 {date_key(latest)}）")
    f.add("可用期数", len(fin), formula=f"len(去重后的 financials 行) = {len(fin)}")
    return f


def build_financial(coll: dict) -> Facts:
    """Agent B（财务与估值）：8 期趋势 + 估值全序列分位（分位**必须**全序列算）。"""
    f = Facts()
    fin, dropped = dedupe_by_date(rows_asc(_data(coll, "financials") or []))
    if not fin:
        f.unavailable("财务序列", "collection 无 financials 维度数据")
    else:
        if dropped:
            f.add("财务序列去重丢弃行数", dropped,
                  formula=f"原始行数 - 去重后行数 = {dropped}（双源合并残留）")
        window = fin[-TREND_PERIODS:]
        f.add("财务序列期数（用于趋势）", len(window),
              formula=f"min({TREND_PERIODS}, 可用期数 {len(fin)}) = {len(window)}")
        f.add("趋势窗口报告期", " → ".join(date_key(r) for r in window),
              field="financials.end_date（升序后取末 8 期）")

        # ⚠️ 累计口径警告：窗口若跨越不同报告期类型（Q1/H1/Q3/年报），各行的
        # ROE/现金流等**累计窗口长度不同**（半年累计 vs 全年累计），直接连成
        # 「趋势」是口径错误。必须显式告知 Agent，否则它会读出一个不存在的趋势。
        months = sorted({date_key(r)[4:8] for r in window if len(date_key(r)) >= 8})
        if len(months) > 1:
            f.add("⚠️ 趋势窗口口径警告",
                  f"窗口含 {len(months)} 种报告期类型（{ '、'.join(months) }）——"
                  "各期累计窗口长度不同，**不可直接连成趋势线**；"
                  "同期对比请用下方「同报告期同比」项",
                  field="financials.end_date 月份集合")
        # 同报告期同比：最新期 vs 上年同期（消除累计窗口差异）
        wanted_base_period = prior_year_period(fin[-1])
        base = next((r for r in fin if normalized_period_key(r) == wanted_base_period), None)
        if base is not None:
            f.add("同报告期基准期", date_key(base), field="financials.end_date（上年同期）")
            for label, key in (("营收", "revenue"), ("净利润", "net_profit"),
                               ("毛利率(%)", "grossprofit_margin"), ("ROE(%)", "roe")):
                f.add(f"{label} 同报告期同比(%)", pct(fin[-1].get(key), base.get(key)),
                      formula=f"latest.{key} / base.{key} − 1")
        else:
            expected = wanted_base_period or "不可识别"
            f.unavailable("同报告期同比", f"未找到最新报告期 {date_key(fin[-1])} 的精确上年同期 {expected}")
        for label, key in (("ROE(%)", "roe"), ("毛利率(%)", "grossprofit_margin"),
                           ("净利率(%)", "netprofit_margin"), ("OCF/净利润", None)):
            if key is None:
                series = [num(r.get("n_cashflow_act")) for r in window]
                prof = [num(r.get("net_profit")) for r in window]
                vals = [round(a / b, 3) if (a is not None and b not in (None, 0)) else None
                        for a, b in zip(series, prof)]
                f.add(f"{label} 近 {len(window)} 期序列", vals,
                      formula="n_cashflow_act / net_profit 逐期（末 8 期，升序）")
                latest_ratio = vals[-1]
                f.add(f"{label} 最新期", latest_ratio,
                      formula=f"{series[-1]} / {prof[-1]}")
            else:
                vals = [num(r.get(key)) for r in window]
                f.add(f"{label} 近 {len(window)} 期序列", vals,
                      field=f"financials.{key}（末 8 期，升序）")
                if len(vals) >= 2 and vals[0] is not None and vals[-1] is not None:
                    f.add(f"{label} 首末变化", round(vals[-1] - vals[0], 3),
                          formula=f"{vals[-1]} - {vals[0]}")

    val = [r for r in (_data(coll, "valuation") or []) if isinstance(r, dict)]
    if not val:
        f.unavailable("估值序列", "collection 无 valuation 维度数据")
    else:
        val = rows_asc(val)
        f.add("估值序列样本数", len(val), formula=f"len(valuation) = {len(val)}")
        f.add("估值序列区间",
              f"{date_key(val[0])} ~ {date_key(val[-1])}",
              field="valuation.trade_date（升序首/末行）")
        cur = val[-1]
        for label, key in (("PE(TTM)", "pe_ttm"), ("PB", "pb"), ("PS(TTM)", "ps_ttm")):
            # 2026-09-18 review #6：分位与中位数只在**正**序列上有定义（对齐
            # insight_model B3 的同日口径）。旧实现取全序列（含负值）：既让负 PE
            # 参与中位数，又让 latest≤0 时恒得 ≈0% 分位——Agent B 会读成「历史偏低
            # 位置（便宜）」，正是 B3 要拦的失真。「非正样本数」同时是 prompt 要求的
            # 「PE 分位失真检测（亏损期占比）」的数据载体（否则 Agent 只能违反 P0 自算）。
            nonnull = [num(r.get(key)) for r in val if num(r.get(key)) is not None]
            series = [x for x in nonnull if x > 0]
            nonpos = len(nonnull) - len(series)
            v = num(cur.get(key))
            f.add(f"{label} 当前", v, field=f"valuation.{key}（{date_key(cur)}）")
            f.add(f"{label} 非正样本数", nonpos,
                  formula=f"count({key} ≤ 0) = {nonpos}（全序列 n={len(nonnull)}）")
            if v is not None and v > 0:
                f.add(f"{label} 历史分位(%)", percentile_rank(series, v),
                      formula=f"count(≤ {v}) / {len(series)} × 100（**正序列** n={len(series)}）")
                if series:
                    f.add(f"{label} 中位数", round(statistics.median(series), 4),
                          formula=f"median({key} > 0 序列，n={len(series)})")
            else:
                f.unavailable(
                    f"{label} 历史分位(%)",
                    f"当前值 {v} 非正或无正样本（n={len(series)}）——分位在非正序列上无定义"
                    "（与 insight_model B3 同口径，不得用全序列近似）")

    kl = rows_asc(_data(coll, "kline") or [])
    if kl:
        f.add("K 线样本数", len(kl), formula=f"len(kline) = {len(kl)}")
        f.add("K 线区间", f"{date_key(kl[0])} ~ {date_key(kl[-1])}",
              field="kline.trade_date（升序首/末行）")
        for n in (20, 60):
            if len(kl) > n:
                f.add(f"近 {n} 个交易日涨跌(%)", pct(kl[-1].get("close"), kl[-1 - n].get("close")),
                      formula=f"close[-1] / close[-{n + 1}] − 1（升序后切片）")
    else:
        f.unavailable("K 线序列", "collection 无 kline 维度数据")
    return f


def build_industry(coll: dict) -> Facts:
    """Agent C（行业与竞争）：**取不到的一律显式不可得**，不留给 Agent 补。"""
    f = Facts()
    ind = _data(coll, "industry")
    if isinstance(ind, dict) and ind:
        for k, v in ind.items():
            f.add(f"industry.{k}", v, field=f"industry.{k}")
    else:
        f.unavailable("行业维度", "collection 无 industry 维度数据")

    # 申万行业指数：**结构性不可得**（本 collection 不产出该序列）
    f.unavailable("申万行业指数 20/60 日涨跌幅",
                  "collection 无申万行业指数序列——须标不可得，禁止用个股涨跌幅替代")
    f.unavailable("行业相对沪深 300 强弱",
                  "同上：无行业指数序列，无法计算相对强弱")

    peers = coll.get("industry_peers") or {}
    plist = (peers.get("peers") if isinstance(peers, dict) else None) or []
    if plist:
        f.add("同行样本数", len(plist), formula=f"len(industry_peers.peers) = {len(plist)}")
        for label, key in (("PE(TTM)", "pe_ttm"), ("PB", "pb"), ("ROE(%)", "roe"),
                           ("毛利率(%)", "grossprofit_margin")):
            xs = [num(p.get(key)) for p in plist if num(p.get(key)) is not None]
            if xs:
                f.add(f"同行 {label} 中位数", round(statistics.median(xs), 4),
                      formula=f"median(peers.{key}，n={len(xs)})")
                f.add(f"同行 {label} 样本数", len(xs), formula=f"len(非空 peers.{key}) = {len(xs)}")
    else:
        f.unavailable("同行可比公司", "collection 无 industry_peers.peers")

    cc = coll.get("chain_context") or {}
    if isinstance(cc, dict) and cc:
        f.add("产业链位置", cc.get("position") or cc.get("industry") or None,
              field="chain_context")
    else:
        f.unavailable("产业链位置", "collection 无 chain_context")
    # 利润池占比是**结构性不可得**（与 chain_context 是否取到无关）：引擎只产出
    # 毛利率对比框架，无上游/中游/下游利润占比数据源。无条件标注，防 Agent 用
    # 毛利率反推占比。
    f.unavailable("产业链利润池分布（上游/中游/下游利润占比）",
                  "引擎只产出毛利率对比框架，无利润池占比数据源（chain_context 自注）")
    return f


def build_risk(coll: dict) -> Facts:
    """Agent D（风险与治理）：events 全量计数 + 内部人信号（**holder_changes 补入**）。

    2026-09-18 review #8/#15 修复：

    - **#8 崩溃**：原 `ev = coll.get("events") or []` 后直接 `ev_sorted[0]`——
      events 为真值但不含 dict 行时（`["legacy string"]`，或 events 是 dict），
      过滤后 `ev_sorted` 为空 → IndexError 崩栈。与模块契约「取不到的项显式输出
      不可得」不符。守卫照 render_risk.py:973 同款 `isinstance(list)` 惯例。
    - **#15 伪事实**：原「近 N 条事件数 = min(n, len(events))」对任何 len≥n 的集合
      恒等于 n，不携带任何信息，却在 Agent D 的强制引用清单里——读者极易读成
      「近 N 日发生 N 起」（该字段与时间无关）。已删除；时间线信息由「事件日期区间」
      + 「事件类型计数」承担（agent-prompts.md 对 Agent D 无「近 N 条」强制项）。
    """
    f = Facts()
    raw_ev = coll.get("events")
    ev = [e for e in raw_ev if isinstance(e, dict)] if isinstance(raw_ev, list) else []
    f.add("事件总数", len(ev),
          formula=f"count(isinstance(e, dict) for e in events) = {len(ev)}")
    if ev:
        ev_sorted = sorted(ev, key=date_key)
        f.add("事件日期区间",
              f"{date_key(ev_sorted[0])} ~ {date_key(ev_sorted[-1])}",
              field="events.date（排序后首/末行）")
        counts: dict[str, int] = {}
        for e in ev_sorted:
            t = str(e.get("type") or e.get("event_type") or "unknown")
            counts[t] = counts.get(t, 0) + 1
        for t, c in sorted(counts.items(), key=lambda kv: -kv[1]):
            f.add(f"事件类型计数 · {t}", c, formula=f"count(type == {t!r}) = {c}")

    hc = _data(coll, "holder_changes")
    if hc:
        f.add("股东增减持记录数", len(hc), formula=f"len(holder_changes) = {len(hc)}")
    else:
        f.unavailable("内部人交易信号（近 12 月增减持方向）",
                      "collection 无 holder_changes 维度或该维度为空——禁止以公告标题推测方向")

    sh = _data(coll, "shareholders")
    if sh:
        f.add("前十大股东记录数", len(sh), formula=f"len(shareholders) = {len(sh)}")
        ratios = [(r.get("holder_name"), num(r.get("hold_ratio")))
                  for r in sh if isinstance(r, dict)]
        top = max((x for x in ratios if x[1] is not None), key=lambda x: x[1], default=None)
        if top:
            f.add("第一大股东持股比例(%)", top[1],
                  formula=f"max(hold_ratio) = {top[1]}（{top[0]}）")
    else:
        f.unavailable("股东结构", "collection 无 shareholders 维度数据")

    risk = coll.get("risk_report")
    if risk:
        f.add("风险扫描覆盖信号数", risk.get("coverage_n"), field="risk_report.coverage_n")
    return f


_BUILDERS = {"business": build_business, "financial": build_financial,
             "industry": build_industry, "risk": build_risk}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="SOP-DEEP 四视角 Python facts 提取器")
    ap.add_argument("collection_json", help="collect --save-raw 产出的 JSON 路径")
    ap.add_argument("--for", dest="role", required=True, choices=sorted(_BUILDERS))
    ap.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    args = ap.parse_args(argv)

    if args.role not in _BUILDERS:
        print(f"❌ 未知 role: {args.role}", file=sys.stderr)
        return 2
    try:
        with open(args.collection_json, encoding="utf-8") as fh:
            coll = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"❌ 读取 collection 失败: {exc}", file=sys.stderr)
        return 2

    facts = _BUILDERS[args.role](coll)
    if args.json:
        print(json.dumps({"role": args.role, "facts": facts.items}, ensure_ascii=False, indent=1))
    else:
        print(f"# Python facts — role={args.role}（**所有数字必须引用本表 id，禁止自行计算**）\n")
        print(facts.render())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
