#!/usr/bin/env python3
"""业绩预告雷达 — tushare forecast 全市场扫描（invest-a-forecast-scan）。

用法：
    cd "${INVEST_SKILLS_ROOT:-.}" && uv run python skills/invest-a-forecast-scan/scripts/forecast_scan.py
    # 扫最近 N 天披露的业绩预告（默认 10 自然日），正面清单落盘 reports/forecast-scan/

退出码：0 正常（有披露或空窗已鉴别为真实淡季）；1 部分日期取数失败
      （报告已落盘、头部含「数据缺口」警示）；2 数据源不可用/无法鉴别（不落盘）；
      3 真实空窗（淡季，报告已落盘标注「无新披露」）。

参数：
    --days N         扫描窗口（自然日，默认 10）
    --ann-date YYYYMMDD   指定单日（调试/补扫；与 --days 互斥）
    --min-gain PCT    预增过滤阈值 p_change_max>=PCT（默认 30%）
    --no-out          不落盘，仅打印
    --out-dir PATH    输出目录（默认 reports/forecast-scan）

设计要点：
    - tushare forecast 的 ann_date 仅接受单日（实测范围形式静默返回 0/报错），
      窗口扫描 = 逐自然日调用（预告窗口期每日 1 次，配额友好）
    - type 为中文枚举：预增/略增/扭亏/续盈/减亏(正面) 预减/首亏/续亏/略减/增亏(负面)
    - 增幅区间为 p_change_min~p_change_max（%）；net_profit_* 单位万元
    - 「超预期」无一致预期数据（report_rc 需 10000 分），本工具口径 =
      公司自披露预告相对自身上年同期的增减幅，非相对市场预期

P0：所有统计由 pandas 完成，禁止目视计数/心算。
红线：纯事实清单，无买卖建议。研究工具，非决策工具。
"""

from __future__ import annotations

import argparse
import datetime as _dt
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[3]  # code/（repo root）

# 共享路径引导统一走 skills/lib/invest_path.py（防手写 sys.path 造成 lib 包遮蔽）
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "lib"))
from invest_path import ensure_invest_a_scripts_on_path  # noqa: E402

ensure_invest_a_scripts_on_path()

from lib.tushare_client import TushareClient  # noqa: E402

FIELDS = (
    "ts_code,ann_date,end_date,type,p_change_min,p_change_max,"
    "net_profit_min,net_profit_max,summary"
)

POSITIVE_TYPES = {"预增", "略增", "扭亏", "续盈", "减亏"}
NEGATIVE_TYPES = {"预减", "首亏", "续亏", "略减", "增亏"}


def fetch_day(client: TushareClient, ann_date: str) -> tuple[list[dict], bool]:
    """拉取单日全市场业绩预告。

    returns (rows, ok)：
      ok=True + 空 rows  = 当日确无披露（或非交易日）
      ok=False          = 取数异常/接口拒绝（40401/40203/网络）—— 与空窗区分，
                          供 scan_window 汇总 failed_days（R0 审查 F4：部分失败不得静默）
    """
    try:
        df = client.query("forecast", fields=FIELDS, ann_date=ann_date)
        if df is None:
            return [], False
        if df.empty:
            return [], True
        return df.to_dict("records"), True
    except Exception:
        return [], False


def scan_window(client: TushareClient, window: list[str], *, sleep_s: float = 0.3,
                log=print) -> tuple[list[dict], list[str]]:
    """逐日拉取窗口；返回 (records, failed_days)。

    failed_days = fetch_day ok=False 的日期列表——调用方必须区分「空窗」与
    「取数失败」，并在报告/退出码中显式呈现（不得把故障静默记成淡季）。
    """
    records: list[dict] = []
    failed: list[str] = []
    for d in window:
        day_rows, ok = fetch_day(client, d)
        if not ok:
            failed.append(d)
        elif day_rows:
            records.extend(day_rows)
            log(f"  {d}: {len(day_rows)} 条")
        if sleep_s:
            time.sleep(sleep_s)
    return records, failed


def fetch_basic(client: TushareClient) -> dict[str, dict[str, str]]:
    """ts_code -> {name, industry}（一次性拉取缓存）。"""
    try:
        df = client.query("stock_basic", fields="ts_code,name,industry")
        if df is None or df.empty:
            return {}
        return {
            r["ts_code"]: {"name": r.get("name", ""), "industry": r.get("industry", "")}
            for r in df.to_dict("records")
        }
    except Exception:
        return {}


def trading_days_in_window(days: int) -> list[str]:
    """近 days 个自然日的日期串（倒序），含非交易日（当日调用返回空即跳过）。"""
    today = _dt.date.today()
    return [(today - _dt.timedelta(days=i)).strftime("%Y%m%d") for i in range(days)]


