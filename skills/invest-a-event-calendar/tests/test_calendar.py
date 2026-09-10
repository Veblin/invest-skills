"""invest-a-event-calendar 纯函数测试（离线，不触网）。"""

import datetime as _dt
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from unlock_calendar import percentile_rank, process  # noqa: E402


def test_percentile_rank():
    vals = [100.0, 200.0, 300.0, 400.0]
    assert percentile_rank(vals, 100.0) == 0.0
    assert percentile_rank(vals, 250.0) == 50.0
    assert percentile_rank(vals, 500.0) == 100.0
    assert percentile_rank([], 1.0) == 100.0  # 空基准 → 保守 100


def _df():
    today = _dt.date(2026, 9, 8)
    rows = []
    # 回看 3 个样本日（低/中/高市值）
    for i, mv in enumerate([1e9, 3e9, 20e9]):
        rows.append({"解禁时间": (today - _dt.timedelta(days=10 * (i + 1))).strftime("%Y-%m-%d"),
                     "当日解禁股票家数": 5, "解禁数量": 1e8, "实际解禁数量": 1e8,
                     "实际解禁市值": mv, "沪深300指数": 4500.0, "沪深300指数涨跌幅": 0.5})
    return pd.DataFrame(rows)


def test_process_future_high_pressure_flag():
    today = _dt.date(2026, 9, 8)
    df = _df()
    big = {"解禁时间": (today + _dt.timedelta(days=3)).strftime("%Y-%m-%d"),
           "当日解禁股票家数": 8, "解禁数量": 2e8, "实际解禁数量": 2e8,
           "实际解禁市值": 30e9, "沪深300指数": 4500.0, "沪深300指数涨跌幅": None}
    df = pd.concat([df, pd.DataFrame([big])], ignore_index=True)
    out = process(df, today, past_days=120, future_days=30)
    assert len(out["past"]) == 3
    fut = out["future"]
    assert len(fut) == 1
    r = fut[0]
    assert r["市值亿"] == 300.0                      # 30e9 元 → 300 亿
    assert r["rank"] == 100.0                        # 大于回看 3 样本全部
    assert r["flag"] == "🔴 极高压力"                 # >=90 分位
    assert r["hs300_chg"] is None                    # 未来行无当日表现
    past_last = out["past"][-1]
    assert past_last["hs300_chg"] == 0.5             # 回看行带沪深300表现


def test_process_units_and_past_window_rank_base():
    today = _dt.date(2026, 9, 8)
    out = process(_df(), today, past_days=120, future_days=30)
    assert out["past"][0]["市值亿"] == 200.0         # 最早日 20e9 元 → 200 亿
    assert out["past"][-1]["市值亿"] == 10.0         # 最近日 1e9 元 → 10 亿
    assert out["past"][0]["数量亿股"] == 1.0         # 1e8 股 = 1 亿股
    # 回看样本全部进入分位基准（都在 120 日内）
    assert len(out["hist_billion"]) == 3


def test_process_future_outside_window_excluded():
    today = _dt.date(2026, 9, 8)
    df = _df()
    far = {"解禁时间": (today + _dt.timedelta(days=90)).strftime("%Y-%m-%d"),
           "当日解禁股票家数": 1, "解禁数量": 1e8, "实际解禁数量": 1e8,
           "实际解禁市值": 5e9, "沪深300指数": 4500.0, "沪深300指数涨跌幅": None}
    df = pd.concat([df, pd.DataFrame([far])], ignore_index=True)
    out = process(df, today, past_days=120, future_days=30)
    assert len(out["future"]) == 0                   # 90 天外被排除


def test_process_empty_df():
    out = process(pd.DataFrame(), _dt.date(2026, 9, 8), 120, 30)
    assert out["past"] == [] and out["future"] == [] and out["error"] is None
