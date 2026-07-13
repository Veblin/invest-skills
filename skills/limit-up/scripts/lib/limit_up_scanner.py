"""全市场涨停扫描模块。

使用 akshare stock_zt_pool_em 系列 API 获取每日涨停池，
去重合并并计算宽度/集中度指标。

跨 skill 导入 invest-A 的 proxy/env 基础设施（通过 sys.path 注入）。
"""

from __future__ import annotations

import logging
import math
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

# ---- 跨 skill 导入 invest-A 基础设施 ----
# scripts/lib/ → scripts/ → limit-up/ → skills/ → invest-A/scripts
_INVEST_A_LIB = (
    Path(__file__).resolve().parent.parent.parent.parent
    / "invest-A" / "scripts"
)
import sys

if str(_INVEST_A_LIB) not in sys.path:
    sys.path.insert(0, str(_INVEST_A_LIB))

from lib import env  # noqa: E402
from lib.proxy import akshare_direct_session, akshare_push2_available  # noqa: E402

# 同目录 Tushare L2
_LIB_DIR = Path(__file__).resolve().parent
if str(_LIB_DIR) not in sys.path:
    sys.path.insert(0, str(_LIB_DIR))

from tushare_enrich import (  # noqa: E402
    enrich_price_data,
    enrich_stock_info,
    get_trade_dates,
)

logger = logging.getLogger(__name__)

_EARLY_SEAL_CUTOFF = "094500"


# ---- 公开 API ----


def scan_market(days: int = 10) -> dict:
    """全市场涨停扫描主入口。

    策略：
    1. push2 / akshare 预检
    2. Tushare trade_cal 优先取交易日，降级 days*1.4 自然日
    3. 顺序调用 stock_zt_pool_em + 辅池（strong/previous/zbgc）
    4. 按 symbol 去重合并，排除退市股（名称含"退"）
    5. Tushare L2 增强（有 Token 时：ST/市场/股价/流通市值）
    6. 计算市场宽度指标（含封板质量 / 市场分布）
    """
    scan_date = datetime.now().strftime("%Y%m%d")

    if not env.is_akshare_available() or not akshare_push2_available():
        return {
            "scan_date": scan_date,
            "trading_days_scanned": 0,
            "stocks": [],
            "market_breadth": _empty_breadth(),
            "errors": ["akshare / EastMoney push2 不可用"],
            "enrichment": {"tushare": False, "enriched_count": 0},
        }

    dates = get_trade_dates(days)
    errors: list[str] = []
    daily_data: dict[str, list[dict]] = {}
    aux_by_date: dict[str, dict[str, set[str]]] = {}

    for date in dates:
        records, error, aux, aux_errors = _fetch_day_with_aux(date)
        if error:
            errors.append(f"{date}: {error}")
            continue
        # M8: 无 error 的交易日（含涨停 0 只）计入
        daily_data[date] = records or []
        aux_by_date[date] = aux
        errors.extend(aux_errors)

    if not daily_data:
        return {
            "scan_date": scan_date,
            "trading_days_scanned": 0,
            "stocks": [],
            "market_breadth": _empty_breadth(),
            "errors": errors,
            "enrichment": {"tushare": False, "enriched_count": 0},
        }

    stocks, breadth = _merge_daily_results(daily_data, aux_by_date)
    trade_date = max(daily_data.keys())
    enrichment = _apply_tushare_enrich(stocks, trade_date=trade_date)
    # enrich 后补算 market_dist（依赖 L2 market）
    breadth = _compute_breadth(stocks, daily_data)
    return {
        "scan_date": scan_date,
        "trading_days_scanned": len(daily_data),
        "stocks": stocks,
        "market_breadth": breadth,
        "errors": errors,
        "enrichment": enrichment,
    }


