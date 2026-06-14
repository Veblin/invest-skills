#!/usr/bin/env python3
"""
investment-learning CLI。

用法:
  python3 invest.py collect 600176              # 采集数据
  python3 invest.py report 600176               # Markdown 报告（默认 stdout）
  python3 invest.py report 600176 --outdir ./out # Markdown 写入目录
  python3 invest.py report 600176 --emit=html    # HTML 报告（v0.1.2 旧版，须显式指定）
  python3 invest.py report 600176 --emit=json   # JSON 报告（stdout）
  python3 invest.py compare 600176 000858        # 对比
  python3 invest.py diagnose                     # 检查数据源
  python3 invest.py store list                   # 查看存储
  python3 invest.py collect 600176 --store       # 采集并存储
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# 确保从本项目的 lib/ 导入，排除旧归档路径
_SCRIPT_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(_SCRIPT_DIR))

# 查找项目根目录（向上遍历直到找到 pyproject.toml）
_project_root = _SCRIPT_DIR
while _project_root != _project_root.parent:
    if (_project_root / "pyproject.toml").exists():
        break
    _project_root = _project_root.parent

from lib import collector, env, render
from lib.proxy import warn_if_proxy_detected

try:
    from lib import store as store_mod
    _HAS_STORE = True
except ImportError as e:
    store_mod = None
    _HAS_STORE = False
    import logging
    logging.getLogger(__name__).warning("store 模块导入失败（功能降级）: %s", e)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="A股个股调研数据采集与分析")
    sub = p.add_subparsers(dest="command", required=True)

    pc = sub.add_parser("collect", help="采集多维度数据")
    pc.add_argument("symbol")
    pc.add_argument("--dims", default="basic_info,financials,quote,shareholders,northbound,valuation,kline")
    pc.add_argument("--store", action="store_true", help="存入持久化存储")
    pc.add_argument("--with-macro", action="store_true", help="包含宏观数据（FRED US 10Y/2Y/VIX/CPI/美元指数）")
    pc.add_argument("--deep", action="store_true", help="深度模式：扩大K线范围，增加行业/舆情分析")

    pr = sub.add_parser("report", help="生成分析报告")
    pr.add_argument("symbol")
    pr.add_argument("--emit", default="md", choices=["compact", "json", "md", "html"])
    pr.add_argument("--dims", default="basic_info,financials,quote,shareholders,northbound,valuation,kline")
    pr.add_argument("--with-macro", action="store_true", help="包含宏观数据（FRED US 10Y/2Y/VIX/CPI/美元指数）")
    pr.add_argument("--deep", action="store_true", help="深度模式：扩大K线范围，增加行业/舆情分析")
    pr.add_argument("--outdir", default="", help="报告输出目录（指定则写 .md 或 .html 文件；默认仅 stdout）")

    pcomp = sub.add_parser("compare", help="双标对比")
    pcomp.add_argument("symbol_a")
    pcomp.add_argument("symbol_b")
    pcomp.add_argument("--emit", default="compact", choices=["compact", "json"])

    pdiff = sub.add_parser("diff", help="对比两次快照变化")
    pdiff.add_argument("symbol")
    pdiff.add_argument("--from", dest="from_id", type=int, help="指定旧快照 ID")
    pdiff.add_argument("--to", dest="to_id", type=int, help="指定新快照 ID")
    pdiff.add_argument("--emit", default="compact", choices=["compact", "json"])

    pd = sub.add_parser("diagnose", help="检查数据源")
    pd.add_argument("--json", action="store_true")

    ps = sub.add_parser("store", help="管理存储")
    ps.add_argument("action", nargs="?", default="list", choices=["list", "stats", "clear"])
    return p


def cmd_collect(args: argparse.Namespace) -> int:
    dims = [d.strip() for d in args.dims.split(",")]
    if args.with_macro and "kline" not in dims:
        dims.append("kline")
    if args.deep:
        if "kline" not in dims:
            dims.append("kline")
        print("🔬 深度模式已启用（扩大K线范围至730日 + 行业/舆情分析）")
    if args.with_macro:
        print("🌐 宏观数据模式已启用（FRED US 10Y/2Y/VIX/CPI/美元指数）")
    warn_if_proxy_detected()
    result = collector.collect_all(args.symbol, dims, deep=args.deep)
    print(render.render(result, args.symbol, "compact"))
    if result["summary"]["available"] == 0:
        print("⚠️ 所有维度均不可用。请运行 diagnose。")
        return 1
    if args.store and _HAS_STORE:
        store_mod.save_collection(result)
        print("💾 已存入持久化存储")
    return 0


def _report_basename(result: dict, symbol: str, ts: str) -> str:
    """生成报告文件名前缀：{ts}-{symbol}-{name}。"""
    name = ""
    for dim in result.get("dimensions", []):
        if dim.get("dimension") == "basic_info":
            data = dim.get("data", {})
            if isinstance(data, dict):
                name = data.get("name", "") or data.get("股票简称", "")
            break
    safe_name = re.sub(r'[\\/:*?"<>|]', "_", name) if name else ""
    return f"{ts}-{symbol}-{safe_name}" if safe_name else f"{ts}-{symbol}"


def cmd_report(args: argparse.Namespace) -> int:
    dims = [d.strip() for d in args.dims.split(",")]
    if args.with_macro and "kline" not in dims:
        dims.append("kline")
    if args.deep:
        if "kline" not in dims:
            dims.append("kline")
        print("🔬 深度模式已启用（扩大K线范围至730日 + 行业/舆情分析）")
    if args.with_macro:
        print("🌐 宏观数据模式已启用（FRED US 10Y/2Y/VIX/CPI/美元指数）")
    warn_if_proxy_detected()
    result = collector.collect_all(args.symbol, dims, deep=args.deep)
    if result["summary"]["available"] == 0:
        print("⚠️ 所有维度均不可用，无法生成报告")
        return 1

    fmt = args.emit

    if fmt == "html":
        print(
            "⚠️ HTML 为 v0.1.2 旧版模板，迭代期请使用默认 Markdown 输出（省略 --emit 或 --emit md）",
            file=sys.stderr,
        )
        md_v2 = render.render_report_v2(result, args.symbol)
        output = render.render_html(result, args.symbol)
        from datetime import datetime
        now = datetime.now()
        ts = now.strftime("%Y-%m-%d-%H-%M-%S")

        basename = _report_basename(result, args.symbol, ts)
        outdir = Path(args.outdir).resolve() if args.outdir else Path.cwd()
        outdir.mkdir(parents=True, exist_ok=True)
        htmlpath = outdir / f"{basename}.html"
        htmlpath.write_text(output, encoding="utf-8")

        mdfile = outdir / f"{basename}.md"
        mdfile.write_text(md_v2, encoding="utf-8")

        print(render.render(result, args.symbol, "compact"))
        print(f"📄 HTML 报告: {htmlpath.resolve()}")
        print(f"📝 Markdown 报告: {mdfile.resolve()}")
        return 0

    output = render.render(result, args.symbol, fmt)

    if fmt == "md" and args.outdir:
        from datetime import datetime
        ts = datetime.now().strftime("%Y-%m-%d-%H-%M-%S")
        basename = _report_basename(result, args.symbol, ts)
        outdir = Path(args.outdir).resolve()
        outdir.mkdir(parents=True, exist_ok=True)
        mdpath = outdir / f"{basename}.md"
        mdpath.write_text(output, encoding="utf-8")
        print(f"📝 Markdown 报告: {mdpath.resolve()}")
        return 0

    print(output)
    return 0


def cmd_compare(args: argparse.Namespace) -> int:
    warn_if_proxy_detected()
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


def cmd_diagnose(args: argparse.Namespace) -> int:
    warn_if_proxy_detected()
    d = env.diagnose()
    if args.json:
        print(json.dumps(d, ensure_ascii=False, indent=2))
        return 0
    proxy_hint = ""
    if d.get("proxy_detected"):
        proxy_hint = "代理环境: 已检测 — 国内数据源应直连，请配置 Clash DIRECT 规则\n"
        if d.get("clash_rules_hint"):
            proxy_hint += f"\n{d['clash_rules_hint']}\n"
    print(f"=== 数据源诊断 ===\n配置: {d['config_source']}\n{proxy_hint}可用: {d['available_count']}/{d['total_count']}\n")
    for s, a in d["sources"].items():
        if isinstance(a, dict):
            em = a
            icon = "✅" if em.get("reachable") else "❌"
            detail = f" (HTTP {em.get('http_status') or 'N/A'})" if em.get("error") else ""
            print(f"  {icon} {s}{detail}")
            if em.get("error"):
                from lib.render import sanitize_error
                print(f"      ↳ {sanitize_error(em['error'], 80)}")
        else:
            print(f"  {'✅' if a else '❌'} {s}")
    print()
    return 0 if d["available_count"] > 0 else 1


def cmd_store(args: argparse.Namespace) -> int:
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


def cmd_diff(args: argparse.Namespace) -> int:
    """对比同一股票两次快照的变化。"""
    if not _HAS_STORE:
        print("⚠️ store 模块不可用，diff 功能无法执行")
        return 1

    # 参数校验
    partial_ids = (args.from_id is not None) != (args.to_id is not None)
    if partial_ids:
        print("❌ --from 和 --to 必须同时指定，或都不指定（使用自动最近两次）",
              file=sys.stderr)
        return 1

    if args.from_id is not None and args.to_id is not None:
        old = store_mod.get_collection(args.from_id)
        new = store_mod.get_collection(args.to_id)
        if old is None:
            print(f"❌ 快照 #{args.from_id} 不存在", file=sys.stderr)
            return 1
        if new is None:
            print(f"❌ 快照 #{args.to_id} 不存在", file=sys.stderr)
            return 1
        # 校验 symbol 一致性
        old_sym = (old.get("raw_json") or old).get("symbol", "")
        new_sym = (new.get("raw_json") or new).get("symbol", "")
        if old_sym != args.symbol or new_sym != args.symbol:
            print(f"⚠️ 快照 symbol 不匹配: #{args.from_id}={old_sym}, #{args.to_id}={new_sym}, CLI={args.symbol}",
                  file=sys.stderr)
        # 确保 old 早于 new
        if (old.get("fetched_at", "") > new.get("fetched_at", "")):
            old, new = new, old
            print(f"ⓘ 已自动交换顺序（#{args.to_id} → #{args.from_id}）")
    else:
        pair = store_mod.get_latest_two(args.symbol)
        if pair is None:
            print(f"❌ {args.symbol} 至少需要 2 次 --store 采集才能 diff（当前不足）", file=sys.stderr)
            return 1
        old, new = pair

    diff_result = store_mod.diff_collections(old, new)

    if args.emit == "json":
        print(json.dumps(diff_result, ensure_ascii=False, indent=2, default=str))
        return 0

    # compact 输出
    _print_diff_compact(diff_result)
    return 0


def _print_diff_compact(diff: dict) -> None:
    """compact 格式 diff 输出。"""
    from datetime import datetime

    old_at = diff.get("old_at", "")[:19]
    new_at = diff.get("new_at", "")[:19]

    # 计算间隔天数
    try:
        old_dt = datetime.fromisoformat(old_at.replace("Z", "+00:00"))
        new_dt = datetime.fromisoformat(new_at.replace("Z", "+00:00"))
        days = (new_dt - old_dt).days
        interval = f" ({days}天)"
    except (ValueError, TypeError):
        interval = ""

    print(f"# {diff['symbol']} 变化摘要")
    print(f"采集间隔: {old_at} → {new_at}{interval}")
    print()

    changed = diff.get("changed", [])
    if changed:
        print("## 发生变化的关键字段")
        print()
        # 按维度分组
        by_dim: dict[str, list[dict]] = {}
        for c in changed:
            dim = c["path"].split(".")[0]
            by_dim.setdefault(dim, []).append(c)

        for dim, items in sorted(by_dim.items()):
            display = dim
            print(f"### {display}")
            for item in items:
                field = item["path"].split(".", 1)[1] if "." in item["path"] else item["path"]
                old_v = item.get("old")
                new_v = item.get("new")
                if old_v is None and new_v is None:
                    # 描述型变更（如新增记录数）
                    desc = item.get("description", "")
                    if desc:
                        print(f"- {field}: {desc}")
                    continue
                pct = item.get("pct")
                pct_str = f" ({pct:+.1f}%)" if pct is not None else ""
                print(f"- {field}: {old_v} → {new_v}{pct_str}")
            print()

    unchanged = diff.get("unchanged", [])
    if unchanged:
        print("## 未变化")
        for dim in unchanged[:10]:
            print(f"- {dim}")
        if len(unchanged) > 10:
            print(f"  ... 共 {len(unchanged)} 个维度")
        print()

    skipped = diff.get("skipped", [])
    if skipped:
        print("## 跳过")
        for s in skipped:
            print(f"- {s.get('dimension', '?')}: {s.get('reason', '?')}")
        print()


def main() -> int:
    env.ensure_env_loaded()
    args = build_parser().parse_args()
    if args.command == "collect":
        return cmd_collect(args)
    elif args.command == "report":
        return cmd_report(args)
    elif args.command == "compare":
        return cmd_compare(args)
    elif args.command == "diff":
        return cmd_diff(args)
    elif args.command == "diagnose":
        return cmd_diagnose(args)
    elif args.command == "store":
        return cmd_store(args)
    return 1


if __name__ == "__main__":
    sys.exit(main())
