#!/usr/bin/env python3
"""invest-a-gap-scan -- 跳空缺口扫描 CLI.

用法::

    uv run python skills/invest-a-gap-scan/scripts/scan.py
    uv run python skills/invest-a-gap-scan/scripts/scan.py --gap-min-pct 2.0
    uv run python skills/invest-a-gap-scan/scripts/scan.py --source baostock
    uv run python skills/invest-a-gap-scan/scripts/scan.py --universe-limit 30
    uv run python skills/invest-a-gap-scan/scripts/scan.py --json

数据流
------
1. 构建指数成分股并集 (build_universe)
2. 创建数据源 (create_source) — Tushare 批量或 baostock 兜底
3. 获取交易日历 (Tushare trade_cal / 兜底估算)
4. 尝试 K 线缓存；未命中则批量拉取日线 + adj_factor
5. 逐只构建前复权 K 线 (build_stock_kline) 并写缓存
6. 停牌检测 (detect_suspensions)
7. 缺口扫描 (scan_all)
8. 输出简报/详文档
"""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

# ---- 将 scripts/lib/ 加入 sys.path，使同目录模块可直接导入 ----
_LIB_DIR = Path(__file__).resolve().parent / "lib"
if str(_LIB_DIR) not in sys.path:
    sys.path.insert(0, str(_LIB_DIR))

# ---- 跨 skill 导入 invest-a-stock 基础设施 ----
from _invest_path import ensure_invest_a_scripts_on_path  # noqa: E402

ensure_invest_a_scripts_on_path()

from dates import shanghai_now  # noqa: E402
from lib import env  # noqa: E402

# ---- 本 skill 的 lib 模块（top-level import 通过 _LIB_DIR） ----
from gap_scanner import scan_all  # noqa: E402
from report_formatter import (  # noqa: E402
    format_brief,
    format_json,
    format_markdown_report,
)
from suspension import detect_suspensions  # noqa: E402
from kline_cache import KlineTTLCache  # noqa: E402（canonical: skills/lib）

_KLINE_CACHE = KlineTTLCache(lambda: env.STORE_DIR / "gap_scan_cache", 3 * 86400)

# 固定缓存键首段（v0.2.5）：文件级 mtime TTL（3 天）据此真正生效。旧实现把当日
# 日期作键首段，次日查询新日期目录必然 miss，TTL 退化为"当日有效"（缺陷修复）。
_CACHE_DATE_SEGMENT = "kline"


def _cache_parts(ts_code: str, source_name: str | None) -> tuple[str, ...]:
    """缓存键：{source}/{ts_code}（无源时仅 {ts_code}）— 与旧 kline_cache 键布局一致。"""
    return (source_name, ts_code) if source_name else (ts_code,)


def _prune_legacy_date_dirs() -> None:
    """一次性迁移：删除旧版 {YYYYMMDD} 日期目录。

    不能复用 KlineTTLCache.cleanup_old()：固定段键下 "kline" 目录的文件被原地
    覆写（open(path, "wb")）时不更新目录 mtime，cleanup_old() 会按目录 mtime
    误删仍新鲜的数据（约每 4 天一次全量重拉）。旧版日期目录的数据对新布局恒为
    死数据，直接删除；"kline" 目录内文件按 mtime 自过期，磁盘占用有界。
    """
    root = env.STORE_DIR / "gap_scan_cache"
    if not root.exists():
        return
    for entry in root.iterdir():
        if entry.is_dir() and entry.name.isdigit() and len(entry.name) == 8:
            shutil.rmtree(entry, ignore_errors=True)


def _report_path_for_now(now: datetime | None = None) -> Path:
    """详文档路径：reports/gap-scan/{YYYY-MM-DD-HH-MM-SS}.md（report-conventions.md §1.2）。

    汇总式扫描无单一 {symbol}-{name}，目录保留 gap-scan；文件名必须带
    实际北京时间（含时分秒）——曾用 %Y%m%d 无时分秒导致同日二次运行覆盖历史。
    """
    stamp = (now or shanghai_now()).strftime("%Y-%m-%d-%H-%M-%S")
    return Path("reports/gap-scan") / f"{stamp}.md"