def quality_filter(
    result: dict,
    *,
    min_consecutive: int = 0,
    sectors: list[str] | None = None,
    exclude_delisting: bool = True,
    max_break_count: int = 3,
    min_price: float = 5.0,
    min_float_mkt_cap: float = 20e8,
    exclude_st: bool = True,
    min_market_cap: float = 0,
    max_market_cap: float = float("inf"),
) -> dict:
    """六维质量过滤（执行计划 §2.2.2）。"""
    stocks = result.get("stocks", [])
    filtered: list[dict] = []
    reasons: dict[str, int] = Counter()
    mcap_constrained = min_market_cap > 0 or max_market_cap < float("inf")

    for s in stocks:
        name = str(s.get("name", ""))
        if exclude_delisting and "退" in name:
            reasons["delisting"] += 1
            continue
        if s.get("max_consecutive", 0) < min_consecutive:
            reasons["min_consecutive"] += 1
            continue
        if sectors and s.get("sector", "") not in sectors:
            reasons["sector"] += 1
            continue

        latest = _latest_appearance(s)
        break_count = int(latest.get("break_count", 0) or 0) if latest else 0
        if break_count >= max_break_count:
            reasons["max_break_count"] += 1
            continue

        # 总市值（L1）；M9: 有阈值但缺市值 → 剔除
        mcap = _latest_market_cap(s)
        if mcap_constrained and mcap is None:
            reasons["market_cap_unknown"] += 1
            continue
        if mcap is not None and (mcap < min_market_cap or mcap > max_market_cap):
            reasons["market_cap"] += 1
            continue

        if exclude_st and s.get("is_st") is True:
            reasons["exclude_st"] += 1
            continue

        close = s.get("close")
        if close is not None and min_price > 0 and close < min_price:
            reasons["min_price"] += 1
            continue

        float_mcap = s.get("float_mkt_cap")
        if float_mcap is None and latest:
            float_mcap = latest.get("float_mkt_cap")
        if float_mcap is not None and min_float_mkt_cap > 0 and float_mcap < min_float_mkt_cap:
            reasons["min_float_mkt_cap"] += 1
            continue

        filtered.append(s)

    # M1+#2: 按筛选集重算日频，保留原扫描交易日骨架（零日填 0）
    orig_daily = result.get("market_breadth", {}).get("daily_counts", {})
    daily = _daily_counts_from_stocks(filtered, calendar=orig_daily)
    return {
        "scan_date": result.get("scan_date", ""),
        "trading_days_scanned": result.get("trading_days_scanned", 0),
        "stocks": filtered,
        "market_breadth": _compute_breadth(filtered, daily),
        "errors": result.get("errors", []),
        "enrichment": result.get("enrichment", {}),
        "filter_stats": {
            "input_count": len(stocks),
            "output_count": len(filtered),
            "filtered_reasons": dict(reasons),
        },
        "quality_filter_applied": True,
    }


def filter_stocks(
    result: dict,
    *,
    min_consecutive: int = 0,
    sectors: list[str] | None = None,
    exclude_names_contain: list[str] | None = None,
    min_market_cap: float = 0,
    max_market_cap: float = float("inf"),
) -> dict:
    """从扫描结果筛选股票（兼容入口）。"""
    exclude_delisting = True
    if exclude_names_contain is not None:
        exclude_delisting = "退" in exclude_names_contain

    out = quality_filter(
        result,
        min_consecutive=min_consecutive,
        sectors=sectors,
        exclude_delisting=exclude_delisting,
        max_break_count=10**9,
        min_price=0,
        min_float_mkt_cap=0,
        exclude_st=False,
        min_market_cap=min_market_cap,
        max_market_cap=max_market_cap,
    )
    if exclude_names_contain:
        extra = [kw for kw in exclude_names_contain if kw != "退"]
        if extra:
            kept = []
            for s in out["stocks"]:
                name = str(s.get("name", ""))
                if any(kw in name for kw in extra):
                    continue
                kept.append(s)
            daily = _daily_counts_from_stocks(
                kept, calendar=result.get("market_breadth", {}).get("daily_counts", {}),
            )
            out["stocks"] = kept
            out["market_breadth"] = _compute_breadth(kept, daily)
            out["filter_stats"]["output_count"] = len(kept)
    return out


