#!/usr/bin/env python3
"""限售解禁压力日历（invest-a-event-calendar v2）。

两种模式：
  ① 池模式（v2 首选，--pool-file）：对清单内个股逐一下钻「个股解禁队列」，
     输出提醒（未来 alert-days 内有解禁的标的）+ 变化检测（相较上次运行的新增/
     消失/字段变化）+ 全池明细，落盘 {YYYYMMDD}-pool.md。状态私有存储，
     只推「新增/临近」——不做每日全表。
  ② 市场模式（v1 保留，低频参考）：全市场解禁日汇总（东财 summary_em）：
     回看近 120 日 + 展望未来 30 日，压力日按「相对近 120 日分位」标注。

用法：
    cd "${INVEST_SKILLS_ROOT:-.}" && uv run python skills/invest-a-event-calendar/scripts/unlock_calendar.py --pool-file pool.txt
    # 池模式 → reports/event-calendar/{YYYYMMDD}-pool.md
    uv run python skills/invest-a-event-calendar/scripts/unlock_calendar.py
    # 市场模式 → reports/event-calendar/{YYYYMMDD}.md

参数（池模式）：
    --pool-file PATH   清单文件（每行一个 6 位代码；# 注释）——池模式开关
    --alert-days N     提醒窗（自然日，默认 30）；--lookahead N 拉取展望（默认 90）
    --state-file PATH  状态文件（默认 ~/.local/share/investment/event_calendar_state.json）
    --no-state         不读写状态（调试）
参数（市场模式）：--days-past N（默认 120）/ --days-future N（默认 30）
公共：--no-out / --out-dir PATH

口径与边界：
    - 个股源：akshare stock_restricted_release_queue_em（东财，单标的，经 skills/lib/unlock_source.py）
      ——失败**显式标注**（不静默当空）；市场源：stock_restricted_release_summary_em
    - 原始字段：解禁数量/实际解禁数量 = 股；实际解禁市值 = 元（python 换算为亿）
    - 解禁 ≠ 减持：供给事件提示，非方向信号（见 SKILL 分析纪律）
    - P0：全部计算由 pandas/bisect 完成；研究工具，非决策工具

退出码：0 正常（含部分标的取数失败，报告内逐行标注）；2 参数/池文件错误；
       3 数据不可得（市场空返回且窗口含交易日；或池模式全部标的失败）。
"""

from __future__ import annotations

import argparse
import bisect
import datetime as _dt
import json
import pathlib
import sys
import time


def _ensure_lib_on_path() -> None:
    """共享库引导（R1 审查 F3）：单体仓库取 skills/lib；包内运行取 <pkg>/scripts/lib。"""
    here = pathlib.Path(__file__).resolve()
    for cand in (here.parents[2] / "lib", here.parents[1] / "scripts" / "lib"):
        if cand.is_dir():
            sys.path.insert(0, str(cand))
            return


_ensure_lib_on_path()
from skill_paths import default_out_dir  # noqa: E402
from unlock_source import fetch_symbol_unlocks  # noqa: E402

try:  # 交易日历（空窗鉴别用）；包内未携带 invest lib 时降级为 None（保守判不可得）
    from invest_path import ensure_invest_a_scripts_on_path  # noqa: E402

    ensure_invest_a_scripts_on_path()
except Exception:  # pragma: no cover
    pass


def _window_has_trading_day(start: str, end: str) -> bool | None:
    """窗口 [start, end] 是否含交易日；日历不可用 → None（调用方保守处理，R1 审查 F8）。"""
    try:
        from lib.trade_cal import fetch_trade_cal

        dates, _estimated = fetch_trade_cal(start, end)
        return bool(dates)
    except Exception:
        return None


def fmt_date(d: _dt.date) -> str:
    return d.strftime("%Y%m%d")


def percentile_rank(values: list[float], v: float) -> float | None:
    """v 在 values 中的分位（0-100，bisect_left 口径）；空基准返回 None。

    R0 审查 F6：原实现对空基准返回哨兵 100.0，会把「零样本」渲染成
    「100.0% 极高压力」的伪分类——无基准即无分位，None 语义。（审 2026-09-10）
    """
    if not values:
        return None
    return rank_in_sorted(sorted(values), v)


