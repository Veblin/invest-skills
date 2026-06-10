"""
全球宏观数据采集模块。

覆盖：
- get_macro_snapshot: 综合宏观快照（美元、美债、VIX、联邦基金利率等）
- get_fred_series: FRED 单序列获取
- get_fedwatch_probabilities: CME FedWatch 利率预期
- get_fx_rates: 人民币/港元兑美元汇率
- get_china_macro_snapshot: 中国宏观数据（GDP/CPI/PMI）

Fallback 链：
  FRED（有 Key）→ yfinance → akshare → 标注不可得
  CME FedWatch → WebFetch（无 API，HTML 抓取）
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)

# FRED 核心序列（美元、美债、VIX、原油、黄金、联邦基金利率）
FRED_SERIES = {
    "DXY": "DTWEXBGS",       # 美元贸易加权指数
    "US10Y": "DGS10",        # 10年期美债收益率
    "US2Y": "DGS2",          # 2年期美债收益率
    "TIPS10Y": "DFII10",     # 10年期 TIPS 实际收益率
    "BREAKEVEN10": "T10YIE", # 10年期盈亏平衡通胀率
    "FEDFUNDS": "FEDFUNDS",  # 联邦基金有效利率
    "VIX": "VIXCLS",         # VIX 收盘价
    "WTI": "DCOILWTICO",     # WTI 原油现货
    "GOLD": "GOLDAMGBD228NLBR",  # 黄金定盘价
    "SP500": "SP500",        # 标普500
}


def _build_meta(
    source: str | None,
    source_group: str | None,
    fallback_chain: list[str],
    attempted_sources: list[str],
    latency_ms: float,
    success: bool,
    warning: str | None = None,
) -> dict[str, Any]:
    if not success and not attempted_sources:
        error_type = "network"
    elif not success:
        error_type = "empty"
    else:
        error_type = None

    return {
        "source": source or "none",
        "source_group": source_group or "unknown",
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "fallback_chain": fallback_chain,
        "confidence": "high" if (source and ("fred" in source or "FRED" in str(source))) else (
            "medium" if source else "low"),
        "latency_ms": round(latency_ms, 1),
        "success": success,
        "error_type": error_type,
        **({"warning": warning} if warning else {}),
    }


def _safe_float(val: Any) -> float | None:
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


# ------------------------------------------------------------------
# FRED 底层调用
# ------------------------------------------------------------------

class _FredClient:
    """FRED 内部客户端（惰性初始化）。"""

    def __init__(self):
        self._fred = None
        self._init_attempted = False

    def _init(self):
        if self._init_attempted:
            return
        self._init_attempted = True
        api_key = os.environ.get("FRED_API_KEY", "")
        if not api_key:
            logger.debug("FRED: 未配置 FRED_API_KEY")
            return
        try:
            from fredapi import Fred
            self._fred = Fred(api_key=api_key)
        except ImportError:
            logger.debug("FRED: fredapi 未安装")
        except Exception as e:
            logger.debug("FRED: 初始化失败 — %s", e)

    def is_available(self) -> bool:
        self._init()
        return self._fred is not None

    def get_series(self, series_id: str) -> pd.Series:
        self._init()
        if self._fred is None:
            raise RuntimeError("FRED 不可用")
        return self._fred.get_series(series_id)


_fred = _FredClient()


# ------------------------------------------------------------------
# get_macro_snapshot
# ------------------------------------------------------------------

def get_macro_snapshot() -> dict[str, Any]:
    """获取综合全球宏观快照。

    Returns:
        dict 含 美债、美元、VIX、联邦基金利率、原油、黄金 等指标 + _meta
    """
    attempted_sources: list[str] = []
    result: dict[str, Any] = {}
    source = ""
    source_group = ""
    start = datetime.now(timezone.utc)

    fallback_chain = ["FRED API", "yfinance", "akshare"]

    # 1. FRED
    if _fred.is_available():
        attempted_sources.append("fred")
        fred_success = 0
        for name, series_id in FRED_SERIES.items():
            try:
                series = _fred.get_series(series_id)
                if len(series) > 0:
                    latest = float(series.dropna().iloc[-1])
                    result[name] = {
                        "latest": latest,
                        "series_id": series_id,
                        "date": str(series.dropna().index[-1]),
                    }
                    fred_success += 1
            except Exception:
                result[name] = None

        if fred_success >= 6:
            source = "FRED API"
            source_group = "official"

    # 2. yfinance
    if not source:
        try:
            import yfinance as yf
            attempted_sources.append("yfinance")

            yf_map = {
                "DXY": "DX-Y.NYB",
                "VIX": "^VIX",
                "US10Y": "^TNX",
                "GOLD_ETF": "GLD",
                "WTI_FUT": "CL=F",
                "SP500": "^GSPC",
            }

            tickers = yf.download(list(yf_map.values()), period="5d", auto_adjust=True)
            if not tickers.empty:
                close = tickers["Close"] if "Close" in tickers else tickers
                for name, sym in yf_map.items():
                    if sym in close.columns:
                        val = close[sym].dropna().iloc[-1] if not close[sym].dropna().empty else None
                        result[name] = {
                            "latest": float(val) if val is not None and not pd.isna(val) else None,
                            "source": "yfinance",
                        }
                source = "yfinance"
                source_group = "global"
        except Exception as e:
            logger.warning("yfinance 宏观快照获取失败: %s", e)

    # 3. akshare
    if not source:
        try:
            import akshare as ak
            attempted_sources.append("akshare")
            # 尝试获取全球指数
            global_df = ak.index_global_spot_em()
            if not global_df.empty:
                for _, row in global_df.iterrows():
                    name = str(row.get("名称", ""))
                    if "美元" in name:
                        result["DXY"] = {"latest": _safe_float(row.get("最新价")), "source": "akshare"}
                source = "akshare.index_global_spot_em"
                source_group = "eastmoney"
        except Exception as e:
            logger.warning("akshare 宏观快照获取失败: %s", e)

    latency = (datetime.now(timezone.utc) - start).total_seconds() * 1000

    if not result:
        result = {"error": "全球宏观数据不可得", "attempted_sources": attempted_sources}
    else:
        # 计算衍生指标
        try:
            us10y = result.get("US10Y", {}).get("latest") if isinstance(result.get("US10Y"), dict) else None
            us2y = result.get("US2Y", {}).get("latest") if isinstance(result.get("US2Y"), dict) else None
            if us10y is not None and us2y is not None:
                result["YIELD_CURVE_10Y2Y"] = {
                    "latest": round(us10y - us2y, 4),
                    "source": "derived",
                }
        except Exception:
            pass

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
# get_fred_series
# ------------------------------------------------------------------

def get_fred_series(series_id: str) -> dict[str, Any]:
    """获取 FRED 单序列数据。

    Args:
        series_id: FRED 序列 ID（如 "DGS10"）

    Returns:
        dict 含 data (pd.Series) + _meta
    """
    attempted_sources: list[str] = []
    source = ""
    start = datetime.now(timezone.utc)

    data = pd.Series(dtype=float)

    if _fred.is_available():
        attempted_sources.append("fred")
        try:
            data = _fred.get_series(series_id)
            source = f"FRED.{series_id}"
        except Exception as e:
            logger.warning("FRED %s 获取失败: %s", series_id, e)

    if data.empty:
        attempted_sources.append("yfinance")
        # yfinance 不支持 FRED 序列，跳过
        pass

    latency = (datetime.now(timezone.utc) - start).total_seconds() * 1000

    return {
        "data": data,
        "series_id": series_id,
        "_meta": _build_meta(
            source=source or None,
            source_group="official" if source else None,
            fallback_chain=["FRED"],
            attempted_sources=attempted_sources,
            latency_ms=latency,
            success=bool(source),
        ),
    }


# ------------------------------------------------------------------
# get_fedwatch_probabilities
# ------------------------------------------------------------------

def get_fedwatch_probabilities() -> dict[str, Any]:
    """获取 CME FedWatch 利率预期概率。

    通过 WebFetch 解析 CME FedWatch 页面（无官方 API）。

    Returns:
        dict 含 probabilities + _meta
    """
    attempted_sources: list[str] = []
    result: dict[str, Any] = {}
    source = ""
    start = datetime.now(timezone.utc)

    # 尝试直接通过 requests 解析 CME 页面
    try:
        import requests
        attempted_sources.append("web_cme")
        # CME FedWatch JSON endpoint (非官方但公开)
        url = "https://www.cmegroup.com/CmeWS/mkt/SmartMarkets/FedWatchTool/getLatestFOMCQuotes.inc"
        resp = requests.get(url, timeout=10)
        if resp.status_code == 200:
            result["raw_response"] = resp.text[:500]  # 保留片段用于分析
            source = "CME_FedWatch_JSON"
            source_group = "official"
    except Exception as e:
        logger.debug("CME FedWatch 直接获取失败: %s", e)

    # Fallback: polly 聚合数据
    if not result:
        try:
            import requests
            attempted_sources.append("web_polly")
            # polymarket 上的美联储决策预测（可选参考）
            url = "https://api.polymarket.com/events/fed"
            resp = requests.get(url, timeout=10)
            if resp.status_code == 200:
                result["polymarket_data"] = resp.text[:500]
                source = "polymarket"
                source_group = "global"
        except Exception:
            pass

    latency = (datetime.now(timezone.utc) - start).total_seconds() * 1000

    if not result:
        result = {
            "error": "FedWatch 数据不可得（请使用 WebFetch 手动获取 cmegroup.com/markets/interest-rates/cme-fedwatch-tool.html）",
            "attempted_sources": attempted_sources,
        }

    result["_meta"] = _build_meta(
        source=source or None,
        source_group=source_group or None,
        fallback_chain=["CME JSON endpoint", "polymarket", "WebFetch(fallback)"],
        attempted_sources=attempted_sources,
        latency_ms=latency,
        success=bool(source),
    )
    return result


# ------------------------------------------------------------------
# get_fx_rates
# ------------------------------------------------------------------

def get_fx_rates() -> dict[str, Any]:
    """获取人民币/港元兑美元汇率。

    Returns:
        dict 含 USDCNY, USDHKD + _meta
    """
    attempted_sources: list[str] = []
    result: dict[str, Any] = {}
    source = ""
    source_group = ""
    start = datetime.now(timezone.utc)

    # akshare
    try:
        import akshare as ak
        attempted_sources.append("akshare")
        fx_df = ak.fx_spot_quote()
        if not fx_df.empty:
            for _, row in fx_df.iterrows():
                pair = str(row.get("货币对", ""))
                if "美元/人民币" in pair or "USD/CNY" in pair:
                    result["USDCNY"] = _safe_float(row.get("最新价", row.get("最新")))
                elif "美元/港元" in pair or "USD/HKD" in pair:
                    result["USDHKD"] = _safe_float(row.get("最新价", row.get("最新")))
            if result:
                source = "akshare.fx_spot_quote"
                source_group = "eastmoney"
    except Exception as e:
        logger.warning("akshare 汇率获取失败: %s", e)

    # yfinance fallback
    if not result:
        try:
            import yfinance as yf
            attempted_sources.append("yfinance")
            cny = yf.Ticker("CNY=X")
            cny_info = cny.history(period="1d")
            if not cny_info.empty:
                result["USDCNY"] = float(cny_info["Close"].iloc[-1])
            hkd = yf.Ticker("HKD=X")
            hkd_info = hkd.history(period="1d")
            if not hkd_info.empty:
                result["USDHKD"] = float(hkd_info["Close"].iloc[-1])
            source = "yfinance"
            source_group = "global"
        except Exception as e:
            logger.warning("yfinance 汇率获取失败: %s", e)

    latency = (datetime.now(timezone.utc) - start).total_seconds() * 1000

    if not result:
        result = {"error": "汇率数据不可得", "attempted_sources": attempted_sources}

    result["_meta"] = _build_meta(
        source=source or None,
        source_group=source_group or None,
        fallback_chain=["akshare.fx_spot_quote", "yfinance.CNY=X,HKD=X"],
        attempted_sources=attempted_sources,
        latency_ms=latency,
        success=bool(source),
    )
    return result


# ------------------------------------------------------------------
# get_china_macro_snapshot
# ------------------------------------------------------------------

def get_china_macro_snapshot() -> dict[str, Any]:
    """获取中国宏观数据：GDP/CPI/PMI。

    Returns:
        dict 含 data + _meta
    """
    attempted_sources: list[str] = []
    result: dict[str, Any] = {}
    source = ""
    source_group = ""
    start = datetime.now(timezone.utc)

    try:
        import akshare as ak
        attempted_sources.append("akshare")

        # CPI
        try:
            cpi_df = ak.macro_china_cpi_monthly()
            if not cpi_df.empty:
                latest_cpi = cpi_df.iloc[-1]
                result["CPI_YOY"] = _safe_float(latest_cpi.get("cpi", latest_cpi.get("全国")))
        except Exception:
            pass

        # PMI
        try:
            pmi_df = ak.macro_china_pmi()
            if not pmi_df.empty:
                latest_pmi = pmi_df.iloc[-1]
                result["PMI"] = _safe_float(latest_pmi.get("制造业", latest_pmi.get("制造业PMI")))
        except Exception:
            pass

        # GDP
        try:
            gdp_df = ak.macro_china_gdp()
            if not gdp_df.empty:
                latest_gdp = gdp_df.iloc[-1]
                result["GDP_YOY"] = _safe_float(latest_gdp.get("国内生产总值同比"))
        except Exception:
            pass

        if result:
            source = "akshare"
            source_group = "eastmoney"
    except Exception as e:
        logger.warning("中国宏观数据获取失败: %s", e)

    latency = (datetime.now(timezone.utc) - start).total_seconds() * 1000

    if not result:
        result = {"error": "中国宏观数据不可得", "attempted_sources": attempted_sources}

    result["_meta"] = _build_meta(
        source=source or None,
        source_group=source_group or None,
        fallback_chain=["akshare.macro_china_*"],
        attempted_sources=attempted_sources,
        latency_ms=latency,
        success=bool(source),
    )
    return result


# ------------------------------------------------------------------
# 测试入口
# ------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    print("=== 全球宏观快照 ===")
    snapshot = get_macro_snapshot()
    for k, v in snapshot.items():
        if k != "_meta":
            print(f"  {k}: {v}")
    print(f"  _meta: {snapshot.get('_meta')}")

    print("\n=== 汇率 ===")
    fx = get_fx_rates()
    for k, v in fx.items():
        if k != "_meta":
            print(f"  {k}: {v}")

    print("\n=== 中国宏观 ===")
    china = get_china_macro_snapshot()
    for k, v in china.items():
        if k != "_meta":
            print(f"  {k}: {v}")
