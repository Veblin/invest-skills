"""A3：flow 合计与份额变化共同起止（共同窗口）的边界用例（离线）。

覆盖任务要求必须测试的四类：首日变化、缺值跨间隔、舍入零、先申后赎净变化零
（份额净变化为零而价格差使 flow 合计非零——合法，不得一概报错）。
另覆盖：尾部 T+1 缺值不得当「无变化」；20/60/120 日跨度窗口。
"""

from __future__ import annotations

import datetime

import pytest

from etf_data import query_etf_share_history


def _dates(n: int, start: datetime.date = datetime.date(2026, 7, 1)) -> list[str]:
    return [(start + datetime.timedelta(days=i)).strftime("%Y%m%d") for i in range(n)]


def _env(dates: list[str], shares: list[float | None],
         closes: list[float] | None = None) -> dict:
    """构造 bridge 信封：shares 为 None 的行不写入 fund_share（模拟 T+1 缺值）。"""
    closes = closes or [4.0] * len(dates)
    fund_share = [{"trade_date": d, "fd_share": sh}
                  for d, sh in zip(dates, shares) if sh is not None]
    fund_daily = [
        {"trade_date": d, "open": c, "high": c + 0.1, "low": c - 0.1,
         "close": c, "pre_close": c, "pct_chg": 0.0, "vol": 10000, "amount": 40000}
        for d, c in zip(dates, closes)
    ]
    return {"status": "ok", "fund_share": fund_share,
            "fund_daily": fund_daily, "note": None}


def _install(monkeypatch, env: dict) -> None:
    def _bridge(getter, *a):
        if getter == "get_etf_share_history":
            return env
        return None

    monkeypatch.setattr("etf_data._bridge_get", _bridge)


def test_successful_share_producer_preserves_html_attribution(monkeypatch):
    from etf_data import build_data_status
    from etf_html import render_etf_html
    dates = _dates(4)
    _install(monkeypatch, _env(dates, [1_000_000, 1_010_000, 1_020_000, 1_030_000]))
    shares = query_etf_share_history("515050", days=3)
    assert shares["available"] and shares["summary"]["share_total_change"] == 30_000
    status = build_data_status(identity={}, profile={}, quote={}, kline={}, share_history=shares)
    html = render_etf_html({"symbol": "515050", "profile": {}, "quote": {}, "kline": {},
                           "share_history": shares, "data_status": status})
    refs = html.split('<section id="refs">', 1)[1].split("</section>", 1)[0]
    assert "份额与资金流" in refs
    assert "Tushare fund_share" in refs and "fund_daily" in refs
    assert "trade_date/fd_share" in refs and "OHLCV" in refs


def test_first_day_change_counted_in_flow_and_share_change(monkeypatch):
    """首日变化段（r0→r1）必须同时进入 flow 合计与份额变化窗口。"""
    dates = _dates(4)
    shares = [1_000_000.0, 1_050_000.0, 1_060_000.0, 1_070_000.0]
    _install(monkeypatch, _env(dates, shares))
    out = query_etf_share_history("515050", days=3)
    s = out["summary"]
    assert s["flow_window"]["start"] == dates[0]      # 首段基期 = r0（不被丢弃）
    assert s["flow_window"]["end"] == dates[-1]
    assert s["flow_window"]["segments"] == 3
    assert s["flow_window"]["trading_days"] == 4
    assert s["flow_window"]["missing_share_days"] == 0
    # 同窗：净变化 = 末−基期（含 +5 万份的首日段）
    assert s["share_total_change"] == 70_000.0
    assert s["share_change_pct"] == pytest.approx(7.0)
    # flow 合计 = 三段和（等价格 4 元：70 万份 × 4 / 1e4 = 28 亿）
    assert s["total_flow_est"] == pytest.approx(28.0)


def test_missing_share_day_crosses_interval_once(monkeypatch):
    """缺值跨间隔：只计一段（基期用缺值前最后一个有效份额日），窗口披露实际覆盖。"""
    dates = _dates(5)
    shares = [100_000.0, 110_000.0, None, 130_000.0, 140_000.0]
    _install(monkeypatch, _env(dates, shares))
    out = query_etf_share_history("515050", days=4)
    s = out["summary"]
    rows = {r["date"]: r for r in out["rows"]}
    assert rows[dates[2]]["share_change"] is None     # 缺值日不是「无变化」
    assert rows[dates[3]]["share_change"] == 20_000.0  # 跨间隔一次计入（110k→130k）
    assert s["flow_window"]["segments"] == 3           # 4 个有效份额行 → 3 段
    assert s["flow_window"]["trading_days"] == 5
    assert s["flow_window"]["missing_share_days"] == 1  # 5−1−3
    assert s["share_total_change"] == 40_000.0          # 140k − 100k（同窗）
    assert s["share_change_pct"] == pytest.approx(40.0)