def rank_in_sorted(sorted_values: list[float], v: float) -> float | None:
    """v 在已排序序列中的分位（供批量行免重复排序）；空序列 → None（防 ZeroDivision）。"""
    if not sorted_values:
        return None
    return round(bisect.bisect_left(sorted_values, v) / len(sorted_values) * 100, 1)


def process(df, today: _dt.date, past_days: int, future_days: int) -> dict:
    """纯函数：换算单位 + 切窗 + 分位标注。P0：pandas 聚合。"""
    import pandas as pd

    if df is None or df.empty:
        # F5：空分支必须返回与正常路径同构的键（含 past_days）——render_md 无条件读取
        return {"past": [], "future": [], "hist_billion": [], "past_recent": [],
                "past_days": past_days, "error": None}
    df = df.copy()
    df["dt"] = pd.to_datetime(df["解禁时间"]).dt.date
    df["市值亿"] = pd.to_numeric(df["实际解禁市值"], errors="coerce") / 1e8
    df["数量亿股"] = pd.to_numeric(df["实际解禁数量"], errors="coerce") / 1e8
    df["家数"] = pd.to_numeric(df["当日解禁股票家数"], errors="coerce")

    past = df[df["dt"] <= today].sort_values("dt")
    future = df[(df["dt"] > today) & (df["dt"] <= today + _dt.timedelta(days=future_days))]
    # 分位标注（回看窗口仅统计近 past_days 内分布用于对照；2026-09-08 review F12 删死变量 hist）
    cutoff = today - _dt.timedelta(days=past_days)
    hist_rank_base = [float(v) for v in past[past["dt"] >= cutoff]["市值亿"].dropna().tolist()]
    hist_sorted = sorted(hist_rank_base)   # 排序一次，行内 bisect（F12：原实现每行重排）

    def row(r, *, with_rank: bool):
        mv = float(r["市值亿"]) if pd.notna(r["市值亿"]) else None
        rank = rank_in_sorted(hist_sorted, mv) if (with_rank and mv is not None and hist_sorted) else None
        flag = ""
        if rank is not None:
            if rank >= 90:
                flag = "🔴 极高压力"
            elif rank >= 80:
                flag = "🟠 高压"
        return {
            "date": str(r["dt"]), "家数": int(r["家数"]) if pd.notna(r["家数"]) else None,
            "数量亿股": round(r["数量亿股"], 2) if pd.notna(r["数量亿股"]) else None,
            "市值亿": round(mv, 1) if mv is not None else None,
            "rank": rank, "flag": flag,
            "hs300_chg": float(r["沪深300指数涨跌幅"]) if "沪深300指数涨跌幅" in df.columns and pd.notna(r["沪深300指数涨跌幅"]) else None,
        }

    # 分位只对展望行计算（F12：回看行 rank 此前计算但从不渲染）
    past_rows = [row(r, with_rank=False) for r in past.to_dict("records")]
    future_rows = [row(r, with_rank=True) for r in future.to_dict("records")]
    # 近 30 日回看 = 按日历窗口过滤（F12：原实现取「最后 30 行」，与标题语义不符）
    recent_cutoff = fmt_date(today - _dt.timedelta(days=30))
    past_recent = [r for r in past_rows if r["date"].replace("-", "") >= recent_cutoff]
    return {"past": past_rows, "future": future_rows, "hist_billion": hist_rank_base,
            "past_recent": past_recent, "past_days": past_days}


def _fmt_v(v) -> str:
    """数值格式化：None 渲染为 '-'（review F12：此前 rank None 输出 'None%'）。"""
    return "-" if v is None else str(v)


