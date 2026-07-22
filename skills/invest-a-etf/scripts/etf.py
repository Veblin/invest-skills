#!/usr/bin/env python3
"""invest-a-etf — ETF 研究数据 CLI.

用法::

    uv run python skills/invest-a-etf/scripts/etf.py report 563300
    uv run python skills/invest-a-etf/scripts/etf.py report 563300 --json
    uv run python skills/invest-a-etf/scripts/etf.py diagnose
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

_LIB_DIR = Path(__file__).resolve().parent / "lib"
if str(_LIB_DIR) not in sys.path:
    sys.path.insert(0, str(_LIB_DIR))

from etf_data import (  # noqa: E402
    CSINDEX_MAP,
    ETF_HEDGE_MAP,
    prefetch_etf_spot,
    query_etf_data,
    query_etf_kline,
    query_etf_quote,
)


def _kline_summary(kline: dict) -> dict:
    """Drop bulky nav_history for default stdout."""
    return {k: v for k, v in kline.items() if k != "nav_history"}


def cmd_report(symbol: str, *, as_json: bool, with_nav: bool) -> int:
    symbol = symbol.strip()
    if not symbol.isdigit() or len(symbol) != 6:
        print(f"错误: 需要 6 位数字代码，收到 {symbol!r}", file=sys.stderr)
        return 2

    prefetch_etf_spot()
    profile = query_etf_data(symbol)
    quote = query_etf_quote(symbol)
    kline = query_etf_kline(symbol)

    payload = {
        "skill": "invest-a-etf",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "symbol": symbol,
        "index_code": CSINDEX_MAP.get(symbol),
        "profile": profile,
        "quote": quote,
        "kline": kline if with_nav else _kline_summary(kline),
        "disclaimer": "研究数据快照，不构成投资建议。",
    }

    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
        return 0

    # Compact human-readable summary for Claude / terminal
    print(f"# invest-a-etf report · {symbol}")
    print(f"generated_at: {payload['generated_at']}")
    print()
    hc = profile.get("hedge_coverage") or {}
    print("## profile")
    print(f"  index_pe:          {profile.get('index_pe')}")
    print(f"  index_pe_status:   {profile.get('index_pe_status')}")
    print(f"  premium_discount:  {profile.get('premium_discount')}")
    print(f"  aum_yi:            {profile.get('aum')}")
    print(f"  hedge_coverage:    {hc.get('coverage')} ({hc.get('index')})")
    print(f"  flags:             {profile.get('flags')}")
    if profile.get("_errors"):
        print(f"  errors:            {profile['_errors']}")
    print()
    print("## quote")
    print(f"  price:             {quote.get('price')}  status={quote.get('status')}")
    print(f"  change_pct:        {quote.get('change_pct')}")
    print(f"  amount:            {quote.get('amount')}")
    print()
    print("## kline")
    print(f"  nav_rows:          {kline.get('nav_rows')}  status={kline.get('status')}")
    print(f"  latest_nav:        {kline.get('latest_nav')}")
    print(f"  vol_ann_%:         {kline.get('volatility_annualized')}")
    print(f"  ma20/ma60:         {kline.get('ma20')} / {kline.get('ma60')}")
    print()
    print("> 完整叙事请按 skills/invest-a-etf/references/report-template.md 合成。")
    print("> ⚠️ 不构成投资建议。")
    return 0


def cmd_diagnose() -> int:
    print("invest-a-etf diagnose")
    print(f"  ETF_HEDGE_MAP entries: {len(ETF_HEDGE_MAP)}")
    print(f"  CSINDEX_MAP entries:   {len(CSINDEX_MAP)}")
    try:
        import akshare as ak  # noqa: F401

        print("  akshare:              OK")
    except Exception as exc:
        print(f"  akshare:              FAIL ({exc})")
        return 1
    # smoke: known code in map
    sample = "510300"
    print(f"  sample hedge[{sample}]: {ETF_HEDGE_MAP.get(sample)}")
    print("diagnose: OK")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="etf.py", description="invest-a-etf CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_report = sub.add_parser("report", help="采集 ETF 数据快照")
    p_report.add_argument("symbol", help="6 位 ETF 代码")
    p_report.add_argument("--json", action="store_true", help="输出完整 JSON")
    p_report.add_argument(
        "--with-nav",
        action="store_true",
        help="JSON/摘要中保留完整净值历史（默认摘要省略）",
    )

    sub.add_parser("diagnose", help="检查依赖与映射表")

    args = parser.parse_args(argv)
    if args.cmd == "report":
        return cmd_report(args.symbol, as_json=args.json, with_nav=args.with_nav)
    if args.cmd == "diagnose":
        return cmd_diagnose()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
