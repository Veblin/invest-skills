"""ETF 专属数据查询 — 指数 PE、折溢价、规模、跟踪误差、对冲覆盖。

v0.2.1：硬编码对冲映射表 + akshare 直调。
"""

from __future__ import annotations

import logging
from typing import Any

from _invest_path import ensure_invest_a_scripts_on_path

ensure_invest_a_scripts_on_path()

from lib.proxy import akshare_direct_session  # noqa: E402

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 对冲工具覆盖映射表
# ---------------------------------------------------------------------------

ETF_HEDGE_MAP: dict[str, dict[str, str | None]] = {
    "510050": {"index": "上证50", "futures": "上证50股指期货(IH)", "options": "上证50ETF期权", "coverage": "high"},
    "510300": {"index": "沪深300", "futures": "沪深300股指期货(IF)", "options": "沪深300ETF期权", "coverage": "high"},
    "510500": {"index": "中证500", "futures": "中证500股指期货(IC)", "options": "中证500ETF期权", "coverage": "high"},
    "512100": {"index": "中证1000", "futures": "中证1000股指期货(IM)", "options": "中证1000ETF期权(部分)", "coverage": "partial"},
    "159845": {"index": "中证1000", "futures": "中证1000股指期货(IM)", "options": "中证1000ETF期权(部分)", "coverage": "partial"},
    "588000": {"index": "科创50", "futures": "科创50期货(2025上线)", "options": "科创50ETF期权", "coverage": "high"},
    "159915": {"index": "创业板指", "futures": None, "options": "创业板ETF期权", "coverage": "partial"},
    "159949": {"index": "创业板50", "futures": None, "options": "创业板50ETF期权", "coverage": "partial"},
    "563300": {"index": "中证2000", "futures": None, "options": None, "coverage": "none"},
    "510880": {"index": "红利指数", "futures": None, "options": None, "coverage": "none"},
    "511880": {"index": "银华日利", "futures": None, "options": None, "coverage": "none"},
    "513100": {"index": "纳指100", "futures": None, "options": None, "coverage": "low"},
    "513500": {"index": "标普500", "futures": None, "options": None, "coverage": "low"},
    "515790": {"index": "光伏产业", "futures": None, "options": None, "coverage": "none"},
    "516970": {"index": "基建工程", "futures": None, "options": None, "coverage": "none"},
    "518880": {"index": "黄金9999", "futures": "黄金期货(AU)", "options": None, "coverage": "low"},
}

# csindex 符号映射（ETF 代码 → csindex 指数代码）
CSINDEX_MAP: dict[str, str] = {
    "510050": "000016",   # 上证50
    "510300": "000300",   # 沪深300
    "510500": "000905",   # 中证500
    "512100": "000852",   # 中证1000
    "159845": "000852",   # 中证1000 ETF（深市，同 512100）
    "563300": "932000",   # 中证2000
    "588000": "000688",   # 科创50
    "159915": "399006",   # 创业板指
    "159949": "399673",   # 创业板50
}


# ---------------------------------------------------------------------------
# 主查询函数
# ---------------------------------------------------------------------------

def query_etf_data(symbol: str, fund_code: str = "") -> dict[str, Any]:
    """查询 ETF 专属数据。

    Parameters
    ----------
    symbol : str
        ETF 代码（如 "563300"）。
    fund_code : str
        对应指数代码（csindex 格式，如 "932000"）。为空时从 CSINDEX_MAP 查找。

    Returns
    -------
    dict
        {index_pe, premium_discount, aum, tracking_error, hedge_coverage,
         flags, data_quality}
    """
    result: dict[str, Any] = {
        "symbol": symbol,
        "index_pe": None,
        "premium_discount": None,
        "aum": None,
        "tracking_error": None,
        "tracking_error_note": "v0.2.1: 跟踪误差需历史净值序列，暂标注为估算（0.05%）",
        "hedge_coverage": _lookup_hedge(symbol),
        "flags": [],
        "_errors": [],
    }

    # csindex PE
    idx_code = fund_code or CSINDEX_MAP.get(symbol, "")
    if idx_code:
        _fetch_csindex_pe(result, idx_code)

    # ETF 行情（折溢价 / AUM）
    _fetch_etf_spot(result, symbol)

    # 自动标记
    _auto_flags(result)

    return result


# ---------------------------------------------------------------------------
# 子查询
# ---------------------------------------------------------------------------

