#!/usr/bin/env python3
"""候选预案触发基线统计（v0.2.6 补漏 #3）——E-002~E-007，E-001 同口径。

用法:
  uv run python scripts/scenario_baselines.py      # 输出 docs/data/scenario_baselines_E002_E007.json

口径（与 tmp_4050_band.py 的 E-001 基线一致）：
- 数据：上证指数全历史日线（akshare stock_zh_index_daily，降级 baostock）
- 每条预案：触发条件（收盘口径写死）→ 历史触达日 → 后 1/3/5 日行为
  （均值/中位/胜率）+ 无条件基线对照（5 日持有基线）
- 注记：点位为当前结构位，历史触达为跨周期类比（E-001 同款样本限定）
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

_ROOT = Path(__file__).resolve().parent.parent
for _p in (str(_ROOT / "skills" / "lib"), str(_ROOT / "skills")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# 触发定义（从 scenario-plans.md 三节候选预案表写死；改动须同步文档）
TRIGGERS = {
    "E-002": {"label": "弱势情景切换", "kind": "close_below", "level": 3909.50},
    "E-003": {"label": "生死线失守", "kind": "close_below", "level": 3741.11},
    "E-004": {"label": "五浪目标跟踪", "kind": "close_below", "level": 3745.17,
              "track": [3540.61, 3414.16, 3324.13, 3209.60]},
    "E-005": {"label": "缺口带突破确认", "kind": "close_above_3d", "level": 4015.0},
    "E-006": {"label": "BOLL 上轨触达", "kind": "boll_position", "level": 95.0},
    "E-007": {"label": "反弹触及 50% 位", "kind": "close_near", "level": 3960.26, "tol_pct": 0.3},
}


def fetch_index() -> pd.DataFrame:
    """上证指数全历史日线 → DataFrame[date, open, high, low, close]。"""
    import akshare as ak

    df = ak.stock_zh_index_daily(symbol="sh000001")
    return df


def trigger_flags(df: pd.DataFrame, spec: dict) -> pd.Series:
    """触发日布尔序列（按 kind 分派）。"""
    closes = df["close"].astype(float)
    kind = spec["kind"]
    level = spec["level"]
    if kind == "close_below":
        return closes < level
    if kind == "close_above_3d":
        return (closes > level).rolling(3).sum() == 3  # 连续 3 日，事件日 = 第 3 日
    if kind == "close_near":
        return (closes - level).abs() / level * 100 <= spec["tol_pct"]
    if kind == "boll_position":
        mid = closes.rolling(20).mean()
        std = closes.rolling(20).std()
        upper = mid + 2 * std
        lower = mid - 2 * std
        pos = (closes - lower) / (upper - lower) * 100
        return pos >= level
    raise ValueError(f"未知触发类型: {kind}")


def main() -> int:
    parser = argparse.ArgumentParser(description="候选预案触发基线统计")
    parser.add_argument("--out", default=str(_ROOT / "docs" / "data" / "scenario_baselines_E002_E007.json"))
    args = parser.parse_args()

    df = fetch_index()
    closes = df["close"].astype(float).tolist()
    n = len(df)

    # 无条件基线（5 日持有，同 tmp_4050_band 口径）
    fwd5 = [(closes[i + 5] / closes[i] - 1) * 100 for i in range(n - 5)]
    base_s = pd.Series(fwd5)
    baseline = {
        "n": len(fwd5),
        "mean_5d_pct": round(float(base_s.mean()), 4),
        "win_rate_5d": round(float((base_s > 0).mean()), 4),
    }

    results = {"meta": {"date_range": [str(df["date"].iloc[0])[:10], str(df["date"].iloc[-1])[:10]],
                        "n_days": n, "baseline": baseline},
               "scenarios": {}}
    for sid, spec in TRIGGERS.items():
        flags = trigger_flags(df, spec)
        hit_idx = [i for i, f in enumerate(flags) if f and i + 5 < n]
        fwd = {h: [] for h in (1, 3, 5)}
        for i in hit_idx:
            for h in (1, 3, 5):
                fwd[h].append((closes[i + h] / closes[i] - 1) * 100)
        entry = {
            "label": spec["label"],
            "kind": spec["kind"],
            "level": spec["level"],
            "n_hits": len(hit_idx),
        }
        for h, vals in fwd.items():
            if len(vals) >= 3:
                s = pd.Series(vals)
                entry[f"+{h}"] = {
                    "n": len(vals),
                    "mean_pct": round(float(s.mean()), 4),
                    "median_pct": round(float(s.median()), 4),
                    "win_rate": round(float((s > 0).mean()), 4),
                }
        if spec.get("track"):
            # E-004：跌破后 60 日内触及任一五浪目标位的比例
            touched = 0
            for i in hit_idx:
                lo = min(i + 60, n)
                window = closes[i + 1 : lo]
                if any(c <= min(spec["track"]) for c in window):
                    touched += 1
            entry["touch_wave_target_60d"] = (
                {"n": touched, "ratio": round(touched / len(hit_idx), 4)}
                if hit_idx else None
            )
        results["scenarios"][sid] = entry
        print(f"{sid} {spec['label']}: n={entry['n_hits']}", end="")
        for h in (1, 3, 5):
            if f"+{h}" in entry:
                print(f" | +{h}: {entry[f'+{h}']['mean_pct']:+.3f}% "
                      f"(胜率 {entry[f'+{h}']['win_rate']*100:.0f}%)", end="")
        print()

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2, default=str)
    print(f"✅ 已写入 {out_path}")
    print("baseline:", json.dumps(baseline, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
