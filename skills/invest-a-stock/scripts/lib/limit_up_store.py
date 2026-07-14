"""SQLite 持久化：涨停扫描结果存储与回溯查询。

与 store.py 共享同一个数据库（~/.local/share/investment/research.db），
新增 limit_up_scans / limit_up_stocks 两张表。

v0.2.0: 首版，支持 scan 存储、按日期/标的/行业/连板回溯。
"""

from __future__ import annotations

import json
import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterator

from . import env
from .json_util import dumps_json
from .nums import safe_float

logger = logging.getLogger(__name__)

DB_PATH = env.STORE_DB
_db_override: Path | None = None
_initialized_paths: set[str] = set()


def _get_path() -> Path:
    """Resolve DB path, sharing store._db_override for test isolation.

    Priority: local _db_override > store._db_override > env.STORE_DB.
    """
    if _db_override is not None:
        return _db_override
    # Lazy import avoids circular import at module load (store.init_db → here).
    from . import store as store_mod

    return store_mod._get_path()


def _conn() -> sqlite3.Connection:
    p = _get_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(p))
    c.row_factory = sqlite3.Row
    # Required for REFERENCES ... ON DELETE CASCADE to take effect.
    c.execute("PRAGMA foreign_keys=ON")
    return c


def _safe_close(c: sqlite3.Connection) -> None:
    try:
        c.close()
    except Exception:
        logger.debug("sqlite close failed", exc_info=True)


@contextmanager
def _connection() -> Iterator[sqlite3.Connection]:
    c = _conn()
    try:
        yield c
    finally:
        _safe_close(c)


def _cutoff_yyyymmdd(days: int) -> str:
    """Calendar lookback as YYYYMMDD (matches scan_date / Asia/Shanghai storage)."""
    from zoneinfo import ZoneInfo
    d = datetime.now(ZoneInfo("Asia/Shanghai")).date() - timedelta(days=max(0, days))
    return d.strftime("%Y%m%d")


def _like_contains(needle: str) -> str:
    """Wrap needle for LIKE '%…%' with %/_/\\ escaped."""
    esc = (
        needle.replace("\\", "\\\\")
        .replace("%", "\\%")
        .replace("_", "\\_")
    )
    return f"%{esc}%"


def init_limit_up_db() -> None:
    """建表（幂等）。store.py init_db() 也会调用此函数。"""
    path_key = str(_get_path())
    if path_key in _initialized_paths:
        return
    with _connection() as c:
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA synchronous=NORMAL")
        c.executescript("""
            CREATE TABLE IF NOT EXISTS limit_up_scans (
                id INTEGER PRIMARY KEY,
                scan_date TEXT NOT NULL,
                trading_days_scanned INTEGER DEFAULT 0,
                total_unique_stocks INTEGER DEFAULT 0,
                avg_daily_count REAL DEFAULT 0,
                days_with_limit_ups INTEGER DEFAULT 0,
                breadth_json TEXT,
                enrichment_json TEXT,
                errors_json TEXT,
                filter_params_json TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_lus_date ON limit_up_scans(scan_date);

            CREATE TABLE IF NOT EXISTS limit_up_stocks (
                id INTEGER PRIMARY KEY,
                scan_id INTEGER REFERENCES limit_up_scans(id) ON DELETE CASCADE,
                symbol TEXT NOT NULL,
                name TEXT,
                sector TEXT,
                market TEXT,
                max_consecutive INTEGER DEFAULT 0,
                total_appearances INTEGER DEFAULT 0,
                first_date TEXT,
                last_date TEXT,
                latest_close REAL,
                latest_change_pct REAL,
                float_mkt_cap REAL,
                market_cap REAL,
                is_st INTEGER DEFAULT 0,
                flags_json TEXT,
                appearances_json TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            );
            CREATE INDEX IF NOT EXISTS idx_lust_scan ON limit_up_stocks(scan_id);
            CREATE INDEX IF NOT EXISTS idx_lust_sym ON limit_up_stocks(symbol);
            CREATE INDEX IF NOT EXISTS idx_lust_sector ON limit_up_stocks(sector);
            CREATE INDEX IF NOT EXISTS idx_lust_consec ON limit_up_stocks(max_consecutive);
        """)
        c.commit()
        _initialized_paths.add(path_key)


# ---- 写入 ----

