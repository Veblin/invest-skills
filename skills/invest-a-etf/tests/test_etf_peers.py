"""Unit tests for ETF peers flow/RS (v0.2.5 R13, no network).

D13: mocks 一律打在定义模块（etf_peers）命名空间。
"""

from __future__ import annotations


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
# _flow_row（A3：直接引用引擎同窗字段，不再从 rows 自算）
# ---------------------------------------------------------------------------


def _share_history(rows, summary) -> dict:
    return {"symbol": "159206", "available": True, "rows": rows, "summary": summary}


def test_flow_row_consumes_engine_same_window_fields():
    """share_change_pct/span 直接来自引擎 summary（与 flow 合计共同起止），
    不从 rows 首尾自算（旧实现起点与 flow 不一致：rows 已丢 r0）。"""
    fw = {"start": "20260711", "end": "20260810", "segments": 20,
          "trading_days": 21, "missing_share_days": 0, "note": "同窗"}
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
            "window_days": 20,
            "flow_window": fw,
            "share_change_pct": 3.0,      # 引擎同窗值（含首日段）
            "share_total_change": 300.0,
        },
    )
    row = _flow_row(sh)
    assert row["flow_20d_e"] == 2.35
    assert row["flow_total_e"] == 2.35
    assert row["flow_5d_e"] == 1.20
    assert row["share_change_pct"] == 3.0            # 引用引擎值，不自算
    assert row["share_change_span"] == 20            # = flow_window.segments
    assert row["flow_window"] == fw
    assert row["window_days"] == 20
    assert row["row_count"] == 18


def test_flow_row_non_20d_window_has_no_fake_20d_alias():
    sh = _share_history(rows=[], summary={
        "total_flow_est": 9.9, "window_days": 60,
        "flow_window": {"start": "a", "end": "b", "segments": 60,
                        "trading_days": 61, "missing_share_days": 0},
        "share_change_pct": 1.0,
    })
    row = _flow_row(sh)
    assert row["flow_total_e"] == 9.9
    assert row["flow_20d_e"] is None                 # 60 日窗口不得冒充 20 日
    assert row["window_days"] == 60


def test_flow_row_skips_missing_shares():
    sh = _share_history(
        rows=[{"date": "20260714", "shares": None}],
        summary={"total_flow_est": None},
    )
    row = _flow_row(sh)
    assert row["share_change_pct"] is None
    assert row["flow_20d_e"] is None
    assert row["flow_total_e"] is None


# ---------------------------------------------------------------------------
# etf_peer_rs
# ---------------------------------------------------------------------------


def test_rs_main_up_peer_flat():
    """主涨 10%、同行持平 → rs_latest≈110；三数字自洽（latest = window_start + change）。

    R8：窗口 = 末 20 个**间隔**（21 点）——窗口起点为第 10 天（0-based 9），
    main 已涨 0.1×9/29 ≈ 3.10% → rs_window_start ≈ 103.10。
    """
    n = 30
    main = [1.0 + 0.1 * i / (n - 1) for i in range(n)]  # +10% 线性
    bench = [1.0] * n
    dates = [f"202608{d:02d}" for d in range(1, n + 1)]
    out = etf_peer_rs(main, bench, dates, window=20)
    assert out["rs_latest"] == pytest.approx(110.0, abs=0.01)
    assert out["rs_window_start"] == pytest.approx(103.10, abs=0.05)
    assert out["rs_change"] == pytest.approx(6.90, abs=0.05)
    # 自洽性：latest = window_start + change
    assert out["rs_latest"] == pytest.approx(
        out["rs_window_start"] + out["rs_change"], abs=0.01
    )
    assert out["rs_change_pct"] == pytest.approx(6.69, abs=0.05)
    assert len(out["rs_series"]) == 21        # 20 个间隔 = 21 个点
    assert out["window_points"] == 21
    assert out["n"] == 30


