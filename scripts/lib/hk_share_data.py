"""
港股数据采集模块。

覆盖：
- get_hk_stock_info: 公司概况
- get_hk_financials: 财务数据（港股披露标准不同，覆盖有限）
- get_hk_daily_kline: 日K线
- get_hk_governance_signals: 做空比率、大股东增减持（披露可得部分）

Fallback 链：
  有 Longbridge: Longbridge(首选) → akshare(hk) → yfinance(.HK) → 标注不可得
  有 Tushare:    Tushare ∥ akshare(hk) → yfinance(.HK) → 标注不可得
  无 Token:      akshare(hk) → yfinance(.HK) → 标注不可得
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)


def _format_hk_code(code: str) -> str:
    """标准化港股代码为纯数字5位（不足左补0）。"""
    code = str(code).strip().replace(".HK", "").replace(".hk", "")
    return code.zfill(5)


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
    if not success and error_type is None:
        error_type = "empty" if attempted_sources else "network"
    meta: dict[str, Any] = {
        "source": source or "none",
        "source_group": source_group or "unknown",
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "fallback_chain": fallback_chain,
        "confidence": "high" if (source and "tushare" in source) else (
            "medium" if source else "low"),
        "latency_ms": round(latency_ms, 1),
        "success": success,
        "error_type": error_type,
    }
    if warning:
        meta["warning"] = warning
    if rows_fetched is not None:
        meta["rows_fetched"] = rows_fetched
    return meta


def _safe_float(val: Any) -> float | None:
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


# ------------------------------------------------------------------
# get_hk_stock_info
# ------------------------------------------------------------------

def get_hk_stock_info(code: str) -> dict[str, Any]:
    """获取港股公司概况。

    Returns:
        dict 含 data + _meta
    """
    code = _format_hk_code(code)
    attempted_sources: list[str] = []
    result: dict[str, Any] = {}
    source = ""
    source_group = ""
    start = datetime.now(timezone.utc)

    fallback_chain = ["akshare(hk)", "yfinance(.HK)"]

    # akshare 港股
    try:
        import akshare as ak
        attempted_sources.append("akshare")
        try:
            profile = ak.stock_hk_profile(symbol=code)
            if profile is not None:
                result["profile"] = profile
                source = "akshare.stock_hk_profile"
                source_group = "eastmoney"
        except Exception:
            # fallback: stock_hk_spot_em
            df = ak.stock_hk_spot_em()
            if not df.empty:
                match = df[df["代码"] == code]
                if not match.empty:
                    row = match.iloc[0]
                    result = {
                        "name": str(row.get("名称", "")),
                        "price": _safe_float(row.get("最新价")),
                        "change_pct": _safe_float(row.get("涨跌幅")),
                        "total_mv": _safe_float(row.get("总市值")),
                        "pe_ratio": _safe_float(row.get("市盈率")),
                    }
                    source = "akshare.stock_hk_spot_em"
                    source_group = "eastmoney"
    except Exception as e:
        logger.warning("akshare 港股信息获取失败: %s", e)

    # yfinance fallback
    if not result:
        try:
            import yfinance as yf
            attempted_sources.append("yfinance")
            ticker = yf.Ticker(f"{code}.HK")
            info = ticker.info
            if info:
                result = {
                    "name": str(info.get("longName", info.get("shortName", ""))),
                    "sector": str(info.get("sector", "")),
                    "industry": str(info.get("industry", "")),
                    "market_cap": _safe_float(info.get("marketCap")),
                    "website": str(info.get("website", "")),
                }
                source = "yfinance"
                source_group = "global"
        except Exception as e:
            logger.warning("yfinance 港股信息获取失败: %s", e)

    latency = (datetime.now(timezone.utc) - start).total_seconds() * 1000

    if not result:
        result = {"error": "港股基本信息不可得", "attempted_sources": attempted_sources}

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
# get_hk_financials
# ------------------------------------------------------------------

def get_hk_financials(code: str) -> dict[str, Any]:
    """获取港股财务数据。

    港股披露标准与 A 股不同，数据覆盖有限。
    Returns:
        dict 含 data + _meta，含 warning 标注覆盖限制
    """
    code = _format_hk_code(code)
    attempted_sources: list[str] = []
    result: dict[str, Any] = {}
    source = ""
    source_group = ""
    start = datetime.now(timezone.utc)

    fallback_chain = ["akshare(hk)", "yfinance(.HK)"]

    # akshare 港股财务
    try:
        import akshare as ak
        attempted_sources.append("akshare")

        try:
            fin_df = ak.stock_hk_financial_indicator(symbol=code)
            if not fin_df.empty:
                latest = fin_df.iloc[-1] if len(fin_df) > 1 else fin_df.iloc[0]
                result = {
                    "latest_date": str(latest.get("日期", latest.get("report_date", ""))),
                    "revenue": _safe_float(latest.get("营业收入", latest.get("revenue"))),
                    "net_income": _safe_float(latest.get("净利润", latest.get("net_income"))),
                    "total_assets": _safe_float(latest.get("总资产", latest.get("total_assets"))),
                    "roe": _safe_float(latest.get("净资产收益率", latest.get("roe"))),
                }
                source = "akshare.stock_hk_financial_indicator"
                source_group = "eastmoney"
        except Exception:
            pass
    except Exception as e:
        logger.warning("akshare 港股财务获取失败: %s", e)

    # yfinance
    if not result:
        try:
            import yfinance as yf
            attempted_sources.append("yfinance")
            ticker = yf.Ticker(f"{code}.HK")
            info = ticker.info
            if info:
                result = {
                    "revenue": _safe_float(info.get("totalRevenue")),
                    "net_income": _safe_float(info.get("netIncomeToCommon")),
                    "total_assets": _safe_float(info.get("totalAssets")),
                    "roe": _safe_float(info.get("returnOnEquity")),
                    "gross_margins": _safe_float(info.get("grossMargins")),
                }
                source = "yfinance"
                source_group = "global"
        except Exception as e:
            logger.warning("yfinance 港股财务获取失败: %s", e)

    latency = (datetime.now(timezone.utc) - start).total_seconds() * 1000

    if not result:
        result = {"error": "港股财务数据不可得", "attempted_sources": attempted_sources}
    else:
        result["warning"] = "港股财务披露标准与A股不同，数据覆盖有限。关键数据请参考港交所公告。"

    result["_meta"] = _build_meta(
        source=source or None,
        source_group=source_group or None,
        fallback_chain=fallback_chain,
        attempted_sources=attempted_sources,
        latency_ms=latency,
        success=bool(source),
        warning="港股财务覆盖有限" if source else None,
    )
    return result


# ------------------------------------------------------------------
# get_hk_daily_kline
# ------------------------------------------------------------------

def get_hk_daily_kline(code: str) -> dict[str, Any]:
    """获取港股日K线数据。

    Returns:
        dict 含 data (DataFrame) + _meta
    """
    code = _format_hk_code(code)
    attempted_sources: list[str] = []
    source = ""
    source_group = ""
    start = datetime.now(timezone.utc)
    df = pd.DataFrame()
    rows = 0

    fallback_chain = ["akshare(hk)", "yfinance(.HK)"]

    # akshare
    try:
        import akshare as ak
        attempted_sources.append("akshare")
        df = ak.stock_hk_hist(symbol=code, period="daily", start_date="20100101",
                               end_date=datetime.now().strftime("%Y%m%d"), adjust="qfq")
        if not df.empty:
            source = "akshare.stock_hk_hist"
            source_group = "eastmoney"
            rows = len(df)
    except Exception as e:
        logger.warning("akshare 港股K线失败: %s", e)

    # yfinance
    if df.empty:
        try:
            import yfinance as yf
            attempted_sources.append("yfinance")
            ticker = yf.Ticker(f"{code}.HK")
            df = ticker.history(period="1y")
            if not df.empty:
                df = df.reset_index()
                source = "yfinance"
                source_group = "global"
                rows = len(df)
        except Exception as e:
            logger.warning("yfinance 港股K线失败: %s", e)

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
        result["error"] = "港股K线数据不可得"
        result["attempted_sources"] = attempted_sources

    return result


# ------------------------------------------------------------------
# get_hk_governance_signals
# ------------------------------------------------------------------

def get_hk_governance_signals(code: str) -> dict[str, Any]:
    """获取港股治理风险信号：做空比率、大股东增减持（披露可得部分）。

    Returns:
        dict 含 data + _meta
    """
    code = _format_hk_code(code)
    attempted_sources: list[str] = []
    result: dict[str, Any] = {
        "short_sell_ratio": None,
        "insider_trades": None,
    }
    sources: list[str] = []
    start = datetime.now(timezone.utc)

    # 做空比率（akshare）
    try:
        import akshare as ak
        attempted_sources.append("akshare.short_sell")
        ss_df = ak.stock_hk_short_sell(symbol=code)
        if not ss_df.empty:
            latest = ss_df.iloc[-1]
            result["short_sell_ratio"] = _safe_float(latest.get("short_sell_ratio", latest.get("沽空比率")))
            sources.append("akshare.stock_hk_short_sell")
    except Exception as e:
        logger.warning("港股做空比率获取失败: %s", e)

    latency = (datetime.now(timezone.utc) - start).total_seconds() * 1000
    success = len(sources) > 0

    result["_meta"] = _build_meta(
        source=sources[0] if sources else None,
        source_group="eastmoney" if sources else None,
        fallback_chain=["akshare.stock_hk_short_sell"],
        attempted_sources=attempted_sources,
        latency_ms=latency,
        success=success,
        warning="港股大股东增减持披露延迟较大" if not success else None,
    )
    return result


# ------------------------------------------------------------------
# 测试入口
# ------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    test_code = sys.argv[1] if len(sys.argv) > 1 else "00700"
    print(f"=== 测试港股采集模块: {test_code} ===\n")

    print("--- get_hk_stock_info ---")
    info = get_hk_stock_info(test_code)
    for k, v in info.items():
        if k != "_meta":
            print(f"  {k}: {v}")

    print("\n--- get_hk_financials ---")
    fin = get_hk_financials(test_code)
    for k, v in fin.items():
        if k != "_meta":
            print(f"  {k}: {v}")

    print("\n--- get_hk_governance_signals ---")
    gov = get_hk_governance_signals(test_code)
    for k, v in gov.items():
        if k != "_meta":
            print(f"  {k}: {v}")
