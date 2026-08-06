"""R11a ETF 历史行情深度：baostock 双源回退 + 历史统计（全部 mock，零活体网络）。

用例：
    ① baostock_code 两市映射 + 非法代码抛错
    ② mock nav 链路失败 → 自动回退 baostock 成功且 source=="baostock"
    ③ compute_history_stats 统计正确性（已知序列 + MA 尾部值）
    ④ nav 序列与 baostock 构造序列交易日序列一致（各源内统计自洽）
冻结 fixture（skills/invest-a-stock/tests/fixtures/v0.2.4/588000_nav_history.json）
用于验证实测口径：142 交易日、16 个 ±5% 交易日。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import etf_data
from etf_data import (
    baostock_code,
    compute_history_stats,
    fetch_etf_kline_baostock,
    query_etf_kline_history,
)
from etf_timeline import (
    align_events_with_price,
    detect_big_move_days,
    load_events_file,
    validate_events_file,
)

_FIXTURE = (
    Path(__file__).resolve().parent.parent.parent
    / "invest-a-stock" / "tests" / "fixtures" / "v0.2.4" / "588000_nav_history.json"
)


# ---------------------------------------------------------------------------
# ① baostock_code 两市映射
# ---------------------------------------------------------------------------

def test_baostock_code_exchange_mapping():
    # 沪市：51/56/58 前缀
    assert baostock_code("588000") == "sh.588000"
    assert baostock_code("510300") == "sh.510300"
    assert baostock_code("563300") == "sh.563300"
    assert baostock_code("518880") == "sh.518880"
    # 深市：15/16/18 前缀
    assert baostock_code("159915") == "sz.159915"
    assert baostock_code("159949") == "sz.159949"
    assert baostock_code("163406") == "sz.163406"
    # 非法代码（股票/不支持的交易所前缀）→ ValueError
    with pytest.raises(ValueError, match="无法映射交易所"):
        baostock_code("600000")
    with pytest.raises(ValueError, match="无法映射交易所"):
        baostock_code("000001")
    with pytest.raises(ValueError, match="无法映射交易所"):
        baostock_code("123456")


def test_fetch_etf_kline_baostock_invalid_code_no_network():
    # 非法代码在 import baostock / 网络之前即抛错 → status=missing 信封（不阻塞）
    env = fetch_etf_kline_baostock("600000", days=250)
    assert env["status"] == "missing"
    assert env["source"] == "baostock"
    assert "无法映射交易所" in env["error"]
    assert env["rows"] == []


# ---------------------------------------------------------------------------
# ② nav 链路失败 → 自动回退 baostock
# ---------------------------------------------------------------------------

def test_query_etf_kline_history_falls_back_to_baostock(monkeypatch):
    # nav 链路全失败（_bridge_get → None → query_etf_kline status=missing）
    monkeypatch.setattr(etf_data, "_bridge_get", lambda *a, **k: None)
    baostock_rows = [
        {"date": "2026-07-01", "open": 1.50, "close": 1.52, "high": 1.55,
         "low": 1.49, "volume": 1_000_000, "change_pct": 1.33},
        {"date": "2026-07-02", "open": 1.52, "close": 1.48, "high": 1.53,
         "low": 1.47, "volume": 1_200_000, "change_pct": -2.63},
    ]

    def _fake_baostock(symbol, days=250):
        assert symbol == "588000" and days == 250
        return {"status": "ok", "source": "baostock", "code": f"sh.{symbol}",
                "rows": baostock_rows, "error": None}

    monkeypatch.setattr(etf_data, "fetch_etf_kline_baostock", _fake_baostock)
    out = query_etf_kline_history("588000", days=250)
    assert out["status"] == "available"
    assert out["source"] == "baostock"
    assert [r["date"] for r in out["rows"]] == ["2026-07-01", "2026-07-02"]


def test_query_etf_kline_history_nav_source_when_available(monkeypatch):
    # nav 链路成功 → source == "nav"，且不应触发 baostock 回退
    nav_rows = [
        {"date": f"2026-06-{d:02d}", "nav": 1.50 + 0.01 * i, "change_pct": 0.7}
        for i, d in enumerate(range(15, 30))
    ]
    monkeypatch.setattr(etf_data, "_bridge_get", lambda *a, **k: {
        "status": "ok", "source": "fund_etf_fund_info_em", "rows": nav_rows, "error": None})
    monkeypatch.setattr(
        etf_data, "fetch_etf_kline_baostock",
        lambda symbol, days=250: pytest.fail("nav 可用时不应回退 baostock"),
    )
    out = query_etf_kline_history("588000", days=250)
    assert out["status"] == "available"
    assert out["source"] == "nav"
    assert len(out["rows"]) >= 10


def test_query_etf_kline_history_both_sources_missing(monkeypatch):
    # 双链路均不可用 → status=missing + error 汇总，不抛异常
    monkeypatch.setattr(etf_data, "_bridge_get", lambda *a, **k: None)
    monkeypatch.setattr(etf_data, "fetch_etf_kline_baostock",
                        lambda symbol, days=250: {"status": "missing", "source": "baostock",
                                                  "code": None, "rows": [], "error": "boom"})
    out = query_etf_kline_history("588000", days=250)
    assert out["status"] == "missing"
    assert out["source"] == "none"
    assert "boom" in out["error"]


# ---------------------------------------------------------------------------
# ③ compute_history_stats 统计正确性
# ---------------------------------------------------------------------------

def test_compute_history_stats_known_series():
    rows = [
        {"date": "2026-01-05", "close": 100.0},
        {"date": "2026-01-06", "close": 95.0},
        {"date": "2026-01-07", "close": 110.0},
        {"date": "2026-01-08", "close": 105.0},
        {"date": "2026-01-09", "close": 120.0},
        {"date": "2026-01-12", "close": 90.0},
        {"date": "2026-01-13", "close": 118.0},
    ]
    s = compute_history_stats(rows)
    assert s["status"] == "available"
    assert s["rows"] == 7
    assert s["date_range"] == "2026-01-05 ~ 2026-01-13"
    # 年度高低点
    assert s["annual_high"] == {"date": "2026-01-09", "close": 120.0}
    assert s["annual_low"] == {"date": "2026-01-12", "close": 90.0}
    # 最大回撤：峰值 120 @ 01-09 → 谷底 90 @ 01-12 = -25%
    assert s["max_drawdown"]["drawdown_pct"] == pytest.approx(-25.0)
    assert s["max_drawdown"]["peak_date"] == "2026-01-09"
    assert s["max_drawdown"]["peak_close"] == pytest.approx(120.0)
    assert s["max_drawdown"]["trough_date"] == "2026-01-12"
    assert s["max_drawdown"]["trough_close"] == pytest.approx(90.0)
    # |change_pct| >= 5：-5.0 / +15.79 / +14.29（01-08 的 -4.55% 不达标）/ -25.0 / +31.11
    got = {r["date"]: r["change_pct"] for r in s["big_move_days"]}
    assert set(got) == {"2026-01-06", "2026-01-07", "2026-01-09", "2026-01-12", "2026-01-13"}
    assert got["2026-01-06"] == pytest.approx(-5.0)
    assert got["2026-01-07"] == pytest.approx(15.79, abs=0.01)
    assert got["2026-01-09"] == pytest.approx(14.29, abs=0.01)
    assert got["2026-01-12"] == pytest.approx(-25.0)
    assert got["2026-01-13"] == pytest.approx(31.11, abs=0.01)
    # 当前价 vs 高低点
    assert s["current_vs_high_pct"] == pytest.approx(-1.67, abs=0.01)
    assert s["current_vs_low_pct"] == pytest.approx(31.11, abs=0.01)
    # 不足 20 行 → MA 全部 None
    assert s["ma20"] is None
    assert s["ma60"] is None
    assert s["ma120"] is None


def test_compute_history_stats_ma_tail_values_and_nav_key():
    # nav 链路行（nav 键）兼容；130 行单调序列：closes[i] = 1.0 + 0.01*i
    rows = [
        {"date": f"2026-{i // 28 + 1:02d}-{i % 28 + 1:02d}", "nav": 1.0 + 0.01 * i}
        for i in range(130)
    ]
    s = compute_history_stats(rows)
    assert s["status"] == "available"
    # MA 尾部值 = 最近 n 行收盘均值
    assert s["ma20"] == pytest.approx(2.195, abs=1e-4)    # 均值索引 119.5
    assert s["ma60"] == pytest.approx(1.995, abs=1e-4)    # 均值索引 99.5
    assert s["ma120"] == pytest.approx(1.695, abs=1e-4)   # 均值索引 69.5
    assert s["annual_high"]["close"] == pytest.approx(2.29)
    assert s["annual_low"]["close"] == pytest.approx(1.0)
    assert s["current_vs_high_pct"] == pytest.approx(0.0)
    assert s["current_vs_low_pct"] == pytest.approx(129.0)
    # 单调 +1% 序列无 |change_pct|>=5 交易日
    assert s["big_move_days"] == []


def test_compute_history_stats_insufficient():
    s = compute_history_stats([{"date": "2026-01-05", "close": 1.0}])
    assert s["status"] == "insufficient"
    assert s["annual_high"] is None
    assert s["max_drawdown"] is None
    assert s["big_move_days"] == []
    s2 = compute_history_stats([])
    assert s2["status"] == "insufficient"
    assert s2["date_range"] is None


# ---------------------------------------------------------------------------
# ④ nav 序列 vs baostock 构造序列：交易日序列一致（各源内统计，不做数值级交叉）
# ---------------------------------------------------------------------------

def test_nav_and_baostock_trading_day_sequence_consistent():
    dates = [
        "2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08", "2026-01-09",
        "2026-01-12", "2026-01-13", "2026-01-14", "2026-01-15", "2026-01-16",
    ]
    closes = [1.00, 1.05, 1.02, 1.10, 1.08, 1.15, 1.12, 1.20, 1.18, 1.22]
    nav_rows = [{"date": d, "nav": c, "change_pct": None} for d, c in zip(dates, closes)]
    bs_rows = [
        {"date": d, "open": c, "close": c, "high": c, "low": c, "volume": 1_000_000}
        for d, c in zip(dates, closes)
    ]
    nav_stats = compute_history_stats(nav_rows)
    bs_stats = compute_history_stats(bs_rows)
    # 交易日序列一致
    assert [r["date"] for r in nav_rows] == [r["date"] for r in bs_rows]
    # 各源内统计自洽：高低点与自身收盘价极值一致、日期范围正确
    for stats, rows in ((nav_stats, nav_rows), (bs_stats, bs_rows)):
        vals = [r.get("nav", r.get("close")) for r in rows]
        assert stats["status"] == "available"
        assert stats["annual_high"]["close"] == max(vals)
        assert stats["annual_low"]["close"] == min(vals)
        assert stats["date_range"] == f"{dates[0]} ~ {dates[-1]}"
        assert all(m["date"] in dates for m in stats["big_move_days"])
        assert all(abs(m["change_pct"]) >= 5.0 for m in stats["big_move_days"])


# ---------------------------------------------------------------------------
# 冻结 fixture 实测口径验证（R11a 单步冻结：142 交易日 / 16 个 ±5% 交易日）
# ---------------------------------------------------------------------------

def test_frozen_588000_fixture_stats_match_measured():
    if not _FIXTURE.exists():
        pytest.skip("冻结 fixture 缺失（baostock 不可用时未生成）")
    data = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    assert data["symbol"] == "588000"
    rows = data["rows"]
    assert len(rows) == 142  # 2026-01-05 ~ 2026-08-05 实测交易日数
    assert rows[0]["date"] == "2026-01-05"
    assert rows[-1]["date"] == "2026-08-05"
    stats = compute_history_stats(rows)
    assert stats["status"] == "available"
    assert len(stats["big_move_days"]) == 16  # 2026 年实测 16 个 |change_pct|>=5% 交易日
    assert all(abs(m["change_pct"]) >= 5.0 for m in stats["big_move_days"])


# ---------------------------------------------------------------------------
# R11b：大波动日识别 / 事件-价格对齐 / 事件文件校验
# ---------------------------------------------------------------------------

def test_detect_big_move_days_frozen_588000():
    """冻结 fixture 验证：detect_big_move_days 与 compute_history_stats 同口径
    （588000 2026 年实测 16 个 |change_pct|>=5% 交易日）。"""
    if not _FIXTURE.exists():
        pytest.skip("冻结 fixture 缺失（baostock 不可用时未生成）")
    rows = json.loads(_FIXTURE.read_text(encoding="utf-8"))["rows"]
    move_days = detect_big_move_days(rows)
    assert len(move_days) == 16
    assert all(abs(m["change_pct"]) >= 5.0 for m in move_days)
    # 与 compute_history_stats 口径一致（日期集相同）
    stats_days = {m["date"] for m in compute_history_stats(rows)["big_move_days"]}
    assert {m["date"] for m in move_days} == stats_days


def test_detect_big_move_days_threshold_and_keys():
    rows = [
        {"date": "2026-01-05", "close": 100.0},
        {"date": "2026-01-06", "close": 95.0},   # -5.00%
        {"date": "2026-01-07", "close": 97.0},   # +2.11%
        {"date": "2026-01-08", "close": 106.0},  # +9.28%
    ]
    moves = detect_big_move_days(rows)
    assert [m["date"] for m in moves] == ["2026-01-06", "2026-01-08"]
    assert moves[0]["change_pct"] == pytest.approx(-5.0)
    # threshold 调整（01-07 为 +2.11%，abs < 3 → 不入选）
    moves2 = detect_big_move_days(rows, threshold_pct=3.0)
    assert [m["date"] for m in moves2] == ["2026-01-06", "2026-01-08"]
    # nav 键兼容
    nav_rows = [{"date": r["date"], "nav": r["close"]} for r in rows]
    assert detect_big_move_days(nav_rows) == moves


def test_align_events_with_price_same_day_and_nearby():
    # 2026-05-11 为周一；05-12 周二 / 05-13 周三 / 05-14 周四 / 05-15 周五
    move_days = [
        {"date": "2026-05-08", "change_pct": -5.12},
        {"date": "2026-05-11", "change_pct": 6.80},
        {"date": "2026-05-15", "change_pct": -7.30},
    ]
    events = [
        {"date": "2026-05-11", "event": "指数创历史新高", "source_url": "https://a",
         "published_date": "2026-05-11", "confidence": "一手"},   # 同日
        {"date": "2026-05-12", "event": "半导体设备景气报道", "source_url": "https://b",
         "published_date": "2026-05-12", "confidence": "二手"},   # 前一交易日 = 05-11 对齐（邻近）
        {"date": "2026-05-14", "event": "权重股中报预告", "source_url": "https://c",
         "published_date": "2026-05-14", "confidence": "二手"},   # 后一交易日 = 05-15 对齐（邻近）
        {"date": "2026-06-01", "event": "无大波动邻近", "source_url": "https://d",
         "published_date": "2026-06-01", "confidence": "一手"},   # 未对齐
    ]
    out = align_events_with_price(move_days, events)
    assert len(out) == 4
    by_date = {r["date"]: r for r in out}

    r1 = by_date["2026-05-11"]
    assert r1["aligned"] is True
    assert r1["同日事实"] == ["2026-05-11 单日 +6.80%"]
    assert "高可信说明候选" in r1["可能关联（待验证）"]  # confidence=一手

    r2 = by_date["2026-05-12"]
    assert r2["aligned"] is True  # 邻近：前一交易日 05-11 有大波动
    assert r2["同日事实"] == []   # 非同日 → 同日事实为空（纯时间线列不受污染）
    assert r2["可能关联（待验证）"].startswith("待验证")  # confidence=二手
    assert "邻近" in r2["可能关联（待验证）"]

    r3 = by_date["2026-05-14"]
    assert r3["aligned"] is True  # 邻近：后一交易日 05-15 有大波动
    assert r3["同日事实"] == []
    assert r3["可能关联（待验证）"].startswith("待验证")  # confidence=二手

    r4 = by_date["2026-06-01"]
    assert r4["aligned"] is False
    assert r4["同日事实"] == []
    assert r4["可能关联（待验证）"] is None


def test_validate_events_file_ok_and_rejections(tmp_path):
    ok_file = tmp_path / "events_ok.json"
    ok_file.write_text(
        '{"date": "2026-05-11", "event": "A", "source_url": "https://a", '
        '"published_date": "2026-05-11", "confidence": "一手"}\n'
        '{"date": "2026-05-12", "event": "B", "source_url": "https://b", '
        '"published_date": "2026-05-12T10:00:00", "confidence": "二手"}\n',
        encoding="utf-8",
    )
    ok, msg = validate_events_file(ok_file)
    assert ok is True
    assert "2 条事件" in msg
    events, _ = load_events_file(ok_file)
    assert len(events) == 2
    assert events[1]["confidence"] == "二手"

    # 缺 source_url → 整文件拒绝并报行号
    bad1 = tmp_path / "events_missing_url.json"
    bad1.write_text(
        '{"date": "2026-05-11", "event": "A", "source_url": "https://a", '
        '"published_date": "2026-05-11", "confidence": "一手"}\n'
        '{"date": "2026-05-12", "event": "B", "published_date": "2026-05-12", '
        '"confidence": "二手"}\n',
        encoding="utf-8",
    )
    ok, msg = validate_events_file(bad1)
    assert ok is False
    assert "第 2 行" in msg and "source_url" in msg

    # 非 ISO 日期 → 整文件拒绝并报行号
    bad2 = tmp_path / "events_bad_date.json"
    bad2.write_text(
        '{"date": "2026/05/11", "event": "A", "source_url": "https://a", '
        '"published_date": "2026-05-11", "confidence": "一手"}\n',
        encoding="utf-8",
    )
    ok, msg = validate_events_file(bad2)
    assert ok is False
    assert "第 1 行" in msg and "date 非 ISO" in msg

    # confidence 非法 → 整文件拒绝并报行号
    bad3 = tmp_path / "events_bad_conf.json"
    bad3.write_text(
        '{"date": "2026-05-11", "event": "A", "source_url": "https://a", '
        '"published_date": "2026-05-11", "confidence": "第三方"}\n',
        encoding="utf-8",
    )
    ok, msg = validate_events_file(bad3)
    assert ok is False
    assert "第 1 行" in msg and "confidence 非法" in msg

    # JSON 解析失败 → 行号报告；文件不存在/为空 → 拒绝
    bad4 = tmp_path / "events_bad_json.json"
    bad4.write_text('{"date": "2026-05-11", broken\n', encoding="utf-8")
    ok, msg = validate_events_file(bad4)
    assert ok is False and "第 1 行 JSON 解析失败" in msg
    ok, msg = validate_events_file(tmp_path / "missing.json")
    assert ok is False and "不存在" in msg
    empty = tmp_path / "events_empty.json"
    empty.write_text("", encoding="utf-8")
    ok, msg = validate_events_file(empty)
    assert ok is False and "为空" in msg
