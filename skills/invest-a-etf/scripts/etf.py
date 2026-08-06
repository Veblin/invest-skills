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
    query_etf_kline,
    query_etf_kline_history,
    query_etf_quote,
    query_etf_share_history,
)


def _kline_summary(kline: dict) -> dict:
    """Drop bulky nav_history for default stdout."""
    return {k: v for k, v in kline.items() if k != "nav_history"}


def _share_history_summary(sh: dict) -> dict:
    """Drop detail rows, keep summary + row_count for compact stdout."""
    return {k: v for k, v in sh.items() if k != "rows"}


def cmd_report(symbol: str, *, as_json: bool, with_nav: bool,
               history: bool = False, history_days: int = 250) -> int:
    symbol = symbol.strip()
    if not symbol.isdigit() or len(symbol) != 6:
        print(f"错误: 需要 6 位数字代码，收到 {symbol!r}", file=sys.stderr)
        return 2

    prefetch_etf_spot()
    profile = query_etf_data(symbol)
    quote = query_etf_quote(symbol)
    kline = query_etf_kline(symbol)
    share_history = query_etf_share_history(symbol, days=20)

    # R11a: --history 时取历史行情深度（nav 链路优先，失败回退 baostock）+ 历史统计
    history_block = None
    if history:
        hist = query_etf_kline_history(symbol, days=history_days)
        hist_stats = (
            compute_history_stats(hist.get("rows") or [])
            if hist.get("status") == "available" else None
        )
        history_block = {"history": hist, "stats": hist_stats}

    payload = {
        "skill": "invest-a-etf",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "symbol": symbol,
        "index_code": CSINDEX_MAP.get(symbol),
        "profile": profile,
        "quote": quote,
        "kline": kline if with_nav else _kline_summary(kline),
        "share_history": _share_history_summary(share_history) if not with_nav else share_history,
        "history": history_block,
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
            # T+1 延迟：份额/资金流字段可能为空
            share_str = f"{sc:>+8.0f}" if sc is not None else "       ⏳"
            flow_str = f"{fe:>+7.2f}" if fe is not None else "     ⏳"
            dir_str = dr if dr else ("⏳ T+1 待确认" if sc is None else "?")
            to_str = f"{to:>6.2f}" if to is not None else "    ⏳"
            print(f"  {r['date']:<10s} "
                  f"{r.get('open', '-'):>6} "
                  f"{r.get('high', '-'):>6} "
                  f"{r.get('low', '-'):>6} "
                  f"{r.get('close', '-'):>6} "
                  f"{r.get('pct_chg', 0):>+7.2f} "
                  f"{r.get('amount', 0) or 0:>7.2f} "
                  f"{to_str} "
                  f"{share_str} "
                  f"{flow_str}  "
                  f"{dir_str}")
        s = share_history.get("summary", {})
        print(f"  资金趋势: {s.get('trend', '?')} | {s['row_count']}日合计: {s.get('total_flow_est', 0):+.2f} 亿 | "
              f"日均成交: {s.get('avg_amount_e', 0):.2f} 亿 | 份额变化: {s.get('share_total_change', 0):+.0f} 万份")
    else:
        print(f"  不可用: {share_history.get('note', '未知')}")
    print()
    print("> 完整叙事请按 skills/invest-a-etf/references/report-template.md 合成。")
    print("> ⚠️ 不构成投资建议。")
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


def cmd_collect_weekly() -> int:
    """手动触发行业 PE 周度采集。"""
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
    return 0


def cmd_share_snap(symbol: str) -> int:
    """保存单只或全部已映射 ETF 的份额快照。"""
    from etf_data import save_etf_share_snapshot

    if symbol == "all":
        symbols = list(set(ETF_HEDGE_MAP.keys()) | set(CSINDEX_MAP.keys()))
        symbols.sort()
        ok = 0
        for sym in symbols:
            snap = save_etf_share_snapshot(sym)
            status = f"AUM {snap['aum']:.2f} 亿" if snap else "跳过(非交易日?)"
            print(f"  {sym}: {status}")
            if snap:
                ok += 1
        print(f"完成: {ok}/{len(symbols)} 只 ETF 份额已保存")
        return 0 if ok > 0 else 1

    snap = save_etf_share_snapshot(symbol)
    if snap:
        print(f"{symbol}: 份额 {snap['shares']:.0f}, AUM {snap['aum']:.2f} 亿 (日期 {snap['date']})")
        return 0
    print(f"{symbol}: 跳过（非交易日或数据不可用）")
    return 1


def cmd_share_flow(symbol: str, days: int = 60) -> int:
    """查询 ETF 份额变化趋势。"""
    from etf_data import etf_share_flow

    flow = etf_share_flow(symbol, days=days)
    if flow.get("note"):
        print(f"{symbol}: {flow['note']}")
        return 0

    print(f"{symbol} 份额变化趋势（{flow.get('history_count', 0)} 条记录）:")
    print(f"  当前份额: {flow.get('shares_current', 0):.0f}")
    print(f"  当前 AUM: {flow.get('aum_current', 0):.2f} 亿")
    for window, label in [(5, "5日"), (20, "20日"), (60, "60日")]:
        chg = flow.get(f"share_change_{window}d")
        flow_est = flow.get(f"flow_est_{window}d")
        if chg is not None and flow_est is not None:
            direction = "流入" if flow_est > 0 else "流出"
            print(f"  {label}: 份额 {chg:+.0f}, 估算资金 {flow_est:+.2f} 亿 ({direction})")
        else:
            print(f"  {label}: 数据不足（需 ≥{window+1} 条记录）")
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

    sub.add_parser("diagnose", help="检查依赖与映射表")

    sub.add_parser("industry-pe", help="申万一级行业 PE/PB 一览")
    sub.add_parser("collect-weekly", help="手动触发行业 PE 周度采集")

    p_snap = sub.add_parser("share-snap", help="保存 ETF 份额快照")
    p_snap.add_argument("symbol", help="6 位 ETF 代码（或 'all' 采集所有已映射 ETF）")

    p_flow = sub.add_parser("share-flow", help="查询 ETF 份额变化趋势")
    p_flow.add_argument("symbol", help="6 位 ETF 代码")
    p_flow.add_argument("--days", type=int, default=60, help="回溯天数（默认 60）")

    args = parser.parse_args(argv)
    if args.cmd == "report":
        return cmd_report(args.symbol, as_json=args.json, with_nav=args.with_nav,
                          history=args.history, history_days=args.history_days)
    if args.cmd == "diagnose":
        return cmd_diagnose()
    if args.cmd == "industry-pe":
        return cmd_industry_pe()
    if args.cmd == "collect-weekly":
        return cmd_collect_weekly()
    if args.cmd == "share-snap":
        return cmd_share_snap(args.symbol)
    if args.cmd == "share-flow":
        return cmd_share_flow(args.symbol, args.days)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
