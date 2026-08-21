#!/usr/bin/env python3
"""market_daily 全市场日线回填 CLI（v0.2.6 全市场分位数据层）。

用法:
  uv run python scripts/archive/backfill_market_daily.py                # 补最近 25 个交易日
  uv run python scripts/archive/backfill_market_daily.py --days 60      # 补最近 60 日
  uv run python scripts/archive/backfill_market_daily.py --until 20260814 --max-missing 100
  uv run python scripts/archive/backfill_market_daily.py --dry-run      # 只列缺日不拉取

配额：每交易日 2 次调用（daily + daily_basic），80/min 自节流；默认单次
最多补 25 日（50 调用，500 日配额内）。断点续跑：已入库日自动跳过。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
for _p in (
    str(_ROOT / "skills"),
    str(_ROOT / "skills" / "invest-a-stock" / "scripts"),
):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from lib import store  # noqa: E402
from lib.market_daily import ensure_market_daily  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="market_daily 全市场日线回填")
    parser.add_argument("--until", default=None, help="回填至该日（YYYYMMDD），默认最近交易日")
    parser.add_argument("--start", default=None, help="起始日（YYYYMMDD），全历史回填用；默认最近 30 个交易日")
    parser.add_argument("--max-missing", type=int, default=25, help="单次最多补 N 个缺失交易日")
    parser.add_argument("--dry-run", action="store_true", help="只列缺失日，不拉取")
    args = parser.parse_args()

    store.init_db()
    from lib.trade_cal import fetch_trade_cal, last_trade_dates

    if args.start:
        end = args.until or last_trade_dates(1)[0]
        trade_dates, _ = fetch_trade_cal(args.start, end)
    else:
        trade_dates = last_trade_dates(30)
    if args.until:
        trade_dates = [d for d in trade_dates if d <= args.until]
    existing = store.market_daily_dates()
    missing = [
        d for d in trade_dates
        if f"{d[:4]}-{d[4:6]}-{d[6:8]}" not in existing
    ][-args.max_missing:]
    print(f"最近 {len(trade_dates)} 个交易日 | 已入库 {len(existing)} 日 | 待补 {len(missing)} 日")
    if args.dry_run:
        print("缺失日:", missing)
        return 0
    result = ensure_market_daily(until_date=args.until, max_missing=args.max_missing, from_date=args.start)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 1 if result["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
