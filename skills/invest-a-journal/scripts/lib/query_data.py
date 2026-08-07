"""轻量数据查询模块 — 为 journal 评估提供按需数据。

经 skills/lib/data_bridge 调 invest-a-stock 底层采集函数（get_quote /
get_kline / get_valuation / get_macro），自动享受 TTL 缓存，不走
collect_all() 后处理链。

v0.2.1：PE 分位依赖 Tushare；无 Tushare 时标注"无历史分位"。
"""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from _invest_path import ensure_invest_a_scripts_on_path

ensure_invest_a_scripts_on_path()

from data_bridge import (  # noqa: E402
    get_kline,
    get_macro,
    get_microstructure,
    get_quote,
    get_valuation,
)
from lib.nums import safe_float  # noqa: E402
from lib.technical import compute, sort_kline_asc  # noqa: E402
from lib.valuation import valuation_summary  # noqa: E402

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
        raw = get_quote(symbol)
        data = raw.get("data", {})
        meta = raw.get("_meta", {})
        # data 可能是 list[dict] 或 dict；list 源（Tushare 等）常为降序，先升序再取最新
        if isinstance(data, list) and data:
            data = sort_kline_asc(data)[-1]
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
        raw = get_kline(symbol)
        data = raw.get("data", [])
        if not isinstance(data, list):
            data = []
        # Tushare 等源常为降序，升序后再取首/末日期，避免 first/last 颠倒
        if data:
            data = sort_kline_asc(data)
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
        raw = get_valuation(symbol)
        data = raw.get("data", {})
        meta = raw.get("_meta", {})
        # data 可能是 list (Tushare 日频序列) 或 dict (腾讯快照)
        pe_list: list[float] = []
        pb_list: list[float] = []
        pe_dates: list[str] = []
        pb_dates: list[str] = []
        pe_current: float | None = None
        pb_current: float | None = None
        pe_date: str | None = None
        pb_date: str | None = None
        pe_stale = pb_stale = False
        history_available = False

        if isinstance(data, list) and data:
            history_available = True
            # Tushare 等源常为降序，升序后取末尾即为最新一期
            data = sort_kline_asc(data)
            for d in data:
                td = str(d.get("trade_date") or "")
                pe = safe_float(d.get("pe_ttm"))
                pb = safe_float(d.get("pb"))
                # 剔除亏损期负 PE/PB：负值参与分位会抬高"分位"与拉低中位数
                # （CLAUDE.md P0-2 口径；与 invest-a-stock valuation.py 一致）
                if pe is not None and pe > 0:
                    pe_list.append(pe)
                    pe_dates.append(td)
                if pb is not None and pb > 0:
                    pb_list.append(pb)
                    pb_dates.append(td)
            if pe_list:
                pe_current = pe_list[-1]
                pe_date = pe_dates[-1]
            if pb_list:
                pb_current = pb_list[-1]
                pb_date = pb_dates[-1]
            # 陈旧性：最新报告期无正 PE/PB（亏损期被 >0 过滤）→ 当前值回退
            # 自旧期。pe_date != 最新期即回退（亏损期被剔除，两者必然不等）。
            # 信号须进入报告 prose（SKILL.md 数据快照节指示），否则
            # "当前亏损却显示旧期 PE 8.0x"会被静默当成当期估值。
            latest_td = str(data[-1].get("trade_date") or "")
            pe_stale = bool(pe_dates) and bool(latest_td) and pe_dates[-1] != latest_td
            pb_stale = bool(pb_dates) and bool(latest_td) and pb_dates[-1] != latest_td
        elif isinstance(data, dict):
            pe_current = safe_float(data.get("pe_ttm"))
            pb_current = safe_float(data.get("pb"))
            pe_date = str(data.get("trade_date") or "") or None
            pb_date = pe_date
            latest_td = ""

        note = ""
        if not history_available:
            note = "无历史分位（仅当前快照）"
        elif pe_stale or pb_stale:
            stale_parts = []
            if pe_stale:
                stale_parts.append(
                    f"最新报告期 {latest_td} 无正 PE（亏损期），"
                    f"当前 PE {pe_current} 回退自 {pe_date}")
            if pb_stale:
                stale_parts.append(
                    f"最新报告期 {latest_td} 无正 PB（亏损期），"
                    f"当前 PB {pb_current} 回退自 {pb_date}")
            note = "；".join(stale_parts)

        # 分位/中位数委托 invest-a-stock lib.valuation.valuation_summary：
        # >0 过滤 + 严格 percentile_rank + median 的唯一实现（journal 此前
        # 自维护一份 percentile_rank_inclusive 副本，同一缓存两个分位语义）
        summary = valuation_summary(
            pe_list, pb_list, current_pe=pe_current, current_pb=pb_current)
        pe_stat = summary.get("pe", {})
        pb_stat = summary.get("pb", {})

        return {
            "pe_current": pe_current,
            "pb_current": pb_current,
            # 最新正 PE/PB 的报告期（亏损期回退旧值时供消费者识别陈旧）
            "pe_date": pe_date,
            "pb_date": pb_date,
            # 最新报告期亏损、当前值回退自旧期（pe_date/pb_date）的标志；
            # 报告 prose 必须注明数据期与回退原因（见 SKILL.md 数据快照节）
            "pe_stale": pe_stale,
            "pb_stale": pb_stale,
            "pe_percentile": pe_stat.get("pct") if history_available else None,
            "pb_percentile": pb_stat.get("pct") if history_available else None,
            "pe_median": pe_stat.get("median"),
            "pb_median": pb_stat.get("median"),
            "history_available": history_available,
            "history_rows": len(pe_list),
            "source": meta.get("source", "unknown"),
            "status": _status_from_raw(raw),
            "note": note,
        }
    except Exception as exc:
        return {"_error": str(exc), "status": "missing", "history_available": False}


