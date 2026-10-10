#!/usr/bin/env python3
"""invest-a-etf — ETF 研究数据 CLI.

用法::

    uv run python skills/invest-a-etf/scripts/etf.py report 563300
    uv run python skills/invest-a-etf/scripts/etf.py report 563300 --json
    uv run python skills/invest-a-etf/scripts/etf.py html 563300 --md reports/.../xxx.md
    uv run python skills/invest-a-etf/scripts/etf.py diagnose
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import webbrowser
from pathlib import Path

_LIB_DIR = Path(__file__).resolve().parent / "lib"
if str(_LIB_DIR) not in sys.path:
    sys.path.insert(0, str(_LIB_DIR))

from etf_data import (  # noqa: E402
    CSINDEX_MAP,
    ETF_HEDGE_MAP,
    build_data_status,
    compute_etf_tracking,
    compute_history_stats,
    prefetch_etf_spot,
    query_etf_data,
    query_etf_est_net_assets,
    query_etf_fees,
    query_etf_holdings,
    query_etf_kline,
    query_etf_kline_history,
    query_etf_quote,
    query_etf_share_history,
)
from lib.nums import safe_float  # noqa: E402


def _fmt4(v) -> str:
    """渲染 round 4 位：NAV close 原始 float 直出会出现 1.4362000000000001
    精度尾巴（P1-3，515050 annual_high 现场）。"""
    f = safe_float(v)
    return "—" if f is None else f"{f:.4f}"


def _fmt2(v) -> str:
    """渲染 round 2 位（百分比类字段）。"""
    f = safe_float(v)
    return "—" if f is None else f"{f:.2f}"


def _kline_summary(kline: dict) -> dict:
    """Drop bulky nav_history for default stdout."""
    return {k: v for k, v in kline.items() if k != "nav_history"}


# 事件文件缺省目录（R11b）：skills/invest-a-etf/events/{symbol}.json
_EVENTS_DIR = Path(__file__).resolve().parent.parent / "events"


def _build_events_block(symbol: str, events_path: str | None,
                        history_block: dict | None) -> dict:
    """R11b: 事件文件加载 + 事件-价格对照（±1 交易日对齐）。无文件不阻断。

    A05-1 R3（v0.3.2）：异动日清单复用已持有的 ``stats.big_move_days``
    （compute_history_stats 与 detect_big_move_days 同口径，冻结 fixture 测
    test_detect_big_move_days_frozen_588000 已证），不再对同一 rows 第二次扫描；
    ``detect_big_move_days`` 的 custom threshold 契约保留不删。
    """
    from etf_timeline import align_events_with_price, load_events_file

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
        move_days = history_block["stats"].get("big_move_days") or []
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


def _collect_report_payload(symbol: str, *, with_nav: bool,
                            history: bool = False, history_days: int = 250,
                            events_path: str | None = None, playbook: bool = False,
                            include_extra_dims: bool = False,
                            index_exposure: bool = False) -> dict:
    """采集并组装 report 数据快照（cmd_report 用；symbol 已校验）。

    ``include_extra_dims=True``（`--payload-out` 冻结 payload 时）时一并采集
    HTML 渲染所需的 holdings/peers/sector_flow 三维（单源失败占位不阻断），
    使冻结 payload 成为 HTML 的唯一完整输入。
    ``index_exposure=True``（`--index-exposure`）时按需查询指数样本/权重
    （csindex 公开文件）并**随 payload 冻结**——Fit 章节使用该数据时必须传，
    否则报告引用的输入不在同代封存内。
    """
    prefetch_etf_spot()
    profile = query_etf_data(symbol)
    # 先查询再入库：index_pe_pct 分位只对「不含今日行」的历史序列计算（避免今日
    # 自投成 100%/5% 假象），与 journal 路径（不 persist-first）语义一致；写库放
    # 查询后，下次报告的分位即含今日（幂等，失败不阻断报告）
    idx = CSINDEX_MAP.get(symbol)
    # R2 修正（ETF 设计 §5）：单只 report 只回填**本报告所需指数**——旧行为
    # `_persist_index_pe(None)` 会顺带请求/写入 CSINDEX_MAP 全部指数，与
    # 「单报告只取该报告所需指数」的设计冲突（负责人反例：参数 [None]）。
    # 全量回填保留在既有批量维护入口 `collect-weekly`（显式、不随报告触发）。
    res = _persist_index_pe([idx]) if idx else {"error": None}
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

    # B02：跟踪质量块（TD/TE 契约见 etf_data 段首注释）+ 费用 + 同日份额×NAV 估算
    tracking = compute_etf_tracking(symbol)
    fees = query_etf_fees(symbol)
    est_net_assets = query_etf_est_net_assets(symbol)
    profile["tracking"] = tracking
    profile["fees"] = fees
    profile["est_net_assets"] = est_net_assets

    from dates import shanghai_now

    payload = {
        "skill": "invest-a-etf",
        "generated_at": shanghai_now().isoformat(),
        "symbol": symbol,
        "index_code": CSINDEX_MAP.get(symbol),
        "profile": profile,
        "quote": quote,
        "kline": kline if with_nav else _kline_summary(kline),
        "share_history": share_history if with_nav else _share_history_summary(share_history),
        "history": history_block if history else None,
        "events": events_block,
        "playbook": playbook_block,
        "disclaimer": "研究数据快照，不构成投资建议。",
    }
    if include_extra_dims:
        payload.update(_html_extra_dims(symbol))
    if index_exposure:
        from etf_data import query_etf_index_exposure  # noqa: E402

        payload["index_exposure"] = query_etf_index_exposure(symbol)
    # B06 数据侧：维度五态 + 实际尝试源（含 extra dims / index_exposure 后的实况）
    payload["data_status"] = build_data_status(
        identity=profile.get("identity"), profile=profile, quote=quote, kline=kline,
        share_history=share_history, history_block=history_block,
        events_block=events_block, tracking=tracking, fees=fees,
        est_net_assets=est_net_assets,
        extra_dims=payload if include_extra_dims else None,
        index_exposure=payload.get("index_exposure"),
    )
    return payload


def cmd_report(symbol: str, *, as_json: bool, with_nav: bool,
               history: bool = False, history_days: int = 250,
               events_path: str | None = None, playbook: bool = False,
               payload_out: str | None = None,
               index_exposure: bool = False) -> int:
    symbol = symbol.strip()
    if not symbol.isdigit() or len(symbol) != 6:
        print(f"错误: 需要 6 位数字代码，收到 {symbol!r}", file=sys.stderr)
        return 2

    # compact 打印体始终读完整 share_history rows（HEAD 行为，与 with_nav 无关）；
    # with_nav 仅塑造 JSON payload 形状（--with-nav 才保留全量）
    payload = _collect_report_payload(
        symbol, with_nav=True if not as_json else with_nav,
        history=history, history_days=history_days,
        events_path=events_path, playbook=playbook,
        include_extra_dims=payload_out is not None,
        index_exposure=index_exposure,
    )
    # 展开回局部变量：compact 打印体保持不变（零回归）
    profile = payload["profile"]
    quote = payload["quote"]
    kline = payload["kline"]
    share_history = payload["share_history"]
    history_block = payload["history"]
    events_block = payload["events"]
    playbook_block = payload["playbook"]

    # B05：冻结 payload（同代输入，HTML 唯一输入）——fail-loud，不静默
    if payload_out is not None:
        from etf_generation import seal_etf_payload
        from snapshot_core import default_serializer

        seal_etf_payload(payload)
        out = Path(payload_out)
        if out.parent and not out.parent.is_dir():
            print(f"错误: --payload-out 父目录不存在: {out.parent}", file=sys.stderr)
            return 2
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=default_serializer) + "\n",
                       encoding="utf-8")
        print(f"payload 已冻结: {out}（payload_hash={payload['_meta']['payload_hash'][:12]}…）",
              file=sys.stderr)

    if as_json:
        from snapshot_core import default_serializer

        print(json.dumps(payload, ensure_ascii=False, indent=2, default=default_serializer))
        return 0

    # Compact human-readable summary for Claude / terminal
    print(f"# invest-a-etf report · {symbol}")
    print(f"generated_at: {payload['generated_at']}")
    print()
    hc = profile.get("hedge_coverage") or {}
    identity = profile.get("identity") or {}
    axes = identity.get("axes") or {}
    labels = identity.get("labels") or {}
    print("## identity")
    print(f"  axes:              asset={axes.get('asset_type')} channel={axes.get('channel')} "
          f"strategy={axes.get('strategy')} ({labels.get('asset_type', '')}/"
          f"{labels.get('channel', '')}/{labels.get('strategy', '')})")
    ver = (identity.get("verification") or {}).get("status")
    ti = identity.get("tracked_index") or {}
    print(f"  verification:      {ver}"
          + (f"  tracked_index={ti.get('name')}({ti.get('code')})" if ti else ""))
    app = identity.get("applicability") or {}
    print(f"  applicability:     index_pe={app.get('index_pe')} "
          f"stock_pe_drilldown={app.get('stock_pe_drilldown')}")
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
    est = profile.get("share_price_mv_est") or {}
    if est:
        print(f"  scale:             {est.get('value_yi')} 亿元（{est.get('caliber')}；"
              "非官方资产净值）")
    else:
        print(f"  scale:             不可得（spot 源未取到份额/价格）")
    ena = profile.get("est_net_assets") or {}
    if ena.get("status") == "ok":
        print(f"  est_net_assets:    {ena.get('value_yi')} 亿元 @ {ena.get('as_of')}"
              "（同日份额×单位净值估算，非官方披露规模）")
    else:
        print(f"  est_net_assets:    {ena.get('status')}（{ena.get('note')}）")
    tr = profile.get("tracking") or {}
    if tr.get("status") == "available":
        print(f"  tracking:          TD {tr.get('td_pct')}% / TE {tr.get('te_pct_annualized')}%"
              f"（年化，样本区间 n={tr.get('sample_n')}）")
    else:
        dev = tr.get("price_benchmark_deviation") or {}
        extra = (f"；价格基准偏离（研究数值，非确认 TD/TE）: "
                 f"TD {dev.get('td_pct')}%（窗口端点） / TE {dev.get('te_pct_annualized')}%"
                 f"（抽样区间 n={dev.get('sample_n')}）" if dev else "")
        print(f"  tracking:          {tr.get('status')}（{tr.get('note')}）{extra}")
    fees = profile.get("fees") or {}
    if fees.get("status") == "ok":
        # R3：current_rate_verified=报告日现行已核（版本已读见 source.version/as_of；
        # 未核形态区分见 query_etf_fees——不再有 currency_verified 第二套布尔）
        cur = ("报告日现行费率已核" if fees.get("current_rate_verified")
               else "报告日现行费率未核")
        print(f"  fees:              " + " / ".join(
            f"{i['name']} {i['annual_rate_pct']}%" for i in fees["items"])
            + f"（{cur}；{fees['source'].get('version') or fees['source'].get('as_of')}）")
    else:
        print(f"  fees:              unverified（未收录官方材料）")
    print(f"  hedge_coverage:    {hc.get('coverage')} ({hc.get('index')})"
          f"  verification={((hc.get('verification') or {}).get('status'))}")
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
    idx_ma20, idx_ma60 = kline.get('index_ma20'), kline.get('index_ma60')
    # P1-4：指数日 K 获取失败静默降级时，标注「不可得」而非裸 None
    if idx_ma20 is None and idx_ma60 is None:
        print(f"  ma20/ma60 (index): 不可得（指数日 K 获取失败，静默降级）")
    else:
        print(f"  ma20/ma60 (index): {idx_ma20} / {idx_ma60}")
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
            # P1-3：close 渲染 round 4 位——原始 float 直出出现过
            # 1.4362000000000001 类精度尾巴（515050 现场）
            print(f"  annual_high/low:   {_fmt4(ah['close'])} @ {ah['date']} / {_fmt4(al['close'])} @ {al['date']}")
            md = stats["max_drawdown"]
            print(f"  max_drawdown:      {_fmt2(md['drawdown_pct'])}%  "
                  f"({_fmt4(md['peak_close'])} @ {md['peak_date']} → {_fmt4(md['trough_close'])} @ {md['trough_date']})")
            print(f"  big_move_days:     {len(stats['big_move_days'])} 个 |change_pct|≥5% 交易日")
            print(f"  ma20/60/120:       {stats['ma20']} / {stats['ma60']} / {stats['ma120']}")
            print(f"  current_vs_high/low: {_fmt2(stats['current_vs_high_pct'])}% / {_fmt2(stats['current_vs_low_pct'])}%")
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
    # 路径须按运行布局打印（包内 references/ 就在包根下，没有 skills/ 目录）
    from skill_paths import skill_relpath

    _tpl = skill_relpath(__file__, "references/report-template.md")
    print(f"> 完整叙事请按 {_tpl} 合成。")
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
        from dates import shanghai_now

        payload = {
            "skill": "invest-a-etf",
            "generated_at": shanghai_now().isoformat(),
            "symbol": symbol,
            "holdings": data,
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
        return 0

    print(f"# holdings · {symbol}")
    print(f"scope: {data.get('holding_scope')}")   # B01-2：前十 ≠ 完整持仓
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
    print(f"集中度(引擎): top1 {t1}% | top5 {t5}% | top10 {t10}%"
          "（分母=占基金资产净值；仅披露范围，缺口不按 0/不重归一化）")
    cov = data.get("coverage") or {}
    if cov.get("uncovered_note"):
        print(f"coverage: {cov['uncovered_note']}")
    pcf = data.get("pcf") or {}
    if pcf.get("status"):
        print(f"pcf: {pcf['status']}（{pcf.get('note')}）")
    clusters = data.get("clusters") or []
    if clusters:
        print("子环节聚类合计(引擎):")
        for c in clusters:
            members = ", ".join(f"{m['name']} {m['pct']:.2f}" for m in c["members"])
            print(f"  {c['cluster']} {c['sum_pct']:.2f}%: {members}")
    print(f"note: {data['note']}")
    # 提示仅在存在未归类持仓时输出（全部映射时避免误导报告层重复「AI 归类」）
    if any(c.get("cluster") == "未归类" for c in clusters):
        print("> 未映射股票归入「未归类」；报告层如需补充归类须标注「AI 归类」")
    return 0


def cmd_peers(symbol: str, *, as_json: bool, peers_str: str | None,
              same_index_str: str | None = None, index_code: str | None = None,
              evidence_path: str | None = None) -> int:
    """R13: 赛道资金流对比 + 相对强弱；B02: 可选同指数实现质量有限比较。"""
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
    # B02 同指数实现质量比较：必须显式给指数代码与**官方材料证据文件**
    # （同指数身份/币种/费率来自本次读取的原文；URL 存在 ≠ 已读，不猜）
    same_index = None
    if same_index_str:
        if not index_code:
            print("错误: --same-index 需要 --index-code（同指数比较的指数代码）", file=sys.stderr)
            return 2
        if not evidence_path:
            print("错误: --same-index 需要 --evidence-json（本次读取的官方材料证据；"
                  "未读材料不得推定同指数/费率）", file=sys.stderr)
            return 2
        si_peers = [p.strip() for p in same_index_str.split(",") if p.strip()]
        bad = [p for p in si_peers if not (p.isdigit() and len(p) == 6)]
        if bad:
            print(f"错误: --same-index 非法代码: {bad}（需 6 位数字）", file=sys.stderr)
            return 2
        ev_file = Path(evidence_path)
        if not ev_file.is_file():
            print(f"错误: 证据文件不存在: {ev_file}", file=sys.stderr)
            return 2
        try:
            evidence = json.loads(ev_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"错误: 证据文件读取/解析失败: {exc}", file=sys.stderr)
            return 2
        if not isinstance(evidence, dict):
            print("错误: 证据文件须为 {symbol: {...}} 对象", file=sys.stderr)
            return 2
        from etf_peers import same_index_quality_comparison

        same_index = same_index_quality_comparison(
            symbol, si_peers, index_code=index_code,
            index_name=(evidence.get(symbol) or {}).get("index_name"),
            evidence=evidence)
    if as_json:
        from dates import shanghai_now

        payload = {
            "skill": "invest-a-etf",
            "generated_at": shanghai_now().isoformat(),
            "symbol": symbol,
            "peers": data,
        }
        if same_index is not None:
            payload["same_index_comparison"] = same_index
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
        return 0
    if same_index is not None:
        print("## 同指数实现质量有限比较 (B02 · 不排名)")
        print(f"  index: {same_index.get('index_code')} "
              f"{same_index.get('index_name') or ''} | status={same_index.get('status')}")
        cw = same_index.get("common_window") or {}
        print(f"  同窗: {cw.get('start', '—')} ~ {cw.get('end', '—')} "
              f"({cw.get('n_dates', '—')} 个交易日)")
        print(f"  {'代码':<8s} {'名称':<16s} {'同窗收益%':>9s} {'费率':>16s} {'状态'}")
        for row in same_index.get("rows") or []:
            ret = (f"{row['window_return_pct']:+.2f}"
                   if row.get("window_return_pct") is not None else "⏳")
            fees = row.get("fees") or {}
            fee_txt = (" ".join(f"{i.get('name')}{i.get('annual_rate_pct')}%"
                                for i in fees.get("items") or [])
                       if fees.get("status") == "ok" else fees.get("status", "—"))
            print(f"  {row['symbol']:<8s} {(row.get('name') or '-'):<16s} {ret:>9s} "
                  f"{fee_txt:>16s} {row.get('status')}")
        for gap in same_index.get("gaps") or []:
            print(f"  ⚠ {gap}")
        print(f"  note: {same_index.get('no_ranking_note')}")

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


def cmd_futures_basis(symbol: str, *, as_json: bool) -> int:
    """ETF 股指期货动态基差状态（F 系列，状态度量非预测）。"""
    from dates import shanghai_now  # noqa: E402
    from futures_basis import query_futures_basis  # noqa: E402

    result = query_futures_basis(symbol)
    payload = {"skill": "invest:a-etf", "generated_at": shanghai_now().isoformat(),
               "symbol": symbol, "futures_basis": result}
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, default=str))
        return 0
    if not result.get("available"):
        print(f"⚠️ {symbol} 期货基差不可得：{result.get('note', '')}")
        return 1
    print(f"=== {symbol} 动态基差（{result['futures_symbol']}，{result['date']}）===")
    print(f"  当前基差: {result['current_basis_pct']}%（{result['current_basis_pts']} 点，合约 {result['contract']}）")
    print(f"  历史分位: {result['percentile']}%（中位数 {result['median_basis_pct']}%，n={result['n_history']}）")
    print(f"  演变分布参照: {result['distribution_ref']}")
    print(f"  ⚠️ {result['note']}")
    return 0


def _html_extra_dims(symbol: str) -> dict:
    """HTML 追加三维度（R12 holdings / R13 peers / R15 sector_flow）。

    单源失败 → {"available": False} 占位，不阻断渲染（与报告采集的独立记录约定一致）。
    """
    dims: dict[str, dict] = {}
    for kind in ("holdings", "peers", "sector_flow"):
        try:
            if kind == "holdings":
                dims[kind] = query_etf_holdings(symbol)
            elif kind == "peers":
                from etf_peers import query_etf_peers
                dims[kind] = query_etf_peers(symbol, None)
            else:
                from sector_flow import query_sector_flow
                dims[kind] = query_sector_flow(symbol)
        except Exception as exc:
            dims[kind] = {"available": False, "note": f"{kind} 采集失败: {exc}"}
    return dims


def _resolve_md_path(symbol: str, md_path: str | None) -> tuple[Path | None, str | None]:
    """解析报告 md 路径：--md 显式校验存在；缺省 glob reports/{symbol}-*/*.md 取最新。

    成功 → (Path, None)；失败 → (None, 错误信息)。
    """
    if md_path:
        p = Path(md_path)
        if not p.is_file():
            return None, f"--md 指定的文件不存在: {p}"
        return p, None
    # 按文件名（时间戳 = 字典序 = 时间序）排序取全局最新；按完整路径排
    # 序会受目录名干扰（F1-7 改名遗留目录「通信ETF华夏」字典序靠后但报告旧）
    # 复盘纪要自身落在同一目录，**不是报告**：不排除会让 review/--init 把上次
    # 生成的纪要当成「最新报告」（实测踩过：report_ts 取成 20260911-review）
    from decision_review import REVIEW_NAME_RE

    candidates: list[Path] = []
    for d in Path("reports").glob(f"{symbol}-*"):
        candidates.extend(p for p in d.glob("*.md")
                          if not REVIEW_NAME_RE.match(p.name))
    candidates.sort(key=lambda p: p.name)
    if not candidates:
        return None, (
            f"未找到 {symbol} 的报告 md（reports/{symbol}-*/*.md）；"
            "请先用 report 流程生成，或 --md 显式指定"
        )
    return candidates[-1], None


