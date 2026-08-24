#!/usr/bin/env python3
"""H6 回测 — 做T 支撑位反弹（ABCD §3.2 H6 行，预注册见
skills/lib/references/backtest_prereg/H6_预注册.md）。

用法:
  uv run python scripts/archive/backtest_h6.py                # 输出 docs/data/H6_backtest_result.json

样本: market_daily 全市场随机抽样 800 只（seed=42）+ 沪深300 全成分（重点池代理）。
事件: ① MA20 日内穿越 ② BOLL 下轨触及 ③ 近 60 日向上缺口首次回探；
分层: ADX14<20 震荡市 / ≥25 趋势市 / 中间带不分组。
输出: JSON（数字全部 Python 计算）。
"""

from __future__ import annotations

import argparse
import json
import random
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

import pandas as pd

_ROOT = Path(__file__).resolve().parents[2]  # C9a 归档后多一层 archive/
for _p in (
    str(_ROOT / "skills" / "lib"),
    str(_ROOT / "skills"),
    str(_ROOT / "skills" / "invest-a-stock" / "scripts"),
    str(_ROOT / "skills" / "invest-a-gap-scan" / "scripts" / "lib"),
):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from backtest import (  # noqa: E402
    binomial_test,
    calendar_time_portfolio,
    describe,
    hac_t_stats,
    significance_grade,
)

N_SAMPLE = 800
SAMPLE_SEED = 42
GAP_LOOKBACK = 60
ADX_BANDS = {"ranging": (None, 20.0), "trending": (25.0, None)}  # 中间带不分组
SUPPORT_TYPES = ("ma20", "boll_lower", "gap")


def load_symbol_series(conn: sqlite3.Connection, ts_code: str) -> pd.DataFrame | None:
    """单标的日线序列（date ASC）。"""
    df = pd.read_sql_query(
        "SELECT date, open, high, low, close, pct_chg FROM market_daily "
        "WHERE ts_code=? ORDER BY date ASC",
        conn,
        params=(ts_code,),
    )
    if df.empty or len(df) < 80:
        return None
    return df


def compute_indicators(df: pd.DataFrame):
    """MA20 / BOLL(20,2σ) / ADX14 + 缺口带 → 附列。"""
    from technical import adx  # noqa: E402 — skills/lib 路径

    df = df.copy()
    df["ma20"] = df["close"].rolling(20).mean()
    df["std20"] = df["close"].rolling(20).std()
    df["boll_lower"] = df["ma20"] - 2 * df["std20"]
    adx_vals = adx(
        df["high"].astype(float).tolist(),
        df["low"].astype(float).tolist(),
        df["close"].astype(float).tolist(),
    )
    df["adx14"] = adx_vals
    return df


def detect_events(df: pd.DataFrame) -> list[dict]:
    """三类支撑位事件（预注册口径）。返回事件行索引列表。"""
    events: list[dict] = []
    n = len(df)
    lows = df["low"].astype(float).tolist()
    highs = df["high"].astype(float).tolist()
    closes = df["close"].astype(float).tolist()
    for i in range(GAP_LOOKBACK, n - 1):  # 最后一日无 forward，天然排除
        pct = df["pct_chg"].iloc[i]
        if pct is None or float(pct) <= -9.5:
            continue  # 跌停级无做T 语义
        adx = df["adx14"].iloc[i]
        if adx is None:
            continue
        regime = (
            "ranging" if float(adx) < 20.0
            else ("trending" if float(adx) >= 25.0 else None)
        )
        if regime is None:
            continue  # 中间带不分组
        ma20 = df["ma20"].iloc[i]
        boll_l = df["boll_lower"].iloc[i]
        if ma20 is not None and not pd.isna(ma20) and lows[i] <= ma20 <= highs[i]:
            events.append({"idx": i, "type": "ma20", "regime": regime})
        if boll_l is not None and not pd.isna(boll_l) and lows[i] <= boll_l:
            events.append({"idx": i, "type": "boll_lower", "regime": regime})
        # 缺口支撑：近 60 日向上缺口首次回探（倒序扫描，认最近的未回探缺口；
        # 已回探的缺口跳过继续向更老缺口找——break 只在事件实际生成时）
        for g in range(i - 2, max(1, i - GAP_LOOKBACK) - 1, -1):
            if lows[g] > highs[g - 1]:
                g_lo = highs[g - 1]
                if lows[i] <= g_lo:
                    # 首次回探：g 之后到 i-1 之间未再触及
                    prior_touch = any(lows[j] <= g_lo for j in range(g + 1, i))
                    if not prior_touch:
                        events.append({"idx": i, "type": "gap", "regime": regime})
                        break  # 只认最近的未回探缺口
    return events


