"""Tushare 数据增强层。

提供交易日历、市场分类、股价数据的增强获取。
TUSHARE_TOKEN 不可用时静默降级，返回空数据。
"""

from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta
from typing import Any

from _invest_path import ensure_invest_a_scripts_on_path

ensure_invest_a_scripts_on_path()

from lib import env  # noqa: E402
from lib.tushare_client import TushareClient  # noqa: E402

logger = logging.getLogger(__name__)

# 单次 query 的 ts_code 上限（避免 URL/参数过长）
_TS_CODE_BATCH = 100

# 缓存 Tushare 客户端（单例）
_tushare: TushareClient | None = None
_tushare_checked = False


def _get_client() -> TushareClient | None:
    """获取可用的 Tushare 客户端；构造/探测失败时静默返回 None。"""
    global _tushare, _tushare_checked
    if _tushare_checked:
        return _tushare
    _tushare_checked = True
    try:
        config = env.get_config()
        if not env.is_tushare_available(config):
            return None
        client = TushareClient()
        if not client.is_available():
            return None
        _tushare = client
        return _tushare
    except Exception as e:
        logger.warning("TushareClient 初始化失败，静默降级: %s", e)
        _tushare = None
        return None


def get_trade_dates(n: int) -> list[str]:
    """获取最近 N 个交易日（YYYYMMDD 降序）。

    Tushare trade_cal 优先，降级到 n*1.4 自然日覆盖。
    """
    client = _get_client()
    if client:
        try:
            end = datetime.now().strftime("%Y%m%d")
            start = (datetime.now() - timedelta(days=max(n * 2, 14))).strftime("%Y%m%d")
            cal = client.query(
                "trade_cal", exchange="SSE",
                start_date=start, end_date=end,
                fields="cal_date,is_open",
            )
            if cal is not None and len(cal) > 0:
                open_days = sorted(
                    cal[cal["is_open"] == 1]["cal_date"].tolist(),
                    reverse=True,
                )
                if open_days:
                    return [str(d) for d in open_days[:n]]
        except Exception as e:
            logger.warning("Tushare trade_cal 失败: %s，降级到自然日覆盖", e)

    # 降级：自然日覆盖
    count = max(int(n * 1.4), 1)
    return [(datetime.now() - timedelta(days=i)).strftime("%Y%m%d") for i in range(count)]


def enrich_stock_info(symbols: list[str]) -> dict[str, dict]:
    """批量获取股票市场分类 + ST 状态。

    Tushare stock_basic → {symbol: {name, market, is_st, list_date}}
    无 Token 时返回空 dict。按 ts_code 分批服务端过滤，不拉全市场。
    """
    if not symbols:
        return {}

    client = _get_client()
    if not client:
        return {}

    ts_codes = [c for c in (_to_ts_code(s) for s in symbols) if c]
    if not ts_codes:
        return {}

    try:
        result: dict[str, dict] = {}
        for i in range(0, len(ts_codes), _TS_CODE_BATCH):
            batch = ts_codes[i:i + _TS_CODE_BATCH]
            df = client.query(
                "stock_basic",
                ts_code=",".join(batch),
                list_status="L",
                fields="ts_code,name,market,list_date",
            )
            if df is None or len(df) == 0:
                continue
            for _, row in df.iterrows():
                ts_code = _safe_str(row.get("ts_code"))
                if not ts_code:
                    continue
                sym = ts_code.split(".")[0]
                name = _safe_str(row.get("name"))
                market_raw = _safe_str(row.get("market"))
                result[sym] = {
                    "ts_code": ts_code,
                    "name": name,
                    "market": _market_label(market_raw),
                    "market_code": market_raw,
                    "is_st": "ST" in name.upper(),
                    "list_date": _safe_str(row.get("list_date")),
                }
        return {s: result[s] for s in symbols if s in result}
    except Exception as e:
        logger.warning("Tushare stock_basic 失败: %s", e)
        return {}


