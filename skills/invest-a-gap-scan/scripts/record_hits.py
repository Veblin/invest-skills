"""gap-scan 命中记录器（W2/M2：前瞻回测状态文件）。

scan.py --json 输出 → `reports/gap-backtest/hits.jsonl`（每命中一行）。
幂等：(scan_date, ts_code) 去重；原子写（temp + rename）。
字段 schema 见 backtest-eval-plan §7.1（键值对齐 gap_scanner.Hit / report_formatter）。

用法：
    uv run python skills/invest-a-gap-scan/scripts/scan.py --json > /tmp/scan.json
    uv run python skills/invest-a-gap-scan/scripts/record_hits.py --json-file /tmp/scan.json
    # 或 stdin 管道；scan.py --record 钩子内部调用（见 scan.py Step 8）
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

SCHEMA_FIELDS = (
    "scan_date", "ts_code", "name", "gap_date", "gap_pct",
    "price_at_scan", "ma60", "pct_from_ma60", "pct_from_gap_high",
    "vol_ratio", "avg_amount_20d",
)

DEFAULT_STATE = Path("reports/gap-backtest/hits.jsonl")


def record(json_output: dict, scan_date: str, state: Path) -> int:
    """追加命中到状态文件；返回新增行数。同 (scan_date, ts_code) 幂等跳过。"""
    state.parent.mkdir(parents=True, exist_ok=True)
    keep: list[str] = []
    seen: set[tuple[str, str]] = set()
    if state.exists():
        for ln in state.read_text(encoding="utf-8").splitlines():
            if not ln.strip():
                continue
            # code-review #14：损坏/截断行必须原样保留（append 到 keep）——
            # 旧实现跳过且不保留 → 下次成功写盘即永久删除该历史及其去重键，
            # 后续扫描会重复追加同一命中（eval 样本与真实历史悄然分叉）。
            keep.append(ln)
            try:
                rec = json.loads(ln)
            except json.JSONDecodeError:
                continue
            seen.add((str(rec.get("scan_date", "")), str(rec.get("ts_code", ""))))

    added = 0
    for h in json_output.get("hits", []) or []:
        key = (scan_date, h.get("ts_code", ""))
        if key in seen:
            continue
        gap = h.get("gap") or {}
        rec = {
            "scan_date": scan_date,
            "ts_code": h.get("ts_code", ""),
            "name": h.get("name", ""),
            "gap_date": gap.get("gap_date"),
            "gap_pct": round(float(gap.get("gap_pct") or 0), 3) if gap.get("gap_pct") is not None else None,
            "price_at_scan": h.get("current_price"),
            "ma60": h.get("ma60"),
            "pct_from_ma60": round(float(h.get("pct_from_ma60") or 0), 2) if h.get("pct_from_ma60") is not None else None,
            "pct_from_gap_high": round(float(h.get("pct_from_gap_high") or 0), 2) if h.get("pct_from_gap_high") is not None else None,
            "vol_ratio": round(float(h.get("vol_ratio") or 0), 2) if h.get("vol_ratio") is not None else None,
            "avg_amount_20d": h.get("avg_amount_20d"),
        }
        keep.append(json.dumps(rec, ensure_ascii=False))
        seen.add(key)
        added += 1

    if added:
        tmp = state.with_suffix(".tmp")
        tmp.write_text("\n".join(keep) + "\n", encoding="utf-8")
        os.replace(tmp, state)
    return added


def _default_scan_date() -> str:
    """默认扫描日 = 上海会话日（数据实际所属交易日，与 snapshot 同口径）。"""
    sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
    from _invest_path import ensure_invest_a_scripts_on_path

    ensure_invest_a_scripts_on_path()
    from dates import shanghai_session_date

    return shanghai_session_date()


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--json-file", default=None, help="scan.py --json 输出文件；缺省读 stdin")
    p.add_argument("--scan-date", default=None, help="扫描日 YYYYMMDD（缺省 = shanghai_session_date）")
    p.add_argument("--state", default=str(DEFAULT_STATE), help="状态文件路径")
    args = p.parse_args()

    if args.json_file:
        payload = json.load(open(args.json_file, encoding="utf-8"))
    else:
        payload = json.load(sys.stdin)
    scan_date = args.scan_date or _default_scan_date()
    added = record(payload, scan_date, Path(args.state))
    print(f"record_hits: {added} 条新增（scan_date={scan_date}）→ {args.state}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