def save_scan(result: dict, filter_params: dict | None = None) -> int:
    """保存一次扫描结果。同一天重复扫描会覆盖旧记录（UPSERT）。

    Args:
        result: scan_market() 或 quality_filter() 的返回 dict
        filter_params: 使用的 quality_filter 参数（可选，用于回溯审计）

    Returns:
        scan_id (int)

    Raises:
        ValueError: empty scan_date, or failed/empty scan (0 trading days and no stocks)
    """
    scan_date = (result.get("scan_date") or "").strip()
    stocks = result.get("stocks") or []
    trading_days = int(result.get("trading_days_scanned") or 0)
    if not scan_date:
        raise ValueError("save_scan: scan_date is required")
    if trading_days == 0 and not stocks:
        raise ValueError(
            "save_scan: refusing empty/failed scan "
            f"(scan_date={scan_date!r}, trading_days_scanned=0, stocks=[])"
        )

    init_limit_up_db()
    with _connection() as c:
        breadth = result.get("market_breadth", {}) or {}

        c.execute(
            """INSERT INTO limit_up_scans
               (scan_date, trading_days_scanned, total_unique_stocks,
                avg_daily_count, days_with_limit_ups,
                breadth_json, enrichment_json, errors_json, filter_params_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(scan_date) DO UPDATE SET
                trading_days_scanned = excluded.trading_days_scanned,
                total_unique_stocks = excluded.total_unique_stocks,
                avg_daily_count = excluded.avg_daily_count,
                days_with_limit_ups = excluded.days_with_limit_ups,
                breadth_json = excluded.breadth_json,
                enrichment_json = excluded.enrichment_json,
                errors_json = excluded.errors_json,
                filter_params_json = excluded.filter_params_json""",
            (
                scan_date,
                trading_days,
                breadth.get("total_unique_stocks", 0),
                breadth.get("avg_daily_count", 0),
                breadth.get("days_with_limit_ups", 0),
                dumps_json(breadth),
                dumps_json(result.get("enrichment")),
                dumps_json(result.get("errors", [])),
                dumps_json(filter_params) if filter_params is not None else None,
            ),
        )
        row = c.execute(
            "SELECT id FROM limit_up_scans WHERE scan_date = ?", (scan_date,)
        ).fetchone()
        scan_id = row["id"]

        c.execute("DELETE FROM limit_up_stocks WHERE scan_id = ?", (scan_id,))
        if stocks:
            _insert_stocks(c, scan_id, stocks)

        c.commit()
        logger.info(
            "limit-up scan saved: scan_date=%s scan_id=%d stocks=%d",
            scan_date, scan_id, len(stocks),
        )
        return scan_id


