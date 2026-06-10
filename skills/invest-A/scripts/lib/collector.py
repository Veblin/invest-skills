"""数据采集模块。封装各数据源，依赖 env.py 做可用性检测。

数据源策略（按优先级）：
  有 Token: Tushare ∥ akshare → 腾讯行情 → 标注不可得
  无 Token: akshare → 腾讯行情 → 标注不可得
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone, timedelta
from typing import Any, Callable

from . import env

logger = logging.getLogger(__name__)


# ---- 日期工具（函数形式，避免导入时固化） ----

def _today() -> str:
    return datetime.now().strftime("%Y%m%d")


def _days_ago(n: int) -> str:
    return (datetime.now() - timedelta(days=n)).strftime("%Y%m%d")


def _latest_quarter_end() -> str:
    """返回最近一个已完整的季度末日期（0331/0630/0930/1231）。"""
    now = datetime.now()
    quarter_ends = [
        (now.year, "0331"),
        (now.year, "0630"),
        (now.year, "0930"),
        (now.year, "1231"),
    ]
    for y, md in reversed(quarter_ends):
        d = datetime.strptime(f"{y}{md}", "%Y%m%d")
        if now > d:
            return f"{y}{md}"
    # 极端情况：当前日期在 1 月 1 日-3 月 31 日间回到上一年
    return f"{now.year - 1}1231"


def _ts_code(symbol: str) -> str:
    """转为 Tushare 格式：600176 → 600176.SH, 000858 → 000858.SZ。

    交易所判断：
      上海: 6xxx → .SH
      北京: 8xxx, 4xxx → .BJ
      深圳: 0xxx, 2xxx, 3xxx → .SZ
    """
    s = symbol.strip().zfill(6)
    if s.startswith("6"):
        return f"{s}.SH"
    if s.startswith(("4", "8")):
        return f"{s}.BJ"
    return f"{s}.SZ"


# ---- 类型别名 ----

CollectResult = dict[str, Any]


# ---- 结果构建辅助 ----

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


# ---- 通用 Tushare 查询辅助 ----

def _tushare_query(
    api_name: str,
    symbol: str,
    fields: str,
    result_source: str,
    confidence: str = "high",
    **kwargs: Any,
) -> CollectResult:
    """执行 Tushare 查询，减少重复模板代码。"""
    config = env.get_config()
    start = time.time()
    if not env.is_tushare_available(config):
        return _error(f"{result_source}不可得（需 TUSHARE_TOKEN）", ["tushare"])
    try:
        tc = _tushare_client(config)
        df = tc.query(api_name, ts_code=_ts_code(symbol), fields=fields, **kwargs)
        if df is not None and not df.empty:
            data = df.iloc[0].to_dict() if len(df) == 1 else df.to_dict("records")
            return _result(data, result_source, "tushare", confidence,
                           latency_ms=(time.time() - start) * 1000)
    except Exception as e:
        logger.warning("Tushare %s 失败: %s", api_name, e)
    return _error(f"{result_source}不可得", ["tushare"])


# ---- 通用 akshare 查询辅助 ----

def _akshare_query(
    fn: Callable[[], Any],
    result_source: str,
    confidence: str = "medium",
    fallback_chain: list[str] | None = None,
) -> CollectResult:
    """执行 akshare 查询，自动处理异常和计时。"""
    start = time.time()
    try:
        data = fn()
        if data is not None:
            # akshare 通常返回 DataFrame
            import pandas as pd
            if isinstance(data, pd.DataFrame) and not data.empty:
                records = data.to_dict("records")
                return _result(records, result_source, "akshare", confidence,
                               fallback_chain=fallback_chain,
                               latency_ms=(time.time() - start) * 1000)
            if isinstance(data, dict) and data:
                return _result(data, result_source, "akshare", confidence,
                               fallback_chain=fallback_chain,
                               latency_ms=(time.time() - start) * 1000)
            if isinstance(data, pd.Series) and not data.empty:
                return _result(data.to_dict(), result_source, "akshare", confidence,
                               fallback_chain=fallback_chain,
                               latency_ms=(time.time() - start) * 1000)
    except Exception as e:
        logger.warning("akshare %s 失败: %s", result_source, e)
    return _error(f"{result_source}不可得（akshare）", (fallback_chain or []) + ["akshare"])


# ---- 各维度采集 ----

def collect_basic_info(symbol: str) -> CollectResult:
    """采集基本信息。Tushare → akshare → 不可得。"""
    # 主路径: Tushare
    result = _tushare_query(
        "stock_basic", symbol,
        fields="ts_code,name,area,industry,market,list_date",
        result_source="tushare.stock_basic",
    )
    if result["data"] is not None:
        return result

    # 兜底: akshare stock_individual_info_em
    if env.is_akshare_available():
        import akshare as ak
        akshare_result = _akshare_query(
            lambda: ak.stock_individual_info_em(symbol=symbol.strip().zfill(6)),
            result_source="akshare.stock_individual_info_em",
            fallback_chain=["tushare"],
        )
        if akshare_result["data"] is not None:
            return akshare_result

    return _error("基本信息不可得", ["tushare", "akshare"])


def collect_financials(symbol: str) -> CollectResult:
    """采集财务指标。Tushare → akshare → 不可得。"""
    # 主路径: Tushare fina_indicator
    result = _tushare_query(
        "fina_indicator", symbol,
        fields="ts_code,end_date,roe,eps,profit_dedt,revenue,net_profit",
        result_source="tushare.fina_indicator",
        start_date=_days_ago(730), end_date=_today(),
    )
    if result["data"] is not None:
        return result

    # 兜底: akshare financial abstract
    if env.is_akshare_available():
        try:
            import akshare as ak
            akshare_result = _akshare_query(
                lambda: ak.stock_financial_abstract_ths(symbol=symbol.strip().zfill(6),
                                                        indicator="按报告期"),
                result_source="akshare.stock_financial_abstract_ths",
                fallback_chain=["tushare"],
            )
            if akshare_result["data"] is not None:
                return akshare_result
        except Exception:
            pass

    return _error("财务数据不可得", ["tushare", "akshare"])


def collect_shareholders(symbol: str) -> CollectResult:
    """采集十大股东。Tushare → akshare → 不可得。"""
    # 主路径: Tushare top10_floatholders（动态计算最近季度）
    result = _tushare_query(
        "top10_floatholders", symbol,
        fields="ts_code,end_date,holder_name,hold_amount,hold_ratio",
        result_source="tushare.top10_floatholders",
        period=_latest_quarter_end(),
    )
    if result["data"] is not None:
        return result

    # 兜底: akshare stock_individual_info_em 中的股东信息
    if env.is_akshare_available():
        try:
            import akshare as ak
            akshare_result = _akshare_query(
                lambda: ak.stock_individual_info_em(symbol=symbol.strip().zfill(6)),
                result_source="akshare.stock_individual_info_em",
                fallback_chain=["tushare"],
            )
            if akshare_result["data"] is not None:
                # 从基本信息中提取股东相关字段
                data = akshare_result["data"]
                if isinstance(data, dict):
                    holders = {k: v for k, v in data.items()
                               if any(w in str(k) for w in ["holder", "股东", "持股"])}
                    if holders:
                        akshare_result["data"] = holders
                return akshare_result
        except Exception:
            pass

    return _error("股东数据不可得", ["tushare", "akshare"])


def collect_quote(symbol: str) -> CollectResult:
    """采集实时行情。Tushare → akshare → 腾讯行情 → 不可得。"""
    # 方案1: Tushare 最新日线
    result = _tushare_query(
        "daily", symbol,
        fields="trade_date,open,high,low,close,vol,amount",
        result_source="tushare.daily",
        start_date=_days_ago(10), end_date=_today(),
    )
    if result["data"] is not None:
        return result

    # 方案2: akshare 日K线
    if env.is_akshare_available():
        try:
            import akshare as ak

            def _to_ak_date(d: str) -> str:
                """20260101 → 2026-01-01"""
                return f"{d[:4]}-{d[4:6]}-{d[6:]}"

            start_ak = _to_ak_date(_days_ago(10))
            end_ak = _to_ak_date(_today())
            akshare_result = _akshare_query(
                lambda: ak.stock_zh_a_hist(symbol=symbol.strip().zfill(6),
                                           period="daily",
                                           start_date=start_ak,
                                           end_date=end_ak,
                                           adjust=""),
                result_source="akshare.stock_zh_a_hist",
                fallback_chain=["tushare"],
            )
            if akshare_result["data"] is not None:
                return akshare_result
        except Exception:
            pass

    # 方案3: 腾讯行情兜底
    try:
        import requests
        market = "sh" if symbol.startswith(("6", "9")) else "sz"
        r = requests.get(f"http://qt.gtimg.cn/q={market}{symbol}", timeout=5)
        if r.status_code == 200 and "~" in r.text:
            p = r.text.split("~")
            if len(p) > 45:
                try:
                    q = {}
                    q["price"] = float(p[3]) if p[3] else 0
                    q["change_pct"] = float(p[32]) if p[32] else 0
                    q["high"] = float(p[33]) if p[33] else 0
                    q["low"] = float(p[34]) if p[34] else 0
                    q["volume"] = float(p[6]) if p[6] else 0
                    q["turnover_rate"] = float(p[38]) if p[38] else 0
                    q["pe_ratio"] = float(p[39]) if p[39] else 0
                    q["total_mv"] = float(p[45]) / 10000 if p[45] else 0
                    return _result(q, "tencent_finance", "tencent", "medium")
                except (ValueError, IndexError) as e:
                    logger.warning("腾讯行情解析失败（字段格式异常）: %s", e)
    except Exception as e:
        logger.warning("腾讯行情请求失败: %s", e)

    return _error("行情数据不可得", ["tushare", "akshare", "tencent"])


def collect_northbound(symbol: str) -> CollectResult:
    """采集北向资金。Tushare moneyflow → akshare → 不可得。"""
    result = _tushare_query(
        "moneyflow", symbol,
        fields="ts_code,trade_date,buy_sm_vol,sell_sm_vol,net_mf_vol",
        result_source="tushare.moneyflow", confidence="medium",
        start_date=_days_ago(10), end_date=_today(),
    )
    if result["data"] is not None:
        return result

    # 兜底: akshare 沪深港通个股资金流向
    if env.is_akshare_available():
        try:
            import akshare as ak
            akshare_result = _akshare_query(
                lambda: ak.stock_hsgt_individual_em(symbol=symbol.strip().zfill(6)),
                result_source="akshare.stock_hsgt_individual_em",
                fallback_chain=["tushare"],
            )
            if akshare_result["data"] is not None:
                return akshare_result
        except Exception:
            pass

    return _error("北向资金数据不可得", ["tushare", "akshare"])


def collect_kline(symbol: str, start_date: str = "", end_date: str = "") -> CollectResult:
    """采集日K线。Tushare → akshare → 不可得。"""
    kwargs: dict[str, Any] = {}
    if start_date:
        kwargs["start_date"] = start_date
    if end_date:
        kwargs["end_date"] = end_date

    # 主路径: Tushare
    result = _tushare_query(
        "daily", symbol,
        fields="trade_date,open,high,low,close,vol,amount",
        result_source="tushare.daily",
        **kwargs,
    )
    if result["data"] is not None:
        return result

    # 兜底: akshare
    if env.is_akshare_available():
        try:
            import akshare as ak
            sd = start_date if start_date else _days_ago(365)
            ed = end_date if end_date else _today()

            def _to_ak_date(d: str) -> str:
                """20260101 → 2026-01-01"""
                return f"{d[:4]}-{d[4:6]}-{d[6:]}"

            akshare_result = _akshare_query(
                lambda: ak.stock_zh_a_hist(symbol=symbol.strip().zfill(6),
                                           period="daily",
                                           start_date=_to_ak_date(sd),
                                           end_date=_to_ak_date(ed),
                                           adjust=""),
                result_source="akshare.stock_zh_a_hist",
                fallback_chain=["tushare"],
            )
            if akshare_result["data"] is not None:
                return akshare_result
        except Exception:
            pass

    return _error("K线数据不可得", ["tushare", "akshare"])


# ---- Tushare 客户端惰性加载 ----

_tc_instance = None


def _tushare_client(config: dict) -> Any:
    global _tc_instance
    if _tc_instance is None:
        from lib.tushare_client import TushareClient
        _tc_instance = TushareClient(token=config.get("TUSHARE_TOKEN"))
    return _tc_instance


# ---- 全维度采集 ----

COLLECTORS = {
    "basic_info": ("基本信息", collect_basic_info),
    "financials": ("财务报告", collect_financials),
    "quote": ("实时行情", collect_quote),
    "shareholders": ("十大股东", collect_shareholders),
    "northbound": ("北向资金", collect_northbound),
    "kline": ("日K线", collect_kline),
}


def collect_all(symbol: str, dims: list[str] | None = None) -> dict[str, Any]:
    if dims is None:
        dims = ["basic_info", "financials", "quote", "shareholders", "northbound"]

    dimensions = []
    for dim in dims:
        if dim not in COLLECTORS:
            continue
        display, fn = COLLECTORS[dim]
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
