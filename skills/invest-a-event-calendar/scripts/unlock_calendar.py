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
import os
import pathlib
import shutil
import sys
import tempfile
import time


def _ensure_lib_on_path() -> None:
    """共享库引导（R1 审查 F3）：单体仓库取 skills/lib；包内运行取 <pkg>/scripts/lib。"""
    here = pathlib.Path(__file__).resolve()
    for cand in (here.parents[2] / "lib", here.parents[1] / "scripts" / "lib"):
        if cand.is_dir():
            sys.path.insert(0, str(cand))
            return


_ensure_lib_on_path()

# 策展表路径由**本脚本**计算后传入数据层——不放进共享 lib：那里按 parents[N]
# 解析会指向 skills/ 而非本技能目录（仓库布局静默读错，包内才暴露）。
# 仓库布局 = <skill>/references/，包内 = <pkg>/references/，同一相对关系。
_REFS_DIR = pathlib.Path(__file__).resolve().parent.parent / "references"
_FOMC_DEFAULT = _REFS_DIR / "fomc_meetings.yaml"
_MACRO_RULES_DEFAULT = _REFS_DIR / "macro_sources.yaml"

from skill_paths import default_out_dir  # noqa: E402
import macro_calendar as macro_cal  # noqa: E402  宏观事件日历数据层（同目录模块）
from unlock_source import fetch_symbol_unlocks  # noqa: E402

try:  # 交易日历（空窗鉴别用）；包内未携带 invest lib 时降级为 None（保守判不可得）
    from invest_path import ensure_invest_a_scripts_on_path  # noqa: E402

    ensure_invest_a_scripts_on_path()
except Exception:  # pragma: no cover
    pass


def _window_has_trading_day(start: str, end: str) -> bool | None:
    """窗口 [start, end] 是否含交易日；日历**不可信** → None（调用方保守处理）。

    估算日历（无 token / 取数失败 → 工作日近似，**节假日混入**）不算权威：把它当
    「有交易日」会让长假窗口的空返回被误判成数据源故障（R0~R2 review 修复）。
    口径对齐 `freshness.trading_day_lag` 与 `sector_flow._is_trading_day`。
    """
    try:
        from lib.trade_cal import fetch_trade_cal

        dates, estimated = fetch_trade_cal(start, end)
        if estimated:
            return None
        return bool(dates)
    except Exception:
        return None


def fmt_date(d: _dt.date) -> str:
    return d.strftime("%Y%m%d")


def _beijing_today() -> _dt.date:
    """北京时区今日——报告日期/文件名/窗口一律按此口径。

    回归（R0~R2 review P2）：三种模式曾用 `date.today()`（**宿主机本地时区**），
    而同文件的状态戳已用 `shanghai_now()` → 美西机器在北京上午运行时，报告文件名
    与窗口都比北京日期晚一天，与同报告内的时间戳、以及仓库「文件名包含实际
    北京时间」的惯例自相矛盾。
    """
    try:
        from dates import shanghai_now

        return shanghai_now().date()
    except Exception:  # noqa: BLE001 —— 日历模块不可用不阻断，退回本地日期
        return _dt.date.today()


def _cell(v) -> str:
    """Markdown 表格单元格转义：远端事件名/备注可能含 | 或换行，转义防拆列。"""
    s = "" if v is None else str(v)
    return s.replace("|", "\\|").replace("\n", " ").strip()


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
    """状态文件读取；仅不存在视为首跑，损坏必须拒绝覆盖。"""
    if not path.exists():
        return {"updated": None, "symbols": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            # ⚠️ 不得因「缺 symbols 键」就丢弃整份数据：本文件与题材模块**共用**，
            # 只写过 themes 的文件在旧逻辑下被判「结构异常」→ save_state 整份覆写
            # → themes 永久消失（R4 评审实跑复现）。缺键补齐即可。
            if not isinstance(data.get("symbols"), dict):
                data["symbols"] = {}
            return data
        raise ValueError(f"状态文件结构异常（{path}）：顶层不是对象")
    except Exception as exc:  # noqa: BLE001 — 损坏不阻断排雷主流程
        raise ValueError(f"状态文件不可读（{path}）：{type(exc).__name__}——拒绝覆盖既有记录") from exc


def save_state(path: pathlib.Path, state: dict) -> str | None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            shutil.copy2(path, path.with_suffix(path.suffix + ".bak"))
        fd, tmp = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps(state, ensure_ascii=False, indent=1, sort_keys=True))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        return None
    except Exception as exc:  # noqa: BLE001
        try:
            pathlib.Path(tmp).unlink(missing_ok=True)
        except (NameError, OSError):
            pass
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


