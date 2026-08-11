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
    assert out["label"] == "近端归零"  # d3≈0：方向只由中段决定，不归负侧
    assert "零值边界" in out["label_detail"]
    assert "中段净流入 10.00 亿" in out["label_detail"]
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
    assert row["trend_span_days"] == 5  # 基线=第 6 旧快照 08-02 → 08-07
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


# ---------------------------------------------------------------------------
# 回归（code-review 修复：NULL 冻结/位漂移/日历/upsert/量级守卫/漂移检测）
# ---------------------------------------------------------------------------


def test_decompose_near_zero_labels():
    """全零净额 → 净额近零（不做「持续净流出」断言）；d3≈0 有中段 → 近端归零。"""
    out = sf.decompose_flow(1e-10, None, 1e-10)
    assert out["label"] == "净额近零"
    assert "≈ 0" in out["label_detail"]
    out = sf.decompose_flow(-1e-10, None, -1e-10)
    assert out["label"] == "净额近零"
    out = sf.decompose_flow(0.0, None, -5.0)
    assert out["label"] == "近端归零"
    assert "中段净流出 5.00 亿" in out["label_detail"]


def test_decompose_rebound_magnitude_guard():
    """回流/退潮分支同样受 _FLOW_EPS 量级守卫（docstring 规则全覆盖）。"""
    out = sf.decompose_flow(0.5, None, -2.0)  # mid=-2.5，日均 0.36 亿 < 1.0
    assert out["label"] == "近端回流"
    assert "金额量级小" in out["label_detail"]
    out = sf.decompose_flow(15.0, None, -5.0)  # mid=-20，日均 2.86 亿 ≥ 1.0
    assert out["label"] == "近端回流"
    assert "金额量级小" not in out["label_detail"]
    out = sf.decompose_flow(-0.5, None, 2.0)  # 近端退潮微量级
    assert out["label"] == "近端退潮"
    assert "金额量级小" in out["label_detail"]


def test_str_col_guards_pd_na_and_nan():
    """pd.NA/NaN/None/'' 不泄漏为字面量；正常值保留。"""
    assert sf._str_col({"x": pd.NA}, "x") is None
    assert sf._str_col({"x": float("nan")}, "x") is None
    assert sf._str_col({"x": None}, "x") is None
    assert sf._str_col({"x": ""}, "x") is None
    assert sf._str_col({"x": "半导体"}, "x") == "半导体"


def test_fetch_nan_industry_skipped(monkeypatch):
    """「行业」单元格 NaN 的行不得以字面量 "nan" 行业入库（实测回归）。"""
    import numpy as np

    df = pd.DataFrame([
        {"行业": np.nan, "阶段涨跌幅": "1.0%", "净额": 1.0},
        {"行业": "半导体", "阶段涨跌幅": "2.0%", "净额": -18.2},
    ])
    _patch_ak(monkeypatch, windows={3: df})
    s = sf.fetch_sector_flow_snapshot()
    assert "nan" not in s["industries"]
    assert "半导体" in s["industries"]


def test_fetch_pdna_leader_guarded(monkeypatch):
    """领涨股列 pd.NA → leader 字段 None 而非 "<NA>"。"""
    df = pd.DataFrame([{
        "行业": "半导体", "行业-涨跌幅": "1.0%", "净额": 1.0,
        "领涨股": pd.NA, "领涨股-涨跌幅": 3.2,
    }])
    _patch_ak(monkeypatch, windows={1: df})
    s = sf.fetch_sector_flow_snapshot()
    assert s["industries"]["半导体"][1]["leader"] is None


def test_save_null_does_not_freeze(isolated_store):
    """窗口净额 NULL → 判定有变化写入（永不因 NULL 全等跳过，防序列冻结）。"""
    sf.save_sector_flow_snapshot(_snapshot(), date="20260810")
    day2 = _snapshot()
    day2["date"] = "20260811"
    day2["industries"]["半导体"][3] = {"net": None, "chg": 2.5}
    r = sf.save_sector_flow_snapshot(day2, date="20260811")
    assert r["skipped"] is False
    assert r["rows_saved"] == 8


def test_save_bit_drift_isclose_skip(isolated_store):
    """浮点位漂移（1e-12）→ 全等跳过；真实差异（0.001）→ 写入。"""
    sf.save_sector_flow_snapshot(_snapshot(), date="20260810")
    day2 = _snapshot()
    day2["date"] = "20260811"
    day2["industries"]["半导体"][3]["net"] = -18.2 + 1e-12
    r = sf.save_sector_flow_snapshot(day2, date="20260811")
    assert r["skipped"] is True
    day3 = _snapshot()
    day3["date"] = "20260811"
    day3["industries"]["半导体"][3]["net"] = -18.2 + 0.001
    r2 = sf.save_sector_flow_snapshot(day3, date="20260811")
    assert r2["skipped"] is False
    assert r2["rows_saved"] == 8