# ---- 由并行代理创建的模块 ----
try:
    from universe import build_universe  # noqa: E402
    from kline_source import (  # noqa: E402
        BaostockSource,
        create_source,
        build_stock_kline,
        group_daily_by_ts_code,
    )
except ImportError:
    print(
        "错误: universe.py 或 kline_source.py 尚未创建。\n"
        "请确保 scripts/lib/ 下存在这两个模块（由并行代理创建）。",
        file=sys.stderr,
    )
    sys.exit(2)

logger = logging.getLogger(__name__)


# ======================================================================
# CLI 参数
# ======================================================================


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="跳空缺口扫描 — 向上缺口 + MA60 上方 + 未回补",
    )
    p.add_argument(
        "--universe", default="csi300,a500,star50",
        help="指数池，逗号分隔（默认 csi300,a500,star50）",
    )
    p.add_argument(
        "--gap-min-pct", type=float, default=1.0,
        help="缺口幅度阈值 %%（默认 1.0）",
    )
    p.add_argument(
        "--gap-lookback", type=int, default=60,
        help="缺口回溯交易日数（默认 60）",
    )
    p.add_argument(
        "--gap-min-vol-ratio", type=float, default=1.0,
        help="缺口日成交额 / 20 日均额下限（默认 1.0 = 不过滤）",
    )
    p.add_argument(
        "--min-avg-amount", type=int, default=100_000_000,
        help="20 日均额门槛（元，默认 100000000 = 1 亿）",
    )
    p.add_argument(
        "--min-list-days", type=int, default=60,
        help="最少 K 线根数（默认 60）",
    )
    p.add_argument(
        "--top", type=int, default=30,
        help="stdout 行数（默认 30；md 报告全量）",
    )
    p.add_argument(
        "--source", choices=["auto", "tushare", "baostock"], default="auto",
        help="数据源（默认 auto：有 token → tushare，无 → baostock）",
    )
    p.add_argument(
        "--no-cache", action="store_true",
        help="强制刷新（含成分股缓存与 K 线缓存）",
    )
    p.add_argument(
        "--universe-limit", type=int, default=None,
        help="只扫前 N 只（开发调试用）",
    )
    p.add_argument(
        "--no-save-report", action="store_true",
        help="不写入 reports/gap-scan/ 目录",
    )
    p.add_argument(
        "--json", action="store_true",
        help="stdout 输出 JSON 格式",
    )
    p.add_argument(
        "--record", action="store_true",
        help="命中追加到 reports/gap-backtest/hits.jsonl（W2/M2 前瞻回测状态）",
    )
    return p


# ======================================================================
# 数据流水线
# ======================================================================


_SESSION_CLOSE = datetime.strptime("15:00", "%H:%M").time()


def _prev_close_date(now: datetime) -> str:
    """now 之前最近已收盘交易日（A 股收盘 15:00 后数据才含当日完整 bar）。

    code-review #2：--record 的数据日期语义——盘前/盘中运行扫描数据只到
    前一交易日（当日最后 bar 未确认、缺口不可判定），必须回拨。
    """
    from trade_cal import prev_trading_day  # noqa: PLC0415

    return prev_trading_day(now.date()).strftime("%Y%m%d")


def _fetch_trade_cal(start_date: str, end_date: str) -> tuple[list[str], bool]:
    """获取交易日列表（C8 收敛：委托 skills/lib/trade_cal.fetch_trade_cal）。"""
    from trade_cal import fetch_trade_cal as _fetch

    return _fetch(start_date, end_date)


def _build_daily_by_date(
    daily_raw: pd.DataFrame,
) -> dict[str, set[str]]:
    """从批量日线 DataFrame 构建按日索引的 ts_code 集合。"""
    if daily_raw is None or daily_raw.empty:
        return {}
    daily_by_date: dict[str, set[str]] = {}
    for trade_date, group in daily_raw.groupby("trade_date", sort=False):
        date = str(trade_date)
        codes = {str(c) for c in group["ts_code"].tolist() if c}
        if date and codes:
            daily_by_date[date] = codes
    return daily_by_date