def diff_batches(old: dict, new: dict, *, not_before: str | None = None) -> dict:
    """两批解禁批次（{date: {qty_yi, holders, kind}}）差异 → added/removed/changed。

    not_before（YYYY-MM-DD）：仅在该日及之后比较 removed。抓取窗口严格前向
    （include_past_days=0），早于该日的批次从窗口消失属正常消化，不是记录被撤回
    ——否则每个解禁日过后的首次运行都会把**已真实发生**的解禁渲染成「❌ 消失」，
    淹没真正的新增/临近提醒。
    """
    added = sorted(d for d in new if d not in old)
    removed = sorted(d for d in old
                     if d not in new and (not_before is None or d >= not_before))
    changed = sorted(d for d in new if d in old and not _batch_equal(old[d], new[d]))
    return {"added": added, "removed": removed, "changed": changed}


def merge_rows_by_date(rows: list[dict]) -> dict[str, dict]:
    """同日多批解禁合并为一条日级总量（源按「解禁时间」逐条返回，同日可有多条）。

    以日期为键直接建字典会让后一批覆盖前一批，低估当日解禁规模（排雷用途受损）。
    口径：数量求和（全 None → None）、股东数求和（同）、类型去重后以 + 连接。
    """
    out: dict[str, dict] = {}
    for r in rows:
        d = r["date"]
        cur = out.get(d)
        if cur is None:
            out[d] = {"qty_yi": r["qty_yi"], "holders": r["holders"], "kind": r["kind"]}
            continue
        q1, q2 = cur["qty_yi"], r["qty_yi"]
        cur["qty_yi"] = (None if q1 is None and q2 is None
                         else round((q1 or 0.0) + (q2 or 0.0), 4))
        h1, h2 = cur["holders"], r["holders"]
        cur["holders"] = (None if h1 is None and h2 is None
                          else (h1 or 0) + (h2 or 0))
        kinds: list[str] = []
        for k in (cur["kind"], r["kind"]):
            if k and k not in kinds:
                kinds.append(k)
        cur["kind"] = "+".join(kinds)
    return out