def enrich_price_data(
    symbols: list[str], trade_date: str
) -> dict[str, dict[str, float]]:
    """批量获取某日股价 + 成交额 + 流通市值。

    Tushare daily + daily_basic → {symbol: {close, pct_chg, amount, float_mkt_cap}}
    无 Token 或无有效 ts_code 时返回空 dict（绝不发空查询以免拉全市场）。
    """
    if not symbols or not trade_date:
        return {}

    client = _get_client()
    if not client:
        return {}

    ts_codes = [c for c in (_to_ts_code(s) for s in symbols) if c]
    if not ts_codes:
        # H6: 全部转换失败时禁止发空 ts_code 请求
        return {}

    try:
        result: dict[str, dict[str, float]] = {}
        for i in range(0, len(ts_codes), _TS_CODE_BATCH):
            batch = ts_codes[i:i + _TS_CODE_BATCH]
            ts_codes_str = ",".join(batch)

            df = client.query(
                "daily",
                ts_code=ts_codes_str,
                trade_date=trade_date,
                fields="ts_code,trade_date,close,pct_chg,amount",
            )
            if df is not None and len(df) > 0:
                for _, row in df.iterrows():
                    ts_code = _safe_str(row.get("ts_code"))
                    if not ts_code:
                        continue
                    sym = ts_code.split(".")[0]
                    close = _safe_float_or_none(row.get("close"))
                    pct_chg = _safe_float_or_none(row.get("pct_chg"))
                    amount = _safe_float_or_none(row.get("amount"))
                    if close is None and pct_chg is None and amount is None:
                        continue
                    result[sym] = {
                        "close": close if close is not None else 0.0,
                        "pct_chg": pct_chg if pct_chg is not None else 0.0,
                        # Tushare amount 单位：千元 → 元
                        "amount": (amount * 1000) if amount is not None else 0.0,
                    }

            # 流通市值（万元）→ 元
            basic = client.query(
                "daily_basic",
                ts_code=ts_codes_str,
                trade_date=trade_date,
                fields="ts_code,circ_mv",
            )
            if basic is not None and len(basic) > 0:
                for _, row in basic.iterrows():
                    ts_code = _safe_str(row.get("ts_code"))
                    if not ts_code:
                        continue
                    sym = ts_code.split(".")[0]
                    circ = _safe_float_or_none(row.get("circ_mv"))
                    if circ is None:
                        continue
                    entry = result.setdefault(sym, {"close": 0.0, "pct_chg": 0.0, "amount": 0.0})
                    entry["float_mkt_cap"] = circ * 1e4

        return result
    except Exception as e:
        logger.warning("Tushare daily/daily_basic 失败: %s", e)
        return {}


def _market_label(code: str) -> str:
    """Tushare market 字段 → 中文标签（兼容数字码与中文）。"""
    raw = str(code or "").strip()
    if not raw:
        return "未知"
    # Pro API 通常直接返回中文
    known_cn = {"主板", "创业板", "科创板", "北交所", "CDR"}
    if raw in known_cn:
        return raw
    mapping = {
        "0": "主板",
        "1": "创业板",
        "2": "科创板",
        "3": "北交所",
        "4": "CDR",
        "主板": "主板",
        "创业板": "创业板",
        "科创板": "科创板",
        "北交所": "北交所",
        "CDR": "CDR",
    }
    return mapping.get(raw, raw if raw else f"未知({code})")


def _to_ts_code(symbol: str) -> str:
    """6 位数字代码 → Tushare ts_code（600176.SH / 300001.SZ）。非法输入返回空串。"""
    s = str(symbol).strip()
    if not s.isdigit():
        return ""
    s = s.zfill(6)
    if len(s) != 6:
        return ""
    if s.startswith(("6", "9")):
        return f"{s}.SH"
    if s.startswith(("0", "2", "3")):
        return f"{s}.SZ"
    if s.startswith(("8", "4")):
        return f"{s}.BJ"
    return ""


def _safe_str(val: Any) -> str:
    """避免 str(NaN) → 'nan'。"""
    if val is None:
        return ""
    try:
        if isinstance(val, float) and math.isnan(val):
            return ""
    except TypeError:
        pass
    text = str(val).strip()
    if text.lower() == "nan":
        return ""
    return text


def _safe_float_or_none(val: Any) -> float | None:
    """安全转 float；None/NaN/非法值返回 None（避免 nan 污染下游）。"""
    if val is None:
        return None
    try:
        if isinstance(val, float) and math.isnan(val):
            return None
        f = float(val)
        if math.isnan(f):
            return None
        return f
    except (TypeError, ValueError):
        return None
