"""参与者行为扫描 — 基于现有资金/股东维度的元分析层。

纯函数，无 API 调用。输出行为事实与交叉验证分歧，不含策略建议（LAW 6）。
"""

from __future__ import annotations

from typing import Any

from lib.nums import fmt_amount
from lib.scoring import insider_signal

# 口径标签：net_sum_* 取自 Tushare moneyflow.net_mf_amount，是**全档**净额
# （小单+中单+大单+特大单）。行情软件惯用的「主力」指大单+特大单，两者量纲
# 口径不同且**方向可完全相反**（300750 2026-09-16 实测：近 5 日全档 +17.96 亿
# vs 大单+特大单 −15.24 亿）。标签不得写「主力」——那会把全档值读成主力值。
_MF_LABELS = {
    "net_sum_5d": "近5日全档净额",
    "net_sum_10d": "近10日全档净额",
    "net_mf_amount": "全档净额",
}
_MF_CV_WINDOW = {
    "net_sum_5d": "全档近5日",
    "net_sum_10d": "全档近10日",
    "net_mf_amount": "全档",
}
_DEFAULT_MF_KEYS = ("net_sum_5d", "net_sum_10d", "net_mf_amount")

# 大单+特大单净额（「主力」的行情软件惯用口径）。缺失时不给该键，
# 消费方须按 None 处理，禁止回落成全档值冒充。
_MF_LG_ELG_KEY = "net_sum_5d_lg_elg"


def northbound_label(nb: dict) -> str:
    """北向净额标签：hsgt_top10 为上榜日累计，akshare 为连续交易日。

    P0-1：net_sum_10d 被时效守卫置 None（源停更/时效不可确认）时输出
    「数据不可用」+ 原因，禁止以 fmt_amount(None) 的占位渲染成
    「上榜日累计净额 -（10 个上榜日）」暗示近期数据。
    """
    if nb.get("net_sum_10d") is None:
        note = nb.get("staleness_note")
        return f"（数据不可用）{note}" if note else "（数据不可用）"
    try:
        days = int(nb.get("days") or 0)
    except (TypeError, ValueError):
        days = 0
    amount = fmt_amount(nb.get("net_sum_10d"))
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


def resolve_moneyflow(mf: dict | None, *keys: str) -> tuple[float | None, str | None]:
    if not keys:
        keys = _DEFAULT_MF_KEYS
    if not isinstance(mf, dict):
        return None, None
    for key in keys:
        value = _moneyflow_net(mf, key)
        if value is not None:
            return value, key
    return None, None


def moneyflow_signal_label(key: str | None) -> str:
    if key:
        return _MF_LABELS.get(key, "全档净额")
    return "全档净额"


def moneyflow_cv_window(key: str | None) -> str:
    """CV 备注里的资金窗口标签。兜底也须写「全档」——调用方传入的键集
    （_DEFAULT_MF_KEYS）全部是全档口径，未知键同样不得回落到「主力」。
    """
    if key:
        return _MF_CV_WINDOW.get(key, "全档")
    return "全档"


