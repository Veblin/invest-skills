"""快照落盘契约 — 离线单测（设计 §6 schema + §8 回填前置）。

`rules_version` 是**回填裁决的唯一锚点**（设计 §8-4「禁止未记录原因改参」），
故缺失必须 fail loud，不能静默写出一份无法归因的快照。
"""
from __future__ import annotations

import json

import pytest

import snapshot

_HIT = {"ts_code": "600000.SH", "name": "浦发银行", "industry": "银行",
        "pe_ttm": 6.0, "ey_pct": 16.67, "pe_grank": 0.01, "ind_rk": 1, "ind_n": 42,
        "gap_flags": [1, 0], "mv_yi": 160.0, "close": 3.59, "fillback": None}


def _rec(**over):
    base = {
        "snapshot_ts": "2026-09-12T15:30:00+08:00",
        "trade_date": "20260911",
        "rules_version": snapshot.RULES_VERSION,
        "pool": {"market": "主板+创业+科创", "n_positive_pe": 3603, "n_pool": 5010},
        "params": {"pe_grank_max": 0.15, "ind_rank_max": 0.25, "roe_min": 8.0, "top_n": 15,
                   "per_industry": 3, "with_bj": False},
        "hits": [dict(_HIT)],
        "warnings": [],
    }
    base.update(over)
    return base


def test_rules_version_pinned():
    assert snapshot.RULES_VERSION == "0.1.0"


def test_build_snapshot_has_all_design_fields():
    rec = snapshot.build_snapshot(
        scan_ts="2026-09-12T15:30:00+08:00", trade_date="20260911",
        pool={"market": "主板+创业+科创", "n_positive_pe": 3603, "n_pool": 5010},
        params={"pe_grank_max": 0.15, "ind_rank_max": 0.25, "roe_min": 8.0, "top_n": 15,
                "per_industry": 3, "with_bj": False},
        hits=[dict(_HIT)], warnings=[])
    for key in ("snapshot_ts", "rules_version", "pool", "params", "hits", "warnings", "trade_date"):
        assert key in rec, f"schema 缺字段 {key}"
    for key in ("market", "n_positive_pe", "n_pool"):
        assert key in rec["pool"]
    for key in ("pe_grank_max", "ind_rank_max", "roe_min", "top_n",
                "per_industry", "with_bj"):
        assert key in rec["params"]
    hit = rec["hits"][0]
    for key in ("ts_code", "name", "industry", "pe_ttm", "ey_pct", "pe_grank",
                "ind_rk", "ind_n", "gap_flags", "mv_yi", "close", "fillback"):
        assert key in hit, f"hits schema 缺字段 {key}"
    assert hit["fillback"] is None, "v0.1 不产回填值（由 v0.2 写入）"


def test_validate_snapshot_requires_rules_version():
    assert snapshot.validate_snapshot(_rec()) == []
    assert snapshot.validate_snapshot(_rec(rules_version=None)), "缺 rules_version 须报错"
    assert snapshot.validate_snapshot(_rec(rules_version=""))


def test_validate_snapshot_reports_missing_top_level():
    errs = snapshot.validate_snapshot({})
    assert errs and any("rules_version" in e for e in errs)


def test_append_and_read_roundtrip(tmp_path):
    p = tmp_path / "2026.jsonl"
    snapshot.append_snapshot(_rec(), path=p)
    snapshot.append_snapshot(_rec(hits=[dict(_HIT, ts_code="000001.SZ")]), path=p)
    got = snapshot.read_snapshots(path=p)
    assert len(got) == 2
    assert got[0]["hits"][0]["ts_code"] == "600000.SH"
    assert got[1]["hits"][0]["ts_code"] == "000001.SZ"
    # 每行须是合法单行 JSON（jsonl 契约）
    for line in p.read_text(encoding="utf-8").strip().splitlines():
        json.loads(line)


def test_empty_hits_still_persisted(tmp_path):
    """「本次无命中」是有信息量的结果——不落盘会让后续回填无法区分「没跑」与「跑了没命中」。"""
    p = tmp_path / "2026.jsonl"
    snapshot.append_snapshot(_rec(hits=[]), path=p)
    got = snapshot.read_snapshots(path=p)
    assert len(got) == 1 and got[0]["hits"] == []


def test_append_rejects_invalid_record(tmp_path):
    p = tmp_path / "2026.jsonl"
    with pytest.raises(ValueError):
        snapshot.append_snapshot(_rec(rules_version=None), path=p)
    assert not p.exists(), "校验失败不得留下半份快照"


def test_read_snapshots_missing_file_is_empty(tmp_path):
    assert snapshot.read_snapshots(path=tmp_path / "nope.jsonl") == []


def test_discovery_dir_is_private_store_path():
    """个股产出仅本地：私有 store 目录（同 research.db 惯例），不得落在仓库内。"""
    d = snapshot.discovery_dir()
    assert "investment" in str(d)
    assert "discovery" in str(d)
    assert str(d).startswith(str(snapshot.Path.home()))


def test_snapshot_path_is_year_scoped():
    assert snapshot.snapshot_path(2026).name == "2026.jsonl"
