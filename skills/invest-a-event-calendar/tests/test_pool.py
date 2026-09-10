"""v2 池模式测试（离线）：清单解析 / 批次 diff / 状态读写 / 采集信号 / 渲染 / CLI 端到端。

D13 注：``uc.fetch_symbol_unlocks`` 是模块级导入引用——mock 必须打在调用方模块
（unlock_calendar）命名空间；unlock_source 源模块不在本文件 mock。
"""

from __future__ import annotations

import datetime as _dt
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import unlock_calendar as uc  # noqa: E402


# ── 清单解析 ─────────────────────────────────────────────────────────────

def test_parse_pool_comments_badlines_dedupe(tmp_path):
    p = tmp_path / "pool.txt"
    p.write_text("# 持仓池\n600176\n\n002466  # 天齐\nbad123\n12345\n600176\n",
                 encoding="utf-8")
    syms, bad = uc.parse_pool(p)
    assert syms == ["600176", "002466"]      # 去重保序
    assert len(bad) == 2 and bad[0].startswith("L5") and bad[1].startswith("L6")


# ── 批次 diff ────────────────────────────────────────────────────────────

def test_diff_batches_added_removed_changed():
    old = {"2026-11-05": {"qty_yi": 3.2, "holders": 5, "kind": "定增"}}
    new = {"2026-11-05": {"qty_yi": 3.2, "holders": 5, "kind": "定增"},
           "2026-12-01": {"qty_yi": 1.0, "holders": None, "kind": "首发"}}
    assert uc.diff_batches(old, new) == {"added": ["2026-12-01"], "removed": [],
                                         "changed": []}
    changed = {"2026-11-05": {"qty_yi": 4.0, "holders": 5, "kind": "定增"}}
    assert uc.diff_batches(old, changed)["changed"] == ["2026-11-05"]
    assert uc.diff_batches(old, {})["removed"] == ["2026-11-05"]


def test_trading_days_until_far_field():
    """远场（>365 自然日）→ 自然日 + degraded（防交易日历覆盖截断，2026-09-10 实测缺陷）。"""
    lag, degraded = uc._trading_days_until("2030-07-01", _dt.date(2026, 9, 10))
    assert degraded is True and lag == (_dt.date(2030, 7, 1) - _dt.date(2026, 9, 10)).days
    bad_lag, bad_deg = uc._trading_days_until("not-a-date", _dt.date(2026, 9, 10))
    assert bad_lag is None and bad_deg is True


def test_diff_batches_none_none_is_equal():
    """双侧 None（如股东数不可得）不得判为变化（区别于 freshness NULL 语义）。"""
    same = {"2026-11-05": {"qty_yi": 3.2, "holders": None, "kind": ""}}
    assert uc.diff_batches(same, dict(same))["changed"] == []


# ── 状态读写 ─────────────────────────────────────────────────────────────

def test_state_roundtrip_and_corrupt(tmp_path):
    sp = tmp_path / "state.json"
    assert uc.load_state(sp) == {"updated": None, "symbols": {}}   # 不存在=首跑
    st = {"updated": "2026-09-10 10:00",
          "symbols": {"600176": {"batches": {}, "last_run": "20260910"}}}
    assert uc.save_state(sp, st) is None
    assert uc.load_state(sp)["symbols"]["600176"]["last_run"] == "20260910"
    sp.write_text("{broken", encoding="utf-8")
    assert uc.load_state(sp)["symbols"] == {}                       # 损坏=首跑


# ── 采集信号（失败与空记录必须可区分）───────────────────────────────────

def test_collect_pool_error_signal(monkeypatch):
    def fake(sym, *, lookahead_days, include_past_days=0):
        if sym == "600176":
            return ([{"date": "2026-10-01", "qty_yi": 1.5, "holders": 3,
                      "kind": "定增"}], None)
        return ([], "ConnectionError: boom")

    monkeypatch.setattr(uc, "fetch_symbol_unlocks", fake)
    out = uc.collect_pool(["600176", "600000"], lookahead_days=90, sleep_s=0)
    assert out["600176"]["error"] is None
    assert out["600176"]["batches"]["2026-10-01"]["qty_yi"] == 1.5
    assert out["600000"]["error"].startswith("ConnectionError")
    assert out["600000"]["batches"] == {}


# ── 渲染 ─────────────────────────────────────────────────────────────────

_TODAY = _dt.date(2026, 9, 10)


def _collected():
    return {
        "600176": {"batches": {"2026-09-20": {"qty_yi": 3.2, "holders": 5,
                                              "kind": "定增"}}, "error": None},
        "002466": {"batches": {}, "error": None},
        "600000": {"batches": {}, "error": "ProxyError: blocked"},
    }


