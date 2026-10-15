"""R4：份额拆分不能被当作申赎流（登记公告调整 + 未登记跳变透明停算）。

真实案例：513660（华夏沪港通恒生 ETF）2026-04-20/21 按 1:2 拆分（公告
2026-04-15，已登记）；120 交易日窗口跨该日，若不调整会把机械翻倍算成
+100% 巨额净流入。
"""

from __future__ import annotations

import datetime

import pytest

from etf_data import (
    ETF_SHARE_SPLITS,
    SPLIT_SUSPECT_RATIO,
    query_etf_share_history,
)


def _env(dates: list[str], shares: list[float | None],
         closes: list[float] | None = None) -> dict:
    closes = closes or [4.0] * len(dates)
    return {
        "status": "ok",
        "fund_share": [{"trade_date": d, "fd_share": s}
                       for d, s in zip(dates, shares) if s is not None],
        "fund_daily": [{"trade_date": d, "open": c, "high": c, "low": c,
                        "close": c, "pre_close": c, "pct_chg": 0.0,
                        "vol": 10000, "amount": 40000}
                       for d, c in zip(dates, closes)],
        "note": None,
    }


def _install(monkeypatch, env: dict) -> None:
    monkeypatch.setattr("etf_data._bridge_get",
                        lambda getter, *a: env if getter == "get_etf_share_history" else None)


def _dates(start: datetime.date, n: int) -> list[str]:
    return [(start + datetime.timedelta(days=i)).strftime("%Y%m%d") for i in range(n)]


def test_registry_513660_split_registered_with_source():
    ev = ETF_SHARE_SPLITS["513660"][0]
    assert ev["ratio"] == 2.0 and ev["effective_from"] == "20260420"
    assert ev["record_date"] == "2026-04-20" and ev["ex_date"] == "2026-04-21"
    assert ev["source"]["sha256"] == (
        "bbf9645e949ea3100d59b1063d3fe645d509b13c2c53850bc6352e85efc90027")
    assert "1:2" in ev["source"]["locator"] or "1：2" in ev["source"]["locator"]


def test_registered_split_day_is_not_flow(monkeypatch):
    """拆分日（数据口径 20260420）机械翻倍不得计入 flow：调整后 Δ=0。"""
    ds = _dates(datetime.date(2026, 4, 16), 4)   # 0416, 0417, 0418, 0419 → 无 0420
    ds = ["20260416", "20260417", "20260420", "20260421", "20260422"]
    shares = [100_000.0, 100_000.0, 200_000.0, 200_000.0, 200_000.0]  # 0420 起翻倍
    _install(monkeypatch, _env(ds, shares))
    out = query_etf_share_history("513660", days=4)
    rows = {r["date"]: r for r in out["rows"]}
    split_row = rows["20260420"]
    assert split_row["share_change"] == 0.0          # 机械翻倍已被调整消除
    assert split_row["flow_est"] == 0.0
    assert split_row["shares"] == 200_000.0          # 原始披露保留
    assert split_row["shares_split_adj"] == 200_000.0  # 最新基准=调整后同值
    assert out["summary"]["flow_window"]["confirmable"] is True
    assert out["summary"]["share_change_pct"] == 0.0  # 窗口净变化 0
    assert out["summary"]["total_flow_est"] == 0.0
    # 拆分事件随封存披露（含原文来源）
    ev = out["summary"]["split_events"][0]
    assert ev["effective_from"] == "20260420" and ev["ratio"] == 2.0
    assert ev["source"]["sha256"]


def test_registered_split_preserves_real_subscription(monkeypatch):
    """拆分日真实申购仍保留：raw 0420=210,000 = 2×100,000（拆分）+ 10,000（真实）。
    调整后（最新基准=拆分后口径）：基期 100,000×2=200,000 → Δ=+10,000（+5%）。
    同时验证**金额口径**：拆分前后价格成对调整，flow_est 不得被未调整价放大。"""
    ds = ["20260417", "20260420", "20260421"]
    shares = [100_000.0, 210_000.0, 210_000.0]
    # 份额 0420 生效，价格 0421 除权：0420 仍是除权前的 8 元。
    _install(monkeypatch, _env(ds, shares, closes=[8.0, 8.0, 4.0]))
    out = query_etf_share_history("513660", days=2)
    rows = {r["date"]: r for r in out["rows"]}
    assert rows["20260420"]["share_change"] == 10_000.0  # 调整后真实变化（万份）
    assert out["summary"]["share_change_pct"] == 5.0     # 10,000/200,000
    # 金额：+10,000 万份 × 调整价 4.0 元 / 1e4 = +4.0 亿（若用未调整价：25,000×... 错）
    r20 = rows["20260420"]
    assert r20["flow_est"] == pytest.approx(4.0)
    assert r20["close_split_adj"] == pytest.approx(4.0)   # 价格已调整
    assert r20["close"] == pytest.approx(8.0)             # 原始价保留
    assert out["summary"]["total_flow_est"] == pytest.approx(4.0)


