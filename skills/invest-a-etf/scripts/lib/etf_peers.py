"""赛道资金流对比 + 相对强弱（v0.2.5 R13）。

依赖：etf_data.query_etf_share_history（Tushare fund_share + fund_daily，
L2 1d 缓存）、query_etf_kline（NAV 序列）。共享库 technical.relative_strength
为「仅测试使用；保留」状态（两点输出、无日期序列），本模块自实现
etf_peer_rs（同归一化口径：RS_t = (main/bench) × 100 × (b0/s0)，基准=首对齐点）。
"""

from __future__ import annotations

from typing import Any

from etf_data import (
    ETF_TO_SW_INDUSTRY,
    _bridge_get,
    _lookup_etf_spot_row,
    query_etf_kline,
    query_etf_share_history,
)

_PEER_NAV_DAYS = 60       # 每只 NAV 取数窗口（交易日）；RS 20 日变化需 ≥21 个对齐点
_RS_WINDOW = 20           # RS 变化窗口（交易日）


def resolve_peer_symbols(symbol: str, peers: list[str] | None) -> dict[str, Any]:
    """赛道清单解析：显式 peers 优先，否则 ETF_TO_SW_INDUSTRY 同 sw_code 自动发现。

    Returns
    -------
    dict
        {symbol, peers, peer_source, note?}；未映射时 peers=[] + note 提示。
    """
    if peers:
        bad = [p for p in peers if not (p.isdigit() and len(p) == 6)]
        if bad:
            return {
                "symbol": symbol,
                "peers": [],
                "peer_source": "explicit",
                "note": f"非法代码: {bad}（需 6 位数字）",
            }
        return {"symbol": symbol, "peers": peers, "peer_source": "explicit"}

    sw = ETF_TO_SW_INDUSTRY.get(symbol)
    if not sw:
        return {
            "symbol": symbol,
            "peers": [],
            "peer_source": "etf_to_sw_industry",
            "note": "未映射申万行业（ETF_TO_SW_INDUSTRY 无此代码），"
                    "请用 --peers 显式指定，如 --peers \"512660,512760\"",
        }
    cohort = sorted(
        c for c, info in ETF_TO_SW_INDUSTRY.items()
        if info["sw_code"] == sw["sw_code"] and c != symbol
    )
    return {
        "symbol": symbol,
        "peers": cohort,
        "peer_source": f"etf_to_sw_industry:{sw['sw_code']} {sw['sw_name']}",
    }


def _flow_row(sh: dict) -> dict[str, Any]:
    """从 query_etf_share_history 结果派生 peers 对比行。

    summary.total_flow_est ≈ 20 日（row_count 行合计）、recent_flow_est ≈ 最近
    5 个可算行（T+1 延迟使实际跨度可能 >5 交易日，trend 已标注 actual span）。
    share_change_pct 从 rows 首尾非 None shares 计算——rows 已丢弃首行 r0，
    实际覆盖 span 个间隔（span = 有效份额行数 - 1，通常 19 而非 20）；
    share_change_span 如实标注，避免与 flow_20d_e（20 间隔）混读。
    """
    s = sh.get("summary") or {}
    shares = [r["shares"] for r in sh.get("rows", []) if r.get("shares") is not None]
    pct: float | None = None
    span: int | None = None
    if len(shares) >= 2 and shares[0]:
        pct = round((shares[-1] - shares[0]) / shares[0] * 100, 2)
        span = len(shares) - 1
    return {
        "symbol": sh.get("symbol"),
        "flow_20d_e": s.get("total_flow_est"),
        "flow_5d_e": s.get("recent_flow_est"),
        "trend": s.get("trend"),
        "share_change_pct": pct,
        "share_change_span": span,
        "row_count": s.get("row_count"),
        "note": sh.get("note"),
    }


def etf_peer_rs(
    main_closes: list[float],
    bench_closes: list[float],
    dates: list[str],
    window: int = _RS_WINDOW,
) -> dict[str, Any]:
    """RS_t = (main/bench) × 100 × (b0/s0)，基准=首对齐点归一化为 100。

    Returns
    -------
    dict
        {rs_latest, rs_start, rs_change, rs_change_pct, rs_series（末 window 点）,
         n}；对齐点 < window+1 时返回 {error}。
    """
    if len(main_closes) < window + 1 or len(bench_closes) < window + 1:
        return {"error": f"对齐交易日不足（需 ≥{window + 1}，实际 {len(main_closes)}）"}
    s0, b0 = main_closes[0], bench_closes[0]
    if not s0 or not b0:
        return {"error": "基准或主标的净值为零"}
    if any(not b for b in bench_closes):
        return {"error": "基准序列含零值（除零），RS 不可计算"}
    rs = [m / b * 100 * (b0 / s0) for m, b in zip(main_closes, bench_closes)]
    tail_d = dates[-window:]
    tail_rs = rs[-window:]
    # 三数字自洽：rs_latest = rs_window_start + rs_change（窗口 = 末 window 点）
    return {
        "rs_latest": round(rs[-1], 2),
        "rs_window_start": round(rs[-window], 2),
        "rs_change": round(rs[-1] - rs[-window], 2),
        "rs_change_pct": round((rs[-1] / rs[-window] - 1) * 100, 2)
        if rs[-window] else None,
        "rs_series": [{"date": d, "rs": round(v, 2)} for d, v in zip(tail_d, tail_rs)],
        "n": len(rs),
    }