def _resolve_out_path(md_path: Path, out_path: str | None) -> Path:
    """HTML 输出路径：--out 显式；缺省与 md 同目录同名 .html。"""
    return Path(out_path) if out_path else md_path.with_suffix(".html")


def _open_browser(html_path: Path) -> bool:
    """引擎进程内 webbrowser.open（stdlib，harness 无关；WB 缺 shell open 也一致）。

    失败仅提示不阻断（路径已打印，rc 仍 0）。
    """
    try:
        return bool(webbrowser.open(html_path.resolve().as_uri(), new=2))
    except Exception as exc:
        print(f"⚠️ 自动打开浏览器失败: {exc}", file=sys.stderr)
        return False


def cmd_html(symbol: str, *, md_path: str | None = None, out_path: str | None = None,
             no_open: bool = False, payload_path: str | None = None) -> int:
    """生成交互式 HTML 报告：冻结 payload（同代输入）+ md 分析全文原样嵌入。

    B05（v0.3.2 纠偏）：**HTML 只读冻结 payload 渲染**——零网络、零 DB 写。
    `--payload` 必填且与 `--md` 同 ts 同目录：缺 payload / JSON 损坏 / symbol
    不一致 / 哈希不符（篡改）/ 与报告 ts 不对应 → 全部 fail-loud rc 2；
    不再保留「缺 payload → 重采数据」的回退路径（重采渲染不受原报告数字
    签认保护，属已退出的旧错误路径）。
    """
    symbol = _validate_symbol(symbol)
    if symbol is None:
        return 2
    md, err = _resolve_md_path(symbol, md_path)
    if err:
        print(f"错误: {err}", file=sys.stderr)
        return 1

    from etf_generation import load_etf_payload  # noqa: E402

    if not payload_path:
        print(
            "错误: html 需要 --payload <冻结 payload>（同代输入）。\n"
            "     先运行: etf.py report SYMBOL --json --payload-out "
            "reports/{symbol}-*/<ts>.payload.json\n"
            "     HTML 不再重采数据（重采渲染非同代，已退出该路径）。",
            file=sys.stderr,
        )
        return 2
    payload_file = Path(payload_path)
    payload, errs = load_etf_payload(payload_file)
    if payload is None:
        print(f"错误: payload 不可用: {'；'.join(errs)}", file=sys.stderr)
        return 2
    if payload.get("symbol") != symbol:
        print(f"错误: payload.symbol={payload.get('symbol')!r} ≠ 请求标的 {symbol!r}",
              file=sys.stderr)
        return 2
    expected_name = f"{md.stem}.payload.json"
    if payload_file.name != expected_name or payload_file.parent != md.parent:
        print(
            f"错误: payload 与报告 md 不配对：期望 {expected_name}（与 md 同目录），"
            f"实际 {payload_file.name}@{payload_file.parent}",
            file=sys.stderr,
        )
        return 2

    try:
        from etf_html import render_etf_html  # noqa: E402
    except ImportError as exc:
        print(f"错误: etf_html 渲染模块不可用: {exc}", file=sys.stderr)
        return 1

    md_text = md.read_text(encoding="utf-8")
    meta = payload.get("_meta") or {}
    generation = {
        "same_generation": True,
        "payload_hash": meta.get("payload_hash"),
        "frozen_at": meta.get("frozen_at"),
        "generated_at": payload.get("generated_at"),
        "payload_file": str(payload_file),
    }
    try:
        html_text = render_etf_html(payload, md_text=md_text, generation=generation)
    except Exception as exc:
        print(f"错误: HTML 渲染失败（md 语法可能超出渲染器子集）: {exc}", file=sys.stderr)
        return 1

    out = _resolve_out_path(md, out_path)
    out.write_text(html_text, encoding="utf-8")
    print(f"HTML 报告: {out}")
    print(f"同代输入: payload_hash={str(meta.get('payload_hash'))[:12]}…"
          f"（frozen_at={meta.get('frozen_at')}）")

    if no_open:
        return 0
    if not _open_browser(out):
        print(f"请在浏览器手动打开: {out}", file=sys.stderr)
    return 0


