"""ETF 专属数据查询 — 指数 PE、折溢价、规模、跟踪误差、对冲覆盖。

Canonical owner: invest-a-etf（自 journal v0.2.1 迁出）。
invest-a-journal 经 scripts/lib/etf_data.py shim 复用本模块。
硬编码对冲映射表 + akshare 直调。
"""

from __future__ import annotations

import logging
import math
import threading
import time
from typing import Any

from _invest_path import ensure_invest_a_scripts_on_path

ensure_invest_a_scripts_on_path()

from lib.nums import safe_float  # noqa: E402
from lib.proxy import akshare_direct_session  # noqa: E402
from lib.technical import rsi_series  # noqa: E402

logger = logging.getLogger(__name__)

# fund_etf_spot_em 全表缓存（短 TTL，去重同请求内多次查询）
_SPOT_CACHE_LOCK = threading.Lock()
_SPOT_CACHE_DF: Any = None
_SPOT_CACHE_TS: float = 0.0
_SPOT_CACHE_TTL_SEC = 30.0


# ---------------------------------------------------------------------------
# 对冲工具覆盖映射表
# ---------------------------------------------------------------------------

# 运行时 canonical 源；人类可读副本见 references/etf-hedge-map.md（改映射请先改此处再同步文档）
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

def query_etf_data(
    symbol: str,
    fund_code: str = "",
    *,
    spot_row: Any = None,
) -> dict[str, Any]:
    """查询 ETF 专属数据。

    Parameters
    ----------
    symbol : str
        ETF 代码（如 "563300"）。
    fund_code : str
        对应指数代码（csindex 格式，如 "932000"）。为空时从 CSINDEX_MAP 查找。
    spot_row : optional
        已解析的 fund_etf_spot_em 行（Series），避免重复拉全表。

    Returns
    -------
    dict
        {index_pe, index_pe_status, premium_discount, aum, tracking_error,
         hedge_coverage, flags, data_quality}
    """
    result: dict[str, Any] = {
        "symbol": symbol,
        "index_pe": None,
        "index_pe_status": "unknown_etf",
        "premium_discount": None,
        "aum": None,
        "tracking_error": None,
        "tracking_error_note": (
            "跟踪误差需 ETF 净值与指数点位序列对比，当前引擎未实现；"
            "请勿填写估算数字"
        ),
        "hedge_coverage": _lookup_hedge(symbol),
        "flags": [],
        "_errors": [],
        "data_quality": {},
    }

    idx_code = fund_code or CSINDEX_MAP.get(symbol, "")
    _set_index_pe_status(result, symbol, idx_code)
    if idx_code:
        _fetch_csindex_pe(result, idx_code)

    if spot_row is not None:
        _apply_spot_row_to_profile(result, spot_row, symbol)
    else:
        row, err = _lookup_etf_spot_row(symbol)
        if err:
            result["_errors"].append(err)
        elif row is not None:
            _apply_spot_row_to_profile(result, row, symbol)

    _auto_flags(result)
    result["data_quality"] = _summarize_etf_data_quality(result)
    return result


# ---------------------------------------------------------------------------
# ETF spot 缓存与查询
# ---------------------------------------------------------------------------

def prefetch_etf_spot() -> bool:
    """预拉 fund_etf_spot_em 全表并写入短 TTL 缓存。返回是否成功。"""
    return _get_etf_spot_df(force=True) is not None


def clear_etf_spot_cache() -> None:
    """清空 spot 缓存（测试用）。"""
    global _SPOT_CACHE_DF, _SPOT_CACHE_TS
    with _SPOT_CACHE_LOCK:
        _SPOT_CACHE_DF = None
        _SPOT_CACHE_TS = 0.0


def _get_etf_spot_df(*, force: bool = False) -> Any:
    """带锁 + TTL 的 fund_etf_spot_em 全表缓存。"""
    global _SPOT_CACHE_DF, _SPOT_CACHE_TS
    now = time.monotonic()
    with _SPOT_CACHE_LOCK:
        if (
            not force
            and _SPOT_CACHE_DF is not None
            and (now - _SPOT_CACHE_TS) < _SPOT_CACHE_TTL_SEC
        ):
            return _SPOT_CACHE_DF
        try:
            import akshare as ak

            with akshare_direct_session():
                df = ak.fund_etf_spot_em()
        except Exception as exc:
            logger.warning("fund_etf_spot_em failed: %s", exc)
            return None
        if df is None or df.empty:
            return None
        _SPOT_CACHE_DF = df
        _SPOT_CACHE_TS = now
        return df