def _aligned_nav_map(kline_rows: list[dict]) -> dict[str, float]:
    """nav_history → {YYYYMMDD: nav}（日期归一化去横线）。"""
    return {
        str(r["date"]).replace("-", "")[:8]: r["nav"]
        for r in kline_rows
        if r.get("nav") is not None
    }


def _peer_name(symbol: str) -> str | None:
    """ETF 名称（best-effort：spot 全表；失败降级 None 不阻断）。"""
    try:
        row, err = _lookup_etf_spot_row(symbol)
        if err or row is None:
            return None
        for key in ("基金简称", "名称"):
            if row.get(key):
                return str(row[key])
    except Exception:
        return None
    return None


def query_etf_peers(symbol: str, peers: list[str] | None = None) -> dict[str, Any]:
    """赛道资金流对比 + 相对强弱（v0.2.5 R13）。

    Returns
    -------
    dict
        {symbol, peers, peer_source, available, flow: {window_days,
         rows: [{symbol, name, flow_20d_e, flow_5d_e, trend, share_change_pct,
         note}]}, rs: {...} | None, names, notes}。

    单只失败不阻断：该行 note 标注失败原因，其余行照常。
    RS 基准 = 同赛道 peer 等权均值（逐日），主标的自身不计入基准。
    """
    resolved = resolve_peer_symbols(symbol, peers)
    cohort = resolved["peers"]
    if not cohort:
        return {
            "symbol": symbol,
            "available": False,
            "peers": [],
            "peer_source": resolved["peer_source"],
            "note": resolved.get("note", ""),
            "flow": None,
            "rs": None,
            "names": {},
            "notes": [],
        }

    names: dict[str, str | None] = {symbol: _peer_name(symbol)}
    flow_rows: list[dict] = []
    nav_maps: dict[str, dict[str, float]] = {}
    notes: list[str] = []
    for code in [symbol] + cohort:
        names[code] = _peer_name(code)
        sh = query_etf_share_history(code, days=20)
        if sh.get("available"):
            flow_rows.append(_flow_row(sh))
        else:
            flow_rows.append(
                {
                    "symbol": code,
                    "flow_20d_e": None,
                    "flow_5d_e": None,
                    "trend": None,
                    "share_change_pct": None,
                    "row_count": None,
                    "note": sh.get("note") or "份额历史不可用",
                }
            )
        kline = query_etf_kline(code, days=_PEER_NAV_DAYS)
        if kline.get("status") == "available" and kline.get("nav_history"):
            nav_maps[code] = _aligned_nav_map(kline["nav_history"])

    # RS：共同交易日交集（停牌/节假日差异自然剔除）
    rs: dict[str, Any] | None = None
    if len(nav_maps) >= 2 and symbol in nav_maps:
        common = sorted(set(nav_maps[symbol]) & set.intersection(
            *[set(m) for c, m in nav_maps.items() if c != symbol]
        ))
        if len(common) >= _RS_WINDOW + 1:
            main_c = [nav_maps[symbol][d] for d in common]
            peers_c = [[nav_maps[c][d] for d in common]
                       for c in nav_maps if c != symbol]
            bench = [
                sum(vals) / len(vals) for vals in zip(*peers_c)
            ]
            rs = etf_peer_rs(main_c, bench, common)
            # 20 日累计收益排名：窗口 = 最近 window 个共同交易日（与 rs_change 一致）
            win = common[-_RS_WINDOW:]
            rets = {
                c: round((nav_maps[c][win[-1]] / nav_maps[c][win[0]] - 1) * 100, 2)
                for c in nav_maps
            }
            rank = sorted(rets, key=lambda c: rets[c], reverse=True)
            rs["rank_20d"] = {
                "rank": rank.index(symbol) + 1,
                "total": len(rank),
                "window": f"{_RS_WINDOW} 个交易日",
                "returns": rets,
            }
            # 收益排名仅覆盖 NAV 可用成员（份额流表可能含更多行）——如实标注
            if len(rank) < len(flow_rows):
                notes.append(
                    f"收益排名分母仅覆盖 NAV 可用成员（{len(rank)}/{len(flow_rows)}），"
                    "另有个别成员净值取数失败未纳入"
                )
        else:
            notes.append(
                f"RS 不可计算：共同交易日 {len(common)} < {_RS_WINDOW + 1}"
            )
    elif symbol not in nav_maps:
        notes.append(f"主标的 NAV 不可得，RS 跳过")
    else:
        notes.append("赛道成员 NAV 不足，RS 跳过")

    return {
        "symbol": symbol,
        "peers": cohort,
        "peer_source": resolved["peer_source"],
        "available": True,
        "flow": {"window_days": 20, "rows": flow_rows},
        "rs": rs,
        "names": names,
        "notes": notes,
    }
