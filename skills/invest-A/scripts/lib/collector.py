"""数据采集模块。封装各数据源，依赖 env.py 做可用性检测。

设计模式（参考 last30days-skill 的 parallel fan-out）：
  每个维度下，对所有可用源并行查询 → SourceResult 归一化 → DimensionResult 合并。
  失败不阻塞，选取最优源为主数据。

数据源策略（v0.3+ 并行取证）：
  有 Token: Tushare ∥ akshare ∥ baostock ∥ 腾讯 → 各渠道并行查询 → 独立记录 → 汇总为证
  无 Token: akshare ∥ baostock ∥ 腾讯 → 各渠道并行查询 → 独立记录 → 汇总为证
"""

from __future__ import annotations

import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta
from typing import Any, Callable

from . import env
from .nums import safe_float
from .proxy import (
    EASTMONEY_BLOCKED_KEYWORDS as _EASTMONEY_BLOCKED_KEYWORDS,
    EASTMONEY_FAILURE_PROXY_MARKER,
    EASTMONEY_FAILURE_TUN_MARKER,
    akshare_direct_session,
    akshare_push2_available,
    no_proxy_session,
    proxy_bypass,
)
from .schema import SourceResult, DimensionResult

logger = logging.getLogger(__name__)


# ---- 日期工具（函数形式，避免导入时固化） ----

def _today() -> str:
    return datetime.now().strftime("%Y%m%d")


def _days_ago(n: int) -> str:
    return (datetime.now() - timedelta(days=n)).strftime("%Y%m%d")


def _fred_date(yyyymmdd: str) -> str:
    """Tushare 风格 YYYYMMDD → FRED API 要求的 YYYY-MM-DD。"""
    s = yyyymmdd.strip()
    if len(s) == 8 and s.isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    return s


def _latest_quarter_end() -> str:
    """返回最近一个已完整的季度末日期（0331/0630/0930/1231）。

    确保季度末日期的完整日已经过去（不提前返回当天）。
    """
    from datetime import date
    today = date.today()
    now = datetime.now()
    quarter_ends = [
        (now.year, "0331"),
        (now.year, "0630"),
        (now.year, "0930"),
        (now.year, "1231"),
    ]
    for y, md in reversed(quarter_ends):
        d = datetime.strptime(f"{y}{md}", "%Y%m%d")
        # 用 date 比较确保季度末整日已过（如 6/30 15:00 不视作 Q2 已完成）
        if today > d.date():
            return f"{y}{md}"
    return f"{now.year - 1}1231"


# ---- 交易所代码转换（共享函数，三种格式统一调度） ----

def _exchange_code(symbol: str) -> dict[str, str]:
    """根据股票代码前缀返回各 API 格式的交易所代码。

    返回 dict:
      tushare: "600176.SH"
      baostock: "sh.600176"
      akshare: "sh600176"

    上海: 6xxx, 9xxx
    北京: 4xxx, 8xxx
    深圳: 0xxx, 2xxx, 3xxx
    """
    s = symbol.strip().zfill(6)
    if s.startswith(("6", "9")):
        return {"tushare": f"{s}.SH", "baostock": f"sh.{s}", "akshare": f"sh{s}"}
    if s.startswith(("4", "8")):
        return {"tushare": f"{s}.BJ", "baostock": f"bj.{s}", "akshare": f"bj{s}"}
    return {"tushare": f"{s}.SZ", "baostock": f"sz.{s}", "akshare": f"sz{s}"}


def _ts_code(symbol: str) -> str:
    """转为 Tushare 格式：600176 → 600176.SH（委托 _exchange_code）。"""
    return _exchange_code(symbol)["tushare"]


# 向后兼容：测试与外部调用仍可从 collector 导入 _proxy_bypass
_proxy_bypass = proxy_bypass

# Baostock 全局 socket 非线程安全，需串行化访问
_BAOSTOCK_LOCK = threading.Lock()

_EASTMONEY_PROXY_MSG = (
    "东方财富(East Money) API 连接失败。"
    f"{EASTMONEY_FAILURE_PROXY_MARKER}，请在 Clash 规则中将 DOMAIN-SUFFIX,eastmoney.com,DIRECT；"
    "或暂时关闭全局代理后重试。"
    "可改用 Tushare / Baostock 作为替代数据源。"
)
_EASTMONEY_TUN_OR_CDN_MSG = (
    f"东方财富 {EASTMONEY_FAILURE_TUN_MARKER}（非 HTTP 代理问题，可能为 TUN 劫持或 CDN 限制）。"
    "已使用 Tushare / Baostock 替代。"
)


def _is_eastmoney_blocked_error(error: str) -> bool:
    """检测异常消息是否明确指向东方财富。"""
    return any(kw in str(error) for kw in _EASTMONEY_BLOCKED_KEYWORDS)


def _eastmoney_failure_message() -> str:
    from .proxy import proxy_status

    status = proxy_status(probe=False)
    if status.get("bypass_effective"):
        return _EASTMONEY_TUN_OR_CDN_MSG
    return _EASTMONEY_PROXY_MSG


def _reraise_eastmoney_api_error(exc: Exception) -> None:
    """在东方财富 akshare 接口内，将连接失败转为可操作提示。

    仅在已知调用东方财富 API 的函数中使用，避免误伤同花顺等其他源。
    """
    msg = _eastmoney_failure_message()
    if _is_eastmoney_blocked_error(str(exc)):
        raise RuntimeError(msg) from exc
    err = str(exc)
    if any(kw in err for kw in (
        "Connection", "Remote end closed", "RemoteDisconnected", "ProxyError",
        "Max retries exceeded",
    )):
        raise RuntimeError(msg) from exc
    raise exc


def _baostock_code(symbol: str) -> str:
    """Baostock 证券代码：sz. / sh. / bj. 前缀（委托 _exchange_code）。"""
    return _exchange_code(symbol)["baostock"]


# ---- 并行执行辅助 ----

def _run_sources_parallel(tasks: list[tuple[str, Callable[[], Any]]],
                          dimension: str) -> list[SourceResult]:
    """并行执行多个源查询任务，返回 SourceResult 列表。

    last30days 的 ThreadPoolExecutor fan-out 模式：
    - 每个任务独立提交
    - 失败不阻塞其他任务
    - 返回所有结果（含失败）供合并

    Args:
        tasks: [(source_name, callable), ...]
        dimension: 维度标识
    """
    if not tasks:
        return []

    sources: list[SourceResult | None] = []
    with ThreadPoolExecutor(max_workers=min(len(tasks), 8)) as executor:
        futures = {
            executor.submit(_run_one_source, name, fn, dimension): i
            for i, (name, fn) in enumerate(tasks)
        }
        results: dict[int, SourceResult] = {}
        for future in as_completed(futures):
            idx = futures[future]
            try:
                results[idx] = future.result()
            except Exception as exc:
                results[idx] = SourceResult(
                    source=f"__internal__",
                    data=None,
                    dimension=dimension,
                    error=f"Executor failure: {exc}",
                )
        sources = [results.get(i) for i in range(len(tasks))]

    return [s for s in sources if s is not None]


def _annotate_query_params(result_map: dict[str, SourceResult],
                           params: dict[str, str]) -> None:
    """为 result_map 中的 SourceResult 设置 query_params（无论成功/失败）。"""
    for name, qp in params.items():
        if name in result_map:
            result_map[name].query_params = qp


def _run_one_source(name: str, fn: Callable[[], Any], dimension: str) -> SourceResult:
    """包装单个源查询为 SourceResult。"""
    start = time.time()
    try:
        data = fn()
        elapsed = (time.time() - start) * 1000
        if data is not None:
            return SourceResult(name, data, dimension, latency_ms=elapsed)
        return SourceResult(name, None, dimension, error="No data returned",
                           latency_ms=elapsed)
    except Exception as e:
        elapsed = (time.time() - start) * 1000
        logger.warning("Source %s failed: %s", name, e)
        return SourceResult(name, None, dimension, error=str(e),
                           latency_ms=elapsed)


# ---- 单个源查询函数 ----

def _q_tushare_basic(symbol: str) -> dict | None:
    """Tushare 基本信息来源。"""
    from . import env as _env
    config = _env.get_config()
    if not _env.is_tushare_available(config):
        raise RuntimeError("TUSHARE_TOKEN not configured")
    tc = _tushare_client(config)
    df = tc.query("stock_basic", ts_code=_ts_code(symbol),
                  fields="ts_code,name,area,industry,market,list_date")
    if df is not None and not df.empty:
        return df.iloc[0].to_dict()
    return None


def _merge_cashflow_into_financials(
    financials: list[dict], cashflow: list[dict],
) -> list[dict]:
    """按 end_date 合并经营现金流，供 CV-1 交叉验证。"""
    cf_by_date = {str(r.get("end_date", "")): r for r in cashflow}
    out: list[dict] = []
    for row in financials:
        merged = dict(row)
        cf = cf_by_date.get(str(row.get("end_date", "")))
        if cf:
            ncf = cf.get("n_cashflow_act")
            if ncf is not None:
                merged["n_cashflow_act"] = ncf
                merged["ocf"] = ncf
        out.append(merged)
    return out


def _merge_balancesheet_into_financials(
    financials: list[dict], balancesheet: list[dict],
) -> list[dict]:
    """按 end_date 合并应收/存货，供 CV-2 交叉验证。"""
    bs_by_date = {str(r.get("end_date", "")): r for r in balancesheet}
    out: list[dict] = []
    for row in financials:
        merged = dict(row)
        bs = bs_by_date.get(str(row.get("end_date", "")))
        if bs:
            ar = bs.get("accounts_rece")
            if ar is None:
                ar = bs.get("accounts_receiv")
            if ar is not None:
                merged["accounts_receiv"] = ar
            inv = bs.get("inventories")
            if inv is None:
                inv = bs.get("inventory")
            if inv is not None:
                merged["inventory"] = inv
        out.append(merged)
    return out


def _q_tushare_financials(symbol: str) -> list[dict] | None:
    from . import env as _env
    config = _env.get_config()
    if not _env.is_tushare_available(config):
        raise RuntimeError("TUSHARE_TOKEN not configured")
    tc = _tushare_client(config)
    ts = _ts_code(symbol)
    lookback = _days_ago(730)
    end = _today()
    df = tc.query(
        "fina_indicator", ts_code=ts,
        fields=(
            "ts_code,end_date,roe,eps,profit_dedt,revenue,net_profit,"
            "grossprofit_margin,netprofit_margin,assets_turn,eqt_to_debt,"
            "debt_to_assets"
        ),
        start_date=lookback, end_date=end,
    )
    if df is None or df.empty:
        return None
    records = df.to_dict("records")
    for rec in records:
        em = rec.get("eqt_to_debt")
        if em is not None and safe_float(em) not in (None, 0):
            rec["equity_multiplier"] = 1.0 + 1.0 / float(em)
        elif rec.get("debt_to_assets") is not None:
            da = safe_float(rec.get("debt_to_assets"))
            if da is not None and 0 <= da <= 100:
                # Tushare debt_to_assets 为百分比（0-100），如 0.8 表示 0.8%
                rec["equity_multiplier"] = 1.0 / max(0.01, (100.0 - da) / 100.0)
    cf_df = tc.query("cashflow", ts_code=ts,
                     fields="ts_code,end_date,n_cashflow_act",
                     start_date=lookback, end_date=end)
    if cf_df is not None and not cf_df.empty:
        records = _merge_cashflow_into_financials(records, cf_df.to_dict("records"))
    elif cf_df is None or cf_df.empty:
        logger.warning("Tushare cashflow query returned empty for %s; n_cashflow_act field will be missing from records", ts)
    bs_df = tc.query("balancesheet", ts_code=ts,
                     fields="ts_code,end_date,accounts_rece,inventories",
                     start_date=lookback, end_date=end)
    if bs_df is not None and not bs_df.empty:
        records = _merge_balancesheet_into_financials(records, bs_df.to_dict("records"))
    elif bs_df is None or bs_df.empty:
        logger.warning("Tushare balancesheet query returned empty for %s; accounts_receiv/inventory fields will be missing from records", ts)
    return records


def _q_tushare_shareholders(symbol: str) -> list[dict] | None:
    """Tushare 十大股东（最新报告期）。"""
    from . import env as _env
    config = _env.get_config()
    if not _env.is_tushare_available(config):
        raise RuntimeError("TUSHARE_TOKEN not configured")
    tc = _tushare_client(config)
    df = tc.query("top10_floatholders", ts_code=_ts_code(symbol),
                  fields="ts_code,end_date,holder_name,hold_amount,hold_ratio",
                  period=_latest_quarter_end())
    if df is not None and not df.empty:
        return df.to_dict("records")
    return None


def _q_tushare_daily(symbol: str, **kwargs) -> list[dict] | None:
    from . import env as _env
    config = _env.get_config()
    if not _env.is_tushare_available(config):
        raise RuntimeError("TUSHARE_TOKEN not configured")
    tc = _tushare_client(config)
    df = tc.query("daily", ts_code=_ts_code(symbol),
                  fields="trade_date,open,high,low,close,vol,amount",
                  **kwargs)
    if df is not None and not df.empty:
        return df.to_dict("records")
    return None


def _normalize_northbound_records(records: list[dict], source: str) -> list[dict]:
    """统一主力资金/北向净额为「元」。

    Tushare moneyflow: ``net_mf_amount`` 单位为万元。
    akshare 北向: ``今日增持资金`` 映射为 ``net_mf_vol``，单位已是元。
    输出同时写入 ``net_mf_amount`` 与 ``net_mf_vol``（后者为兼容别名）。
    """
    if not records:
        return records
    # 仅 moneyflow 为万元；hsgt_top10.net_amount 已是元
    scale = 10000.0 if source.startswith("tushare.moneyflow") else 1.0
    out: list[dict] = []
    for r in records:
        row = dict(r)
        raw = row.get("net_mf_amount")
        if raw is None:
            raw = row.get("net_mf_vol")
        if raw is not None:
            yuan = float(raw) * scale
            row["net_mf_amount"] = yuan
            row["net_mf_vol"] = yuan
        out.append(row)
    return out


