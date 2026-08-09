"""R12g-A 龙虎榜/涨停池采集层（决策 C4：akshare 免费源先行，不依赖 tushare 升积分）。

数据源（2026-08-06 探测确认）：
- 东财 `stock_lhb_detail_em`（datacenter-web 代理可达；上榜日/净买额/上榜原因/上榜后 N 日）
- 新浪 `stock_lhb_detail_daily_sina`（名单互备；无席位字段，仅可做名单层互备）
- 东财 `stock_lhb_stock_detail_em`（席位：营业部买入/卖出榜；空结果存在 akshare 解析 bug，须防御）
- 东财 `stock_zt_pool_em`（涨停池：当日家数 + 连板分布 → 情绪周期定位）

⚠️ 连板 ≠ 必然上榜：603773（沃格）8-04/8-05 双连板但双源龙虎榜均未上榜
（涨停潮日家数 >30 时个别连板股可能不触发"偏离 7% 前 5 只"等上榜条件）——
空结果必须优雅降级（席位缺失 → 渲染层用资金流三日结构替代）。
"""

from __future__ import annotations

import logging
from datetime import timedelta

from .shared_dates import shanghai_now as _shanghai_now

logger = logging.getLogger(__name__)

# 连板触发门槛（R12e 检测输出近 5 日涨停数）
TRIGGER_LIMIT_UPS_5D = 2


def should_trigger_lhb(kline: list[dict], symbol: str = "",
                       name: str = "") -> bool:
    """R12g-A 触发判定：近 5 日 ≥2 涨停 → 采集龙虎榜/涨停池。

    name: 证券简称；传入时主板 ST/*ST 股按 5% 涨停阈值判定
    （否则按板块阈值 10%/20%/30%，ST 两板会被漏判）。
    kline 缺失/异常 → False（防御，不触发）。
    """
    if not isinstance(kline, list) or not kline:
        return False
    try:
        from lib.technical import detect_limit_streaks
        st = detect_limit_streaks(kline, symbol=symbol, name=name, lookback=5)
        if not st.get("available"):
            return False
        return int(st.get("recent_limit_ups", 0)) >= TRIGGER_LIMIT_UPS_5D
    except Exception:
        return False


def fetch_lhb_detail(symbol: str, days: int = 7) -> dict | None:
    """该 symbol 最近 days 自然日内的龙虎榜记录（东财 → 新浪回退）。

    Returns:
        {"records": [...], "source": "em"|"sina", "seats": {上榜日: {买入: [...], 卖出: [...]}}}
        未上榜或双源均失败 → None
    """
    symbol = str(symbol).strip().zfill(6)
    em = _fetch_lhb_em(symbol, days)
    if em is not None:
        return em
    sina = _fetch_lhb_sina(symbol, days)
    if sina is not None:
        return sina
    return None


def _fetch_lhb_em(symbol: str, days: int) -> dict | None:
    """东财龙虎榜明细（datacenter-web，2026-08-06 探测可达）。"""
    try:
        import akshare as ak
        end = _shanghai_now().date()
        start = end - timedelta(days=days - 1)
        df = ak.stock_lhb_detail_em(
            start_date=start.strftime("%Y%m%d"), end_date=end.strftime("%Y%m%d"),
        )
        if df is None or df.empty:
            return None
        rows = [r for r in df.to_dict("records")
                if str(r.get("代码", "")).strip().zfill(6) == symbol]
        if not rows:
            return None
        return {"records": rows, "source": "em", "seats": _fetch_seats(symbol, rows)}
    except Exception as exc:
        logger.warning("lhb[em] %s: %s", symbol, exc)
        return None


def _fetch_seats(symbol: str, em_rows: list[dict]) -> dict:
    """席位明细：按上榜日 × 买入/卖出榜聚合（每榜前 5 营业部）。

    akshare 对未上榜标的的 `stock_lhb_stock_detail_em` 存在空结果解析 bug
    （TypeError 'NoneType' not subscriptable）——防御捕获，失败仅丢该榜。
    """
    seats: dict[str, dict] = {}
    try:
        import akshare as ak
    except Exception:
        return seats
    for r in em_rows:
        day = str(r.get("上榜日", ""))[:10]
        if not day:
            continue
        for flag in ("买入", "卖出"):
            try:
                df = ak.stock_lhb_stock_detail_em(symbol=symbol, date=day, flag=flag)
                if df is not None and not df.empty:
                    seats.setdefault(day, {})[flag] = df.head(5).to_dict("records")
            except Exception:
                continue
    return seats


def _fetch_lhb_sina(symbol: str, days: int) -> dict | None:
    """新浪龙虎榜名单（名单互备；无席位字段）。逐日拉取最近 days 自然日。"""
    try:
        import akshare as ak
        rows: list[dict] = []
        for offset in range(days):
            day = _shanghai_now().date() - timedelta(days=offset)
            df = ak.stock_lhb_detail_daily_sina(date=day.strftime("%Y%m%d"))
            if df is None or df.empty:
                continue
            for r in df.to_dict("records"):
                if str(r.get("代码", "")).strip().zfill(6) == symbol:
                    rows.append(r)
            if rows:
                break
        if not rows:
            return None
        return {"records": rows, "source": "sina", "seats": {}}
    except Exception as exc:
        logger.warning("lhb[sina] %s: %s", symbol, exc)
        return None


