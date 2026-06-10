"""SQLite 研究记录持久化。支持采集结果存储、查询和统计。

数据库: ~/.local/share/investment/research.db
WAL 模式安全并发。轻量 Schema 迁移。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import env

DB_PATH = env.STORE_DB
SCHEMA_VERSION = 1

_db_override: Path | None = None


def _get_path() -> Path:
    return _db_override or DB_PATH


def _conn() -> sqlite3.Connection:
    p = _get_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(p))
    c.row_factory = sqlite3.Row
    return c


def init_db() -> None:
    c = _conn()
    try:
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA synchronous=NORMAL")
        c.executescript("""
            CREATE TABLE IF NOT EXISTS schema_version (
                version INTEGER PRIMARY KEY, applied_at TEXT DEFAULT (datetime('now')));
            CREATE TABLE IF NOT EXISTS collections (
                id INTEGER PRIMARY KEY, symbol TEXT NOT NULL, name TEXT,
                fetched_at TEXT NOT NULL, dimensions_total INTEGER DEFAULT 0,
                dimensions_ok INTEGER DEFAULT 0, raw_json TEXT,
                created_at TEXT DEFAULT (datetime('now')));
            CREATE INDEX IF NOT EXISTS idx_c_sym ON collections(symbol);
            CREATE INDEX IF NOT EXISTS idx_c_fa ON collections(fetched_at);
            CREATE TABLE IF NOT EXISTS findings (
                id INTEGER PRIMARY KEY, collection_id INTEGER REFERENCES collections(id),
                symbol TEXT NOT NULL, dimension TEXT NOT NULL, source TEXT,
                confidence TEXT, summary TEXT, created_at TEXT DEFAULT (datetime('now')));
            CREATE INDEX IF NOT EXISTS idx_f_sym ON findings(symbol);
        """)
        row = c.execute("SELECT MAX(version) as v FROM schema_version").fetchone()
        if not row or not row["v"]:
            c.execute("INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,))
        c.commit()
    finally:
        c.close()


def save_collection(result: dict[str, Any]) -> int:
    init_db()
    symbol = result.get("symbol", "?")
    dims = result.get("dimensions", [])
    sm = result.get("summary", {})
    name = next((d["data"].get("name", "") for d in dims
                 if d.get("dimension") == "basic_info" and d.get("data")), "")
    c = _conn()
    try:
        cur = c.execute(
            "INSERT INTO collections (symbol,name,fetched_at,dimensions_total,dimensions_ok,raw_json) VALUES (?,?,?,?,?,?)",
            (symbol, name, result.get("fetched_at", ""), sm.get("total", 0), sm.get("available", 0),
             json.dumps(result, ensure_ascii=False, default=str)))
        cid = cur.lastrowid
        for d in dims:
            data = d.get("data")
            if isinstance(data, dict):
                # 字典截取安全：只保留前 5 个 key 的值
                small = {k: data[k] for k in list(data.keys())[:5]}
                summary = json.dumps(small, ensure_ascii=False, default=str)
            elif isinstance(data, list):
                summary = f"{len(data)} 条记录"
            else:
                summary = ""
            m = d.get("_meta", {})
            c.execute("INSERT INTO findings (collection_id,symbol,dimension,source,confidence,summary) VALUES (?,?,?,?,?,?)",
                      (cid, symbol, d.get("dimension", ""), m.get("source", ""), m.get("confidence", ""), summary))
        c.commit()
        return cid
    finally:
        c.close()


def list_collections(limit: int = 20, symbol: str | None = None) -> list[dict]:
    init_db()
    c = _conn()
    try:
        if symbol:
            rows = c.execute("SELECT * FROM collections WHERE symbol=? ORDER BY fetched_at DESC LIMIT ?", (symbol, limit)).fetchall()
        else:
            rows = c.execute("SELECT * FROM collections ORDER BY fetched_at DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        c.close()


def get_stats() -> dict:
    init_db()
    c = _conn()
    try:
        tc = c.execute("SELECT COUNT(*) as c FROM collections").fetchone()["c"]
        tf = c.execute("SELECT COUNT(*) as c FROM findings").fetchone()["c"]
        us = c.execute("SELECT COUNT(DISTINCT symbol) as c FROM collections").fetchone()["c"]
        lat = c.execute("SELECT symbol,fetched_at FROM collections ORDER BY fetched_at DESC LIMIT 5").fetchall()
        return {"total_collections": tc, "total_findings": tf, "unique_symbols": us,
                "latest": [dict(r) for r in lat], "db_path": str(_get_path())}
    finally:
        c.close()


def clear_all() -> None:
    init_db()
    c = _conn()
    try:
        c.execute("BEGIN")
        c.execute("DELETE FROM findings")
        c.execute("DELETE FROM collections")
        c.commit()
    finally:
        c.close()