def _lookup_etf_spot_row(symbol: str) -> tuple[Any | None, str | None]:
    """从缓存/网络查找单只 ETF spot 行。返回 (row, error)。"""
    df = _get_etf_spot_df()
    if df is None:
        return None, "etf_spot: empty response"
    row_df = df[df["代码"] == symbol]
    if row_df.empty:
        return None, f"etf_spot: {symbol} not found"
    return row_df.iloc[0], None


def _apply_spot_row_to_profile(result: dict, row: Any, symbol: str) -> None:
    """将 spot 行写入 profile（折溢价 / AUM）。"""
    result["premium_discount"] = _em_to_premium_discount(row.get("基金折价率"))
    shares = safe_float(row.get("最新份额"))
    price = safe_float(row.get("最新价"))
    if shares is not None and price is not None:
        result["aum"] = round(shares * price / 1e8, 2)


def _spot_row_to_quote(symbol: str, row: Any) -> dict[str, Any]:
    """将 spot 行转为 quote 结构。"""
    return {
        "symbol": symbol,
        "price": safe_float(row.get("最新价")),
        "change_pct": safe_float(row.get("涨跌幅")),
        "volume": safe_float(row.get("成交量")),
        "amount": safe_float(row.get("成交额")),
        "premium_discount": _em_to_premium_discount(row.get("基金折价率")),
        "status": "available",
        "_error": None,
    }


# ---------------------------------------------------------------------------
# 子查询
# ---------------------------------------------------------------------------

def _set_index_pe_status(result: dict, symbol: str, idx_code: str) -> None:
    if idx_code:
        result["index_pe_status"] = "mapped"
        return
    if symbol in ETF_HEDGE_MAP:
        result["index_pe_status"] = "not_mapped"
        result["index_pe_note"] = (
            "该 ETF 在对冲映射表中，但尚无 csindex 指数代码映射，"
            "无法自动获取指数 PE（常见于行业/主题 ETF）"
        )
        return
    result["index_pe_status"] = "unknown_etf"
    result["index_pe_note"] = "不在已知映射表中，请手动核实跟踪指数"


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
        pe1 = safe_float(latest.get("市盈率1"))
        pe2 = safe_float(latest.get("市盈率2"))
        result["index_pe"] = pe1 if pe1 is not None else pe2
        result["index_pe_note"] = (
            f"来源: csindex {idx_code}，仅 {len(df)} 条历史，"
            "无可靠分位；市盈率1=股本加权，市盈率2=流通加权"
        )
    except Exception as exc:
        logger.warning("csindex_pe(%s) failed: %s", idx_code, exc)
        result["_errors"].append(f"csindex_pe: {exc}")


def _summarize_etf_data_quality(result: dict) -> dict[str, str]:
    """8 态简版 data_quality（与 journal 语义对齐）。"""
    dq: dict[str, str] = {}

    status = result.get("index_pe_status")
    if result.get("index_pe") is not None:
        dq["index_pe"] = "available"
    elif status == "not_mapped":
        dq["index_pe"] = "not_applicable"
    elif status == "mapped":
        dq["index_pe"] = "missing"
    else:
        dq["index_pe"] = "not_applicable"

    if result.get("premium_discount") is not None or result.get("aum") is not None:
        dq["spot"] = "available"
    elif any(e.startswith("etf_spot") for e in result.get("_errors", [])):
        dq["spot"] = "missing"
    else:
        dq["spot"] = "missing"

    hc = result.get("hedge_coverage") or {}
    cov = hc.get("coverage", "unknown")
    dq["hedge"] = "available" if cov != "unknown" else "unknown"

    return dq


# ---------------------------------------------------------------------------
# 自动标记
# ---------------------------------------------------------------------------

def _auto_flags(result: dict) -> None:
    """基于阈值自动生成 flags。"""
    flags: list[str] = []

    aum = result.get("aum")
    if aum is not None and aum < 2:
        flags.append("❌ AUM < 2 亿，存在清盘/流动性风险")

    pd_val = result.get("premium_discount")
    if pd_val is not None:
        if pd_val > 2:
            flags.append(f"⚠️ 溢价 {pd_val:.1f}%，买入成本偏高")
        elif pd_val < -2:
            flags.append(f"⚠️ 折价 {abs(pd_val):.1f}%，可能存在流动性或结构问题")

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
# ETF 行情 + K 线（净值序列）
# ---------------------------------------------------------------------------