def format_market_brief(result: dict) -> str:
    """Markdown 市场宽度简报。"""
    b = result.get("market_breadth", {})
    stocks = result.get("stocks", [])
    errors = result.get("errors", [])
    enrichment = result.get("enrichment", {})

    lines = [
        f"## 涨停扫描 — {_fmt_date(result.get('scan_date', ''))}",
        "",
        f"扫描交易日: {result.get('trading_days_scanned', 0)} 天"
        f" | 有涨停日: {b.get('days_with_limit_ups', 0)} 天"
        f" | 去重标的: {b.get('total_unique_stocks', 0)} 只"
        f" | 日均涨停: {b.get('avg_daily_count', 0):.1f} 只",
        "",
    ]

    if enrichment:
        ts_ok = enrichment.get("tushare", False)
        lines.append(
            f"数据增强: Tushare L2 {'✅' if ts_ok else '❌（未启用/降级）'}"
            f" | 增强标的: {enrichment.get('enriched_count', 0)}"
        )
        lines.append("")

    if result.get("filter_stats"):
        fs = result.get("filter_stats") or {}
        mode = "质量过滤" if result.get("quality_filter_applied") else "轻量筛选（行业/连板）"
        lines.append(
            f"筛选: 已应用（{mode}）"
            f" | 输入 {fs.get('input_count', '?')} → 输出 {fs.get('output_count', len(stocks))}"
        )
        lines.append("")

    if errors:
        lines.append(f"⚠️ 采集错误: {len(errors)} 条")
        lines.append("")

    daily = b.get("daily_counts", {})
    if daily:
        lines.append("### 每日涨停趋势")
        lines.append("| 日期 | 涨停家数 |")
        lines.append("|------|----------|")
        for date, count in sorted(daily.items(), reverse=True):
            bar = "█" * min(int(count / 10), 15) if count else ""
            lines.append(f"| {_fmt_date(date)} | {bar} {count} |")
        lines.append("")

    consec = b.get("consecutive_dist", {})
    if consec:
        labels = ["1板", "2板", "3板", "4板+"]
        vals = [str(consec.get(k, 0)) for k in labels]
        lines.append("### 连板分布")
        lines.append("| " + " | ".join(labels) + " |")
        lines.append("|" + "|".join(["-----"] * len(labels)) + "|")
        lines.append("| " + " | ".join(vals) + " |")
        lines.append("")

    market_dist = b.get("market_dist", {})
    if market_dist:
        labels = ["主板", "创业板", "科创板", "北交所", "未知"]
        present = [k for k in labels if market_dist.get(k, 0)]
        extra = [k for k in market_dist if k not in labels]
        cols = present + extra
        if cols:
            lines.append("### 市场分布")
            lines.append("| " + " | ".join(cols) + " |")
            lines.append("|" + "|".join(["-----"] * len(cols)) + "|")
            lines.append("| " + " | ".join(str(market_dist.get(k, 0)) for k in cols) + " |")
            lines.append("")

    seal = b.get("seal_quality", {})
    if seal:
        lines.append("### 封板质量")
        lines.append("| 指标 | 值 |")
        lines.append("|------|-----|")
        lines.append(f"| 早盘封板率(<09:45) | {seal.get('early_seal_rate', 0):.0%} |")
        lines.append(f"| 封流比>5%比例 | {seal.get('seal_flow_gt_5pct', 0):.0%} |")
        lines.append(f"| 平均炸板次数 | {seal.get('avg_break_count', 0):.1f} |")
        lines.append(f"| 一进二晋级率 | {seal.get('one_to_two_rate', 0):.0%} |")
        lines.append("")

    sector_dist = b.get("sector_dist", [])
    if sector_dist:
        lines.append("### 行业热度 Top 10")
        lines.append("| 行业 | 涨停标的数 |")
        lines.append("|------|-----------|")
        total = max(b.get("total_unique_stocks", 1), 1)
        for sector, count in sector_dist[:10]:
            lines.append(f"| {sector} | {count} ({count/total*100:.1f}%) |")
        lines.append("")

    leaders = [s for s in stocks if s.get("max_consecutive", 0) >= 3]
    if leaders:
        leaders.sort(key=lambda s: s.get("max_consecutive", 0), reverse=True)
        lines.append("### 连板龙头（≥3板）")
        lines.append("| 代码 | 名称 | 连板 | 行业 | 最新封板 |")
        lines.append("|------|------|------|------|---------|")
        for s in leaders[:10]:
            latest = _latest_appearance(s) or {}
            lines.append(
                f"| {s['symbol']} | {s['name']} | {s['max_consecutive']} | "
                f"{s.get('sector', '-')} | {latest.get('seal_time', '-')} |"
            )
        lines.append("")

    return "\n".join(lines)