def _insert_stocks(c: sqlite3.Connection, scan_id: int, stocks: list[dict]) -> None:
    """批量写入股票记录。"""
    rows: list[tuple] = []
    for s in stocks:
        apps = s.get("appearances", [])
        latest = apps[-1] if apps else {}
        rows.append((
            scan_id,
            s.get("symbol", ""),
            s.get("name", ""),
            s.get("sector", ""),
            s.get("market", ""),
            s.get("max_consecutive", 0),
            s.get("total_appearances", 0),
            s.get("first_date", ""),
            s.get("last_date", ""),
            latest.get("close"),
            latest.get("change_pct"),
            safe_float(s.get("float_mkt_cap")),
            safe_float(s.get("market_cap")),
            1 if s.get("is_st") else 0,
            dumps_json(s.get("flags", {})),
            dumps_json(apps),
        ))
    c.executemany(
        """INSERT INTO limit_up_stocks
           (scan_id, symbol, name, sector, market,
            max_consecutive, total_appearances, first_date, last_date,
            latest_close, latest_change_pct, float_mkt_cap, market_cap,
            is_st, flags_json, appearances_json)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        rows,
    )


# ---- 查询 ----

def list_scans(limit: int = 20) -> list[dict]:
    """列出最近扫描记录（不含 stocks 明细）。"""
    init_limit_up_db()
    with _connection() as c:
        rows = c.execute(
            "SELECT * FROM limit_up_scans ORDER BY scan_date DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [_scan_row_to_dict(r) for r in rows]


def get_scan(scan_id: int | None = None, scan_date: str | None = None) -> dict | None:
    """获取单次扫描完整记录（含股票列表）。"""
    init_limit_up_db()
    with _connection() as c:
        if scan_id is not None:
            row = c.execute(
                "SELECT * FROM limit_up_scans WHERE id = ?", (scan_id,)
            ).fetchone()
        elif scan_date is not None:
            row = c.execute(
                "SELECT * FROM limit_up_scans WHERE scan_date = ?", (scan_date,)
            ).fetchone()
        else:
            return None

        if not row:
            return None

        result = _scan_row_to_dict(row)
        sid = row["id"]
        stocks = c.execute(
            "SELECT * FROM limit_up_stocks WHERE scan_id = ? ORDER BY max_consecutive DESC, symbol",
            (sid,),
        ).fetchall()
        result["stocks"] = [_stock_row_to_dict(r) for r in stocks]
        return result


def get_stock_history(symbol: str, limit: int = 30) -> list[dict]:
    """查询某个标的的历史涨停记录（按日期降序）。"""
    init_limit_up_db()
    with _connection() as c:
        rows = c.execute(
            """SELECT s.scan_date, st.*
               FROM limit_up_stocks st
               JOIN limit_up_scans s ON st.scan_id = s.id
               WHERE st.symbol = ?
               ORDER BY s.scan_date DESC
               LIMIT ?""",
            (symbol, limit),
        ).fetchall()
        return [_stock_row_to_dict(r, include_scan_date=True) for r in rows]


def get_sector_top(sector: str, days: int = 30, top_n: int = 20) -> list[dict]:
    """查询某行业在最近 N 天内的涨停活跃标的（按出现次数降序）。"""
    init_limit_up_db()
    cutoff = _cutoff_yyyymmdd(days)
    pattern = _like_contains(sector)
    with _connection() as c:
        rows = c.execute(
            """SELECT st.symbol, st.name, COUNT(*) as scan_count,
                    MAX(st.max_consecutive) as max_board,
                    MAX(st.latest_close) as latest_close
               FROM limit_up_stocks st
               JOIN limit_up_scans s ON st.scan_id = s.id
               WHERE st.sector LIKE ? ESCAPE '\\'
                 AND s.scan_date >= ?
               GROUP BY st.symbol
               ORDER BY scan_count DESC, max_board DESC
               LIMIT ?""",
            (pattern, cutoff, top_n),
        ).fetchall()
        return [dict(r) for r in rows]


def get_breadth_trend(days: int = 30) -> list[dict]:
    """获取市场宽度趋势（日历天数 cutoff，与 get_sector_top 一致）。"""
    init_limit_up_db()
    cutoff = _cutoff_yyyymmdd(days)
    with _connection() as c:
        rows = c.execute(
            """SELECT scan_date, total_unique_stocks, avg_daily_count,
                    days_with_limit_ups, trading_days_scanned,
                    json_extract(breadth_json, '$.consecutive_dist') as consecutive_dist_json,
                    json_extract(breadth_json, '$.seal_quality') as seal_quality_json
               FROM limit_up_scans
               WHERE scan_date >= ?
               ORDER BY scan_date DESC""",
            (cutoff,),
        ).fetchall()
        results: list[dict] = []
        for r in rows:
            d = dict(r)
            for key in ("consecutive_dist_json", "seal_quality_json"):
                raw = d.pop(key, None)
                if isinstance(raw, str):
                    try:
                        d[key.replace("_json", "")] = json.loads(raw)
                    except json.JSONDecodeError:
                        d[key.replace("_json", "")] = None
            results.append(d)
        return results


def get_stats() -> dict:
    """获取 limit-up 存储统计。"""
    init_limit_up_db()
    with _connection() as c:
        ts = c.execute("SELECT COUNT(*) as c FROM limit_up_scans").fetchone()["c"]
        tst = c.execute("SELECT COUNT(*) as c FROM limit_up_stocks").fetchone()["c"]
        first = c.execute(
            "SELECT scan_date FROM limit_up_scans ORDER BY scan_date ASC LIMIT 1"
        ).fetchone()
        last = c.execute(
            "SELECT scan_date FROM limit_up_scans ORDER BY scan_date DESC LIMIT 1"
        ).fetchone()
        return {
            "total_scans": ts,
            "total_stock_records": tst,
            "first_scan_date": first["scan_date"] if first else None,
            "last_scan_date": last["scan_date"] if last else None,
            "db_path": str(_get_path()),
        }


def delete_scan(scan_date: str) -> bool:
    """删除指定日期的扫描记录（含关联股票）。"""
    init_limit_up_db()
    with _connection() as c:
        row = c.execute(
            "SELECT id FROM limit_up_scans WHERE scan_date = ?", (scan_date,)
        ).fetchone()
        if not row:
            return False
        scan_id = row["id"]
        c.execute("DELETE FROM limit_up_stocks WHERE scan_id = ?", (scan_id,))
        cur = c.execute(
            "DELETE FROM limit_up_scans WHERE id = ?", (scan_id,)
        )
        c.commit()
        return cur.rowcount > 0


# ---- 工具函数 ----

def _scan_row_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    for key in ("breadth_json", "enrichment_json", "errors_json", "filter_params_json"):
        raw = d.pop(key, None)
        if isinstance(raw, str):
            try:
                d[key.replace("_json", "")] = json.loads(raw)
            except json.JSONDecodeError:
                d[key.replace("_json", "")] = None
        else:
            d[key.replace("_json", "")] = raw
    return d


def _stock_row_to_dict(row: sqlite3.Row, include_scan_date: bool = False) -> dict:
    d = dict(row)
    for key in ("flags_json", "appearances_json"):
        raw = d.pop(key, None)
        if isinstance(raw, str):
            try:
                d[key.replace("_json", "")] = json.loads(raw)
            except json.JSONDecodeError:
                d[key.replace("_json", "")] = None
        else:
            d[key.replace("_json", "")] = raw
    if include_scan_date and "scan_date" in d:
        pass
    return d