def _safe_collect_macro(symbol: str) -> dict:
    try:
        # 宏观数据非个股维度，data_bridge.get_macro() 不接收 symbol
        return get_macro()
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
            "ma20": raw.get("ma20"),
            "ma60": raw.get("ma60"),
            "index_ma20": raw.get("index_ma20"),
            "index_ma60": raw.get("index_ma60"),
            "boll_upper": raw.get("boll_upper"),
            "boll_mid": raw.get("boll_mid"),
            "boll_lower": raw.get("boll_lower"),
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
    """走 data_bridge 5min TTL 缓存（v0.2.3）；避免每次评估重采 8 个数据源。"""
    try:
        snap = get_microstructure()
        return snap if snap is not None else {"_error": "microstructure unavailable"}
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
        "index_ma20": None,
        "index_ma60": None,
        "boll_upper": None,
        "boll_mid": None,
        "boll_lower": None,
        "kline_days": len(rows) if isinstance(rows, list) else 0,
        "status": "missing",
    }

    # ETF：净值序列已预计算波动率/RSI/MA
    if is_etf:
        kline_error = kline.get("_error")
        if kline_error:
            result["technical"]["status"] = "fetch_failed"
            result["technical"]["kline_error"] = str(kline_error)
            return
        result["technical"]["latest_close"] = kline.get("latest_nav")
        result["technical"]["volatility_annualized"] = kline.get("volatility_annualized")
        result["technical"]["rsi"] = kline.get("rsi")
        result["technical"]["rsi_period"] = kline.get("rsi_period")
        result["technical"]["ma20"] = kline.get("ma20")
        result["technical"]["ma60"] = kline.get("ma60")
        result["technical"]["index_ma20"] = kline.get("index_ma20")
        result["technical"]["index_ma60"] = kline.get("index_ma60")
        result["technical"]["boll_upper"] = kline.get("boll_upper")
        result["technical"]["boll_mid"] = kline.get("boll_mid")
        result["technical"]["boll_lower"] = kline.get("boll_lower")
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
    if not rows or not isinstance(rows, list):
        result["technical"]["status"] = "missing"
        return
    if len(rows) < 20:
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
    # v0.2.4: 宏观日快照入库（best-effort；journal 每次评估必经此处，天然 1 行/天幂等累积，
    # 缓解 macro 缓存 TTL 7d 造成的断档，供宏观护栏历史分位消费）
    try:
        from lib.store import save_macro_snapshot
        save_macro_snapshot(macro_raw)
    except Exception:
        logger.warning("macro snapshot persist failed", exc_info=True)


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


def _status_from_raw(raw: dict) -> str:
    """将 collector 返回的 status 映射到 8 态枚举。"""
    status = raw.get("status", "missing")
    meta = raw.get("_meta", {})
    if status == "available":
        # 维度级 _meta 恒带 success（schema._best_meta：有 primary 源即 True，
        # "merged:" 前缀源同样携带）；缺 key/非 True（legacy 缓存、手写 meta、
        # 未来生产者）一律 fail-closed 降级——不信任未标注的维度
        return "available" if meta.get("success") is True else "degraded"
    if status == "partial":
        return "partial"
    return "missing"