def test_query_peers_benchmark_normalized_equal_weight(monkeypatch):
    """R8：等权基准先归一化再平均——直接平均不同 NAV 水平会按 NAV 加权。

    main 持平；peer A（NAV 10 起）线性 +10%；peer B（NAV 1 起）持平。
    归一化等权：mean(1.10, 1.00)=1.05 → rs_latest ≈ 100/1.05 ≈ 95.24；
    未归一化平均会得 mean(11,1)=6.0 / 基期 5.5 → 91.67（错误口径）。
    """
    n = 30
    a_vals = [10.0 + 1.0 * i / (n - 1) for i in range(n)]       # NAV 10 → 11（+10%）
    kline_main = {"status": "available",
                  "nav_history": [{"date": f"202608{d:02d}", "nav": 1.0}
                                  for d in range(1, n + 1)]}
    kline_a = {"status": "available",
               "nav_history": [{"date": f"202608{d:02d}", "nav": v}
                               for v, d in zip(a_vals, range(1, n + 1))]}
    kline_b = {"status": "available",
               "nav_history": [{"date": f"202608{d:02d}", "nav": 1.0}
                               for d in range(1, n + 1)]}
    _patch_env(
        monkeypatch,
        share_map={},
        kline_map={"159206": kline_main, "512660": kline_a, "512710": kline_b},
    )
    out = query_etf_peers("159206", ["512660", "512710"])
    assert out["rs"] is not None
    # 独立复算（窗口=末 21 点，基期为窗口首点）：bench=mean(A_i/A_base, 1.0)
    base = a_vals[9]
    bench_last = ((a_vals[-1] / base) + 1.0) / 2
    assert out["rs"]["rs_latest"] == pytest.approx(round(100 / bench_last, 2), abs=0.02)
    # 若按未归一化的原始 NAV 平均（错误口径）会给出不同值 → 必须被区分
    wrong = 100 * (((a_vals[9] + 1.0) / 2) / ((a_vals[-1] + 1.0) / 2))
    assert out["rs"]["rs_latest"] != pytest.approx(round(wrong, 2), abs=0.05)
    assert out["rs"]["benchmark"]["method"].startswith("非主标的成员**先按共同基期")
    ev = out["rs"]["input_evidence"]
    assert ev["benchmark_values"][0] == pytest.approx(1.0)      # 基期归一化
    assert ev["benchmark_values"][-1] == pytest.approx(bench_last)
    assert len(ev["dates"]) == 21
    assert len(ev["main_normalized"]) == 21


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
                    "trend": "→ 资金面平稳", "row_count": 18,
                    "window_days": 20,
                    "flow_window": {"start": "20260713", "end": "20260810",
                                    "segments": 20, "trading_days": 21,
                                    "missing_share_days": 0},
                    "share_change_pct": 0.5, "share_total_change": 50.0},
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


@pytest.mark.parametrize("start,end,confirmable,expected", [
    ("20260713", "20260810", True, True),
    ("20260713", "20260809", True, False),
    ("20260714", "20260810", True, False),
    ("20260811", "20260812", True, False),
    ("20260713", "20260810", False, False),
])
def test_peer_comparison_requires_identical_confirmable_windows(
        monkeypatch, start, end, confirmable, expected):
    main = _mk_share("159206", 10.0)
    peer = _mk_share("512660", 2.0)
    main["summary"]["flow_window"]["confirmable"] = True
    peer["summary"]["flow_window"].update(
        start=start, end=end, confirmable=confirmable)
    _patch_env(monkeypatch, share_map={"159206": main, "512660": peer}, kline_map={})
    flow = query_etf_peers("159206", ["512660"])["flow"]
    assert flow["common_window"]["confirmable"] is expected
    assert bool(flow["common_window_note"]) is (not expected)
    assert [r["flow_total_e"] for r in flow["rows"]] == [10.0, 2.0]
    same = start == "20260713" and end == "20260810"
    assert all(r["same_window_as_group"] is same for r in flow["rows"])


def test_peer_comparison_blocks_missing_member_value(monkeypatch):
    main = _mk_share("159206", 10.0)
    peer = _mk_share("512660", 2.0)
    for sh in (main, peer):
        sh["summary"]["flow_window"]["confirmable"] = True
    peer["summary"]["share_change_pct"] = None
    _patch_env(monkeypatch, share_map={"159206": main, "512660": peer}, kline_map={})
    flow = query_etf_peers("159206", ["512660"])["flow"]
    assert flow["common_window"]["all_products_same_window"] is True
    assert flow["common_window"]["confirmable"] is False
    assert "份额变化不可得" in flow["common_window_note"]