def format_stock_table(stocks: list[dict], max_rows: int = 80) -> str:
    """涨停股票列表 Markdown 表格。"""
    sorted_stocks = sorted(
        stocks,
        key=lambda s: (s.get("max_consecutive", 0), s.get("total_appearances", 0)),
        reverse=True,
    )
    display = sorted_stocks[:max_rows]

    lines = [
        "### 涨停股票列表",
        "| 代码 | 名称 | 最大连板 | 出现天数 | 行业 | 市值(亿) | 换手% | 封板 | 炸板 |",
        "|------|------|----------|----------|------|----------|-------|------|------|",
    ]

    for s in display:
        latest = _latest_appearance(s) or {}
        mcap = _fmt_yi(latest.get("market_cap"))
        turnover = f"{latest.get('turnover', 0):.1f}"
        lines.append(
            f"| {s['symbol']} | {s['name']} | {s.get('max_consecutive', 0)} | "
            f"{s.get('total_appearances', 0)} | {s.get('sector', '-')} | "
            f"{mcap} | {turnover} | {latest.get('seal_time', '-')} | "
            f"{latest.get('break_count', 0)} |"
        )

    if len(stocks) > max_rows:
        lines.append(f"\n> 显示前 {max_rows} 只，共 {len(stocks)} 只。使用 --sector/--min-board 缩小范围。")

    return "\n".join(lines)


# ---- 内部函数 ----


def _get_trade_dates(days: int) -> list[str]:
    """兼容旧测试：委托 tushare_enrich.get_trade_dates。"""
    return get_trade_dates(days)


def _apply_tushare_enrich(stocks: list[dict], trade_date: str) -> dict:
    """将 Tushare L2 字段挂到 stocks；无 Token 时静默跳过。"""
    if not stocks:
        return {"tushare": False, "enriched_count": 0}

    symbols = [s["symbol"] for s in stocks]
    info = enrich_stock_info(symbols)
    prices = enrich_price_data(symbols, trade_date)
    if not info and not prices:
        return {"tushare": False, "enriched_count": 0}

    count = 0
    for s in stocks:
        sym = s["symbol"]
        changed = False
        if sym in info:
            s["is_st"] = info[sym].get("is_st", False)
            s["market"] = info[sym].get("market", "")
            s["list_date"] = info[sym].get("list_date", "")
            changed = True
        if sym in prices:
            p = prices[sym]
            # #3: 用键存在判断，避免 close/amount=0 被真值判断丢弃
            if "close" in p and p["close"] is not None:
                s["close"] = p["close"]
            if "amount" in p and p["amount"] is not None:
                s["amount"] = p["amount"]
            if "float_mkt_cap" in p and p["float_mkt_cap"] is not None:
                s["float_mkt_cap"] = p["float_mkt_cap"]
            changed = True
        if changed:
            count += 1
    return {"tushare": True, "enriched_count": count}