def test_save_calendar_nontrading_skipped(monkeypatch, isolated_store):
    """权威日历非交易日 → 跳过，note 标注日历判定。"""
    monkeypatch.setattr(sf, "_is_trading_day", lambda d: False)
    sf.save_sector_flow_snapshot(_snapshot(), date="20260810")
    day2 = _snapshot()
    day2["date"] = "20260811"
    r = sf.save_sector_flow_snapshot(day2, date="20260811")
    assert r["skipped"] is True
    assert "非交易日（日历判定）" in r["note"]


def test_save_identical_trading_day_note(monkeypatch, isolated_store):
    """交易日 + 数据全等 → 跳过，note 标注疑似盘前未刷新。"""
    monkeypatch.setattr(sf, "_is_trading_day", lambda d: True)
    sf.save_sector_flow_snapshot(_snapshot(), date="20260810")
    day2 = _snapshot()
    day2["date"] = "20260811"
    r = sf.save_sector_flow_snapshot(day2, date="20260811")
    assert r["skipped"] is True
    assert "盘前未刷新" in r["note"]


def test_save_calendar_unavailable_fallback(monkeypatch, isolated_store):
    """日历不可用（None）→ 回退原全等跳过语义。"""
    monkeypatch.setattr(sf, "_is_trading_day", lambda d: None)
    sf.save_sector_flow_snapshot(_snapshot(), date="20260810")
    day2 = _snapshot()
    day2["date"] = "20260811"
    r = sf.save_sector_flow_snapshot(day2, date="20260811")
    assert r["skipped"] is True
    assert "盘前未刷新" in r["note"]


def test_save_upsert_preserves_values_on_null(isolated_store):
    """同日重存：新净额 None 不覆盖旧值（COALESCE merge upsert）。"""
    sf.save_sector_flow_snapshot(_snapshot(), date="20260811")
    day2 = _snapshot()
    day2["date"] = "20260811"
    day2["industries"]["半导体"][3] = {"net": None, "chg": 2.5}
    r = sf.save_sector_flow_snapshot(day2, date="20260811")
    assert r["skipped"] is False  # None → 判定有变化
    hist = sf.load_sector_flow_history("半导体", 3, 10)
    assert hist[-1]["net"] == pytest.approx(-18.2)  # 旧值保留


def test_query_missing_latest_industry(isolated_store, monkeypatch):
    """最新快照缺失的行业：net/趋势全 None + 明确提示（防与旧趋势混排）。"""
    sf.save_sector_flow_snapshot(_snapshot(), date="20260811")
    _patch_query_env(monkeypatch, {"159206": {"sw_code": "801080", "sw_name": "电子", "sub": "半导体"}})
    out = sf.query_sector_flow("159206")
    rows = {r["industry"]: r for r in out["industries"]}
    assert "半导体" in rows  # 有最新快照
    el = rows["元件"]
    assert el["net_3d"] is None
    assert el["trend_label"] == "数据不足"
    assert el["trend_5d"] is None and el["turn_5d"] is None
    assert el["trend_span_days"] is None
    assert any("「元件」无最新快照" in n for n in out["notes"])


def test_query_sequence_span_with_gap(isolated_store, monkeypatch):
    """跨缺采段的 6 快照 → span_days 标注实际跨度（>7）。"""
    dates = ["20260803", "20260804", "20260805", "20260806", "20260807", "20260814"]
    for i, d in enumerate(dates):
        v = float(i + 1)  # 逐日不同，规避 C5 全等跳过
        snap = {"date": d, "available": True,
                "industries": {"半导体": {
                    1: {"net": v, "chg": 1.0}, 3: {"net": v, "chg": 1.0},
                    5: {"net": v, "chg": 1.0}, 10: {"net": v, "chg": 1.0},
                }},
                "errors": []}
        sf.save_sector_flow_snapshot(snap, date=d)
    _patch_query_env(monkeypatch, {"159206": {"sw_code": "801080", "sw_name": "电子", "sub": "半导体"}})
    out = sf.query_sector_flow("159206")
    assert out["industries"][0]["trend_span_days"] == 11  # 08-03 → 08-14 缺采一周


def test_snapshot_drift_detects_unmapped():
    """快照名单不在 SW_TO_THS_INDUSTRY 的行业 → 反向漂移告警清单。"""
    drift = sf.check_snapshot_drift({
        "industries": {"半导体": {}, "存储": {}, "军工电子": {}},
    })
    assert "存储" in drift
    assert "半导体" not in drift
    assert "军工电子" not in drift
    # 与正向 check_mapping_coverage 互补：未映射行业不在 wanted，不触发正向缺失
    missing = sf.check_mapping_coverage({
        "industries": {"半导体": {}, "存储": {}, "军工电子": {}},
    })
    assert "存储" not in missing
