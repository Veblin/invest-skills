"""轻量数据查询模块 — 为 journal 评估提供按需数据。

直接调 invest-a-stock 底层采集函数（collect_quote / collect_kline /
collect_valuation / collect_macro_context），不走 collect_all() 后处理链。

v0.2.1：PE 分位依赖 Tushare；无 Tushare 时标注"无历史分位"。
"""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from _invest_path import ensure_invest_a_scripts_on_path

ensure_invest_a_scripts_on_path()

from lib.collector import collect_quote, collect_kline, collect_valuation  # noqa: E402
from lib.nums import safe_float  # noqa: E402
from lib.technical import compute  # noqa: E402
from lib.macro import collect_macro_context  # noqa: E402

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------

def query_for_evaluation(symbol: str, asset_type: str = "stock") -> dict[str, Any]:
    """为日志评估查询关键数据。

    并行采集 quote + kline + macro + valuation，5-10 秒完成。
    任一维度失败 → 部分返回 + data_quality 标注，不阻塞评估。

    Parameters
    ----------
    symbol : str
        6 位股票/ETF 代码（如 "600176"、"563300"）。
    asset_type : str
        "stock" | "etf"。

    Returns
    -------
    dict
        {symbol, asset_type, quote, valuation, technical, macro_snapshot,
         market_microstructure, etf_data, data_quality}
    """
    t0 = time.monotonic()

    if asset_type == "etf":
        try:
            from etf_data import prefetch_etf_spot
            prefetch_etf_spot()
        except Exception as exc:
            logger.warning("etf spot prefetch failed: %s", exc)

    result: dict[str, Any] = {
        "symbol": symbol,
        "asset_type": asset_type,
        "quote": {},
        "valuation": {},
        "technical": {},
        "macro_snapshot": {},
        "market_microstructure": None,
        "etf_data": None,
        "data_quality": {},
    }

    # --- 并行采集 quote + kline + valuation + macro ---
    futures: dict[str, Any] = {}
    with ThreadPoolExecutor(max_workers=4) as ex:
        if asset_type == "etf":
            futures["quote"] = ex.submit(_safe_etf_quote, symbol)
            futures["kline"] = ex.submit(_safe_etf_kline, symbol)
            futures["valuation"] = ex.submit(_safe_collect_valuation_skip, symbol)
        else:
            futures["quote"] = ex.submit(_safe_collect_quote, symbol)
            futures["kline"] = ex.submit(_safe_collect_kline, symbol)
            futures["valuation"] = ex.submit(_safe_collect_valuation, symbol)
        futures["macro"] = ex.submit(_safe_collect_macro, symbol)

        for key, fut in futures.items():
            try:
                result[key] = fut.result(timeout=30)
            except Exception as exc:
                logger.warning("%s collect failed: %s", key, exc)
                result[key] = {"_error": str(exc)}

    # --- 技术指标计算（基于 kline data） ---
    _compute_technical(result)

    # --- 宏观快照 ---
    _process_macro(result)

    # --- 市场微观结构：个股与 ETF 评估均注入（环境标签 / 护栏） ---
    result["market_microstructure"] = _safe_collect_microstructure()

    # --- ETF 专属 ---
    if asset_type == "etf":
        result["etf_data"] = _safe_collect_etf(symbol)

    # --- 汇总 data_quality ---
    _summarize_quality(result)

    elapsed = time.monotonic() - t0
    result["_elapsed_ms"] = round(elapsed * 1000)
    logger.info("query_for_evaluation(%s) done in %.1fs", symbol, elapsed)

    return result


# ---------------------------------------------------------------------------
# 子采集（safe wrappers）
# ---------------------------------------------------------------------------