def _fetch_day_with_aux(
    date: str,
) -> tuple[list[dict] | None, str | None, dict[str, set[str]], list[str]]:
    """主池 + 辅池。返回 (records, error, aux_sets, aux_errors)。"""
    aux: dict[str, set[str]] = {
        "strong": set(),
        "previous": set(),
        "zbgc": set(),
    }
    aux_errors: list[str] = []
    try:
        with akshare_direct_session():
            import akshare as ak

            df = ak.stock_zt_pool_em(date)
            records = None if df is None or len(df) == 0 else df.to_dict("records")

            # #5: 主池为空日跳过辅池，避免 3×N 无效 EastMoney 请求
            if records:
                for key, fn_name in (
                    ("strong", "stock_zt_pool_strong_em"),
                    ("previous", "stock_zt_pool_previous_em"),
                    ("zbgc", "stock_zt_pool_zbgc_em"),
                ):
                    try:
                        fn = getattr(ak, fn_name, None)
                        if fn is None:
                            aux_errors.append(f"{date}: {fn_name} 不可用")
                            continue
                        adf = fn(date)
                        if adf is None or len(adf) == 0:
                            continue
                        for row in adf.to_dict("records"):
                            sym = str(row.get("代码", "") or "")
                            if sym:
                                aux[key].add(sym)
                    except Exception as e:
                        logger.warning("%s(%s) 失败: %s", fn_name, date, e)
                        aux_errors.append(f"{date}: {fn_name} {e}")

            return records, None, aux, aux_errors
    except Exception as e:
        logger.warning("stock_zt_pool_em(%s) 失败: %s", date, e)
        return None, str(e), aux, aux_errors


def _merge_daily_results(
    daily_data: dict[str, list[dict]],
    aux_by_date: dict[str, dict[str, set[str]]] | None = None,
) -> tuple[list[dict], dict]:
    """跨日去重合并 + 市场宽度统计。"""
    aux_by_date = aux_by_date or {}
    by_symbol: dict[str, dict] = {}

    for date, records in daily_data.items():
        aux = aux_by_date.get(date, {})
        for r in records:
            sym = str(r.get("代码", ""))
            if not sym:
                continue
            name = str(r.get("名称", ""))
            if "退" in name:
                continue

            if sym not in by_symbol:
                by_symbol[sym] = {
                    "symbol": sym,
                    "name": name,
                    "appearances": [],
                    "sector": "",
                    "flags": {"in_strong": False, "in_previous": False, "in_zbgc": False},
                }

            float_mcap = _safe_float(r.get("流通市值"))
            in_strong = sym in aux.get("strong", set())
            in_previous = sym in aux.get("previous", set())
            in_zbgc = sym in aux.get("zbgc", set())
            by_symbol[sym]["appearances"].append({
                "date": date,
                "consecutive": int(r.get("连板数", 0) or 0),
                "seal_time": str(r.get("首次封板时间", "")),
                "seal_amount": _safe_float(r.get("封板资金")),
                "break_count": int(r.get("炸板次数", 0) or 0),
                "turnover": _safe_float(r.get("换手率")),
                "market_cap": _safe_float(r.get("总市值")),
                "float_mkt_cap": float_mcap if float_mcap else None,
                "change_pct": _safe_float(r.get("涨跌幅")),
                "stat": str(r.get("涨停统计", "")),
                "in_strong": in_strong,
                "in_previous": in_previous,
                "in_zbgc": in_zbgc,
            })
            flags = by_symbol[sym]["flags"]
            flags["in_strong"] = flags["in_strong"] or in_strong
            flags["in_previous"] = flags["in_previous"] or in_previous
            flags["in_zbgc"] = flags["in_zbgc"] or in_zbgc
            sector = str(r.get("所属行业", ""))
            if sector:
                by_symbol[sym]["sector"] = sector

    stocks = []
    for sym, info in by_symbol.items():
        apps = sorted(info["appearances"], key=lambda a: a["date"])
        info["appearances"] = apps
        info["max_consecutive"] = max((a["consecutive"] for a in apps), default=0)
        info["total_appearances"] = len(apps)
        info["first_date"] = apps[0]["date"] if apps else ""
        info["last_date"] = apps[-1]["date"] if apps else ""
        latest_float = apps[-1].get("float_mkt_cap") if apps else None
        if latest_float:
            info["float_mkt_cap"] = latest_float
        stocks.append(info)

    return stocks, _compute_breadth(stocks, daily_data)


