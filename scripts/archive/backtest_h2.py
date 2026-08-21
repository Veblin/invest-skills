#!/usr/bin/env python3
"""H2 回测 — 大跌 ≥10% 低吸（ABCD §3.2 H2 行，预注册见
skills/lib/references/backtest_prereg/H2_预注册.md）。

用法:
  uv run python scripts/archive/backtest_h2.py                # 输出 docs/data/H2_backtest_result.json

数据: market_daily（Tushare pro.daily 全市场，2021-01 起回填）；
事件 pct_chg ≤ -10%，分层（封死/开板/未触及）× 成交假设双口径；
+1/+3/+5 市场调整超额（全 A 等权）+ NW t + bootstrap CI + calendar-time；
板块对照 THS 商业航天指数大跌日。输出: JSON。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]  # C9a 归档后多一层 archive/
for _p in (
    str(_ROOT / "skills" / "lib"),
    str(_ROOT / "skills"),
    str(_ROOT / "skills" / "invest-a-stock" / "scripts"),
):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from backtest import (  # noqa: E402
    calendar_time_portfolio,
    describe,
    hac_t_stats,
    market_adjusted,
    significance_grade,
)
from multiple_testing import bootstrap_ci  # noqa: E402

START = "2021-01-01"
HORIZONS = (1, 3, 5)
LIMIT_PCT = {"0": 0.10, "3": 0.20, "6": 0.10}  # 0/6 主板 10%；3 创业板 20%（688 前缀为 6 → 20% 例外）


def limit_pct_for(ts_code: str) -> float:
    """跌停幅度：688 科创板 20%、300/301 创业板 20%、其余 10%。"""
    code = ts_code.split(".")[0]
    if code.startswith(("688", "300", "301")):
        return 0.20
    return 0.10


def layer_of(row: dict, limit_pct: float) -> str | None:
    """三态分层：封死 / 开板 / 未触及。pre_close 缺失返回 None（剔除）。"""
    pre = row.get("pre_close")
    close = row.get("close")
    low = row.get("low")
    if pre is None or close is None or low is None or pre <= 0:
        return None
    limit_price = round(pre * (1 - limit_pct), 2)
    if close <= limit_price:
        return "sealed"
    if low <= limit_price < close:
        return "opened"
    return "untouched"


def main() -> int:
    parser = argparse.ArgumentParser(description="H2 大跌低吸回测")
    parser.add_argument("--out", default=str(_ROOT / "docs" / "data" / "H2_backtest_result.json"))
    args = parser.parse_args()

    from lib import store

    dates = sorted(d for d in store.market_daily_dates() if d >= START)
    if len(dates) < 30:
        print(f"market_daily 数据不足（{len(dates)} 日），请先跑 backfill_market_daily.py --start {START}")
        return 1

    # 1) 逐日流式：全 A 等权日收益 + 事件表
    mkt_ret: dict[str, float] = {}
    events: list[dict] = []
    prev_ew: dict[str, float] = {}
    for d in dates:
        rows = store.load_market_daily(dates=[d])
        ew = {}
        sum_pct = 0.0
        n_pct = 0
        for r in rows:
            code = r["ts_code"]
            pct = r.get("pct_chg")
            if pct is not None:
                sum_pct += float(pct)
                n_pct += 1
            if r.get("close") is not None:
                ew[code] = float(r["close"])
            if pct is not None and float(pct) <= -10.0:
                layer = layer_of(r, limit_pct_for(code))
                if layer:
                    events.append({
                        "date": d, "ts_code": code, "layer": layer,
                        "pct_chg": float(pct), "close": r.get("close"),
                    })
        if n_pct:
            mkt_ret[d] = sum_pct / n_pct
        prev_ew = ew

    # 2) 事件前向收益（个股未来收盘经 SQL 取，ts_code 索引）
    import sqlite3
    from lib.store import _get_path

    conn = sqlite3.connect(str(_get_path()))
    conn.row_factory = sqlite3.Row

    def forward_close(ts_code: str, date: str, n: int) -> float | None:
        row = conn.execute(
            "SELECT close FROM market_daily WHERE ts_code=? AND date>? "
            "ORDER BY date ASC LIMIT 1 OFFSET ?",
            (ts_code, date, n - 1),
        ).fetchone()
        return float(row["close"]) if row and row["close"] is not None else None

    results = {
        "meta": {"start": START, "end": dates[-1], "n_days": len(dates),
                 "n_events_total": len(events), "event_pct_threshold": -10.0},
        "layers": {},
        "sector_compare": None,
    }
    # 3) 分层 × 窗口统计
    for layer in ("sealed", "opened", "untouched", "tradable"):
        layer_events = [e for e in events if (layer == "tradable" and e["layer"] != "sealed") or e["layer"] == layer]
        results["layers"][layer] = {"n_events": len(layer_events), "horizons": {}}
        for h in HORIZONS:
            adj: list[float] = []
            by_date: dict[str, list[float]] = {}
            for e in layer_events:
                fc = forward_close(e["ts_code"], e["date"], h)
                if fc is None or not e["close"]:
                    continue
                ev_r = (fc / e["close"] - 1) * 100.0
                mkt_cum = 1.0
                mkt_ok = True
                # 市场同期：从事件日后第 1 到第 h 个交易日
                idx = dates.index(e["date"])
                for k in range(1, h + 1):
                    if idx + k >= len(dates):
                        mkt_ok = False
                        break
                    dk = dates[idx + k]
                    if dk not in mkt_ret:
                        mkt_ok = False
                        break
                    mkt_cum *= (1 + mkt_ret[dk] / 100.0)
                if not mkt_ok:
                    continue
                a = ev_r - (mkt_cum - 1) * 100.0
                adj.append(a)
                by_date.setdefault(e["date"], []).append(a)
            if len(adj) < 10:
                continue
            reg = hac_t_stats(adj, [], names=[])  # 仅截距：超额均值 + NW t
            ci = bootstrap_ci(adj, n_boot=5000, seed=42)
            desc = describe(adj)
            ct = calendar_time_portfolio(by_date)
            ct_reg = hac_t_stats(ct, [], names=[]) if len(ct) >= 5 else None
            results["layers"][layer]["horizons"][f"+{h}"] = {
                "n": desc["n"],
                "mean_pct": desc["mean_daily_pct"],
                "median_pct": desc["median_daily_pct"],
                "win_rate": desc["up_prob"],
                "t_nw": reg["hac_t_stats"][0],
                "grade": significance_grade(abs(reg["hac_t_stats"][0])),
                "ci_95": [ci["lower"], ci["upper"]],
                "calendar_time": (
                    {
                        "n_days": len(ct),
                        "mean_pct": ct_reg["intercept"],
                        "t_nw": ct_reg["hac_t_stats"][0],
                    }
                    if ct_reg is not None else None
                ),
            }

    # 4) 板块对照：THS 商业航天指数大跌日
    try:
        import akshare as ak

        df = ak.stock_board_concept_index_ths(symbol="商业航天")  # THS 源列名为中文
        idx_dates = [str(r["日期"])[:10] for _, r in df.iterrows()]
        closes = [float(r["收盘价"]) for _, r in df.iterrows()]
        drops = []
        for i in range(1, len(closes)):
            if closes[i - 1] > 0:
                pct = (closes[i] / closes[i - 1] - 1) * 100.0
                if pct <= -10.0:
                    fwd = {}
                    for h in HORIZONS:
                        if i + h < len(closes):
                            fwd[f"+{h}"] = (closes[i + h] / closes[i] - 1) * 100.0
                    drops.append({"date": idx_dates[i], "pct_chg": pct, **fwd})
        results["sector_compare"] = {
            "source": "THS 商业航天板块指数 309130",
            "n_drop_days": len(drops),
            "drops": drops,
        }
    except Exception as exc:  # noqa: BLE001 — 对照失败不阻塞主结果
        results["sector_compare"] = {"error": str(exc)}

    conn.close()
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2, default=str)
    print(f"✅ 已写入 {out_path}")
    for layer, v in results["layers"].items():
        line = f"{layer}: n={v['n_events']}"
        for h, hv in v["horizons"].items():
            line += f" | {h}: {hv['mean_pct']:+.2f}% t={hv['t_nw']:+.2f} {hv['grade']}"
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
