#!/usr/bin/env python3
"""H5 日历效应回测 — 8/15-8/31 窗口 vs 全年（上证指数 + 附中证全指）。

用法:
  uv run python scripts/backtest_calendar.py                     # 默认上证指数，全历史 + 2006+ 双样本
  uv run python scripts/backtest_calendar.py --min-year 2010     # 样本起点
  uv run python scripts/backtest_calendar.py --out /tmp/h5.json  # 输出路径

数据源: akshare stock_zh_index_daily（sina 全历史）→ 降级 baostock sh.000001
统计口径: ABCD 设计 §3.2 H5 行 — Welch t + permutation + 逐年效应 + 滚动 5 年窗（AMH 时变检查）
输出: JSON（脚本只输出数字与来源标注；报告须引用本 JSON 字段，P0 数字纪律）
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT / "skills") not in sys.path:
    sys.path.insert(0, str(_ROOT / "skills"))

from lib.backtest import (  # noqa: E402 — 路径引导必须在导入前
    WINDOW_END,
    WINDOW_START,
    cohen_d,
    daily_returns,
    describe,
    permutation_test,
    rolling_span_effects,
    significance_grade,
    split_window,
    welch_t,
    yearly_effects,
)

SECONDARY_WINDOW_START = (8, 11)  # ABCD §3.2 H5 行附窗口
DEFAULT_MIN_YEAR = 2006  # 股改后样本（附窗口，主样本为全历史）


def fetch_index_akshare(symbol: str) -> list[dict]:
    """akshare stock_zh_index_daily（sina 源，指数全历史）。返回 [{date, close}]。"""
    import akshare as ak

    df = ak.stock_zh_index_daily(symbol=symbol)
    rows = []
    for _, r in df.iterrows():
        d = r["date"]
        if not isinstance(d, dt.date):
            d = dt.datetime.strptime(str(d), "%Y-%m-%d").date()
        close = float(r["close"])
        if close > 0:
            rows.append({"date": d, "close": close})
    return rows


def fetch_index_baostock(bs_symbol: str) -> list[dict]:
    """baostock 指数日线（降级源）。bs_symbol 如 'sh.000001'。返回 [{date, close}]。"""
    import baostock as bs

    rows: list[dict] = []
    try:
        lg = bs.login()
        if lg.error_code != "0":
            raise RuntimeError(f"baostock login 失败: {lg.error_msg}")
        rs = bs.query_history_k_data_plus(bs_symbol, "date,close", frequency="d")
        if rs.error_code != "0":
            raise RuntimeError(f"baostock 查询失败: {rs.error_msg}")
        while rs.next():
            row = rs.get_row_data()
            close = float(row[1])
            if close > 0:
                rows.append(
                    {
                        "date": dt.datetime.strptime(row[0], "%Y-%m-%d").date(),
                        "close": close,
                    }
                )
    finally:
        bs.logout()
    return rows


def fetch_index(symbol: str) -> tuple[list[dict], str]:
    """主源 akshare → 降级 baostock。返回 (rows, 实际来源标注)。"""
    try:
        rows = fetch_index_akshare(symbol)
        if len(rows) >= 100:
            return rows, f"akshare stock_zh_index_daily({symbol})"
        raise RuntimeError(f"akshare 仅返回 {len(rows)} 行")
    except Exception as exc:  # noqa: BLE001 — 降级链：任何失败都尝试 baostock
        bs_symbol = f"{symbol[:2]}.{symbol[2:]}"
        rows = fetch_index_baostock(bs_symbol)
        return rows, f"baostock({bs_symbol}) [降级原因: {exc}]"


def _window_result(rets: list, start: tuple[int, int], end: tuple[int, int]) -> dict:
    """单窗口完整检验：描述统计 + Welch t + permutation + Cohen d + 分级。"""
    inside, outside = split_window(rets, start, end)
    if len(inside) < 2 or len(outside) < 2:
        return {
            "start": f"{start[0]:02d}-{start[1]:02d}",
            "end": f"{end[0]:02d}-{end[1]:02d}",
            "error": f"样本不足（窗口内 {len(inside)} / 窗口外 {len(outside)}）",
        }
    t, dof = welch_t(inside, outside)
    perm = permutation_test(inside, outside)
    return {
        "start": f"{start[0]:02d}-{start[1]:02d}",
        "end": f"{end[0]:02d}-{end[1]:02d}",
        "inside": describe(inside),
        "outside": describe(outside),
        "diff_mean_pct": describe(inside)["mean_daily_pct"] - describe(outside)["mean_daily_pct"],
        "welch_t": {"t": t, "dof": dof},
        "permutation": perm,
        "cohen_d": cohen_d(inside, outside),
        "grade": significance_grade(t),
    }


def run_symbol(rows: list[dict], source: str, min_year: int | None = None) -> dict:
    """对单个指数的全量检验（全历史 + min-year 子样本）。"""
    all_rets = daily_returns(rows)
    if len(all_rets) < 250:
        return {"error": f"有效收益样本仅 {len(all_rets)} 日", "source": source}

    def _sample(from_year: int | None) -> dict:
        rets = all_rets if from_year is None else [(d, r) for d, r in all_rets if d.year >= from_year]
        if len(rets) < 250:
            return {"error": f"min_year={from_year} 样本仅 {len(rets)} 日"}
        return {
            "n_returns": len(rets),
            "window_8_15_8_31": _window_result(rets, WINDOW_START, WINDOW_END),
            "window_8_11_8_31": _window_result(rets, SECONDARY_WINDOW_START, WINDOW_END),
            "yearly_effects": yearly_effects(rets),
            "rolling_5y": rolling_span_effects(rets, span_years=5),
        }

    subsamples: dict = {"all": _sample(None)}
    if min_year is not None:
        subsamples[f"y{min_year}"] = _sample(min_year)
    return {
        "source": source,
        "date_range": [rows[0]["date"].isoformat(), rows[-1]["date"].isoformat()],
        "n_rows": len(rows),
        "subsamples": subsamples,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="H5 日历效应回测（8/15-8/31 vs 全年）")
    parser.add_argument("--symbol", default="sh000001", help="指数代码（默认 sh000001 上证指数）")
    parser.add_argument("--min-year", type=int, default=DEFAULT_MIN_YEAR, help="附样本起点年份")
    parser.add_argument("--out", default=str(_ROOT / "host-docs" / "v0.2.6" / "H5_backtest_result.json"))
    args = parser.parse_args()

    rows, source = fetch_index(args.symbol)
    result = run_symbol(rows, source, min_year=args.min_year)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2, default=str)
    print(f"✅ 已写入 {out_path}")
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str)[:2000])
    return 0


if __name__ == "__main__":
    sys.exit(main())