def _daily_counts_from_stocks(
    stocks: list[dict],
    calendar: dict[str, Any] | list[str] | None = None,
) -> dict[str, int]:
    """按筛选集 appearances 重算每日涨停家数。

    calendar: 原扫描交易日骨架（dict keys 或 list）；提供时对缺失日填 0，
    避免筛选后丢掉零涨停日导致日均偏高。
    """
    counts: Counter[str] = Counter()
    for s in stocks:
        for a in s.get("appearances", []):
            d = a.get("date")
            if d:
                counts[d] += 1
    if calendar is None:
        return dict(counts)
    if isinstance(calendar, dict):
        keys = list(calendar.keys())
    else:
        keys = list(calendar)
    # 保持原顺序；补上筛选集多出的日期
    out = {d: int(counts.get(d, 0)) for d in keys}
    for d, c in counts.items():
        if d not in out:
            out[d] = c
    return out


def _normalize_seal_time(raw: str) -> str:
    """归一化为 HHMMSS 便于比较。"""
    digits = re.sub(r"\D", "", str(raw or ""))
    if len(digits) >= 6:
        return digits[:6]
    if len(digits) == 5:
        return digits.zfill(6)
    if len(digits) == 4:
        return digits + "00"
    return digits


def _one_to_two_rate(
    stocks: list[dict],
    trade_dates: list[str] | None = None,
) -> float:
    """一进二晋级率：交易日历上相邻日，前日连板=1 且次日连板≥2。

    trade_dates: 完整扫描交易日（含零涨停日）。缺省时回退到 appearances 日期
    （可能把不相邻交易日误判为相邻，仅作兜底）。
    """
    by_date: dict[str, dict[str, int]] = {}
    for s in stocks:
        sym = s["symbol"]
        for a in s.get("appearances", []):
            d = a.get("date")
            if not d:
                continue
            by_date.setdefault(d, {})[sym] = int(a.get("consecutive", 0) or 0)

    dates = sorted(trade_dates) if trade_dates else sorted(by_date.keys())
    if len(dates) < 2:
        return 0.0

    promoted = 0
    base = 0
    for i in range(len(dates) - 1):
        d0, d1 = dates[i], dates[i + 1]
        first_board = {sym for sym, c in by_date.get(d0, {}).items() if c == 1}
        if not first_board:
            continue
        base += len(first_board)
        next_map = by_date.get(d1, {})
        for sym in first_board:
            if next_map.get(sym, 0) >= 2:
                promoted += 1
    return round(promoted / base, 4) if base else 0.0


def _compute_seal_quality(
    stocks: list[dict],
    trade_dates: list[str] | None = None,
) -> dict:
    """封板质量指标（基于各股最新 appearance）。"""
    early = 0
    seal_n = 0
    flow_ok = 0
    flow_n = 0
    breaks: list[int] = []

    for s in stocks:
        latest = _latest_appearance(s)
        if not latest:
            continue
        seal_n += 1
        st = _normalize_seal_time(latest.get("seal_time", ""))
        if st and st < _EARLY_SEAL_CUTOFF:
            early += 1
        breaks.append(int(latest.get("break_count", 0) or 0))

        float_mcap = s.get("float_mkt_cap") or latest.get("float_mkt_cap")
        seal_amt = _safe_float(latest.get("seal_amount"))
        if float_mcap and float_mcap > 0 and seal_amt > 0:
            flow_n += 1
            if seal_amt / float_mcap > 0.05:
                flow_ok += 1

    return {
        "early_seal_rate": round(early / seal_n, 4) if seal_n else 0.0,
        "seal_flow_gt_5pct": round(flow_ok / flow_n, 4) if flow_n else 0.0,
        "avg_break_count": round(sum(breaks) / len(breaks), 2) if breaks else 0.0,
        "one_to_two_rate": _one_to_two_rate(stocks, trade_dates),
    }


