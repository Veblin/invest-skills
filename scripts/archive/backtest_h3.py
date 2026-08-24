#!/usr/bin/env python3
"""H3 回测 — 半导体材料 vs 设备 RS 动量（ABCD §3.2 H3 行，预注册见
skills/lib/references/backtest_prereg/H3_预注册.md）。

用法:
  uv run python scripts/archive/backtest_h3.py                # 输出 docs/data/H3_backtest_result.json

数据: akshare index_hist_sw（swsindex 直连）——850813.SI 材料 / 850818.SI 设备 /
801081.SI 半导体二级背景。检验: spread_{t+1} ~ RS_t(20/60/120) NW t +
RS>0 次日占优天数二项检验。输出: JSON。
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
):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from backtest import binomial_test, hac_t_stats, rs_momentum, significance_grade, spread_series  # noqa: E402

INDICES = {
    "materials": "850813",  # 申万三级 半导体材料
    "equipment": "850818",  # 申万三级 半导体设备
    "semiconductor_l2": "801081",  # 申万二级 半导体（背景）
}
LOOKBACKS = (20, 60, 120)


def fetch_sw(symbol: str) -> list[dict]:
    """index_hist_sw → [{date(YYYY-MM-DD), close}]（升序）。"""
    import akshare as ak

    df = ak.index_hist_sw(symbol=symbol, period="day")  # swsindex 源列名为中文
    out = []
    for _, r in df.iterrows():
        d = str(r["日期"])[:10]
        c = float(r["收盘"])
        if d and c > 0:
            out.append({"date": d, "close": c})
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="H3 材料 vs 设备 RS 动量回测")
    parser.add_argument("--out", default=str(_ROOT / "docs" / "data" / "H3_backtest_result.json"))
    args = parser.parse_args()

    mat = fetch_sw(INDICES["materials"])
    eqp = fetch_sw(INDICES["equipment"])
    semi = fetch_sw(INDICES["semiconductor_l2"])

    # 按材料指数交易日对齐（两指数交易日几乎一致）
    mat_dates = {r["date"]: r["close"] for r in mat}
    eqp_dates = {r["date"]: r["close"] for r in eqp}
    semi_dates = {r["date"]: r["close"] for r in semi}
    dates = [r["date"] for r in mat if r["date"] in eqp_dates]
    mat_closes = [mat_dates[d] for d in dates]
    eqp_closes = [eqp_dates[d] for d in dates]

    spread = spread_series(mat_closes, eqp_closes)  # 对数收益差（材料 − 设备）

    results = {
        "meta": {
            "materials": INDICES["materials"],
            "equipment": INDICES["equipment"],
            "n_days": len(dates),
            "date_range": [dates[0], dates[-1]],
        },
        "lookbacks": {},
    }
    for L in LOOKBACKS:
        rs = rs_momentum(spread, L)
        # 预测回归：spread_{t+1} = a + b·RS_t
        y, x = [], []
        for t in range(len(spread) - 1):
            if spread[t + 1] is not None and rs[t] is not None:
                y.append(spread[t + 1])
                x.append(rs[t])
        reg = hac_t_stats(y, [x], names=[f"RS_{L}"]) if len(y) >= 30 else None
        # 占优天数：RS>0 次日材料占优
        k = sum(1 for t in range(len(spread) - 1)
                if rs[t] is not None and rs[t] > 0 and spread[t + 1] is not None and spread[t + 1] > 0)
        n = sum(1 for t in range(len(spread) - 1)
                if rs[t] is not None and rs[t] > 0 and spread[t + 1] is not None)
        binom = binomial_test(k, n) if n >= 1 else None
        entry = {
            "n_obs": len(y),
            "b": reg["coefs"][0] if reg else None,
            "t_nw": reg["hac_t_stats"][1] if reg else None,
            "r_squared": reg["r_squared"] if reg else None,
            "dominance": {"k": k, "n": n, **({k2: v for k2, v in binom.items() if k2 in ("p_value", "proportion")} if binom else {})},
            "grade": significance_grade(abs(reg["hac_t_stats"][1])) if reg else None,
        }
        results["lookbacks"][f"L{L}"] = entry
        print(f"L{L}: b={entry['b']:.5f} t={entry['t_nw']:.2f} "
              f"占优 {entry['dominance']['k']}/{entry['dominance']['n']} p={entry['dominance'].get('p_value'):.4f} {entry['grade']}")

    # 背景：半导体二级 2020 后子样本稳健性（仅报告日期范围，不做第三回归）
    results["meta"]["semiconductor_l2_dates"] = [semi[0]["date"], semi[-1]["date"]] if semi else None

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2, default=str)
    print(f"✅ 已写入 {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