def _safe_collect_quote(symbol: str) -> dict:
    try:
        raw = collect_quote(symbol)
        data = raw.get("data", {})
        meta = raw.get("_meta", {})
        # data 可能是 list[dict] 或 dict
        if isinstance(data, list) and data:
            data = data[-1]
        elif not isinstance(data, dict):
            data = {}

        return {
            "price": safe_float(data.get("close")),
            "change_pct": safe_float(data.get("pct_chg")),
            "pe_ttm": safe_float(data.get("pe_ttm")),
            "pb": safe_float(data.get("pb")),
            "total_mv": safe_float(data.get("total_mv")),
            "source": meta.get("source", "unknown"),
            "status": _status_from_raw(raw),
            "_raw_status": raw.get("status"),
        }
    except Exception as exc:
        return {"_error": str(exc), "status": "missing"}


def _safe_collect_kline(symbol: str) -> dict:
    try:
        raw = collect_kline(symbol)
        data = raw.get("data", [])
        if not isinstance(data, list):
            data = []
        meta = raw.get("_meta", {})
        return {
            "rows": len(data),
            "data": data,
            "source": meta.get("source", "unknown"),
            "status": _status_from_raw(raw),
            "first_date": data[0].get("trade_date", "") if data else "",
            "last_date": data[-1].get("trade_date", "") if data else "",
        }
    except Exception as exc:
        return {"_error": str(exc), "rows": 0, "data": [], "status": "missing"}


def _safe_collect_valuation(symbol: str) -> dict:
    try:
        raw = collect_valuation(symbol)
        data = raw.get("data", {})
        meta = raw.get("_meta", {})
        # data 可能是 list (Tushare 日频序列) 或 dict (腾讯快照)
        pe_list: list[float] = []
        pb_list: list[float] = []
        pe_current: float | None = None
        pb_current: float | None = None
        history_available = False

        if isinstance(data, list) and data:
            history_available = True
            for d in data:
                pe = safe_float(d.get("pe_ttm"))
                pb = safe_float(d.get("pb"))
                if pe is not None:
                    pe_list.append(pe)
                if pb is not None:
                    pb_list.append(pb)
            if pe_list:
                pe_current = pe_list[-1]
            if pb_list:
                pb_current = pb_list[-1]
        elif isinstance(data, dict):
            pe_current = safe_float(data.get("pe_ttm"))
            pb_current = safe_float(data.get("pb"))

        return {
            "pe_current": pe_current,
            "pb_current": pb_current,
            "pe_percentile": _percentile(pe_current, pe_list) if history_available else None,
            "pb_percentile": _percentile(pb_current, pb_list) if history_available else None,
            "pe_median": _median(pe_list) if pe_list else None,
            "pb_median": _median(pb_list) if pb_list else None,
            "history_available": history_available,
            "history_rows": len(pe_list),
            "source": meta.get("source", "unknown"),
            "status": _status_from_raw(raw),
            "note": "" if history_available else "无历史分位（仅当前快照）",
        }
    except Exception as exc:
        return {"_error": str(exc), "status": "missing", "history_available": False}


def _safe_collect_macro(symbol: str) -> dict:
    try:
        return collect_macro_context(symbol)
    except Exception as exc:
        return {"status": "all_failed", "indicators": {}, "_error": str(exc)}


def _safe_etf_quote(symbol: str) -> dict:
    """ETF 行情（fund_etf_spot_em）。"""
    try:
        from etf_data import query_etf_quote
        raw = query_etf_quote(symbol)
        return {
            "price": raw.get("price"),
            "change_pct": raw.get("change_pct"),
            "pe_ttm": None,
            "pb": None,
            "total_mv": None,
            "source": "akshare.fund_etf_spot_em",
            "status": raw.get("status", "missing"),
        }
    except Exception as exc:
        return {"_error": str(exc), "status": "missing"}


