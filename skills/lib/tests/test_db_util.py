"""Tests for skills/lib/db_util.py — SQLite 连接助手（无网络）。"""

from __future__ import annotations

import logging
import sqlite3
import sys
from pathlib import Path

_SKILLS_LIB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SKILLS_LIB))  # 无条件插 0：防其他 skill 目录先行入 path 遮蔽同名模块

from db_util import connect_db, safe_close  # noqa: E402


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