def _flow_amount_yuan(record: dict) -> float | None:
    """从归一化后的资金流记录读取净额（元），缺失时返回 None。"""
    val = record.get("net_mf_amount")
    if val is None:
        val = record.get("net_mf_vol")
    if val is None:
        return None
    return float(val)


def _q_tushare_moneyflow(symbol: str) -> list[dict] | None:
    from . import env as _env
    config = _env.get_config()
    if not _env.is_tushare_available(config):
        raise RuntimeError("TUSHARE_TOKEN not configured")
    tc = _tushare_client(config)
    df = tc.query("moneyflow", ts_code=_ts_code(symbol),
                  fields="ts_code,trade_date,net_mf_amount,buy_sm_vol,sell_sm_vol,net_mf_vol",
                  start_date=_days_ago(10), end_date=_today())
    if df is not None and not df.empty:
        return _normalize_northbound_records(df.to_dict("records"), "tushare.moneyflow")
    return None


def _q_tushare_hsgt_top10(symbol: str) -> list[dict] | None:
    """个股沪/深股通成交（仅上榜日有数据）。net_amount 单位：元。"""
    from . import env as _env
    config = _env.get_config()
    if not _env.is_tushare_available(config):
        raise RuntimeError("TUSHARE_TOKEN not configured")
    tc = _tushare_client(config)
    df = tc.query("hsgt_top10", ts_code=_ts_code(symbol),
                  fields="ts_code,trade_date,net_amount",
                  start_date=_days_ago(30), end_date=_today())
    if df is None or df.empty:
        return None
    rows = [
        {"trade_date": r.get("trade_date"), "net_mf_amount": r.get("net_amount")}
        for r in df.to_dict("records")
        if r.get("net_amount") is not None
    ]
    if not rows:
        return None
    return _normalize_northbound_records(rows, "tushare.hsgt_top10")


def _q_akshare_basic(symbol: str) -> dict | None:
    """akshare 基本信息来源（东方财富 push2 API）。"""
    with akshare_direct_session():
        import akshare as ak
        try:
            result = ak.stock_individual_info_em(symbol=symbol.strip().zfill(6),
                                                  timeout=8)
            if result is not None:
                if hasattr(result, "to_dict"):
                    records = result.to_dict("records") if callable(result.to_dict) else result.to_dict
                    if isinstance(records, list) and records:
                        return {str(r.get("item", "")): r.get("value", "") for r in records}
                if isinstance(result, dict):
                    return result
            return None
        except Exception as e:
            _reraise_eastmoney_api_error(e)


def _q_akshare_financials(symbol: str) -> list[dict] | None:
    with _proxy_bypass():
        import akshare as ak
        result = ak.stock_financial_abstract_ths(symbol=symbol.strip().zfill(6),
                                                 indicator="按报告期")
        if result is not None and hasattr(result, "to_dict"):
            records = result.to_dict("records") if callable(result.to_dict) else result.to_dict
            if records:
                return [_map_akshare_financial_keys(r) for r in records]
        return None


def _q_akshare_kline(symbol: str, start_date: str = "", end_date: str = "") -> list[dict] | None:
    """akshare K线来源（东方财富 push2 API）。"""
    with akshare_direct_session():
        import akshare as ak
        sd = start_date or _days_ago(365)
        ed = end_date or _today()
        sd_fmt = f"{sd[:4]}-{sd[4:6]}-{sd[6:]}"
        ed_fmt = f"{ed[:4]}-{ed[4:6]}-{ed[6:]}"
        try:
            result = ak.stock_zh_a_hist(symbol=symbol.strip().zfill(6),
                                        period="daily",
                                        start_date=sd_fmt,
                                        end_date=ed_fmt,
                                        adjust="",
                                        timeout=10)
            if result is not None and hasattr(result, "to_dict"):
                records = result.to_dict("records") if callable(result.to_dict) else result.to_dict
                if records:
                    return [_map_akshare_kline_keys(r) for r in records]
            return None
        except Exception as e:
            _reraise_eastmoney_api_error(e)


def _q_akshare_northbound(symbol: str) -> list[dict] | None:
    with akshare_direct_session():
        import akshare as ak
        try:
            result = ak.stock_hsgt_individual_em(symbol=symbol.strip().zfill(6))
            if result is not None and hasattr(result, "to_dict"):
                records = result.to_dict("records") if callable(result.to_dict) else result.to_dict
                if records:
                    mapped = [_map_akshare_northbound_keys(r) for r in records]
                    return _normalize_northbound_records(mapped, "akshare.northbound")
            return None
        except Exception as e:
            _reraise_eastmoney_api_error(e)


# ---- akshare 中文列名 → 英文键名映射 ----

def _map_akshare_kline_keys(r: dict) -> dict:
    """akshare stock_zh_a_hist 列名映射。"""
    return {
        "trade_date": str(r.get("日期", "")),
        "open": r.get("开盘"),
        "high": r.get("最高"),
        "low": r.get("最低"),
        "close": r.get("收盘"),
        "vol": r.get("成交量"),
    }


def _parse_akshare_num(v) -> float | None:
    """将 akshare 返回的字符串数值转为 float，兼容 '%' / '万亿' / '亿' / '万' 后缀。

    例如 "8.37%" → 8.37, "17.88亿" → 1788000000.0, "2.35万亿" → 2.35e12
    """
    if v is None:
        return None
    if isinstance(v, str):
        s = v.strip().replace(",", "").replace(" ", "")
        multiplier = 1.0
        if "万亿" in s:
            multiplier = 1e12
            s = s.replace("万亿", "")
        elif "亿" in s:
            multiplier = 1e8
            s = s.replace("亿", "")
        elif "万" in s:
            multiplier = 1e4
            s = s.replace("万", "")
        if "%" in s:
            s = s.replace("%", "")
        try:
            return float(s) * multiplier
        except (ValueError, TypeError):
            return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _map_akshare_financial_keys(r: dict) -> dict:
    """akshare stock_financial_abstract_ths 列名映射。

    注意：akshare 返回的数值带中文单位（如 "17.88亿"、"8.37%"),
    _parse_akshare_num 将其转换为与 Tushare 一致的纯 float 格式。
    """
    out = {
        "end_date": str(r.get("报告期", "")),
        "roe": _parse_akshare_num(r.get("净资产收益率")),
        "eps": _parse_akshare_num(r.get("基本每股收益")),
        "profit_dedt": _parse_akshare_num(r.get("扣非净利润")),
        "revenue": _parse_akshare_num(r.get("营业总收入")),
        "net_profit": _parse_akshare_num(r.get("净利润")),
    }
    gm = _parse_akshare_num(r.get("销售毛利率") or r.get("毛利率"))
    if gm is not None:
        out["grossprofit_margin"] = gm
    ar = _parse_akshare_num(r.get("应收账款"))
    if ar is not None:
        out["accounts_receiv"] = ar
    inv = _parse_akshare_num(r.get("存货"))
    if inv is not None:
        out["inventory"] = inv
    return out


def _map_akshare_northbound_keys(r: dict) -> dict:
    """akshare stock_hsgt_individual_em 列名映射。"""
    return {
        "trade_date": str(r.get("持股日期", "")),
        "net_mf_vol": r.get("今日增持资金"),
    }


# ---- akshare 股东信息 ----

def _akshare_top10_code(symbol: str) -> str:
    """akshare 股东接口需要的代码格式：sh600519 / sz000858（委托 _exchange_code）。"""
    return _exchange_code(symbol)["akshare"]


def _q_akshare_shareholders(symbol: str) -> list[dict] | None:
    """akshare 前十大股东来源（东方财富 datacenter API）。"""
    with akshare_direct_session():
        import akshare as ak
        dates_to_try = _latest_quarter_dates()
        for date_str in dates_to_try:
            try:
                code = _akshare_top10_code(symbol)
                result = ak.stock_gdfx_top_10_em(symbol=code, date=date_str)
                if result is not None and hasattr(result, "to_dict"):
                    records = result.to_dict("records") if callable(result.to_dict) else result.to_dict
                    if records:
                        return [{"holder_name": str(r.get("股东名称", "")),
                                 "hold_amount": r.get("持股数"),
                                 "hold_ratio": r.get("占总股本持股比例")}
                                for r in records[:10]]
            except Exception as e:
                if _is_eastmoney_blocked_error(str(e)):
                    _reraise_eastmoney_api_error(e)
                continue
        return None


# ---- akshare 行业数据查询辅助 ----


def _q_akshare_industry_board(symbol: str, industry_name: str = "") -> dict | None:
    """获取个股所属行业板块的近期行情（akshare 东方财富）。

    Returns:
        dict with keys: industry_name, board_code, recent_return_pct
        或 None（采集失败时）
    """
    if not env.is_akshare_available() or not akshare_push2_available():
        return None
    try:
        with akshare_direct_session():
            import akshare as ak
            # 获取行业板块列表
            df = ak.stock_board_industry_name_em()
            if df is None or df.empty:
                return None

            # 获取个股所属行业（优先使用预取，避免重复 API）
            if not industry_name:
                info = _q_akshare_basic(symbol)
                if not info:
                    return None
                industry_name = info.get("行业") or info.get("industry", "")
            if not industry_name:
                return None

            # 在板块列表中匹配行业
            matched = df[df["板块名称"].str.contains(industry_name, na=False)]
            if matched.empty:
                # 模糊匹配
                for _, row in df.iterrows():
                    name = str(row.get("板块名称", ""))
                    if industry_name in name or name in industry_name:
                        matched = df[df["板块名称"] == name]
                        break

            if matched.empty:
                return {"industry_name": industry_name, "board_code": None,
                        "note": "未在板块列表中找到匹配"}

            board_name = str(matched.iloc[0]["板块名称"])
            board_code = str(matched.iloc[0]["板块代码"])

            # 获取板块历史行情（近30日）
            try:
                hist = ak.stock_board_industry_hist_em(
                    symbol=board_name,
                    period="日k",
                    start_date=_days_ago(30)[:4] + "-" + _days_ago(30)[4:6] + "-" + _days_ago(30)[6:],
                    end_date=_today()[:4] + "-" + _today()[4:6] + "-" + _today()[6:],
                    adjust="",
                )
                if hist is not None and not hist.empty:
                    closes = [
                        f for v in hist["收盘"].tolist()
                        if (f := safe_float(v)) is not None
                    ]
                    recent_ret = None
                    if len(closes) >= 2 and closes[0] != 0:
                        recent_ret = safe_float(
                            (closes[-1] - closes[0]) / closes[0] * 100,
                        )
                    return {
                        "industry_name": industry_name,
                        "board_name": board_name,
                        "board_code": board_code,
                        "recent_return_pct": (
                            round(recent_ret, 2) if recent_ret is not None else None
                        ),
                        "trading_days_in_window": len(closes),
                        "source": "akshare.stock_board_industry_hist_em",
                    }
            except Exception as exc:
                logger.debug("akshare board hist failed for %s: %s", board_name, exc)

            return {
                "industry_name": industry_name,
                "board_name": board_name,
                "board_code": board_code,
                "source": "akshare.stock_board_industry_name_em",
            }
    except Exception as exc:
        logger.debug("akshare industry board failed for %s: %s", symbol, exc)
        return None


def _q_akshare_industry_pe(symbol: str, industry_name: str = "") -> dict | None:
    """获取行业PE中位数（akshare/巨潮资讯）。

    Returns:
        dict with: industry_pe_median, industry_pe_avg, company_pe, relative_position
        或 None
    """
    if not env.is_akshare_available() or not akshare_push2_available():
        return None
    try:
        with akshare_direct_session():
            import akshare as ak
            df = ak.stock_board_industry_pe_ratio_cninfo()
            if df is None or df.empty:
                return None

            # 获取个股行业（优先使用预取）
            if not industry_name:
                info = _q_akshare_basic(symbol)
                if not info:
                    return None
                industry_name = info.get("行业") or info.get("industry", "")

            # 匹配行业PE
            matched = df[df["行业名称"].str.contains(industry_name, na=False)]
            if matched.empty:
                for _, row in df.iterrows():
                    name = str(row.get("行业名称", ""))
                    if industry_name in name or name in industry_name:
                        matched = df[df["行业名称"] == name]
                        break

            if matched.empty:
                return {"industry_name": industry_name, "note": "未匹配到行业PE数据"}

            row = matched.iloc[0]
            pe_median = safe_float(row.get("市盈率中位数") or row.get("市盈率"))
            pe_avg = safe_float(row.get("市盈率平均值"))

            return {
                "industry_name": str(row.get("行业名称", "")),
                "industry_pe_median": pe_median,
                "industry_pe_avg": pe_avg,
                "stock_count": safe_float(row.get("公司数量")),
                "source": "akshare.stock_board_industry_pe_ratio_cninfo",
            }
    except Exception as exc:
        logger.debug("akshare industry PE failed for %s: %s", symbol, exc)
        return None


def _latest_quarter_dates(as_of: datetime | None = None, count: int = 5) -> list[str]:
    """返回最近 count 个已结束季末日期（YYYYMMDD），用于股东多期查询。"""
    import calendar
    from datetime import datetime

    now = as_of or datetime.now()
    dates: list[str] = []
    year, quarter = now.year, (now.month - 1) // 3 + 1

    while len(dates) < count:
        end_month = quarter * 3
        last_day = calendar.monthrange(year, end_month)[1]
        q_end = datetime(year, end_month, last_day)
        if q_end <= now:
            dates.append(q_end.strftime("%Y%m%d"))
        quarter -= 1
        if quarter < 1:
            quarter = 4
            year -= 1
    return dates