def render_md(result: dict, today: str, future_days: int) -> str:
    lines = [
        f"# 🗓 限售解禁压力日历 — {today}",
        "",
        f"> 全市场解禁汇总（东财）。展望未来 {future_days} 日；分位基准 = 近 {result['past_days']} 日有解禁日",
        f"> 解禁市值序列（{len(result['hist_billion'])} 个样本日）。解禁 ≠ 减持，为供给事件提示。",
        "> 研究工具，非决策工具，不含任何买卖建议。",
        "",
    ]
    fut = result["future"]
    if not fut:
        lines.append("**展望窗口内无解禁日**（或东财数据不可得——确认输出非 ProxyError 降级）。")
    else:
        if not result["hist_billion"]:
            lines.append("> ⚠️ 分位基准为空（回看窗口内无样本日）——本次不输出压力标注（无基准即无分位）。")
            lines.append("")
        lines.append("## 🔮 未来解禁日")
        lines.append("")
        lines.append(f"| 日期 | 家数 | 解禁数量(亿股) | 实际解禁市值(亿) | 近{result['past_days']}日分位 | 标注 |")
        lines.append("|------|------|---------------|-----------------|------------|------|")
        for r in fut:
            rank = "-" if r["rank"] is None else f"{r['rank']}%"
            lines.append(f"| {r['date']} | {_fmt_v(r['家数'])} | {_fmt_v(r['数量亿股'])} "
                         f"| {_fmt_v(r['市值亿'])} | {rank} | {r['flag']} |")
    # 显式条件取键（dict.get 默认值会被急切求值——R1 审查尾部项）
    p = result["past_recent"] if "past_recent" in result else result["past"]
    lines.append("")
    lines.append("## 📜 近 30 日回看（含当日沪深300表现）")
    lines.append("")
    lines.append("| 日期 | 家数 | 解禁市值(亿) | 当日沪深300涨跌% |")
    lines.append("|------|------|-------------|----------------|")
    for r in p:
        hs = "-" if r["hs300_chg"] is None else f"{r['hs300_chg']:+.2f}"
        lines.append(f"| {r['date']} | {_fmt_v(r['家数'])} | {_fmt_v(r['市值亿'])} | {hs} |")
    lines.append("")
    lines.append("> 声明：解禁为公开供给事件的事实清单，不构成投资建议。数据源东财（datacenter）。")
    return "\n".join(lines)


# ── v2 池模式：清单池下钻 + 变化检测 ─────────────────────────────────────

_STATE_DEFAULT = pathlib.Path.home() / ".local" / "share" / "investment" / "event_calendar_state.json"


def parse_pool(path: pathlib.Path) -> tuple[list[str], list[str]]:
    """清单文件解析：每行一个 6 位代码（# 注释、空行跳过）；坏行收集返回（不中断）。"""
    symbols: list[str] = []
    bad: list[str] = []
    for i, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if len(line) == 6 and line.isdigit():
            if line not in symbols:
                symbols.append(line)
        else:
            bad.append(f"L{i}: {raw.strip()[:40]}")
    return symbols, bad


def load_state(path: pathlib.Path) -> dict:
    """状态文件读取；不存在视为首跑；损坏 → 按首跑处理并告警（不中断）。"""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("symbols"), dict):
            return data
        print(f"⚠ 状态文件结构异常（{path}）——按首跑处理", file=sys.stderr)
    except FileNotFoundError:
        pass
    except Exception as exc:  # noqa: BLE001 — 损坏不阻断排雷主流程
        print(f"⚠ 状态文件不可读（{path}）：{exc}——按首跑处理", file=sys.stderr)
    return {"updated": None, "symbols": {}}


def save_state(path: pathlib.Path, state: dict) -> str | None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state, ensure_ascii=False, indent=1, sort_keys=True),
                        encoding="utf-8")
        return None
    except Exception as exc:  # noqa: BLE001
        return f"{type(exc).__name__}: {exc}"


def _batch_equal(a: dict, b: dict) -> bool:
    """批次字段全等（双侧 None 视为相等——区别于 freshness.values_equal 的 NULL 永不判等语义）。"""
    from freshness import values_equal

    for k in set(a) | set(b):
        va, vb = a.get(k), b.get(k)
        if va is None and vb is None:
            continue
        if not values_equal(va, vb):
            return False
    return True


def diff_batches(old: dict, new: dict) -> dict:
    """两批解禁批次（{date: {qty_yi, holders, kind}}）差异 → added/removed/changed。"""
    added = sorted(d for d in new if d not in old)
    removed = sorted(d for d in old if d not in new)
    changed = sorted(d for d in new if d in old and not _batch_equal(old[d], new[d]))
    return {"added": added, "removed": removed, "changed": changed}


