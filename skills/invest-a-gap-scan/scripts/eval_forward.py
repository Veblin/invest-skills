"""gap-scan 前瞻回测评估器（W2/M3）：hits.jsonl → 统计报告。

每组 (最早 scan_date, ts_code) → 拉前复权收盘（baostock adjustflag=2，T+1 滞后
标注）+ 沪深300 基准 → 超额统计：
  胜率（双侧符号检验 p）/ 均值超额（t，uncorrected——Newey-West 列 P1）
  / 中位超额 / 窗口分解（5/10/20/40 会话）/ 环境二分（margin 20d 分位）
硬红线：n<30 → 输出「样本不足，结论为方向性」声明（backtest-eval-plan §6/§8）。

用法：
    uv run python skills/invest-a-gap-scan/scripts/eval_forward.py \
        --hits reports/gap-backtest/hits.jsonl --out reports/gap-backtest/2026-09.md
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from collections import defaultdict
from pathlib import Path

_BENCH = "sh.000300"


def load_hits(path: Path) -> list[dict]:
    recs = []
    for ln in path.read_text(encoding="utf-8").splitlines():
        if ln.strip():
            try:
                recs.append(json.loads(ln))
            except json.JSONDecodeError:
                continue
    return recs


def earliest_per_code(recs: list[dict]) -> list[dict]:
    """每组取最早 scan_date（前瞻评估：信号发布时点即回测起点，零前瞻）。"""
    best: dict[str, dict] = {}
    for r in recs:
        key = r["ts_code"]
        if key not in best or r["scan_date"] < best[key]["scan_date"]:
            best[key] = r
    return list(best.values())


def _bs_closes(code: str, start_iso: str, end_iso: str) -> list[list[str]]:
    """baostock 前复权日线 [date, close]（会话由 main 单例 login/logout）。"""
    import baostock as bs

    rs = bs.query_history_k_data_plus(
        code, "date,close", start_date=start_iso, end_date=end_iso,
        frequency="d", adjustflag="2")
    rows = []
    while rs.error_code == "0" and rs.next():
        rows.append(rs.get_row_data())
    return rows


def _fmt_iso(yyyymmdd: str) -> str:
    return f"{yyyymmdd[:4]}-{yyyymmdd[4:6]}-{yyyymmdd[6:]}"


def _excess_for(rec: dict, as_of_iso: str) -> dict | None:
    """扫描日收盘进场 → as_of 收盘；返回收益/基准/超额/会话数。"""
    code6 = rec["ts_code"].split(".")[0]
    exch = "sh" if rec["ts_code"].endswith("SH") else "sz"
    start_iso = _fmt_iso(rec["scan_date"])
    try:
        rows = _bs_closes(f"{exch}.{code6}", start_iso, as_of_iso)
        bench = _bs_closes(_BENCH, start_iso, as_of_iso)
    except Exception:
        return None
    if len(rows) < 2 or len(bench) < 2:
        return None
    bmap = {r[0]: float(r[1]) for r in bench}
    s0, s1 = float(rows[0][1]), float(rows[-1][1])
    if rows[0][0] not in bmap or rows[-1][0] not in bmap:
        return None
    ret = (s1 / s0 - 1) * 100
    bench_ret = (bmap[rows[-1][0]] / bmap[rows[0][0]] - 1) * 100
    return {
        "code": rec["ts_code"], "name": rec["name"],
        "scan_date": rec["scan_date"],
        "sessions": len(rows),
        "ret": round(ret, 2), "bench": round(bench_ret, 2),
        "excess": round(ret - bench_ret, 2),
        "end_date": rows[-1][0],
    }


def stats_report(items: list[dict]) -> dict:
    """主统计量（§6）：胜率符号检验 / 均值 t（uncorrected）/ 中位。

    code-review #1：双侧符号检验委托共享 skills/lib/backtest.binomial_test
    （min-tail 选择 + cap 1.0）——旧实现 2*P(X>=k) 在跑输月份输出 p>1
    （k=0 时 2.0），恰好掩盖「策略失效」信号。
    """
    n = len(items)
    ex = [it["excess"] for it in items]
    k = sum(1 for e in ex if e > 0)
    if n:
        # 共享 skills/lib/backtest.binomial_test（code-review #1：min-tail +
        # cap 1.0 语义）。importlib 文件加载——`lib` 包名在测试进程可能已被
        # 其它 skill 的 lib 缓存（sys.path.insert 无法解除包缓存绑定）。
        import importlib.util

        _bt_path = Path(__file__).resolve().parents[2] / "lib" / "backtest.py"
        _spec = importlib.util.spec_from_file_location("_shared_bt", _bt_path)
        _bt = importlib.util.module_from_spec(_spec)
        _spec.loader.exec_module(_bt)  # type: ignore[union-attr]
        p_two = round(_bt.binomial_test(k, n)["p_value"], 3)
    else:
        p_two = None
    mean = sum(ex) / n if n else 0.0
    sd = (sum((e - mean) ** 2 for e in ex) / (n - 1)) ** 0.5 if n > 1 else 0.0
    return {
        "n": n, "beat": k,
        "p_two_sided": p_two,
        "mean_excess": round(mean, 2),
        "t": round(mean / (sd / n ** 0.5), 2) if n > 1 and sd > 0 else None,
        "median_excess": round(statistics.median(ex), 2) if n else None,
        "uncorrected_note": "t 未做截面相关校正（同日多命中共享市场冲击）——Newey-West/事件日聚类列 P1",
    }


def window_split(items: list[dict], by_ret: bool = False) -> list[dict]:
    """窗口分解（5/10/20/40 会话）——按每项 sessions 归类取对应窗口收益较复杂，
    简化为持有期分组（≤5 / ≤10 / ≤20 / ≤40 会话）的中位超额。"""
    groups = []
    for cap in (5, 10, 20, 40):
        g = [it for it in items if it["sessions"] <= cap]
        if g:
            groups.append({
                "cap_sessions": cap, "n": len(g),
                "median_excess": round(
                    statistics.median(x["excess"] for x in g), 2),
            })
    return groups


def env_regime(margin_20d_series: list[float], today_val: float) -> str:
    """margin 20d 变化率序列 → 自身分位二分（>50% 扩张 / <50% 去杠杆）。"""
    if not margin_20d_series:
        return "环境不可判定（margin 序列不足）"
    s = sorted(margin_20d_series)
    pct = __import__("bisect").bisect_left(s, today_val) / len(s)
    return "扩张" if pct >= 0.5 else "去杠杆"


def _disclaimer_block() -> str:
    """§9 偏差清单固定文本（每期报告必附）。"""
    return (
        "**偏差与局限声明**：① 幸存者偏差——池为当前指数成分股，退市/调出股"
        "缺失（事件表保留全部命中含后续退市股）；② 复权口径为现值前复权重放；"
        "③ 制度漂移（涨跌停幅度/涨停池可得性）未分段；④ t 值未做截面相关校正；"
        "⑤ 拉价通道 baostock T+1 滞后（截至前一交易日收盘）。仅供研究，不构成投资建议。"
    )


def render_report(items: list[dict], stat: dict, groups: list[dict],
                  regime: str, as_of: str) -> str:
    n_ok = stat["n"]
    disclaimer = "⚠️ **样本不足（n<30）：结论为方向性，不具统计显著性**。" \
        if n_ok < 30 else ""
    month = f"{as_of[:4]}-{as_of[4:6]}"  # 20260903 → 2026-09
    lines = [
        f"# gap-scan 前瞻回测月度报告（{month}）",
        "",
        f"> 数据截至 {as_of}（baostock T+1）| 样本 = 最早扫描日去重命中 | "
        f"进场 = 扫描日收盘 | 基准 = 沪深300 | {disclaimer}",
        "",
        "## 主统计量",
        "",
        f"| n | 跑赢基准 | 双侧符号检验 p | 均值超额 | t（uncorrected） | 中位超额 |",
        "|---|---|---|---|---|---|",
        f"| {stat['n']} | {stat['beat']}/{stat['n']} | {stat['p_two_sided']} | "
        f"{stat['mean_excess']}% | {stat['t']} | {stat['median_excess']}% |",
        "",
        f"> {stat['uncorrected_note']}",
        "",
        "## 持有期分组（中位超额）",
        "",
        "| ≤N 会话 | n | 中位超额 |",
        "|---|---|---|",
    ]
    for g in groups:
        lines.append(f"| {g['cap_sessions']} | {g['n']} | {g['median_excess']}% |")
    lines += [
        "",
        f"## 环境（margin 20d 变化率自身分位二分）：{regime}",
        "",
        "## 明细",
        "",
        "| 代码 | 名称 | 扫描日 | 持有会话 | 收益% | 基准% | 超额% |",
        "|---|---|---|---|---|---|---|",
    ]
    for it in sorted(items, key=lambda x: x["excess"], reverse=True):
        lines.append(
            f"| {it['code']} | {it['name']} | {it['scan_date']} | "
            f"{it['sessions']} | {it['ret']} | {it['bench']} | {it['excess']} |")
    lines += ["", _disclaimer_block(), ""]
    return "\n".join(lines)


def _margin_regime_label() -> str:
    """环境二分：margin 20d 变化率 vs 自身 1 年分位（akshare **沪市 SSE**）。

    code-review：源为 stock_margin_sse（单市场），须显式标注口径——否则
    「97% 分位 → 扩张」被读者当作全国两融状况（仓库两融惯例为沪深两市合计）。
    """
    try:
        import akshare as ak
        import bisect
        from datetime import datetime, timedelta

        end = datetime.now().strftime("%Y%m%d")
        start = (datetime.now() - timedelta(days=400)).strftime("%Y%m%d")
        df = ak.stock_margin_sse(start_date=start, end_date=end)
        df = df.sort_values("信用交易日期")
        mz = df["融资余额"].astype(float)
        if len(mz) < 21:
            return "环境不可判定（沪市 SSE margin 序列不足 21 行）"
        today_val = (mz.iloc[-1] / mz.iloc[-21] - 1) * 100
        series = [
            (mz.iloc[i] / mz.iloc[i - 20] - 1) * 100
            for i in range(20, len(mz))]
        s = sorted(series)
        pct = bisect.bisect_left(s, today_val) / len(s)
        return f"沪市(SSE)融资余额 20d 变化 {today_val:+.2f}%（1 年分位 " \
            f"{pct * 100:.0f}%）→ {'扩张' if pct >= 0.5 else '去杠杆'}"
    except Exception:
        return "环境不可判定（沪市 SSE margin 数据不可得）"


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--hits", default="reports/gap-backtest/hits.jsonl")
    # code-review：--out 缺省 = 按当前月真实路径（原模板字面量 'YYYY-MM.md'
    # 会生成同名垃圾文件并谎报成功）
    import datetime as _dt

    p.add_argument(
        "--out",
        default=f"reports/gap-backtest/{_dt.datetime.now():%Y-%m}.md",
        help="输出报告路径（缺省按当月）")
    p.add_argument("--as-of", default=None, help="评估截至日 YYYYMMDD（缺省今天）")
    args = p.parse_args()

    recs = load_hits(Path(args.hits))
    uniq = earliest_per_code(recs)
    as_of = args.as_of or __import__("datetime").datetime.now().strftime("%Y%m%d")
    as_of_iso = _fmt_iso(as_of)

    import baostock as bs

    bs.login()
    try:
        items = []
        for rec in uniq:
            try:
                r = _excess_for(rec, as_of_iso)
            except Exception:
                r = None
            if r:
                items.append(r)
    finally:
        bs.logout()

    # code-review #7（D5 fail-loud）+ 二轮 F：空月（hits 文件无记录，合法）
    # 与全失败（有记录但拉价全挂，异常）必须可区分——
    # 空月 → exit 0 不产报告不覆写；全失败 → exit 1。
    if not recs:
        print("eval_forward: hits.jsonl 无记录（空月），跳过——不生成报告",
              file=sys.stderr)
        return 0
    if not items:
        print("eval_forward: 有记录但全部标的拉价失败/不可解析——"
              "未生成报告（exit 1，勿覆写既有月报）", file=sys.stderr)
        return 1

    stat = stats_report(items)
    groups = window_split(items)
    regime = _margin_regime_label()
    report = render_report(items, stat, groups, regime, as_of)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report, encoding="utf-8")
    print(f"eval_forward: {len(recs)} 记录 → {len(uniq)} 唯一命中 → "
          f"{stat['n']} 可评估 | 报告: {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