def _q_baostock_kline(symbol: str, start_date: str = "", end_date: str = "") -> list[dict] | None:
    """Baostock K 线来源（免费、稳定，适合历史日K线）。

    使用 _BAOSTOCK_LOCK 串行化访问：Baostock 内部使用全局单例 socket，
    多线程并行调用会导致连接竞态。
    """
    with _BAOSTOCK_LOCK, _proxy_bypass():
        import baostock as bs
        logged_in = False
        try:
            lg = bs.login()
            if lg.error_code != "0":
                logger.warning("baostock login failed: %s", lg.error_msg)
                return None
            logged_in = True

            sd = start_date or _days_ago(365)
            ed = end_date or _today()
            sd_fmt = f"{sd[:4]}-{sd[4:6]}-{sd[6:]}"
            ed_fmt = f"{ed[:4]}-{ed[4:6]}-{ed[6:]}"

            bs_code = _baostock_code(symbol)
            rs = bs.query_history_k_data_plus(
                bs_code,
                "date,open,high,low,close,volume,amount",
                start_date=sd_fmt, end_date=ed_fmt,
                frequency="d", adjustflag="3",
            )
            if rs.error_code != "0":
                logger.warning("baostock query failed: %s", rs.error_msg)
                return None

            rows = []
            while rs.next():
                row = rs.get_row_data()
                rows.append({
                    "trade_date": row[0].replace("-", ""),
                    "open": float(row[1]) if row[1] else None,
                    "high": float(row[2]) if row[2] else None,
                    "low": float(row[3]) if row[3] else None,
                    "close": float(row[4]) if row[4] else None,
                    "vol": float(row[5]) if row[5] else 0,
                    "amount": float(row[6]) if row[6] else 0,
                })
            return rows if rows else None
        except Exception as e:
            logger.warning("baostock query failed: %s", e)
            return None
        finally:
            if logged_in:
                try:
                    bs.logout()
                except Exception:
                    pass


def _q_tencent_quote(symbol: str) -> dict | None:
    """腾讯行情。"""
    _UNAVAILABLE_MARKERS = ("--", "N/A", "", "—")

    def _safe_float(val: str | None) -> float | None:
        """解析腾讯行情字段；不可用标记返回 None（与真实 0 区分）。"""
        if val is None or val in _UNAVAILABLE_MARKERS:
            return None
        try:
            return float(val)
        except (ValueError, TypeError):
            return None

    market = "sh" if symbol.startswith(("6", "9")) else "sz"
    with no_proxy_session() as sess:
        r = sess.get(f"http://qt.gtimg.cn/q={market}{symbol}", timeout=5)
    if r.status_code == 200 and "~" in r.text:
        p = r.text.split("~")
        if len(p) > 45:
            mv = _safe_float(p[45])
            return {
                "price": _safe_float(p[3]),
                "change_pct": _safe_float(p[32]),
                "high": _safe_float(p[33]),
                "low": _safe_float(p[34]),
                "volume": _safe_float(p[6]),
                "turnover_rate": _safe_float(p[38]),
                "pe_ratio": _safe_float(p[39]),
                "total_mv": mv / 10000 if mv is not None else None,
            }
    return None


# ---- 查询参数字符串生成 ----

def _qp_tushare(api: str, symbol: str, **kw) -> str:
    pairs = [f"{k}='{v}'" for k, v in sorted(kw.items()) if v]
    return f"pro.{api}(ts_code='{_ts_code(symbol)}'{', ' + ', '.join(pairs) if pairs else ''})"


def _qp_akshare(name: str, symbol: str, **kw) -> str:
    pairs = [f"{k}='{v}'" for k, v in sorted(kw.items()) if v]
    return f"ak.{name}(symbol='{symbol.strip().zfill(6)}'{', ' + ', '.join(pairs) if pairs else ''})"


def _qp_tencent(symbol: str) -> str:
    market = "sh" if symbol.startswith(("6", "9")) else "sz"
    return f"qt.gtimg.cn/q={market}{symbol}"


def _qp_baostock(symbol: str, start_date: str, end_date: str) -> str:
    code = _baostock_code(symbol)
    return (
        f"bs.query_history_k_data_plus(code='{code}', "
        f"start='{start_date}', end='{end_date}', frequency='d')"
    )


# ---- 各维度采集（并行 fan-out）----

def collect_basic_info(symbol: str) -> dict:
    """基本信息。并行：Tushare + akshare。"""
    tasks: list[tuple[str, Callable]] = []
    if env.is_tushare_available(env.get_config()):
        tasks.append(("tushare.stock_basic", lambda: _q_tushare_basic(symbol)))
    if env.is_akshare_available() and akshare_push2_available():
        tasks.append(("akshare.stock_individual_info_em",
                      lambda: _q_akshare_basic(symbol)))

    results = _run_sources_parallel(tasks, "basic_info")
    result_map = {r.source: r for r in results}
    _annotate_query_params(result_map, {
        "tushare.stock_basic": _qp_tushare("stock_basic", symbol),
        "akshare.stock_individual_info_em": _qp_akshare("stock_individual_info_em", symbol),
    })

    dim = DimensionResult("basic_info", results)
    return dim.to_legacy_dict()


def collect_financials(symbol: str) -> dict:
    """财务报告。并行：Tushare + akshare。"""
    tasks: list[tuple[str, Callable]] = []
    if env.is_tushare_available(env.get_config()):
        tasks.append(("tushare.fina_indicator", lambda: _q_tushare_financials(symbol)))
    if env.is_akshare_available():
        tasks.append(("akshare.stock_financial_abstract_ths",
                      lambda: _q_akshare_financials(symbol)))

    results = _run_sources_parallel(tasks, "financials")
    result_map = {r.source: r for r in results}
    _annotate_query_params(result_map, {
        "tushare.fina_indicator": _qp_tushare("fina_indicator", symbol,
                                              start_date=_days_ago(730), end_date=_today()),
        "akshare.stock_financial_abstract_ths": _qp_akshare(
            "stock_financial_abstract_ths", symbol, indicator="按报告期"),
    })

    dim = DimensionResult("financials", results)
    return dim.to_legacy_dict()


def collect_shareholders(symbol: str) -> dict:
    """十大股东。并行：Tushare + akshare。"""
    tasks: list[tuple[str, Callable]] = []
    if env.is_tushare_available(env.get_config()):
        tasks.append(("tushare.top10_floatholders", lambda: _q_tushare_shareholders(symbol)))
    if env.is_akshare_available() and akshare_push2_available():
        tasks.append(("akshare.stock_gdfx_top_10_em",
                      lambda: _q_akshare_shareholders(symbol)))

    results = _run_sources_parallel(tasks, "shareholders")
    result_map = {r.source: r for r in results}

    _annotate_query_params(result_map, {
        "tushare.top10_floatholders": _qp_tushare("top10_floatholders", symbol,
                                                  period=_latest_quarter_end()),
        "akshare.stock_gdfx_top_10_em": _qp_akshare("stock_gdfx_top_10_em", symbol),
    })

    dim = DimensionResult("shareholders", results)
    return dim.to_legacy_dict()


def collect_quote(symbol: str) -> dict:
    """实时行情。并行：Tushare + akshare + 腾讯。"""
    tasks: list[tuple[str, Callable]] = []
    if env.is_tushare_available(env.get_config()):
        tasks.append(("tushare.daily", lambda: _q_tushare_daily(symbol,
                      start_date=_days_ago(10), end_date=_today())))
    if env.is_akshare_available() and akshare_push2_available():
        tasks.append(("akshare.stock_zh_a_hist",
                      lambda: _q_akshare_kline(symbol, start_date=_days_ago(10), end_date=_today())))
    tasks.append(("tencent_finance", lambda: _q_tencent_quote(symbol)))

    results = _run_sources_parallel(tasks, "quote")
    result_map = {r.source: r for r in results}
    _annotate_query_params(result_map, {
        "tushare.daily": _qp_tushare("daily", symbol,
                                     start_date=_days_ago(10), end_date=_today()),
        "akshare.stock_zh_a_hist": _qp_akshare("stock_zh_a_hist", symbol,
                                               period="daily",
                                               start_date=_days_ago(10),
                                               end_date=_today()),
        "tencent_finance": _qp_tencent(symbol),
    })

    dim = DimensionResult("quote", results)
    return dim.to_legacy_dict()


def collect_northbound(symbol: str) -> dict:
    """北向资金。并行：Tushare hsgt_top10 + akshare 个股持股变动。"""
    tasks: list[tuple[str, Callable]] = []
    if env.is_tushare_available(env.get_config()):
        tasks.append(("tushare.hsgt_top10", lambda: _q_tushare_hsgt_top10(symbol)))
    if env.is_akshare_available() and akshare_push2_available():
        tasks.append(("akshare.stock_hsgt_individual_em",
                      lambda: _q_akshare_northbound(symbol)))

    results = _run_sources_parallel(tasks, "northbound")
    result_map = {r.source: r for r in results}
    _annotate_query_params(result_map, {
        "tushare.hsgt_top10": _qp_tushare("hsgt_top10", symbol,
                                          start_date=_days_ago(30), end_date=_today()),
        "akshare.stock_hsgt_individual_em": _qp_akshare("stock_hsgt_individual_em", symbol),
    })

    dim = DimensionResult("northbound", results)
    return dim.to_legacy_dict()


def collect_kline(symbol: str, start_date: str = "", end_date: str = "") -> dict:
    """日K线。并行：Tushare + akshare + baostock。

    默认窗口 400 自然日，覆盖 MA250（需 ≥250 个交易日缓冲）。
    --deep 模式通过 invest.py 传入 start_date=_days_ago(730)。
    """
    sd = start_date or _days_ago(400)
    ed = end_date or _today()

    tasks: list[tuple[str, Callable]] = []
    if env.is_tushare_available(env.get_config()):
        tasks.append(("tushare.daily", lambda: _q_tushare_daily(symbol, start_date=sd, end_date=ed)))
    if env.is_akshare_available() and akshare_push2_available():
        tasks.append(("akshare.stock_zh_a_hist",
                      lambda: _q_akshare_kline(symbol, start_date=sd, end_date=ed)))
    if env.is_baostock_available():
        tasks.append(("baostock.kline",
                      lambda: _q_baostock_kline(symbol, start_date=sd, end_date=ed)))

    results = _run_sources_parallel(tasks, "kline")
    result_map = {r.source: r for r in results}
    qp_map: dict[str, str] = {
        "tushare.daily": _qp_tushare("daily", symbol, start_date=sd, end_date=ed),
        "akshare.stock_zh_a_hist": _qp_akshare("stock_zh_a_hist", symbol,
                                               period="daily", start_date=sd, end_date=ed),
    }
    if env.is_baostock_available():
        qp_map["baostock.kline"] = _qp_baostock(symbol, sd, ed)
    _annotate_query_params(result_map, qp_map)

    dim = DimensionResult("kline", results)
    return dim.to_legacy_dict()


# ---- Tushare 客户端惰性加载 ----
# 使用 threading.local() 避免多线程共享同一个 requests.Session
# （requests.Session 不是线程安全的，且 TushareClient 内部维护配额计数无锁保护）

_tc_local = threading.local()


def _tushare_client(config: dict) -> Any:
    """按线程惰性加载 TushareClient，配置变化时重建实例。"""
    token = config.get("TUSHARE_TOKEN")
    if not hasattr(_tc_local, "instance") or getattr(_tc_local, "_tc_token", None) != token:
        from lib.tushare_client import TushareClient
        _tc_local.instance = TushareClient(token=token)
        _tc_local._tc_token = token
    return _tc_local.instance


# ---- 估值维度 ----

def _q_tushare_daily_basic(symbol: str) -> list[dict] | None:
    """Tushare daily_basic 接口：获取每日 PE/PB/PS 历史序列。

    API: pro.daily_basic(ts_code, start_date, end_date, fields=...)
    配额: 每股 1 次调用。
    """
    from . import env as _env
    config = _env.get_config()
    if not _env.is_tushare_available(config):
        raise RuntimeError("TUSHARE_TOKEN not configured")
    tc = _tushare_client(config)
    df = tc.query("daily_basic", ts_code=_ts_code(symbol),
                  fields="trade_date,pe,pe_ttm,pb,ps,ps_ttm,dv_ratio,total_mv",
                  start_date=_days_ago(1825), end_date=_today())
    if df is not None and not df.empty:
        return df.to_dict("records")
    return None


def _q_tencent_valuation_snapshot(symbol: str) -> dict | None:
    """腾讯行情估值快照：当前 PE。作为 Tushare 不可用时的降级源。"""
    quote = _q_tencent_quote(symbol)
    if quote is None:
        return None
    result: dict[str, Any] = {}
    if quote.get("pe_ratio") is not None:
        result["pe_ttm"] = quote["pe_ratio"]
    if quote.get("total_mv") is not None:
        result["total_mv"] = quote["total_mv"]
    result["history_available"] = False  # 腾讯仅为快照，无历史序列
    return result if result else None


def _qp_tushare_daily_basic(symbol: str) -> str:
    return _qp_tushare("daily_basic", symbol,
                       start_date=_days_ago(1825), end_date=_today(),
                       fields="trade_date,pe,pe_ttm,pb,ps,ps_ttm,dv_ratio,total_mv")


def collect_valuation(symbol: str) -> dict:
    """估值分析。并行：Tushare daily_basic（历史序列） + 腾讯快照。

    有 Tushare Token: 获取 5 年历史序列 + 分位
    无 Tushare Token: 仅腾讯当前 PE 快照，标注"历史分位不可得"
    """
    tasks: list[tuple[str, Callable]] = []
    if env.is_tushare_available(env.get_config()):
        tasks.append(("tushare.daily_basic", lambda: _q_tushare_daily_basic(symbol)))
    tasks.append(("tencent_finance", lambda: _q_tencent_valuation_snapshot(symbol)))

    results = _run_sources_parallel(tasks, "valuation")
    result_map = {r.source: r for r in results}

    qp_map: dict[str, str] = {
        "tencent_finance": _qp_tencent(symbol),
    }
    if "tushare.daily_basic" in result_map:
        qp_map["tushare.daily_basic"] = _qp_tushare_daily_basic(symbol)
    _annotate_query_params(result_map, qp_map)

    dim = DimensionResult("valuation", results)
    return dim.to_legacy_dict()


# ---- 机构研报与盈利预测采集 ----