def _daily_by_date_from_klines(
    stock_kline_map: dict[str, pd.DataFrame],
) -> dict[str, set[str]]:
    """Rebuild daily_by_date from cached per-stock klines."""
    daily_by_date: dict[str, set[str]] = {}
    for ts_code, kline in stock_kline_map.items():
        if kline is None or kline.empty or "trade_date" not in kline.columns:
            continue
        for date in kline["trade_date"].astype(str):
            daily_by_date.setdefault(date, set()).add(ts_code)
    return daily_by_date


def _split_adj_factor_map(
    adj_all: pd.DataFrame,
    ts_codes: list[str],
) -> dict[str, pd.DataFrame | None]:
    """Split bulk adj_factor DataFrame into per-stock map."""
    adj_factor_map: dict[str, pd.DataFrame | None] = {c: None for c in ts_codes}
    if adj_all is None or adj_all.empty or "ts_code" not in adj_all.columns:
        return adj_factor_map
    for ts_code, group in adj_all.groupby("ts_code", sort=False):
        code = str(ts_code)
        if code not in adj_factor_map:
            continue
        sub = group[["trade_date", "adj_factor"]].copy()
        adj_factor_map[code] = sub if not sub.empty else None
    return adj_factor_map


# ======================================================================
# 主入口
# ======================================================================


def main() -> int:
    args = build_parser().parse_args()

    # 配置日志
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stderr,
    )

    start_wall = time.time()
    logger.info("开始扫描 (universe=%s)", args.universe)

    # 一次性迁移：清理旧版 {YYYYMMDD} 日期目录（v0.2.5 起键为固定段 kline/）。
    # 不复用 cleanup_old()：目录 mtime 不随文件原地覆写更新，会误删新鲜数据。
    try:
        _prune_legacy_date_dirs()
    except Exception as exc:
        logger.warning("kline_cache legacy dir pruning failed: %s", exc)

    # ---- Step 1: 构建成分股并集 ----
    try:
        stocks = build_universe(
            indices=[i.strip() for i in args.universe.split(",") if i.strip()],
            force_refresh=args.no_cache,
            universe_limit=args.universe_limit,
        )
    except Exception as exc:
        logger.error("构建成分股失败: %s", exc)
        return 1

    if not stocks:
        logger.warning("成分股列表为空，退出")
        return 1

    universe_ts_codes = {s.ts_code for s in stocks}
    logger.info("成分股: %d 只 (去重)", len(universe_ts_codes))

    # ---- Step 2: 创建数据源 ----
    source = create_source(args.source, ts_codes=list(universe_ts_codes))
    source_label = {
        "tushare": "Tushare Pro (前复权自算)",
        "baostock": "baostock (前复权 adjustflag=2)",
    }.get(source.source_name(), source.source_name())
    logger.info("数据源: %s", source_label)
    already_qfq = source.source_name() == "baostock"

    try:
        return _run_scan(args, stocks, universe_ts_codes, source, source_label, already_qfq, start_wall)
    finally:
        if hasattr(source, "cleanup"):
            try:
                source.cleanup()
            except Exception as exc:
                logger.warning("source.cleanup failed: %s", exc)