def main() -> int:
    parser = argparse.ArgumentParser(description="H6 做T 支撑位反弹回测")
    parser.add_argument("--out", default=str(_ROOT / "docs" / "data" / "H6_backtest_result.json"))
    parser.add_argument("--n-sample", type=int, default=N_SAMPLE)
    args = parser.parse_args()

    from lib import store  # noqa: E402

    # 1) 样本：全市场随机抽样 + 沪深300 全成分
    all_dates = sorted(store.market_daily_dates())
    conn = sqlite3.connect(str(store._get_path()))
    all_codes = [
        r[0] for r in conn.execute(
            "SELECT DISTINCT ts_code FROM market_daily WHERE date=?",
            (all_dates[-1],),
        ).fetchall()
    ]
    rng = random.Random(SAMPLE_SEED)
    sample_codes = rng.sample(all_codes, min(args.n_sample, len(all_codes)))
    try:
        from invest_path import load_gap_scan_module  # noqa: E402

        universe_mod = load_gap_scan_module("universe")
        hs300 = [s.ts_code for s in universe_mod.build_universe(indices=["csi300"])]
    except Exception:  # noqa: BLE001 — 网络/环境失败时仅用抽样池
        hs300 = []
    codes = sorted(set(sample_codes) | set(hs300))

    # 2) 全 A 等权日收益（市场基准）
    mkt_ret: dict[str, float] = {}
    for d in all_dates:
        r = conn.execute(
            "SELECT AVG(pct_chg) FROM market_daily WHERE date=?", (d,)
        ).fetchone()
        if r and r[0] is not None:
            mkt_ret[d] = float(r[0])
    date_idx = {d: i for i, d in enumerate(all_dates)}

    # 3) 逐只检测 + forward
    adj_events: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    by_date_events: dict[tuple[str, str], dict[str, list[float]]] = defaultdict(
        lambda: defaultdict(list))
    baseline: dict[str, list[float]] = defaultdict(list)  # 无条件基线（指标有效日全样本）
    n_stocks = 0
    for code in codes:
        df = load_symbol_series(conn, code)
        if df is None:
            continue
        n_stocks += 1
        df = compute_indicators(df)
        events = detect_events(df)
        closes = df["close"].astype(float).tolist()
        dates = df["date"].tolist()
        for ev in events:
            i = ev["idx"]
            layer = f"{ev['type']}|{ev['regime']}"
            for h in (1, 2, 3):
                if i + h >= len(closes) or closes[i] <= 0:
                    continue
                ev_r = (closes[i + h] / closes[i] - 1) * 100.0
                d = dates[i]
                di = date_idx.get(d)
                if di is None:
                    continue
                mkt_cum = 1.0
                ok = True
                for k in range(1, h + 1):
                    if di + k >= len(all_dates) or all_dates[di + k] not in mkt_ret:
                        ok = False
                        break
                    mkt_cum *= (1 + mkt_ret[all_dates[di + k]] / 100.0)
                if not ok:
                    continue
                adj = ev_r - (mkt_cum - 1) * 100.0
                adj_events[layer][f"+{h}"].append(adj)
                by_date_events[(layer, f"+{h}")][d].append(adj)
        # 无条件基线：指标有效日的 forward（抽样 1/20 控制内存）
        for i in range(GAP_LOOKBACK, len(closes) - 1):
            if i % 20:
                continue
            d = dates[i]
            di = date_idx.get(d)
            if di is None or closes[i] <= 0:
                continue
            for h in (1, 2, 3):
                if i + h >= len(closes):
                    continue
                ev_r = (closes[i + h] / closes[i] - 1) * 100.0
                mkt_cum = 1.0
                ok = True
                for k in range(1, h + 1):
                    if di + k >= len(all_dates) or all_dates[di + k] not in mkt_ret:
                        ok = False
                        break
                    mkt_cum *= (1 + mkt_ret[all_dates[di + k]] / 100.0)
                if not ok:
                    continue
                baseline[f"+{h}"].append(ev_r - (mkt_cum - 1) * 100.0)

    # 4) 统计
    results = {
        "meta": {
            "n_universe_codes": len(codes), "n_stocks_loaded": n_stocks,
            "sample_seed": SAMPLE_SEED, "adx_bands": ADX_BANDS,
            "n_events_by_layer": {k: sum(len(v) for v in hh.values())
                                   for k, hh in adj_events.items()},
        },
        "layers": {},
        "baseline": {},
    }
    for layer, horizons in sorted(adj_events.items()):
        results["layers"][layer] = {}
        for h, vals in horizons.items():
            if len(vals) < 30:
                results["layers"][layer][h] = {"n": len(vals), "error": "样本 <30"}
                continue
            reg = hac_t_stats(vals, [], names=[])
            ci_desc = describe(vals)
            wins = sum(1 for v in vals if v > 0)
            binom = binomial_test(wins, len(vals))
            ct = calendar_time_portfolio(by_date_events[(layer, h)])
            results["layers"][layer][h] = {
                "n": ci_desc["n"],
                "mean_pct": ci_desc["mean_daily_pct"],
                "median_pct": ci_desc["median_daily_pct"],
                "win_rate": ci_desc["up_prob"],
                "t_nw": reg["hac_t_stats"][0],
                "grade": significance_grade(abs(reg["hac_t_stats"][0])),
                "binomial_p": binom["p_value"],
                "calendar_time_n_days": len(ct),
            }
    for h, vals in baseline.items():
        if len(vals) >= 30:
            d = describe(vals)
            results["baseline"][h] = {
                "n": d["n"], "mean_pct": d["mean_daily_pct"],
                "median_pct": d["median_daily_pct"], "win_rate": d["up_prob"],
            }

    conn.close()
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2, default=str)
    print(f"✅ 已写入 {out_path}")
    print("meta:", json.dumps(results["meta"], ensure_ascii=False))
    for layer, horizons in sorted(results["layers"].items()):
        line = f"{layer}:"
        for h, hv in horizons.items():
            if "mean_pct" in hv:
                line += f" {h}: {hv['mean_pct']:+.2f}% t={hv['t_nw']:+.2f} {hv['grade']}"
        print(line)
    print("baseline:", json.dumps(results["baseline"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