def _q_tushare_report_rc(symbol: str) -> list[dict] | None:
    """Tushare report_rc：机构研报盈利预测（含评级/目标价）。

    权限：特色大数据，需 10000+积分 或单独购买券商研报库（500元/年）。
    120 积分可试用（日 10 次）。
    字段：rating, max_price, min_price, eps, pe, np, org_name, report_date

    Returns:
        list[dict] | None — 研报记录列表，权限不足或失败返回 None
    """
    from . import env as _env
    config = _env.get_config()
    if not _env.is_tushare_available(config):
        raise RuntimeError("TUSHARE_TOKEN not configured")
    tc = _tushare_client(config)
    ts = _ts_code(symbol)
    try:
        df = tc.query("report_rc", ts_code=ts,
                      start_date=_days_ago(180), end_date=_today(),
                      fields="ts_code,name,report_date,report_title,"
                             "org_name,rating,max_price,min_price,"
                             "eps,pe,np,op_rt,roe,classify,quarter")
        if df is not None and not df.empty:
            out = df.to_dict("records")
            logger.info("report_rc: %d records for %s", len(out), ts)
            return out
        return None
    except Exception as exc:
        err = str(exc)
        if "权限" in err or "40203" in err or "无权限" in err:
            logger.info("report_rc 权限不足（需 10000+积分），降级: %s", err)
            return None
        logger.warning("report_rc query failed for %s: %s", ts, err)
        return None


def _q_tushare_forecast(symbol: str) -> list[dict] | None:
    """Tushare forecast：业绩预告（上市公司自行披露的盈利预测）。

    权限：2000 积分可用。
    字段：end_date, type, p_change_min, p_change_max, profit_min, profit_max

    Returns:
        list[dict] | None — 业绩预告记录，权限不足或无数据返回 None
    """
    from . import env as _env
    config = _env.get_config()
    if not _env.is_tushare_available(config):
        raise RuntimeError("TUSHARE_TOKEN not configured")
    tc = _tushare_client(config)
    ts = _ts_code(symbol)
    try:
        df = tc.query("forecast", ts_code=ts,
                      start_date=_days_ago(365), end_date=_today(),
                      fields="ts_code,end_date,type,p_change_min,p_change_max,"
                             "profit_min,profit_max,last_parent_net")
        if df is not None and not df.empty:
            out = df.to_dict("records")
            logger.info("forecast: %d records for %s", len(out), ts)
            return out
        return None
    except Exception as exc:
        err = str(exc)
        if "权限" in err or "40203" in err or "无权限" in err:
            logger.info("forecast 权限不足（需 2000+积分），降级: %s", err)
            return None
        logger.warning("forecast query failed for %s: %s", ts, err)
        return None


def _q_akshare_research(symbol: str) -> list[dict] | None:
    """akshare：东方财富个股研报（免注册，但依赖东方财富接口）。

    注意：当前代理环境可能阻断东方财富 push2 接口。
    当 akshare_push2_available() 为 False 时跳过。
    """
    if not env.is_akshare_available():
        return None
    if not akshare_push2_available():
        logger.info("akshare_research: 东方财富 push2 不可达，跳过")
        return None
    try:
        import akshare as ak
        # stock_research_report_em 接口仅支持 symbol 参数
        df = ak.stock_research_report_em(symbol=symbol)
        if df is not None and not df.empty:
            out = df.to_dict("records")
            logger.info("akshare_research: %d records for %s", len(out), symbol)
            return out
        return None
    except Exception as exc:
        logger.warning("akshare_research failed for %s: %s", symbol, exc)
        return None


def _aggregate_sellside_price_range(
    prices: list[tuple[Any, Any]],
) -> dict[str, float] | None:
    """聚合卖方预期价位；单侧仅有 max 或 min 时也输出区间。"""
    valid_max: list[float] = []
    valid_min: list[float] = []
    for mx, mn in prices:
        fm = safe_float(mx)
        fn = safe_float(mn)
        if fm is not None:
            valid_max.append(fm)
        if fn is not None:
            valid_min.append(fn)
    if not valid_max and not valid_min:
        return None
    low = min(valid_min) if valid_min else min(valid_max)
    high = max(valid_max) if valid_max else max(valid_min)
    out: dict[str, float] = {
        "min": round(low, 2),
        "max": round(high, 2),
    }
    if out["min"] > out["max"]:
        out["min"], out["max"] = out["max"], out["min"]
    if valid_max:
        out["avg_upper"] = round(sum(valid_max) / len(valid_max), 2)
    if valid_min:
        out["avg_lower"] = round(sum(valid_min) / len(valid_min), 2)
    return out


def _summarize_research(tushare_rc: list[dict] | None,
                        tushare_fc: list[dict] | None,
                        akshare_rc: list[dict] | None) -> dict:
    """将多源研报数据汇总为统一结构。

    优先使用 Tushare report_rc（含评级和目标价），
    其次使用 Tushare forecast（仅业绩预告），
    最后使用 akshare。
    """
    summary = {
        "latest_ratings": [],       # 最新卖方评级
        "target_price_range": None, # {min, max} 目标价区间
        "eps_forecasts": [],        # EPS预测列表
        "profit_forecasts": [],     # 净利润预测
        "company_guidance": None,   # 公司自身业绩预告
        "source": None,
        "status": "no_data",
        "summary_text": "",
    }

    # Tier 1: Tushare report_rc (含评级+目标价)
    if tushare_rc:
        # 取最新（按 report_date 排序）
        latest = sorted(tushare_rc, key=lambda r: r.get("report_date", ""), reverse=True)
        # 提取评级
        ratings = [
            {"org": r.get("org_name"), "rating": r.get("rating"),
             "report_date": r.get("report_date")}
            for r in latest if r.get("rating")
        ]
        summary["latest_ratings"] = ratings[:10]  # 取前10条

        # 提取卖方预期价位
        prices = [
            (r.get("max_price"), r.get("min_price"))
            for r in latest
            if r.get("max_price") is not None or r.get("min_price") is not None
        ]
        if prices:
            summary["target_price_range"] = _aggregate_sellside_price_range(prices)

        # 提取 EPS 预测（按报告期聚合）
        eps_by_quarter = {}
        for r in latest:
            q = r.get("quarter") or r.get("report_type", "") or "unknown"
            eps = r.get("eps")
            if eps is not None:
                if q not in eps_by_quarter:
                    eps_by_quarter[q] = []
                eps_by_quarter[q].append(eps)
        summary["eps_forecasts"] = [
            {"quarter": q, "avg_eps": round(sum(vs) / len(vs), 4),
             "n_analysts": len(vs)}
            for q, vs in sorted(eps_by_quarter.items())
        ]

        # 提取净利润预测（NP 字段，万元→亿元）
        np_by_quarter = {}
        for r in latest:
            q = r.get("quarter") or "unknown"
            np_val = r.get("np")
            if np_val is not None:
                if q not in np_by_quarter:
                    np_by_quarter[q] = []
                np_by_quarter[q].append(np_val)
        summary["profit_forecasts"] = [
            {"quarter": q, "avg_np_100m": round(sum(vs) / len(vs) / 10000, 2),
             "n_analysts": len(vs)}
            for q, vs in sorted(np_by_quarter.items())
        ]

        summary["source"] = "tushare.report_rc"
        summary["status"] = "ok"

        # 生成摘要文本（LAW 6：禁「买入」「目标价」字面）
        n_ratings = len(ratings)
        bullish = sum(
            1 for r in ratings
            if "买" in str(r.get("rating", "")) and "卖" not in str(r.get("rating", ""))
        )
        if summary["target_price_range"]:
            tp = summary["target_price_range"]
            summary["summary_text"] = (
                f"近半年 {n_ratings} 条机构评级（{bullish} 条偏多），"
                f"卖方预期价位 {tp['min']}–{tp['max']} 元"
            )
        else:
            summary["summary_text"] = (
                f"近半年 {n_ratings} 条机构评级（{bullish} 条偏多），无公开价位预期"
            )
        return summary

    # Tier 2: Tushare forecast (业绩预告)
    if tushare_fc:
        latest_fc = sorted(tushare_fc, key=lambda r: r.get("end_date", ""), reverse=True)
        if latest_fc:
            rec = latest_fc[0]
            p_min = safe_float(rec.get("p_change_min"))
            p_max = safe_float(rec.get("p_change_max"))
            last_net = safe_float(rec.get("last_parent_net"))  # 上年同期归母净利（万元）
            profit_min = safe_float(rec.get("profit_min"))
            profit_max = safe_float(rec.get("profit_max"))
            guidance = {
                "end_date": rec.get("end_date"),
                "type": rec.get("type"),
                "pct_change_min": p_min,
                "pct_change_max": p_max,
            }
            if profit_min is not None and profit_max is not None:
                guidance["profit_min_100m"] = round(profit_min / 10000, 2)
                guidance["profit_max_100m"] = round(profit_max / 10000, 2)
            elif last_net is not None and p_min is not None and p_max is not None:
                guidance["profit_min_100m"] = round(last_net * (1 + p_min / 100) / 10000, 2)
                guidance["profit_max_100m"] = round(last_net * (1 + p_max / 100) / 10000, 2)
                guidance["last_parent_net_100m"] = round(last_net / 10000, 2)
            summary["company_guidance"] = guidance
            summary["source"] = "tushare.forecast"
            summary["status"] = "ok_guidance_only"
            if guidance.get("profit_min_100m") is not None:
                summary["summary_text"] = (
                    f"公司业绩预告（{rec.get('type', '')}）：净利润 "
                    f"{guidance['profit_min_100m']}–{guidance['profit_max_100m']} 亿元 "
                    f"（同比 {p_min}%–{p_max}%）"
                )
            elif p_min is not None and p_max is not None:
                summary["summary_text"] = (
                    f"公司业绩预告（{rec.get('type', '')}）：同比 {p_min}%–{p_max}%"
                )
            elif p_min is not None:
                summary["summary_text"] = (
                    f"公司业绩预告（{rec.get('type', '')}）：同比变动约 {p_min}% 起"
                )
            else:
                summary["summary_text"] = (
                    f"公司业绩预告（{rec.get('type', '')}）：变动区间数据不足"
                )
        return summary

    # Tier 3: akshare
    if akshare_rc:
        summary["source"] = "akshare.research"
        summary["status"] = "ok_limited"
        n_records = len(akshare_rc)
        summary["summary_text"] = f"东方财富 {n_records} 条研报记录（无结构化评级摘要）"
        summary["raw_records"] = akshare_rc[:10]
        return summary

    # 全部失败
    summary["status"] = "no_data"
    summary["summary_text"] = "未获取到机构研报/评级数据"
    return summary


def collect_research(symbol: str) -> dict:
    """采集机构研报、评级与盈利预测数据。

    三层降级策略（按 Tushare 积分体系）：
      1️⃣ Tushare report_rc（10000+积分/特色大数据）→ 含评级+目标价+盈利预测
      2️⃣ Tushare forecast（2000+积分）→ 仅公司业绩预告
      3️⃣ akshare 东方财富个股研报（免注册，可能被代理阻断）
      4️⃣ 全部失败 → 标注不可得

    Returns:
        dict: 标准 DimensionResult 格式
    """
    results: list[SourceResult] = []
    dim_val = "research"
    ts = _ts_code(symbol)

    # Tier 1 → 2 → 3 顺序降级；高阶成功则跳过低阶 API 调用
    rc_data: list[dict] | None = None
    try:
        rc_data = _q_tushare_report_rc(symbol)
    except RuntimeError:
        pass
    except Exception as exc:
        logger.warning("collect_research/report_rc: %s", exc)
    if rc_data:
        results.append(SourceResult(
            source="tushare.report_rc",
            data=rc_data,
            dimension=dim_val,
            query_params=f"pro.report_rc(ts_code='{ts}', start_date='{_days_ago(180)}')",
        ))
    else:
        results.append(SourceResult(
            source="tushare.report_rc",
            data=None,
            dimension=dim_val,
            error="权限不足或无数据返回（需 Tushare 10000+积分）",
            query_params=f"pro.report_rc(ts_code='{ts}')",
        ))

    fc_data: list[dict] | None = None
    if not rc_data:
        try:
            fc_data = _q_tushare_forecast(symbol)
        except RuntimeError:
            pass
        except Exception as exc:
            logger.warning("collect_research/forecast: %s", exc)
        if fc_data:
            results.append(SourceResult(
                source="tushare.forecast",
                data=fc_data,
                dimension=dim_val,
                query_params=f"pro.forecast(ts_code='{ts}')",
            ))
        else:
            results.append(SourceResult(
                source="tushare.forecast",
                data=None,
                dimension=dim_val,
                error="权限不足或无数据（需 Tushare 2000+积分）",
                query_params=f"pro.forecast(ts_code='{ts}')",
            ))

    ak_data: list[dict] | None = None
    if not rc_data and not fc_data:
        try:
            ak_data = _q_akshare_research(symbol)
        except Exception as exc:
            logger.warning("collect_research/akshare: %s", exc)
        if ak_data:
            results.append(SourceResult(
                source="akshare.research",
                data=ak_data,
                dimension=dim_val,
                query_params=f"ak.stock_research_report_em(symbol='{symbol}')",
            ))
        else:
            results.append(SourceResult(
                source="akshare.research",
                data=None,
                dimension=dim_val,
                error="东方财富 push2 不可达或接口异常",
                query_params=f"ak.stock_research_report_em(symbol='{symbol}')",
            ))

    # 汇总
    tushare_rc = next((r.data for r in results if r.source == "tushare.report_rc" and r.success), None)
    tushare_fc = next((r.data for r in results if r.source == "tushare.forecast" and r.success), None)
    akshare_rc = next((r.data for r in results if r.source == "akshare.research" and r.success), None)
    summary = _summarize_research(tushare_rc, tushare_fc, akshare_rc)

    dim = DimensionResult("research", results)
    dim_dict = dim.to_legacy_dict()
    dim_dict["research_summary"] = summary
    return dim_dict


