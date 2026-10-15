"""A02 公共 DB 连接契约：公开入口 + 私有名同实现别名 + 跨 skill 反例扫描。

验证（v0.3.2 A基础）：
1. `_db_override` 隔离下 `open_connection()` / `connection()` 落点与提交可见性；
2. `connection()` 不自动 commit（事务归调用方）且退出即关闭；
3. 私有名 `_conn/_connection/_safe_close` 是**同实现别名**（不是第二份实现）；
4. 全仓反例：stock 包**之外**不得再出现 `lib.store._conn/_connection/
   _safe_close` 的 import 或 `store._conn` 式私有属性穿透；
5. db_util 公开原语在独立 subprocess（仅 skills/lib 在 path）可用。
"""

from __future__ import annotations

import ast
import sqlite3
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SKILLS = _REPO_ROOT / "skills"
_STOCK_SKILL = _SKILLS / "invest-a-stock"

_PRIVATE_NAMES = {"_conn", "_connection", "_safe_close"}


def test_open_connection_lands_in_override(isolated_store) -> None:
    from lib.store import get_db_path, open_connection

    c = open_connection()
    try:
        c.execute("CREATE TABLE IF NOT EXISTS a02_probe(x INTEGER)")
        c.execute("INSERT INTO a02_probe VALUES (1)")
        c.commit()
    finally:
        c.close()
    assert Path(get_db_path()).exists()
    c2 = open_connection()
    try:
        assert c2.execute("SELECT x FROM a02_probe").fetchone()[0] == 1
    finally:
        c2.close()


def test_connection_context_no_autocommit_and_closes(isolated_store) -> None:
    from lib.store import connection, open_connection

    with connection() as c:
        c.execute("CREATE TABLE a02_probe2(x INTEGER)")
        c.execute("INSERT INTO a02_probe2 VALUES (42)")
        # 不 commit —— 退出即回滚（连接关闭）
    c2 = open_connection()
    try:
        assert c2.execute("SELECT COUNT(*) FROM a02_probe2").fetchone()[0] == 0
    finally:
        c2.close()
    try:
        c.execute("SELECT 1")
        raise AssertionError("connection() 退出后连接应已关闭")
    except sqlite3.ProgrammingError:
        pass


def test_private_names_are_aliases_of_public() -> None:
    from lib import store

    assert store._conn is store.open_connection
    assert store._safe_close is store.close_connection
    assert store._connection is store.connection


def _scan_private_store_wiring() -> list[str]:
    offenders: list[str] = []
    for path in sorted(_SKILLS.rglob("*.py")):
        if "__pycache__" in path.parts or _STOCK_SKILL in path.parents:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "lib.store":
                for alias in node.names:
                    if alias.name in _PRIVATE_NAMES:
                        offenders.append(f"{path.relative_to(_REPO_ROOT)}:{node.lineno}: import {alias.name}")
            if isinstance(node, ast.Attribute) and node.attr in _PRIVATE_NAMES:
                v = node.value
                if isinstance(v, ast.Name) and v.id in {"store", "store_mod", "_store"}:
                    offenders.append(f"{path.relative_to(_REPO_ROOT)}:{node.lineno}: {v.id}.{node.attr}")
    return offenders


def test_no_private_store_connection_usage_outside_stock() -> None:
    """stock 包外的消费者必须走公开连接（A02 验收反例，应失败即此测试红）。"""
    offenders = _scan_private_store_wiring()
    assert not offenders, "stock 包外仍在使用 store 私有连接接口:\n" + "\n".join(offenders)


def test_db_util_public_primitives_standalone_subprocess(tmp_path: Path) -> None:
    """独立 subprocess（仅 skills/lib 在 path）：db_util 公开原语可用、零网络。"""
    code = (
        "import sys, sqlite3\n"
        f"sys.path.insert(0, {str(_SKILLS / 'lib')!r})\n"
        "from db_util import close_connection, connection, open_connection\n"
        f"db = {str(tmp_path / 'standalone.db')!r}\n"
        "c = open_connection(db)\n"
        "c.execute('CREATE TABLE t(x)'); c.execute('INSERT INTO t VALUES (7)'); c.commit()\n"
        "close_connection(c)\n"
        "with connection(db) as c2:\n"
        "    assert c2.execute('SELECT x FROM t').fetchone()[0] == 7\n"
        "try:\n"
        "    c2.execute('SELECT 1')\n"
        "    raise SystemExit('connection() must close on exit')\n"
        "except sqlite3.ProgrammingError:\n"
        "    pass\n"
        "print('ok')\n"
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True,
                          text=True, cwd=str(_REPO_ROOT), timeout=120)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "ok"