def _run_scan(
    args: argparse.Namespace,
    stocks: list,
    universe_ts_codes: set[str],
    source,
    source_label: str,
    already_qfq: bool,
    start_wall: float,
) -> int:
    # ---- Step 3: 交易日历 ----
    now = shanghai_now()
    end_date = now.strftime("%Y%m%d")
    # MA60 needs 59-bar warmup; to have valid MA60 for every bar in the
    # gap_lookback window we need ≥ gap_lookback + 59 trading bars.
    # ~1.46x calendar→trading conversion (365 / 250) + 5-day buffer.
    need_bars = args.gap_lookback + 59
    calendar_days = int(need_bars * 365 / 250) + 5
    start_date = (now - timedelta(days=calendar_days)).strftime("%Y%m%d")
    trade_dates, cal_is_estimated = _fetch_trade_cal(start_date, end_date)

    if not trade_dates:
        logger.error("交易日列表为空，无法继续")
        return 1

    trade_dates = sorted(td for td in trade_dates if td <= end_date)
    logger.info("交易日: %d 日 (%s ~ %s)", len(trade_dates), trade_dates[0], trade_dates[-1])

    # 固定段键：文件级 mtime TTL 跨日生效（旧实现按当日日期建键，次日必然 miss）
    cache_date = _CACHE_DATE_SEGMENT

    # ---- Step 4/5: 缓存 + 日线 / 前复权 ----
    stock_kline_map: dict[str, pd.DataFrame] = {}
    adj_factor_map: dict[str, pd.DataFrame | None] = {
        s.ts_code: None for s in stocks
    }
    cache_misses: list = []

    if not args.no_cache:
        for stock in stocks:
            cached = _KLINE_CACHE.load(cache_date,
                                       _cache_parts(stock.ts_code, source.source_name()))
            if cached is not None and not cached.empty:
                stock_kline_map[stock.ts_code] = cached
            else:
                cache_misses.append(stock)
    else:
        cache_misses = list(stocks)

    all_cache_hit = len(cache_misses) == 0 and len(stocks) > 0
    daily_raw: pd.DataFrame | None = None

    if all_cache_hit:
        logger.info("K线缓存全部命中: %d 只，跳过日线/adj 拉取", len(stock_kline_map))
        daily_by_date = _daily_by_date_from_klines(stock_kline_map)
    else:
        logger.info(
            "拉取日线中... (缓存命中 %d, 待拉 %d)",
            len(stock_kline_map),
            len(cache_misses),
        )
        # baostock: 只拉取缓存未命中的标的，避免全量重复拉取
        miss_codes = [s.ts_code for s in cache_misses]
        if isinstance(source, BaostockSource):
            source.set_ts_codes(miss_codes)
        try:
            daily_raw = source.fetch_daily_batch(trade_dates)
        finally:
            # 恢复完整 ts_codes（即使 fetch 失败也恢复，避免状态泄漏）
            if isinstance(source, BaostockSource):
                source.set_ts_codes(list(universe_ts_codes))

        if daily_raw is None or daily_raw.empty:
            if stock_kline_map:
                logger.warning("日线拉取失败，仅使用缓存 K 线继续")
                daily_by_date = _daily_by_date_from_klines(stock_kline_map)
            else:
                logger.error("未获取到任何日线数据")
                return 1
        else:
            daily_raw = daily_raw[daily_raw["ts_code"].isin(universe_ts_codes)].copy()
            logger.info(
                "日线拉取完成: %d 行, %d 只标的",
                len(daily_raw),
                daily_raw["ts_code"].nunique(),
            )
            daily_by_date = _build_daily_by_date(daily_raw)

            # Batch adj_factor (tushare); baostock returns empty
            if not already_qfq:
                adj_all = source.fetch_adj_factor_batch(trade_dates)
                # Restrict adj rows to universe when possible
                if adj_all is not None and not adj_all.empty and "ts_code" in adj_all.columns:
                    adj_all = adj_all[adj_all["ts_code"].isin(universe_ts_codes)]
                adj_factor_map = _split_adj_factor_map(
                    adj_all, [s.ts_code for s in stocks],
                )

            daily_by_ts = group_daily_by_ts_code(daily_raw)
            for stock in cache_misses:
                ts_code = stock.ts_code
                adj_df = adj_factor_map.get(ts_code)
                kline = build_stock_kline(
                    daily_raw,
                    adj_df,
                    ts_code,
                    min_bars=1,
                    already_qfq=already_qfq,
                    daily_by_ts=daily_by_ts,
                )
                if kline is not None:
                    stock_kline_map[ts_code] = kline
                    try:
                        _KLINE_CACHE.save(cache_date,
                                          _cache_parts(ts_code, source.source_name()),
                                          kline)
                    except Exception as exc:
                        logger.warning("cache save failed for %s: %s", ts_code, exc)

        # Build daily_by_date from stock_kline_map when available — it
        # covers both cached and freshly-fetched stocks.  Fall back to
        # daily_raw only when no klines were built at all.
        if stock_kline_map:
            daily_by_date = _daily_by_date_from_klines(stock_kline_map)
        elif daily_raw is not None and not daily_raw.empty:
            daily_by_date = _build_daily_by_date(daily_raw)

    logger.info(
        "K线构建完成: %d / %d 只成功",
        len(stock_kline_map),
        len(stocks),
    )

    # ---- Step 6: 停牌检测 ----
    if cal_is_estimated:
        logger.warning(
            "交易日历为自然日估算，跳过停牌检测（节假日可能被误判为停牌）"
        )
        suspension_map: dict[str, list[str]] = {}
    else:
        list_dates = {
            s.ts_code: getattr(s, "list_date", "") or ""
            for s in stocks
        }
        suspension_map = detect_suspensions(
            list(universe_ts_codes),
            daily_by_date,
            trade_dates,
            list_dates=list_dates,
        )
    logger.info("停牌检测: %d 只曾有停牌", len(suspension_map))

    # ---- Step 7: 缺口扫描 ----
    params: dict = {
        "gap_min_pct": args.gap_min_pct,
        "gap_lookback": args.gap_lookback,
        "gap_min_vol_ratio": args.gap_min_vol_ratio,
        "min_avg_amount": args.min_avg_amount,
        "min_list_days": args.min_list_days,
        "universe_str": args.universe,
        "source_label": source_label,
    }

    result = scan_all(
        stocks,
        stock_kline_map,
        adj_factor_map,
        suspension_map,
        params,
        trade_cal=trade_dates,
        already_qfq=already_qfq,
        # 收盘后（上海时间 ≥15:00）日线 bar 完整：最新 bar 缺口 low>gap_high
        # 可直接确认未回补，不再标 GAP_UNCONFIRMED 假阴性
        after_close=shanghai_now().hour >= 15,
    )

    elapsed = time.time() - start_wall
    logger.info(
        "扫描完成: 耗时 %.1fs | 命中 %d | 跨停牌 %d | 排除 %d | 未命中 %d",
        elapsed,
        len(result.hits),
        len(result.across_suspension_hits),
        sum(result.exclude_reasons.values()),
        sum(result.non_hit_reasons.values()),
    )

    # ---- Step 8: 输出 ----
    json_out = format_json(result) if args.json else None
    if args.json:
        print(json_out)
    else:
        print()
        print(format_brief(result, top_n=args.top))

    # W2/M2：--record → 命中落前瞻回测状态文件（幂等）
    if args.record:
        try:
            from record_hits import DEFAULT_STATE, record
            from dates import shanghai_now

            # code-review #2：扫描 bars 只到最近已收盘交易日（当日 15:00 前
            # 最后 bar 未确认、缺口不可判定）——记录日必须取**数据实际截止日**
            # （盘前/盘中 = 前一交易日），否则幂等键锁死跨会话错位的记录，
            # 且 eval 会以「信号发布时点不可观测的当日价」为进场锚。
            now = shanghai_now()
            data_date = now.strftime("%Y%m%d") \
                if now.time() >= _SESSION_CLOSE else _prev_close_date(now)
            added = record(json.loads(json_out or format_json(result)),
                           data_date, DEFAULT_STATE)
            logger.info("record_hits: %d 条新增（data_date=%s）→ %s",
                        added, data_date, DEFAULT_STATE)
        except Exception as exc:
            logger.warning("record_hits failed: %s", exc)

    # ---- Step 9: 保存详文档 ----
    if not args.no_save_report:
        report_path = _report_path_for_now()
        report_path.parent.mkdir(parents=True, exist_ok=True)
        format_markdown_report(result, str(report_path))
        print(f"\n详文档: {report_path.resolve()}", file=sys.stderr)

    # ---- Step 10: 退出码（覆盖率 = 有可用 K 线 / 池大小） ----
    coverage = result.total_with_kline / max(result.total_in_universe, 1)
    if coverage < 0.5:
        logger.warning("覆盖率 %.1f%% < 50%%，退出码=1", coverage * 100)
        return 1

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n扫描被用户中断", file=sys.stderr)
        sys.exit(130)
    except Exception as exc:
        logger.exception("扫描异常: %s", exc)
        sys.exit(1)
