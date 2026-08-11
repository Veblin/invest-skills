"""Unit tests for sector flow fetch/save/decompose/query (v0.2.5 R15, no network).

D13: mocks 一律打在定义模块（sector_flow）命名空间；SQLite 走 isolated_store fixture。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterator

import pandas as pd
import pytest

import sector_flow as sf


@pytest.fixture
def isolated_store(tmp_path: Path) -> Iterator[Any]:
    """临时 SQLite 隔离（仿 test_index_pe_snapshot.isolated_store）。"""
    from lib import store as store_mod

    previous = store_mod._db_override
    store_mod._db_override = tmp_path / "test_research.db"
    try:
        store_mod.init_db()
        yield store_mod
    finally:
        store_mod._db_override = previous


# ---------------------------------------------------------------------------
# fetch 信封
# ---------------------------------------------------------------------------


def _mk_df(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)


def _patch_ak(monkeypatch, *, windows: dict[int, pd.DataFrame]):
    """mock sector_flow 命名空间的 akshare 调用（按 _WINDOW_MAP symbol 分发）。"""
    import akshare as ak

    sym_map = {"即时": 1, "3日排行": 3, "5日排行": 5, "10日排行": 10}

    def fake(symbol: str):
        wd = sym_map[symbol]
        df = windows.get(wd)
        if df is None:
            raise RuntimeError(f"{symbol} boom")
        return df

    import dates
    from lib import proxy as proxy_mod

    # fetch 内为函数内 import：mock 须打在目标定义模块（D13）
    monkeypatch.setattr(dates, "shanghai_today", lambda: "20260811")
    monkeypatch.setattr(ak, "stock_fund_flow_industry", fake)
    monkeypatch.setattr(proxy_mod, "akshare_direct_session", _noop_cm)


def _noop_cm():
    import contextlib

    return contextlib.nullcontext()


def _ind_row(name: str, wd_vals: dict[int, dict]) -> pd.DataFrame:
    rows = []
    for wd, v in wd_vals.items():
        if wd == 1:
            rows.append({
                "行业": name, "行业-涨跌幅": v.get("chg", 1.0), "流入资金": v.get("in", 10.0),
                "流出资金": v.get("out", 9.0), "净额": v.get("net", 1.0),
                "领涨股": v.get("leader", "龙头A"), "领涨股-涨跌幅": v.get("leader_chg", 3.2),
            })
        else:
            rows.append({
                "行业": name, "阶段涨跌幅": f"{v.get('chg', 1.0)}%",
                "流入资金": v.get("in", 10.0), "流出资金": v.get("out", 9.0),
                "净额": v.get("net", 1.0),
            })
    return pd.DataFrame(rows)


def test_fetch_envelope_four_windows(monkeypatch):
    _patch_ak(monkeypatch, windows={
        1: _ind_row("半导体", {1: {"net": 9.5, "leader": "超纯应材", "leader_chg": 5.0}}),
        3: _ind_row("半导体", {3: {"net": -18.2, "chg": 2.5}}),
        5: _ind_row("半导体", {5: {"net": 219.35, "chg": 16.96}}),
        10: _ind_row("半导体", {10: {"net": -249.76, "chg": 26.03}}),
    })
    s = sf.fetch_sector_flow_snapshot()
    assert s["available"] is True
    assert s["date"] == "20260811"
    assert s["errors"] == []
    d = s["industries"]["半导体"]
    assert d[1]["net"] == 9.5
    assert d[1]["leader"] == "超纯应材"
    assert d[5]["net"] == 219.35
    assert d[10]["chg"] == pytest.approx(26.03)  # "%" 已 rstrip
    # leader 仅即时窗口
    assert d[3].get("leader") is None


def test_fetch_field_drift_coalesce(monkeypatch):
    # 缺「净额」列只有「主力净流入」（历史列名变体）
    df = pd.DataFrame([{
        "行业": "银行", "阶段涨跌幅": "1.2%", "流入资金": 5.0, "流出资金": 6.0,
        "主力净流入": -1.0,
    }])
    _patch_ak(monkeypatch, windows={5: df})
    s = sf.fetch_sector_flow_snapshot()
    assert s["industries"]["银行"][5]["net"] == pytest.approx(-1.0)


def test_fetch_partial_window_failure(monkeypatch):
    _patch_ak(monkeypatch, windows={
        3: _ind_row("半导体", {3: {"net": -18.2}}),
        5: _ind_row("半导体", {5: {"net": 219.35}}),
        # 10 日窗口缺失 → 抛异常
    })
    s = sf.fetch_sector_flow_snapshot()
    assert s["available"] is True
    assert "半导体" in s["industries"]
    assert any("10日排行" in e for e in s["errors"])


# ---------------------------------------------------------------------------
# save / load（isolated_store）
# ---------------------------------------------------------------------------


def _snapshot(d3: float = -18.2, d5: float = 219.35, d10: float = -249.76) -> dict:
    return {
        "date": "20260811",
        "available": True,
        "industries": {
            "半导体": {
                1: {"net": 9.5, "chg": 0.05, "leader": "超纯应材"},
                3: {"net": d3, "chg": 2.5},
                5: {"net": d5, "chg": 16.96},
                10: {"net": d10, "chg": 26.03},
            },
            "军工电子": {
                1: {"net": -2.9, "chg": -1.27, "leader": "龙头X"},
                3: {"net": -3.31, "chg": 1.0},
                5: {"net": 6.26, "chg": 2.0},
                10: {"net": -20.86, "chg": 8.78},
            },
        },
        "errors": [],
    }


def test_save_load_roundtrip_idempotent(isolated_store):
    r1 = sf.save_sector_flow_snapshot(_snapshot(), date="20260811")
    assert r1["rows_saved"] == 8
    assert r1["skipped"] is False
    # 同日同数据重跑 → C5 全等检测跳过（幂等保护，非交易日同款路径）
    r2 = sf.save_sector_flow_snapshot(_snapshot(), date="20260811")
    assert r2["skipped"] is True
    assert r2["rows_saved"] == 0
    hist = sf.load_sector_flow_history("半导体", 3, 10)
    assert len(hist) == 1
    assert hist[0]["net"] == pytest.approx(-18.2)


def test_save_skips_identical_snapshot(isolated_store):
    sf.save_sector_flow_snapshot(_snapshot(), date="20260810")
    r = sf.save_sector_flow_snapshot(_snapshot(), date="20260811")
    assert r["skipped"] is True  # C5 全等检测
    assert r["rows_saved"] == 0
    # 差异快照 → 正常写入
    r2 = sf.save_sector_flow_snapshot(_snapshot(d3=-99.0), date="20260811")
    assert r2["skipped"] is False
    assert r2["rows_saved"] == 8


def test_load_no_table_returns_empty(isolated_store):
    assert sf.load_sector_flow_history("半导体", 3, 10) == []


# ---------------------------------------------------------------------------
# decompose 四象限
# ---------------------------------------------------------------------------


def test_decompose_four_quadrants():
    # (+,+) 持续净流入 + 近端加速（r = (30/3)/(70/7) = 10/10 = 1.0 → 节奏平稳；
    # 用 r≥1.2 场景：d3=36, mid=70 → r=(36/3)/(70/7)=12/10=1.2）
    out = sf.decompose_flow(36.0, None, 106.0)
    assert out["label"] == "持续净流入"
    assert "近端加速" in out["label_detail"]
    # (+,-) 近端回流
    out = sf.decompose_flow(15.0, None, -5.0)
    assert out["label"] == "近端回流"
    assert "中段净流出 20.00 亿" in out["label_detail"]
    # (-,+) 近端退潮
    out = sf.decompose_flow(-15.0, None, 5.0)
    assert out["label"] == "近端退潮"
    assert "中段净流入 20.00 亿" in out["label_detail"]
    # (-,-) 持续净流出
    out = sf.decompose_flow(-36.0, None, -106.0)
    assert out["label"] == "持续净流出"


def test_decompose_boundary_and_missing():
    out = sf.decompose_flow(0.0, None, 10.0)
    assert out["label"] == "近端退潮"  # 0 归负侧
    assert "零值边界" in out["label_detail"]
    out = sf.decompose_flow(None, None, 10.0)
    assert out["label"] == "数据不足"
    out = sf.decompose_flow(1.0, None, None)
    assert out["label"] == "数据不足"


# ---------------------------------------------------------------------------
# query 编排
# ---------------------------------------------------------------------------


def _patch_query_env(monkeypatch, sw_map: dict):
    # query_sector_flow 内为函数内 import → mock 打在定义模块 etf_data（D13）
    import etf_data

    monkeypatch.setattr(etf_data, "ETF_TO_SW_INDUSTRY", sw_map)


def test_query_159206_mapping(isolated_store, monkeypatch):
    sf.save_sector_flow_snapshot(_snapshot(), date="20260811")
    _patch_query_env(monkeypatch, {"159206": {"sw_code": "801740", "sw_name": "国防军工", "sub": "卫星"}})
    out = sf.query_sector_flow("159206")
    assert out["available"] is True
    assert [r["industry"] for r in out["industries"]] == ["军工电子", "军工装备"]
    row = out["industries"][0]
    assert row["net_10d"] == pytest.approx(-20.86)
    assert row["trend_label"] == "持续净流出"


def test_query_515230_mapping(isolated_store, monkeypatch):
    sf.save_sector_flow_snapshot(_snapshot(), date="20260811")
    _patch_query_env(monkeypatch, {"515230": {"sw_code": "801750", "sw_name": "计算机", "sub": "软件"}})
    out = sf.query_sector_flow("515230")
    assert [r["industry"] for r in out["industries"]] == ["计算机设备", "软件开发", "IT服务"]


def test_query_unmapped(isolated_store, monkeypatch):
    _patch_query_env(monkeypatch, {})
    out = sf.query_sector_flow("999999")
    assert out["available"] is False
    assert "未映射" in "; ".join(out["notes"])


def test_query_insufficient_history(isolated_store, monkeypatch):
    sf.save_sector_flow_snapshot(_snapshot(), date="20260811")
    _patch_query_env(monkeypatch, {"159206": {"sw_code": "801740", "sw_name": "国防军工", "sub": "卫星"}})
    out = sf.query_sector_flow("159206")
    assert out["history_days"] == 1
    assert out["industries"][0]["trend_5d"] is None
    assert any("积累中" in n for n in out["notes"])
    assert out["industries"][0]["trend_label"] is not None  # 单点分解不依赖积累


def test_query_sequence_trend_turn(isolated_store, monkeypatch):
    # 种 7 日序列：d3 由负转正 → turn_5d = 转向流入
    dates = [f"2026080{i}" for i in range(1, 8)]
    vals = [-50.0, -40.0, -30.0, -20.0, -10.0, 5.0, 20.0]
    for d, v in zip(dates, vals):
        snap = {
            "date": d, "available": True,
            "industries": {"军工电子": {
                1: {"net": v, "chg": 1.0}, 3: {"net": v, "chg": 1.0},
                5: {"net": v, "chg": 1.0}, 10: {"net": v, "chg": 1.0},
            }},
            "errors": [],
        }
        sf.save_sector_flow_snapshot(snap, date=d)
    _patch_query_env(monkeypatch, {"159206": {"sw_code": "801740", "sw_name": "国防军工", "sub": "卫星"}})
    out = sf.query_sector_flow("159206")
    row = out["industries"][0]
    assert row["trend_5d"] == pytest.approx(60.0)  # 20 - (-40)，5 日变化（第 6 旧为基线）
    assert row["turn_5d"] == "转向流入"
    assert out["history_days"] == 7


def test_mapping_coverage_check():
    missing = sf.check_mapping_coverage({
        "industries": {"半导体": {}, "军工电子": {}, "军工装备": {}, "银行": {}},
    })
    assert "半导体" not in missing
    assert "软件开发" in missing
    assert "银行" not in missing


# ---------------------------------------------------------------------------
# 回归（code-review C5/边界/映射/积累门控）
# ---------------------------------------------------------------------------


def _snapshot_without_windows(snap: dict, wd_remove: set[int]) -> dict:
    """剔除指定窗口后的快照（模拟窗口取数失败）。"""
    out = {k: v for k, v in snap.items()}
    out["industries"] = {
        ind: {wd: v for wd, v in windows.items() if wd not in wd_remove}
        for ind, windows in snap["industries"].items()
    }
    return out


def test_save_industry_set_change_not_skipped(isolated_store):
    """C5 bug A：行业从名单消失 → 名单不等 → 不得整体误判 skipped（防数据丢失）。"""
    sf.save_sector_flow_snapshot(_snapshot(), date="20260810")
    day2 = {"date": "20260811", "available": True,
            "industries": {"半导体": _snapshot()["industries"]["半导体"]},
            "errors": []}
    r = sf.save_sector_flow_snapshot(day2, date="20260811")
    assert r["skipped"] is False
    assert r["rows_saved"] == 4  # 半导体×4 窗口正常写入


def test_save_partial_window_failure_still_skips_nontrading(isolated_store):
    """C5 bug B：非交易日即时窗口取数失败 → 其余可比窗口全等 → 仍跳过（不写重复快照）。"""
    sf.save_sector_flow_snapshot(_snapshot(), date="20260810")
    day2 = _snapshot_without_windows(_snapshot(), wd_remove={1})
    day2["date"] = "20260811"
    r = sf.save_sector_flow_snapshot(day2, date="20260811")
    assert r["skipped"] is True
    assert r["rows_saved"] == 0


def test_save_partial_window_failure_saves_when_changed(isolated_store):
    """部分窗口失败 + 其余窗口有变化 → 正常写入（不得因缺窗口而漏存交易日）。"""
    sf.save_sector_flow_snapshot(_snapshot(), date="20260810")
    day2 = _snapshot_without_windows(_snapshot(d3=-99.0), wd_remove={1})
    day2["date"] = "20260811"
    r = sf.save_sector_flow_snapshot(day2, date="20260811")
    assert r["skipped"] is False
    assert r["rows_saved"] == 6  # 2 行业 × 3 窗口


def test_save_invalid_date_rejected(isolated_store):
    """date 非 YYYYMMDD（带连字符）→ error 信封，不写库；缺省/空串回退快照日期。"""
    r = sf.save_sector_flow_snapshot(_snapshot(), date="2026-08-11")
    assert r["error"] is not None and "date 非法" in r["error"]
    assert r["rows_saved"] == 0
    assert sf.load_sector_flow_history("半导体", 3, 10) == []
    # date=None/"" → 回退快照内日期（合法 YYYYMMDD），正常写入
    r2 = sf.save_sector_flow_snapshot(_snapshot(), date=None)
    assert r2["error"] is None and r2["rows_saved"] == 8


def test_decompose_mid_zero_not_rebound():
    """中段归零 → 方向由近端决定，不误判「近端回流/退潮」（回流语义要求中段有方向）。"""
    out = sf.decompose_flow(5.0, None, 5.0)
    assert out["label"] == "持续净流入"
    assert "中段 7 日净额 ≈ 0.00" in out["label_detail"]
    assert "近端回流" not in out["label_detail"]
    out = sf.decompose_flow(-5.0, None, -5.0)
    assert out["label"] == "持续净流出"


def test_decompose_tiny_flow_no_strength_claim():
    """微量级金额不输出「加速/减速」（避免 0.01 亿级被断言为强度方向）。"""
    out = sf.decompose_flow(0.0001, None, 0.005)
    assert out["label"] == "持续净流入"
    assert "金额量级小" in out["label_detail"]
    assert "加速" not in out["label_detail"] and "减速" not in out["label_detail"]


def test_decompose_none_formatted_not_literal():
    out = sf.decompose_flow(None, None, 10.0)
    assert "—" in out["label_detail"]
    assert "None" not in out["label_detail"]


def test_decompose_no_dead_fields():
    """死字段清理：net_5d/note 不再回传（d5 由消费方直取 latest 行）。"""
    out = sf.decompose_flow(15.0, 99.0, -5.0)
    assert "net_5d" not in out
    assert "note" not in out


def test_query_ths_mapping_missing_returns_false(isolated_store, monkeypatch):
    """sw_code 已映射但 THS 细分缺失 → available=False + 明确提示（非静默空表）。"""
    sf.save_sector_flow_snapshot(_snapshot(), date="20260811")
    _patch_query_env(monkeypatch, {"159206": {"sw_code": "801010", "sw_name": "农林牧渔", "sub": "种植"}})
    out = sf.query_sector_flow("159206")
    assert out["available"] is False
    assert any("THS 行业映射缺失" in n for n in out["notes"])


def test_query_per_industry_insufficient_note(isolated_store, monkeypatch):
    """全局积累达标但单行业积累不足 → 行级「积累中」提示（非静默 None）。"""
    dates = [f"2026080{i}" for i in range(1, 8)]
    vals = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0]  # 逐日递增：规避 C5 非交易日跳过
    for d, v in zip(dates, vals):
        snap = {"date": d, "available": True,
                "industries": {"半导体": {
                    1: {"net": v, "chg": 1.0}, 3: {"net": v, "chg": 1.0},
                    5: {"net": v, "chg": 1.0}, 10: {"net": v, "chg": 1.0},
                }},
                "errors": []}
        sf.save_sector_flow_snapshot(snap, date=d)
    _patch_query_env(monkeypatch, {"159206": {"sw_code": "801740", "sw_name": "国防军工", "sub": "卫星"}})
    out = sf.query_sector_flow("159206")
    assert out["history_days"] == 7
    row = out["industries"][0]  # 军工电子：0 行
    assert row["trend_5d"] is None
    assert any("军工电子」序列积累中" in n for n in out["notes"])
