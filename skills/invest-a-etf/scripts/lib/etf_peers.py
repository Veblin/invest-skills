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
    query_etf_est_net_assets,
    query_etf_kline,
    query_etf_share_history,
)

from nums import safe_float  # noqa: E402 — etf_data 已引导共享路径

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


# ---------------------------------------------------------------------------
# B02：同指数产品的**实现质量有限比较**（不排名）
# ---------------------------------------------------------------------------

#: 同指数比较的证据字段（每只产品必须有官方材料定位；URL 存在 ≠ 已读）
SAME_INDEX_EVIDENCE_FIELDS = (
    "index_code", "index_name", "fund_name", "source_type", "url",
    "as_of", "read_at", "sha256", "locator",
)


def _same_index_nav_leg(code: str, days: int) -> dict[str, Any]:
    """同指数比较的净值腿：复权（前复权）净值序列；未复权/不可得 = 不参与比较。

    口径与主报告跟踪块一致：复权净值（≈总收益）才可与同指数产品比收益口径；
    未复权（分红/拆分断点未消除）不得参与可比窗口（B02 反例：分红拆分跳变）。
    """
    kline = query_etf_kline(code, days=days)
    if kline.get("status") != "available" or not kline.get("nav_history"):
        return {"status": "missing", "symbol": code,
                "note": kline.get("_error") or "净值序列不可得"}
    if not kline.get("adj_applied"):
        return {"status": "missing", "symbol": code,
                "note": "基金腿未复权（分红/拆分断点未消除）：不参与同指数收益比较"}
    return {
        "status": "ok", "symbol": code,
        "source": kline.get("source"),
        # 键用 ISO 日期（同窗披露/复算可读；_aligned_nav_map 的原键为 compact）
        "values": {_iso_date(d): v
                   for d, v in _aligned_nav_map(kline["nav_history"]).items()},
        "attempted_sources": list(kline.get("attempted_sources") or []),
    }


def _iso_date(compact: str) -> str:
    """YYYYMMDD → YYYY-MM-DD（非 8 位数字原样返回，不猜）。"""
    d = str(compact)
    return f"{d[:4]}-{d[4:6]}-{d[6:8]}" if len(d) == 8 and d.isdigit() else d


def _same_index_fee_row(code: str, ev: dict[str, Any] | None) -> dict[str, Any]:
    """费率行只出自**本次读取的官方材料**（evidence）；未读 = not_collected。

    不查产品注册表、不用渠道页费率、不给估计值；材料版本与定位随行冻结。
    """
    if not ev:
        return {"status": "not_collected", "symbol": code, "items": [], "source": None,
                "note": "未读该产品官方费率材料（不猜、不用渠道页；URL 存在 ≠ 已读）"}
    missing = [k for k in ("url", "sha256", "locator") if not ev.get(k)]
    if missing:
        return {"status": "invalid_evidence", "symbol": code, "items": [],
                "source": None,
                "note": f"证据缺少 {'/'.join(missing)}（须 URL+sha256+原文定位）"}
    items = ev.get("fee_items")
    if items is None or items == []:
        return {"status": "not_collected", "symbol": code, "items": [],
                "source": None, "note": "材料未回填费率条目（身份材料不等于费用取数）"}
    if not isinstance(items, list) or any(
        not isinstance(i, dict) or not isinstance(i.get("name"), str)
        or not i["name"].strip() or isinstance(i.get("annual_rate_pct"), bool)
        or safe_float(i.get("annual_rate_pct")) is None
        or safe_float(i.get("annual_rate_pct")) < 0 for i in items
    ):
        return {"status": "invalid_evidence", "symbol": code, "items": [],
                "source": None, "note": "费率条目须有名称与有限非负 annual_rate_pct"}
    return {
        "status": "ok", "symbol": code,
        "items": [dict(i) for i in items],
        "source": {k: ev.get(k) for k in
                   ("source_type", "url", "as_of", "read_at", "sha256", "locator",
                    "version")},
        "note": ev.get("note"),
    }


_IDENTITY_EVIDENCE_FIELDS = ("source_type", "url", "as_of", "read_at", "sha256", "locator")


def _missing_identity_evidence(ev: dict[str, Any]) -> list[str]:
    """指数/币种声明须绑定已读取官方材料的来源、版本日期与原文定位。"""
    return [k for k in _IDENTITY_EVIDENCE_FIELDS
            if not isinstance(ev.get(k), str) or not ev[k].strip()]


