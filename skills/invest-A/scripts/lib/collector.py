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
from .proxy import no_proxy_session, proxy_bypass
from .proxy import EASTMONEY_BLOCKED_KEYWORDS as _EASTMONEY_BLOCKED_KEYWORDS
from .schema import SourceResult, DimensionResult

logger = logging.getLogger(__name__)


# ---- 日期工具（函数形式，避免导入时固化） ----

def _today() -> str:
    return datetime.now().strftime("%Y%m%d")


def _days_ago(n: int) -> str:
    return (datetime.now() - timedelta(days=n)).strftime("%Y%m%d")


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

# 东方财富 API 连接失败时的可操作提示
_EASTMONEY_BLOCKED_MSG = (
    "东方财富(East Money) API 连接失败。"
    "若使用 Clash/VPN，请在规则中将 DOMAIN-SUFFIX,eastmoney.com,DIRECT；"
    "TUN 模式需在网卡层配置规则，或暂时关闭 VPN 后重试。"
    "可改用 Tushare / Baostock 作为替代数据源。"
)


def _is_eastmoney_blocked_error(error: str) -> bool:
    """检测异常消息是否明确指向东方财富。"""
    return any(kw in str(error) for kw in _EASTMONEY_BLOCKED_KEYWORDS)


def _reraise_eastmoney_api_error(exc: Exception) -> None:
    """在东方财富 akshare 接口内，将连接失败转为可操作的 VPN/TUN 提示。

    仅在已知调用东方财富 API 的函数中使用，避免误伤同花顺等其他源。
    """
    if _is_eastmoney_blocked_error(str(exc)):
        raise RuntimeError(_EASTMONEY_BLOCKED_MSG) from exc
    err = str(exc)
    if any(kw in err for kw in (
        "Connection", "Remote end closed", "RemoteDisconnected", "ProxyError",
        "Max retries exceeded",
    )):
        raise RuntimeError(_EASTMONEY_BLOCKED_MSG) from exc
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


def _q_tushare_financials(symbol: str) -> list[dict] | None:
    from . import env as _env
    config = _env.get_config()
    if not _env.is_tushare_available(config):
        raise RuntimeError("TUSHARE_TOKEN not configured")
    tc = _tushare_client(config)
    df = tc.query("fina_indicator", ts_code=_ts_code(symbol),
                  fields="ts_code,end_date,roe,eps,profit_dedt,revenue,net_profit",
                  start_date=_days_ago(730), end_date=_today())
    if df is not None and not df.empty:
        return df.to_dict("records")
    return None


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
    """统一 net_mf_vol 为「元」。Tushare moneyflow 字段单位为万元，akshare 为元。"""
    if not records:
        return records
    scale = 10000.0 if source.startswith("tushare") else 1.0
    out: list[dict] = []
    for r in records:
        row = dict(r)
        v = row.get("net_mf_vol")
        if v is not None and scale != 1.0:
            row["net_mf_vol"] = float(v) * scale
        out.append(row)
    return out


def _q_tushare_moneyflow(symbol: str) -> list[dict] | None:
    from . import env as _env
    config = _env.get_config()
    if not _env.is_tushare_available(config):
        raise RuntimeError("TUSHARE_TOKEN not configured")
    tc = _tushare_client(config)
    df = tc.query("moneyflow", ts_code=_ts_code(symbol),
                  fields="ts_code,trade_date,buy_sm_vol,sell_sm_vol,net_mf_vol",
                  start_date=_days_ago(10), end_date=_today())
    if df is not None and not df.empty:
        return _normalize_northbound_records(df.to_dict("records"), "tushare.moneyflow")
    return None


def _q_akshare_basic(symbol: str) -> dict | None:
    """akshare 基本信息来源（东方财富 API）。"""
    with _proxy_bypass():
        import akshare as ak
        try:
            result = ak.stock_individual_info_em(symbol=symbol.strip().zfill(6),
                                                  timeout=8)
            if result is not None:
                if hasattr(result, "to_dict"):
                    records = result.to_dict("records") if callable(result.to_dict) else result.to_dict
                    # stock_individual_info_em 返回 [{"item":..., "value":...}, ...]
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
                # 中文列名 → 英文键名映射
                return [_map_akshare_financial_keys(r) for r in records]
        return None


def _q_akshare_kline(symbol: str, start_date: str = "", end_date: str = "") -> list[dict] | None:
    """akshare K线来源（东方财富 API）。"""
    with _proxy_bypass():
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
    with _proxy_bypass():
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


