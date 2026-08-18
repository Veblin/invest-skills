#!/usr/bin/env python3
"""F 系列历史演变分布刻画（v0.2.6，预注册见 backtest_prereg/F{1,2,3}_预注册.md）。

用法:
  uv run python scripts/backtest_futures.py --hypothesis F1     # 基差分位 → ETF 收益分布
  uv run python scripts/backtest_futures.py --hypothesis F2     # 贴水极值 → 指数 20 日收益分布
  uv run python scripts/backtest_futures.py --hypothesis F3     # 持仓量变化 → 基差/收益联合演变

定位（用户定稿）：状态度量与历史演变分布刻画，不做市场预测。
输出: docs/data/F{1,2,3}_backtest_result.json（数字全部 Python 计算）。
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

import pandas as pd

_ROOT = Path(__file__).resolve().parent.parent
for _p in (
    str(_ROOT / "skills" / "lib"),
    str(_ROOT / "skills"),
    str(_ROOT / "skills" / "invest-a-stock" / "scripts"),
):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from backtest import describe, permutation_test, significance_grade, welch_t  # noqa: E402
from multiple_testing import bootstrap_ci  # noqa: E402
from stats import expanding_percentile_rank  # noqa: E402

INDEX_SYMBOL = {"IF": "sh000300", "IH": "sh000016", "IC": "sh000905", "IM": "sh000852"}
ETF_MAP = {"IC": ["510500"], "IM": ["512100", "159845"]}  # 基差品种 → ETF


def load_futures_df(symbol: str) -> pd.DataFrame:
    """futures_daily → DataFrame（date ASC）。"""
    from lib import store

    rows = store.load_futures_daily(symbol=symbol, limit=10000)
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["date"] = df["date"].astype(str)
    return df.sort_values("date").reset_index(drop=True)


def load_index_closes(symbol: str) -> dict[str, float]:
    import akshare as ak

    df = ak.stock_zh_index_daily(symbol=symbol)
    return {str(r["date"])[:10]: float(r["close"]) for _, r in df.iterrows()}


def load_etf_closes(etf: str) -> dict[str, float]:
    import akshare as ak

    sym = f"sh{etf}" if etf.startswith("5") else f"sz{etf}"
    df = ak.fund_etf_hist_sina(symbol=sym)
    return {str(r["date"])[:10]: float(r["close"]) for _, r in df.iterrows()}


def _describe_group(vals: list[float]) -> dict:
    d = describe(vals)
    return {"n": d["n"], "mean_pct": d["mean_daily_pct"],
            "median_pct": d["median_daily_pct"], "win_rate": d["up_prob"]}


def run_f1(out_path: Path) -> dict:
    """基差深度四分位 → IC/IM ETF 次日/5 日收益分布。"""
    results = {"hypothesis": "F1", "etfs": {}, "meta": {}}
    for sym, etfs in ETF_MAP.items():
        fdf = load_futures_df(sym)
        if fdf.empty:
            results["etfs"][sym] = {"error": "futures 数据缺失"}
            continue
        # 基差历史分位：expanding window（截至当日——全序列分位把未来
        # 信息泄漏进当日标签，look-ahead 修复）；min_history=30 暖机
        # （首行 inclusive 分位恒 100 → 无暖机会在序列首日产生幻影极值）
        basis = fdf["basis_pct"].astype(float)
        fdf["depth_pctile"] = expanding_percentile_rank(basis.tolist(), min_history=30)
        fdf["depth_pctile"] = fdf["depth_pctile"].astype(float)  # 暖机期 None → NaN
        fdf["quartile"] = pd.cut(fdf["depth_pctile"], [0, 25, 50, 75, 100],
                                 labels=["Q1_深贴水", "Q2", "Q3", "Q4_浅贴水升水"], include_lowest=True)
        for etf in etfs:
            closes = load_etf_closes(etf)
            dates = [d for d in fdf["date"] if d in closes]
            fdf_etf = fdf[fdf["date"].isin(dates)].reset_index(drop=True)
            entry: dict = {"n_aligned_days": len(dates), "quartiles": {}, "baseline": {}}
            all_fwd: dict[int, list[float]] = defaultdict(list)
            for _, row in fdf_etf.iterrows():
                d = row["date"]
                # 暖机期/NaN 基差 → 分位 NaN → 无四分位，不入任何桶
                # （str(NaN)='nan' 会产生幻影桶）
                if not isinstance(row["quartile"], str):
                    continue
                q = row["quartile"]
                for h in (1, 5):
                    # ETF 未来收益：按交易日序列（closes 有序）；keys[0] 为 d 后
                    # 第 1 个交易日 → h 日收益取 keys[h-1]
                    keys = [k for k in closes if k > d]
                    if len(keys) < h:
                        continue
                    fwd = (closes[keys[h - 1]] / closes[d] - 1) * 100
                    entry["quartiles"].setdefault(q, defaultdict(list))[h].append(fwd)
                    all_fwd[h].append(fwd)
            for q, by_h in entry["quartiles"].items():
                entry["quartiles"][q] = {
                    f"+{h}": _describe_group(vals) for h, vals in by_h.items()
                    if len(vals) >= 3
                }
            entry["baseline"] = {
                f"+{h}": _describe_group(vals) for h, vals in all_fwd.items()
                if len(vals) >= 3
            }
            # Q1 vs Q4 差异强度（描述性）
            q1 = entry["quartiles"].get("Q1_深贴水", {})
            q4 = entry["quartiles"].get("Q4_浅贴水升水", {})
            if "+5" in q1 and "+5" in q4:
                # 需要原始列表——重建
                fwd_by_q: dict[str, list[float]] = defaultdict(list)
                for _, row in fdf_etf.iterrows():
                    d = row["date"]
                    if not isinstance(row["quartile"], str):
                        continue  # 暖机期/NaN 基差无四分位
                    keys = [k for k in closes if k > d]
                    if len(keys) < 5:
                        continue
                    fwd_by_q[row["quartile"]].append(
                        (closes[keys[4]] / closes[d] - 1) * 100)
                t, _ = welch_t(fwd_by_q.get("Q1_深贴水", []), fwd_by_q.get("Q4_浅贴水升水", []))
                perm = permutation_test(fwd_by_q.get("Q1_深贴水", []),
                                        fwd_by_q.get("Q4_浅贴水升水", []), n_perm=2000, seed=42)
                entry["q1_vs_q4_+5"] = {
                    "welch_t": t, "permutation_p": perm["p_value"],
                    "note": "差异强度仅描述性，非因果裁决",
                }
            results["etfs"][f"{sym}_{etf}"] = entry
    results["meta"] = {"note": "份额流口径不可得（快照仅 2 天）→ ETF 价格收益口径（预注册冻结）"}
    out_path.write_text(json.dumps(results, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return results


def run_f2(out_path: Path) -> dict:
    """贴水极值（分位 <10%）/ 升水极值（分位 >90%）→ 指数 +5/+10/+20 收益分布。"""
    results = {"hypothesis": "F2", "scenarios": {}, "baseline": {}}
    for sym, idx_code in INDEX_SYMBOL.items():
        fdf = load_futures_df(sym)
        if fdf.empty:
            results["scenarios"][sym] = {"error": "futures 数据缺失"}
            continue
        basis = fdf["basis_pct"].astype(float).tolist()
        # expanding 分位（无未来信息；升序分位：p<10 深贴水 / p>90 升水）；
        # min_history=30 暖机（首行 inclusive 分位恒 100 → 幻影升水事件修复）
        fdf["pctile"] = expanding_percentile_rank(basis, min_history=30)
        closes = load_index_closes(idx_code)
        all_dates = sorted(closes)
        entry = {"deep_discount": {}, "premium": {}, "n_events": {}}
        # 事件首日（连续极值只计首日）；expanding_percentile_rank 为升序分位，
        # 基差负值 = 贴水 → 分位越低贴水越深：deep_discount = p<10，premium = p>90
        for state, cond in (("deep_discount", lambda p: p < 10), ("premium", lambda p: p > 90)):
            ev_dates = []
            prev_state = False
            for _, row in fdf.iterrows():
                p = row["pctile"]
                # 暖机期/NaN 基差 → 分位 None → 不入事件（None < 10 抛 TypeError）
                cur = cond(p) if p is not None else False
                if cur and not prev_state:
                    ev_dates.append(row["date"])
                prev_state = cur
            fwd: dict[int, list[float]] = defaultdict(list)
            used = 0  # 进入前向统计的事件数（与 +5 n 一致）
            for d in ev_dates:
                if d not in closes:
                    continue  # 期货交易日不在指数收盘日历（补班/数据缺口）→ 跳过
                keys = [k for k in all_dates if k > d]
                if len(keys) < 5:
                    continue  # 尾部事件无 5 日前向 → 不计入 n_events
                for h in (5, 10, 20):
                    if len(keys) < h:
                        continue
                    fwd[h].append((closes[keys[h - 1]] / closes[d] - 1) * 100)
                used += 1
            for h, vals in fwd.items():
                if len(vals) >= 3:
                    entry[state][f"+{h}"] = _describe_group(vals)
            entry["n_events"][state] = used
        # 无条件基线
        for h in (5, 10, 20):
            vals = [(closes[all_dates[i + h]] / closes[all_dates[i]] - 1) * 100
                    for i in range(len(all_dates) - h)]
            results["baseline"].setdefault(f"+{h}", _describe_group(vals))
        results["scenarios"][sym] = entry
    results["meta"] = {"note": "事件首日口径；n_events = 进入前向统计的事件数（+5 口径，日历守卫/尾部跳过不计）"}
    out_path.write_text(json.dumps(results, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return results


def _basis_state_after(
    basis_map: dict[str, float], closes: dict[str, float],
    all_dates: list[str], d: str, *, horizon: int = 20,
) -> tuple[str, float] | None:
    """事件日 d 后 horizon 个指数交易日的（基差方向, 指数收益%）。

    基差与收益共用指数交易日历（原实现期货行号 fi+20 vs 指数行号 idx0+20
    错位，且每月数据洞放大错位——finding #7）；
    事件日不在指数日历 / 目标日不在期货日历 / 任一端基差缺失 → None。
    """
    idx0 = all_dates.index(d) if d in all_dates else None
    if idx0 is None or idx0 + horizon >= len(all_dates):
        return None
    tgt = all_dates[idx0 + horizon]
    b0 = basis_map.get(d)
    b1 = basis_map.get(tgt)
    if b0 is None or b1 is None or pd.isna(b0) or pd.isna(b1):
        return None
    basis_dir = "converge" if abs(b1) < abs(b0) else "diverge"
    idx_chg = (closes[tgt] / closes[d] - 1) * 100
    return basis_dir, idx_chg


def _oi_20d_series(oi_list: list) -> list[float | None]:
    """20 日持仓复利变化序列（run_f3 事件循环用）。

    前 19 行无满 20 日窗口 → None（对齐旧 rolling(20, min_periods=20) 语义——
    18-19 因子的短窗口不得冒充 20 日变化）；有效因子阈值见
    lib.futures_data.compound_oi_change（口径单份实现）。
    """
    from lib.futures_data import compound_oi_change  # noqa: E402 — 惰性导入（invest-a-stock 路径）

    return [compound_oi_change(oi_list[max(0, i - 19) : i + 1]) if i >= 19 else None
            for i in range(len(oi_list))]


def run_f3(out_path: Path) -> dict:
    """持仓量 20 日变化方向 → 后 20 日基差演变 × 指数收益联合分布 + Granger 方向检验。"""
    results = {"hypothesis": "F3", "scenarios": {}, "granger": {}}
    for sym, idx_code in INDEX_SYMBOL.items():
        fdf = load_futures_df(sym)
        if fdf.empty:
            results["scenarios"][sym] = {"error": "futures 数据缺失"}
            continue
        # 20 日持仓变化：共享 helper（口径与消费方一致——掩码/有效数阈值
        # 单份实现；finding #10）。定位为展期节奏度量（F3 结论已降级为
        # "不可刻画持仓状态"，此处仅保留历史演变刻画用）
        fdf["oi_20d_chg"] = _oi_20d_series(fdf["oi_change_pct"].tolist())
        closes = load_index_closes(idx_code)
        all_dates = sorted(closes)
        entry = {"up": {}, "down": {}, "n_events": {}}
        basis_map = dict(zip(fdf["date"], fdf["basis_pct"].astype(float)))
        for state, cond in (("up", lambda v: v >= 5.0), ("down", lambda v: v <= -5.0)):
            ev_dates = []
            prev_state = False
            for _, row in fdf.iterrows():
                cur = cond(row["oi_20d_chg"]) if row["oi_20d_chg"] is not None else False
                if cur and not prev_state:
                    ev_dates.append(row["date"])
                prev_state = cur
            cross: dict[tuple[str, str], int] = defaultdict(int)
            for d in ev_dates:
                st = _basis_state_after(basis_map, closes, all_dates, d)
                if st is None:
                    continue
                basis_dir, idx_chg = st
                idx_dir = "up" if idx_chg > 0 else "down"
                cross[(basis_dir, idx_dir)] += 1
            entry[state] = {"cross_tab": {f"{k[0]}|{k[1]}": v for k, v in sorted(cross.items())},
                            "n": len(ev_dates)}
            entry["n_events"][state] = len(ev_dates)
        # Granger 方向检验：指数日收益 → oi_chg（滞后 1/3/5 日，NW t）
        from backtest import hac_t_stats

        gr = {}
        idx_ret = {}
        for i in range(1, len(all_dates)):
            if closes[all_dates[i - 1]] > 0:
                idx_ret[all_dates[i]] = (closes[all_dates[i]] / closes[all_dates[i - 1]] - 1) * 100
        oi_chg_map = dict(zip(fdf["date"], fdf["oi_chg"].astype(float)))
        for lag in (1, 3, 5):
            y, x = [], []
            dates = fdf["date"].tolist()
            for i in range(lag, len(dates)):
                d = dates[i]
                prev_d = dates[i - lag]
                if prev_d in idx_ret and pd.notna(oi_chg_map.get(d)):
                    y.append(float(oi_chg_map[d]))
                    x.append(idx_ret[prev_d])
            if len(y) >= 30:
                reg = hac_t_stats(y, [x], names=[f"idx_ret_lag{lag}"])
                gr[f"lag{lag}"] = {"t": reg["hac_t_stats"][1], "coef": reg["coefs"][0],
                                   "n": len(y),
                                   "note": "仅防因果倒置：|t|≥3 提示收益领先持仓，解读降级"}
        results["granger"][sym] = gr
        results["scenarios"][sym] = entry
    out_path.write_text(json.dumps(results, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="F 系列历史演变分布刻画")
    parser.add_argument("--hypothesis", choices=["F1", "F2", "F3"], required=True)
    parser.add_argument("--out", default=None)
    args = parser.parse_args()
    out = Path(args.out) if args.out else _ROOT / "docs" / "data" / f"{args.hypothesis}_backtest_result.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    runner = {"F1": run_f1, "F2": run_f2, "F3": run_f3}[args.hypothesis]
    results = runner(out)
    print(f"✅ {args.hypothesis} 已写入 {out}")
    print(json.dumps(results, ensure_ascii=False, indent=2, default=str)[:2500])
    return 0


if __name__ == "__main__":
    sys.exit(main())
