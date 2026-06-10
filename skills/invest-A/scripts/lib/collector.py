"""数据采集模块。封装各数据源，依赖 env.py 做可用性检测。"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any, Callable

from . import env

logger = logging.getLogger(__name__)


def _ts_code(symbol: str) -> str:
    """转为 Tushare 格式：600176 → 600176.SH, 000858 → 000858.SZ"""
    s = symbol.strip().zfill(6)
    if s.startswith(("6", "9")):
        return f"{s}.SH"
    return f"{s}.SZ"

CollectResult = dict[str, Any]


def _result(data: Any, source: str, source_group: str = "",
            confidence: str = "medium", fallback_chain: list[str] | None = None,
            latency_ms: float = 0) -> CollectResult:
    return {
        "data": data,
        "_meta": {
            "source": source, "source_group": source_group,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "fallback_chain": fallback_chain or [],
            "confidence": confidence, "latency_ms": round(latency_ms, 1),
            "success": data is not None,
        },
    }


def _error(message: str, attempted: list[str]) -> CollectResult:
    return {
        "data": None, "error": message,
        "_meta": {"source": "none", "source_group": "unknown",
                   "fetched_at": datetime.now(timezone.utc).isoformat(),
                   "fallback_chain": attempted, "confidence": "low",
                   "latency_ms": 0, "success": False, "error_type": "empty",
                   "attempted_sources": attempted},
    }


# ---- 各维度采集 ----

def collect_basic_info(symbol: str) -> CollectResult:
    config = env.get_config()
    start = time.time()
    if env.is_tushare_available(config):
        try:
            tc = _tushare_client(config)
            df = tc.query("stock_basic", ts_code=_ts_code(symbol),
                          fields="ts_code,name,area,industry,market,list_date")
            if df is not None and not df.empty:
                return _result(df.iloc[0].to_dict(), "tushare.stock_basic",
                               "tushare", "high", latency_ms=(time.time()-start)*1000)
        except Exception as e:
            logger.warning("Tushare basic_info 失败: %s", e)
    return _error("基本信息不可得（需 TUSHARE_TOKEN）", ["tushare"])


def collect_financials(symbol: str) -> CollectResult:
    config = env.get_config()
    start = time.time()
    if env.is_tushare_available(config):
        try:
            tc = _tushare_client(config)
            df = tc.query("fina_indicator", ts_code=_ts_code(symbol),
                          start_date="20240101", end_date="20251231",
                          fields="ts_code,end_date,roe,eps,profit_dedt,revenue,net_profit")
            if df is not None and not df.empty:
                return _result(df.to_dict("records"), "tushare.fina_indicator",
                               "tushare", "high", latency_ms=(time.time()-start)*1000)
        except Exception as e:
            logger.warning("Tushare financials 失败: %s", e)
    return _error("财务数据不可得（需 TUSHARE_TOKEN）", ["tushare"])


def collect_quote(symbol: str) -> CollectResult:
    config = env.get_config()
    start = time.time()
    if env.is_tushare_available(config):
        try:
            tc = _tushare_client(config)
            df = tc.query("daily", ts_code=_ts_code(symbol),
                          start_date="20260601", end_date="20260610",
                          fields="trade_date,open,high,low,close,vol,amount")
            if df is not None and not df.empty:
                return _result(df.iloc[0].to_dict(), "tushare.daily",
                               "tushare", "high", latency_ms=(time.time()-start)*1000)
        except Exception as e:
            logger.warning("Tushare quote 失败: %s", e)
    # Tencent fallback
    try:
        import requests
        market = "sh" if symbol.startswith(("6", "9")) else "sz"
        r = requests.get(f"http://qt.gtimg.cn/q={market}{symbol}", timeout=5)
        if r.status_code == 200 and "~" in r.text:
            p = r.text.split("~")
            if len(p) > 45:
                q = {"price": float(p[3]), "change_pct": float(p[32]) if p[32] else 0,
                     "high": float(p[33]), "low": float(p[34]),
                     "volume": float(p[6]), "turnover_rate": float(p[38]) if p[38] else 0,
                     "pe_ratio": float(p[39]) if p[39] else 0,
                     "total_mv": float(p[45]) / 10000 if p[45] else 0}
                return _result(q, "tencent_finance", "tencent", "medium",
                               latency_ms=(time.time()-start)*1000)
    except Exception:
        pass
    return _error("行情数据不可得", ["tushare", "tencent"])


def collect_shareholders(symbol: str) -> CollectResult:
    config = env.get_config()
    start = time.time()
    if env.is_tushare_available(config):
        try:
            tc = _tushare_client(config)
            df = tc.query("top10_floatholders", ts_code=_ts_code(symbol),
                          period="20260331",
                          fields="ts_code,end_date,holder_name,hold_amount,hold_ratio")
            if df is not None and not df.empty:
                return _result(df.to_dict("records"), "tushare.top10_floatholders",
                               "tushare", "high", latency_ms=(time.time()-start)*1000)
        except Exception as e:
            logger.warning("Tushare shareholders 失败: %s", e)
    return _error("股东数据不可得", ["tushare"])


def collect_northbound(symbol: str) -> CollectResult:
    config = env.get_config()
    start = time.time()
    if env.is_tushare_available(config):
        try:
            tc = _tushare_client(config)
            df = tc.query("moneyflow", ts_code=_ts_code(symbol),
                          start_date="20260601", end_date="20260610",
                          fields="ts_code,trade_date,buy_sm_vol,sell_sm_vol,net_mf_vol")
            if df is not None and not df.empty:
                return _result(df.to_dict("records"), "tushare.moneyflow",
                               "tushare", "medium", latency_ms=(time.time()-start)*1000)
        except Exception as e:
            logger.warning("Tushare northbound 失败: %s", e)
    return _error("北向资金数据不可得", ["tushare"])


def collect_kline(symbol: str, start_date: str = "", end_date: str = "") -> CollectResult:
    config = env.get_config()
    start = time.time()
    if env.is_tushare_available(config):
        try:
            tc = _tushare_client(config)
            params = {"ts_code": _ts_code(symbol)}
            if start_date: params["start_date"] = start_date
            if end_date: params["end_date"] = end_date
            df = tc.query("daily", **params)
            if df is not None and not df.empty:
                return _result(df.to_dict("records"), "tushare.daily",
                               "tushare", "high", latency_ms=(time.time()-start)*1000)
        except Exception as e:
            logger.warning("Tushare kline 失败: %s", e)
    return _error("K线数据不可得", ["tushare"])


# ---- Tushare 客户端惰性加载 ----

_tc_instance = None

def _tushare_client(config: dict) -> Any:
    global _tc_instance
    if _tc_instance is None:
        from lib.tushare_client import TushareClient
        _tc_instance = TushareClient(token=config.get("TUSHARE_TOKEN"))
    return _tc_instance


# ---- 全维度采集 ----

def collect_all(symbol: str, dims: list[str] | None = None) -> dict[str, Any]:
    if dims is None:
        dims = ["basic_info", "financials", "quote", "shareholders", "northbound"]

    collectors = {
        "basic_info": ("基本信息", collect_basic_info),
        "financials": ("财务报告", collect_financials),
        "quote": ("实时行情", collect_quote),
        "shareholders": ("十大股东", collect_shareholders),
        "northbound": ("北向资金", collect_northbound),
        "kline": ("日K线", collect_kline),
    }

    dimensions = []
    for dim in dims:
        if dim not in collectors:
            continue
        display, fn = collectors[dim]
        result = fn(symbol)
        status = "available" if result.get("data") is not None else ("degraded" if result.get("error") else "missing")
        dimensions.append({
            "dimension": dim, "display": display,
            "data": result.get("data"), "error": result.get("error"),
            "_meta": result.get("_meta", {}), "status": status,
        })

    avail = sum(1 for d in dimensions if d["status"] == "available")
    return {
        "symbol": symbol,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "dimensions": dimensions,
        "summary": {"total": len(dimensions), "available": avail,
                     "degraded": sum(1 for d in dimensions if d["status"] == "degraded"),
                     "missing": sum(1 for d in dimensions if d["status"] == "missing")},
    }