@pytest.mark.parametrize("dates,shares,closes,expected", [
    (["20260417", "20260420"], [100_000.0, 210_000.0], [8.0, 8.0], 4.0),
    (["20260420", "20260421"], [210_000.0, 220_000.0], [8.0, 4.0], 4.0),
    (["20260421", "20260422"], [210_000.0, 220_000.0], [4.0, 4.0], 4.0),
])
def test_price_ex_date_window_boundaries(monkeypatch, dates, shares, closes, expected):
    """窗口止于登记日、始于登记日、始于除权日均保留真实申赎金额。"""
    _install(monkeypatch, _env(dates, shares, closes))
    out = query_etf_share_history("513660", days=1)
    assert out["summary"]["total_flow_est"] == pytest.approx(expected)
    assert out["summary"]["flow_window"]["base_row"]["close_split_adj"] == pytest.approx(4.0)


def test_pre_split_real_subscription_money_not_inflated(monkeypatch):
    """拆分**前**的真实净申购金额不被 2× 放大（Codex R4 复查反例）。

    0416→0417 均为拆分前：raw +1,000 万份（真实申购）、价格 8.0 → 8.1（拆分前价）；
    调整后份额 +2,000 万份、调整价 4.0 → 4.05；flow = 2,000×4.025/1e4 = 0.805 亿，
    与未调整口径 1,000×8.05/1e4 = 0.805 亿一致（经济恒等式 shares_adj×price_adj=raw×raw）。
    拆分日（0421，数据口径）机械翻倍份额 + 价格减半：调整后 Δ=0、flow=0。
    """
    ds = ["20260416", "20260417", "0421-no", "0422-no"]
    ds = ["20260416", "20260417", "20260421", "20260422"]
    shares = [100_000.0, 101_000.0, 202_000.0, 202_000.0]
    _install(monkeypatch, _env(ds, shares, closes=[8.0, 8.1, 4.05, 4.05]))
    out = query_etf_share_history("513660", days=3)
    rows = {r["date"]: r for r in out["rows"]}
    # 0417 段：拆分前真实申购
    r17 = rows["20260417"]
    assert r17["share_change"] == 2_000.0                 # 调整后（=1,000×2）
    assert r17["close_split_adj"] == pytest.approx(4.05)  # 8.1/2
    # 与 raw 口径一致：round(1,000×8.05/1e4, 2) = round(0.805, 2) = 0.81（同一舍入）
    assert r17["flow_est"] == pytest.approx(round(1_000 * 8.05 / 1e4, 2))
    assert r17["close"] == pytest.approx(8.1)             # 原始价保留
    # 拆分日（0421）机械翻倍：调整后 Δ=0、flow=0
    r21 = rows["20260421"]
    assert r21["shares"] == 202_000.0
    assert r21["shares_split_adj"] == 202_000.0
    assert r21["close_split_adj"] == pytest.approx(4.05)
    assert r21["share_change"] == 0.0
    assert r21["flow_est"] == 0.0


def test_unregistered_jump_is_flagged_not_flow(monkeypatch):
    """未登记的大跳变（≥阈值）：该段流量不可确认，窗口标 confirmable=False，
    flow 合计只含可确认段（不得把机械份额变化当申赎流）。"""
    ds = ["20260803", "20260804", "20260805", "20260806"]
    shares = [10_000.0, 10_000.0, 22_000.0, 22_000.0]   # 0805 跳 +120%（无登记）
    _install(monkeypatch, _env(ds, shares))
    out = query_etf_share_history("510300", days=3)
    rows = {r["date"]: r for r in out["rows"]}
    assert rows["20260805"]["suspected_adjustment"] is True
    assert rows["20260805"]["flow_est"] is None          # 流量不可确认
    assert "不可确认" in rows["20260805"]["direction"]
    s = out["summary"]
    assert s["flow_window"]["confirmable"] is False
    assert s["flow_window"]["unconfirmable_segments"] == 1
    assert "未登记份额跳变" in s["flow_window"]["note"]
    assert s["total_flow_est"] == 0.0                    # 只含可确认段（0804 段 0）
    assert SPLIT_SUSPECT_RATIO == 0.5                    # 阈值显式（不隐式漂移）


@pytest.mark.parametrize("code", ["513660", "159920"])
def test_split_factor_absent_means_no_field(monkeypatch, code):
    """无登记拆分的标的不得出现 shares_split_adj 字段（零影响、零噪声）。"""
    ds = ["20260803", "20260804", "20260805"]
    shares = [10_000.0, 10_100.0, 10_200.0]
    _install(monkeypatch, _env(ds, shares))
    out = query_etf_share_history(code, days=2)
    assert all("shares_split_adj" not in r for r in out["rows"])
    assert "split_events" not in out["summary"]
