#!/usr/bin/env python3
"""limit-up Skill CLI — 全市场涨停扫描。

用法:
  uv run python skills/invest-a-limit-up/scripts/scan.py --days 10
  uv run python skills/invest-a-limit-up/scripts/scan.py --sector 半导体 --min-board 2
  uv run python skills/invest-a-limit-up/scripts/scan.py --quality-filter
  uv run python skills/invest-a-limit-up/scripts/scan.py --min-price 8   # 隐式启用质量过滤
  uv run python skills/invest-a-limit-up/scripts/scan.py --json --out /tmp/scan.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# 导入 scripts/lib/
_LIB_DIR = Path(__file__).resolve().parent / "lib"
if str(_LIB_DIR) not in sys.path:
    sys.path.insert(0, str(_LIB_DIR))

from limit_up_scanner import (
    format_market_brief,
    format_stock_table,
    quality_filter,
    scan_market,
)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="全市场涨停扫描（近N日涨停池 + 市场宽度）",
    )
    p.add_argument("--days", type=int, default=10,
                   help="扫描交易日范围（默认10；优先 Tushare 日历）")
    p.add_argument("--json", action="store_true",
                   help="JSON 输出（默认 Markdown 简报）")
    p.add_argument("--sector", default="",
                   help="按行业筛选（如 '半导体'）")
    p.add_argument("--min-board", type=int, default=0,
                   help="最低连板数筛选（0=全部）")
    p.add_argument("--quality-filter", action="store_true",
                   help="启用六维质量过滤（炸板/股价/流通市值/ST 等；默认关闭）")
    p.add_argument("--max-break", type=int, default=None,
                   help="最大炸板次数（传入即启用质量过滤；默认3）")
    p.add_argument("--min-price", type=float, default=None,
                   help="最低股价元（传入即启用质量过滤；默认5）")
    p.add_argument("--min-float-mkt-cap", type=float, default=None,
                   help="最低流通市值元（传入即启用质量过滤；默认20亿）")
    p.add_argument("--include-st", action="store_true",
                   help="质量过滤时保留 ST（需同时启用 --quality-filter 或其它质量参数）")
    p.add_argument("--max-rows", type=int, default=80,
                   help="股票列表最多显示行数（默认80）")
    p.add_argument("--out", default="",
                   help="保存 JSON 到指定路径（可选）")
    return p


def resolve_cli_filter(args: argparse.Namespace) -> dict | None:
    """根据 CLI 参数生成 quality_filter kwargs；无需筛选时返回 None。

    --include-st 仅修饰已启用的 full 模式，不单独触发质量过滤。
    """
    use_full = (
        args.quality_filter
        or any(v is not None for v in (args.max_break, args.min_price, args.min_float_mkt_cap))
    )
    if not (use_full or args.sector or args.min_board > 0):
        return None

    kwargs: dict = {
        "filter_mode": "full" if use_full else "lightweight",
        "sectors": [args.sector] if args.sector else None,
        "min_consecutive": args.min_board,
    }
    if use_full:
        kwargs.update({
            "max_break_count": args.max_break if args.max_break is not None else 3,
            "min_price": args.min_price if args.min_price is not None else 5.0,
            "min_float_mkt_cap": (
                args.min_float_mkt_cap if args.min_float_mkt_cap is not None else 20e8
            ),
            "exclude_st": not args.include_st,
        })
    return kwargs


def main() -> int:
    args = build_parser().parse_args()

    result = scan_market(days=args.days)

    use_full = (
        args.quality_filter
        or any(v is not None for v in (args.max_break, args.min_price, args.min_float_mkt_cap))
    )
    if use_full and not args.quality_filter:
        print(
            "ℹ️ 检测到质量过滤参数，已自动启用 --quality-filter",
            file=sys.stderr,
        )

    filter_kwargs = resolve_cli_filter(args)
    if filter_kwargs is not None:
        result = quality_filter(result, **filter_kwargs)

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2, default=str)
        print(f"📊 扫描结果已保存: {out_path.resolve()}", file=sys.stderr)

    if args.json:
        json.dump(result, sys.stdout, ensure_ascii=False, indent=2, default=str)
        print()
    else:
        print(format_market_brief(result))
        print()
        stocks = result.get("stocks", [])
        if stocks:
            print(format_stock_table(stocks, max_rows=args.max_rows))
        else:
            print("> 无涨停股票或所有标的已被筛选条件排除。")

    errors = result.get("errors", [])
    if errors:
        print(f"\n⚠️ 采集异常: {len(errors)} 条", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
