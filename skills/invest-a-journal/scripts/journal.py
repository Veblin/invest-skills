#!/usr/bin/env python3
"""invest-a-journal CLI — 日志查看/统计/搜索。

v0.2.1：新建日志走 Claude 驱动（/invest-a-journal），本 CLI 不提供交互式 add。
保留数据查看功能：list / show / delete / stats / search。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# 确保可从任意目录运行
_this_dir = Path(__file__).resolve().parent
_lib_dir = _this_dir / "lib"
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))

from _invest_path import ensure_invest_a_scripts_on_path  # noqa: E402

ensure_invest_a_scripts_on_path()

from db import (  # noqa: E402
    delete_journal,
    get_journal,
    journal_stats,
    list_journals,
    search_by_symbol,
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _prompt(label: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    try:
        val = input(f"  {label}{suffix}: ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\n取消。")
        sys.exit(0)
    return val if val else default


def cmd_add() -> int:
    """Shell add 已移除 — 引导至 /invest-a-journal。"""
    print(
        "Shell 交互式 add 已停用。\n"
        "请在 Claude Code / Hermes 中运行 /invest-a-journal 完成四维评估后落库。\n"
        "查看已有日志可用：list / show / search / stats。"
    )
    return 1


# ---------------------------------------------------------------------------
# list / show / delete / stats / search
# ---------------------------------------------------------------------------

def cmd_list(limit: int = 20) -> int:
    entries = list_journals(limit)
    if not entries:
        print("暂无日志。")
        return 0

    print(f"\n最近 {len(entries)} 条日志:\n")
    print(f"{'ID':<5} {'方':<3} {'标的':<10} {'日期':<12} {'类型':<10} {'标记':<6}")
    print("-" * 55)
    for e in entries:
        direction = e.get("direction", "buy")
        dir_mark = "买" if direction == "buy" else "卖"
        reviewed = "✅" if e.get("reviewed") else ""
        has_eval = "📋" if e.get("evaluation_json") else ""
        print(
            f"{e['id']:<5} {dir_mark:<3} {e['symbol']:<10} "
            f"{str(e.get('entry_date', ''))[:10]:<12} "
            f"{str(e.get('asset_type', ''))[:10]:<10} "
            f"{reviewed}{has_eval}"
        )
    return 0


def cmd_show(journal_id: int) -> int:
    e = get_journal(journal_id)
    if not e:
        print(f"日志 #{journal_id} 不存在。")
        return 1

    print(f"\n=== 日志 #{e['id']} ===")
    print(f"  方向:       {'买入' if e.get('direction', 'buy') == 'buy' else '卖出'}")
    print(f"  标的:       {e['symbol']}")
    print(f"  类型:       {e.get('asset_type', '')}")
    if e.get("linked_journal_id"):
        print(f"  关联买入:   #{e['linked_journal_id']}")
    if e.get("driver"):
        print(f"  驱动:       {e['driver']}")
    if e.get("hypothesis"):
        print(f"  假设:       {e['hypothesis']}")
    print(f"  错误条件:   {e.get('wrong_conditions', '')}")
    if e.get("position_pct") is not None:
        print(f"  仓位:       {e['position_pct']}%")
    if e.get("entry_price") is not None:
        print(f"  入场价:     {e['entry_price']}")
    print(f"  入场日期:   {e.get('entry_date', '')}")

    if e.get("reviewed"):
        print(f"\n  --- 复盘 ---")
        print(f"  出场价:     {e.get('exit_price')}")
        print(f"  出场日期:   {e.get('exit_date', '')}")
        if e.get("lessons"):
            print(f"  教训:       {e['lessons']}")

    # 评估摘要
    eval_json = e.get("evaluation_json")
    if eval_json:
        if isinstance(eval_json, str):
            try:
                eval_json = json.loads(eval_json)
            except json.JSONDecodeError:
                eval_json = None
        if isinstance(eval_json, dict):
            dims = eval_json.get("dimensions", {})
            if dims:
                print(f"\n  --- 评估 ---")
                dim_names = {
                    "logic": "逻辑完整性",
                    "blind_spots": "数据盲点",
                    "position_sizing": "仓位匹配",
                    "risk_reward": "风险收益比",
                }
                for dim_key, dim_data in dims.items():
                    name = dim_names.get(dim_key, dim_key)
                    level = dim_data.get("level", "?") if isinstance(dim_data, dict) else "?"
                    print(f"  {name}: {level}")

    print(f"\n  创建时间:   {e.get('created_at', '')}")
    return 0


def cmd_delete(journal_id: int) -> int:
    e = get_journal(journal_id)
    if not e:
        print(f"日志 #{journal_id} 不存在。")
        return 1
    print(f"\n确认删除 #{journal_id} — {e['symbol']}？")
    confirm = _prompt("输入 y 确认:", "")
    if confirm.lower() == "y":
        delete_journal(journal_id)
        print(f"已删除 #{journal_id}")
    else:
        print("已取消。")
    return 0


def cmd_stats() -> int:
    stats = journal_stats()
    print(f"\n日志统计:")
    print(f"  总计:   {stats['total']}")
    print(f"  买入:   {stats['buy']}")
    print(f"  卖出:   {stats['sell']}")
    print(f"  已复盘: {stats['reviewed']}")
    if stats["total"] > 0:
        print(f"  复盘率: {stats['reviewed'] / stats['total'] * 100:.0f}%")
    return 0


def cmd_search(symbol: str) -> int:
    results = search_by_symbol(symbol.upper())
    if not results:
        print(f"\n未找到 {symbol} 的相关日志。")
        return 0
    print(f"\n{symbol} 的历史日志 ({len(results)} 条):\n")
    print(f"{'ID':<5} {'方':<3} {'日期':<12} {'驱动':<30}")
    print("-" * 55)
    for r in results:
        direction = "买" if r.get("direction", "buy") == "buy" else "卖"
        print(
            f"{r['id']:<5} {direction:<3} "
            f"{str(r.get('entry_date', ''))[:12]:<12} "
            f"{str(r.get('driver', ''))[:30]}"
        )
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="invest-a-journal — 交易日志 CLI（v0.2.1）"
    )
    sub = p.add_subparsers(dest="action")

    sub.add_parser(
        "add",
        help="已停用：请使用 /invest-a-journal（Claude 四维评估后落库）",
    )

    p_list = sub.add_parser("list", help="列出最近日志")
    p_list.add_argument("--limit", type=int, default=20)
    p_list.add_argument("--stats", action="store_true", help="显示统计信息")

    p_show = sub.add_parser("show", help="显示日志详情")
    p_show.add_argument("id", type=int)

    p_del = sub.add_parser("delete", help="删除日志")
    p_del.add_argument("id", type=int)

    sub.add_parser("stats", help="日志统计")

    p_search = sub.add_parser("search", help="按标的搜索日志")
    p_search.add_argument("symbol", type=str)

    return p


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if not args.action:
        parser.print_help()
        return 0

    if args.action == "add":
        return cmd_add()
    elif args.action == "list":
        if getattr(args, "stats", False):
            return cmd_stats()
        return cmd_list(args.limit)
    elif args.action == "show":
        return cmd_show(args.id)
    elif args.action == "delete":
        return cmd_delete(args.id)
    elif args.action == "stats":
        return cmd_stats()
    elif args.action == "search":
        return cmd_search(args.symbol)
    else:
        parser.print_help()
        return 0


if __name__ == "__main__":
    sys.exit(main())