def test_rounding_to_zero_is_flat_not_missing(monkeypatch):
    """份额变化舍入到 0.00 万份：记 0 流（持平），不当作缺值/None。

    d0→d1 变化 0.004 万份（round→0.00）为持平段；d1→d2 +2000 万份为流入段。
    持平段必须计入 segments/flat_days，flow 0.0（不是 None）。
    """
    dates = _dates(3)
    shares = [1_000_000.0, 1_000_000.004, 1_002_000.0]
    _install(monkeypatch, _env(dates, shares))
    out = query_etf_share_history("515050", days=2)
    s = out["summary"]
    r0 = out["rows"][0]        # detail_rows 首行 = d1（d0→d1 段）
    assert r0["share_change"] == 0.0
    assert r0["flow_est"] == 0.0
    assert r0["direction"] == "→ 持平"
    assert s["flat_days"] == 1
    assert s["inflow_days"] == 1 and s["outflow_days"] == 0
    assert s["flow_window"]["segments"] == 2           # 持平段计入窗口
    assert s["total_flow_est"] == pytest.approx(2000.0 * 4.0 / 10000)  # 0.8 亿
    assert s["share_total_change"] == 2_000.0          # 同窗口径含持平段


def test_net_zero_shares_with_nonzero_flow_price_effect(monkeypatch):
    """先申后赎净变化零：两段均价不同 → flow 合计非零（合法，不报错）。

    d0 收 4.0 → d1 收 4.0：+10000 万份 × 4.0 元 = +4.0 亿；
    d1 → d2 收 6.0：−10000 万份 × 均价 5.0 元 = −5.0 亿；
    净份额变化 0，flow 合计 = −1.0 亿（价格差异，非错误）。
    """
    dates = _dates(3)
    shares = [100_000.0, 110_000.0, 100_000.0]
    _install(monkeypatch, _env(dates, shares, closes=[4.0, 4.0, 6.0]))
    out = query_etf_share_history("515050", days=2)
    s = out["summary"]
    assert s["share_total_change"] == 0.0
    assert s["share_change_pct"] == 0.0
    assert s["total_flow_est"] == pytest.approx(-1.0)
    assert s["flow_window"]["segments"] == 2
    # 口径声明：两者不是一致性约束（QC 不得据此报错）
    assert "不必相等" in out["caliber_note"]


def test_tail_missing_share_days_excluded_from_window(monkeypatch):
    """尾部 T+1 缺值：不在 flow 窗口内（不是「无变化」），窗口末端 < rows 末端。"""
    dates = _dates(6)
    shares = [100_000.0, 110_000.0, 120_000.0, 130_000.0, None, None]
    _install(monkeypatch, _env(dates, shares))
    out = query_etf_share_history("515050", days=5)
    s = out["summary"]
    assert s["flow_window"]["end"] == dates[3]
    assert out["date_range"].endswith(dates[-1])       # rows 覆盖到 d5
    assert s["flow_window"]["start"] == dates[0]
    assert s["share_total_change"] == 30_000.0         # 130k − 100k（不含无份额尾部）
    assert s["flow_window"]["missing_share_days"] == 0  # 尾部缺值在窗外，不虚增窗内缺口


@pytest.mark.parametrize("days", [20, 60, 120])
def test_long_window_spans_segments_and_same_endpoints(monkeypatch, days):
    """20/60/120 日跨度：窗口段数、起止点与份额变化同窗。"""
    n = days + 1
    dates = _dates(n)
    shares = [1_000_000.0 + 1_000.0 * i for i in range(n)]
    _install(monkeypatch, _env(dates, shares))
    out = query_etf_share_history("515050", days=days)
    s = out["summary"]
    assert s["window_days"] == days
    assert s["flow_window"]["segments"] == days
    assert s["flow_window"]["trading_days"] == n
    assert s["flow_window"]["start"] == dates[0]
    assert s["flow_window"]["end"] == dates[-1]
    assert s["share_total_change"] == pytest.approx(1_000.0 * days)
    assert s["share_change_pct"] == pytest.approx(
        round(1_000.0 * days / 1_000_000.0 * 100, 2))
