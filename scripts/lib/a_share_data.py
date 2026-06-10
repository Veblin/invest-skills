"""
A 股数据采集模块。

覆盖：
- get_stock_info: 概况、行业、实控人
- get_financials: 三张报表关键指标（近3年+最近季报）
- get_daily_kline: 日K线数据
- get_shareholders: 十大股东
- get_governance_signals: 质押、解禁、商誉
- get_institutional_research: 机构调研记录

Fallback 链（Tushare 与 efinance 并列，先到先用）：
  有 Token: Tushare ∥ efinance → akshare → baostock → yfinance → curl → 标注不可得
  无 Token: efinance → akshare → baostock → yfinance → curl → 标注不可得
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import pandas as pd
import requests

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------
# 模块级代理绕过
# 因 akshare/efinance 底层继承系统代理，优先清除代理环境变量
# ------------------------------------------------------------------

def _scrub_proxy() -> None:
    """在模块加载时清除代理环境变量，避免 EastMoney API 被阻断。"""
    proxy_keys = [
        "HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy",
        "ALL_PROXY", "all_proxy", "FTP_PROXY", "ftp_proxy",
    ]
    for k in proxy_keys:
        os.environ.pop(k, None)

_scrub_proxy()

# 确保 requests.Session 默认不信任环境代理
_original_session_init = requests.Session.__init__

def _patched_init(self):
    _original_session_init(self)
    self.trust_env = False

requests.Session.__init__ = _patched_init

# 默认尝试次数
MAX_RETRIES = 3

# ------------------------------------------------------------------
# _meta 构建 helper
# ------------------------------------------------------------------

def _build_meta(
    source: str | None,
    source_group: str | None,
    fallback_chain: list[str],
    attempted_sources: list[str],
    latency_ms: float,
    success: bool,
    warning: str | None = None,
    rows_fetched: int | None = None,
    error_type: str | None = None,
) -> dict[str, Any]:
    """构建 _meta 字段（符合架构关系 §2.2 契约）。"""
    if not success and error_type is None:
        error_type = "empty" if attempted_sources else "network"

    return {
        "source": source or "none",
        "source_group": source_group or "unknown",
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "fallback_chain": fallback_chain,
        "confidence": _confidence(source, success),
        "latency_ms": round(latency_ms, 1),
        "success": success,
        "error_type": error_type,
        **({"warning": warning} if warning else {}),
        **({"rows_fetched": rows_fetched} if rows_fetched is not None else {}),
    }


def _confidence(source: str | None, success: bool) -> str:
    if not success:
        return "low"
    if source and "tushare" in source:
        return "high"
    if source and "efinance" in source:
        return "high"
    if source and "akshare" in source:
        return "medium"
    if source and "baostock" in source:
        return "medium"
    return "low"


def _format_code(code: str) -> str:
    """标准化 A 股代码为纯数字6位格式。"""
    code = str(code).strip().replace(".SH", "").replace(".SZ", "").replace(".BJ", "")
    return code.zfill(6)


def _to_ts_code(code: str) -> str:
    """转为 Tushare 格式（如 600519.SH, 000858.SZ）。"""
    code = _format_code(code)
    if code.startswith(("6", "9")):
        return f"{code}.SH"
    elif code.startswith(("0", "3")):
        return f"{code}.SZ"
    elif code.startswith(("8", "4")):
        return f"{code}.BJ"
    return f"{code}.SH"


# ------------------------------------------------------------------
# 并行 helper
# ------------------------------------------------------------------

def _parallel_tushare_efinance(
    code: str,
    tushare_fn,
    efinance_fn,
    ts_code: str,
) -> dict[str, Any] | None:
    """Tushare 和 efinance 并行请求，先到先用。"""
    results = {}

    def _try_tushare():
        try:
            from scripts.lib.tushare_client import TushareClient
            client = TushareClient()
            if client.is_available():
                return tushare_fn(client, ts_code)
        except Exception:
            pass
        return None

    def _try_efinance():
        try:
            return efinance_fn(code)
        except Exception:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        fut_tushare = pool.submit(_try_tushare)
        fut_efinance = pool.submit(_try_efinance)

        for fut in as_completed([fut_tushare, fut_efinance]):
            try:
                result = fut.result()
                if result:
                    return result
            except Exception:
                pass

    return None


# ------------------------------------------------------------------
# 1. get_stock_info
# ------------------------------------------------------------------

def get_stock_info(code: str) -> dict[str, Any]:
    """获取 A 股公司概况、行业、实控人。

    Returns:
        dict 含 data + _meta
    """
    code = _format_code(code)
    ts_code = _to_ts_code(code)
    attempted_sources: list[str] = []
    result: dict[str, Any] = {}
    source = ""
    source_group = ""
    start = datetime.now(timezone.utc)

    fallback_chain = [
        "tushare.stock_basic ∥ efinance.stock_info",
        "akshare.stock_individual_info_em",
        "baostock.query_stock_basic",
    ]

    # 尝试 Tushare ∥ efinance 并行
    def _tushare_fn(client, tc):
        attempted_sources.append("tushare")
        df = client.query("stock_basic", ts_code=tc, fields="ts_code,name,area,industry,market,list_date")
        if not df.empty:
            row = df.iloc[0]
            return {
                "ts_code": tc,
                "name": str(row.get("name", "")),
                "area": str(row.get("area", "")),
                "industry": str(row.get("industry", "")),
                "market": str(row.get("market", "")),
                "list_date": str(row.get("list_date", "")),
            }
        return None

    def _efinance_fn(c):
        attempted_sources.append("efinance")
        import efinance as ef
        info = ef.stock.get_base_info(c)
        if info and isinstance(info, dict):
            return {
                "ts_code": ts_code,
                "name": str(info.get("股票简称", info.get("name", ""))),
                "industry": str(info.get("行业", "")),
                "area": str(info.get("地区", "")),
            }
        return None

    parallel_result = _parallel_tushare_efinance(code, _tushare_fn, _efinance_fn, ts_code)
    if parallel_result:
        result = parallel_result
        src_key = "tushare" if "list_date" in parallel_result else "efinance"
        source = f"{src_key}.stock_basic"
        source_group = "official" if src_key == "tushare" else "eastmoney"

    # Fallback: akshare
    if not result:
        try:
            import akshare as ak
            attempted_sources.append("akshare")
            info_df = ak.stock_individual_info_em(symbol=code)
            if not info_df.empty:
                info_dict = dict(zip(info_df["item"], info_df["value"]))
                result = {
                    "ts_code": ts_code,
                    "name": str(info_dict.get("股票简称", "")),
                    "industry": str(info_dict.get("行业", "")),
                    "area": "",
                    "total_market_value": str(info_dict.get("总市值", "")),
                    "circulating_market_value": str(info_dict.get("流通市值", "")),
                }
                source = "akshare.stock_individual_info_em"
                source_group = "eastmoney"
        except Exception as e:
            logger.warning("akshare get_stock_info 失败: %s", e)

    # Fallback: baostock
    if not result:
        try:
            import baostock as bs
            attempted_sources.append("baostock")
            bs.login()
            rs = bs.query_stock_basic(code=ts_code)
            if rs.error_code == "0":
                data = rs.data
                if data:
                    row = data[0]
                    result = {
                        "ts_code": ts_code,
                        "name": str(row[1]) if len(row) > 1 else "",
                        "industry": "",
                    }
                    source = "baostock.query_stock_basic"
                    source_group = "academic"
            bs.logout()
        except Exception as e:
            logger.warning("baostock get_stock_info 失败: %s", e)

    latency = (datetime.now(timezone.utc) - start).total_seconds() * 1000

    if not result:
        result = {"error": "公司基本信息不可得", "attempted_sources": attempted_sources}

    result["_meta"] = _build_meta(
        source=source or None,
        source_group=source_group or None,
        fallback_chain=fallback_chain,
        attempted_sources=attempted_sources,
        latency_ms=latency,
        success=bool(source),
    )
    return result


# ------------------------------------------------------------------
# 2. get_financials
# ------------------------------------------------------------------

def get_financials(code: str) -> dict[str, Any]:
    """获取 A 股财务数据：三张报表关键指标（近3年+最近季报）。

    Returns:
        dict 含 data（盈利能力、成长性、现金流、偿债指标）+ _meta
    """
    code = _format_code(code)
    ts_code = _to_ts_code(code)
    attempted_sources: list[str] = []
    result: dict[str, Any] = {}
    source = ""
    source_group = ""
    start = datetime.now(timezone.utc)

    fallback_chain = [
        "tushare ∥ efinance",
        "akshare.stock_financial_abstract",
        "baostock",
    ]

    # Tushare ∥ efinance 并行
    def _tushare_fn(client, tc):
        attempted_sources.append("tushare")
        income = client.query("income", ts_code=tc, limit=12,
                              fields="ts_code,end_date,revenue,operate_profit,total_profit,n_income")
        balance = client.query("balancesheet", ts_code=tc, limit=12,
                               fields="ts_code,end_date,total_assets,total_liab,total_hldr_eqy_exc_min_int")
        cashflow = client.query("cashflow", ts_code=tc, limit=12,
                                fields="ts_code,end_date,n_cashflow_act")
        if not income.empty:
            return _merge_financials_tushare(income, balance, cashflow)
        return None

    def _efinance_fn(c):
        attempted_sources.append("efinance")
        try:
            import efinance as ef
            perf = ef.stock.get_latest_performance_express(c)
            if perf is not None and not (hasattr(perf, 'empty') and perf.empty):
                return _extract_efinance_financials(perf)
        except Exception:
            pass

        # 尝试用 akshare (efinance 内部实际也走东财, akshare 财务接口更稳定)
        try:
            import akshare as ak
            fin_df = ak.stock_financial_abstract(symbol=c)
            if not fin_df.empty:
                return _extract_akshare_financials(fin_df, c)
        except Exception:
            pass
        return None

    parallel_result = _parallel_tushare_efinance(code, _tushare_fn, _efinance_fn, ts_code)
    if parallel_result:
        result = parallel_result
        source = parallel_result.get("_tmp_source", "efinance")
        source_group = "official" if "tushare" in source else "eastmoney"

    # Fallback: akshare financial_abstract
    if not result:
        try:
            import akshare as ak
            attempted_sources.append("akshare")
            fin_df = ak.stock_financial_abstract(symbol=code)
            if not fin_df.empty:
                result = _extract_akshare_financials(fin_df, code)
                source = "akshare.stock_financial_abstract"
                source_group = "eastmoney"
        except Exception as e:
            logger.warning("akshare financial_abstract 失败: %s", e)

    latency = (datetime.now(timezone.utc) - start).total_seconds() * 1000

    if not result:
        result = {"error": "财务数据不可得", "attempted_sources": attempted_sources}

    result.pop("_tmp_source", None)
    result["_meta"] = _build_meta(
        source=source or None,
        source_group=source_group or None,
        fallback_chain=fallback_chain,
        attempted_sources=attempted_sources,
        latency_ms=latency,
        success=bool(source),
    )
    return result


def _merge_financials_tushare(
    income: pd.DataFrame,
    balance: pd.DataFrame,
    cashflow: pd.DataFrame,
) -> dict[str, Any]:
    """合并 Tushare 三张表的关键指标。"""
    result: dict[str, Any] = {"_tmp_source": "tushare.stock_basic"}
    try:
        # 最新一期
        latest_income = income.iloc[0] if not income.empty else None
        latest_balance = balance.iloc[0] if not balance.empty else None
        latest_cf = cashflow.iloc[0] if not cashflow.empty else None

        if latest_income is not None:
            result["revenue"] = _safe_float(latest_income.get("revenue"))
            result["operate_profit"] = _safe_float(latest_income.get("operate_profit"))
            result["net_income"] = _safe_float(latest_income.get("n_income"))
        if latest_balance is not None:
            result["total_assets"] = _safe_float(latest_balance.get("total_assets"))
            result["total_liab"] = _safe_float(latest_balance.get("total_liab"))
            result["equity"] = _safe_float(latest_balance.get("total_hldr_eqy_exc_min_int"))
        if latest_cf is not None:
            result["operating_cf"] = _safe_float(latest_cf.get("n_cashflow_act"))

        # 计算衍生指标
        if result.get("equity") and result.get("net_income"):
            result["roe"] = round(result["net_income"] / result["equity"] * 100, 2)
        if result.get("revenue") and result.get("operate_profit"):
            result["operating_margin"] = round(result["operate_profit"] / result["revenue"] * 100, 2)
    except Exception:
        pass
    return result


def _extract_efinance_financials(perf) -> dict[str, Any]:
    """从 efinance 性能快报中提取财务指标。"""
    result: dict[str, Any] = {"_tmp_source": "efinance"}
    try:
        if isinstance(perf, dict):
            result["revenue"] = _safe_float(perf.get("营业收入"))
            result["net_income"] = _safe_float(perf.get("归母净利润"))
        elif hasattr(perf, 'iloc'):
            row = perf.iloc[0] if hasattr(perf, 'iloc') else perf
            result["revenue"] = _safe_float(row.get("营业收入"))
            result["net_income"] = _safe_float(row.get("归母净利润"))
    except Exception:
        pass
    return result


def _extract_akshare_financials(fin_df: pd.DataFrame, code: str) -> dict[str, Any]:
    """从 akshare stock_financial_abstract 中提取财务指标。"""
    result: dict[str, Any] = {"_tmp_source": "akshare"}
    try:
        # akhare 返回的 DataFrame，行是指标，列是日期
        # 取最新一列（最近报告期）
        date_cols = [c for c in fin_df.columns if c != "指标"]
        if date_cols:
            latest_col = date_cols[-1]
            indicators = dict(zip(fin_df["指标"], fin_df[latest_col]))
            result["revenue"] = _safe_float(indicators.get("营业总收入"))
            result["net_income"] = _safe_float(indicators.get("归母净利润"))
            result["roe"] = _safe_float(indicators.get("净资产收益率"))
            result["total_assets"] = _safe_float(indicators.get("资产总计"))
            result["total_liab"] = _safe_float(indicators.get("负债合计"))
            result["equity"] = _safe_float(indicators.get("股东权益"))
            result["operating_cf"] = _safe_float(indicators.get("经营活动现金流量净额"))
            result["latest_period"] = latest_col
    except Exception:
        pass
    return result


# ------------------------------------------------------------------
# 3. get_daily_kline
# ------------------------------------------------------------------

def get_daily_kline(code: str, period: str = "daily") -> dict[str, Any]:
    """获取 A 股日K线数据。

    Args:
        code: 股票代码
        period: K线周期 ("daily")

    Returns:
        dict 含 data (DataFrame) + _meta
    """
    code = _format_code(code)
    ts_code = _to_ts_code(code)
    attempted_sources: list[str] = []
    source = ""
    source_group = ""
    start = datetime.now(timezone.utc)
    df = pd.DataFrame()
    rows = 0

    fallback_chain = [
        "efinance.get_k_data",
        "akshare.stock_zh_a_hist",
        "baostock.query_history_k_data_plus",
        "yfinance",
    ]

    # efinance
    try:
        import efinance as ef
        attempted_sources.append("efinance")
        df = ef.stock.get_quote_history(code, ktype=1, max_count=250)
        if not df.empty:
            source = "efinance.stock.get_quote_history"
            source_group = "eastmoney"
            rows = len(df)
    except Exception as e:
        logger.warning("efinance K线获取失败: %s", e)

    # akshare
    if df.empty:
        try:
            import akshare as ak
            attempted_sources.append("akshare")
            df = ak.stock_zh_a_hist(symbol=code, period="daily", start_date="20100101",
                                     end_date=datetime.now().strftime("%Y%m%d"),
                                     adjust="qfq")
            if not df.empty:
                source = "akshare.stock_zh_a_hist"
                source_group = "eastmoney"
                rows = len(df)
        except Exception as e:
            logger.warning("akshare K线获取失败: %s", e)

    # baostock
    if df.empty:
        try:
            import baostock as bs
            attempted_sources.append("baostock")
            bs.login()
            rs = bs.query_history_k_data_plus(
                ts_code,
                "date,open,high,low,close,volume,amount",
                start_date="2010-01-01",
                end_date=datetime.now().strftime("%Y-%m-%d"),
                frequency="d",
                adjustflag="2",  # 前复权
            )
            if rs.error_code == "0":
                data = rs.data
                if data:
                    df = pd.DataFrame(data, columns=["date","open","high","low","close","volume","amount"])
                    source = "baostock.query_history_k_data_plus"
                    source_group = "academic"
                    rows = len(df)
            bs.logout()
        except Exception as e:
            logger.warning("baostock K线获取失败: %s", e)

    # yfinance
    if df.empty:
        try:
            import yfinance as yf
            attempted_sources.append("yfinance")
            yf_code = f"{code}.SS" if code.startswith("6") else f"{code}.SZ"
            ticker = yf.Ticker(yf_code)
            df = ticker.history(period="1y")
            if not df.empty:
                df = df.reset_index()
                source = "yfinance"
                source_group = "global"
                rows = len(df)
        except Exception as e:
            logger.warning("yfinance K线获取失败: %s", e)

    latency = (datetime.now(timezone.utc) - start).total_seconds() * 1000

    result: dict[str, Any] = {
        "data": df,
        "_meta": _build_meta(
            source=source or None,
            source_group=source_group or None,
            fallback_chain=fallback_chain,
            attempted_sources=attempted_sources,
            latency_ms=latency,
            success=bool(source),
            rows_fetched=rows if rows > 0 else None,
        ),
    }

    if df.empty:
        result["error"] = "K线数据不可得"
        result["attempted_sources"] = attempted_sources

    return result


# ------------------------------------------------------------------
# 4. get_shareholders
# ------------------------------------------------------------------

def get_shareholders(code: str) -> dict[str, Any]:
    """获取十大股东信息。

    Returns:
        dict 含 data (list of dict) + _meta
    """
    code = _format_code(code)
    ts_code = _to_ts_code(code)
    attempted_sources: list[str] = []
    result: dict[str, Any] = {}
    source = ""
    source_group = ""
    start = datetime.now(timezone.utc)

    # akshare
    try:
        import akshare as ak
        attempted_sources.append("akshare")
        df = ak.stock_shareholder(symbol=code)
        if not df.empty:
            top10 = df.head(10).to_dict(orient="records")
            result = {"top10_shareholders": top10}
            source = "akshare.stock_shareholder"
            source_group = "eastmoney"
    except Exception as e:
        logger.warning("akshare get_shareholders 失败: %s", e)

    latency = (datetime.now(timezone.utc) - start).total_seconds() * 1000

    if not result:
        result = {"error": "股东数据不可得", "attempted_sources": attempted_sources}

    result["_meta"] = _build_meta(
        source=source or None,
        source_group=source_group or None,
        fallback_chain=["akshare.stock_shareholder"],
        attempted_sources=attempted_sources,
        latency_ms=latency,
        success=bool(source),
    )
    return result


# ------------------------------------------------------------------
# 5. get_governance_signals
# ------------------------------------------------------------------

def get_governance_signals(code: str) -> dict[str, Any]:
    """获取公司治理风险信号：质押比例、限售解禁、商誉占比。

    Returns:
        dict 含 data（pledge_ratio, restricted_release, goodwill_ratio）+ _meta
        单项不可得时对应字段为 null
    """
    code = _format_code(code)
    ts_code = _to_ts_code(code)
    attempted_sources: list[str] = []
    result: dict[str, Any] = {
        "pledge_ratio": None,
        "pledge_detail": None,
        "restricted_release": None,
        "goodwill_ratio": None,
        "goodwill_detail": None,
    }
    sources: list[str] = []
    start = datetime.now(timezone.utc)

    # 质押比例（akshare 东财）
    try:
        import akshare as ak
        attempted_sources.append("akshare.pledge")
        pledge_df = ak.stock_gpzy_pledge_ratio_em()
        if not pledge_df.empty:
            match = pledge_df[pledge_df["股票代码"] == code]
            if not match.empty:
                row = match.iloc[0]
                result["pledge_ratio"] = _safe_float(row.get("质押比例"))
                result["pledge_detail"] = {
                    "total_shares": _safe_float(row.get("质押股数")),
                    "total_amount": _safe_float(row.get("质押市值")),
                }
                sources.append("akshare.stock_gpzy_pledge_ratio_em")
    except Exception as e:
        logger.warning("质押比例获取失败: %s", e)

    # 限售解禁
    try:
        import akshare as ak
        attempted_sources.append("akshare.restricted")
        restrict_df = ak.stock_restricted_release(symbol=code)
        if not restrict_df.empty:
            upcoming = restrict_df[restrict_df["解禁日期"] >= datetime.now().strftime("%Y-%m-%d")]
            result["restricted_release"] = {
                "upcoming_count": len(upcoming),
                "upcoming_shares": _safe_float(upcoming.iloc[0].get("解禁数量")) if not upcoming.empty else 0,
                "next_date": str(upcoming.iloc[0].get("解禁日期")) if not upcoming.empty else None,
            }
            sources.append("akshare.stock_restricted_release")
    except Exception as e:
        logger.warning("限售解禁获取失败: %s", e)

    # 商誉（从资产负债表提取）
    try:
        # 尝试 akshare 资产负债表
        import akshare as ak
        attempted_sources.append("akshare.balance_sheet")
        bs_df = ak.stock_balance_sheet_em(symbol=code)
        if not bs_df.empty:
            goodwill_col = None
            for col in bs_df.columns:
                if "商誉" in str(col):
                    goodwill_col = col
                    break
            if goodwill_col is not None:
                latest_goodwill = _safe_float(bs_df.iloc[0][goodwill_col])
                result["goodwill_detail"] = {"latest_value": latest_goodwill}
                # 取净资产
                equity_col = None
                for col in bs_df.columns:
                    if "归属" in str(col) and "权益" in str(col):
                        equity_col = col
                        break
                if equity_col is not None:
                    equity = _safe_float(bs_df.iloc[0][equity_col])
                    if equity and equity > 0 and latest_goodwill is not None:
                        result["goodwill_ratio"] = round(latest_goodwill / equity * 100, 2)
                sources.append("akshare.stock_balance_sheet_em")
    except Exception as e:
        logger.warning("商誉数据获取失败: %s", e)

    latency = (datetime.now(timezone.utc) - start).total_seconds() * 1000
    success = len(sources) > 0

    result["_meta"] = _build_meta(
        source=sources[0] if sources else None,
        source_group="eastmoney" if sources else None,
        fallback_chain=["akshare.stock_gpzy_pledge_ratio_em", "akshare.stock_restricted_release",
                        "akshare.stock_balance_sheet_em"],
        attempted_sources=attempted_sources,
        latency_ms=latency,
        success=success,
    )
    return result


# ------------------------------------------------------------------
# 6. get_institutional_research
# ------------------------------------------------------------------

def get_institutional_research(code: str) -> dict[str, Any]:
    """获取机构调研记录。

    Returns:
        dict 含 data (list of dict) + _meta
    """
    code = _format_code(code)
    attempted_sources: list[str] = []
    result: dict[str, Any] = {}
    source = ""
    source_group = ""
    start = datetime.now(timezone.utc)

    try:
        import akshare as ak
        attempted_sources.append("akshare")
        df = ak.stock_institute_research(symbol=code)
        if not df.empty:
            recent = df.head(20).to_dict(orient="records")
            result = {
                "recent_researches": recent,
                "total_count": len(df),
            }
            source = "akshare.stock_institute_research"
            source_group = "eastmoney"
    except Exception as e:
        logger.warning("机构调研获取失败: %s", e)

    latency = (datetime.now(timezone.utc) - start).total_seconds() * 1000

    if not result:
        result = {"error": "机构调研数据不可得", "attempted_sources": attempted_sources}

    result["_meta"] = _build_meta(
        source=source or None,
        source_group=source_group or None,
        fallback_chain=["akshare.stock_institute_research"],
        attempted_sources=attempted_sources,
        latency_ms=latency,
        success=bool(source),
    )
    return result


# ------------------------------------------------------------------
# Helper
# ------------------------------------------------------------------

def _safe_float(val: Any) -> float | None:
    """安全转换为 float，失败返回 None。"""
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


# ------------------------------------------------------------------
# 测试入口
# ------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    test_code = sys.argv[1] if len(sys.argv) > 1 else "600519"
    print(f"=== 测试 A 股采集模块: {test_code} ===\n")

    print("--- get_stock_info ---")
    info = get_stock_info(test_code)
    print(info.get("_meta"))
    for k, v in info.items():
        if k != "_meta":
            print(f"  {k}: {v}")

    print("\n--- get_financials ---")
    fin = get_financials(test_code)
    print(fin.get("_meta"))
    for k, v in fin.items():
        if k not in ("_meta", "_tmp_source"):
            print(f"  {k}: {v}")

    print("\n--- get_governance_signals ---")
    gov = get_governance_signals(test_code)
    print(gov.get("_meta"))
    for k, v in gov.items():
        if k != "_meta":
            print(f"  {k}: {v}")