def _fetch_csindex_pe(result: dict, idx_code: str) -> None:
    """指数 PE（csindex，仅 20 条历史，不足以计算可靠分位）。"""
    try:
        import akshare as ak
        with akshare_direct_session():
            df = ak.stock_zh_index_value_csindex(symbol=idx_code)
        if df is None or df.empty:
            result["_errors"].append("csindex_pe: empty response")
            return
        latest = df.iloc[-1]
        # 市盈率1 = 股本加权，市盈率2 = 流通加权
        pe1 = _to_float(latest.get("市盈率1"))
        pe2 = _to_float(latest.get("市盈率2"))
        result["index_pe"] = pe1 or pe2
        result["index_pe_note"] = (
            f"来源: csindex {idx_code}，仅 {len(df)} 条历史，"
            "无可靠分位；市盈率1=股本加权，市盈率2=流通加权"
        )
    except Exception as exc:
        logger.warning("csindex_pe(%s) failed: %s", idx_code, exc)
        result["_errors"].append(f"csindex_pe: {exc}")


def _fetch_etf_spot(result: dict, symbol: str) -> None:
    """ETF 折溢价 + AUM（fund_etf_spot_em）。"""
    try:
        import akshare as ak
        with akshare_direct_session():
            df = ak.fund_etf_spot_em()
        if df is None or df.empty:
            result["_errors"].append("etf_spot: empty response")
            return
        row = df[df["代码"] == symbol]
        if row.empty:
            result["_errors"].append(f"etf_spot: {symbol} not found")
            return
        r = row.iloc[0]
        result["premium_discount"] = _em_to_premium_discount(r.get("基金折价率"))
        # AUM = 最新份额 × 最新价
        shares = _to_float(r.get("最新份额"))
        price = _to_float(r.get("最新价"))
        if shares is not None and price is not None:
            result["aum"] = round(shares * price / 1e8, 2)  # → 亿元
    except Exception as exc:
        logger.warning("etf_spot(%s) failed: %s", symbol, exc)
        result["_errors"].append(f"etf_spot: {exc}")


# ---------------------------------------------------------------------------
# 自动标记
# ---------------------------------------------------------------------------

def _auto_flags(result: dict) -> None:
    """基于阈值自动生成 flags。"""
    flags: list[str] = []

    # AUM < 2 亿 → 清盘/流动性风险
    aum = result.get("aum")
    if aum is not None and aum < 2:
        flags.append("❌ AUM < 2 亿，存在清盘/流动性风险")

    # 折溢价 > 2% 或 < -2%
    pd_val = result.get("premium_discount")
    if pd_val is not None:
        if pd_val > 2:
            flags.append(f"⚠️ 溢价 {pd_val:.1f}%，买入成本偏高")
        elif pd_val < -2:
            flags.append(f"⚠️ 折价 {abs(pd_val):.1f}%，可能存在流动性或结构问题")

    # 对冲覆盖度
    hc = result.get("hedge_coverage", {})
    cov = hc.get("coverage", "unknown")
    if cov == "none":
        flags.append("⚠️ 该 ETF 无可用的期货/期权对冲工具")
    elif cov == "low":
        flags.append("⚠️ 对冲工具覆盖有限")

    result["flags"] = flags


# ---------------------------------------------------------------------------
# 对冲覆盖查询
# ---------------------------------------------------------------------------

def _lookup_hedge(symbol: str) -> dict:
    """查找 ETF 对冲工具覆盖。未知 ETF 返回 unknown。"""
    entry = ETF_HEDGE_MAP.get(symbol)
    if entry:
        return dict(entry)
    return {"index": "未知", "futures": None, "options": None, "coverage": "unknown",
            "note": "未在已知对冲工具映射表中，请手动核实"}


# ---------------------------------------------------------------------------
# ETF 行情 + K 线（净值序列）— v0.2.1 fix
# ---------------------------------------------------------------------------

def query_etf_quote(symbol: str) -> dict[str, Any]:
    """ETF 当前行情：价格、涨跌幅、折溢价（从 fund_etf_spot_em）。"""
    result: dict[str, Any] = {
        "symbol": symbol,
        "price": None,
        "change_pct": None,
        "volume": None,
        "amount": None,
        "premium_discount": None,
        "status": "missing",
        "_error": None,
    }
    try:
        import akshare as ak
        from lib.proxy import akshare_direct_session
        with akshare_direct_session():
            df = ak.fund_etf_spot_em()
        if df is None or df.empty:
            result["_error"] = "empty response"
            return result
        row = df[df["代码"] == symbol]
        if row.empty:
            result["_error"] = f"{symbol} not found"
            return result
        r = row.iloc[0]
        result["price"] = _to_float(r.get("最新价"))
        result["change_pct"] = _to_float(r.get("涨跌幅"))
        result["volume"] = _to_float(r.get("成交量"))
        result["amount"] = _to_float(r.get("成交额"))
        result["premium_discount"] = _em_to_premium_discount(r.get("基金折价率"))
        result["status"] = "available"
    except Exception as exc:
        logger.warning("etf_quote(%s) failed: %s", symbol, exc)
        result["_error"] = str(exc)
    return result