def collect_industry(symbol: str) -> dict:
    """行业级数据采集：行业指数、行业PE、行业资金流向。

    依赖 akshare 东方财富接口。akshare 不可用时返回 missing。
    """
    dim_val = "industry"
    tasks: list[tuple[str, Callable]] = []

    industry_name = ""
    if env.is_akshare_available() and akshare_push2_available():
        info = _q_akshare_basic(symbol)
        if info:
            industry_name = info.get("行业") or info.get("industry", "") or ""

    # 行业板块历史行情（个股所属行业指数）
    if env.is_akshare_available() and akshare_push2_available():
        ind = industry_name
        tasks.append(("akshare.stock_board_industry_hist_em",
                      lambda i=ind: _q_akshare_industry_board(symbol, industry_name=i)))
        tasks.append(("akshare.stock_board_industry_pe_ratio_cninfo",
                      lambda i=ind: _q_akshare_industry_pe(symbol, industry_name=i)))

    if not tasks:
        return {
            "dimension": dim_val,
            "display": "行业数据",
            "data": None,
            "status": "missing",
            "error": "无可用行业数据源（需 akshare + 东方财富 push2 可用）",
            "_meta": {"source": "none", "success": False,
                      "all_sources": [], "multi_source": False,
                      "source_count": 0},
        }

    results = _run_sources_parallel(tasks, dim_val)
    dim = DimensionResult(dim_val, results)
    dim_dict = dim.to_legacy_dict()
    merged: dict = {}
    sources_ok: list[str] = []
    for r in results:
        if r.data and isinstance(r.data, dict):
            merged.update(r.data)
            sources_ok.append(r.source)
    if merged:
        dim_dict["data"] = merged
        if len(sources_ok) > 1:
            meta = dim_dict.setdefault("_meta", {})
            meta["source"] = "merged:" + "+".join(sources_ok)
            meta["merged_sources"] = sources_ok
            meta["multi_source"] = True
    return dim_dict


# ---- 全维度采集 ----

COLLECTORS = {
    "basic_info": ("基本信息", collect_basic_info),
    "financials": ("财务报告", collect_financials),
    "quote": ("实时行情", collect_quote),
    "shareholders": ("十大股东", collect_shareholders),
    "northbound": ("北向资金", collect_northbound),
    "kline": ("日K线", collect_kline),
    "valuation": ("估值分析", collect_valuation),
    "research": ("机构研报", collect_research),
    "industry": ("行业数据", collect_industry),  # R-11: NEW
}

_DEFAULT_DIMS = ["basic_info", "financials", "quote", "shareholders",
                 "northbound", "valuation", "kline"]


def collect_all(symbol: str, dims: list[str] | None = None,
                deep: bool = False,
                with_macro: bool = False,
                with_chain: bool = False) -> dict[str, Any]:
    """全维度采集。

    last30days 模式扩展：维度之间也并行执行（跨维度 fan-out）。
    每个维度内部已在 collect_* 中并行查源。

    Args:
        symbol: 股票代码
        dims: 维度列表，None 使用默认（含 valuation + kline）
        deep: 深度模式，kline 扩大到 730 自然日
        with_macro: 采集中国宏观指标（PMI/CPI/PPI/LPR）
        with_chain: 采集产业链上下文（复用已采集的 basic_info）
    """
    if dims is None:
        dims = list(_DEFAULT_DIMS)

    dim_results: dict[str, dict] = {}

    # 深度模式：kline 用更长窗口
    kline_kwargs = {}
    if deep:
        kline_kwargs["start_date"] = _days_ago(730)

    # 跨维度并行
    with ThreadPoolExecutor(max_workers=min(len(dims), 6)) as executor:
        future_map = {}
        for dim in dims:
            if dim not in COLLECTORS:
                logger.warning("忽略未知维度 '%s'（有效维度: %s）", dim, list(COLLECTORS.keys()))
                continue
            if dim == "kline" and kline_kwargs:
                _, fn = COLLECTORS[dim]
                future_map[executor.submit(fn, symbol, **kline_kwargs)] = dim
            else:
                _, fn = COLLECTORS[dim]
                future_map[executor.submit(fn, symbol)] = dim

        for future in as_completed(future_map):
            dim = future_map[future]
            try:
                dim_results[dim] = future.result()
            except Exception as exc:
                dim_results[dim] = {
                    "dimension": dim,
                    "display": COLLECTORS[dim][0] if dim in COLLECTORS else dim,
                    "data": None,
                    "status": "missing",
                    "error": f"维度采集失败: {exc}",
                    "_meta": {"source": "none", "success": False,
                              "all_sources": [], "multi_source": False,
                              "source_count": 0, "error": str(exc)},
                }

    # 按输入顺序排列
    dimensions = [dim_results.get(d) for d in dims if d in COLLECTORS]

    # R-08: RRF 多源融合
    fusion_results: dict[str, Any] = {}
    try:
        from .fusion import (
            dimension_results_from_legacy,
            fuse_from_legacy_dicts,
            fuse_from_source_results,
            fusion_results_to_dict,
        )
        dim_result_map = dimension_results_from_legacy(dimensions)
        if dim_result_map:
            fusion_raw = fuse_from_source_results(dim_result_map)
        else:
            fusion_raw = fuse_from_legacy_dicts(dimensions)
        fusion_results = fusion_results_to_dict(fusion_raw)
        if fusion_results:
            logger.info(
                "fusion: %d dimensions fused for %s",
                len(fusion_results), symbol,
            )
    except Exception as exc:
        logger.warning("fusion failed for %s: %s", symbol, exc)

    # R-09: 证据可信度评分
    credibility_scores: dict[str, float] = {}
    try:
        from .rerank import score_all_dimensions
        credibility_scores = score_all_dimensions(dimensions)
    except Exception as exc:
        logger.warning("rerank scoring failed for %s: %s", symbol, exc)

    # R-12: 宏观数据采集（层5，opt-in）
    macro_context: dict[str, Any] = {}
    if with_macro:
        try:
            from .macro import collect_macro_context
            macro_context = collect_macro_context(symbol)
        except Exception as exc:
            logger.warning("macro context collection failed for %s: %s", symbol, exc)
            macro_context = {"status": "error", "error": str(exc)}

    # R-12: 产业链数据（层3+4，opt-in）
    chain_context: dict[str, Any] = {}
    if with_chain:
        try:
            from .chain import collect_chain_context
            basic_dim = dim_results.get("basic_info") or {}
            basic_data = basic_dim.get("data") if isinstance(basic_dim, dict) else None
            industry = ""
            if isinstance(basic_data, dict):
                industry = basic_data.get("industry", "") or basic_data.get("行业", "")
            chain_context = collect_chain_context(
                symbol, industry=industry, basic_data=basic_data,
            )
        except Exception as exc:
            logger.warning("chain context collection failed for %s: %s", symbol, exc)
            chain_context = {"status": "error", "error": str(exc)}

    has_data = sum(
        1 for d in dimensions
        if d and d.get("data") is not None and d.get("status") in ("available", "partial")
    )
    partial = sum(1 for d in dimensions if d and d.get("status") == "partial")
    missing = sum(1 for d in dimensions if d and (d.get("data") is None and d.get("status") != "partial"))

    result: dict[str, Any] = {
        "symbol": symbol,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "dimensions": dimensions or [],
        "fusion": fusion_results,  # R-08: RRF 多源融合
        "credibility": credibility_scores,  # R-09: 证据可信度评分
        "macro_context": macro_context,  # R-12
        "chain_context": chain_context,  # R-12
        "summary": {
            "total": len(dimensions),
            "available": has_data,
            "degraded": partial,
            "missing": missing,
            "all_partial": (
                has_data > 0 and partial == has_data and missing == 0
            ),
        },
    }
    try:
        attach_phase2_extras(result, symbol)
    except Exception as exc:
        logger.warning("attach_phase2_extras failed for %s: %s", symbol, exc)
        result.setdefault("phase2_extras_errors", []).append(str(exc))
        if not result.get("industry_peers"):
            result["industry_peers"] = {
                "peers": [],
                "target": None,
                "rankings": {},
                "industry_name": None,
                "sufficient": False,
                "error": f"Phase 2 同行采集异常: {exc}",
            }
    return result


# ---- 市场结构采集（v0.1.3 Phase 1） ----

_HS300_CODE = "000300.SH"
_ERP_MIN_ALIGNED_DAYS = 60
_ERP_DGS10_LOOKBACK_DAYS = 5


def _ms_set_unavailable(availability: dict[str, str], key: str, reason: str) -> None:
    availability[key] = f"unavailable: {reason}"


def _ms_lookup_sw_index_code_at_level(
    tc: Any, industry: str, level: str,
) -> str | None:
    """在指定申万层级（L1/L2）中按行业名精确匹配申万指数代码。"""
    df = tc.query("index_classify", level=level, src="SW2021")
    if df is None or df.empty:
        return None
    name = industry.strip()
    for _, row in df.iterrows():
        idx_name = str(row.get("industry_name") or row.get("name") or "").strip()
        if not idx_name:
            continue
        code = str(row.get("index_code", "")).strip()
        if name == idx_name and code:
            return code
    return None


def _ms_lookup_sw_index_code(tc: Any, industry: str | None) -> str | None:
    """按申万行业名称匹配指数代码（L3 → L2 → L1）。"""
    if not industry:
        return None
    for level in ("L3", "L2", "L1"):
        code = _ms_lookup_sw_index_code_at_level(tc, industry, level)
        if code:
            return code
    return None


def _resolve_sw_industry_name(
    tc: Any, symbol: str, industry_hint: str | None = None,
) -> str | None:
    """解析申万行业名：优先 collection 提示，再 stock_basic，再申万分类模糊匹配。"""
    candidates: list[str] = []
    if industry_hint and industry_hint.strip():
        candidates.append(industry_hint.strip())
    basic_df = tc.query("stock_basic", ts_code=_ts_code(symbol),
                        fields="ts_code,name,industry")
    if basic_df is not None and not basic_df.empty:
        bi = str(basic_df.iloc[0].get("industry", "")).strip()
        if bi and bi not in candidates:
            candidates.append(bi)
    for name in candidates:
        if _ms_lookup_sw_index_code(tc, name):
            return name
    for name in candidates:
        for level in ("L3", "L2", "L1"):
            df = tc.query("index_classify", level=level, src="SW2021")
            if df is None or df.empty:
                continue
            for _, row in df.iterrows():
                idx_name = str(row.get("industry_name") or row.get("name") or "").strip()
                if idx_name and (name in idx_name or idx_name in name):
                    return idx_name
    return candidates[0] if candidates else None


def _ms_fetch_pmi() -> dict[str, Any] | None:
    """中国制造业 PMI（akshare），供 A-① 行业景气度补充。"""
    if not env.is_akshare_available():
        return None
    try:
        with akshare_direct_session():
            import akshare as ak
            df = ak.macro_china_pmi()
        if df is None or df.empty:
            return None
        row = df.iloc[-1]
        raw_month = row.get("月份")
        if raw_month is not None:
            month = str(raw_month)
        elif hasattr(row, "name") and row.name is not None:
            month = str(row.name)
        else:
            month = ""
        pmi = safe_float(row.get("制造业-指数", row.get("制造业", None)))
        if pmi is None and len(row) > 1:
            pmi = safe_float(row.iloc[-1])
        if pmi is None:
            return None
        return {
            "month": month,
            "manufacturing_pmi": round(pmi, 2),
            "signal": "扩张" if pmi >= 50 else "收缩",
            "source": "akshare.macro_china_pmi",
        }
    except Exception as exc:
        logger.debug("PMI fetch failed: %s", exc)
        return None


def _ms_return_pct(closes: list[float]) -> float | None:
    if len(closes) < 2:
        return None
    start, end = closes[0], closes[-1]
    if not start:
        return None
    return (end - start) / start * 100


def _ms_sw_numeric_code(index_code: str) -> str:
    """851024.SI → 851024（akshare index_hist_sw 格式）。"""
    return index_code.split(".")[0]


def _ms_lookup_akshare_sw_code(industry: str) -> str | None:
    """按申万行业名在 akshare 行业列表中匹配指数代码（L3→L2→L1）。"""
    if not env.is_akshare_available():
        return None
    name = industry.strip()
    if not name:
        return None
    try:
        import akshare as ak
        loaders = (ak.sw_index_third_info, ak.sw_index_second_info, ak.sw_index_first_info)
        with akshare_direct_session():
            for loader in loaders:
                df = loader()
                if df is None or df.empty:
                    continue
                for _, row in df.iterrows():
                    idx_name = str(row.get("行业名称", "")).strip()
                    code = str(row.get("行业代码", "")).strip()
                    if name == idx_name and code:
                        return code
        with akshare_direct_session():
            for loader in loaders:
                df = loader()
                if df is None or df.empty:
                    continue
                for _, row in df.iterrows():
                    idx_name = str(row.get("行业名称", "")).strip()
                    code = str(row.get("行业代码", "")).strip()
                    if idx_name and code and (name in idx_name or idx_name in name):
                        return code
    except Exception as exc:
        logger.debug("akshare sw index lookup failed: %s", exc)
    return None


def _akshare_closes_from_hist_sw(index_code: str, *, days: int = 70) -> list[float]:
    """akshare 申万指数日线收盘价序列（升序）。"""
    import akshare as ak

    sym = _ms_sw_numeric_code(index_code)
    with akshare_direct_session():
        df = ak.index_hist_sw(symbol=sym, period="day")
    if df is None or df.empty:
        return []
    col = "收盘" if "收盘" in df.columns else "close"
    tail = df.sort_values("日期" if "日期" in df.columns else "trade_date").tail(days + 5)
    return [float(v) for v in tail[col].tolist() if v is not None]


def _akshare_hs300_closes(*, days: int = 70) -> list[float]:
    """沪深300 日线收盘价（akshare / 东方财富）。"""
    import akshare as ak

    sd = _days_ago(days + 10)
    ed = _today()
    sd_fmt = f"{sd[:4]}-{sd[4:6]}-{sd[6:]}"
    ed_fmt = f"{ed[:4]}-{ed[4:6]}-{ed[6:]}"
    with akshare_direct_session():
        df = ak.stock_zh_index_daily_em(
            symbol="sh000300", start_date=sd_fmt, end_date=ed_fmt,
        )
    if df is None or df.empty:
        return []
    col = "收盘" if "收盘" in df.columns else "close"
    date_col = "日期" if "日期" in df.columns else "date"
    sorted_df = df.sort_values(date_col)
    return [float(v) for v in sorted_df[col].tolist() if v is not None]