def collect_pool(symbols: list[str], *, lookahead_days: int, include_past_days: int = 0,
                 sleep_s: float = 0.5, today: _dt.date | None = None) -> dict:
    """逐标的拉取（串行 + 间隔）；{symbol: {"batches": {date: {...}}, "error": str|None}}。

    ``today`` 须由调用方按**北京日期**传入（`_beijing_today`）：`fetch_symbol_unlocks`
    的缺省是宿主本地 `date.today()`，与同一次运行的报告名/提醒窗/`diff_batches` 差一天
    ——跨时区时窗口止于 host+90 而非北京+90，恰在边界的那批被静默滤掉，排雷工具
    给出假「无解禁记录」（R2 review P2；`catalyst.py` 同版本已显式传今日，契约既定）。
    """
    out: dict[str, dict] = {}
    for idx, sym in enumerate(symbols):
        rows, err = fetch_symbol_unlocks(sym, lookahead_days=lookahead_days,
                                         include_past_days=include_past_days, today=today)
        out[sym] = {"batches": merge_rows_by_date(rows), "error": err}
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

    today = _beijing_today()
    collected = collect_pool(symbols, lookahead_days=args.lookahead, today=today)
    if all(v["error"] for v in collected.values()):
        print("解禁数据不可得（池内全部标的取数失败）——不硬编。", file=sys.stderr)
        return 3
    # 全池**空返回且无错误** → 疑源侧空帧（东财反爬/限流会返回空而**不抛**）。
    # 照常渲染即是**假全清**（排雷工具给出「无解禁记录」比不报告更危险），且空批次
    # 会被写成新基线 → 下次 API 恢复时全部标 🆕 新增、再抖动一次又全标 ❌ 消失。
    # 故判不可得并**跳过状态写入**（基线保留，避免后续大规模假变动告警）。
    if collected and all(not v["error"] and not v["batches"] for v in collected.values()):
        print(f"解禁数据不可得（{len(collected)} 个标的**全部返回空**——疑源侧空帧/限流，"
              "不视为「无解禁」）——不硬编、不更新基线。", file=sys.stderr)
        return 3

    changes: dict | None = None
    baseline_symbols: list[str] = []
    state_updated: str | None = None
    if not args.no_state:
        state_path = pathlib.Path(args.state_file)
        try:
            state = load_state(state_path)
        except ValueError as exc:
            print(f"解禁状态不可得：{exc}——为保留题材/历史账本，本次不写入。", file=sys.stderr)
            return 3
        changes = {}
        # 本轮**存在**取数失败 → 空结果不可验证（源侧空帧与「确实无解禁」在本轮
        # 不可区分）：空批次不得覆写基线，否则同一轮就渲染出「❌ 消失」、恢复后
        # 又全标「🆕 新增」。全池空守卫要求池内无一失败，故部分限流会绕过它
        # ——守卫须做到**每标的一粒度**（R2 review P1）。
        degraded = any(v["error"] for v in collected.values())
        unverified: list[str] = []
        for sym, info in collected.items():
            if info["error"]:
                continue  # 失败标的保留旧状态（不覆盖）
            if not info["batches"] and degraded:
                unverified.append(sym)
                continue  # 不改基线、不算变化（下轮干净时再落账）
            old = (state["symbols"].get(sym) or {}).get("batches")
            if old is None:
                baseline_symbols.append(sym)
            else:
                changes[sym] = diff_batches(old, info["batches"],
                                            not_before=today.isoformat())
            state["symbols"][sym] = {"batches": info["batches"],
                                     "last_run": fmt_date(today)}
        from dates import shanghai_now  # 时间戳与项目惯例一致（上海时区）

        state_updated = shanghai_now().strftime("%Y-%m-%d %H:%M")
        state["updated"] = state_updated
        err = save_state(state_path, state)
        if err:
            print(f"⚠ 状态写入失败（{err}）——下次运行将按首跑处理", file=sys.stderr)
        if unverified:
            print(f"⚠ 本轮存在取数失败，{len(unverified)} 个标的返回空**不可验证**"
                  f"——已跳过其状态更新（基线保留，避免假 🆕/❌ 告警）："
                  f"{'、'.join(unverified)}", file=sys.stderr)

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


# ── 第三模式（v3）：定期宏观事件日程 ─────────────────────────────────────
#
# 诚实的呈现是这一模式的核心难点：三个源的覆盖窗口不同（中国 ≈30 天 /
# 美国 ≥3 个月 / 议息 人工表），且任一源都可能不可得。**「不可得」与「无事件」
# 是两种不同的事实**，报告必须分开呈现——把取数失败渲染成「无日程」会让用户
# 据一个错误的前提做判断（仓库历史缺陷模式 R1-F8 / R1-F13）。

_MACRO_CN_MAX_DAYS = 30   # 百度源前向窗实测 ≈30 天；不作超出承诺
_HIGHLIGHT_MAX_ITEMS = 6  # 重点段单日最多列几条（超出折叠为「…另 N 项」，完整日程不减）
_REGION_FLAG = {"中国": "🇨🇳", "美国": "🇺🇸", "日本": "🇯🇵"}
_WEEKDAYS = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")


def _weekday_cn(d: str) -> str:
    try:
        return _WEEKDAYS[_dt.date.fromisoformat(d).weekday()]
    except (ValueError, TypeError):
        return "—"


