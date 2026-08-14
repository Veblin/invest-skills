#!/usr/bin/env python3
"""H4 回测 — 黄金股金价 beta（ABCD §3.2 H4 行，预注册见
skills/lib/references/backtest_prereg/H4_预注册.md）。

用法:
  uv run python scripts/backtest_h4.py                # 全量 9 只，输出 docs/data/H4_backtest_result.json
  uv run python scripts/backtest_h4.py --out /tmp/h4.json

数据: 个股 Tushare daily+adj_factor 前复权 5 年；市场 000300.SH index_daily；
金价 COMEX GC（T-1 对齐）主因子 + 沪金 AU0 稳健性对照。
输出: JSON（数字全部 Python 计算；报告须引用本 JSON 字段）。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
for _p in (
    str(_ROOT / "skills" / "lib"),
    str(_ROOT / "skills"),
    str(_ROOT / "skills" / "invest-a-stock" / "scripts"),
):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from backtest import hac_t_stats, ols_multi, significance_grade  # noqa: E402

SYMBOLS = [
    "600547.SH", "600489.SH", "601899.SH", "600988.SH", "002155.SZ",
    "000975.SZ", "601069.SH", "002237.SZ", "300139.SZ",
]
START_DATE = "20210813"


def fetch_stock(symbol: str) -> list[dict]:
    """Tushare daily + adj_factor 前复权 → [{date(YYYY-MM-DD), close}]。

    注意：_q_tushare_daily_qfq 接受裸 6 位代码（内部 _ts_code 转换），
    带 .SH/.SZ 后缀会抛 Invalid symbol。
    """
    from lib.collector._sources import _q_tushare_daily_qfq

    rows = _q_tushare_daily_qfq(symbol.split(".")[0], start_date=START_DATE)
    if not rows:
        raise RuntimeError(f"{symbol} 无 K 线数据")
    out = []
    for r in rows:
        d = str(r.get("trade_date", ""))
        if len(d) == 8:
            d = f"{d[:4]}-{d[4:6]}-{d[6:8]}"
        close = float(r.get("close"))
        if d and close > 0:
            out.append({"date": d, "close": close})
    return out


def fetch_index_300() -> dict[str, float]:
    """000300.SH 收盘序列（Tushare index_daily）→ {date: close}。"""
    from lib.tushare_client import TushareClient
    from lib import env

    cfg = env.get_config()
    client = TushareClient(token=cfg["TUSHARE_TOKEN"])
    df = client.query("index_daily", ts_code="000300.SH", start_date=START_DATE, end_date="")
    out: dict[str, float] = {}
    for r in df.to_dict("records"):
        d = str(r.get("trade_date", ""))
        out[f"{d[:4]}-{d[4:6]}-{d[6:8]}"] = float(r.get("close"))
    return out


def fetch_gold() -> tuple[dict[str, float], dict[str, float]]:
    """(COMEX GC 收盘序列, 沪金 AU0 序列) → {date: close}。

    COMEX T-1 对齐在回归层处理（gold_ret 前移 1 日）。
    """
    import akshare as ak

    comex_df = ak.futures_foreign_hist(symbol="GC")
    comex: dict[str, float] = {}
    for _, r in comex_df.iterrows():
        d = str(r["date"])[:10]
        if d and r["close"]:
            comex[d] = float(r["close"])

    au0_df = ak.futures_main_sina(symbol="AU0")  # sina 源列名为中文
    au0: dict[str, float] = {}
    for _, r in au0_df.iterrows():
        d = str(r["日期"])[:10]
        if d and r["收盘价"]:
            au0[d] = float(r["收盘价"])
    return comex, au0


def ret_series(prices: dict[str, float], dates: list[str]) -> dict[str, float]:
    """{date: close} → {date: 日收益%}（首日无收益跳过）。"""
    ordered = [(d, prices[d]) for d in dates if d in prices]
    out: dict[str, float] = {}
    for i in range(1, len(ordered)):
        prev = ordered[i - 1][1]
        if prev > 0:
            out[ordered[i][0]] = (ordered[i][1] / prev - 1) * 100.0
    return out


def align(
    stock_rows: list[dict],
    mkt_close: dict[str, float],
    gold_close: dict[str, float],
    gold_t_minus_1: bool,
) -> dict:
    """按个股交易日对齐三序列 → {mkt, gold, stock} 收益列表。"""
    dates = [r["date"] for r in stock_rows]
    stock_prices = {r["date"]: r["close"] for r in stock_rows}
    mkt_ret = ret_series(mkt_close, dates)
    gold_ret = ret_series(gold_close, dates)

    aligned = {"mkt": [], "gold": [], "stock": [], "n": 0}
    for i in range(1, len(stock_rows)):
        d = dates[i]
        prev = stock_rows[i - 1]["close"]
        if prev <= 0:
            continue
        stock_r = (stock_rows[i]["close"] / prev - 1) * 100.0
        mkt_r = mkt_ret.get(d)
        # T-1：t 日个股收益配 t-1 日金价收益（gold_ret 键为收益归属日）
        gd = dates[i - 1] if gold_t_minus_1 else d
        gold_r = gold_ret.get(gd)
        if mkt_r is None or gold_r is None:
            continue
        aligned["mkt"].append(mkt_r)
        aligned["gold"].append(gold_r)
        aligned["stock"].append(stock_r)
    aligned["n"] = len(aligned["stock"])
    return aligned


def _run_regression(a: dict, labels: tuple[str, str]) -> dict | None:
    if a["n"] < 30:
        return None
    r = hac_t_stats(a["stock"], [a["mkt"], a["gold"]], names=list(labels))
    r1 = ols_multi(a["stock"], [a["mkt"]])
    delta_r2 = r["r_squared"] - r1["r_squared"]
    return {
        "beta_gold": r["coefs"][1],
        "t_gold": r["hac_t_stats"][1],
        "beta_mkt": r["coefs"][0],
        "t_mkt": r["hac_t_stats"][0],
        "delta_r2": delta_r2,
        "n": a["n"],
    }


def monthly_regression(stock_rows: list[dict], mkt_close: dict[str, float],
                        gold_close: dict[str, float]) -> dict | None:
    """月度口径回归（Tufano 1998 / Baur 2014 文献同口径；日频 beta 会因
    COMEX 时区噪音衰减）。月收益 = 月内首尾收盘比；金价同月。"""
    by_month: dict[str, dict] = {}
    for r in stock_rows:
        m = r["date"][:7]
        slot = by_month.setdefault(m, {"first": None, "last": None, "first_date": None, "last_date": None})
        if slot["first"] is None:
            slot["first"] = r["close"]
            slot["first_date"] = r["date"]
        slot["last"] = r["close"]
        slot["last_date"] = r["date"]

    def _mret(prices: dict[str, float], m: str) -> float | None:
        ds = sorted(d for d in prices if d[:7] == m)
        if len(ds) < 2:
            return None
        return (prices[ds[-1]] / prices[ds[0]] - 1) * 100.0 if prices[ds[0]] > 0 else None

    months = sorted(by_month)
    sr, mr, gr = [], [], []
    for m in months:
        slot = by_month[m]
        s_r = (slot["last"] / slot["first"] - 1) * 100.0 if slot["first"] > 0 else None
        m_r = _mret(mkt_close, m)
        g_r = _mret(gold_close, m)
        if s_r is None or m_r is None or g_r is None:
            continue
        sr.append(s_r); mr.append(m_r); gr.append(g_r)
    if len(sr) < 30:
        return None
    r = hac_t_stats(sr, [mr, gr], names=["mkt", "gold"])
    r1 = ols_multi(sr, [mr])
    return {
        "beta_gold": r["coefs"][1],
        "t_gold": r["hac_t_stats"][1],
        "delta_r2": r["r_squared"] - r1["r_squared"],
        "n_months": len(sr),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="H4 黄金股金价 beta 回测")
    parser.add_argument("--out", default=str(_ROOT / "docs" / "data" / "H4_backtest_result.json"))
    args = parser.parse_args()

    mkt_close = fetch_index_300()
    comex_close, au0_close = fetch_gold()

    results = {"meta": {"start": START_DATE, "gold_source": "COMEX GC (T-1)", "market": "000300.SH"}}
    pool = []
    per_stock: dict[str, dict] = {}
    for sym in SYMBOLS:
        stock_rows = fetch_stock(sym)
        a = align(stock_rows, mkt_close, comex_close, gold_t_minus_1=True)
        r = _run_regression(a, ("mkt", "gold"))
        if r is None:
            per_stock[sym] = {"error": f"有效样本仅 {a['n']}"}
            continue
        # Baur 非对称：金价月度方向分桶
        monthly_up: dict[str, bool] = {}
        for d, c in sorted(comex_close.items()):
            monthly_up[d[:7]] = monthly_up.get(d[:7], c)
        up_days, down_days = [], []
        dates = [x["date"] for x in stock_rows]
        for i in range(1, len(stock_rows)):
            d = dates[i]
            prev = stock_rows[i - 1]["close"]
            if prev <= 0:
                continue
            sr = (stock_rows[i]["close"] / prev - 1) * 100.0
            mkt_r = ret_series(mkt_close, dates).get(d)
            gd = dates[i - 1]
            gold_r = ret_series(comex_close, dates).get(gd)
            if mkt_r is None or gold_r is None:
                continue
            month = gd[:7]
            is_up = True  # 月初锚：本月首日金价 ≥ 上月首日金价（月度方向）
            if month in monthly_up and len(monthly_up) >= 2:
                keys = sorted(monthly_up)
                idx = keys.index(month)
                if idx > 0:
                    is_up = monthly_up[month] >= monthly_up[keys[idx - 1]]
            (up_days if is_up else down_days).append((sr, mkt_r, gold_r))
        up_a = {"stock": [x[0] for x in up_days], "mkt": [x[1] for x in up_days],
                "gold": [x[2] for x in up_days], "n": len(up_days)}
        down_a = {"stock": [x[0] for x in down_days], "mkt": [x[1] for x in down_days],
                  "gold": [x[2] for x in down_days], "n": len(down_days)}
        r_up = _run_regression(up_a, ("mkt", "gold"))
        r_down = _run_regression(down_a, ("mkt", "gold"))
        r_monthly = monthly_regression(stock_rows, mkt_close, comex_close)
        # 沪金 AU0 稳健性对照（2024-01 起；人民币金价，无货币错配）
        au0_rows = [x for x in stock_rows if x["date"] >= "2024-01-01"]
        a_au0 = align(au0_rows, mkt_close, au0_close, gold_t_minus_1=False) if len(au0_rows) > 30 else None
        r_au0 = _run_regression(a_au0, ("mkt", "gold_au0")) if a_au0 else None
        r_au0_monthly = monthly_regression(au0_rows, mkt_close, au0_close)
        entry = {
            "full": r,
            "monthly": r_monthly,
            "au0_daily_2024": r_au0,
            "au0_monthly_2024": r_au0_monthly,
            "gold_up_months": r_up,
            "gold_down_months": r_down,
        }
        per_stock[sym] = entry
        pool.append(r)
        m_tag = f" Mβ={r_monthly['beta_gold']:.2f}(t{r_monthly['t_gold']:.1f})" if r_monthly else ""
        au_tag = f" AU0β={r_au0_monthly['beta_gold']:.2f}(t{r_au0_monthly['t_gold']:.1f})" if r_au0_monthly else ""
        print(f"{sym}: beta_gold={r['beta_gold']:.3f} t={r['t_gold']:.2f} "
              f"ΔR²={r['delta_r2']*100:.2f}pp n={r['n']}{m_tag}{au_tag}")

    betas = [p["beta_gold"] for p in pool]
    ts = [p["t_gold"] for p in pool]
    results["pool_summary"] = {
        "n_stocks": len(pool),
        "beta_gold_mean": sum(betas) / len(betas) if betas else None,
        "beta_gold_median": sorted(betas)[len(betas) // 2] if betas else None,
        "beta_positive_count": sum(1 for b in betas if b > 0),
        "significant_t3_count": sum(1 for t in ts if abs(t) >= 3.0),
        "significant_t2_count": sum(1 for t in ts if abs(t) >= 2.0),
        "grade": None,
    }
    monthly_betas = [e["monthly"]["beta_gold"] for e in per_stock.values()
                    if isinstance(e.get("monthly"), dict)]
    monthly_ts = [e["monthly"]["t_gold"] for e in per_stock.values()
                  if isinstance(e.get("monthly"), dict)]
    results["pool_summary"]["monthly_beta_gold_mean"] = (
        sum(monthly_betas) / len(monthly_betas) if monthly_betas else None
    )
    results["pool_summary"]["monthly_positive_count"] = sum(1 for b in monthly_betas if b > 0)
    results["pool_summary"]["monthly_significant_t2_count"] = sum(1 for t in monthly_ts if abs(t) >= 2.0)
    au0_betas = [e["au0_monthly_2024"]["beta_gold"] for e in per_stock.values()
                 if isinstance(e.get("au0_monthly_2024"), dict)]
    results["pool_summary"]["au0_monthly_beta_gold_mean"] = (
        sum(au0_betas) / len(au0_betas) if au0_betas else None
    )
    results["pool_summary"]["au0_monthly_positive_count"] = sum(1 for b in au0_betas if b > 0)
    # 池级分级：显著比例 ≥ 一半且 β 全部为正 → ✅；部分 → ⚠️；否则 ❌
    n = len(pool)
    if n:
        sig = results["pool_summary"]["significant_t2_count"]
        results["pool_summary"]["grade"] = (
            "✅" if sig >= n / 2 and all(b > 0 for b in betas)
            else ("⚠️" if sig > 0 and sum(1 for b in betas if b > 0) >= n / 2 else "❌")
        )
    results["per_stock"] = per_stock

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2, default=str)
    print(f"✅ 已写入 {out_path}")
    print("pool:", json.dumps(results["pool_summary"], ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