def test_render_pool_alerts_changes_failures(monkeypatch):
    monkeypatch.setattr(uc, "_trading_days_until", lambda d, t: (2, False))
    md = uc.render_pool_md(
        _collected(), today=_TODAY, alert_days=30, lookahead_days=90,
        pool_path="pool.txt",
        changes={"600176": {"added": ["2026-09-20"], "removed": [], "changed": []},
                 "002466": {"added": [], "removed": [], "changed": []}},
        baseline_symbols=["600000"], state_updated="2026-09-10 10:00")
    assert "🔔 提醒" in md and "| 600176 | 2026-09-20 | 2 | 3.20 | 5 | 定增 |" in md
    assert "🆕 新增" in md and "首次建基线 1 个标的" in md
    assert "取数失败：ProxyError" in md          # 失败逐行标注（不静默当空）
    assert "无解禁记录（90 日内）" in md


def test_render_pool_no_changes(monkeypatch):
    monkeypatch.setattr(uc, "_trading_days_until", lambda d, t: (2, False))
    md = uc.render_pool_md(
        _collected(), today=_TODAY, alert_days=30, lookahead_days=90,
        pool_path="pool.txt",
        changes={"600176": {"added": [], "removed": [], "changed": []},
                 "002466": {"added": [], "removed": [], "changed": []}},
        baseline_symbols=[], state_updated="2026-09-10 10:00")
    assert "无变化" in md


# ── CLI 端到端 ───────────────────────────────────────────────────────────

def _fake_fetch_factory():
    def fake(sym, *, lookahead_days, include_past_days=0):
        if sym == "600176":
            return ([{"date": "2026-09-20", "qty_yi": 2.0, "holders": None,
                      "kind": ""}], None)
        return ([], None)

    return fake


def test_cli_pool_baseline_then_no_change(tmp_path, monkeypatch, capsys):
    pool = tmp_path / "pool.txt"
    pool.write_text("600176\n# c\n002466\n", encoding="utf-8")
    state = tmp_path / "state.json"
    out = tmp_path / "out"
    monkeypatch.setattr(uc, "fetch_symbol_unlocks", _fake_fetch_factory())
    monkeypatch.setattr(uc, "_trading_days_until", lambda d, t: (3, False))

    argv = ["unlock_calendar.py", "--pool-file", str(pool),
            "--state-file", str(state), "--out-dir", str(out)]
    monkeypatch.setattr(sys, "argv", argv)
    assert uc.main() == 0
    first = capsys.readouterr().out
    assert "池来源" in first and "🔔 提醒" in first
    assert "首次建基线 2 个标的" in first          # 首跑两次都为基线
    st = json.loads(state.read_text(encoding="utf-8"))
    assert set(st["symbols"]) == {"600176", "002466"}

    monkeypatch.setattr(sys, "argv", argv)
    assert uc.main() == 0
    second = capsys.readouterr().out
    assert "无变化" in second                      # 第二次运行无变化
    assert list(out.glob("*-pool.md")), "池报告须落盘"


def test_cli_pool_all_failed_exit3(tmp_path, monkeypatch, capsys):
    pool = tmp_path / "pool.txt"
    pool.write_text("600176\n002466\n", encoding="utf-8")
    monkeypatch.setattr(uc, "fetch_symbol_unlocks",
                        lambda s, **k: ([], "ProxyError: blocked"))
    monkeypatch.setattr(sys, "argv", ["unlock_calendar.py", "--pool-file", str(pool),
                                      "--no-out", "--no-state"])
    assert uc.main() == 3
    assert "不可得" in capsys.readouterr().err


def test_cli_pool_missing_file_exit2(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["unlock_calendar.py", "--pool-file",
                                      str(tmp_path / "nope.txt"), "--no-out"])
    assert uc.main() == 2


def test_cli_pool_partial_failure_exit0_with_marker(tmp_path, monkeypatch, capsys):
    pool = tmp_path / "pool.txt"
    pool.write_text("600176\n600000\n", encoding="utf-8")

    def fake(sym, *, lookahead_days, include_past_days=0):
        if sym == "600176":
            return ([{"date": "2026-09-20", "qty_yi": 1.0, "holders": 1,
                      "kind": "x"}], None)
        return ([], "timeout")

    monkeypatch.setattr(uc, "fetch_symbol_unlocks", fake)
    monkeypatch.setattr(uc, "_trading_days_until", lambda d, t: (1, False))
    monkeypatch.setattr(sys, "argv", ["unlock_calendar.py", "--pool-file", str(pool),
                                      "--no-out", "--no-state"])
    assert uc.main() == 0
    out = capsys.readouterr().out
    assert "取数失败：timeout" in out and "失败 1" in out
