#!/usr/bin/env python3
"""futures_daily 股指期货回填 CLI（v0.2.6 F 系列数据层）。

用法:
  uv run python scripts/archive/backfill_futures_daily.py                 # 回填 2015-04 起当月合约
  uv run python scripts/archive/backfill_futures_daily.py --start 2022-07 # 仅 IM
  uv run python scripts/archive/backfill_futures_daily.py --max 10        # 单次最多 10 合约（测试用）
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]  # C9a 归档后多一层 archive/
for _p in (str(_ROOT / "skills"), str(_ROOT / "skills" / "invest-a-stock" / "scripts"), str(_ROOT / "skills" / "lib")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from lib import store  # noqa: E402
from lib.futures_data import ensure_futures_daily  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="futures_daily 股指期货回填")
    parser.add_argument("--start", default="2015-04", help="起始月 YYYY-MM")
    parser.add_argument("--max", dest="max_contracts", type=int, default=600)
    parser.add_argument("--force", action="store_true",
                        help="清空 futures_daily 后全量重建（数据口径修复用）")
    args = parser.parse_args()

    store.init_db()
    result = ensure_futures_daily(start_month=args.start, max_contracts=args.max_contracts,
                                  force=args.force)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    print(f"latest date: {store.latest_futures_date()}")
    # force 预检中止（error 无 failed）也须非零退出——静默截断曾是 exit 0
    return 1 if result["failed"] or result.get("error") else 0


if __name__ == "__main__":
    sys.exit(main())