def _summary_text(v) -> str:
    """summary 安全截断（R0 审查 F1）：pandas 3 的 str dtype 会把 JSON null 变 NaN，
    NaN 为真值（`nan or ""` 仍得 nan）→ `nan[:60]` TypeError 中止整个扫描。
    None/NaN/非字符串一律返回 ''。"""
    if v is None:
        return ""
    if isinstance(v, float) and v != v:  # NaN
        return ""
    return str(v)[:60]


def analyze(records: list[dict], basic: dict[str, dict[str, str]], min_gain: float) -> dict:
    """纯函数：过滤/排序/统计。P0：全部聚合由 pandas 完成。

    returns {
      counts: {type: n}           // 窗口内全部 type 计数
      gainers: [...],             // 预增且 p_change_max>=min_gain，按 p_change_max 降序
      turnarounds: [...],         // 扭亏，按 net_profit_max 降序
      negatives: [...],           // 首亏/预减，按 |p_change_min| 降序（风险关注）
      window_days: n              // 实际有数据的交易日数
    }
    """
    import pandas as pd

    if not records:
        return {"counts": {}, "gainers": [], "turnarounds": [], "negatives": [],
                "window_days": 0, "all": []}
    df = pd.DataFrame(records)
    df["name"] = df["ts_code"].map(lambda c: basic.get(c, {}).get("name", ""))
    df["industry"] = df["ts_code"].map(lambda c: basic.get(c, {}).get("industry", ""))
    for col in ("p_change_min", "p_change_max", "net_profit_min", "net_profit_max"):
        df[col] = pd.to_numeric(df[col], errors="coerce")

    counts = df["type"].value_counts().to_dict()
    window_days = int(df["ann_date"].nunique())

    def norm(r: dict) -> dict:
        pmin = r.get("p_change_min")
        pmax = r.get("p_change_max")
        npmin = r.get("net_profit_min")
        npmax = r.get("net_profit_max")
        return {
            "ts_code": r["ts_code"], "name": r["name"], "industry": r["industry"],
            "ann_date": str(r["ann_date"]), "end_date": str(r.get("end_date", "")),
            "type": r["type"],
            "p_chg": f"{pmin if pmin == pmin else '-'}~{pmax if pmax == pmax else '-'}"
            if pd.notna(pmin) or pd.notna(pmax) else "-",
            "p_max": float(pmax) if pd.notna(pmax) else None,
            "p_min": float(pmin) if pd.notna(pmin) else None,
            "np_range": f"{npmin if npmin == npmin else '-'}~{npmax if npmax == npmax else '-'}"
            if pd.notna(npmin) or pd.notna(npmax) else "-",
            "np_max": float(npmax) if pd.notna(npmax) else None,
            "summary": _summary_text(r.get("summary")),
        }

    gain = df[(df["type"] == "预增") & (df["p_change_max"].fillna(0) >= min_gain)]
    gainers = [norm(r) for r in gain.sort_values("p_change_max", ascending=False).to_dict("records")]
    turn = df[df["type"] == "扭亏"]
    turnarounds = [norm(r) for r in turn.sort_values("net_profit_max", ascending=False).to_dict("records")]
    neg = df[df["type"].isin({"首亏", "预减"})]
    neg["_key"] = neg["p_change_min"].abs()
    negatives = [
        norm(r) for r in neg.sort_values("_key", ascending=False).head(30).to_dict("records")
    ]
    # 全部明细（报告自包含——淡季窗口清单可能为空，明细段兜底可见「是谁」）
    type_order = ["预增", "略增", "扭亏", "续盈", "减亏", "预减", "略减", "首亏", "续亏", "增亏"]
    df["_ord"] = df["type"].map({t: i for i, t in enumerate(type_order)}).fillna(99)
    all_rows = [
        norm(r) for r in df.sort_values(["_ord", "ann_date"]).to_dict("records")
    ]
    return {"counts": counts, "gainers": gainers, "turnarounds": turnarounds,
            "negatives": negatives, "window_days": window_days, "all": all_rows}


def _cell(v) -> str:
    """Markdown 表格单元格转义（review F8）：远端 summary 可能含 |/换行，转义防拆列。"""
    return str(v).replace("|", "\\|").replace("\n", " ").replace("\r", " ")


