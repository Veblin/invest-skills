#!/usr/bin/env python3
"""形态扫描 CLI — 双底/三角形底全市场检出（研究信号，非决策）。

用法:
  uv run python skills/invest-a-pattern-scan/scripts/scan.py              # 默认成分池 + 150 交易日
  uv run python skills/invest-a-pattern-scan/scripts/scan.py --universe csi300 --days 180
  uv run python skills/invest-a-pattern-scan/scripts/scan.py --out /tmp/patterns.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
_LIB_DIR = _SCRIPT_DIR / "lib"
for _p in (str(_LIB_DIR), str(_SCRIPT_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)
_ROOT = _SCRIPT_DIR.parent.parent.parent  # 仓库根（scripts → skill → skills → repo）
for _p in (str(_ROOT / "skills"), str(_ROOT / "skills" / "lib")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# 关键顺序：invest-a-stock/scripts 必须先入 path（universe.py 的
# `from lib import env` 依赖其 lib 包），随后由 ensure_* 插 0 位保证优先。
from invest_path import ensure_invest_a_scripts_on_path, ensure_shared_lib_on_path  # noqa: E402

ensure_invest_a_scripts_on_path()  # 插 0：`import lib` 命中 invest-a-stock
ensure_shared_lib_on_path()

from pattern_scanner import LOOKBACK_DAYS, reality_check_report, scan_universe  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="invest-a-pattern-scan 形态扫描")
    parser.add_argument("--universe", nargs="+", default=None, help="指数成分池（默认 csi300 a500 star50）")
    parser.add_argument("--days", type=int, default=LOOKBACK_DAYS, help="回看交易日数")
    parser.add_argument("--out", default=str(_ROOT / "docs" / "data" / "pattern_scan_result.json"))
    parser.add_argument("--json", action="store_true", help="stdout 输出完整 JSON")
    args = parser.parse_args()

    from trade_cal import fetch_trade_cal, last_trade_dates  # noqa: E402

    end = last_trade_dates(1)[0]
    from datetime import datetime as _dt
    from datetime import timedelta as _td

    start = (_dt.strptime(end, "%Y%m%d") - _td(days=int(args.days * 2))).strftime("%Y%m%d")
    dates, _ = fetch_trade_cal(start, end)
    dates = sorted(dates)[-args.days:]

    from invest_path import load_gap_scan_module  # noqa: E402

    universe_mod = load_gap_scan_module("universe")
    universe = universe_mod.build_universe(indices=list(args.universe)) if args.universe else universe_mod.build_universe()
    # build_universe 返回 list[StockInfo]（dataclass，ts_code 字段）
    ts_codes = [s.ts_code if hasattr(s, "ts_code") else s for s in universe]
    print(f"成分池: {len(ts_codes)} 只 | 交易日: {len(dates)} ({dates[0]} ~ {dates[-1]})", file=sys.stderr)

    hits, rule_matrix = scan_universe(ts_codes, dates)
    rc = reality_check_report(rule_matrix)

    result = {
        "meta": {"n_universe": len(ts_codes), "n_days": len(dates),
                 "date_range": [dates[0], dates[-1]],
                 "n_hits": len(hits), "n_rules": len(rule_matrix)},
        "reality_check": rc,
        "hits": [
            {
                "ts_code": h.ts_code, "pattern": h.pattern,
                "bandwidth": h.bandwidth, "endpoint_idx": h.endpoint_idx,
                "retest_status": h.retest_status, "retest_day": h.retest_day,
                **h.detail,
            }
            for h in hits
        ],
    }
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2, default=str)
    print(f"✅ 已写入 {out_path}")
    print(f"命中 {len(hits)} | RC p={rc.get('p_value')} best={rc.get('best_rule_name')} "
          f"({rc.get('n_rules')} 规则宇宙)")
    if args.json:
        print(json.dumps(result, ensure_ascii=False, default=str)[:4000])
    return 0


if __name__ == "__main__":
    sys.exit(main())
