"""Tests for skills/lib/db_util.py — SQLite 连接助手（无网络）。"""

from __future__ import annotations

import logging
import sqlite3
import sys
from pathlib import Path

import pytest

_SKILLS_LIB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SKILLS_LIB))  # 无条件插 0：防其他 skill 目录先行入 path 遮蔽同名模块

from db_util import (  # noqa: E402
    connect_db,
    load_recent_rows,
    safe_close,
    upsert_daily_rows,
)


class TestConnectDb:
    def test_creates_parent_dirs(self, tmp_path: Path):
        p = tmp_path / "a" / "b" / "test.db"
        c = connect_db(p)
        try:
            assert p.parent.is_dir()
        finally:
            c.close()

    def test_row_factory_and_foreign_keys(self, tmp_path: Path):
        c = connect_db(tmp_path / "test.db")
        try:
            assert c.row_factory is sqlite3.Row
            row = c.execute("PRAGMA foreign_keys").fetchone()
            assert row[0] == 1
        finally:
            c.close()

    def test_read_write_roundtrip(self, tmp_path: Path):
        c = connect_db(tmp_path / "test.db")
        try:
            c.execute("CREATE TABLE t (x INTEGER)")
            c.execute("INSERT INTO t VALUES (42)")
            got = c.execute("SELECT x FROM t").fetchone()
            assert got[0] == 42
        finally:
            c.close()


class TestSafeClose:
    def test_noop_on_open_conn(self, tmp_path: Path):
        c = connect_db(tmp_path / "test.db")
        safe_close(c)  # 不应抛异常

    def test_logger_optional(self, tmp_path: Path, caplog):
        c = connect_db(tmp_path / "test.db")
        with caplog.at_level(logging.DEBUG, logger="test_logger"):
            safe_close(c, logger=logging.getLogger("test_logger"))
        # 正常关闭不产生日志
        assert not caplog.records

    def test_close_twice_no_raise(self, tmp_path: Path):
        c = connect_db(tmp_path / "test.db")
        safe_close(c)
        safe_close(c)  # 二次关闭不应抛异常


class TestUpsertDailyRows:
    def _table(self, tmp_path: Path):
        c = connect_db(tmp_path / "test.db")
        c.execute(
            "CREATE TABLE snaps (date TEXT PRIMARY KEY, a REAL, b REAL, "
            "note TEXT DEFAULT 'x')"
        )
        return c

    def test_replace_semantics(self, tmp_path: Path):
        """merge=False：整行替换（非 PK 列全量覆盖）。"""
        c = self._table(tmp_path)
        try:
            upsert_daily_rows(c, "snaps", [{"date": "20260801", "a": 1.0, "b": 2.0}],
                              pk=("date",), merge=False)
            upsert_daily_rows(c, "snaps", [{"date": "20260801", "a": 9.0, "b": None}],
                              pk=("date",), merge=False)
            row = dict(c.execute("SELECT * FROM snaps").fetchone())
            assert row["a"] == 9.0 and row["b"] is None  # b 被整行覆盖为 NULL
        finally:
            c.close()

    def test_merge_semantics(self, tmp_path: Path):
        """merge=True：逐列 COALESCE——NULL 不覆盖旧值，非 NULL 覆盖。"""
        c = self._table(tmp_path)
        try:
            upsert_daily_rows(c, "snaps", [{"date": "20260801", "a": 1.0, "b": 2.0}],
                              pk=("date",), merge=True)
            upsert_daily_rows(c, "snaps", [{"date": "20260801", "a": 9.0, "b": None}],
                              pk=("date",), merge=True)
            row = dict(c.execute("SELECT * FROM snaps").fetchone())
            assert row["a"] == 9.0      # 非 NULL 覆盖
            assert row["b"] == 2.0      # NULL 保留旧值
        finally:
            c.close()

    def test_rows_must_share_keys(self, tmp_path: Path):
        c = self._table(tmp_path)
        try:
            with pytest.raises(AssertionError):
                upsert_daily_rows(c, "snaps",
                                  [{"date": "1", "a": 1.0}, {"date": "2"}],
                                  pk=("date",))
        finally:
            c.close()


class TestLoadRecentRows:
    def test_asc_order_with_where(self, tmp_path: Path):
        c = connect_db(tmp_path / "test.db")
        try:
            c.execute("CREATE TABLE t (code TEXT, date TEXT, v REAL, PRIMARY KEY (code, date))")
            rows = [
                {"code": "300", "date": "20260801", "v": 1.0},
                {"code": "300", "date": "20260802", "v": 2.0},
                {"code": "300", "date": "20260803", "v": 3.0},
                {"code": "999", "date": "20260803", "v": 9.0},
            ]
            upsert_daily_rows(c, "t", rows, pk=("code", "date"), merge=False)
            got = load_recent_rows(c, "t", limit=2, where="code = ?", params=("300",))
            assert [r["date"] for r in got] == ["20260802", "20260803"]  # ASC + LIMIT
            assert [r["v"] for r in got] == [2.0, 3.0]
        finally:
            c.close()
