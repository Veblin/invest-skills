"""E4 (v0.2.7): thesis 失效/触发日期戳。

覆盖：
- --invalidate / --trigger-redline 写入 invalidated_at / triggered_at（JSON 结构内）
- thesis --status 展示「失效于 YYYY-MM-DD」/「触发于 YYYY-MM-DD」
- 存量数据（无日期字段）读取不报错，展示「日期未记录」
"""

from __future__ import annotations

import json
import sqlite3
import sys
from argparse import Namespace
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))


def _thesis_args(**overrides) -> Namespace:
    base = dict(symbol="TST-E4", init=False, update=False, status=False,
                invalidate=[], trigger_redline=[])
    base.update(overrides)
    return Namespace(**base)


def _today() -> str:
    from lib.shared_dates import shanghai_now

    return shanghai_now().strftime("%Y-%m-%d")


def _insert_old_schema_row(store_mod, symbol: str) -> None:
    """直接写库，模拟存量数据：assumptions/red_lines 无 invalidated_at/triggered_at。"""
    assumptions = [
        {"id": "a1", "statement": "s1", "confidence": 0.7, "last_check_date": None, "valid": True},
        {"id": "a2", "statement": "s2", "confidence": 0.6, "last_check_date": None, "valid": False},
    ]
    red_lines = [
        {"id": "r1", "condition": "c1", "triggered": True},
    ]
    with sqlite3.connect(str(store_mod._get_path())) as c:
        c.execute(
            """INSERT INTO thesis
               (symbol, assumptions_json, red_lines_json, health_score, state,
                created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (symbol, json.dumps(assumptions, ensure_ascii=False),
             json.dumps(red_lines, ensure_ascii=False),
             0.5, "受损", "2026-01-01 00:00:00", "2026-01-01 00:00:00"),
        )


class TestThesisInvalidateTimestamp:
    def test_invalidate_writes_invalidated_at(self, isolated_store):
        """--invalidate 后 JSON 结构出现 invalidated_at，且等于上海当天日期."""
        import invest

        invest.cmd_thesis(_thesis_args(init=True))
        assert invest.cmd_thesis(_thesis_args(update=True, invalidate=["a2"])) == 0

        t = isolated_store.thesis_get("TST-E4")
        assert t is not None
        a2 = next(a for a in t["assumptions"] if a["id"] == "a2")
        assert a2["valid"] is False
        assert a2["invalidated_at"] == _today()
        # 未失效的假设不被打时间戳
        a1 = next(a for a in t["assumptions"] if a["id"] == "a1")
        assert a1["valid"] is True
        assert a1.get("invalidated_at") is None

    def test_trigger_redline_writes_triggered_at(self, isolated_store):
        """--trigger-redline 后 JSON 结构出现 triggered_at，且等于上海当天日期."""
        import invest

        invest.cmd_thesis(_thesis_args(init=True))
        assert invest.cmd_thesis(_thesis_args(update=True, trigger_redline=["r1"])) == 0

        t = isolated_store.thesis_get("TST-E4")
        assert t is not None
        r1 = next(r for r in t["red_lines"] if r["id"] == "r1")
        assert r1["triggered"] is True
        assert r1["triggered_at"] == _today()
        r2 = next(r for r in t["red_lines"] if r["id"] == "r2")
        assert r2["triggered"] is False
        assert r2.get("triggered_at") is None

    def test_new_init_has_date_fields(self, isolated_store):
        """新初始化的记录结构自带日期字段（None），schema 自文档化."""
        from lib.store import thesis_get, thesis_init

        thesis_init("TST-NEW")
        t = thesis_get("TST-NEW")
        assert all("invalidated_at" in a for a in t["assumptions"])
        assert all("triggered_at" in r for r in t["red_lines"])


class TestThesisStatusDisplay:
    def test_status_shows_invalidate_and_trigger_dates(self, isolated_store, capsys):
        """--status 展示「失效于 YYYY-MM-DD」/「触发于 YYYY-MM-DD」."""
        import invest

        invest.cmd_thesis(_thesis_args(init=True))
        invest.cmd_thesis(_thesis_args(
            update=True, invalidate=["a2"], trigger_redline=["r1"]))

        assert invest.cmd_thesis(_thesis_args(status=True)) == 0
        out = capsys.readouterr().out
        assert f"失效于 {_today()}" in out
        assert f"触发于 {_today()}" in out
        assert "有效" in out
        assert "未触发" in out

    def test_status_old_schema_shows_dates_not_recorded(self, isolated_store, capsys):
        """存量数据（无日期字段）读取不报错，展示「日期未记录」."""
        import invest

        _insert_old_schema_row(isolated_store, "TST-E4")

        # 读取不报错（E4 验收标准 2）
        t = isolated_store.thesis_get("TST-E4")
        assert t is not None
        assert t["assumptions"][1]["valid"] is False
        assert "invalidated_at" not in t["assumptions"][1]

        assert invest.cmd_thesis(_thesis_args(status=True)) == 0
        out = capsys.readouterr().out
        assert "失效 · 日期未记录" in out
        assert "触发 · 日期未记录" in out

    def test_status_old_schema_update_roundtrip_ok(self, isolated_store):
        """存量数据再走 --update 不报错，旧记录缺失字段保持缺失."""
        import invest

        _insert_old_schema_row(isolated_store, "TST-E4")
        assert invest.cmd_thesis(_thesis_args(update=True, invalidate=["a1"])) == 0

        t = isolated_store.thesis_get("TST-E4")
        a1 = next(a for a in t["assumptions"] if a["id"] == "a1")
        assert a1["valid"] is False
        assert a1["invalidated_at"] == _today()
        # 旧记录 a2 原样保留，未被打补丁
        a2 = next(a for a in t["assumptions"] if a["id"] == "a2")
        assert "invalidated_at" not in a2

    def test_status_missing_thesis_returns_1(self, isolated_store, capsys):
        """未 --init 时 --status 返回 1 并提示."""
        import invest

        assert invest.cmd_thesis(_thesis_args(status=True)) == 1
        # 状态/错误行走 stderr（stdout 契约：命令正文；v0.2.7 code-review 第五轮）
        assert "未找到" in capsys.readouterr().err