def _safe_etf_kline(symbol: str) -> dict:
    """ETF 净值序列 + 波动率（fund_etf_fund_info_em）。"""
    try:
        from etf_data import query_etf_kline
        raw = query_etf_kline(symbol, days=60)
        return {
            "rows": raw.get("nav_rows", 0),
            "data": raw.get("nav_history", []),
            "source": "akshare.fund_etf_fund_info_em",
            "status": raw.get("status", "missing"),
            "volatility_annualized": raw.get("volatility_annualized"),
            "latest_nav": raw.get("latest_nav"),
            "rsi": raw.get("rsi"),
            "rsi_period": raw.get("rsi_period"),
            "rsi_24": raw.get("rsi_24"),
            "ma20": raw.get("ma20"),
            "ma60": raw.get("ma60"),
        }
    except Exception as exc:
        return {"_error": str(exc), "rows": 0, "data": [], "status": "missing"}


def _safe_collect_valuation_skip(symbol: str) -> dict:
    """ETF 跳过 stock collector valuation（PE 来自 csindex / etf_data）。"""
    return {"_skipped": True, "note": "ETF PE 来自 csindex（etf_data.index_pe）", "status": "not_applicable"}


def _safe_collect_etf(symbol: str) -> dict:
    try:
        from etf_data import query_etf_data
        return query_etf_data(symbol)
    except Exception as exc:
        return {"_error": str(exc)}


def _safe_collect_microstructure() -> dict:
    try:
        from market_microstructure import snapshot
        return snapshot()
    except Exception as exc:
        return {"_error": str(exc)}


# ---------------------------------------------------------------------------
# 加工
# ---------------------------------------------------------------------------

def _compute_technical(result: dict) -> None:
    """基于 kline data 计算技术指标。

    个股：调 technical.compute(rows)。
    ETF：使用 kline 中预计算的值（净值序列，非 OHLCV）。
    """
    kline = result.get("kline", {})
    rows = kline.get("data", [])
    is_etf = result.get("asset_type") == "etf"

    result["technical"] = {
        "volatility_annualized": None,
        "rsi": None,
        "rsi_period": None,
        "rsi_24": None,
        "latest_close": None,
        "ma20": None,
        "ma60": None,
        "kline_days": len(rows) if isinstance(rows, list) else 0,
        "status": "missing",
    }

    # ETF：净值序列已预计算波动率/RSI/MA
    if is_etf:
        result["technical"]["latest_close"] = kline.get("latest_nav")
        result["technical"]["volatility_annualized"] = kline.get("volatility_annualized")
        result["technical"]["rsi"] = kline.get("rsi")
        result["technical"]["rsi_period"] = kline.get("rsi_period")
        result["technical"]["rsi_24"] = kline.get("rsi_24")
        result["technical"]["ma20"] = kline.get("ma20")
        result["technical"]["ma60"] = kline.get("ma60")
        rows_count = kline.get("rows", len(rows) if isinstance(rows, list) else 0)
        kline_status = kline.get("status", "missing")
        if rows_count == 0:
            result["technical"]["status"] = "missing"
        elif kline_status == "available":
            result["technical"]["status"] = "available"
        elif kline_status == "missing":
            result["technical"]["status"] = "missing"
        elif rows_count < 20:
            result["technical"]["status"] = "insufficient"
        else:
            result["technical"]["status"] = kline_status
        return

    # 个股：调 technical.compute(rows)
    if not rows or not isinstance(rows, list) or len(rows) < 20:
        result["technical"]["status"] = "insufficient"
        return

    try:
        tech = compute(rows)
    except Exception as exc:
        logger.warning("technical.compute failed: %s", exc)
        result["technical"]["status"] = "fetch_failed"
        return

    result["technical"]["latest_close"] = tech.get("latest_close")

    vc = tech.get("volatility_cone", {})
    if vc:
        by_win = vc.get("by_window", {})
        d20 = by_win.get("20") or by_win.get(20)
        if d20 is not None:
            result["technical"]["volatility_annualized"] = round(float(d20), 2)

    rsi = tech.get("overbought_oversold", {}).get("rsi", {}).get("24", {})
    if rsi.get("available"):
        result["technical"]["rsi_24"] = rsi.get("value")

    ma = tech.get("trend", {}).get("ma", {})
    ma20_vals = ma.get("20", [])
    ma60_vals = ma.get("60", [])
    if ma20_vals:
        result["technical"]["ma20"] = ma20_vals[-1]
    if ma60_vals:
        result["technical"]["ma60"] = ma60_vals[-1]

    result["technical"]["status"] = "available"