def collect_pool(symbols: list[str], *, lookahead_days: int, include_past_days: int = 0,
                 sleep_s: float = 0.5) -> dict:
    """逐标的拉取（串行 + 间隔）；{symbol: {"batches": {date: {...}}, "error": str|None}}。"""
    out: dict[str, dict] = {}
    for idx, sym in enumerate(symbols):
        rows, err = fetch_symbol_unlocks(sym, lookahead_days=lookahead_days,
                                         include_past_days=include_past_days)
        out[sym] = {
            "batches": {r["date"]: {"qty_yi": r["qty_yi"], "holders": r["holders"],
                                    "kind": r["kind"]} for r in rows},
            "error": err,
        }
        if sleep_s and idx < len(symbols) - 1:
            time.sleep(sleep_s)
    return out


_FAR_FIELD_DAYS = 365  # 超过一年的解禁日：交易日历通常不覆盖 → 自然日粗判
# （2026-09-10 实测：2028-2030 批次曾被截断显示为同一交易日数 318）


def _trading_days_until(date_str: str, today: _dt.date) -> tuple[int | None, bool]:
    """事件日距今距离。

    近场（≤365 自然日）= 交易日数（freshness，长假口径安全）；
    远场 / 日历不可用 = 自然日数 + degraded=True（渲染为「自然日粗判」，
    防交易日历覆盖不足时给出被截断的错误交易日数）。
    """
    try:
        dd = _dt.date.fromisoformat(date_str)
    except (ValueError, TypeError):
        return None, True
    natural = (dd - today).days
    if natural > _FAR_FIELD_DAYS:
        return natural, True
    from freshness import trading_day_lag

    return trading_day_lag(fmt_date(today), date_str.replace("-", ""))


