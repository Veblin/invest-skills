"""SQLite 连接助手 — 供各 skill 共享（无业务依赖）。

历史：journal db.py 与 stock store.py 各有一份 _conn/_safe_close（body 近乎逐行相同）。
统一收敛至此；WAL/synchronous PRAGMA 属各 schema 的 init_db 逻辑，留在各自侧。
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path


def connect_db(path: Path) -> sqlite3.Connection:
    """mkdir(parents=True, exist_ok=True) + sqlite3.connect + row_factory=Row
    + PRAGMA foreign_keys=ON（两处历史实现的公共行为）。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(p))
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    return c


def safe_close(conn: sqlite3.Connection, *, logger: logging.Logger | None = None) -> None:
    """try: conn.close() / except Exception: pass — logger 传入时才记录 debug 日志。"""
    try:
        conn.close()
    except Exception:
        if logger is not None:
            logger.debug("sqlite close failed", exc_info=True)
