#!/usr/bin/env python3
"""
investment-learning CLI。

用法:
  python3 invest.py collect 600176              # 采集数据
  python3 invest.py report 600176               # 报告（compact）
  python3 invest.py report 600176 --emit=mjson  # JSON 报告
  python3 invest.py compare 600176 000858        # 对比
  python3 invest.py diagnose                     # 检查数据源
  python3 invest.py store list                   # 查看存储
  python3 invest.py collect 600176 --store       # 采集并存储
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# 确保从本项目目录导入
_SCRIPT_DIR = Path(__file__).parent.resolve()
sys.path = [str(_SCRIPT_DIR)] + [p for p in sys.path if p and "scripts/lib" not in p]
os.chdir(_SCRIPT_DIR.parent.parent.parent)  # 切到 code/ 目录

from lib import collector, env, render

try:
    from lib import store as store_mod
    _HAS_STORE = True
except ImportError:
    store_mod = None
    _HAS_STORE = False


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="A股个股调研数据采集与分析")
    sub = p.add_subparsers(dest="command", required=True)

    pc = sub.add_parser("collect", help="采集多维度数据")
    pc.add_argument("symbol")
    pc.add_argument("--dims", default="basic_info,financials,quote,shareholders,northbound")
    pc.add_argument("--store", action="store_true", help="存入持久化存储")

    pr = sub.add_parser("report", help="生成分析报告")
    pr.add_argument("symbol")
    pr.add_argument("--emit", default="compact", choices=["compact", "json"])
    pr.add_argument("--dims", default="basic_info,financials,quote,shareholders,northbound")

    pcomp = sub.add_parser("compare", help="双标对比")
    pcomp.add_argument("symbol_a")
    pcomp.add_argument("symbol_b")
    pcomp.add_argument("--emit", default="compact", choices=["compact", "json"])

    pd = sub.add_parser("diagnose", help="检查数据源")
    pd.add_argument("--json", action="store_true")

    ps = sub.add_parser("store", help="管理存储")
    ps.add_argument("action", nargs="?", default="list", choices=["list", "stats", "clear"])
    return p


def cmd_collect(args) -> int:
    dims = [d.strip() for d in args.dims.split(",")]
    result = collector.collect_all(args.symbol, dims)
    print(render.render(result, args.symbol, "compact"))
    if result["summary"]["available"] == 0:
        print("⚠️ 所有维度均不可用。请运行 diagnose。")
        return 1
    if args.store and _HAS_STORE:
        store_mod.save_collection(result)
        print("💾 已存入持久化存储")
    return 0


def cmd_report(args) -> int:
    dims = [d.strip() for d in args.dims.split(",")]
    result = collector.collect_all(args.symbol, dims)
    print(render.render(result, args.symbol, args.emit))
    return 0 if result["summary"]["available"] > 0 else 1


def cmd_compare(args) -> int:
    ra = collector.collect_all(args.symbol_a)
    rb = collector.collect_all(args.symbol_b)
    da = {d["dimension"]: d for d in ra["dimensions"]}
    db = {d["dimension"]: d for d in rb["dimensions"]}
    lines = [f"# 对比: {args.symbol_a} vs {args.symbol_b}", ""]
    for dn in sorted(set(list(da.keys()) + list(db.keys()))):
        lines.append(f"## {da.get(dn, db.get(dn, {})).get('display', dn)}\n")
        if dn == "financials":
            lines.append("| 期间 | 标的A ROE | 标的B ROE | 标的A EPS | 标的B EPS |\n|------|-----------|-----------|-----------|-----------|")
            ra_ = {r["end_date"]: r for r in (da.get(dn, {}).get("data") or [])}
            rb_ = {r["end_date"]: r for r in (db.get(dn, {}).get("data") or [])}
            for d in sorted(set(list(ra_.keys()) + list(rb_.keys())), reverse=True)[:8]:
                lines.append(f"| {d} | {ra_.get(d,{}).get('roe','-')}% | {rb_.get(d,{}).get('roe','-')}% | {ra_.get(d,{}).get('eps','-')} | {rb_.get(d,{}).get('eps','-')} |")
            lines.append("")
    print("\n".join(lines))
    return 0


def cmd_diagnose(args) -> int:
    d = env.diagnose()
    if args.json:
        print(json.dumps(d, ensure_ascii=False, indent=2))
        return 0
    print(f"=== 数据源诊断 ===\n配置: {d['config_source']}\n可用: {d['available_count']}/{d['total_count']}\n")
    for s, a in d["sources"].items():
        print(f"  {'✅' if a else '❌'} {s}")
    print()
    return 0 if d["available_count"] > 0 else 1


def cmd_store(args) -> int:
    if not _HAS_STORE:
        print("⚠️ store 模块不可用")
        return 1
    if args.action == "list":
        for r in store_mod.list_collections(20):
            print(f"  #{r['id']}: {r['symbol']} | {r.get('fetched_at','')[:19]} | {r.get('dimensions_ok','?')}/{r.get('dimensions_total','?')}")
        return 0
    if args.action == "stats":
        for k, v in store_mod.get_stats().items():
            print(f"  {k}: {v}")
        return 0
    if args.action == "clear":
        store_mod.clear_all()
        print("✅ 已清空")
        return 0
    return 0


def main() -> int:
    env.ensure_env_loaded()
    args = build_parser().parse_args()
    return {"collect": cmd_collect, "report": cmd_report, "compare": cmd_compare,
            "diagnose": cmd_diagnose, "store": cmd_store}[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