def _parse_board_count(r: dict) -> int:
    """连板数逐行安全解析（缺陷修复：单行畸形不得吞整池）。

    - 数值（含 0/0.0）原样返回 → 0 连板不再被 falsy 误改 1
    - NaN/None/缺字段 → 回退「涨停统计」
    - 涨停统计为 akshare 'N/M' 风格字符串 → 取 '/' 前 N
    - 均不可解析 → 1（首板兜底，仅该行降级）
    """
    for key in ("连板数", "涨停统计"):
        v = r.get(key)
        if v is None:
            continue
        try:
            head = str(v).split("/")[0].strip()
            f = float(head)
            if f == f:  # 排除 NaN（'nan' → float nan → 非自身）
                return int(f)
        except (TypeError, ValueError):
            continue
    return 1


def fetch_zt_pool(max_lookback: int = 5) -> dict | None:
    """涨停池：当日涨停家数 + 连板分布（情绪周期定位）。

    非交易日/早盘数据为空 → 向前回溯最多 max_lookback 个自然日。
    Returns: {"date": str, "total": int, "max_board": int, "board_dist": {N板: 家数}}
    """
    try:
        import akshare as ak
        for offset in range(max_lookback):
            day = _shanghai_now().date() - timedelta(days=offset)
            df = ak.stock_zt_pool_em(date=day.strftime("%Y%m%d"))
            if df is None or df.empty:
                continue
            records = df.to_dict("records")
            total = len(records)
            dist: dict[int, int] = {}
            for r in records:
                try:
                    b = _parse_board_count(r)
                except Exception:  # 单行畸形兜底：仅该行按首板计，不丢整池
                    b = 1
                dist[b] = dist.get(b, 0) + 1
            return {
                "date": day.strftime("%Y-%m-%d"),
                "total": total,
                "max_board": max(dist) if dist else 0,
                "board_dist": {k: dist[k] for k in sorted(dist)},
            }
        return None
    except Exception as exc:
        logger.warning("lhb[zt_pool]: %s", exc)
        return None


def parse_seats(seats: dict) -> dict:
    """席位分析：聚合买入/卖出榜 top 营业部。

    Returns: {"top_buy": [...], "top_sell": [...], "source": "em", "has_seats": bool}
    空 seats → has_seats=False（渲染层降级：资金流三日结构替代）。
    """
    top_buy: list[dict] = []
    top_sell: list[dict] = []
    for day, flags in (seats or {}).items():
        for r in (flags.get("买入") or []):
            top_buy.append({"date": day, **r})
        for r in (flags.get("卖出") or []):
            top_sell.append({"date": day, **r})
    return {
        "top_buy": top_buy,
        "top_sell": top_sell,
        "source": "em",
        "has_seats": bool(top_buy or top_sell),
    }


def attach_limit_streak_dims(collection: dict, symbol: str) -> bool:
    """连板触发 → 采集龙虎榜/涨停池 → 写入 collection["dimensions"]。

    未触发 → False（零额外网络调用）；触发但源不可得 → 写 degraded 维度（不阻断）。
    """
    try:
        from lib.render_utils import _get_dim_data, _index_dims
    except ImportError:
        return False
    indexed = _index_dims(collection)
    kline = _get_dim_data(indexed, "kline")
    basic = _get_dim_data(indexed, "basic_info") or {}
    name = ""
    if isinstance(basic, dict):
        name = str(basic.get("name") or basic.get("股票简称") or "")
    if not should_trigger_lhb(kline, symbol=symbol, name=name):
        return False
    dims = collection.setdefault("dimensions", [])
    lhb = fetch_lhb_detail(symbol, days=7)
    zt = fetch_zt_pool()
    if lhb:
        dims.append({
            "dimension": "lhb", "display": "龙虎榜（连板触发 R12g）",
            "data": {"records": lhb["records"], "seats": parse_seats(lhb["seats"]),
                     "source": lhb["source"]},
            "status": "available",
            "_meta": {"source": lhb["source"], "query_params": "stock_lhb_detail_em/sina"},
        })
    else:
        dims.append({
            "dimension": "lhb", "display": "龙虎榜（连板触发 R12g）",
            "data": {"records": [], "seats": {"has_seats": False}, "source": ""},
            "status": "degraded",
            "error": "双源均不可得或未上榜（连板 ≠ 必然上榜）",
            "_meta": {"source": "em/sina", "query_params": "stock_lhb_detail_em/sina"},
        })
    if zt:
        dims.append({
            "dimension": "zt_pool", "display": "涨停池（连板触发 R12g）",
            "data": zt, "status": "available",
            "_meta": {"source": "em", "query_params": "stock_zt_pool_em"},
        })
    return True
