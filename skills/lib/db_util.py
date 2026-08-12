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


def upsert_daily_rows(
    conn: sqlite3.Connection,
    table: str,
    rows: list[dict],
    *,
    pk: tuple[str, ...],
    merge: bool = False,
    exclude_cols: tuple[str, ...] = (),
) -> int:
    """日快照批量写入（合并 macro_snapshots / market_snapshots / index_pe_history 三处拷贝）。

    - ``merge=False``：``INSERT OR REPLACE``（整行替换，PK 冲突时按现有语义）。
    - ``merge=True``：``INSERT ... ON CONFLICT(pk) DO UPDATE SET``
      ``col=COALESCE(excluded.col, table.col)`` — 逐列合并：新值非 NULL 覆盖，
      新值 NULL 保留旧值。防同一天第二次写入（部分指标 fetch 失败为 None、
      或 7d TTL 缓存旧值）冲掉早先写入的好值。冲突时不更新 PK 列，
      ``collected_at`` 类时间戳列保留首次写入值。
    - ``exclude_cols``：仅 merge=True 时生效——这些列不进入 DO UPDATE SET
      子句（冲突时整列保留表的旧值），但仍参与 INSERT 的 VALUES（全新行写入
      这些列的值）。用于"冲突时保留表中更完整的衍生值、全新行仍写本行值"
      的场景（如 _auto_persist 的 v1 env_label 不覆盖 save_snapshot 的 v2）。

    rows 为 dict 列表，键即列名（各 row 键必须一致）；返回写入行数。
    table/pk 必须为代码控制的字面量，不拼接外部输入。
    """
    if not rows:
        return 0
    columns = list(rows[0].keys())
    assert all(list(r.keys()) == columns for r in rows), "rows must share identical keys"
    ph = ",".join("?" * len(columns))
    cols_sql = ",".join(columns)
    if merge:
        pk_cols = ",".join(pk)
        assign = ",".join(
            f"{col}=COALESCE(excluded.{col}, {table}.{col})"
            for col in columns
            if col not in pk and col not in exclude_cols
        )
        sql = (
            f"INSERT INTO {table} ({cols_sql}) VALUES ({ph}) "
            f"ON CONFLICT({pk_cols}) DO UPDATE SET {assign}"
        )
    else:
        sql = f"INSERT OR REPLACE INTO {table} ({cols_sql}) VALUES ({ph})"
    for row in rows:
        conn.execute(sql, tuple(row.get(col) for col in columns))
    return len(rows)


def load_recent_rows(
    conn: sqlite3.Connection,
    table: str,
    *,
    limit: int,
    order_col: str = "date",
    where: str = "",
    params: tuple = (),
) -> list[dict]:
    """近 N 行按 order_col 降序取后反转（升序），供分位/趋势窗口消费。

    对齐三处历史实现的 ``SELECT * ... ORDER BY {order_col} DESC LIMIT ?
    → reversed`` 模式。table/order_col/where 必须为代码控制的字面量。
    """
    sql = f"SELECT * FROM {table}"
    if where:
        sql += f" WHERE {where}"
    sql += f" ORDER BY {order_col} DESC LIMIT ?"
    rows = conn.execute(sql, (*params, int(limit))).fetchall()
    return [dict(r) for r in reversed(rows)]


def hist_ex_today(history: list[dict], date) -> list[dict]:
    """剔除 history 中与 date 同日（``date`` 或 ``trade_date`` 键）的行，防「今日双计」。

    journal market_microstructure / etf index_pe 在 snapshot→持久化→load_history
    后 history 已含今日刚入库的行，分位/窗口统计前必须剔除自身（cd5e7a4 起
    多轮修补的同型 bug）；双侧 str 归一（DB 读出为 str，内存 dict 可能为其他
    类型）。行缺少两个键时按非同日处理（保留）。
    """
    d = str(date)
    return [h for h in history if str(h.get("date") or h.get("trade_date")) != d]