def _timeline_item(e, *, today_iso: str = "", now_hm: str = "") -> str:
    """时间轴上的一天里的一条事件：`<旗> 标题 <时刻>★ <已过时点?>`。

    时刻为北京口径 → 用北京当前时刻比较。仅对**当天**且**有时刻**的条目标注
    「✓已过时点」——否则读者分不清「今天这条出没出」（实测：报告生成于 22:50 时，
    当日 20:30 的 PPI 已出、次日 20:30 的 CPI 未出，表上却看不出差别）。

    措辞取「已过**时点**」而非「已公布」：这是按排期做的时间比较，不代表源侧
    确实已发布（发布可能推迟）。
    """
    flag = _REGION_FLAG.get(e.region, e.region)
    t = f" {e.time}" if e.time else ""
    star = "★" if "SEP" in (e.note or "") else ""
    mark = ""
    if (today_iso and now_hm and e.date == today_iso and e.time
            and e.time <= now_hm):
        mark = " ✓已过时点"
    return f"{flag} {_cell(e.title)}{t}{star}{mark}"


def render_macro_md(view: dict, *, today: _dt.date, days: int,
                    window: tuple[str, str], fomc_warnings: list[str]) -> str:
    """宏观事件日历报告：源覆盖矩阵 → 分区段 → 降级项（数字全部引用引擎输出）。"""
    w0, w1 = window
    fmt = lambda d: f"{d[:4]}-{d[4:6]}-{d[6:]}"          # noqa: E731
    try:   # 时刻为北京口径 → 用上海时区当前时刻比较（同项目惯例）
        from dates import shanghai_now

        now_hm = shanghai_now().strftime("%H:%M")
    except Exception:  # noqa: BLE001 —— 取不到当前时刻只放弃标注，不影响排期
        now_hm = ""
    today_iso = today.isoformat()
    lines = [
        f"# 🗓 宏观事件日历 — {today.isoformat()}",
        "",
        f"> 展望窗：{fmt(w0)} ~ {fmt(w1)}（{days} 自然日）"
        f"｜中国区受源限制仅覆盖前 {_MACRO_CN_MAX_DAYS} 天",
        "> 本表为**排定日程**（周期性、非突发）；公布值与市场反应不在此范围。",
        "> 研究工具，非决策工具，不含任何买卖建议。",
        "",
    ]

    # ⭐ 重点事件（头部速览）：只取策展档位「高」；完整日程见下节，全集不减
    high_by_date: dict[str, list] = {}
    for e in view["events"]:
        if e.importance == "高":
            high_by_date.setdefault(e.date, []).append(e)
    lines += ["## ⭐ 重点事件（对市场影响较大）", ""]
    if high_by_date:
        lines += ["| 日期 | 星期 | 距今(交易日) | 事件 |",
                  "|------|------|------------|------|"]
        for d in sorted(high_by_date):
            items = sorted(high_by_date[d], key=lambda e: (e.time or "99:99", e.region))
            lag, degraded = _trading_days_until(d, today)
            lag_txt = "-" if lag is None else f"{lag}{'（自然日粗判）' if degraded else ''}"
            shown = items[:_HIGHLIGHT_MAX_ITEMS]
            cell = " · ".join(_timeline_item(e, today_iso=today_iso, now_hm=now_hm)
                              for e in shown)
            if len(items) > len(shown):
                # 兜底上限：源可能把一次发布拆成十几条，重点段必须保持可扫读；
                # 被截断的部分在下方完整日程中仍可见（不丢信息）
                cell += f" · …另 {len(items) - len(shown)} 项（见下节完整日程）"
            lines.append(f"| {d} | {_weekday_cn(d)} | {lag_txt} | {cell} |")
    else:
        lines.append("— 本窗内无高影响事件 [来源: macro_calendar.build_view]")
    lines += [
        "",
        "> 重点判定 = 策展档位「高」（见 `references/macro_sources.yaml`）——"
        "是**影响量级**的判断，不是方向判断。完整日程（含中/低档）见下节。",
        "> ⚠ **覆盖边界与降级项同样约束本节**：源不可得或窗口外的事件不会出现在这里，"
        "见下方源覆盖矩阵。",
        "",
        "## 📡 源覆盖矩阵",
        "",
        "| 区域/类别 | 源 | 覆盖至 | 状态 |",
        "|------|------|--------|------|",
    ]
    for r in view["results"]:
        if r.error:
            status = f"❌ 不可得：{r.error}"
        elif r.failed_days:
            status = f"⚠ 部分失败：{len(r.failed_days)} 天取数失败"
        elif r.events:
            status = "✅ 正常"
        elif r.ok_days:
            # 源有返回、事件却为空 → 是**筛选**（区域/白名单）造成的，不是源里没有：
            # 说成「窗口内无排期」会让用户以为无事可做，而真因要改配置。
            status = f"— 筛选后无排期（源 {r.ok_days} 天有数据）"
        elif r.empty_days:
            # 契约守卫：源结果自述「整窗零成功日」时，渲染层**任何情况下**都不得
            # 出「窗口内无排期」。百度源会先在源头判不可得（走上方 error 分支），
            # 此分支覆盖其它来源/构造路径，避免同一误渲染再次出现。
            status = f"⚠ 整窗 {r.empty_days} 天返回空（不可得，≠ 无事件）"
        else:
            status = "— 窗口内无排期"
        regions = "、".join(sorted({e.region for e in r.events})) or "—"
        lines.append(f"| {regions} | {r.name} | {r.coverage_end or '—'} | {status} |")
    for w in fomc_warnings:
        lines.append(f"| 🇺🇸 美国（议息） | 人工策展表 | — | ⚠ {w} |")
    lines += [
        "",
        "> 覆盖列为**实测观测值**（最后一个有事件的日期），非承诺窗口；"
        "「不可得」是取数失败，**不等于没有事件**。",
        "> 事件按 `references/macro_sources.yaml` **策展白名单**收录——"
        "源中未收录的条目不在表内（白名单是覆盖口径的一部分）。",
        "",
    ]

    # 时间轴：**日期为统一尺度**，一天一行，格内含当日全部区域事件
    by_date: dict[str, list] = {}
    for e in view["events"]:
        by_date.setdefault(e.date, []).append(e)
    lines += [
        "## 📅 日程（按**北京日期**归并）",
        "",
        f"> 窗口内 **{len(by_date)}** 天有排期（仅列有排期的日期）"
        " [来源: macro_calendar.build_view]；区域标识：🇨🇳 中国 · 🇺🇸 美国 · 🇯🇵 日本",
        "",
        "| 日期 | 星期 | 事件 |",
        "|------|------|------|",
    ]
    for d in sorted(by_date):
        items = sorted(by_date[d], key=lambda e: (e.time or "99:99", e.region, e.title))
        lines.append(f"| {d} | {_weekday_cn(d)} | "
                     f"{' · '.join(_timeline_item(e, today_iso=today_iso, now_hm=now_hm)
                                   for e in items)} |")
    lines += [
        "",
        "> 时区口径：**表中「时刻」列一律为北京时间**（百度源实测：61/61 个美国事件的"
        "时刻 = 公认美东发布时刻 +12h，即 8:30 ET → 20:30 北京；中国/日本同源同口径）。",
        "> 美国（FRED）条目**无时刻**（源不提供，不推测），其日期为**美东日期口径**"
        "——该类发布多在美东上午，对应北京时间当日；议息见下条。",
        "> **✓已过时点** = 当天且该时刻已过（按北京时刻比较）。这只是按**排期**做的"
        "时间比较，**不代表源侧确实已发布**（发布可能推迟）；无时刻的条目无法判断。",
    ]
    cov_cn = view["coverage"].get("中国")
    if cov_cn:
        lines.append(f"> ⚠ 中国区本次**事件覆盖至 {cov_cn}**；该日之后的本区日程"
                     "**不可得（不等于没有事件）**——源前向窗有限（实测 ≈"
                     f"{_MACRO_CN_MAX_DAYS} 天），需进入窗口后再看。")
    if any(e.source == "fomc" for e in view["events"]):
        lines.append("> 🏛 议息会议：决议为**美东 14:00**、表中日期已是**北京次日**；"
                     "含 SEP（经济预测摘要/点阵图）者标 ★。官方注：会议日期在紧邻的"
                     "上次会议确认前均为**暂定**。")
    if fomc_warnings:
        lines.append(f"> ❌ 议息：{fomc_warnings[0]}")
    lines.append("")

    # 降级与过滤留痕（过滤发生了必须让人知道，否则无法区分「源没有」与「被滤掉」）
    lines += ["## 🧾 本次降级与过滤", ""]
    if view["errors"]:
        lines += [f"- ❌ {msg}" for msg in view["errors"]]
    if view["filtered"]:
        fams = "、".join(f[:28] for f in view["filtered_families"][:4])
        lines.append(f"- 🧹 已过滤每日类噪音 **{view['filtered']}** 条"
                     f"（{len(view['filtered_families'])} 类：{fams}…）"
                     " [来源: macro_calendar.filter_noise]")
    for n in view["notes"]:
        lines.append(f"- ℹ {n}")
    if len(lines) and lines[-1] == "":
        lines.append("- 无")
    lines += [
        "",
        "> 声明：宏观数据日程为公开信息（百度财经日历 / FRED / 联邦储备官网），"
        "仅描述排期事实，不构成投资建议。",
    ]
    return "\n".join(lines)


