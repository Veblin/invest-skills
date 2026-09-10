#!/usr/bin/env python3
"""限售解禁压力日历（invest-a-event-calendar v1）。

全市场解禁日汇总（东财限售股解禁 summary_em）：回看近 120 日 + 展望未来 30 日，
逐日解禁家数/解禁数量/实际解禁市值，未来压力日按「相对近 120 日分位」标注：
>80% 高压日 / >90% 极高压力日。

用法：
    cd "${INVEST_SKILLS_ROOT:-.}" && uv run python skills/invest-a-event-calendar/scripts/unlock_calendar.py
    # 输出 reports/event-calendar/{YYYY-MM-DD}.md

参数：
    --days-past N    回看窗口（自然日，默认 120——对齐 argparse 与 SKILL.md）
    --days-future N  展望窗口（默认 30 自然日）
    --no-out / --out-dir PATH

口径与边界：
    - 数据源：akshare stock_restricted_release_summary_em（东财，symbol=全部股票）
      ——代理环境若失败会报 ProxyError，输出「数据不可得」而非硬编
    - 原始字段：解禁数量/实际解禁数量 = 股；实际解禁市值 = 元（python 换算为亿）
    - 汇总口径缺失的日期 = 当日无解禁（东财页面语义），未来空窗日不列出
    - 分位 = 该未来日解禁市值在近 120 日有解禁日市值序列中的位置（bisect，同 pulse 口径）
    - 解禁 ≠ 减持：解禁是供给事件提示，不是方向信号（见 SKILL 分析纪律）
    - P0：全部计算由 pandas/bisect 完成；研究工具，非决策工具

退出码：0 正常；3 数据不可得（东财阻断）。
"""

from __future__ import annotations

import argparse
import bisect
import datetime as _dt
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[3]


def fmt_date(d: _dt.date) -> str:
    return d.strftime("%Y%m%d")


def percentile_rank(values: list[float], v: float) -> float:
    """v 在 values 中的分位（0-100，bisect_left 口径）。"""
    if not values:
        return 100.0
    s = sorted(values)
    return round(bisect.bisect_left(s, v) / len(s) * 100, 1)


def process(df, today: _dt.date, past_days: int, future_days: int) -> dict:
    """纯函数：换算单位 + 切窗 + 分位标注。P0：pandas 聚合。"""
    import pandas as pd

    if df is None or df.empty:
        return {"past": [], "future": [], "hist_billion": [], "error": None}
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

    def row(r):
        mv = float(r["市值亿"]) if pd.notna(r["市值亿"]) else None
        rank = percentile_rank(hist_rank_base, mv) if mv is not None else None
        flag = ""
        if rank is not None and mv is not None:
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

    past_rows = [row(r) for r in past.to_dict("records")]
    future_rows = [row(r) for r in future.to_dict("records")]
    return {"past": past_rows, "future": future_rows,
            "hist_billion": hist_rank_base, "past_days": past_days}


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
        lines.append("## 🔮 未来解禁日")
        lines.append("")
        lines.append("| 日期 | 家数 | 解禁数量(亿股) | 实际解禁市值(亿) | 近120日分位 | 标注 |")
        lines.append("|------|------|---------------|-----------------|------------|------|")
        for r in fut:
            rank = "-" if r["rank"] is None else f"{r['rank']}%"
            lines.append(f"| {r['date']} | {_fmt_v(r['家数'])} | {_fmt_v(r['数量亿股'])} "
                         f"| {_fmt_v(r['市值亿'])} | {rank} | {r['flag']} |")
    p = result["past"]
    lines.append("")
    lines.append("## 📜 近 30 日回看（含当日沪深300表现）")
    lines.append("")
    lines.append("| 日期 | 家数 | 解禁市值(亿) | 当日沪深300涨跌% |")
    lines.append("|------|------|-------------|----------------|")
    for r in p[-30:]:
        hs = "-" if r["hs300_chg"] is None else f"{r['hs300_chg']:+.2f}"
        lines.append(f"| {r['date']} | {_fmt_v(r['家数'])} | {_fmt_v(r['市值亿'])} | {hs} |")
    lines.append("")
    lines.append("> 声明：解禁为公开供给事件的事实清单，不构成投资建议。数据源东财（datacenter）。")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days-past", type=int, default=120, help="分位回看窗口（自然日，默认 120）")
    ap.add_argument("--days-future", type=int, default=30, help="展望窗口（自然日，默认 30）")
    ap.add_argument("--no-out", action="store_true")
    ap.add_argument("--out-dir", type=str, default=str(ROOT / "reports/event-calendar"))
    args = ap.parse_args()

    import akshare as ak

    today = _dt.date.today()
    start = fmt_date(today - _dt.timedelta(days=args.days_past * 2))
    end = fmt_date(today + _dt.timedelta(days=args.days_future))
    try:
        df = ak.stock_restricted_release_summary_em(symbol="全部股票", start_date=start, end_date=end)
    except Exception as exc:
        print(f"解禁数据不可得（东财阻断/代理）：{type(exc).__name__}", file=sys.stderr)
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