def render_pool_md(collected: dict, *, today: _dt.date, alert_days: int, lookahead_days: int,
                   pool_path: str, changes: dict | None,
                   baseline_symbols: list[str], state_updated: str | None) -> str:
    """池模式报告：提醒段 → 变化段 → 全池明细。数字全部引用采集结果（P0）。"""
    ok_n = sum(1 for v in collected.values() if not v["error"])
    fail_n = len(collected) - ok_n
    lines = [
        f"# 🔔 解禁排雷（池模式）— {fmt_date(today)}",
        "",
        f"> 池来源：{pool_path}（{len(collected)} 标的：成功 {ok_n} / 失败 {fail_n}）"
        f"｜拉取展望 {lookahead_days} 日｜提醒窗 {alert_days} 自然日",
        "> 解禁 ≠ 减持：本清单为供给事件提示；真减持须查减持预披露公告（双信号）。",
        "> 研究工具，非决策工具，不含任何买卖建议。",
        "",
    ]

    # ① 提醒段：提醒窗内有解禁批次的标的（距离按交易日计，升序）
    alerts: list[tuple] = []
    for sym, info in collected.items():
        if info["error"]:
            continue
        for d, b in info["batches"].items():
            dd = _dt.date.fromisoformat(d)
            if today < dd <= today + _dt.timedelta(days=alert_days):
                lag, degraded = _trading_days_until(d, today)
                alerts.append((lag if lag is not None else 10**6, sym, d, b, degraded))
    alerts.sort(key=lambda t: (t[0], t[1]))
    lines.append(f"## 🔔 提醒（未来 {alert_days} 自然日内有解禁）")
    lines.append("")
    if not alerts:
        lines.append(f"提醒窗内无解禁批次 ✅（{ok_n} 个标的成功拉取）")
    else:
        lines.append("| 代码 | 解禁日 | 距今(交易日) | 数量(亿股) | 股东数 | 类型 |")
        lines.append("|------|--------|------------|-----------|--------|------|")
        for lag, sym, d, b, degraded in alerts:
            lag_txt = "-" if lag >= 10**6 else f"{lag}{'（自然日粗判）' if degraded else ''}"
            qty = "-" if b.get("qty_yi") is None else f"{b['qty_yi']:.2f}"
            holders = "-" if b.get("holders") is None else str(b["holders"])
            lines.append(f"| {sym} | {d} | {lag_txt} | {qty} | {holders} | {b.get('kind') or '-'} |")
    lines.append("")

    # ② 变化段
    lines.append("## 🆕 变化（相较上次运行）")
    lines.append("")
    if changes is None:
        lines.append("状态未启用（--no-state）——跳过变化检测。")
    else:
        if baseline_symbols:
            lines.append(f"> ℹ 首次建基线 {len(baseline_symbols)} 个标的"
                         f"（{', '.join(baseline_symbols[:8])}{'…' if len(baseline_symbols) > 8 else ''}）"
                         "——本次不产变化告警")
            lines.append("")
        body = [(sym, ch) for sym, ch in sorted(changes.items())
                if ch["added"] or ch["removed"] or ch["changed"]]
        if not body:
            lines.append("无变化")
        else:
            lines.append("| 代码 | 变化 | 日期 | 详情 |")
            lines.append("|------|------|------|------|")
            for sym, ch in body:
                for d in ch["added"]:
                    b = collected[sym]["batches"][d]
                    qty = "-" if b.get("qty_yi") is None else f"{b['qty_yi']:.2f} 亿股"
                    lines.append(f"| {sym} | 🆕 新增 | {d} | {qty} |")
                for d in ch["removed"]:
                    lines.append(f"| {sym} | ❌ 消失 | {d} | 上次运行曾出现 |")
                for d in ch["changed"]:
                    lines.append(f"| {sym} | ✏️ 字段更新 | {d} | 数量/股东数/类型有变 |")
        if state_updated:
            lines.append("")
            lines.append(f"> 状态已更新：{state_updated}")
    lines.append("")

    # ③ 全池明细
    lines.append("## 📋 全池明细")
    lines.append("")
    lines.append("| 代码 | 解禁日 | 距今(交易日) | 数量(亿股) | 股东数 | 类型 | 备注 |")
    lines.append("|------|--------|------------|-----------|--------|------|------|")
    for sym in sorted(collected):
        info = collected[sym]
        if info["error"]:
            lines.append(f"| {sym} | - | - | - | - | - | ⚠ 取数失败：{info['error']} |")
            continue
        if not info["batches"]:
            lines.append(f"| {sym} | - | - | - | - | - | 无解禁记录（{lookahead_days} 日内） |")
            continue
        for d, b in sorted(info["batches"].items()):
            lag, degraded = _trading_days_until(d, today)
            lag_txt = "-" if lag is None else f"{lag}{'（自然日粗判）' if degraded else ''}"
            qty = "-" if b.get("qty_yi") is None else f"{b['qty_yi']:.2f}"
            holders = "-" if b.get("holders") is None else str(b["holders"])
            note = "🔔 提醒窗内" if (_dt.date.fromisoformat(d) <= today + _dt.timedelta(days=alert_days) and _dt.date.fromisoformat(d) > today) else ""
            lines.append(f"| {sym} | {d} | {lag_txt} | {qty} | {holders} "
                         f"| {b.get('kind') or '-'} | {note} |")
    lines.append("")
    lines.append("> 声明：解禁为公开供给事件的事实清单，不构成投资建议。数据源东财（个股解禁队列）。")
    return "\n".join(lines)