def _parse_akshare_num(v, unit: str = "") -> float | None:
    """将 akshare 返回的字符串数值转为 float，兼容 '%' / '亿' / '万' 后缀。

    例如 "8.37%" → 8.37, "17.88亿" → 1788000000.0, "2,456.78万" → 24567800.0
    """
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if not isinstance(v, str):
        return None
    s = v.strip().replace(",", "").replace(" ", "")
    multiplier = 1.0
    if "亿" in s:
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


def _map_akshare_financial_keys(r: dict) -> dict:
    """akshare stock_financial_abstract_ths 列名映射。

    注意：akshare 返回的数值带中文单位（如 "17.88亿"、"8.37%"),
    _parse_akshare_num 将其转换为与 Tushare 一致的纯 float 格式。
    """
    return {
        "end_date": str(r.get("报告期", "")),
        "roe": _parse_akshare_num(r.get("净资产收益率")),
        "eps": _parse_akshare_num(r.get("基本每股收益")),
        "profit_dedt": _parse_akshare_num(r.get("扣非净利润")),
        "revenue": _parse_akshare_num(r.get("营业总收入")),
        "net_profit": _parse_akshare_num(r.get("净利润")),
    }


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
    """akshare 前十大股东来源（东方财富）。"""
    with _proxy_bypass():
        import akshare as ak
        from datetime import datetime
        # 使用最新两次报告期的数据
        now = datetime.now()
        # 尝试当前季度末日期
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
    with _BAOSTOCK_LOCK:
        with _proxy_bypass():
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

    with no_proxy_session() as sess:
        market = "sh" if symbol.startswith(("6", "9")) else "sz"
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
    if env.is_akshare_available():
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
    if env.is_akshare_available():
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
    if env.is_akshare_available():
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
    """北向资金。并行：Tushare + akshare。"""
    tasks: list[tuple[str, Callable]] = []
    if env.is_tushare_available(env.get_config()):
        tasks.append(("tushare.moneyflow", lambda: _q_tushare_moneyflow(symbol)))
    if env.is_akshare_available():
        tasks.append(("akshare.stock_hsgt_individual_em",
                      lambda: _q_akshare_northbound(symbol)))

    results = _run_sources_parallel(tasks, "northbound")
    result_map = {r.source: r for r in results}
    _annotate_query_params(result_map, {
        "tushare.moneyflow": _qp_tushare("moneyflow", symbol,
                                         start_date=_days_ago(10), end_date=_today()),
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
    if env.is_akshare_available():
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
    """按线程惰性加载 TushareClient，避免跨线程共享 Session 和配额状态。"""
    if not hasattr(_tc_local, "instance"):
        from lib.tushare_client import TushareClient
        _tc_local.instance = TushareClient(token=config.get("TUSHARE_TOKEN"))
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


# ---- 全维度采集 ----

COLLECTORS = {
    "basic_info": ("基本信息", collect_basic_info),
    "financials": ("财务报告", collect_financials),
    "quote": ("实时行情", collect_quote),
    "shareholders": ("十大股东", collect_shareholders),
    "northbound": ("北向资金", collect_northbound),
    "kline": ("日K线", collect_kline),
    "valuation": ("估值分析", collect_valuation),
}


def collect_all(symbol: str, dims: list[str] | None = None,
                deep: bool = False) -> dict[str, Any]:
    """全维度采集。

    last30days 模式扩展：维度之间也并行执行（跨维度 fan-out）。
    每个维度内部已在 collect_* 中并行查源。

    Args:
        symbol: 股票代码
        dims: 维度列表，None 使用默认（含 valuation + kline）
        deep: 深度模式，kline 扩大到 730 自然日
    """
    if dims is None:
        dims = ["basic_info", "financials", "quote", "shareholders",
                "northbound", "valuation", "kline"]

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

    has_data = sum(1 for d in dimensions if d and d.get("data") is not None and d.get("status") != "partial")
    partial = sum(1 for d in dimensions if d and d.get("status") == "partial")
    missing = sum(1 for d in dimensions if d and (d.get("data") is None and d.get("status") != "partial"))

    return {
        "symbol": symbol,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "dimensions": dimensions or [],
        "summary": {
            "total": len(dimensions),
            "available": has_data,
            "degraded": partial,
            "missing": missing,
        },
    }