def query_etf_quote(symbol: str, *, spot_row: Any = None) -> dict[str, Any]:
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
        if spot_row is not None:
            return _spot_row_to_quote(symbol, spot_row)
        row, err = _lookup_etf_spot_row(symbol)
        if err:
            result["_error"] = err.replace("etf_spot: ", "", 1)
            return result
        if row is None:
            result["_error"] = "empty response"
            return result
        return _spot_row_to_quote(symbol, row)
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
    from datetime import date, timedelta

    result: dict[str, Any] = {
        "symbol": symbol,
        "nav_rows": 0,
        "latest_nav": None,
        "volatility_annualized": None,
        "rsi": None,
        "rsi_period": None,
        "rsi_24": None,
        "rsi_note": "Wilder RSI on NAV closes（与个股 technical.compute 一致），非交易信号",
        "ma20": None,
        "ma60": None,
        "nav_history": [],
        "status": "missing",
        "_error": None,
    }

    try:
        import akshare as ak

        end_date = date.today().strftime("%Y%m%d")
        calendar_days = int(days * 365 / 250) + 15
        start_date = (date.today() - timedelta(days=calendar_days)).strftime("%Y%m%d")

        with akshare_direct_session():
            df = ak.fund_etf_fund_info_em(fund=symbol, start_date=start_date, end_date=end_date)

        if df is None or df.empty:
            result["_error"] = "empty response"
            return result

        result["nav_rows"] = len(df)
        navs, returns = _aligned_nav_returns(df)
        if navs:
            result["latest_nav"] = navs[-1]

        if len(returns) < 5:
            result["status"] = "insufficient"
            result["_error"] = f"only {len(returns)} daily returns"
            return result

        mean_ret = sum(returns) / len(returns)
        variance = sum((r - mean_ret) ** 2 for r in returns) / (len(returns) - 1)
        daily_vol = math.sqrt(variance)
        result["volatility_annualized"] = round(daily_vol * math.sqrt(252) * 100, 2)

        if len(navs) >= 20:
            result["ma20"] = round(sum(navs[-20:]) / 20, 4)
        if len(navs) >= 60:
            result["ma60"] = round(sum(navs[-60:]) / 60, 4)

        period = 24 if len(navs) >= 25 else (14 if len(navs) >= 15 else None)
        if period is not None:
            rsi_val = _latest_rsi(navs, period)
            result["rsi"] = rsi_val
            result["rsi_period"] = period
            result["rsi_24"] = rsi_val if period == 24 else None

        result["nav_history"] = [
            {"date": str(r.get("净值日期", "")), "nav": safe_float(r.get("单位净值")),
             "change_pct": safe_float(r.get("日增长率"))}
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

def _aligned_nav_returns(df: Any) -> tuple[list[float], list[float]]:
    """从净值表构建对齐的 navs / returns（同一行样本）。"""
    navs: list[float] = []
    returns: list[float] = []
    prev_nav: float | None = None
    for _, row_data in df.iterrows():
        nav = safe_float(row_data.get("单位净值"))
        if nav is None:
            continue
        chg = safe_float(row_data.get("日增长率"))
        if chg is not None:
            ret = chg / 100.0
        elif prev_nav is not None and prev_nav > 0:
            ret = (nav / prev_nav) - 1.0
        else:
            prev_nav = nav
            continue
        navs.append(nav)
        returns.append(ret)
        prev_nav = nav
    return navs, returns


def _latest_rsi(navs: list[float], period: int) -> float | None:
    """Latest Wilder RSI from NAV close series (aligned with lib.technical)."""
    if len(navs) < period + 1:
        return None
    series = rsi_series(navs, period)
    for val in reversed(series):
        if val is not None:
            return round(val, 2)
    return None


def rollup_etf_quality_status(etf: dict) -> str:
    """Roll up etf_data bundle to available / partial / missing (journal query_data)."""
    errors = list(etf.get("_errors") or [])
    if etf.get("_error") and not errors:
        errors = [str(etf["_error"])]
    has_data = any(
        etf.get(k) is not None
        for k in ("index_pe", "premium_discount", "aum", "tracking_error")
    ) or bool(etf.get("hedge_coverage"))
    if errors and has_data:
        return "partial"
    if errors or not has_data:
        return "missing"
    return "available"


def _em_to_premium_discount(em_raw: object) -> float | None:
    """EM 基金折价率（+ = 折价）→ premium_discount（+ = 溢价）。"""
    em = safe_float(em_raw)
    return None if em is None else -em
