"""Unit tests for ETF peers flow/RS (v0.2.5 R13, no network).

D13: mocks 一律打在定义模块（etf_peers）命名空间。
"""

from __future__ import annotations

import copy

import pytest

from etf_peers import (
    _flow_row,
    etf_peer_rs,
    query_etf_peers,
    resolve_peer_symbols,
)


# ---------------------------------------------------------------------------
# resolve_peer_symbols
# ---------------------------------------------------------------------------


def test_resolve_explicit_peers_priority():
    out = resolve_peer_symbols("159206", ["512660", "512760"])
    assert out["peers"] == ["512660", "512760"]
    assert out["peer_source"] == "explicit"


def test_resolve_explicit_invalid_codes():
    out = resolve_peer_symbols("159206", ["51266", "abc123"])
    assert out["peers"] == []
    assert "非法代码" in out["note"]


def test_resolve_auto_discover_same_sw():
    out = resolve_peer_symbols("159206", None)
    assert out["peer_source"] == "etf_to_sw_industry:801740 国防军工"
    assert "512660" in out["peers"]
    assert "512710" in out["peers"]
    assert "159206" not in out["peers"]


def test_resolve_unmapped_hints_explicit(monkeypatch):
    monkeypatch.setattr("etf_peers.ETF_TO_SW_INDUSTRY", {})
    out = resolve_peer_symbols("999999", None)
    assert out["peers"] == []
    assert "--peers" in out["note"]


# ---------------------------------------------------------------------------
# _flow_row（share_change_pct 派生）
# ---------------------------------------------------------------------------


def _share_history(rows, summary) -> dict:
    return {"symbol": "159206", "available": True, "rows": rows, "summary": summary}


def test_flow_row_derives_share_change_pct():
    sh = _share_history(
        rows=[
            {"date": "20260714", "shares": 10000.0, "flow_est": 0.5},
            {"date": "20260715", "shares": None, "flow_est": None},
            {"date": "20260810", "shares": 10300.0, "flow_est": 0.3},
        ],
        summary={
            "total_flow_est": 2.35,
            "recent_flow_est": 1.20,
            "trend": "🟢 持续净流入",
            "row_count": 18,
        },
    )
    row = _flow_row(sh)
    assert row["flow_20d_e"] == 2.35
    assert row["flow_5d_e"] == 1.20
    assert row["share_change_pct"] == pytest.approx(3.0)  # (10300-10000)/10000
    assert row["share_change_span"] == 1  # 2 个有效份额行 → 1 个间隔
    assert row["row_count"] == 18


def test_flow_row_skips_missing_shares():
    sh = _share_history(
        rows=[{"date": "20260714", "shares": None}],
        summary={"total_flow_est": None},
    )
    row = _flow_row(sh)
    assert row["share_change_pct"] is None
    assert row["flow_20d_e"] is None


# ---------------------------------------------------------------------------
# etf_peer_rs
# ---------------------------------------------------------------------------


def test_rs_main_up_peer_flat():
    """主涨 10%、同行持平 → rs_latest≈110；三数字自洽（latest = window_start + change）。"""
    n = 30
    main = [1.0 + 0.1 * i / (n - 1) for i in range(n)]  # +10% 线性
    bench = [1.0] * n
    dates = [f"202608{d:02d}" for d in range(1, n + 1)]
    out = etf_peer_rs(main, bench, dates, window=20)
    assert out["rs_latest"] == pytest.approx(110.0, abs=0.01)
    # 窗口起点（第 11 天，main 已涨 ~3.45%）→ rs_window_start ≈ 103.45
    assert out["rs_window_start"] == pytest.approx(103.45, abs=0.05)
    assert out["rs_change"] == pytest.approx(6.55, abs=0.05)
    # 自洽性：latest = window_start + change
    assert out["rs_latest"] == pytest.approx(
        out["rs_window_start"] + out["rs_change"], abs=0.01
    )
    assert out["rs_change_pct"] == pytest.approx(6.33, abs=0.05)
    assert len(out["rs_series"]) == 20
    assert out["n"] == 30


def test_rs_insufficient_alignment():
    out = etf_peer_rs([1.0] * 10, [1.0] * 10, ["20260801"] * 10, window=20)
    assert "error" in out
    assert "对齐交易日不足" in out["error"]


def test_rs_zero_benchmark():
    out = etf_peer_rs([1.0, 1.1, 1.2], [1.0, 0.0, 1.1], ["a", "b", "c"], window=2)
    assert "error" in out


# ---------------------------------------------------------------------------
# query_etf_peers 编排（D13: mock 打在 etf_peers 命名空间）
# ---------------------------------------------------------------------------


