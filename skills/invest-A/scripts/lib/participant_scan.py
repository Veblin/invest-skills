"""参与者行为扫描 — 基于现有资金/股东维度的元分析层。

纯函数，无 API 调用。输出行为事实与交叉验证分歧，不含策略建议（LAW 6）。
"""

from __future__ import annotations

from typing import Any

from lib.scoring import insider_signal


def _fmt_amount(v: Any) -> str:
    if v is None:
        return "-"
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    if abs(f) >= 1e8:
        return f"{f / 1e8:.2f}亿"
    if abs(f) >= 1e4:
        return f"{f / 1e4:.2f}万"
    return f"{f:.2f}"


def _northbound_label(nb: dict) -> str:
    days = int(nb.get("days") or 0)
    amount = _fmt_amount(nb.get("net_sum_10d"))
    src = str(nb.get("source") or "")
    if "hsgt_top10" in src:
        return f"上榜日累计净额 {amount}（{days} 个上榜日）"
    if days:
        return f"近 {days} 日净额 {amount}"
    return f"净额 {amount}"


def _moneyflow_net(mf: dict, key: str) -> float | None:
    if not isinstance(mf, dict):
        return None
    v = mf.get(key)
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _scan_rows(
    market_structure: dict,
    dims: dict,
) -> tuple[list[dict[str, str]], list[str]]:
    """返回 (参与者行, 交叉验证备注)。"""
    rows: list[dict[str, str]] = []
    cv_notes: list[str] = []
    ms = market_structure or {}

    nb = ms.get("northbound")
    if isinstance(nb, dict) and nb.get("net_sum_10d") is not None:
        rows.append({
            "role": "北向（外资）",
            "signal": _northbound_label(nb),
            "source": str(nb.get("source") or "market_structure.northbound"),
        })

    mf = ms.get("moneyflow")
    if isinstance(mf, dict):
        net10 = _moneyflow_net(mf, "net_sum_10d") or _moneyflow_net(mf, "net_mf_amount")
        if net10 is not None:
            rows.append({
                "role": "主力（大单代理）",
                "signal": f"近10日主力净额 {_fmt_amount(net10)}",
                "source": str(mf.get("source") or "market_structure.moneyflow"),
            })

    margin = ms.get("margin")
    if isinstance(margin, dict) and margin.get("change_pct") is not None:
        try:
            chg = float(margin["change_pct"])
            rows.append({
                "role": "杠杆资金",
                "signal": f"融资余额变化 {chg:+.2f}%",
                "source": str(margin.get("source") or "market_structure.margin"),
            })
        except (TypeError, ValueError):
            pass

    hc_dim = dims.get("holder_changes") or {}
    hc_data = hc_dim if isinstance(hc_dim, dict) else {}
    sig = insider_signal(hc_data)
    if sig != "数据不足":
        rows.append({
            "role": "产业/内部人",
            "signal": f"内部人一致性信号: {sig}",
            "source": "lib.scoring.insider_signal / holder_changes",
        })

    sh = dims.get("shareholders", {}).get("data")
    if isinstance(sh, list) and sh and not any(r["role"] == "产业/内部人" for r in rows):
        rows.append({
            "role": "股东结构",
            "signal": f"前十大流通股东记录 {len(sh)} 条（行为见 holder_changes）",
            "source": "collect.shareholders",
        })

    turnover = ms.get("turnover")
    if isinstance(turnover, dict) and turnover.get("percentile_1y") is not None:
        try:
            pct = float(turnover["percentile_1y"])
            rows.append({
                "role": "换手（散户活跃度代理）",
                "signal": f"近一年换手历史位置 {pct:.0f}%",
                "source": str(turnover.get("source") or "market_structure.turnover"),
            })
        except (TypeError, ValueError):
            pass

    pcr = ms.get("put_call_ratio")
    if isinstance(pcr, dict) and pcr.get("ratio") is not None:
        rows.append({
            "role": "期权情绪代理（PCR）",
            "signal": f"认沽认购比 {pcr.get('ratio')}",
            "source": str(pcr.get("source") or "market_structure.put_call_ratio"),
        })

    # CV: 北向 vs 主力方向
    nb_net = None
    if isinstance(nb, dict):
        try:
            nb_net = float(nb.get("net_sum_10d"))
        except (TypeError, ValueError):
            nb_net = None
    mf_net = _moneyflow_net(mf, "net_sum_10d") if isinstance(mf, dict) else None
    if mf_net is None and isinstance(mf, dict):
        mf_net = _moneyflow_net(mf, "net_mf_amount")
    if nb_net is not None and mf_net is not None:
        if (nb_net > 0) == (mf_net > 0):
            cv_notes.append("北向与主力近10日净流入方向一致")
        else:
            cv_notes.append("北向与主力近10日净流入方向相反（可能存在参与者差异或滞后）")

    quote = dims.get("quote", {}).get("data") or {}
    chg = quote.get("change_pct") if isinstance(quote, dict) else None
    if nb_net is not None and chg is not None:
        try:
            chg_f = float(chg)
            if nb_net > 0 and chg_f < -2:
                cv_notes.append(
                    f"北向净流入与股价 {chg_f:+.1f}% 背离 [来源: northbound+quote]"
                )
            elif nb_net < 0 and chg_f > 2:
                cv_notes.append(
                    f"北向净流出与股价 {chg_f:+.1f}% 背离 [来源: northbound+quote]"
                )
        except (TypeError, ValueError):
            pass

    return rows, cv_notes


def build_participant_behavior_section(
    collection: dict,
    symbol: str,
    market_structure: dict,
    dims: dict,
) -> str:
    """渲染「参与者行为扫描」Markdown 节。"""
    lines = [
        "## 参与者行为扫描",
        "",
        "> 元分析层（v0.1.9）：描述各类参与者近期行为事实，非策略建议。",
        "> 方法论见 `references/game-theory.md`。",
        "",
    ]

    rows, cv_notes = _scan_rows(market_structure, dims)
    if not rows:
        lines.extend([
            "未获取到任何有效数据，无法判断参与者行为结构。"
            "[尝试了 market_structure（northbound/moneyflow/margin/turnover）、"
            "holder_changes、shareholders，均不可用]",
            "",
            "🔍 **待独立验证:** 配置 TUSHARE_TOKEN 后重试，或查阅龙虎榜等公开记录（v0.2.0 规划接入）。",
        ])
        return "\n".join(lines)

    lines.append("| 参与者类型 | 近期行为信号 | 来源 |")
    lines.append("|-----------|-------------|------|")
    for r in rows:
        lines.append(f"| {r['role']} | {r['signal']} | {r['source']} |")
    lines.append("")

    if cv_notes:
        lines.append("**交叉验证（行为分歧）：**")
        for note in cv_notes:
            lines.append(f"- {note}")
        lines.append("")

    lines.append(
        "**分析提示（Claude 填写）：** 基于上表陈述行为一致性或分歧；"
        "禁止输出操作建议或均衡推断。"
    )
    lines.append("")
    lines.append("🔍 **待独立验证:** 主力/北向数据口径因源而异；内部人信号窗口为近12个月公告。")
    return "\n".join(lines)