def _compute_breadth(stocks: list[dict], daily_data: dict[str, Any]) -> dict:
    """计算市场宽度指标。daily_data 值为 list[dict] 或 int 均可。"""
    daily_counts: dict[str, int] = {}
    for date, val in daily_data.items():
        if isinstance(val, int):
            daily_counts[date] = val
        elif isinstance(val, list):
            daily_counts[date] = len(val)
        else:
            daily_counts[date] = 0
    total = len(stocks)
    daily_vals = list(daily_counts.values())
    avg = sum(daily_vals) / len(daily_vals) if daily_vals else 0.0
    days_with = sum(1 for v in daily_vals if v > 0)

    consec: Counter[str] = Counter()
    for s in stocks:
        mc = int(s.get("max_consecutive", 0) or 0)
        # #6: max_consecutive<=0 不计入「1板」
        if mc <= 0:
            continue
        if mc >= 4:
            consec["4板+"] += 1
        elif mc == 3:
            consec["3板"] += 1
        elif mc == 2:
            consec["2板"] += 1
        elif mc == 1:
            consec["1板"] += 1

    sector_counter: Counter[str] = Counter()
    market_counter: Counter[str] = Counter()
    for s in stocks:
        sec = s.get("sector", "")
        if sec:
            sector_counter[sec] += 1
        market = s.get("market") or "未知"
        market_counter[market] += 1

    trade_dates = sorted(daily_counts.keys())
    return {
        "daily_counts": daily_counts,
        "days_with_limit_ups": days_with,
        "total_unique_stocks": total,
        "avg_daily_count": round(avg, 1),
        "consecutive_dist": dict(consec),
        "sector_dist": sector_counter.most_common(15),
        "market_dist": dict(market_counter),
        "seal_quality": _compute_seal_quality(stocks, trade_dates),
    }


def _empty_breadth() -> dict:
    return {
        "daily_counts": {},
        "days_with_limit_ups": 0,
        "total_unique_stocks": 0,
        "avg_daily_count": 0,
        "consecutive_dist": {},
        "sector_dist": [],
        "market_dist": {},
        "seal_quality": {
            "early_seal_rate": 0.0,
            "seal_flow_gt_5pct": 0.0,
            "avg_break_count": 0.0,
            "one_to_two_rate": 0.0,
        },
    }


def _latest_appearance(stock: dict) -> dict | None:
    apps = stock.get("appearances", [])
    return apps[-1] if apps else None


def _latest_market_cap(stock: dict) -> float | None:
    latest = _latest_appearance(stock)
    if not latest:
        return None
    mcap = latest.get("market_cap")
    if mcap is None:
        return None
    try:
        v = float(mcap)
        return v if v > 0 else None
    except (TypeError, ValueError):
        return None


def _safe_float(val: Any) -> float:
    if val is None:
        return 0.0
    try:
        f = float(val)
        if math.isnan(f):
            return 0.0
        return f
    except (TypeError, ValueError):
        return 0.0


def _fmt_yi(val: Any) -> str:
    v = _safe_float(val)
    return f"{v / 1e8:.1f}" if v != 0 else "-"


def _fmt_date(yyyymmdd: str) -> str:
    if len(yyyymmdd) == 8:
        return f"{yyyymmdd[:4]}-{yyyymmdd[4:6]}-{yyyymmdd[6:8]}"
    return yyyymmdd