def query_etf_kline(symbol: str, days: int = 60) -> dict[str, Any]:
    """ETF 净值序列 + 年化波动率计算。

    通过 fund_etf_fund_info_em 获取历史单位净值，计算日收益率
    的年化标准差。同时返回 MA20/MA60 基于净值。

    Args:
        days: Number of **trading bars** needed (not calendar days).
            Calendar lookback uses ``int(days * 365 / 250) + 15`` so MA60
            has enough history after weekends/holidays.
    """
    import math
    from datetime import date, timedelta

    result: dict[str, Any] = {
        "symbol": symbol,
        "nav_rows": 0,
        "latest_nav": None,
        "volatility_annualized": None,
        "rsi_24": None,
        "ma20": None,
        "ma60": None,
        "nav_history": [],
        "status": "missing",
        "_error": None,
    }

    try:
        import akshare as ak
        from lib.proxy import akshare_direct_session

        end_date = date.today().strftime("%Y%m%d")
        # days = trading bars; convert to calendar span (≈250 trading / 365)
        calendar_days = int(days * 365 / 250) + 15
        start_date = (date.today() - timedelta(days=calendar_days)).strftime("%Y%m%d")

        with akshare_direct_session():
            df = ak.fund_etf_fund_info_em(fund=symbol, start_date=start_date, end_date=end_date)

        if df is None or df.empty:
            result["_error"] = "empty response"
            return result

        result["nav_rows"] = len(df)

        # 最新净值
        latest = df.iloc[-1]
        result["latest_nav"] = _to_float(latest.get("单位净值"))

        # 日收益率序列
        returns: list[float] = []
        navs: list[float] = []
        for _, row_data in df.iterrows():
            chg = _to_float(row_data.get("日增长率"))
            nav = _to_float(row_data.get("单位净值"))
            if chg is not None:
                returns.append(chg / 100.0)  # % → 小数
            if nav is not None:
                navs.append(nav)

        if len(returns) < 5:
            result["status"] = "insufficient"
            result["_error"] = f"only {len(returns)} daily returns"
            return result

        # 年化波动率 = std(日收益) * sqrt(252)
        mean_ret = sum(returns) / len(returns)
        variance = sum((r - mean_ret) ** 2 for r in returns) / (len(returns) - 1)
        daily_vol = math.sqrt(variance)
        result["volatility_annualized"] = round(daily_vol * math.sqrt(252) * 100, 2)

        # MA20 / MA60（基于单位净值）
        if len(navs) >= 20:
            result["ma20"] = round(sum(navs[-20:]) / 20, 4)
        if len(navs) >= 60:
            result["ma60"] = round(sum(navs[-60:]) / 60, 4)

        # 简易 RSI(24) 基于净值日收益
        if len(returns) >= 24:
            gains = [r for r in returns[-24:] if r > 0]
            losses = [abs(r) for r in returns[-24:] if r < 0]
            avg_gain = sum(gains) / 24 if gains else 0
            avg_loss = sum(losses) / 24 if losses else 0
            if avg_loss > 0:
                rs = avg_gain / avg_loss
                result["rsi_24"] = round(100 - (100 / (1 + rs)), 2)
            else:
                result["rsi_24"] = 100.0
        elif len(returns) >= 14:
            gains = [r for r in returns[-14:] if r > 0]
            losses = [abs(r) for r in returns[-14:] if r < 0]
            avg_gain = sum(gains) / 14 if gains else 0
            avg_loss = sum(losses) / 14 if losses else 0
            if avg_loss > 0:
                rs = avg_gain / avg_loss
                result["rsi_24"] = round(100 - (100 / (1 + rs)), 2)

        result["nav_history"] = [
            {"date": str(r.get("净值日期", "")), "nav": _to_float(r.get("单位净值")),
             "change_pct": _to_float(r.get("日增长率"))}
            for _, r in df.iterrows()
        ]
        result["status"] = "available"

    except Exception as exc:
        logger.warning("etf_kline(%s) failed: %s", symbol, exc)
        result["_error"] = str(exc)

    return result


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _to_float(val: Any) -> float | None:
    """安全转 float。"""
    if val is None:
        return None
    try:
        v = float(val)
        import math
        if math.isnan(v) or math.isinf(v):
            return None
        return v
    except (ValueError, TypeError):
        return None


def _em_to_premium_discount(em_raw: object) -> float | None:
    """EM 基金折价率（+ = 折价）→ premium_discount（+ = 溢价）。"""
    em = _to_float(em_raw)
    return None if em is None else -em