def _ms_build_sw_index_result(
    *,
    index_code: str,
    industry: str | None,
    sw_closes: list[float],
    bench_closes: list[float] | None,
    stock_closes: list[float] | None,
    source: str,
) -> dict | None:
    if len(sw_closes) < 2:
        return None
    ret_20 = _ms_return_pct(sw_closes[-21:]) if len(sw_closes) >= 2 else None
    bench_ret = (
        _ms_return_pct(bench_closes[-21:])
        if bench_closes and len(bench_closes) >= 2 else None
    )
    stock_ret = (
        _ms_return_pct(stock_closes[-21:])
        if stock_closes and len(stock_closes) >= 2 else None
    )
    rel_vs_bench = (ret_20 - bench_ret) if ret_20 is not None and bench_ret is not None else None
    rel_stock_vs_ind = (
        (stock_ret - ret_20) if stock_ret is not None and ret_20 is not None else None
    )
    return {
        "index_code": index_code,
        "industry": industry,
        "return_20d_pct": round(ret_20, 2) if ret_20 is not None else None,
        "benchmark_return_20d_pct": round(bench_ret, 2) if bench_ret is not None else None,
        "stock_return_20d_pct": round(stock_ret, 2) if stock_ret is not None else None,
        "relative_vs_benchmark_pct": round(rel_vs_bench, 2) if rel_vs_bench is not None else None,
        "stock_vs_industry_pct": round(rel_stock_vs_ind, 2) if rel_stock_vs_ind is not None else None,
        "source": source,
    }


def _ms_fetch_sw_index_akshare(
    symbol: str,
    industry: str | None,
    index_code: str | None = None,
    tc: Any | None = None,
) -> dict | None:
    """Tushare sw_daily 不可用时的 akshare 申万指数回退。"""
    if not env.is_akshare_available():
        return None
    code: str | None = None
    if industry:
        code = _ms_lookup_akshare_sw_code(industry)
    if not code and index_code:
        code = _ms_sw_numeric_code(index_code)
    if not code:
        return None
    try:
        sw_closes = _akshare_closes_from_hist_sw(code)
    except Exception as exc:
        logger.debug("akshare index_hist_sw failed: %s", exc)
        return None
    if len(sw_closes) < 2:
        return None

    bench_closes: list[float] | None = None
    if tc is not None:
        df_hs = tc.query("index_daily", ts_code=_HS300_CODE,
                         start_date=_days_ago(70), end_date=_today())
        if df_hs is not None and not df_hs.empty:
            hs = df_hs.sort_values("trade_date")
            bench_closes = [float(v) for v in hs["close"].tolist() if v is not None]
    if not bench_closes:
        try:
            bench_closes = _akshare_hs300_closes()
        except Exception as exc:
            logger.debug("akshare HS300 for sw_index failed: %s", exc)
            bench_closes = None

    stock_closes: list[float] | None = None
    if tc is not None:
        df_stk = tc.query("daily", ts_code=_ts_code(symbol),
                          start_date=_days_ago(70), end_date=_today(),
                          fields="trade_date,close")
        if df_stk is not None and not df_stk.empty:
            stk = df_stk.sort_values("trade_date")
            stock_closes = [float(v) for v in stk["close"].tolist() if v is not None]
    if not stock_closes:
        try:
            rows = _q_akshare_kline(symbol, start_date=_days_ago(70), end_date=_today())
            if rows:
                ordered = sorted(rows, key=lambda r: str(r.get("trade_date", "")))
                stock_closes = [
                    float(r["close"]) for r in ordered
                    if r.get("close") is not None
                ]
        except Exception as exc:
            logger.debug("akshare stock kline for sw_index failed: %s", exc)

    return _ms_build_sw_index_result(
        index_code=code,
        industry=industry,
        sw_closes=sw_closes,
        bench_closes=bench_closes,
        stock_closes=stock_closes,
        source="akshare.index_hist_sw",
    )


def _ms_sw_index_availability_label(value: dict) -> str:
    """sw_index 可用性标注（区分 Tushare 原生 vs akshare 回退）。"""
    if value.get("source") == "akshare.index_hist_sw":
        return (
            "available (akshare fallback; Tushare sw_daily 需 5000 积分，见 "
            "https://tushare.pro/document/2?doc_id=327)"
        )
    return "available"


def _ms_fetch_sw_index(tc: Any, symbol: str, industry: str | None) -> dict | None:
    resolved = industry or _resolve_sw_industry_name(tc, symbol, industry)
    index_code = _ms_lookup_sw_index_code(tc, resolved) if resolved else None
    if index_code:
        df_sw = tc.query("sw_daily", ts_code=index_code,
                         start_date=_days_ago(70), end_date=_today())
        if df_sw is not None and not df_sw.empty:
            sw = df_sw.sort_values("trade_date")
            sw_closes = [float(v) for v in sw["close"].tolist() if v is not None]
            df_hs = tc.query("index_daily", ts_code=_HS300_CODE,
                             start_date=_days_ago(70), end_date=_today())
            bench_closes: list[float] | None = None
            if df_hs is not None and not df_hs.empty:
                hs = df_hs.sort_values("trade_date")
                bench_closes = [float(v) for v in hs["close"].tolist() if v is not None]
            stock_closes: list[float] | None = None
            df_stk = tc.query("daily", ts_code=_ts_code(symbol),
                              start_date=_days_ago(70), end_date=_today(),
                              fields="trade_date,close")
            if df_stk is not None and not df_stk.empty:
                stk = df_stk.sort_values("trade_date")
                stock_closes = [float(v) for v in stk["close"].tolist() if v is not None]
            built = _ms_build_sw_index_result(
                index_code=index_code,
                industry=resolved,
                sw_closes=sw_closes,
                bench_closes=bench_closes,
                stock_closes=stock_closes,
                source="tushare.sw_daily",
            )
            if built is not None:
                return built
    return _ms_fetch_sw_index_akshare(symbol, resolved, index_code, tc=tc)


def _recent_flow_records(records: list[dict], *, limit: int) -> list[dict]:
    return sorted(
        records, key=lambda r: str(r.get("trade_date", "")), reverse=True,
    )[:limit]


_MIN_NORTHBOUND_DAYS = 5


def _ms_fetch_northbound_stock(tc: Any, symbol: str) -> dict | None:
    """个股北向近 10 个交易日净额（元）。

    Tushare hsgt_top10（仅上榜日有 net_amount）→ akshare 个股持股变动回退。
    hsgt_top10 上榜日过少时回退 akshare，避免稀疏序列误导汇总。
    不使用 moneyflow（主力）或 moneyflow_hsgt（市场级汇总）。
    """
    try:
        records = _q_tushare_hsgt_top10(symbol)
        if records:
            recent = _recent_flow_records(records, limit=10)
            if len(recent) >= _MIN_NORTHBOUND_DAYS:
                net_sum = sum(v for v in (_flow_amount_yuan(r) for r in recent) if v is not None)
                return {
                    "records": recent,
                    "net_sum_10d": net_sum,
                    "days": len(recent),
                    "source": "tushare.hsgt_top10",
                }
    except Exception as exc:
        logger.debug("tushare hsgt_top10 failed for %s: %s", symbol, exc)

    records = _q_akshare_northbound(symbol)
    if not records:
        return None
    recent = _recent_flow_records(records, limit=10)
    net_sum = sum(v for v in (_flow_amount_yuan(r) for r in recent) if v is not None)
    return {
        "records": recent,
        "net_sum_10d": net_sum,
        "days": len(recent),
        "source": "akshare.stock_hsgt_individual_em",
    }


def _ms_fetch_margin(tc: Any, symbol: str) -> dict | None:
    """个股融资余额变化（margin_detail，非交易所汇总 margin）。

    按 LAW 16 分离三类余额变化：
      - change_pct: 融资余额（rzye）增速
      - rqye_change_pct: 融券余额增速
      - rzrqye_change_pct: 融资融券合计余额增速（如有）
    """
    df = tc.query("margin_detail", ts_code=_ts_code(symbol),
                  start_date=_days_ago(15), end_date=_today())
    if df is None or df.empty:
        return None
    records = df.sort_values("trade_date").to_dict("records")
    if len(records) < 2:
        return None

    first, last = records[0], records[-1]

    def _pct_chg(key: str) -> float | None:
        fv = first.get(key)
        lv = last.get(key)
        if fv is None or lv is None:
            return None
        f = float(fv)
        l = float(lv)
        if f == 0:
            return None
        return (l - f) / f * 100

    change_pct = _pct_chg("rzye")
    rqye_change_pct = _pct_chg("rqye")
    rzrqye_change_pct = _pct_chg("rzrqye")

    result: dict[str, Any] = {
        "records": records[-10:],
        "source": "tushare.margin_detail",
    }
    if change_pct is not None:
        result["change_pct"] = round(change_pct, 2)
    if rqye_change_pct is not None:
        result["rqye_change_pct"] = round(rqye_change_pct, 2)
    if rzrqye_change_pct is not None:
        result["rzrqye_change_pct"] = round(rzrqye_change_pct, 2)
    return result if result.get("change_pct") is not None else None


def _ms_fetch_moneyflow(tc: Any, symbol: str) -> dict | None:
    records = _q_tushare_moneyflow(symbol)
    if not records:
        return None
    recent = _recent_flow_records(records, limit=10)
    net_sum = sum(v for v in (_flow_amount_yuan(r) for r in recent[:5]) if v is not None)
    return {
        "records": recent,
        "net_sum_5d": net_sum,
        "source": "tushare.moneyflow",
    }


def _ms_fetch_turnover(tc: Any, symbol: str) -> dict | None:
    from lib.valuation import percentile_rank

    df = tc.query("daily_basic", ts_code=_ts_code(symbol),
                  fields="trade_date,turnover_rate",
                  start_date=_days_ago(90), end_date=_today())
    if df is None or df.empty:
        return None
    rows = df.sort_values("trade_date")
    rates = [float(v) for v in rows["turnover_rate"].tolist()
             if v is not None and float(v) > 0]
    if not rates:
        return None
    avg_5 = sum(rates[-5:]) / min(5, len(rates[-5:]))
    avg_60 = sum(rates[-60:]) / min(60, len(rates[-60:]))
    current = rates[-1]
    pct = percentile_rank(rates[-60:], current) if len(rates) >= 5 else None
    return {
        "avg_5d": round(avg_5, 4),
        "avg_60d": round(avg_60, 4),
        "current": round(current, 4),
        "ratio_5_60": round(avg_5 / avg_60, 3) if avg_60 else None,
        "percentile_60d": round(pct, 1) if pct is not None else None,
        "source": "tushare.daily_basic",
    }


def _akshare_date_to_iso(value: Any) -> str:
    if hasattr(value, "strftime"):
        return value.strftime("%Y-%m-%d")
    s = str(value).strip().replace("/", "-")
    return s[:10] if len(s) >= 10 else s


def _ms_fetch_akshare_cn10y_series() -> list[tuple[str, float]]:
    """中国 10Y 国债收益率日序列（date, yield%）。FRED 不可用时的 ERP 回退。"""
    if not env.is_akshare_available():
        return []
    import akshare as ak
    try:
        with _proxy_bypass():
            df = ak.bond_zh_us_rate(start_date=_days_ago(1825))
    except Exception as exc:
        logger.warning("akshare bond_zh_us_rate failed: %s", exc)
        return []
    if df is None or getattr(df, "empty", True):
        return []
    col = "中国国债收益率10年"
    if col not in df.columns:
        return []
    out: list[tuple[str, float]] = []
    for _, row in df.iterrows():
        dt = _akshare_date_to_iso(row.get("日期"))
        val = row.get(col)
        if not dt or val is None:
            continue
        try:
            fval = float(val)
        except (TypeError, ValueError):
            continue
        if fval != fval:  # NaN
            continue
        out.append((dt, fval))
    out.sort(key=lambda x: x[0])
    return out


def _ms_fetch_y10_series(config: dict) -> tuple[list[tuple[str, float]], str]:
    """10Y 国债收益率序列：FRED DGS10 优先，akshare 中国 10Y 回退。"""
    fred = _ms_fetch_fred_dgs10_series(config)
    if fred:
        return fred, "FRED.DGS10"
    cn = _ms_fetch_akshare_cn10y_series()
    if cn:
        return cn, "akshare.bond_zh_us_rate"
    return [], ""


def _ms_fetch_fred_dgs10_series(config: dict) -> list[tuple[str, float]]:
    """FRED DGS10 日序列（date, yield%）。"""
    if not env.is_fred_available(config):
        return []
    import json
    import urllib.parse
    import urllib.request

    key = config.get("FRED_API_KEY", "")
    params = urllib.parse.urlencode({
        "series_id": "DGS10",
        "api_key": key,
        "file_type": "json",
        "observation_start": _fred_date(_days_ago(1825)),
        "observation_end": _fred_date(_today()),
    })
    url = f"https://api.stlouisfed.org/fred/series/observations?{params}"
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            payload = json.loads(resp.read().decode())
    except Exception as exc:
        logger.warning("FRED DGS10 fetch failed: %s", exc)
        return []
    out: list[tuple[str, float]] = []
    for obs in payload.get("observations", []):
        val = obs.get("value")
        if val is None or val == ".":
            continue
        try:
            out.append((obs.get("date", ""), float(val)))
        except (TypeError, ValueError):
            continue
    return out


def _dgs10_for_trade_date(
    dgs10_by_date: dict[str, float],
    trade_date_fmt: str,
    *,
    lookback_days: int = _ERP_DGS10_LOOKBACK_DAYS,
) -> float | None:
    """取交易日对应 DGS10；若当日无数据则向前最多 lookback_days 个自然日。"""
    try:
        d = datetime.strptime(trade_date_fmt, "%Y-%m-%d")
    except ValueError:
        return dgs10_by_date.get(trade_date_fmt)
    for i in range(lookback_days + 1):
        key = (d - timedelta(days=i)).strftime("%Y-%m-%d")
        if key in dgs10_by_date:
            return dgs10_by_date[key]
    return None