def _run_macro(args: argparse.Namespace) -> int:
    today = _beijing_today()
    days = args.macro_days
    regions = [r.strip() for r in str(args.macro_regions).split(",") if r.strip()]
    w_start, w_end = macro_cal.date_range(days, today=today)

    try:
        rules = macro_cal.load_rules(getattr(args, "macro_rules_file", None) or None)
    except ValueError as exc:
        # 规则表决定区域白名单、影响档位与噪音过滤；不能在其不可读时输出
        # 「无排期」或无标注的默认口径报告。
        print(f"宏观日程不可得（策展规则不可用）：{exc}", file=sys.stderr)
        return 3
    results: list[macro_cal.SourceResult] = []

    baidu_regions = tuple(r for r in regions if r in _REGION_FLAG)
    if baidu_regions:
        baidu_end = macro_cal.date_range(min(days, _MACRO_CN_MAX_DAYS), today=today)[1]
        results.append(macro_cal.fetch_baidu_calendar(
            w_start, baidu_end, regions=baidu_regions, rules=rules,
            cookie=getattr(args, "baidu_cookie", None) or None))
    if "美国" in regions:
        try:
            from lib.env import get_config
            fred_key = (get_config() or {}).get("FRED_API_KEY")
        except Exception:  # noqa: BLE001 —— 配置不可读按未配置处理（显式降级）
            fred_key = None
        results.append(macro_cal.fetch_us_calendar(
            w_start, w_end, fred_key=fred_key, rules=rules))

    fomc_events, fomc_warnings = macro_cal.load_fomc_meetings(
        args.fomc_file, today=today.strftime("%Y%m%d"))
    fomc_events = [e for e in fomc_events if w_start <= e.date.replace("-", "") <= w_end]
    if "美国" in regions or not results:
        # v0.3.0 C3：策展表不可得（文件缺失/解析失败/已过期）时必须走 `error` 字段
        # ——SourceResult 的契约是「error 非空 = 该源**不可得**（≠ 无事件）」。旧实现
        # 丢弃 fomc_warnings 构造出 error=None 的 SourceResult，覆盖矩阵于是同时渲染
        # 「— 窗口内无排期」与「⚠ FOMC 策展表不可得」，把不可得写成无事件（LAW 5）。
        results.append(macro_cal.SourceResult(
            "FOMC 策展表", fomc_events,
            error=("；".join(fomc_warnings) if fomc_warnings else None),
            coverage_end=max((e.date for e in fomc_events), default=None)))

    view = macro_cal.build_view(results)
    view["results"] = results

    if not view["events"]:
        print("宏观日程不可得（全部源失败或无内容）——不硬编。", file=sys.stderr)
        for msg in view["errors"] or ["无可呈现的日程来源"]:
            print(f"  {msg}", file=sys.stderr)
        # v0.3.0 C3：fomc_warnings 已随 `error=` 进入 view["errors"]（build_view 收集
        # r.error），此处不再单独打印，避免同一告警出现两次。
        return 3

    md = render_macro_md(view, today=today, days=days, window=(w_start, w_end),
                         fomc_warnings=fomc_warnings)
    # R-D04：政治/宏观不确定性窗口——**接在宏观报告尾部**（此前 load/render 已实现但
    # 零调用方 → 2026-11-03 美国中期选举窗口从未出现在任何输出里）。
    # 不可得时渲染「⚠️ 不可得：…」而非静默省略（策展表维护纪律）。
    md = md + "\n\n" + macro_cal.render_political_windows(today=today.isoformat())
    if getattr(args, "no_out", False):
        print(md)
    else:
        out_dir = pathlib.Path(args.out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"{fmt_date(today)}-macro.md"
        path.write_text(md, encoding="utf-8")
        print(md)
        print(f"\n已落盘: {path}")
    return 0


def _run_theme(args) -> int:
    """题材日历记账（R-D01）——转交 theme_calendar，**共用状态文件但只写 themes 键**。

    退出码：0 正常 / 2 参数或锚点来源非法（含「热度 ≠ 证实」拒绝）。
    """
    import theme_calendar as tc

    if args.no_state:
        # v0.3.0 C4：`--theme` 与 `--no-state` 语义矛盾——`register_theme` 恒落盘
        # （theme_calendar 无内存模式），故「不读写状态」下**无法**完成登记。
        # 旧实现打印「登记未持久化」却零登记、退出码 0：读者被暗示「已登记，只是
        # 没落盘」，复盘时该题材并不存在（静默空结果，违反 D5）。此处改为 fail-loud。
        # 注：原三元表达式 `render_themes(...) if not args.no_state else …` 位于
        # `if args.no_state:` 之内，条件恒假、只能走 else，属死分支。
        print("❌ --no-state 下无法登记题材：登记需落盘状态文件（无内存模式）。"
              "请去掉 --no-state，或改用只读模式查询台账。", file=sys.stderr)
        return 2

    concepts = [c for c in (args.concepts or "").split(",") if c.strip()]
    try:
        dw = (tc.make_demand_window(args.demand_window, source=args.demand_source,
                                    lit_note=args.demand_lit_note)
              if args.demand_window else None)
        hw = (tc.make_hype_window(args.hype_window, source=args.hype_source)
              if args.hype_window else None)
        rec = tc.register_theme(args.theme, stage=args.stage, anchor=args.anchor,
                                anchor_source=args.anchor_source, concepts=concepts,
                                confirmed_date=args.confirmed_date,
                                note=args.theme_note, demand_window=dw, hype_window=hw,
                                state_file=args.state_file)
    except ValueError as exc:
        print(f"❌ {exc}", file=sys.stderr)
        return 2
    print(f"✅ 已登记题材：{rec['theme']} / {rec['stage']}"
          f"（锚点来源 {rec['anchor_source']}）")
    print(tc.render_themes(state_file=args.state_file))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days-past", type=int, default=120, help="分位回看窗口（自然日，默认 120）")
    ap.add_argument("--days-future", type=int, default=30, help="展望窗口（自然日，默认 30）")
    ap.add_argument("--pool-file", type=str, default="", help="清单文件（池模式；每行一个 6 位代码，# 注释）")
    ap.add_argument("--theme", type=str, default="",
                    help="题材日历记账（R-D01；与 --macro/--pool-file 三向互斥）")
    ap.add_argument("--stage", type=str, default="", help="题材阶段（首波/扩散/延伸/兑现）")
    ap.add_argument("--anchor", type=str, default="", help="证实锚点（可核验事件）")
    ap.add_argument("--anchor-source", type=str, default="", help="锚点来源（须官方/权威）")
    ap.add_argument("--concepts", type=str, default="", help="概念（逗号分隔）")
    ap.add_argument("--confirmed-date", type=str, default=None, help="证实日（阶段=兑现时必填）")
    ap.add_argument("--theme-note", type=str, default="", help="备注（描述性，勿写方向判断）")
    ap.add_argument("--demand-window", type=str, default="", help="需求窗口（基本面季节性；须 --demand-source）")
    ap.add_argument("--demand-source", type=str, default="", help="需求窗口来源")
    ap.add_argument("--demand-lit-note", type=str, default="", help="需求窗口文献注记")
    ap.add_argument("--hype-window", type=str, default="", help="炒作窗口（从业者惯例；禁收益预期）")
    ap.add_argument("--hype-source", type=str, default="", help="炒作窗口出处")
    ap.add_argument("--macro", action="store_true",
                    help="宏观日程模式：定期宏观数据发布日程（中美 CPI/社零/非农）+ 议息会议")
    ap.add_argument("--macro-days", type=int, default=90, help="宏观展望窗（自然日，默认 90）")
    ap.add_argument("--macro-regions", type=str, default="中国,美国,日本",
                    help="宏观区域（逗号分隔，默认 中国,美国,日本）")
    ap.add_argument("--macro-rules-file", type=str, default=str(_MACRO_RULES_DEFAULT),
                    help="宏观策展规则表（默认 references/macro_sources.yaml）")
    ap.add_argument("--fomc-file", type=str, default=str(_FOMC_DEFAULT),
                    help="FOMC 会议策展表（默认 references/fomc_meetings.yaml）")
    ap.add_argument("--baidu-cookie", type=str, default="",
                    help="百度财经日历 cookie（可选；复用可显著降低逐日失败率）")
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
    if args.macro and args.pool_file:
        # 互斥必须显式报错：下方 `if args.pool_file:` 在前，否则 --macro 会被静默忽略
        ap.error("--macro 与 --pool-file 互斥（宏观日程 / 解禁池分属两种模式）")
    if args.theme and (args.macro or args.pool_file):
        ap.error("--theme 与 --macro/--pool-file 互斥（题材记账 / 宏观日程 / 解禁池分属三种模式）")
    if args.theme and not (args.stage and args.anchor and args.anchor_source):
        ap.error("--theme 模式须同时给 --stage / --anchor / --anchor-source")
    if args.macro and args.macro_days < 1:
        ap.error("--macro-days 须 ≥ 1")
    if args.alert_days > args.lookahead:
        # 提醒窗宽于拉取窗 → 窗内批次根本不在 batches 里，报告仍会打印
        # 「提醒窗内无解禁批次 ✅」，即假全清。两者须同源可比。
        ap.error(f"--alert-days({args.alert_days}) 不得大于 --lookahead({args.lookahead})"
                 "：提醒窗宽于拉取窗会产出假「无解禁批次」结论")

    if args.theme:
        return _run_theme(args)

    if args.macro:
        return _run_macro(args)      # 不触碰 event_calendar_state.json（解禁状态）

    if args.pool_file:
        return _run_pool(args)

    import akshare as ak

    today = _beijing_today()
    # F12：拉取窗口对齐参数（原实现固定 past_days*2，约 44% 行解析后废弃）
    start = fmt_date(today - _dt.timedelta(days=args.days_past))
    end = fmt_date(today + _dt.timedelta(days=args.days_future))
    try:  # 东财直连 + ≥0.5s 节流（与 unlock_source 同一会话口径；CLAUDE.md：东财需直连）
        from lib.proxy import akshare_direct_session
    except Exception:  # 库引导不可用 → 无会话退化（不阻断取数，行为同修复前）
        from contextlib import nullcontext as akshare_direct_session

    try:
        with akshare_direct_session():
            df = ak.stock_restricted_release_summary_em(
                symbol="全部股票", start_date=start, end_date=end)
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
        if has_td is None:
            # 日历不可信（估算日历/不可用）→ **无法鉴别**「窗口无交易日」与「取数失败」。
            # 不得归因成「疑代理阻断」：那是把日历缺失说成了数据源故障。
            print(f"解禁数据为空，且交易日历不可信（估算日历/不可用）——"
                  f"无法鉴别「窗口无交易日（{start}~{end}）」与「取数失败」；"
                  f"配 TUSHARE_TOKEN 可消除该不确定性。不硬编。", file=sys.stderr)
            return 3
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