def _run_pool(args: argparse.Namespace) -> int:
    pf = pathlib.Path(args.pool_file)
    if not pf.exists():
        print(f"❌ 池文件不存在：{pf}", file=sys.stderr)
        return 2
    symbols, bad = parse_pool(pf)
    if bad:
        print(f"⚠ 池文件跳过 {len(bad)} 行：{'; '.join(bad[:5])}"
              f"{'…' if len(bad) > 5 else ''}", file=sys.stderr)
    if not symbols:
        print("❌ 池文件无有效 6 位代码", file=sys.stderr)
        return 2

    today = _dt.date.today()
    collected = collect_pool(symbols, lookahead_days=args.lookahead)
    if all(v["error"] for v in collected.values()):
        print("解禁数据不可得（池内全部标的取数失败）——不硬编。", file=sys.stderr)
        return 3

    changes: dict | None = None
    baseline_symbols: list[str] = []
    state_updated: str | None = None
    if not args.no_state:
        state_path = pathlib.Path(args.state_file)
        state = load_state(state_path)
        changes = {}
        for sym, info in collected.items():
            if info["error"]:
                continue  # 失败标的保留旧状态（不覆盖）
            old = (state["symbols"].get(sym) or {}).get("batches")
            if old is None:
                baseline_symbols.append(sym)
            else:
                changes[sym] = diff_batches(old, info["batches"])
            state["symbols"][sym] = {"batches": info["batches"],
                                     "last_run": fmt_date(today)}
        from dates import shanghai_now  # 时间戳与项目惯例一致（上海时区）

        state_updated = shanghai_now().strftime("%Y-%m-%d %H:%M")
        state["updated"] = state_updated
        err = save_state(state_path, state)
        if err:
            print(f"⚠ 状态写入失败（{err}）——下次运行将按首跑处理", file=sys.stderr)

    md = render_pool_md(collected, today=today, alert_days=args.alert_days,
                        lookahead_days=args.lookahead, pool_path=str(pf),
                        changes=changes, baseline_symbols=baseline_symbols,
                        state_updated=state_updated)
    if args.no_out:
        print(md)
    else:
        out_dir = pathlib.Path(args.out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"{fmt_date(today)}-pool.md"
        path.write_text(md, encoding="utf-8")
        print(md)
        print(f"\n已落盘: {path}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days-past", type=int, default=120, help="分位回看窗口（自然日，默认 120）")
    ap.add_argument("--days-future", type=int, default=30, help="展望窗口（自然日，默认 30）")
    ap.add_argument("--pool-file", type=str, default="", help="清单文件（池模式；每行一个 6 位代码，# 注释）")
    ap.add_argument("--alert-days", type=int, default=30, help="提醒窗（自然日，默认 30；池模式）")
    ap.add_argument("--lookahead", type=int, default=90, help="拉取展望（自然日，默认 90；池模式）")
    ap.add_argument("--state-file", type=str, default=str(_STATE_DEFAULT),
                    help="状态文件路径（池模式变化检测）")
    ap.add_argument("--no-state", action="store_true", help="不读写状态（调试）")
    ap.add_argument("--no-out", action="store_true")
    ap.add_argument("--out-dir", type=str, default=default_out_dir(__file__, "event-calendar"))
    args = ap.parse_args()

    if args.days_past < 1:
        ap.error("--days-past 须 ≥ 1")
    if args.days_future < 0:
        ap.error("--days-future 须 ≥ 0")
    if args.alert_days < 0 or args.lookahead < 0:
        ap.error("--alert-days/--lookahead 须 ≥ 0")

    if args.pool_file:
        return _run_pool(args)

    import akshare as ak

    today = _dt.date.today()
    # F12：拉取窗口对齐参数（原实现固定 past_days*2，约 44% 行解析后废弃）
    start = fmt_date(today - _dt.timedelta(days=args.days_past))
    end = fmt_date(today + _dt.timedelta(days=args.days_future))
    try:
        df = ak.stock_restricted_release_summary_em(symbol="全部股票", start_date=start, end_date=end)
    except Exception as exc:
        print(f"解禁数据不可得（东财阻断/代理）：{type(exc).__name__}", file=sys.stderr)
        return 3
    if df is None or df.empty:
        # F5 + R1 审查 F8：空返回可能是「窗口内合法无解禁」（窄窗口/全非交易日），
        # 也可能是数据不可得——先用交易日历鉴别；含交易日（或日历不可用）→ 判不可得
        has_td = _window_has_trading_day(start, end)
        if has_td is False:
            print(f"解禁数据为空且窗口内无交易日（{start}~{end}）——无可报告内容（exit 0）")
            return 0
        print("解禁数据不可得（东财空返回——疑代理阻断或接口变化），不硬编。", file=sys.stderr)
        return 3

    result = process(df, today, args.days_past, args.days_future)
    md = render_md(result, fmt_date(today), args.days_future)
    if args.no_out:
        print(md)
    else:
        out_dir = pathlib.Path(args.out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"{fmt_date(today)}.md"
        path.write_text(md, encoding="utf-8")
        print(md)
        print(f"\n已落盘: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
