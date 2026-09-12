"""股票池构建 — 离线单测（设计 §2.2 中过滤第 1/2 条）。

⚠️ 文件名带 `discover_` 前缀：pytest 默认 prepend 模式下，不同目录的同名 test 模块
会互相覆盖（`test_pool.py` 与 invest-a-event-calendar 的同名文件冲突，2026-09-12 实测）。
"""
from __future__ import annotations

import pytest

import pool


def _row(ts_code="600000.SH", name="浦发银行", industry="银行", market="主板"):
    return {"ts_code": ts_code, "name": name, "industry": industry, "market": market}


def test_build_pool_keeps_main_gem_star():
    rows = [_row("600000.SH", market="主板"), _row("300001.SZ", market="创业板"),
            _row("688001.SH", market="科创板")]
    out = pool.build_pool(rows)
    assert out["n_total"] == 3 and out["n_excluded_bj"] == 0
    assert {r["ts_code"] for r in out["rows"]} == {"600000.SH", "300001.SZ", "688001.SH"}


def test_build_pool_excludes_bj_by_default_and_can_include():
    rows = [_row("600000.SH"), _row("830001.BJ", market="北交所")]
    default = pool.build_pool(rows)
    assert [r["ts_code"] for r in default["rows"]] == ["600000.SH"]
    assert default["n_excluded_bj"] == 1
    with_bj = pool.build_pool(rows, with_bj=True)
    assert len(with_bj["rows"]) == 2 and with_bj["n_excluded_bj"] == 0


@pytest.mark.parametrize("name", ["ST 中安", "*ST 德豪", "退市海润", "ST德豪"])
def test_build_pool_excludes_st_and_delisting(name):
    rows = [_row("600000.SH"), _row("000001.SZ", name=name)]
    out = pool.build_pool(rows)
    assert [r["ts_code"] for r in out["rows"]] == ["600000.SH"]
    assert out["n_excluded_st"] == 1


@pytest.mark.parametrize("name", ["贵州茅台", "特斯拉概念", "STAR科技", "退一步海阔天空"])
def test_is_st_does_not_over_match(name):
    """「ST」须在**名称开头**才算前缀；含「退」的字面不得误伤（防误杀正常标的）。"""
    expected = name.startswith("ST") or name.startswith("*ST") or "退市" in name
    assert pool.is_st(name) is expected


def test_build_pool_empty_fails_loud():
    """D5：空输入静默返回空池 = 报告显示「今日无命中」，属事实性错误。"""
    with pytest.raises(ValueError):
        pool.build_pool([])


def test_build_pool_counts_are_consistent():
    rows = [_row("600000.SH"), _row("000001.SZ", name="*ST 某某"),
            _row("830001.BJ", market="北交所")]
    out = pool.build_pool(rows)
    assert out["n_total"] == 3
    assert out["n_excluded_st"] + out["n_excluded_bj"] + len(out["rows"]) == out["n_total"]


def test_build_pool_missing_industry_is_dash_not_dropped():
    """行业缺失 → 归「—」桶（L1 行业条件随后跳过），**不得**因此剔除标的。"""
    rows = [_row("600000.SH", industry=None)]
    out = pool.build_pool(rows)
    assert len(out["rows"]) == 1 and out["rows"][0]["industry"] == "—"
