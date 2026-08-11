#!/usr/bin/env python3
"""invest-a-etf — ETF 研究数据 CLI.

用法::

    uv run python skills/invest-a-etf/scripts/etf.py report 563300
    uv run python skills/invest-a-etf/scripts/etf.py report 563300 --json
    uv run python skills/invest-a-etf/scripts/etf.py diagnose
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

_LIB_DIR = Path(__file__).resolve().parent / "lib"
if str(_LIB_DIR) not in sys.path:
    sys.path.insert(0, str(_LIB_DIR))

from etf_data import (  # noqa: E402
    CSINDEX_MAP,
    ETF_HEDGE_MAP,
    compute_history_stats,
    prefetch_etf_spot,
    query_etf_data,
    query_etf_holdings,
    query_etf_kline,
    query_etf_kline_history,
    query_etf_quote,
    query_etf_share_history,
)
from lib.nums import safe_float  # noqa: E402


def _kline_summary(kline: dict) -> dict:
    """Drop bulky nav_history for default stdout."""
    return {k: v for k, v in kline.items() if k != "nav_history"}


# 事件文件缺省目录（R11b）：skills/invest-a-etf/events/{symbol}.json
_EVENTS_DIR = Path(__file__).resolve().parent.parent / "events"


def _build_events_block(symbol: str, events_path: str | None,
                        history_block: dict | None) -> dict:
    """R11b: 事件文件加载 + 事件-价格对照（±1 交易日对齐）。无文件不阻断。"""
    from etf_timeline import align_events_with_price, detect_big_move_days, load_events_file

    path = Path(events_path) if events_path else _EVENTS_DIR / f"{symbol}.json"
    if not path.exists():
        return {"available": False, "path": str(path),
                "note": f"无事件文件（{path.name}），跳过（不阻断）",
                "rows": None, "aligned": None}
    events, msg = load_events_file(path)
    if events is None:
        return {"available": False, "path": str(path),
                "note": f"事件文件校验失败: {msg}", "rows": None, "aligned": None}
    aligned = None
    if (history_block and history_block.get("stats")
            and history_block["stats"].get("status") == "available"):
        move_days = detect_big_move_days(history_block["history"].get("rows") or [])
        aligned = align_events_with_price(move_days, events)
    return {"available": True, "path": str(path), "note": None,
            "rows": events, "aligned": aligned}


def _build_playbook_block(history_block: dict | None, kline: dict) -> dict:
    """R11c: 回撤档位 σ 分级 + 三步核查 + LAW 6a 声明（研究流程规则，非买卖指令）。"""
    from etf_playbook import (
        LAW6A_DISCLAIMER,
        daily_vol_pct,
        drawdown_levels,
        three_step_checklist,
    )

    closes: list[float] = []
    if (history_block and history_block.get("stats")
            and history_block["stats"].get("status") == "available"):
        rows = history_block["history"].get("rows") or []
        closes = [c for c in (safe_float(r.get("nav", r.get("close"))) for r in rows)
                  if c is not None]
    vol = daily_vol_pct(closes)
    vol_source = "history"
    if vol is None:
        derived = kline.get("derived") or {}
        vol = derived.get("daily_volatility_pct")
        vol_source = "kline.derived"
    return {
        "available": vol is not None,
        "vol_60d_daily_pct": round(vol, 2) if vol is not None else None,
        "vol_source": vol_source if vol is not None else None,
        "drawdown_levels": drawdown_levels(closes, vol),
        "checklist": three_step_checklist(),
        "disclaimer": LAW6A_DISCLAIMER,
        "note": None if vol is not None
        else "无足够历史数据计算 60 日日均波动（需 ≥61 个收盘价）",
    }


def _share_history_summary(sh: dict) -> dict:
    """Drop detail rows, keep summary + row_count for compact stdout."""
    return {k: v for k, v in sh.items() if k != "rows"}


def cmd_report(symbol: str, *, as_json: bool, with_nav: bool,
               history: bool = False, history_days: int = 250,
               events_path: str | None = None, playbook: bool = False) -> int:
    symbol = symbol.strip()
    if not symbol.isdigit() or len(symbol) != 6:
        print(f"错误: 需要 6 位数字代码，收到 {symbol!r}", file=sys.stderr)
        return 2

    prefetch_etf_spot()
    profile = query_etf_data(symbol)
    # 先查询再入库：index_pe_pct 分位只对「不含今日行」的历史序列计算（避免今日
    # 自投成 100%/5% 假象），与 journal 路径（不 persist-first）语义一致；写库放
    # 查询后，下次报告的分位即含今日（幂等，失败不阻断报告）
    idx = CSINDEX_MAP.get(symbol)
    if idx:
        res = _persist_index_pe([idx])
        if res.get("error"):
            print(f"⚠️ 指数 PE 入库失败: {res['error']}", file=sys.stderr)
    quote = query_etf_quote(symbol)
    kline = query_etf_kline(symbol)
    share_history = query_etf_share_history(symbol, days=20)

    # R11a: --history/--playbook 时取历史行情深度（nav 链路优先，失败回退 baostock）+ 历史统计
    history_block = None
    if history or playbook:
        hist = query_etf_kline_history(symbol, days=history_days)
        hist_stats = (
            compute_history_stats(hist.get("rows") or [])
            if hist.get("status") == "available" else None
        )
        history_block = {"history": hist, "stats": hist_stats}

    # R11b: 事件文件（缺省自动读 events/{symbol}.json，无文件不阻断）+ 事件-价格对照
    events_block = _build_events_block(symbol, events_path, history_block)
    # R11c: 情景预案（回撤档位 σ 分级 + 三步核查 + LAW 6a 声明）
    playbook_block = _build_playbook_block(history_block, kline) if playbook else None

    payload = {
        "skill": "invest-a-etf",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "symbol": symbol,
        "index_code": CSINDEX_MAP.get(symbol),
        "profile": profile,
        "quote": quote,
        "kline": kline if with_nav else _kline_summary(kline),
        "share_history": _share_history_summary(share_history) if not with_nav else share_history,
        "history": history_block if history else None,
        "events": events_block,
        "playbook": playbook_block,
        "disclaimer": "研究数据快照，不构成投资建议。",
    }

    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
        return 0

    # Compact human-readable summary for Claude / terminal
    print(f"# invest-a-etf report · {symbol}")
    print(f"generated_at: {payload['generated_at']}")
    print()
    hc = profile.get("hedge_coverage") or {}
    print("## profile")
    print(f"  index_pe:          {profile.get('index_pe')}")
    print(f"  index_pe_status:   {profile.get('index_pe_status')}")
    ipe_pct = profile.get("index_pe_pct")
    if ipe_pct is not None:
        print(f"  index_pe_pct:      {ipe_pct}%（index_pe_history 历史分位）")
    alloc = profile.get("industry_allocation")
    if alloc:
        top3 = ", ".join(f"{a['industry']} {a['pct']:.1f}%" for a in alloc[:3])
        print(f"  industry_alloc:    {top3}")
    print(f"  premium_discount:  {profile.get('premium_discount')}")
    print(f"  aum_yi:            {profile.get('aum')}")
    print(f"  hedge_coverage:    {hc.get('coverage')} ({hc.get('index')})")
    print(f"  flags:             {profile.get('flags')}")
    if profile.get("_errors"):
        print(f"  errors:            {profile['_errors']}")
    print()
    print("## quote")
    print(f"  price:             {quote.get('price')}  status={quote.get('status')}")
    print(f"  change_pct:        {quote.get('change_pct')}")
    print(f"  amount:            {quote.get('amount')}")
    print()
    print("## kline")
    print(f"  nav_rows:          {kline.get('nav_rows')}  status={kline.get('status')}")
    print(f"  latest_nav:        {kline.get('latest_nav')}  adj={kline.get('adj_applied')}")
    print(f"  ma20/ma60 (NAV):   {kline.get('ma20')} / {kline.get('ma60')}")
    print(f"  ma20/ma60 (index): {kline.get('index_ma20')} / {kline.get('index_ma60')}")
    print(f"  boll (u/m/l):      {kline.get('boll_upper')} / {kline.get('boll_mid')} / {kline.get('boll_lower')}")
    print()
    print()
    print("## events (事件-价格对照 · R11b)")
    if events_block.get("available"):
        print(f"  {events_block['path']} · {len(events_block['rows'])} 条事件")
        aligned_rows = events_block.get("aligned")
        if aligned_rows:
            for r in aligned_rows:
                facts = "; ".join(r["同日事实"]) if r["同日事实"] else "无同日大波动"
                link = r["可能关联（待验证）"] or "未对齐"
                print(f"  {r['date']} {r['event'][:24]:<24s} | 同日: {facts[:34]:<34s} | {link}")
        else:
            print("  提示: 加 --history 输出事件-价格对照（±1 交易日对齐）")
    else:
        print(f"  {events_block['note']}")

    if playbook_block is not None:
        print()
        print("## playbook (情景预案 · R11c · LAW 6a)")
        if playbook_block.get("available"):
            print(f"  60 日日均波动: {playbook_block['vol_60d_daily_pct']}%  "
                  f"(来源: {playbook_block['vol_source']})")
            print("  回撤档位 → σ 倍数 → 触发核验深度（非动作指令）:")
            for lv in playbook_block["drawdown_levels"]:
                sigma = f"{lv['sigma_multiple']}σ" if lv["sigma_multiple"] is not None else "N/A"
                print(f"    {lv['level_pct']:+.0f}%  →  {sigma}  →  {lv['verification_depth']}")
            print("  三步核查:")
            for step in playbook_block["checklist"]:
                print(f"    {step}")
            print(f"  {playbook_block['disclaimer']}")
        else:
            print(f"  {playbook_block['note']}")

    if history_block is not None:
        hist = history_block["history"]
        stats = history_block["stats"]
        print("## history (历史行情深度 · R11a)")
        if hist.get("status") == "available" and stats:
            print(f"  source:            {hist['source']}  ({hist.get('note', '')})")
            print(f"  rows:              {stats['rows']}  ({stats['date_range']})")
            ah, al = stats["annual_high"], stats["annual_low"]
            print(f"  annual_high/low:   {ah['close']} @ {ah['date']} / {al['close']} @ {al['date']}")
            md = stats["max_drawdown"]
            print(f"  max_drawdown:      {md['drawdown_pct']}%  "
                  f"({md['peak_close']} @ {md['peak_date']} → {md['trough_close']} @ {md['trough_date']})")
            print(f"  big_move_days:     {len(stats['big_move_days'])} 个 |change_pct|≥5% 交易日")
            print(f"  ma20/60/120:       {stats['ma20']} / {stats['ma60']} / {stats['ma120']}")
            print(f"  current_vs_high/low: {stats['current_vs_high_pct']}% / {stats['current_vs_low_pct']}%")
        else:
            print(f"  不可用: {hist.get('error') or '历史行情获取失败'}")
    print()
    print("## share_history (近20日 份额资金流 + OHLCV | Tushare fund_share+fund_daily)")
    if share_history.get("available"):
        # 表头
        print(f"  {'日期':<10s} {'开盘':>6s} {'最高':>6s} {'最低':>6s} {'收盘':>6s} {'涨跌%':>7s} "
              f"{'成交额':>7s} {'换手%':>6s} {'份额变':>8s} {'资金流':>7s}  方向")
        print("  " + "-" * 90)
        for r in share_history.get("rows", []):
            sc = r.get('share_change')
            fe = r.get('flow_est')
            dr = r.get('direction')
            to = r.get('turnover_rate')
            sh = r.get('shares')
            # T+1 延迟：份额/资金流字段可能为空；停牌日 OHLC/pct_chg 可为
            # None（None 直入 f-string 格式说明符会 TypeError 崩溃 CLI）
            share_str = f"{sc:>+8.0f}" if sc is not None else "       ⏳"
            flow_str = f"{fe:>+7.2f}" if fe is not None else "     ⏳"
            dir_str = dr if dr else ("⏳ T+1 待确认" if sc is None else "?")
            to_str = f"{to:>6.2f}" if to is not None else "    ⏳"
            pct_str = f"{r.get('pct_chg'):>+7.2f}" if r.get('pct_chg') is not None else "      -"
            amount_str = f"{r.get('amount', 0) or 0:>7.2f}"
            open_str = f"{r.get('open'):>6}" if r.get('open') is not None else "     -"
            high_str = f"{r.get('high'):>6}" if r.get('high') is not None else "     -"
            low_str = f"{r.get('low'):>6}" if r.get('low') is not None else "     -"
            close_str = f"{r.get('close'):>6}" if r.get('close') is not None else "     -"
            print(f"  {r['date']:<10s} "
                  f"{open_str} "
                  f"{high_str} "
                  f"{low_str} "
                  f"{close_str} "
                  f"{pct_str} "
                  f"{amount_str} "
                  f"{to_str} "
                  f"{share_str} "
                  f"{flow_str}  "
                  f"{dir_str}")
        s = share_history.get("summary", {})
        total_flow = s.get('total_flow_est')
        total_flow_str = f"{total_flow:+.2f}" if total_flow is not None else "-"
        avg_amt = s.get('avg_amount_e')
        avg_amt_str = f"{avg_amt:.2f}" if avg_amt is not None else "-"
        share_chg = s.get('share_total_change')
        share_chg_str = f"{share_chg:+.0f}" if share_chg is not None else "-"
        print(f"  资金趋势: {s.get('trend', '?')} | {s['row_count']}日合计: {total_flow_str} 亿 | "
              f"日均成交: {avg_amt_str} 亿 | 份额变化: {share_chg_str} 万份")
    else:
        print(f"  不可用: {share_history.get('note', '未知')}")
    print()
    print("> 完整叙事请按 skills/invest-a-etf/references/report-template.md 合成。")
    print("> ⚠️ 不构成投资建议。")
    return 0


def _validate_symbol(symbol: str) -> str | None:
    """校验 6 位数字代码；非法时打印错误并返回 None。"""
    symbol = symbol.strip()
    if not symbol.isdigit() or len(symbol) != 6:
        print(f"错误: 需要 6 位数字代码，收到 {symbol!r}", file=sys.stderr)
        return None
    return symbol


def cmd_holdings(symbol: str, *, as_json: bool) -> int:
    """R12: 前十大持仓 + 集中度统计。"""
    symbol = _validate_symbol(symbol)
    if symbol is None:
        return 2
    data = query_etf_holdings(symbol)
    if as_json:
        payload = {
            "skill": "invest-a-etf",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "symbol": symbol,
            "holdings": data,
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
        return 0

    print(f"# holdings · {symbol}")
    if data["status"] != "ok":
        print(f"⏳ 持仓数据不可用: {data['note']}")
        return 0
    quarter = data.get("quarter") or ""
    print(f"report_date: {data['report_date']} ({quarter})  source: {data['source']}")
    print(f"  {'#':<3s} {'代码':<8s} {'名称':<10s} {'占比%':>7s} "
          f"{'持股(万股)':>12s} {'市值(万元)':>14s}")
    for i, r in enumerate(data["rows"], start=1):
        pct = f"{r['pct']:.2f}" if r.get("pct") is not None else "-"
        shares = f"{r['shares']:.2f}" if r.get("shares") is not None else "-"
        amount = f"{r['amount']:.2f}" if r.get("amount") is not None else "-"
        print(f"  {i:<3d} {r['code']:<8s} {r['name']:<10s} {pct:>7s} "
              f"{shares:>12s} {amount:>14s}")
    top1 = data.get("top1_pct")
    top5 = data.get("top5_sum_pct")
    top10 = data.get("top10_sum_pct")
    t1 = f"{top1:.2f}" if top1 is not None else "-"
    t5 = f"{top5:.2f}" if top5 is not None else "-"
    t10 = f"{top10:.2f}" if top10 is not None else "-"
    print(f"集中度(引擎): top1 {t1}% | top5 {t5}% | top10 {t10}%")
    print(f"note: {data['note']}")
    print("> 子环节聚类：由报告层 AI 基于名单归类，标注「AI 归类」（引擎不聚类）")
    return 0


def cmd_peers(symbol: str, *, as_json: bool, peers_str: str | None) -> int:
    """R13: 赛道资金流对比 + 相对强弱。"""
    symbol = _validate_symbol(symbol)
    if symbol is None:
        return 2
    peers_list = None
    if peers_str:
        peers_list = [p.strip() for p in peers_str.split(",") if p.strip()]
    try:
        from etf_peers import query_etf_peers
    except ImportError as exc:
        print(f"etf_peers 模块不可用: {exc}（请检查路径配置或 canonical 模块加载）")
        return 1
    data = query_etf_peers(symbol, peers_list)
    if as_json:
        payload = {
            "skill": "invest-a-etf",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "symbol": symbol,
            "peers": data,
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
        return 0

    print(f"# peers · {symbol}")
    print(f"peer_source: {data['peer_source']}")
    if not data["available"]:
        print(f"⏳ {data.get('note', '赛道不可用')}")
        return 0
    names = data.get("names") or {}
    print("## 资金流对比 (Tushare fund_share · 20日窗口 · 亿元)")
    print(f"  {'代码':<8s} {'名称':<14s} {'20日流':>8s} {'5日流':>7s} "
          f"{'份额变化%':>9s} {'趋势'}")
    for row in data["flow"]["rows"]:
        code = row["symbol"]
        name = names.get(code) or "-"
        f20 = f"{row['flow_20d_e']:+.2f}" if row.get("flow_20d_e") is not None else "⏳"
        f5 = f"{row['flow_5d_e']:+.2f}" if row.get("flow_5d_e") is not None else "⏳"
        sp = f"{row['share_change_pct']:+.2f}" if row.get("share_change_pct") is not None else "⏳"
        trend = row.get("trend") or "⏳"
        note = row.get("note")
        extra = f" ⚠ {note}" if note else ""
        print(f"  {code:<8s} {name:<14s} {f20:>8s} {f5:>7s} {sp:>9s} {trend}{extra}")
        span = row.get("share_change_span")
        if span is not None and span < 19:
            print(f"  ⚠ {code} 份额变化% 仅覆盖 {span} 个交易日间隔（份额历史不足 20 日）")
    rs = data.get("rs")
    if rs and "error" not in rs:
        rank = rs.get("rank_20d") or {}
        rank_str = f"{rank.get('rank', '-')}/{rank.get('total', '-')}" if rank else "-"
        print("## 相对强弱 (基准=同赛道等权均值 · 20日)")
        print(f"  rs_latest {rs['rs_latest']} | rs_window_start {rs['rs_window_start']} | "
              f"rs_change {rs['rs_change']:+.2f} | 20日收益排名 {rank_str}")
    elif rs:
        print(f"## 相对强弱: ⏳ {rs['error']}")
    for note in data.get("notes") or []:
        print(f"note: {note}")
    return 0


def cmd_industry_pe() -> int:
    """打印申万一级行业 PE/PB 一览。"""
    try:
        from industry_snapshot import list_industry_snapshot
    except ImportError:
        print("industry_snapshot 模块不可用（请检查路径配置）")
        return 1
    rows = list_industry_snapshot()
    if not rows:
        print("无行业 PE 数据。请先运行 `etf.py collect-weekly` 采集。")
        print("（首次采集后需等待每周五收盘后自动更新，或手动触发。）")
        return 0
    print(f"{'行业':<10s} {'代码':<8s} {'PE':>8s} {'PB':>6s} {'涨跌%':>8s} {'换手%':>8s} {'日期':>10s}")
    print("-" * 62)
    for r in rows:
        pe_str = f"{r.get('pe', 0):.2f}" if r.get('pe') is not None else "N/A"
        pb_str = f"{r.get('pb', 0):.2f}" if r.get('pb') is not None else "N/A"
        chg_str = f"{r.get('chg_pct', 0):+.2f}" if r.get('chg_pct') is not None else "N/A"
        to_str = f"{r.get('turnover_pct', 0):.2f}" if r.get('turnover_pct') is not None else "N/A"
        print(f"{r.get('index_name', '?'):<10s} {r.get('index_code', '?'):<8s} "
              f"{pe_str:>8s} {pb_str:>6s} {chg_str:>8s} {to_str:>8s} {r.get('date', '?'):>10s}")
    print(f"\n共 {len(rows)} 个申万一级行业（数据来源: index_analysis_weekly_sw）")
    return 0


def _persist_index_pe(idx_codes: list[str] | None) -> dict:
    """把 etf_index_pe 缓存信封写入 index_pe_history（幂等，失败不阻断报告）。

    返回 persist_index_pe_from_cache 的结果 dict（含 error/rows_saved/
    index_codes/ok_envelopes）；调用抛异常时返回 {"error": ...}，由调用方
    决定是否提示。cold-cache 失败日 data_bridge 不缓存 missing 信封，此
    调用与报告自身取数各回源一次（见 index_pe_snapshot 模块 docstring）。
    """
    from index_pe_snapshot import persist_index_pe_from_cache
    try:
        return persist_index_pe_from_cache(idx_codes)
    except Exception as exc:
        return {"error": f"index_pe persist raised: {exc}"}


def cmd_collect_weekly() -> int:
    """手动触发行业 PE 周度采集 + 指数 PE 历史快照入库。"""
    try:
        from industry_snapshot import collect_industry_weekly
    except ImportError:
        print("industry_snapshot 模块不可用（请检查路径配置）")
        return 1
    print("采集申万行业 PE/PB 周度快照...")
    result = collect_industry_weekly()
    if result.get("error"):
        print(f"采集失败: {result['error']}")
        return 1
    print(f"完成: {result['industries_saved']} 个行业已写入 industry_weekly（日期 {result['date']}）")
    # 顺带全量写指数 PE 历史（CSINDEX_MAP 全部代码，从 L2 缓存信封提取）
    pe_result = _persist_index_pe(None)
    if pe_result.get("error"):
        print(f"⚠️ 指数 PE 入库失败: {pe_result['error']}", file=sys.stderr)
    elif pe_result.get("rows_saved", 0) == 0:
        # 0 行 ≠ 成功：信封全部缺失（非交易日/取数失败）需要显式告警（D5）
        print(
            f"⚠️ 指数 PE 历史: 0 行可写（{pe_result.get('index_codes', 0)} 个指数代码均无 ok 信封；"
            "非交易日或取数失败）",
            file=sys.stderr,
        )
    else:
        print(f"指数 PE 历史: {pe_result['rows_saved']} 行已写入 index_pe_history（{pe_result['index_codes']} 个指数代码）")
    return 0


def cmd_diagnose() -> int:
    print("invest-a-etf diagnose")
    print(f"  ETF_HEDGE_MAP entries: {len(ETF_HEDGE_MAP)}")
    print(f"  CSINDEX_MAP entries:   {len(CSINDEX_MAP)}")
    try:
        import akshare as ak  # noqa: F401

        print("  akshare:              OK")
    except Exception as exc:
        print(f"  akshare:              FAIL ({exc})")
        return 1
    # smoke: known code in map
    sample = "510300"
    print(f"  sample hedge[{sample}]: {ETF_HEDGE_MAP.get(sample)}")
    print("diagnose: OK")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="etf.py", description="invest-a-etf CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_report = sub.add_parser("report", help="采集 ETF 数据快照")
    p_report.add_argument("symbol", help="6 位 ETF 代码")
    p_report.add_argument("--json", action="store_true", help="输出完整 JSON")
    p_report.add_argument(
        "--with-nav",
        action="store_true",
        help="JSON/摘要中保留完整净值历史（默认摘要省略）",
    )
    p_report.add_argument(
        "--history",
        action="store_true",
        help="R11a: 输出历史行情深度（nav 链路优先，失败自动回退 baostock）+ 历史统计",
    )
    p_report.add_argument(
        "--history-days",
        type=int,
        default=250,
        help="历史行情回溯交易日数（默认 250）",
    )
    p_report.add_argument(
        "--events",
        metavar="PATH",
        default=None,
        help="R11b: 事件文件路径（JSON Lines）；缺省自动读 skills/invest-a-etf/events/{symbol}.json，无文件不阻断",
    )
    p_report.add_argument(
        "--playbook",
        action="store_true",
        help="R11c: 输出情景预案（回撤档位 σ 分级 + 三步核查清单 + LAW 6a 声明）",
    )

    sub.add_parser("diagnose", help="检查依赖与映射表")

    sub.add_parser("industry-pe", help="申万一级行业 PE/PB 一览")
    sub.add_parser("collect-weekly", help="手动触发行业 PE 周度采集")

    p_holdings = sub.add_parser("holdings", help="R12: 前十大持仓 + 集中度统计")
    p_holdings.add_argument("symbol", help="6 位 ETF 代码")
    p_holdings.add_argument("--json", action="store_true", help="输出完整 JSON")

    p_peers = sub.add_parser("peers", help="R13: 赛道资金流对比 + 相对强弱")
    p_peers.add_argument("symbol", help="6 位 ETF 代码")
    p_peers.add_argument("--json", action="store_true", help="输出完整 JSON")
    p_peers.add_argument(
        "--peers",
        metavar="CODES",
        default=None,
        help="显式赛道清单（逗号分隔，如 \"512660,512760\"）；缺省按 ETF_TO_SW_INDUSTRY 自动发现同行业",
    )

    args = parser.parse_args(argv)
    if args.cmd == "report":
        return cmd_report(args.symbol, as_json=args.json, with_nav=args.with_nav,
                          history=args.history, history_days=args.history_days,
                          events_path=args.events, playbook=args.playbook)
    if args.cmd == "diagnose":
        return cmd_diagnose()
    if args.cmd == "industry-pe":
        return cmd_industry_pe()
    if args.cmd == "collect-weekly":
        return cmd_collect_weekly()
    if args.cmd == "holdings":
        return cmd_holdings(args.symbol, as_json=args.json)
    if args.cmd == "peers":
        return cmd_peers(args.symbol, as_json=args.json, peers_str=args.peers)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
