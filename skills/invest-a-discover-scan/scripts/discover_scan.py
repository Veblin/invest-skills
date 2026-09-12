#!/usr/bin/env python3
"""invest-a-discover-scan CLI — 多透镜粗筛 → 研究观察短清单（v0.1）。

**研究信号，非决策**：检出的语义 = 「该标的符合**预注册规则定义**的客观条件」，
不构成任何交易建议（LAW 6）。深判走 invest-a-stock 既有流水线（清单每行附下钻命令）。

管线（设计 §4，控制调用量）：
    stock_basic(缓存 7d) → daily_basic(全市场 1 次) → L1 横截面
    → fina_indicator(仅候选集，按 ts_code) → 中过滤 → L3 gap → 排序 → 短清单
    → 落盘 md + 私有快照 JSONL（rules_version 必录）

退出码：0 正常 / 3 数据不可得（**不产空清单**）/ 4 缺 token 或权限 / 2 参数错误。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
_LIB_DIR = _SCRIPT_DIR / "lib"
# ⚠️ **只插 `scripts/lib`，不插 `scripts/` 根**：本 skill 的 lib 目录含 `__init__.py`
# （包名 `lib`），一旦把 `scripts/` 放进 sys.path，`import lib` 会命中本 skill 的 lib
# 而非 invest-a-stock 的 `lib` 包 → `lib.tushare_client` / `lib.trade_cal` 全部
# ModuleNotFoundError（跨技能跑测时实测：event-calendar 的 `import lib.trade_cal` 被带崩）。
# 本 skill 的模块按**顶层名**导入（`import lenses` 等），只需 `scripts/lib` 在路径上。
if str(_LIB_DIR) not in sys.path:
    sys.path.insert(0, str(_LIB_DIR))

from _invest_path import ensure_invest_a_scripts_on_path, ensure_skills_lib_on_path  # noqa: E402

ensure_skills_lib_on_path()
ensure_invest_a_scripts_on_path()

import lenses  # noqa: E402
import pool as pool_mod  # noqa: E402
import quality  # noqa: E402
import snapshot  # noqa: E402
import sources  # noqa: E402

DEFAULT_OUT_DIR = "reports/discover-scan"
DEFAULT_TOP = 15


def _drill_cmd(code: str) -> str:
    """下钻命令——**按当前运行形态自适应**。

    单体仓库：`skills/invest-a-stock/scripts/invest.py` 存在 → 给出可直接粘贴的命令。
    包内分发（skillhub/WorkBuddy 单包）：该路径不存在 → 给出**形态无关**的提示，
    避免打印一条包内用户无法执行的命令（R4 评审：原实现为硬编码 monorepo 路径）。
    """
    from pathlib import Path as _P

    # __file__ = skills/invest-a-discover-scan/scripts/discover_scan.py
    # parents: [0]=scripts [1]=invest-a-discover-scan [2]=skills [3]=code
    mono = _P(__file__).resolve().parents[2] / "invest-a-stock" / "scripts" / "invest.py"
    # ⚠️ 必须传**纯 6 位数字**，不是 ts_code：invest.py 的 `exchange_code()` 要求
    # `symbol.isdigit()`，带 `.SZ` 后缀会抛
    # `Invalid symbol: '000612.SZ' (must be 1-6 digits)` 并让整个 report 失败
    # （2026-09-12 真机实测：首版发出的命令跑不通——清单里附一条跑不通的命令
    #  比不附更糟，它会让使用者以为「工具坏了」）
    sym = str(code).split(".")[0].strip()
    if mono.exists():
        return f"uv run python skills/invest-a-stock/scripts/invest.py report {sym}"
    return f"invest-a-stock 的 report 子命令（下钻 {sym}）：详见其 SKILL.md 的 CLI 段"


def _now_shanghai() -> str:
    from dates import shanghai_now

    return shanghai_now().strftime("%Y-%m-%d-%H:%M:%S")


def _today_iso() -> str:
    from dates import shanghai_today, yyyymmdd_to_iso

    return yyyymmdd_to_iso(shanghai_today())


# ---------------------------------------------------------------------------
# 管线
# ---------------------------------------------------------------------------

def run_scan(*, top: int = DEFAULT_TOP, with_bj: bool = False,
             per_industry: int = 3) -> dict:
    """执行管线 → ``{"hits", "pool_stats", "warnings", "params", "trade_date"}``。

    失败语义：全市场源不可得 → raise ``RuntimeError``（由 CLI 转退出码 3）。
    """
    sources.reset_warnings()
    trade_date = sources.latest_trade_date()

    basic = sources.fetch_stock_basic()
    pool = pool_mod.build_pool(basic, with_bj=with_bj)

    daily = sources.fetch_daily_basic(trade_date)
    by_code = {str(r.get("ts_code")): r for r in daily}
    merged: list[dict] = []
    for r in pool["rows"]:
        d = by_code.get(r["ts_code"])
        if not d:
            continue
        merged.append({**r, "pe_ttm": d.get("pe_ttm"), "dv_ratio": d.get("dv_ratio"),
                       "close": d.get("close"), "total_mv": d.get("total_mv")})
    if not merged:
        raise RuntimeError(f"池内标的在 daily_basic（{trade_date}）中无一匹配——数据口径异常")

    n_positive_pe = sum(1 for r in merged if lenses.ey_pct(r.get("pe_ttm")) is not None)
    median_pe = lenses.universe_median_pe(merged)   # 分位的参照中心（规则 3 要求伴随）

    l1 = lenses.select_candidates(merged)
    missing_ind = sum(1 for r in l1 if r.get("industry_missing"))
    if missing_ind:
        sources.warnings.append(
            f"{missing_ind} 只标的行业字段缺失 → L1 行业条件跳过（设计 §5 降级）")

    rf_pct, rf_src = sources.rf_10y_pct()
    if rf_pct is None:
        # 降级须无条件记录（不能只在取数函数内部记——桩/换源时降级事实不变）
        sources.warnings.append(
            "中国 10Y 国债收益率不可得 → L3 利差项降级（仅保留 隐含增速 vs 预告上限 一项）")

    hits: list[dict] = []
    n_unassessable = 0
    quality_warnings: list[str] = []
    for r in l1:
        fina = sources.fetch_fina_indicator(r["ts_code"])
        # 降级链**按候选生效**（设计 §5：fina → 预告口径 → 跳过）。
        # 原实现只在 fina 为空时取预告却又 `continue` 丢掉，使降级档永不生效、
        # L3 增速子项恒 0（R4 评审实测复现）
        if fina:
            fc = []
            q = quality.pass_quality(r, fina, fc, tier="fina")
        else:
            fc = sources.fetch_forecast(r["ts_code"])
            q = quality.pass_quality(r, fina, fc, tier="forecast")
        if q.get("warning"):
            quality_warnings.append(q["warning"])
        if q["pass"] is None:
            n_unassessable += 1
            continue
        if not q["pass"]:
            continue
        # L3 增速子项需预告增速——**在通过质量闸门之后**才取（候选已收敛到几十只，
        # 调用量可控）；此前放在循环头部会让 403 只候选各多打一次接口
        if not fc:
            fc = sources.fetch_forecast(r["ts_code"])
        # ⚠️ 只用 `p_change_max`（净利**同比增速**上限，百分数）——可加总/可比。
        # **不得**回退到 `net_profit_max`：那是净利润的**绝对金额**，与
        # `g_implied`（百分比）相比是量纲错误，会得到一个恒真的假「满足」
        peers_fc_max = None
        if fc:
            v = fc[0].get("p_change_max")
            if v is not None:
                try:
                    peers_fc_max = float(v)
                except (TypeError, ValueError):
                    peers_fc_max = None
        flags = lenses.gap_flags(ey=r["ey_pct"], rf_pct=rf_pct, pe_ttm=r["pe_ttm"],
                                 forecast_growth_max_pct=peers_fc_max)
        mv = r.get("total_mv")
        hits.append({
            "ts_code": r["ts_code"], "name": r["name"], "industry": r["industry"],
            "pe_ttm": r["pe_ttm"], "ey_pct": round(r["ey_pct"], 2) if r["ey_pct"] else None,
            "pe_grank": round(r["pe_grank"], 4) if r["pe_grank"] is not None else None,
            "ind_rk": r["ind_rk"], "ind_n": r["ind_n"],
            "gap_flags": flags,
            "mv_yi": round(float(mv) / 1e4, 2) if mv is not None else None,   # 万元 → 亿元
            "close": r.get("close"), "fillback": None,
        })

    if not hits and n_unassessable == 0 and l1:
        sources.warnings.append(
            f"L1 命中 {len(l1)} 只但**无一通过质量闸门**（非「今日无机会」，是过滤结果）")

    ranked = lenses.rank_candidates(hits, per_industry=per_industry)[:top]
    # 逐条 warning 会淹没信号（L1 命中 403 只时曾刷出 188 行）→ 同消息合并计数。
    # 计数与调用量一并入报告，便于判断「空返回是限流还是真无数据」。
    merged_warnings = sources.aggregate_warnings(list(sources.warnings) + quality_warnings)
    if n_unassessable:
        merged_warnings.append(
            f"{n_unassessable} 只候选**质量过滤不可评估**（fina_indicator 空返回或限流）"
            f"——该过滤项对这些标的跳过，未冒充已过滤")
    return {
        "hits": ranked,
        "pool_stats": {"market": "主板+创业+科创" if not with_bj else "主板+创业+科创+北交所",
                       "n_positive_pe": n_positive_pe, "n_pool": len(merged),
                       "n_l1": len(l1), "n_excluded_st": pool["n_excluded_st"],
                       "median_pe": median_pe,
                       "n_unassessable": n_unassessable,
                       "calls": dict(sources.CALL_COUNT),
                       "empty_retries": sources.EMPTY_RETRY_COUNT},
        "warnings": merged_warnings,
        # per_industry / with_bj 也是**规则参数**（rules.md §三.5）：缺了它们，
        # 同一天不同规则的两次运行会产出无法归因的快照（回填裁决的锚点失效）
        "params": {"pe_grank_max": lenses.PE_GRANK_MAX, "ind_rank_max": lenses.IND_RANK_MAX,
                   "roe_min": quality.ROE_MIN_PCT, "top_n": top,
                   "per_industry": per_industry, "with_bj": with_bj},
        "trade_date": trade_date,
        "rf": {"pct": rf_pct, "source": rf_src},
        "market_context": sources.market_form_context(),
    }


# ---------------------------------------------------------------------------
# 渲染
# ---------------------------------------------------------------------------

def render_report(scan: dict) -> str:
    """短清单 → markdown（报告头含生成日/数据日/池口径/规则版本/降级清单/免责）。"""
    hits = scan["hits"]
    ps = scan["pool_stats"]
    ctx = scan.get("market_context") or {}
    lines = [
        f"# 低估发现扫描 — {_today_iso()}",
        "",
        "> **研究信号，非决策**：检出 = 「该标的符合**预注册规则定义**的客观条件」，"
        "不构成任何交易建议（LAW 6）。清单是**观察起点**，深判走下钻命令。",
        "",
        f"- 生成时间：{_now_shanghai()}（北京时间）｜数据日：{scan['trade_date']}",
        f"- 池口径：{ps['market']}，剔 ST/退市（剔除 {ps['n_excluded_st']} 只）；"
        f"池内 {ps['n_pool']} 只，正 PE {ps['n_positive_pe']} 只，L1 命中 {ps['n_l1']} 只"
        + (f"；正 PE 子总体**中位 PE {ps['median_pe']:.2f}x**"
           f" [来源: Python calc: median(正 PE 序列)]" if ps.get("median_pe") else ""),
        f"- 规则版本：{snapshot.RULES_VERSION}；阈值 pe_grank ≤ {scan['params']['pe_grank_max']}、"
        f"行业内排名 ≤ {scan['params']['ind_rank_max']:.0%}、ROE(年化) ≥ {scan['params']['roe_min']}%",
        f"- 质量口径：ROE 用 `roe_yearly`（年化，跨期同口径）；净利用**扣非归母净利**"
        f"（比「归母净利」严格——剔除非经常性损益）",
    ]
    calls = ps.get("calls") or {}
    if calls:
        lines.append(
            f"- 接口调用：stock_basic {calls.get('stock_basic', 0)} / "
            f"daily_basic {calls.get('daily_basic', 0)} / "
            f"fina_indicator {calls.get('fina_indicator', 0)}"
            f"（其中空返回重试 {ps.get('empty_retries', 0)} 次）——"
            f"fina_indicator 无全市场批量形态，按候选逐个取")
    rf = scan.get("rf") or {}
    # rf.get("pct") 为 None 时不得渲染成「中国 10Y None%」（把 Python None 当收益率）
    if rf.get("pct") is not None:
        lines.append(f"- L3 利差口径：中国 10Y {rf['pct']}% [来源: {rf.get('source')}]")
    else:
        lines.append("- L3 利差口径：中国 10Y **不可得** → 利差项降级"
                     "（仅保留「隐含增速 vs 预告上限」子项）")
    if ctx.get("available"):
        lines.append(f"- L4 市场语境（**不进规则**）：{ctx.get('market_form')} —— {ctx.get('note')}")
    else:
        lines.append(f"- L4 市场语境：不可得（{ctx.get('note')}）")
    lines.append("")

    lines.append("## 降级清单 / 警告\n")
    if scan["warnings"]:
        lines.extend(f"- ⚠️ {w}" for w in scan["warnings"])
    else:
        lines.append("- （无）")
    lines.append("")

    lines.append(f"## 短清单（{len(hits)} 只，上限 {scan['params']['top_n']}）\n")
    if not hits:
        lines.append("本次**无标的通过全部闸门**——这是过滤结果，不是「市场无机会」的事实断言。\n")
    else:
        lines.append("| # | 代码 | 名称 | 行业 | PE(TTM) | EY% | 全A分位 | 行业排名 | gap | 市值(亿) |")
        lines.append("|---|---|---|---|---|---|---|---|---|---|")
        for i, h in enumerate(hits, 1):
            rk = f"{h['ind_rk']}/{h['ind_n']}" if h.get("ind_rk") else "—"
            lines.append(
                f"| {i} | {h['ts_code']} | {h['name']} | {h['industry']} | "
                f"{h['pe_ttm']} | {h['ey_pct']} | {h['pe_grank']:.1%} | {rk} | "
                f"{sum(h['gap_flags'])}/2 | {h['mv_yi']} |")
        lines.append("")
        lines.append("### 逐只理由与下钻\n")
        for h in hits:
            flags = h["gap_flags"]
            # 行业缺失的命中行 ind_rk/ind_n 均为 None——不得渲染成「None/None」
            ind_txt = (f"行业内排名 {h['ind_rk']}/{h['ind_n']}；" if h.get("ind_rk")
                       else "行业内排名：行业字段缺失，该条件已跳过（设计 §5 降级）；")
            # 分位**必须伴随中位数**（CLAUDE.md 估值分位使用规则 3；
            # lint `percentile-without-median` 拦截）。中位缺失时退回不给分位文本，
            # 而不是只印分位
            if ps.get("median_pe"):
                pct_txt = (f"全 A 正 PE 子总体分位 {h['pe_grank']:.1%}"
                           f"（≤{scan['params']['pe_grank_max']:.0%}；"
                           f"该子总体中位 PE {ps['median_pe']:.2f}x）；")
            else:
                pct_txt = (f"全 A 正 PE 子总体分位 {h['pe_grank']:.1%}"
                           f"（≤{scan['params']['pe_grank_max']:.0%}）；")
            lines.append(
                f"**{h['ts_code']} {h['name']}** — 命中 L1 横截面便宜："
                f"PE(TTM) {h['pe_ttm']}，EY {h['ey_pct']}%，"
                + pct_txt + ind_txt
                + f"L3 gap 标记 {sum(flags)}/2（利差{'>0' if flags[0] else '未满足'}、"
                f"隐含增速<预告上限{'满足' if flags[1] else '未满足/不可得'}）"
                f" [来源: Python calc: EY=100/PE; 分位=percentile_rank_inclusive/100]"
            )
            lines.append(f"  下钻：`{_drill_cmd(h['ts_code'])}`")
        lines.append("")

    lines.append("> 声明：本清单为**研究观察起点**，不构成投资建议，不含买卖/仓位建议；"
                 "阈值以**预注册草案**身份入库（`references/rules.md`），改动须 bump 规则版本"
                 "并记录原因。数据源：tushare（全链路）；降级项见上方清单。")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="invest-a-discover-scan — 多透镜粗筛 → 研究观察短清单（研究信号，非决策）")
    p.add_argument("--top", type=int, default=DEFAULT_TOP, help=f"短清单上限（默认 {DEFAULT_TOP}）")
    p.add_argument("--with-bj", action="store_true", help="纳入北交所（默认排除）")
    p.add_argument("--per-industry", type=int, default=3, help="同行业最多入选数（默认 3）")
    p.add_argument("--no-out", action="store_true", help="不落 md（快照仍写）")
    p.add_argument("--out-dir", default=DEFAULT_OUT_DIR, help=f"md 输出目录（默认 {DEFAULT_OUT_DIR}）")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if not sources.has_token():
        print("❌ 缺少 TUSHARE_TOKEN——本 skill 数据面全程走 tushare（退出码 4）", file=sys.stderr)
        return 4
    if args.top < 1 or args.per_industry < 1:
        print("❌ --top / --per-industry 须为正整数", file=sys.stderr)
        return 2

    try:
        scan = run_scan(top=args.top, with_bj=args.with_bj,
                        per_industry=args.per_industry)
    except Exception as exc:  # noqa: BLE001 —— 数据不可得：退出 3，**不产空清单**
        print(f"❌ 数据不可得（{type(exc).__name__}: {exc}）——"
              f"不产出空清单（空清单会被误读为「市场无机会」这一事实断言）", file=sys.stderr)
        return 3

    body = render_report(scan)
    if not args.no_out:
        outdir = Path(args.out_dir)
        outdir.mkdir(parents=True, exist_ok=True)
        path = outdir / f"{_today_iso()}.md"
        path.write_text(body, encoding="utf-8")
        print(f"📝 短清单: {path}\n")
    print(body)
    if not args.no_out:
        print(f"\n（快照已写入：{snapshot.snapshot_path()}）")

    rec = snapshot.build_snapshot(
        scan_ts=_now_shanghai(), trade_date=scan["trade_date"], pool=scan["pool_stats"],
        params=scan["params"], hits=scan["hits"], warnings=scan["warnings"])
    snapshot.append_snapshot(rec)
    return 0


if __name__ == "__main__":
    sys.exit(main())