def render_md(result: dict, window: list[str], failed_days: list[str] | None = None) -> str:
    """渲染 Markdown 报告（数字全部来自 analyze 的 pandas 输出，禁止手写）。"""
    lines: list[str] = []
    if len(window) > 1:
        w0, w1 = window[-1], window[0]
        lines.append(f"# 📡 业绩预告雷达 — {w0[:4]}-{w0[4:6]}-{w0[6:]} ~ {w1[:4]}-{w1[4:6]}-{w1[6:]} 窗口")
    else:
        d0 = window[0]
        lines.append(f"# 📡 业绩预告雷达 — {d0[:4]}-{d0[4:6]}-{d0[6:]} 窗口")
    lines.append("")
    lines.append(f"> 扫描窗口：近 {len(window)} 自然日（有数据交易日 {result['window_days']} 个）| 数据源：tushare forecast")
    lines.append("> 口径：公司自披露预告相对上年同期增减幅（无一致预期数据，「超预期」不作断言）")
    lines.append("> 报告文件：见对话输出 | 研究工具，非决策工具，不含任何买卖建议")
    if failed_days:
        shown = ", ".join(failed_days[:10]) + ("…" if len(failed_days) > 10 else "")
        lines.append(f"> ⚠️ **数据缺口**：{len(failed_days)} 个日期取数失败（{shown}）"
                     "——下方统计不含这些日期，结论请按缺口折减。")
    lines.append("")
    c = result["counts"]
    if not c:
        lines.append("**窗口内无新披露业绩预告**（预告窗口期通常在 1/4/7/10 月）。")
        return "\n".join(lines)
    lines.append("## 窗口披露统计（type 计数）")
    lines.append("")
    total = sum(c.values())
    lines.append(f"- 合计 {total} 条（{result['window_days']} 个披露日）")
    order = ["预增", "略增", "扭亏", "续盈", "减亏", "预减", "略减", "首亏", "续亏", "增亏"]
    for t in order:
        if t in c:
            share = round(c[t] / total * 100, 1)
            lines.append(f"- {t} {c[t]} 条（{share}%）")
    lines.append("")
    g, t, n = result["gainers"], result["turnarounds"], result["negatives"]
    if g:
        lines.append("## ✅ 预增 ≥ 阈值（按增幅上限降序）")
        lines.append("")
        lines.append("| 代码 | 名称 | 行业 | 披露日 | 报告期 | 增幅区间% | 净利区间(万) | 摘要 |")
        lines.append("|------|------|------|--------|--------|-----------|-------------|------|")
        for r in g[:40]:
            lines.append(f"| {r['ts_code']} | {_cell(r['name'])} | {_cell(r['industry'])} | {r['ann_date']} "
                         f"| {r['end_date']} | {r['p_chg']} | {r['np_range']} | {_cell(r['summary'])} |")
        if len(g) > 40:
            lines.append(f"> 表内 {40}/{len(g)} 条（截断说明；其余见下方「披露明细」段）")
        lines.append("")
    if t:
        lines.append("## 🔄 扭亏（按净利上限降序）")
        lines.append("")
        lines.append("| 代码 | 名称 | 行业 | 披露日 | 报告期 | 净利区间(万) | 摘要 |")
        lines.append("|------|------|------|--------|--------|-------------|------|")
        for r in t[:30]:
            lines.append(f"| {r['ts_code']} | {_cell(r['name'])} | {_cell(r['industry'])} | {r['ann_date']} "
                         f"| {r['end_date']} | {r['np_range']} | {_cell(r['summary'])} |")
        if len(t) > 30:
            lines.append(f"> 表内 {30}/{len(t)} 条（截断说明；其余见下方「披露明细」段）")
        lines.append("")
    if n:
        lines.append("## ⚠️ 负面关注（首亏/预减 Top30，按降幅绝对值）")
        lines.append("")
        lines.append("| 代码 | 名称 | 行业 | 披露日 | 报告期 | 增幅区间% | 摘要 |")
        lines.append("|------|------|------|--------|--------|-----------|------|")
        for r in n:
            lines.append(f"| {r['ts_code']} | {_cell(r['name'])} | {_cell(r['industry'])} | {r['ann_date']} "
                         f"| {r['end_date']} | {r['p_chg']} | {_cell(r['summary'])} |")
        lines.append("")
    # 全部披露明细（窗口自包含；清单未触发时本段保证「是谁」可见）
    all_rows = result["all"]
    covered = {f"{r['ts_code']}|{r['ann_date']}|{r['type']}|{r['p_chg']}" for r in g[:40] + t[:30] + n}
    rest = [r for r in all_rows if f"{r['ts_code']}|{r['ann_date']}|{r['type']}|{r['p_chg']}" not in covered]
    lines.append("## 📋 窗口内披露明细（未入上方清单的其余条目）")
    lines.append("")
    lines.append("| 代码 | 名称 | 行业 | 披露日 | 报告期 | 类型 | 增幅区间% | 净利区间(万) | 摘要 |")
    lines.append("|------|------|------|--------|--------|------|-----------|-------------|------|")
    for r in rest[:50]:
        lines.append(f"| {r['ts_code']} | {_cell(r['name'])} | {_cell(r['industry'])} | {r['ann_date']} | "
                     f"{r['end_date']} | {r['type']} | {r['p_chg']} | {r['np_range']} | {_cell(r['summary'])} |")
    if not rest:
        lines.append("| — 全部条目已在上方清单中 — |")
    elif len(rest) > 50:
        lines.append(f"> 明细段表内 {50}/{len(rest)} 条（截断说明）")
    lines.append("")
    lines.append("> 声明：本报告为公开业绩预告的事实清单，数据源 tushare。不构成投资建议。")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=int, default=10, help="扫描窗口（自然日，默认 10）")
    ap.add_argument("--ann-date", type=str, default="", help="指定单日 YYYYMMDD（与 --days 互斥）")
    ap.add_argument("--min-gain", type=float, default=30.0, help="预增阈值 p_change_max>=PCT（默认 30）")
    ap.add_argument("--no-out", action="store_true", help="不落盘仅打印")
    ap.add_argument("--out-dir", type=str, default=str(ROOT / "reports/forecast-scan"))
    args = ap.parse_args()

    # 参数校验（R0 审查尾部项）：--days 0 会在 window[0] 处 IndexError；
    # 非法 --ann-date 会生成 2026-09-.md 垃圾文件名并误报淡季
    if args.ann_date:
        try:
            _dt.datetime.strptime(args.ann_date, "%Y%m%d")
        except ValueError:
            ap.error("--ann-date 须为合法 YYYYMMDD（如 20260715）")
    elif args.days < 1:
        ap.error("--days 须 ≥ 1")

    if args.ann_date:
        window = [args.ann_date]
    else:
        window = trading_days_in_window(args.days)

    client = TushareClient()
    if not client.is_available():
        print("Tushare 不可用（缺 token 或网络），无法扫描", file=sys.stderr)
        return 2

    print(f"拉取 {len(window)} 个日期…（{window[0]} ~ {window[-1]}）")
    basic = fetch_basic(client)
    records, failed_days = scan_window(client, window)

    # 空窗口鉴别：区分「真实淡季」与「取数故障」（R0 审查 F3/F4 修订）：
    # ① forecast 权限/配额被拒（40203/限额，client 内部记录）→ 明确故障，不落盘 exit 2
    #    （原实现用 stock_basic 探测——170 分接口可用而 forecast 被拒时会误报淡季）
    # ② 窗口全部日期取数失败 → 无法鉴别，不落盘 exit 2
    # ③ 其余空窗 → stock_basic 连通性兜底探测（同样失败 → exit 2）；通过 → 真实淡季 exit 3
    if not records:
        if "forecast" in getattr(client, "_permission_denied_apis", set()):
            print("forecast 接口权限/配额异常（40203/限额）——无法断言空窗，本次不落盘。",
                  file=sys.stderr)
            return 2
        if failed_days and len(failed_days) == len(window):
            print(f"窗口内全部 {len(window)} 个日期取数失败——无法鉴别真实空窗，本次不落盘。",
                  file=sys.stderr)
            return 2
        if not fetch_basic(client):
            print(
                "窗口内零披露且连通性探测失败（Tushare 配额/网络/权限异常）——"
                "无法区分真实空窗与数据源故障，本次不落盘。",
                file=sys.stderr,
            )
            return 2

    result = analyze(records, basic, args.min_gain)
    md = render_md(result, window, failed_days=failed_days)

    if args.no_out:
        print(md)
    else:
        out_dir = pathlib.Path(args.out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        # 文件名 = 窗口最早披露日-最晚披露日（单日则单日期），同日窗口不再出现 XX-XX 双同值
        days = sorted({str(r["ann_date"]) for r in records}) if records else sorted(window)
        fname = f"{days[0][:8]}-{days[-1][:8]}.md" if len(days) > 1 else f"{days[0][:8]}.md"
        path = out_dir / fname
        path.write_text(md, encoding="utf-8")
        print(md[:600])
        print(f"\n已落盘: {path}")
    # 空窗口（预告淡季，已鉴别）：返回 3 供调用方判断「无新披露」；
    # 部分日期取数失败（有数据但含缺口）：返回 1（报告已含数据缺口警示，区别于正常 0）
    if not records:
        return 3
    return 1 if failed_days else 0


if __name__ == "__main__":
    sys.exit(main())