def _table_cell(text: Any) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


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
            "signal": northbound_label(nb),
            "source": str(nb.get("source") or "market_structure.northbound"),
        })
    elif isinstance(nb, dict) and nb.get("staleness_note"):
        # P0-1：源停更陈旧——保留一行说明，标注停更原因而非静默消失
        rows.append({
            "role": "北向（外资）",
            "signal": nb["staleness_note"],
            "source": str(nb.get("source") or "market_structure.northbound"),
        })

    mf = ms.get("moneyflow")
    mf_net: float | None = None
    mf_key: str | None = None
    if isinstance(mf, dict):
        mf_net, mf_key = resolve_moneyflow(mf)
        if mf_net is not None:
            # 两个口径并列输出：全档（net_mf_amount）与行情软件惯用的
            # 大单+特大单。二者方向可完全相反，只报一个会把结论读反——
            # 故这里不做取舍，两个都给，让读者自行对齐口径。
            sig = f"{moneyflow_signal_label(mf_key)} {fmt_amount(mf_net)}"
            lg_elg = mf.get(_MF_LG_ELG_KEY)
            if isinstance(lg_elg, (int, float)):
                sig += f"；大单+特大单近5日 {fmt_amount(lg_elg)}"
            rows.append({
                "role": "资金流（moneyflow 全档）",
                "signal": sig,
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

    sh = (dims.get("shareholders") or {}).get("data")
    if isinstance(sh, list) and sh and not any(r["role"] == "产业/内部人" for r in rows):
        rows.append({
            "role": "股东结构",
            "signal": f"前十大流通股东记录 {len(sh)} 条（行为见 holder_changes）",
            "source": "collect.shareholders",
        })

    turnover = ms.get("turnover")
    if isinstance(turnover, dict) and turnover.get("percentile_60d") is not None:
        try:
            pct = float(turnover["percentile_60d"])
            rows.append({
                "role": "换手（散户活跃度代理）",
                "signal": f"近60日换手历史位置 {pct:.0f}%",
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
    mf_window = moneyflow_cv_window(mf_key)
    if nb_net is not None and mf_net is not None:
        if nb_net * mf_net > 0:
            cv_notes.append(f"北向与全档资金净流入方向一致（北向近10日 vs {mf_window}）")
        elif nb_net == 0 and mf_net == 0:
            cv_notes.append(f"北向与全档资金净流入方向一致（北向近10日 vs {mf_window}）")
        elif nb_net == 0 or mf_net == 0:
            cv_notes.append(f"资金数据不完整（北向近10日 vs {mf_window}）")
        else:
            cv_notes.append(
                f"北向与全档资金净流入方向相反（北向近10日 vs {mf_window}，可能存在参与者差异或滞后）"
            )

    quote = (dims.get("quote") or {}).get("data") or {}
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
    analysis: list[dict] | None = None,
) -> str:
    """渲染「参与者行为扫描」Markdown 节。

    analysis（v0.3.0 fix③）：命中 participant_scan 槽位的段替换
    「分析提示（Claude 填写）」占位；无匹配段时保持占位不变。
    """
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

    # QC `structure-analysis-without-fact`：本表是 [事实] 块，下方 [分析]
    # 须有同节段内前置的 [事实]（50 行回溯、遇标题停止）。
    lines.append("**[事实]** 各类参与者近期行为信号（口径与来源逐行标注）：")
    lines.append("")
    lines.append("| 参与者类型 | 近期行为信号 | 来源 |")
    lines.append("|-----------|-------------|------|")
    for r in rows:
        lines.append(
            f"| {_table_cell(r['role'])} | {_table_cell(r['signal'])} | {_table_cell(r['source'])} |"
        )
    lines.append("")

    if cv_notes:
        lines.append("**交叉验证（参与者行为）：**")
        for note in cv_notes:
            lines.append(f"- {note}")
        lines.append("")

    # v0.3.0 fix③：participant_scan 槽位的分析段替换「待 Claude 填写」占位
    # （该串是 QC `completion-template-placeholder` 的 error 级命中项）。
    # 无匹配段 → 保持原提示，完成度门禁照常拦截未填报告。
    from lib.analysis_schema import PARTICIPANT_SCAN_KEYS, find_section, mark_inline_consumed
    _sec = find_section(analysis, PARTICIPANT_SCAN_KEYS)
    _amd = str((_sec or {}).get("analysis_md") or "").strip()
    if _amd:
        mark_inline_consumed(collection, _sec)
        lines.append("**[分析]**")
        lines.append("")
        lines.append(_amd)
    else:
        lines.append(
            "**分析提示（Claude 填写）：** 基于上表陈述行为一致性或分歧；"
            "禁止输出操作建议或均衡推断。"
        )
    lines.append("")
    lines.append("🔍 **待独立验证:** 主力/北向数据口径因源而异；内部人信号窗口为近12个月公告。")
    return "\n".join(lines)