def _process_macro(result: dict) -> None:
    """将 macro 返回值展平为 journal 友好格式。"""
    macro_raw = result.get("macro", {})
    indicators = macro_raw.get("indicators", {})
    if not indicators:
        result["macro_snapshot"] = {"status": macro_raw.get("status", "missing")}
        return

    snap: dict[str, Any] = {"status": macro_raw.get("status", "ok")}
    for key in ("pmi", "cpi", "ppi", "lpr", "vix", "sox"):
        ind = indicators.get(key)
        if ind:
            snap[key] = {
                "value": ind.get("value"),
                "signal": ind.get("signal", ""),
                "source": ind.get("source", ""),
            }
        else:
            snap[key] = None
    result["macro_snapshot"] = snap


def _summarize_quality(result: dict) -> None:
    """汇总 data_quality。"""
    dq: dict[str, str] = {}

    # quote
    q = result.get("quote", {})
    dq["quote"] = q.get("status", "missing")

    # kline
    k = result.get("kline", {})
    dq["kline"] = k.get("status", "missing")

    # valuation
    v = result.get("valuation", {})
    dq["valuation"] = v.get("status", "missing")

    # technical (computed)
    t = result.get("technical", {})
    dq["technical"] = t.get("status", "missing")

    # macro
    m = result.get("macro", {})
    dq["macro"] = m.get("status", "missing") if isinstance(m, dict) else "missing"

    # etf
    etf = result.get("etf_data")
    if etf is not None:
        from etf_data import rollup_etf_quality_status

        dq["etf"] = rollup_etf_quality_status(etf)
        for key, val in (etf.get("data_quality") or {}).items():
            dq[f"etf_{key}"] = val
    else:
        dq["etf"] = "not_applicable"

    # microstructure
    ms = result.get("market_microstructure")
    if ms is not None:
        dq["microstructure"] = "available" if not ms.get("_error") else "missing"
    else:
        dq["microstructure"] = "not_applicable"

    # overall: worst of all
    statuses = list(dq.values())
    if all(s == "available" or s == "not_applicable" for s in statuses):
        dq["overall"] = "available"
    elif all(s in ("missing", "not_applicable") for s in statuses):
        dq["overall"] = "critical_missing"
    else:
        dq["overall"] = "partial"

    result["data_quality"] = dq


def _percentile(value: float | None, population: list[float]) -> float | None:
    """计算 value 在 population 中的分位（0-100）。"""
    if value is None or not population:
        return None
    sorted_vals = sorted(population)
    rank = sum(1 for x in sorted_vals if x < value)
    return round(rank / len(sorted_vals) * 100, 1)


def _median(population: list[float]) -> float | None:
    if not population:
        return None
    s = sorted(population)
    n = len(s)
    if n % 2 == 1:
        return s[n // 2]
    return round((s[n // 2 - 1] + s[n // 2]) / 2, 4)


def _status_from_raw(raw: dict) -> str:
    """将 collector 返回的 status 映射到 8 态枚举。"""
    status = raw.get("status", "missing")
    meta = raw.get("_meta", {})
    if status == "available":
        # 检查是否为降级源
        sources = meta.get("all_sources", [])
        primary_ok = any(
            s.get("source_group") == "primary" and s.get("success")
            for s in sources
        )
        return "available" if primary_ok else "degraded"
    if status == "partial":
        return "partial"
    return "missing"