def _ms_fetch_erp(tc: Any, config: dict) -> dict | None:
    from lib.valuation import percentile_rank

    df = tc.query("index_dailybasic", ts_code=_HS300_CODE,
                  fields="trade_date,pe_ttm",
                  start_date=_days_ago(1825), end_date=_today())
    if df is None or df.empty:
        return None
    dgs10_series, y10_source = _ms_fetch_y10_series(config)
    dgs10_by_date = {d: v for d, v in dgs10_series}
    latest_dgs10 = dgs10_series[-1][1] if dgs10_series else None

    rows = df.sort_values("trade_date").to_dict("records")
    erp_hist: list[float] = []
    for r in rows:
        pe = r.get("pe_ttm")
        if pe is None or float(pe) <= 0:
            continue
        td = str(r.get("trade_date", ""))
        d_fmt = f"{td[:4]}-{td[4:6]}-{td[6:8]}" if len(td) == 8 else td
        y10 = _dgs10_for_trade_date(dgs10_by_date, d_fmt)
        if y10 is None:
            continue
        ep = 100.0 / float(pe)
        erp_hist.append(ep - y10)

    if not erp_hist:
        return None
    current = erp_hist[-1]
    pct_5y = percentile_rank(erp_hist, current)
    erp_days = len(erp_hist)
    partial = erp_days < _ERP_MIN_ALIGNED_DAYS
    return {
        "raw": round(current, 3),
        "percentile_5y": round(pct_5y, 1) if pct_5y is not None else None,
        "dgs10": round(latest_dgs10, 3) if latest_dgs10 is not None else None,
        "erp_days": erp_days,
        "partial": partial,
        "index": _HS300_CODE,
        "source": f"tushare.index_dailybasic+{y10_source}" if y10_source else "tushare.index_dailybasic",
    }


_50ETF_UNDERLYING = "510050.SH"
_ETF_300_CODE = "510300.SH"
_NEW_HIGH_SAMPLE = 30
_PCR_HISTORY_5Y_CAL_DAYS = 1825
_PCR_HISTORY_60D = 60
_PCR_MIN_5Y_TRADING_DAYS = 252
# 5 年 PCR 历史分位：均匀降采样上限，避免逐日 opt_daily 风暴
_PCR_MAX_DAILY_QUERIES = 80


def _ms_50etf_option_codes(tc: Any) -> tuple[list[str], list[str]]:
    """SSE 50ETF 期权合约代码（认沽/认购）。"""
    df = tc.query("opt_basic", exchange="SSE", fields="ts_code,call_put,name")
    if df is None or df.empty:
        return [], []
    puts, calls = [], []
    for _, row in df.iterrows():
        name = str(row.get("name") or "")
        if "50ETF" not in name and "510050" not in name:
            continue
        code = str(row.get("ts_code") or "").strip()
        cp = str(row.get("call_put") or "").upper()
        if not code:
            continue
        if cp == "P":
            puts.append(code)
        elif cp == "C":
            calls.append(code)
    return puts, calls