def same_index_quality_comparison(
    symbol: str,
    peers: list[str],
    *,
    index_code: str,
    index_name: str | None = None,
    evidence: dict[str, dict[str, Any]] | None = None,
    window_days: int = 250,
) -> dict[str, Any]:
    """同指数产品的实现质量**有限比较**（B02；不排名，不做单一指标裁决）。

    可比性先核验（缺一即降为 limited/缺口，不硬凑比较）：

    - **同指数**：每只产品的指数身份来自本次读取的官方材料（``evidence`` 的
      ``index_code``，须附 source_type/url/as_of/read_at/sha256/locator），
      与 ``index_code`` 不一致/证据不完整 → 该行标记不可比；
    - **币种**：来自官方材料（境内产品 CNY）；未知不推定；
    - **期间**：所有可比产品取**同一交集日期窗**（同起止），窗内缺值使窗收缩，
      各行披露实际覆盖；
    - **收益口径**：统一用复权净值收益（≈总收益）；未复权/不可得 → 该行不参与。

    指标（有则列示，无则明确缺口）：同窗收益、费率（官方材料）、估算资产净值
    （既有口径，标注非官方）。输出显式 ``ranking: None``——不同产品的差异受
    费率/跟踪/现金流/申赎摩擦等多因素影响，本比较不裁决优劣、不重复扣费用。
    """
    codes = list(dict.fromkeys([symbol, *peers]))
    evidence = evidence or {}
    rows: list[dict[str, Any]] = []
    nav_legs: dict[str, dict[str, Any]] = {}
    for code in codes:
        leg = _same_index_nav_leg(code, window_days)
        nav_legs[code] = leg
        try:
            ena = query_etf_est_net_assets(code)  # 既有口径（同日份额×净值，非官方）
        except Exception as exc:  # pragma: no cover — 单只失败不阻断比较
            ena = {"status": "error", "note": str(exc)}
        ena_row = ({"status": ena.get("status"), "value_yi": ena.get("value_yi"),
                    "as_of": ena.get("as_of"), "caliber": ena.get("caliber")}
                   if ena.get("status") == "ok"
                   else {"status": ena.get("status"), "note": ena.get("note")})
        rows.append({
            "symbol": code,
            "name": (evidence.get(code) or {}).get("fund_name") or _peer_name(code),
            "index": {
                "code": (evidence.get(code) or {}).get("index_code"),
                "name": (evidence.get(code) or {}).get("index_name"),
                "declared_same_index":
                    bool((evidence.get(code) or {}).get("index_code") == index_code
                         and not _missing_identity_evidence(evidence.get(code) or {})),
                "evidence": ({k: (evidence.get(code) or {}).get(k)
                              for k in ("source_type", "url", "as_of", "read_at",
                                        "sha256", "locator")}
                             if evidence.get(code) else None),
            },
            "currency": (evidence.get(code) or {}).get("currency"),
            "return_type": "复权净值收益（≈总收益）",
            "nav": ({"status": leg["status"], "note": leg.get("note")}
                    if leg["status"] != "ok" else {"status": "ok"}),
            "fees": _same_index_fee_row(code, evidence.get(code)),
            "est_net_assets": ena_row,
            "window_return_pct": None,
            "status": "limited",
        })

    # 同窗（交集）——可比行 = 官方材料声明同指数 + 币种一致 + 净值腿复权可用；
    # 任一前置不满足 → 该行 not_comparable（明确缺口，不缩小口径凑数）
    comparable: list[str] = []
    for row in rows:
        code = row["symbol"]
        ev = evidence.get(code) or {}
        reasons: list[str] = []
        missing = _missing_identity_evidence(ev)
        if missing:
            reasons.append("官方材料证据缺少 " + "/".join(missing))
        if ev.get("index_code") != index_code:
            reasons.append("官方材料未声明同指数（或未读材料）")
        if ev.get("currency") != "CNY":
            reasons.append("币种未由官方材料确认（不推定）")
        if nav_legs[code]["status"] != "ok":
            reasons.append(nav_legs[code].get("note") or "净值腿不可用")
        row["comparability_reasons"] = reasons
        if not reasons:
            comparable.append(code)
    common_window: dict[str, Any] | None = None
    window_returns: dict[str, float] = {}
    if len(comparable) >= 2:
        common = sorted(set.intersection(*[set(nav_legs[c]["values"]) for c in comparable]))
        if len(common) >= 2:
            common_window = {"start": common[0], "end": common[-1], "n_dates": len(common)}
            for c in comparable:
                vals = nav_legs[c]["values"]
                window_returns[c] = round((vals[common[-1]] / vals[common[0]] - 1) * 100, 4)
    for row in rows:
        code = row["symbol"]
        in_window = code in window_returns
        if in_window:
            row["window_return_pct"] = window_returns[code]
        row["status"] = ("comparable" if in_window
                         else ("limited" if not row["comparability_reasons"]
                               else "not_comparable"))
    excluded = [r for r in rows if r["status"] == "not_comparable"]
    gaps: list[str] = []
    for r in excluded:
        gaps.append(f"{r['symbol']} 不可比：" + "；".join(r["comparability_reasons"]))
    if len(comparable) < 2:
        gaps.append("可比产品不足 2 只：无法做同指数实现质量比较（明确缺口，不猜源）")
    if common_window is None and len(comparable) >= 2:
        gaps.append("同窗交集不足 2 个交易日：同窗收益不可算")
    return {
        "status": "ok" if len(comparable) >= 2 and common_window else "limited",
        "index_code": index_code,
        "index_name": index_name,
        "window_days": window_days,
        "common_window": common_window,
        "comparability": {
            "index": "官方材料声明（逐行 evidence；未读不推定）",
            "currency": "官方材料；未知不推定",
            "period": "交集日期窗（同起止；缺值使窗收缩并逐行披露）",
            "return_type": "复权净值收益（≈总收益）；未复权不参与",
        },
        "rows": rows,
        "ranking": None,
        "no_ranking_note": "不排名、不裁决优劣：差异受费率/跟踪/现金流/申赎摩擦等多因素"
                           "影响；费率已在净值中体现，不重复扣减",
        "gaps": gaps,
        "note": "同指数实现质量有限比较（B02）：无指标即明确缺口，不用不同指数排序替代",
    }