def _mk_share(code, flow_20d, note=None):
    return {
        "symbol": code,
        "available": note is None,
        "rows": [{"date": "20260801", "shares": 10000.0},
                 {"date": "20260810", "shares": 10050.0}],
        "summary": {"total_flow_est": flow_20d, "recent_flow_est": 0.5,
                    "trend": "→ 资金面平稳", "row_count": 18},
        "note": note,
    }


def _mk_kline(code, closes):
    return {
        "status": "available",
        "nav_history": [{"date": f"202608{d:02d}", "nav": v}
                        for d, v in enumerate(closes, start=1)],
    }


def _patch_env(monkeypatch, *, share_map, kline_map, sw_map=None, names=None):
    if sw_map is not None:
        monkeypatch.setattr("etf_peers.ETF_TO_SW_INDUSTRY", sw_map)
    monkeypatch.setattr(
        "etf_peers.query_etf_share_history",
        lambda code, days=20: share_map.get(code, _mk_share(code, None)),
    )
    monkeypatch.setattr(
        "etf_peers.query_etf_kline",
        lambda code, days=60: kline_map.get(code, {"status": "available", "nav_history": []}),
    )
    monkeypatch.setattr("etf_peers._lookup_etf_spot_row", lambda s: (None, "no spot"))
    monkeypatch.setattr("etf_peers._peer_name", lambda s: names.get(s) if names else None)


_SW = {
    "159206": {"sw_code": "801740", "sw_name": "国防军工", "sub": "卫星"},
    "512660": {"sw_code": "801740", "sw_name": "国防军工", "sub": "军工"},
    "512710": {"sw_code": "801740", "sw_name": "国防军工", "sub": "军工龙头"},
}


def test_query_peers_flow_and_rs(monkeypatch):
    closes_main = [1.0 + 0.001 * i for i in range(30)]
    closes_peer = [1.0] * 30
    _patch_env(
        monkeypatch,
        share_map={"159206": _mk_share("159206", -31.45),
                   "512660": _mk_share("512660", 4.02)},
        kline_map={"159206": _mk_kline("159206", closes_main),
                   "512660": _mk_kline("512660", closes_peer)},
        sw_map=_SW,
    )
    out = query_etf_peers("159206")
    assert out["available"] is True
    assert out["peers"] == ["512660", "512710"]
    assert out["rs"] is not None
    assert out["rs"]["rank_20d"]["rank"] == 1  # 主标的涨幅最大
    flows = {r["symbol"]: r["flow_20d_e"] for r in out["flow"]["rows"]}
    assert flows["159206"] == -31.45
    assert flows["512660"] == 4.02
    assert len(out["flow"]["rows"]) == 3  # 主 + 2 peers


def test_query_peers_single_failure_does_not_block(monkeypatch):
    share_map = {
        "159206": _mk_share("159206", 1.0),
        "512660": _mk_share("512660", None, note="需 ≥2000 Tushare 积分"),
        "512710": _mk_share("512710", 2.0),
    }
    kline_map = {c: _mk_kline(c, [1.0] * 30) for c in ("159206", "512660", "512710")}
    _patch_env(monkeypatch, share_map=share_map, kline_map=kline_map, sw_map=_SW)
    out = query_etf_peers("159206")
    assert out["available"] is True
    rows = {r["symbol"]: r for r in out["flow"]["rows"]}
    assert rows["512660"]["note"] == "需 ≥2000 Tushare 积分"
    assert rows["159206"]["flow_20d_e"] == 1.0  # 其余行照常


def test_query_peers_unmapped(monkeypatch):
    monkeypatch.setattr("etf_peers.ETF_TO_SW_INDUSTRY", {})
    out = query_etf_peers("999999")
    assert out["available"] is False
    assert "--peers" in out["note"]
    assert out["flow"] is None
    assert out["rs"] is None


def test_query_peers_rs_skipped_when_alignment_short(monkeypatch):
    share_map = {"159206": _mk_share("159206", 1.0), "512660": _mk_share("512660", 1.0)}
    # 只有 5 个共同交易日 → RS 跳过，flow 照常
    kline_map = {
        "159206": _mk_kline("159206", [1.0, 1.01, 1.02, 1.03, 1.04]),
        "512660": _mk_kline("512660", [1.0, 1.0, 1.0, 1.0, 1.0]),
    }
    _patch_env(monkeypatch, share_map=share_map, kline_map=kline_map, sw_map=_SW)
    out = query_etf_peers("159206")
    assert out["available"] is True
    assert out["rs"] is None
    assert any("RS 不可计算" in n for n in out["notes"])
    assert len(out["flow"]["rows"]) == 3
