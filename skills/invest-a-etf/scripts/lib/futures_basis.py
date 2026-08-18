"""ETF 股指期货动态基差查询（v0.2.6 F 系列接入）——独立模块（仿 sector_flow 先例）。

定位（用户定稿）：**状态度量与历史演变分布参照，不做市场预测**。
输出 = 该 ETF 对应股指期货品种的当前基差水平 + 历史分位（伴随中位数，
估值分位规则同款）+ 历史演变分布参照（条件句，非必然）。

品种映射：复用 etf_data.ETF_HEDGE_MAP 的 futures 字段（510300→IF、510500→IC、
512100/159845→IM、510050→IH）；无期货品种的 ETF 返回 available=False。
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def futures_symbol_for_etf(symbol: str) -> str | None:
    """ETF → 期货品种（IF/IH/IC/IM）；有映射但数据层未覆盖的品种
    （如科创50期货）返回品种名（"科创50"），无映射 → None。

    F1-4 修复：hedge-map 记录了「科创50期货(2025上线)」，旧实现只认
    IF/IH/IC/IM 四个品种 → 返回 None → 提示「无映射」，与 hedge-map
    自相矛盾。现在区分「无映射」与「有映射但数据层未覆盖」。
    """
    from etf_data import ETF_HEDGE_MAP  # noqa: E402

    entry = ETF_HEDGE_MAP.get(str(symbol))
    if not entry or not entry.get("futures"):
        return None
    futures_name = entry["futures"]
    for sym in ("IM", "IC", "IF", "IH"):
        if sym in futures_name:
            return sym
    if "科创50" in futures_name:
        return "科创50"
    return None


def query_futures_basis(symbol: str, *, days: int = 1000) -> dict[str, Any]:
    """ETF 的期货基差状态 → {available, futures_symbol, current_basis_pct,
    current_basis_pts, percentile, median_basis_pct, n_history,
    distribution_ref, source, note}。

    days: 分位与中位数所用的历史窗口（futures_daily 按 symbol 加载上限）。
    """
    result: dict[str, Any] = {
        "available": False,
        "etf_symbol": str(symbol),
        "futures_symbol": None,
    }
    try:
        from lib import store  # noqa: E402 — invest-a-stock 路径引导
    except ImportError:
        result["note"] = "futures 数据层不可用（invest-a-stock 路径缺失）"
        return result

    fsym = futures_symbol_for_etf(symbol)
    if fsym is None:
        result["note"] = "该 ETF 无股指期货对冲品种（hedge-map 无 futures 映射）"
        return result
    if fsym not in ("IF", "IH", "IC", "IM"):
        # F1-4: 品种映射存在但 futures_daily 数据层仅覆盖 IF/IH/IC/IM
        result["futures_symbol"] = fsym
        result["note"] = (
            f"品种映射存在（{fsym}期货，hedge-map），但 futures_daily 数据层"
            "仅覆盖 IF/IH/IC/IM，基差状态不可得（非「无对冲品种」）"
        )
        return result
    result["futures_symbol"] = fsym

    try:
        store.init_db()
        rows = store.load_futures_daily(symbol=fsym, limit=days)
    except Exception:  # noqa: BLE001
        result["note"] = "futures_daily 读取失败"
        return result
    if len(rows) < 60:
        result["note"] = f"{fsym} 历史数据不足（{len(rows)} 日），无法计算分位"
        return result

    basis_vals = [float(r["basis_pct"]) for r in rows if r.get("basis_pct") is not None]
    if len(basis_vals) < 60:
        result["note"] = "有效基差样本不足"
        return result
    from stats import percentile_rank_inclusive  # noqa: E402

    latest = rows[-1]
    result.update({
        "available": True,
        "date": latest["date"],
        "contract": latest.get("contract"),
        "current_basis_pct": latest.get("basis_pct"),
        "current_basis_pts": latest.get("basis_pts"),
        "percentile": percentile_rank_inclusive(basis_vals, latest.get("basis_pct")),
        "median_basis_pct": sorted(basis_vals)[len(basis_vals) // 2],
        "n_history": len(basis_vals),
        "source": latest.get("source"),
    })
    # 历史演变分布参照（描述性，非预测）：当前分位 ±10 邻域内的历史事件 → 其后 20 日收益分布
    # 由 F2 报告表提供（此处仅给出引用锚点，不在查询时实时计算——P0 数字须预计算）
    result["distribution_ref"] = "docs/data/F2_backtest_result.json（状态-演变分布，报告引用）"
    result["note"] = (
        "状态度量与历史演变分布参照（用户定稿：不做市场预测）；"
        "分红期基差假收窄口径注记；当月合约 settle 口径"
    )
    return result