def cmd_sector_flow(symbol: str, *, as_json: bool) -> int:
    """R15: 关联行业资金流 + 趋势（同花顺 3/5/10 日，大单口径亿元）。"""
    symbol = _validate_symbol(symbol)
    if symbol is None:
        return 2
    try:
        from sector_flow import query_sector_flow, sector_flow_stale_note
    except ImportError as exc:
        print(f"sector_flow 模块不可用: {exc}（请检查路径配置）")
        return 1
    data = query_sector_flow(symbol)
    stale = sector_flow_stale_note(data.get("as_of"))   # T7-1：as_of 滞后预警
    if as_json:
        from dates import shanghai_now

        payload = {
            "skill": "invest-a-etf",
            "generated_at": shanghai_now().isoformat(),
            "symbol": symbol,
            "sector_flow": data,
            "warnings": [stale] if stale else [],
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
        return 0
    if stale:
        print(f"⚠ {stale}", file=sys.stderr)

    sw_name = data.get("sw_name") or "-"
    sw_code = data.get("sw_code") or "-"
    as_of = data.get("as_of") or "⏳"
    print(f"# sector-flow · {symbol}  ({sw_name} {sw_code})  as_of={as_of}")
    if not data["available"]:
        print(f"⏳ {'; '.join(data.get('notes') or [])}")
        return 0
    print(f"  {'THS行业':<8s} {'今日':>8s} {'3日':>8s} {'5日':>8s} {'10日':>9s} "
          f"{'10日涨跌%':>9s}  趋势标签 / 详情")
    for r in data["industries"]:
        def f(v):
            return f"{v:+.2f}" if v is not None else "   ⏳"
        t5 = f"{r['trend_5d']:+.2f}" if r.get("trend_5d") is not None else "   —"
        turn = r.get("turn_5d") or ""
        span = r.get("trend_span_days")
        if span and span != 7:
            turn = f"{turn}（跨度 {span} 日）"
        print(f"  {r['industry']:<8s} {f(r['net_1d']):>8s} {f(r['net_3d']):>8s} "
              f"{f(r['net_5d']):>8s} {f(r['net_10d']):>9s} {f(r['chg_10d']):>9s}  "
              f"{r['trend_label']} / {r['trend_detail']}  5日Δ {t5} {turn}")
    print(f"history_days: {data['history_days']}")
    for n in data.get("notes") or []:
        print(f"note: {n}")
    return 0


def cmd_collect_sector_flow() -> int:
    """R15 每日采集：同花顺行业资金流 90 行业×4 窗口 → sector_flow_snapshots（幂等）。

    采集后执行 SW_TO_THS_INDUSTRY 映射自检（check_mapping_coverage + 反向
    check_snapshot_drift）：名单不在最新快照的行业名 / 快照新增未映射行业名
    输出警告，供映射表在线核对；单窗口取数失败告警且跳过自检（名单不全防误报）。
    """
    try:
        from sector_flow import (check_mapping_coverage, check_snapshot_drift,
                                 fetch_sector_flow_snapshot, load_drift_baseline,
                                 save_drift_baseline, save_sector_flow_snapshot)
    except ImportError as exc:
        print(f"sector_flow 模块不可用: {exc}（请检查路径配置）")
        return 1
    snapshot = fetch_sector_flow_snapshot()
    errs = snapshot.get("errors") or []
    result = save_sector_flow_snapshot(snapshot)
    if result.get("error"):
        print(f"采集失败: {result['error']}")
        return 1
    if result.get("skipped"):
        # T7-2（R1 审查 F6 修正）：停更信号挂在跳过路径——权威交易日 + 全等 →
        # stale_suspect=True（写前 C5 门已丢弃全等快照，写后比较永不触发）
        mark = "⚠ " if result.get("stale_suspect") else ""
        print(f"{mark}跳过: {result['note']}")
        return 0  # 快照为旧数据/非交易日，名单自检基于旧名单无意义（R16）
    partial = "（部分窗口失败）" if errs else ""
    print(f"完成: {result['rows_saved']} 行已写入 sector_flow_snapshots"
          f"（日期 {result['date']}）{partial}")
    if errs:
        print(f"⚠ 部分窗口取数失败: {'; '.join(errs)}")
        return 1  # 部分失败 → 非零退出码，cron 可按键告警（R16）
    # R15 C6：映射自检（部分窗口失败时名单不全，跳过防误报）
    missing = check_mapping_coverage(snapshot)
    if missing:
        print(f"⚠ 映射自检: 以下 THS 行业不在最新名单（SW_TO_THS_INDUSTRY 需核对）: "
              f"{', '.join(missing)}")
    # 漂移自检（R16 基线制）：首次建立基线不告警，后续仅报告相对基线的新增
    # 未映射行业（映射表仅 39/约 90 行业，全量告警每次必刷 ~50 条假警告）
    baseline = load_drift_baseline()
    if baseline is None:
        n = save_drift_baseline(snapshot)
        if n is None:
            print("⚠ 漂移基线建立失败（跳过，下次采集重试，不阻断）")
        else:
            print(f"ℹ 已建立漂移基线（{n} 个未映射行业），后续仅报告新增（不阻断）")
    else:
        drift = check_snapshot_drift(snapshot, baseline=baseline)
        if drift:
            print(f"⚠ 映射自检（新增未映射行业）: 共 {len(drift)} 个不在 "
                  f"SW_TO_THS_INDUSTRY: {', '.join(drift)}")
    return 0


def cmd_industry_pe() -> int:
    """打印申万一级行业 PE/PB 一览。"""
    try:
        from industry_snapshot import industry_snapshot_stale_note, list_industry_snapshot
    except ImportError:
        print("industry_snapshot 模块不可用（请检查路径配置）")
        return 1
    rows = list_industry_snapshot()
    if not rows:
        print("无行业 PE 数据。请先运行 `etf.py collect-weekly` 采集。")
        print("（首次采集后需等待每周五收盘后自动更新，或手动触发。）")
        return 0
    # T7-3（R1 审查 F10/F2 修正）：日期取全表最大（原取 PE 最高行）；滞后按源发布日期判
    latest_date = max((r.get("date") or "" for r in rows), default="")
    latest_src = max((r.get("src_date") or "" for r in rows), default="")
    stale = industry_snapshot_stale_note(latest_date or None, src_date=latest_src or None)
    if stale:
        print(f"⚠ {stale}")   # 快照陈旧提示（输出在表前，防读到旧值不自知）
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

    调用方约定（R2）：**单只 report 只传本标的指数**（`[CSINDEX_MAP[symbol]]`）；
    `None`（=CSINDEX_MAP 全部）仅供批量维护入口 `collect-weekly` 使用，
    报告路径不再触发全量回填。

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
        from industry_snapshot import collect_industry_weekly, weekly_unchanged_vs_previous
    except ImportError:
        print("industry_snapshot 模块不可用（请检查路径配置）")
        return 1
    print("采集申万行业 PE/PB 周度快照...")
    result = collect_industry_weekly()
    if result.get("error"):
        print(f"采集失败: {result['error']}")
        return 1
    print(f"完成: {result['industries_saved']} 个行业已写入 industry_weekly（日期 {result['date']}）")
    if weekly_unchanged_vs_previous(result["date"]):
        print(f"⚠ {result['industries_saved']} 行业数值与上一期全同，"
              "疑数据源停更（写入已完成，请人工核对）")
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


_COVERAGE_LEVELS = ("high", "partial", "low", "none", "unknown")


def _hedge_md_rows(text: str) -> dict[str, str]:
    """解析 etf-hedge-map.md 的映射表行 → {code: coverage}（只收 6 位代码行）。"""
    rows: dict[str, str] = {}
    for line in text.splitlines():
        if not line.startswith("|"):
            continue
        cells = [c.strip().strip("*").strip() for c in line.strip("|").split("|")]
        if len(cells) < 2 or not re.fullmatch(r"\d{6}", cells[0]):
            continue
        cov = next((c for c in cells if c in _COVERAGE_LEVELS), None)
        if cov:
            rows[cells[0]] = cov
    return rows


def _check_hedge_map_consistency() -> int:
    """代码表 ↔ references/etf-hedge-map.md 一致性（B03）。

    双向核对：MD 缺代码 / MD 多代码 / 覆盖度不一致 → 打印差异并 rc 1。
    （人工副本缺失 = 分发/文档破坏，属确定性错误，fail-loud 不降级。）
    """
    from skill_paths import default_reference, skill_relpath  # noqa: E402

    # 读文件用**绝对路径**（skill_relpath 是给用户看的布局相对路径，CWD 非
    # 布局根时会误判缺失）；报错信息仍打印相对路径便于定位。
    rel = skill_relpath(__file__, "references/etf-hedge-map.md")
    path = Path(default_reference(__file__, "references/etf-hedge-map.md"))
    if not path.is_file():
        print(f"  ❌ 对冲表文档缺失: {rel}（解析为 {path}）")
        return 1
    try:
        md_rows = _hedge_md_rows(path.read_text(encoding="utf-8"))
    except OSError as exc:
        print(f"  ❌ 对冲表文档读取失败: {exc}")
        return 1
    code_keys = set(ETF_HEDGE_MAP)
    md_keys = set(md_rows)
    missing_in_md = sorted(code_keys - md_keys)
    extra_in_md = sorted(md_keys - code_keys)
    cov_mismatch = sorted(
        f"{c}: code={ETF_HEDGE_MAP[c].get('coverage')} md={md_rows[c]}"
        for c in code_keys & md_keys
        if ETF_HEDGE_MAP[c].get("coverage") != md_rows[c]
    )
    print(f"  代码表↔etf-hedge-map.md: code={len(code_keys)} md={len(md_keys)}")
    ok = True
    if missing_in_md:
        ok = False
        print(f"  ❌ 代码有文档无（补文档）: {', '.join(missing_in_md)}")
    if extra_in_md:
        ok = False
        print(f"  ❌ 文档有代码无（补代码或删行）: {', '.join(extra_in_md)}")
    if cov_mismatch:
        ok = False
        print(f"  ❌ 覆盖度不一致（先改代码再同步文档）: {'; '.join(cov_mismatch)}")
    return 0 if ok else 1


def cmd_diagnose() -> int:
    print("invest-a-etf diagnose")
    print(f"  ETF_HEDGE_MAP entries: {len(ETF_HEDGE_MAP)}")
    print(f"  CSINDEX_MAP entries:   {len(CSINDEX_MAP)}")
    rc = _check_hedge_map_consistency()
    try:
        import akshare as ak  # noqa: F401

        print("  akshare:              OK")
    except Exception as exc:
        print(f"  akshare:              FAIL ({exc})")
        return 1
    # smoke: known code in map
    sample = "510300"
    print(f"  sample hedge[{sample}]: {ETF_HEDGE_MAP.get(sample)}")
    if rc != 0:
        print("diagnose: FAIL（对冲表一致性，见上）")
        return 1
    print("diagnose: OK")
    return 0


def cmd_decision(symbol: str, *, from_path: str | None, init: bool,
                 md: str | None = None) -> int:
    """复盘原料 sidecar（decision.json）：初始化模板 / 校验并落盘。

    设计：host-docs/v0.3.0/review-material-design.md D1/D3（用户已批准）。
    sidecar 与报告 md **同目录同 ts**——必须能配到某一份报告，否则 review 无法
    把「当时的假设」和当时的报告对上。故没有报告 md 时**显式失败**，
    不落一个无主的 sidecar。

    校验 fail-loud（退出 2）：LAW 6 要求多情景参考价带假设前提 + 概率权重，
    缺项不得静默落盘。
    """
    from decision_schema import DecisionSchemaError, load_decision_json, minimal_decision

    md_path, err = _resolve_md_path(symbol, md)
    if err:
        print(f"❌ {err}", file=sys.stderr)
        return 2
    assert md_path is not None
    ts = md_path.stem

    if init:
        from dates import shanghai_now  # noqa: E402

        payload = minimal_decision(symbol=symbol, report_ts=ts,
                                   as_of=shanghai_now().date().isoformat())
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    if not from_path:
        print("❌ 需指定 --from <path>（校验并落盘）或 --init（输出模板）",
              file=sys.stderr)
        return 2

    try:
        payload = load_decision_json(from_path)
    except DecisionSchemaError as exc:
        print(f"❌ {exc}", file=sys.stderr)
        return 2

    # 配对守卫：symbol/report_ts 须与目标报告一致，防张冠李戴
    mismatches: list[str] = []
    if payload.get("symbol") != symbol:
        mismatches.append(f"symbol: {payload.get('symbol')!r} ≠ {symbol!r}")
    if payload.get("report_ts") != ts:
        mismatches.append(f"report_ts: {payload.get('report_ts')!r} ≠ {ts!r}")
    if mismatches:
        print("❌ sidecar 与目标报告不配对：" + "；".join(mismatches), file=sys.stderr)
        return 2

    out = md_path.parent / f"{ts}.decision.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    print(f"✅ 复盘原料已落盘: {out}")
    return 0


def _render_generation_check(report_dir: Path) -> str:
    """同代输入配对核查小节（B05/B06；只读，不改历史产物）。

    ``paired`` 只表示输入配对，不表示整体通过——逐代展示 QC 记录侧车状态
    （产物 sha256 绑定与分层签认；改产物后旧记录 stale）。
    """
    from etf_generation import verify_report_generation  # noqa: E402

    gen = verify_report_generation(report_dir)
    lines = ["", "## 同代输入配对核查（引擎）", "",
             "> 只读核验（md/payload/html/decision 配对、payload 哈希、产物 "
             "sha256 与 QC 记录绑定）；旧代无冻结 payload 标 legacy，不等于"
             "新报告允许缺 payload。paired 只表示输入配对，不表示整体通过。", ""]
    if gen.get("error"):
        lines.append(f"- ⚠️ 同代核验失败：{gen['error']}")
        return "\n".join(lines) + "\n"
    for g in gen["generations"]:
        mark = {"paired": "✅", "legacy_no_payload": "ℹ️"}.get(g["status"], "⚠️")
        line = f"- {mark} `{g['ts']}`：{g['status']}"
        if g.get("payload_hash"):
            line += f"（payload {g['payload_hash'][:12]}…）"
        rec = g.get("qc_record") or {}
        if rec:
            st = rec.get("status")
            if st == "valid":
                signoffs = rec.get("signoffs") or {}
                unexec = [k for k, v in signoffs.items()
                          if (v or {}).get("status") == "not_executed"]
                partial = [k for k, v in signoffs.items()
                           if (v or {}).get("status") == "partial"]
                bits = []
                if unexec:
                    bits.append(f"未执行签认: {', '.join(unexec)}")
                if partial:
                    bits.append(f"部分签认: {', '.join(partial)}")
                line += (f"；QC 记录 valid（机器 {rec.get('machine_overall')}；"
                         + ("；".join(bits) if bits else "签认齐") + "）")
            elif st:
                line += f"；QC 记录 {st}"
        if g["issues"]:
            line += "；" + "；".join(g["issues"])
        lines.append(line)
    if not gen["generations"]:
        lines.append("- （无报告 md）")
    return "\n".join(lines) + "\n"


def cmd_review(symbol: str, *, out: str | None = None, md: str | None = None) -> int:
    """复盘纪要：把该标的的报告序列 + 复盘原料（sidecar）对照成三段式纪要。

    设计：host-docs/v0.3.0/review-material-design.md D3。
    **只对照假设状态**——不评价该不该行动、不含买卖语义（LAW 6）。
    产出后须过 `report_qc.py --fail-on error` 方可交付。
    """
    md_path, err = _resolve_md_path(symbol, md)
    if err:
        print(f"❌ {err}", file=sys.stderr)
        return 2

    from decision_review import collect_sidecars, render_review
    from dates import shanghai_now

    report_dir = md_path.parent
    sidecars = collect_sidecars(report_dir)
    today = shanghai_now().date()
    body = render_review(symbol, sidecars=sidecars, today=today)
    body += _render_generation_check(report_dir)

    out_path = Path(out) if out else report_dir / f"{today:%Y%m%d}-review.md"
    if out and not out_path.parent.is_dir():
        # 与 --md / --from 的路径参数同口径：显式报错 + exit 2（此前是
        # FileNotFoundError traceback）。**不代建目录**——手误路径不该留下垃圾目录树。
        print(f"❌ --out 父目录不存在：{out_path.parent}", file=sys.stderr)
        return 2
    out_path.write_text(body, encoding="utf-8")
    print(body)
    print(f"\n已落盘: {out_path}")
    # 准出命令须**按运行布局**打印（仓内 skills/lib ↔ 包内 scripts/lib）：写死仓内
    # 路径会让分发包里的 agent 照自己 CLI 的指示执行必然失败 →「机器层准出（必跑）」
    # 在分发形态从未运行（R2 review P2，实测 builder 产物内无 skills/ 目录）。
    from skill_paths import shared_tool_relpath

    qc = shared_tool_relpath(__file__, "report_qc")
    print(f"> 交付前须跑：uv run python {qc} {out_path} --fail-on error")
    return 0


def main(argv: list[str] | None = None) -> int:
    from logutil import setup_logging
    setup_logging(skill="invest-a-etf")  # INVEST_DEV=1 时启用开发日志；release 零文件 I/O
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
    p_report.add_argument(
        "--payload-out",
        metavar="PATH",
        default=None,
        help="B05: 冻结本次 payload（含 HTML 渲染所需维度）到指定文件"
             "（约定 reports/{symbol}-*/<md ts>.payload.json）；html 只读该文件渲染",
    )
    p_report.add_argument(
        "--index-exposure",
        action="store_true",
        help="按需查询指数样本/权重（csindex 公开文件）并随 payload 冻结；"
             "报告 Fit 章节引用该数据时必须传（否则引用输入不在同代封存内）",
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
    p_peers.add_argument(
        "--same-index",
        metavar="CODES",
        default=None,
        help="B02 同指数产品清单（逗号分隔）；需同时给 --index-code 与 "
             "--evidence-json（官方材料证据），输出实现质量有限比较（不排名）",
    )
    p_peers.add_argument("--index-code", metavar="CODE", default=None,
                         help="同指数比较的指数代码（如 000300）")
    p_peers.add_argument("--evidence-json", metavar="PATH", default=None,
                         help="本次读取的官方材料证据（{code: {index_code, url, "
                              "sha256, locator, ...}}）；URL 存在 ≠ 已读")

    p_sf = sub.add_parser("sector-flow", help="R15: 关联行业资金流 + 趋势（同花顺 3/5/10 日）")
    p_sf.add_argument("symbol", help="6 位 ETF 代码")
    p_sf.add_argument("--json", action="store_true", help="输出完整 JSON")
    sub.add_parser(
        "collect-sector-flow",
        help="R15: 手动触发行业资金流每日采集（幂等，非交易日跳过；盘后触发）",
    )
    p_fb = sub.add_parser("futures-basis", help="F 系列: ETF 股指期货动态基差状态（状态度量，非预测）")
    p_fb.add_argument("symbol", help="6 位 ETF 代码")
    p_fb.add_argument("--json", action="store_true", help="输出完整 JSON")

    p_dec = sub.add_parser(
        "decision", help="复盘原料 sidecar：初始化模板 / 校验并落盘（与报告 md 同 ts）")
    p_dec.add_argument("symbol", help="6 位 ETF 代码")
    p_dec.add_argument("--from", dest="from_path", default=None,
                       help="待校验并落盘的 decision.json 路径")
    p_dec.add_argument("--init", action="store_true",
                       help="按最新报告 ts 输出最小 schema 模板（供填写）")
    p_dec.add_argument("--md", default=None, help="显式指定配对报告 md 路径")

    p_rev = sub.add_parser(
        "review", help="复盘纪要：报告序列 + 复盘原料对照（只对照假设状态，非决策）")
    p_rev.add_argument("symbol", help="6 位 ETF 代码")
    p_rev.add_argument("--out", default=None, help="输出路径（缺省 报告目录/{YYYYMMDD}-review.md）")
    p_rev.add_argument("--md", default=None, help="显式指定配对报告 md 路径")

    p_html = sub.add_parser("html", help="生成交互式 HTML 报告（仪表盘 + 报告 md 原文嵌入）")
    p_html.add_argument("symbol", help="6 位 ETF 代码")
    p_html.add_argument("--md", metavar="PATH", default=None,
                        help="报告 md 路径（缺省自动取 reports/{symbol}-* 最新）")
    p_html.add_argument("--payload", dest="payload", metavar="PATH", default=None,
                        help="B05: 冻结 payload 路径（必填；须与 md 同目录同 ts："
                             "<ts>.payload.json；HTML 只读该输入，零网络零 DB 写）")
    p_html.add_argument("--out", metavar="PATH", default=None,
                        help="HTML 输出路径（缺省与 md 同目录同名 .html）")
    p_html.add_argument("--no-open", action="store_true",
                        help="不自动打开浏览器（仅生成文件）")

    args = parser.parse_args(argv)
    if args.cmd == "report":
        return cmd_report(args.symbol, as_json=args.json, with_nav=args.with_nav,
                          history=args.history, history_days=args.history_days,
                          events_path=args.events, playbook=args.playbook,
                          payload_out=args.payload_out,
                          index_exposure=args.index_exposure)
    if args.cmd == "diagnose":
        return cmd_diagnose()
    if args.cmd == "industry-pe":
        return cmd_industry_pe()
    if args.cmd == "collect-weekly":
        return cmd_collect_weekly()
    if args.cmd == "holdings":
        return cmd_holdings(args.symbol, as_json=args.json)
    if args.cmd == "peers":
        return cmd_peers(args.symbol, as_json=args.json, peers_str=args.peers,
                         same_index_str=args.same_index, index_code=args.index_code,
                         evidence_path=args.evidence_json)
    if args.cmd == "sector-flow":
        return cmd_sector_flow(args.symbol, as_json=args.json)
    if args.cmd == "collect-sector-flow":
        return cmd_collect_sector_flow()
    if args.cmd == "futures-basis":
        return cmd_futures_basis(args.symbol, as_json=args.json)
    if args.cmd == "decision":
        return cmd_decision(args.symbol, from_path=args.from_path, init=args.init,
                            md=args.md)
    if args.cmd == "review":
        return cmd_review(args.symbol, out=args.out, md=args.md)
    if args.cmd == "html":
        return cmd_html(args.symbol, md_path=args.md, out_path=args.out,
                        no_open=args.no_open, payload_path=args.payload)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