def _ms_subsample_trade_dates(dates: list[str], max_points: int) -> list[str]:
    """均匀降采样交易日列表，始终保留最后一日。"""
    if len(dates) <= max_points:
        return dates
    step = max(1, len(dates) // max_points)
    sampled = list(dates[::step])
    if dates[-1] not in sampled:
        sampled.append(dates[-1])
    return sorted(set(sampled))


def _ms_pcr_on_date(
    tc: Any, trade_date: str, put_codes: set[str], call_codes: set[str],
) -> float | None:
    df = tc.query("opt_daily", trade_date=trade_date, exchange="SSE")
    if df is None or df.empty:
        return None
    put_vol = call_vol = 0.0
    for _, row in df.iterrows():
        code = str(row.get("ts_code") or "")
        try:
            vol = float(row.get("vol") or 0)
        except (TypeError, ValueError):
            continue
        if code in put_codes:
            put_vol += vol
        elif code in call_codes:
            call_vol += vol
    if call_vol <= 0:
        return None
    return put_vol / call_vol


def _ms_fetch_put_call_ratio(tc: Any) -> dict | None:
    """50ETF 认沽认购比（opt_daily，需 5000 积分）。"""
    from lib.valuation import percentile_rank

    puts, calls = _ms_50etf_option_codes(tc)
    if not puts or not calls:
        return None
    put_set, call_set = set(puts), set(calls)
    cal = tc.query(
        "trade_cal", exchange="SSE",
        start_date=_days_ago(_PCR_HISTORY_5Y_CAL_DAYS + 5), end_date=_today(), is_open="1",
    )
    if cal is None or cal.empty:
        return None
    dates = sorted(str(d) for d in cal["cal_date"].tolist())
    raw_days = len(dates)
    dates = _ms_subsample_trade_dates(dates, _PCR_MAX_DAILY_QUERIES)
    ratios: list[float] = []
    for td in dates:
        r = _ms_pcr_on_date(tc, td, put_set, call_set)
        if r is not None:
            ratios.append(r)
    if not ratios:
        return None
    current = ratios[-1]
    pct_5y = percentile_rank(ratios, current) if len(ratios) >= 5 else None
    ratios_60d = ratios[-_PCR_HISTORY_60D:]
    pct_60d = (
        percentile_rank(ratios_60d, current)
        if len(ratios_60d) >= 5 else None
    )
    return {
        "ratio": round(current, 3),
        "percentile_5y": round(pct_5y, 1) if pct_5y is not None else None,
        "percentile_60d": round(pct_60d, 1) if pct_60d is not None else None,
        "history_days": len(ratios),
        "partial": len(ratios) < _PCR_MIN_5Y_TRADING_DAYS or raw_days > len(dates),
        "sampled": raw_days > len(dates),
        "sample_points": len(dates),
        "calendar_days": raw_days,
        "underlying": _50ETF_UNDERLYING,
        "source": "tushare.opt_daily",
    }


def _ms_fetch_short_margin_growth(tc: Any, symbol: str) -> dict | None:
    """融券余额增速（交易所 margin 优先，个股 margin_detail 回退）。"""
    from lib.valuation import percentile_rank

    df = tc.query("margin", start_date=_days_ago(1825), end_date=_today())
    if df is not None and not df.empty:
        by_date: dict[str, float] = {}
        for _, row in df.iterrows():
            td = str(row.get("trade_date") or "")
            rqye = row.get("rqye")
            if not td or rqye is None:
                continue
            by_date[td] = by_date.get(td, 0.0) + float(rqye)
        dates = sorted(by_date)
        if len(dates) >= 11:
            growths: list[float] = []
            for i in range(10, len(dates)):
                base, cur = by_date[dates[i - 10]], by_date[dates[i]]
                if base > 0:
                    growths.append((cur - base) / base * 100)
            if growths:
                current_g = growths[-1]
                pct = percentile_rank(growths, current_g) if len(growths) >= 5 else None
                return {
                    "growth_pct": round(current_g, 2),
                    "percentile_5y": round(pct, 1) if pct is not None else None,
                    "scope": "exchange",
                    "source": "tushare.margin",
                }
    margin = _ms_fetch_margin(tc, symbol)
    if margin and margin.get("rqye_change_pct") is not None:
        return {
            "growth_pct": margin["rqye_change_pct"],
            "scope": "stock",
            "source": margin.get("source", "tushare.margin_detail"),
        }
    return None


def _ms_new_high_ratio_from_panel(panel: dict[str, list[dict]]) -> float | None:
    if not panel:
        return None
    n_high = n_valid = 0
    for rows in panel.values():
        if len(rows) < 2:
            continue
        closes = [safe_float(r.get("close")) for r in rows]
        highs = [safe_float(r.get("high")) for r in rows]
        closes = [c for c in closes if c is not None]
        highs = [h for h in highs if h is not None]
        if not closes or len(highs) < 2:
            continue
        n_valid += 1
        if closes[-1] >= max(highs[:-1]):
            n_high += 1
    if n_valid == 0:
        return None
    return n_high / n_valid * 100


def _ms_fetch_new_high_ratio(tc: Any) -> dict | None:
    """创新高个股占比（采样 daily，partial 标注）。"""
    from lib.valuation import percentile_rank

    basic = tc.query("stock_basic", list_status="L", fields="ts_code")
    if basic is None or basic.empty:
        return None
    codes = [
        str(c) for c in basic["ts_code"].tolist()
        if c is not None
    ][:_NEW_HIGH_SAMPLE]
    if not codes:
        return None
    def _fetch_daily_panel_row(ts_code: str) -> tuple[str, list[dict] | None]:
        df = tc.query(
            "daily", ts_code=ts_code,
            start_date=_days_ago(70), end_date=_today(),
            fields="trade_date,close,high",
        )
        if df is None or df.empty:
            return ts_code, None
        return ts_code, df.sort_values("trade_date").to_dict("records")

    panel: dict[str, list[dict]] = {}
    with ThreadPoolExecutor(max_workers=min(len(codes), 8)) as executor:
        futures = {executor.submit(_fetch_daily_panel_row, c): c for c in codes}
        for fut in as_completed(futures):
            ts_code, records = fut.result()
            if records:
                panel[ts_code] = records
    current = _ms_new_high_ratio_from_panel(panel)
    if current is None:
        return None
    hist: list[float] = []
    if panel:
        min_len = min(len(v) for v in panel.values())
        # 排除全样本切片，避免 current 计入自身分位
        hist_end = min_len - 1 if min_len > 1 else 0
        for offset in range(max(0, min_len - 60), hist_end):
            slice_panel = {
                k: v[: offset + 1] for k, v in panel.items() if len(v) > offset
            }
            r = _ms_new_high_ratio_from_panel(slice_panel)
            if r is not None:
                hist.append(r)
    pct = percentile_rank(hist, current) if len(hist) >= 5 else None
    return {
        "ratio_pct": round(current, 2),
        "percentile_60d": round(pct, 1) if pct is not None else None,
        "sample_size": len(panel),
        "sample_requested": len(codes),
        "partial": len(panel) < _NEW_HIGH_SAMPLE,
        "source": "tushare.daily",
    }


def _ms_fetch_etf_flow(tc: Any) -> dict | None:
    """宽基 ETF（510300）份额变动估算资金流向。"""
    ts_code = _ETF_300_CODE
    df_share = tc.query(
        "fund_share", ts_code=ts_code,
        start_date=_days_ago(30), end_date=_today(),
    )
    df_price = tc.query(
        "fund_daily", ts_code=ts_code,
        start_date=_days_ago(30), end_date=_today(),
        fields="trade_date,close",
    )
    if df_share is None or df_share.empty:
        return None
    shares = df_share.sort_values("trade_date").to_dict("records")
    prices = {}
    if df_price is not None and not df_price.empty:
        for r in df_price.sort_values("trade_date").to_dict("records"):
            prices[str(r.get("trade_date", ""))] = safe_float(r.get("close"))

    def _net_flow(days: int) -> float | None:
        if len(shares) < days + 1:
            return None
        first, last = shares[-(days + 1)], shares[-1]
        d_shares = float(last.get("fd_share") or 0) - float(first.get("fd_share") or 0)
        px = prices.get(str(last.get("trade_date", "")))
        if px is None or px <= 0:
            return None
        return d_shares * 10000 * px  # fd_share 单位：万份

    flow_5d = _net_flow(5)
    flow_10d = _net_flow(10)
    if flow_5d is None and flow_10d is None:
        return None
    out: dict[str, Any] = {
        "ts_code": ts_code,
        "source": "tushare.fund_share+fund_daily",
    }
    if not prices:
        out["price_incomplete"] = True
    if flow_5d is not None:
        out["net_flow_5d"] = round(flow_5d, 0)
    if flow_10d is not None:
        out["net_flow_10d"] = round(flow_10d, 0)
    return out


def extract_industry_from_basic_info(data: dict | None) -> str | None:
    """从 basic_info 主数据提取行业名（兼容 Tushare / akshare 字段）。"""
    if not data or not isinstance(data, dict):
        return None
    for key in ("industry", "行业", "所属行业"):
        v = data.get(key)
        if v is not None and str(v).strip():
            return str(v).strip()
    return None


def extract_industry_from_collection(collection: dict) -> str | None:
    """从 collection 维度列表提取行业名。"""
    for dim in collection.get("dimensions", []):
        if dim.get("dimension") == "basic_info":
            return extract_industry_from_basic_info(dim.get("data"))
    return None


def attach_market_structure(collection: dict, symbol: str) -> dict:
    """采集市场结构并写入 collection['market_structure']。"""
    industry = extract_industry_from_collection(collection)
    collection["market_structure"] = collect_market_structure(symbol, industry=industry)
    return collection["market_structure"]


def attach_industry_peers(collection: dict, symbol: str) -> dict[str, Any]:
    """采集同行可比数据并写入 collection['industry_peers']。"""
    industry = extract_industry_from_collection(collection)
    collection["industry_peers"] = collect_industry_peers(symbol, industry=industry)
    return collection["industry_peers"]


def attach_pe_band(collection: dict, *, years: int = 5) -> dict[str, Any] | None:
    """计算 PE Band 数据层并写入 collection['pe_band']（供 Phase 4 消费）。"""
    from lib.valuation import pe_band_series

    val_rows: list[dict] = []
    for dim in collection.get("dimensions", []):
        if dim.get("dimension") == "valuation":
            data = dim.get("data")
            if isinstance(data, list):
                val_rows = data
            break
    if not val_rows:
        collection["pe_band"] = None
        return None
    band = pe_band_series(val_rows, years=years)
    collection["pe_band"] = band
    return band


def attach_phase2_extras(collection: dict, symbol: str) -> None:
    """挂载 Phase 2 扩展数据（同行、PE Band）。"""
    errors: list[str] = []
    peers_existing = collection.get("industry_peers")
    if not peers_existing or peers_existing.get("error"):
        try:
            attach_industry_peers(collection, symbol)
        except Exception as exc:
            errors.append(f"industry_peers: {exc}")
            collection["industry_peers"] = {
                "peers": [],
                "target": None,
                "rankings": {},
                "industry_name": None,
                "sufficient": False,
                "error": f"同行采集异常: {exc}",
            }
    if collection.get("pe_band") is None:
        try:
            attach_pe_band(collection)
        except Exception as exc:
            errors.append(f"pe_band: {exc}")
            collection["pe_band"] = None
    if errors:
        collection.setdefault("phase2_extras_errors", []).extend(errors)
        logger.warning("attach_phase2_extras partial failure for %s: %s", symbol, errors)


def _ms_try_fetch(
    result: dict[str, Any],
    key: str,
    fetch_fn: Callable[[], Any],
    *,
    unavailable_msg: str,
    on_success: Callable[[Any], str] | None = None,
) -> None:
    """采集单个子源并写入 result / availability（统一 try/except 模式）。"""
    try:
        value = fetch_fn()
        result[key] = value
        if value is None:
            _ms_set_unavailable(result["availability"], key, unavailable_msg)
        elif on_success is not None:
            result["availability"][key] = on_success(value)
        else:
            result["availability"][key] = "available"
    except Exception as exc:
        _ms_set_unavailable(result["availability"], key, str(exc))


def collect_market_structure(symbol: str, *, industry: str | None = None) -> dict:
    """采集市场结构因子（行业情绪/资金/ERP/换手）。各子源独立降级。"""
    result: dict[str, Any] = {
        "sw_index": None,
        "northbound": None,
        "margin": None,
        "moneyflow": None,
        "turnover": None,
        "erp": None,
        "pmi": None,
        "put_call_ratio": None,
        "short_margin": None,
        "new_high_ratio": None,
        "etf_flow": None,
        "availability": {},
    }
    config = env.get_config()
    _ms_keys = (
        "sw_index", "northbound", "margin", "moneyflow", "turnover", "erp",
        "put_call_ratio", "short_margin", "new_high_ratio", "etf_flow",
    )
    if not env.is_tushare_available(config):
        for key in _ms_keys:
            _ms_set_unavailable(result["availability"], key, "TUSHARE_TOKEN not configured")
        return result

    tc = _tushare_client(config)

    _ms_try_fetch(
        result, "sw_index",
        lambda: _ms_fetch_sw_index(tc, symbol, industry),
        unavailable_msg=(
            "申万行业指数不可得；Tushare sw_daily 需 5000 积分"
            "（https://tushare.pro/document/2?doc_id=327），"
            "2000 分档已尝试 akshare 回退"
        ),
        on_success=_ms_sw_index_availability_label,
    )
    _ms_try_fetch(
        result, "northbound",
        lambda: _ms_fetch_northbound_stock(tc, symbol),
        unavailable_msg="hsgt_top10 empty (not in top10) or akshare northbound unavailable",
    )
    _ms_try_fetch(
        result, "margin",
        lambda: _ms_fetch_margin(tc, symbol),
        unavailable_msg="margin_detail empty, insufficient history, or permission denied",
    )
    _ms_try_fetch(
        result, "moneyflow",
        lambda: _ms_fetch_moneyflow(tc, symbol),
        unavailable_msg="moneyflow empty or permission denied",
    )
    _ms_try_fetch(
        result, "turnover",
        lambda: _ms_fetch_turnover(tc, symbol),
        unavailable_msg="daily_basic turnover empty",
    )
    _ms_try_fetch(
        result, "erp",
        lambda: _ms_fetch_erp(tc, config),
        unavailable_msg="index_dailybasic or 10Y yield (FRED DGS10 / akshare CN10Y) unavailable",
        on_success=lambda v: (
            f"partial: {v.get('erp_days', 0)} aligned days (min {_ERP_MIN_ALIGNED_DAYS})"
            if v.get("partial") else "available"
        ),
    )
    _ms_try_fetch(
        result, "pmi",
        _ms_fetch_pmi,
        unavailable_msg="akshare macro_china_pmi unavailable",
    )
    _ms_try_fetch(
        result, "put_call_ratio",
        lambda: _ms_fetch_put_call_ratio(tc),
        unavailable_msg="opt_daily empty, no 50ETF options, or permission denied (5000 pts)",
        on_success=lambda v: (
            f"partial: {v.get('history_days', 0)} days"
            if v.get("partial") else "available"
        ),
    )
    _ms_try_fetch(
        result, "short_margin",
        lambda: _ms_fetch_short_margin_growth(tc, symbol),
        unavailable_msg="margin / margin_detail rqye empty or permission denied",
    )
    _ms_try_fetch(
        result, "new_high_ratio",
        lambda: _ms_fetch_new_high_ratio(tc),
        unavailable_msg="daily sample empty or insufficient",
        on_success=lambda v: (
            f"partial: sample {v.get('sample_size', 0)}/{_NEW_HIGH_SAMPLE}"
            if v.get("partial") else "available"
        ),
    )
    _ms_try_fetch(
        result, "etf_flow",
        lambda: _ms_fetch_etf_flow(tc),
        unavailable_msg="fund_share / fund_daily empty or permission denied",
        on_success=lambda v: (
            "partial: fund_daily close missing for flow estimate"
            if v.get("price_incomplete") else "available"
        ),
    )

    return result


# ---- 行业同行采集（v0.1.3 Phase 2） ----

# 同行分位：数值越高越好的指标（rank=1 表示最高）
_PEER_HIGHER_IS_BETTER = frozenset({"revenue_yoy", "roe"})


def _prior_year_end_date(end_date: str) -> str:
    """报告期 → 去年同期（同月同日，YYYYMMDD）。"""
    from lib.financials import prior_year_end_date
    return prior_year_end_date(end_date)


def _revenue_yoy_from_fina_rows(rows: list[dict]) -> float | None:
    """按 end_date 对齐去年同期营收，计算同比增速（%）。"""
    if not rows:
        return None
    sorted_rows = sorted(rows, key=lambda r: str(r.get("end_date", "")))
    latest = sorted_rows[-1]
    rev_cur = safe_float(latest.get("revenue"))
    if rev_cur is None or rev_cur <= 0:
        return None
    prev_ed = _prior_year_end_date(str(latest.get("end_date", "")))
    if not prev_ed:
        return None
    from lib.financials import normalize_end_date
    rev_prev = None
    for r in reversed(sorted_rows[:-1]):
        if normalize_end_date(str(r.get("end_date", ""))) == prev_ed:
            rev_prev = safe_float(r.get("revenue"))
            break
    if rev_prev is None or rev_prev <= 0:
        return None
    return round((rev_cur - rev_prev) / rev_prev * 100, 2)


def _gross_margin_trend_from_rows(fin_rows: list[dict]) -> str | None:
    """近 2 个会计年度毛利率方向（供竞争加剧信号）。"""
    from lib.financials import gross_margin_trend_from_rows
    return gross_margin_trend_from_rows(fin_rows)


def _peer_metrics_from_fina(
    fin_rows: list[dict], fin_row: dict | None,
) -> dict[str, Any]:
    """从 fina_indicator 行提取同行对比 / 风险扫描字段。"""
    out: dict[str, Any] = {}
    if fin_row:
        out["roe"] = safe_float(fin_row.get("roe"))
        out["revenue_yoy"] = _revenue_yoy_from_fina_rows(fin_rows)
        gm = safe_float(fin_row.get("grossprofit_margin"))
        if gm is not None:
            out["grossprofit_margin"] = gm
            out["gross_margin"] = gm
        debt = safe_float(fin_row.get("debt_to_assets"))
        if debt is not None:
            out["debt_to_assets"] = debt
    trend = _gross_margin_trend_from_rows(fin_rows)
    if trend is not None:
        out["gross_margin_trend"] = trend
    return out


def _fetch_peer_fina_rows(tc: Any, code: str) -> list[dict]:
    """拉取近 2.5 年 fina_indicator，供 ROE / 毛利率 / 负债率等使用。"""
    fin_df = tc.query(
        "fina_indicator",
        ts_code=code,
        fields="ts_code,end_date,roe,revenue,grossprofit_margin,debt_to_assets",
        start_date=_days_ago(950),
        end_date=_today(),
    )
    if fin_df is None or fin_df.empty:
        return []
    return fin_df.sort_values("end_date").to_dict("records")


def collect_industry_peers(
    symbol: str,
    *,
    industry: str | None = None,
    max_peers: int = 10,
) -> dict[str, Any]:
    """申万行业同行池，PE/PB/ROE/营收增速分位排名。

    1. 查申万行业成分股（L3→L2→L1）
    2. 获取每只成分股的 PE(TTM)、PB、ROE、近一年营收增速
    3. 计算 target 在同行中的分位排名
    4. 返回可比公司表（上限 max_peers 家）
    """
    result: dict[str, Any] = {
        "peers": [],
        "target": None,
        "rankings": {},
        "industry_name": None,
        "peer_source": None,
        "sufficient": False,
    }

    config = env.get_config()
    if not env.is_tushare_available(config):
        result["error"] = "Tushare Token 不可用，无法采集同行数据"
        return result

    tc = _tushare_client(config)
    target_sym = _ts_code(symbol)

    industry = _resolve_sw_industry_name(tc, symbol, industry)
    if not industry:
        result["error"] = "无法确定行业分类"
        return result

    result["industry_name"] = industry

    index_code = _ms_lookup_sw_index_code(tc, industry)
    members: list[dict] = []
    name_by_code: dict[str, str] = {}
    peer_source: str | None = None

    if index_code:
        try:
            member_df = tc.query("index_member", index_code=index_code)
            if member_df is not None and not member_df.empty:
                members = member_df.to_dict("records")
                peer_source = "sw_index_member"
                for m in members:
                    code = str(m.get("ts_code", "")).strip()
                    if code:
                        name_by_code[code] = str(m.get("name", "")).strip()
        except Exception as exc:
            logger.debug("index_member failed for %s: %s", index_code, exc)

    if not members:
        basic_all = tc.query("stock_basic", fields="ts_code,name,industry")
        if basic_all is not None and not basic_all.empty:
            for _, row in basic_all.iterrows():
                if str(row.get("industry", "")).strip() == industry:
                    rec = row.to_dict() if hasattr(row, "to_dict") else dict(row)
                    members.append(rec)
                    code = str(rec.get("ts_code", "")).strip()
                    if code:
                        name_by_code[code] = str(rec.get("name", "")).strip()
        if members:
            peer_source = "stock_basic_fallback"
            result["warning"] = (
                "同行池来自 Tushare stock_basic.industry 粗分类，非申万 L3 成分股；"
                "分位排名与可比公司表已降级，仅供参考。"
            )

    if not members:
        result["error"] = f"未找到「{industry}」行业成分股"
        return result

    result["peer_source"] = peer_source

    all_codes = sorted({str(m.get("ts_code", "")).strip() for m in members if m.get("ts_code")})
    if target_sym not in all_codes:
        all_codes.append(target_sym)
        basic_one = tc.query("stock_basic", ts_code=target_sym, fields="ts_code,name")
        if basic_one is not None and not basic_one.empty:
            name_by_code[target_sym] = str(basic_one.iloc[0].get("name", "")).strip()

    other_codes = sorted(c for c in all_codes if c != target_sym)[:max_peers]
    peer_codes = [target_sym, *other_codes]

    target_metrics: dict[str, Any] | None = None
    peers_metrics: list[dict[str, Any]] = []

    for code in peer_codes:
        try:
            fin_rows = _fetch_peer_fina_rows(tc, code)
            fin_row = fin_rows[-1] if fin_rows else None
            val_df = tc.query("daily_basic", ts_code=code,
                              fields="ts_code,pe_ttm,pb,total_mv",
                              start_date=_days_ago(30), end_date=_today(),
                              limit=1)

            val_row = val_df.iloc[-1].to_dict() if val_df is not None and not val_df.empty else None

            peer_entry: dict[str, Any] = {
                "symbol": code.split(".")[0] if "." in code else code,
                "name": name_by_code.get(code, ""),
                "pe_ttm": None,
                "pb": None,
                "roe": None,
                "revenue_yoy": None,
                "total_mv": None,
            }
            if fin_row:
                peer_entry.update(_peer_metrics_from_fina(fin_rows, fin_row))
            if val_row:
                pe_v = safe_float(val_row.get("pe_ttm"))
                if pe_v is not None and pe_v > 0:
                    peer_entry["pe_ttm"] = pe_v
                peer_entry["pb"] = safe_float(val_row.get("pb"))
                peer_entry["total_mv"] = safe_float(val_row.get("total_mv"))

            if code == target_sym:
                target_metrics = peer_entry
            else:
                peers_metrics.append(peer_entry)

        except Exception as exc:
            logger.debug("collect_industry_peers: skip %s: %s", code, exc)
            continue

    peers_metrics.sort(
        key=lambda p: (p.get("total_mv") is None, -(p.get("total_mv") or 0)),
    )

    if target_metrics:
        result["target"] = target_metrics

    result["peers"] = peers_metrics
    result["sufficient"] = (
        peer_source == "sw_index_member" and len(peers_metrics) >= 3
    )

    if target_metrics and len(peers_metrics) >= 1:
        rankings: dict[str, Any] = {}
        for metric in ("pe_ttm", "pb", "roe", "revenue_yoy"):
            tv = target_metrics.get(metric)
            pv = [p.get(metric) for p in peers_metrics if p.get(metric) is not None]
            if tv is not None and pv:
                below = sum(1 for v in pv if v < tv)
                above = sum(1 for v in pv if v > tv)
                pct = round(below / len(pv) * 100, 1)
                rankings[f"{metric}_pct"] = pct
                if metric in _PEER_HIGHER_IS_BETTER:
                    rankings[f"{metric}_rank"] = above + 1
                else:
                    rankings[f"{metric}_rank"] = below + 1
                rankings[f"{metric}_total"] = len(pv) + 1
            else:
                rankings[f"{metric}_pct"] = None
                rankings[f"{metric}_rank"] = None
                rankings[f"{metric}_total"] = None
        result["rankings"] = rankings

    return result


# 测试与旧代码兼容别名
_safe_float_val = safe_float
