"""SQLite 研究记录持久化。支持采集结果存储、查询和统计。

数据库: ~/.local/share/investment/research.db
WAL 模式安全并发。轻量 Schema 迁移。

v0.1.9: thesis 表（假设追踪）
"""

from __future__ import annotations

import json
import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

logger = logging.getLogger(__name__)

from . import env
from .db_util import connect_db, load_recent_rows, safe_close, upsert_daily_rows
from .json_util import dumps_json, json_default
from .nums import safe_float
from .schema import index_dimensions
from .shared_dates import shanghai_now  # 上海时区口径（曾用 UTC，跨时区偏移 8h）

DB_PATH = env.STORE_DB
SCHEMA_VERSION = 1

_db_override: Path | None = None


def _get_path() -> Path:
    return _db_override or DB_PATH


def _conn() -> sqlite3.Connection:
    return connect_db(_get_path())


def _safe_close(c: sqlite3.Connection) -> None:
    safe_close(c, logger=logger)


@contextmanager
def _connection() -> Iterator[sqlite3.Connection]:
    c = _conn()
    try:
        yield c
    finally:
        _safe_close(c)


_SCHEMA_DDL = """
            CREATE TABLE IF NOT EXISTS schema_version (
                version INTEGER PRIMARY KEY, applied_at TEXT DEFAULT (datetime('now')));
            CREATE TABLE IF NOT EXISTS collections (
                id INTEGER PRIMARY KEY, symbol TEXT NOT NULL, name TEXT,
                fetched_at TEXT NOT NULL, dimensions_total INTEGER DEFAULT 0,
                dimensions_ok INTEGER DEFAULT 0, raw_json TEXT,
                kind TEXT NOT NULL DEFAULT 'collect',
                created_at TEXT DEFAULT (datetime('now')));
            CREATE INDEX IF NOT EXISTS idx_c_sym ON collections(symbol);
            CREATE INDEX IF NOT EXISTS idx_c_fa ON collections(fetched_at);
            CREATE TABLE IF NOT EXISTS findings (
                id INTEGER PRIMARY KEY, collection_id INTEGER REFERENCES collections(id),
                symbol TEXT NOT NULL, dimension TEXT NOT NULL, source TEXT,
                confidence TEXT, summary TEXT, created_at TEXT DEFAULT (datetime('now')));
            CREATE INDEX IF NOT EXISTS idx_f_sym ON findings(symbol);
            CREATE TABLE IF NOT EXISTS pipeline_states (
                symbol TEXT NOT NULL,
                step TEXT NOT NULL,
                state_json TEXT,
                completed_at TEXT,
                PRIMARY KEY (symbol, step)
            );
            CREATE TABLE IF NOT EXISTS thesis (
                symbol TEXT PRIMARY KEY,
                assumptions_json TEXT,
                red_lines_json TEXT,
                health_score REAL,
                state TEXT CHECK(state IN ('完整','边际弱化','受损','破裂')),
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS valuations (
                id INTEGER PRIMARY KEY,
                symbol TEXT NOT NULL,
                price REAL, ttm_eps REAL, bvps REAL,
                ttm_pe REAL, pb REAL, rf REAL, erp REAL,
                roe_annualized REAL, ocf_ratio REAL,
                pe_median REAL, pb_median REAL,
                pe_pct REAL, pb_pct REAL,
                bull_low REAL, bull_high REAL,
                base_low REAL, base_high REAL,
                bear_low REAL, bear_high REAL,
                result_json TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            );
            CREATE INDEX IF NOT EXISTS idx_val_sym ON valuations(symbol);
            CREATE TABLE IF NOT EXISTS market_snapshots (
                date TEXT PRIMARY KEY,
                -- Tier 1 原始指标
                margin_balance REAL, margin_buy_amount REAL,
                ad_ratio REAL, limit_up_count INTEGER, limit_down_count INTEGER,
                lu_ld_ratio REAL,
                total_turnover REAL,
                sse_float_mcap REAL, szse_float_mcap REAL,
                -- Tier 2 衍生指标
                margin_to_mcap REAL,
                margin_buy_to_turnover REAL,
                margin_20d_change REAL,
                ad_ratio_5d_ma REAL,
                limit_down_20d_pct REAL,
                -- Tier 3 高级指标
                erp REAL, pcr REAL, below_book_pct REAL,
                -- 资金面 — 北向
                northbound_net_inflow REAL, northbound_direction TEXT, northbound_source TEXT,
                -- 环境标签（JSON）
                env_label TEXT,
                collected_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS etf_share_snapshots (
                date TEXT,
                symbol TEXT,
                shares REAL,
                price REAL,
                aum REAL,
                collected_at TEXT DEFAULT (datetime('now')),
                PRIMARY KEY (date, symbol)
            );
            CREATE TABLE IF NOT EXISTS industry_weekly (
                index_code TEXT NOT NULL,
                index_name TEXT NOT NULL,
                date TEXT NOT NULL,
                pe REAL, pb REAL, chg_pct REAL,
                turnover_pct REAL, dividend_yield REAL,
                mkt_cap REAL,
                PRIMARY KEY (index_code, date)
            );
            CREATE TABLE IF NOT EXISTS index_pe_history (
                index_code TEXT NOT NULL,
                index_name TEXT NOT NULL DEFAULT '',
                date TEXT NOT NULL,
                pe REAL, pe_circulating REAL,
                dividend_yield REAL, dividend_yield_circulating REAL,
                source TEXT DEFAULT 'csindex',
                collected_at TEXT DEFAULT (datetime('now')),
                PRIMARY KEY (index_code, date)
            );
            CREATE TABLE IF NOT EXISTS macro_snapshots (
                date TEXT PRIMARY KEY,
                pmi REAL, cpi REAL, ppi REAL, lpr REAL,
                money_supply REAL, loan REAL,
                vix REAL, sox REAL,
                raw_json TEXT,
                collected_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS market_daily (
                date TEXT NOT NULL,
                ts_code TEXT NOT NULL,
                open REAL, high REAL, low REAL, close REAL,
                pre_close REAL, pct_chg REAL, vol REAL, amount REAL,
                turnover_rate REAL,
                PRIMARY KEY (date, ts_code)
            );
            CREATE INDEX IF NOT EXISTS idx_market_daily_date ON market_daily(date);
            CREATE INDEX IF NOT EXISTS idx_market_daily_code ON market_daily(ts_code);
            CREATE TABLE IF NOT EXISTS futures_daily (
                date TEXT NOT NULL,
                symbol TEXT NOT NULL,          -- IF / IH / IC / IM
                contract TEXT NOT NULL,        -- 当月合约（IF2608.CFX）或 main_continuous（sina 降级）
                open REAL, high REAL, low REAL, close REAL, settle REAL,
                oi REAL, oi_chg REAL,
                basis_pts REAL, basis_pct REAL, oi_change_pct REAL,
                source TEXT DEFAULT 'tushare',
                collected_at TEXT DEFAULT (datetime('now')),
                PRIMARY KEY (date, symbol)
            );
            CREATE INDEX IF NOT EXISTS idx_futures_daily_symbol ON futures_daily(symbol);
"""


def _add_column_if_missing(c: sqlite3.Connection, table: str, col: str, col_type: str) -> None:
    """ALTER ADD COLUMN，duplicate column 幂等跳过（其余异常原样上抛）。"""
    try:
        c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {col_type}")
    except sqlite3.OperationalError as e:
        if "duplicate column" in str(e).lower():
            pass  # 列已存在
        else:
            raise


def _apply_migrations(c: sqlite3.Connection) -> None:
    """历史 schema 迁移（v0.2.2/v0.2.4/v0.2.6），duplicate-column 守卫幂等。"""
    # v0.2.2 迁移：为已有表添加北向资金列
    for col, col_type in [
        ("northbound_net_inflow", "REAL"),
        ("northbound_direction", "TEXT"),
        ("northbound_source", "TEXT"),
    ]:
        _add_column_if_missing(c, "market_snapshots", col, col_type)
    # v0.2.6 迁移：futures 资金面维度（F 系列）
    for col, col_type in [
        ("futures_basis_pct", "REAL"),
        ("futures_oi_change_pct", "REAL"),
    ]:
        _add_column_if_missing(c, "market_snapshots", col, col_type)
    # v0.2.4 迁移：collections.kind（collect/report 快照区分，review #9 第二轮）
    # 旧库行默认 'collect'（report 自动入库前的历史行均为真实采集）
    _add_column_if_missing(c, "collections", "kind", "TEXT NOT NULL DEFAULT 'collect'")


def init_db() -> None:
    with _connection() as c:
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA synchronous=NORMAL")
        c.executescript(_SCHEMA_DDL)
        _apply_migrations(c)
        row = c.execute("SELECT MAX(version) as v FROM schema_version").fetchone()
        if not row or not row["v"]:
            c.execute("INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,))
        c.commit()


def save_collection(result: dict[str, Any], kind: str = "collect") -> int:
    """采集结果入库。kind: 'collect'（显式采集/现场采集）或 'report'（报告快照）。

    kind 用于 diff 自动配对（get_latest_two 优先同 kind），避免同会话
    report+collect 两行互相比较掩盖跨会话变化（review #9 第二轮）。
    """
    init_db()
    symbol = result.get("symbol", "?")
    dims = result.get("dimensions")
    if not isinstance(dims, list):
        dims = []
    sm = result.get("summary", {})
    name = ""
    for d in dims:
        if d.get("dimension") == "basic_info":
            data = d.get("data")
            if isinstance(data, dict):
                name = data.get("name", "")
            break
    c = _conn()
    try:
        cur = c.execute(
            "INSERT INTO collections (symbol,name,fetched_at,dimensions_total,dimensions_ok,raw_json,kind) VALUES (?,?,?,?,?,?,?)",
            (symbol, name, result.get("fetched_at", ""), sm.get("total", 0), sm.get("available", 0),
             dumps_json(result), kind))
        cid = cur.lastrowid
        for d in dims:
            data = d.get("data")
            if isinstance(data, dict):
                # 字典截取安全：只保留前 5 个 key 的值
                small = {k: data[k] for k in list(data.keys())[:5]}
                summary = json.dumps(small, ensure_ascii=False, default=json_default)
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
        _safe_close(c)


# P2-1 v0.2.7: 同会话时间窗口。原实现只跳过 fetched_at 全等行——微秒精度
# 下同会话 collect/report 恒不相等，31 秒前的快照被当「上次调研」
# （batch-review P2-1）。fetched_at 距今 < 10 分钟视为同会话。
# 窗宽权衡（code-review 第五轮）：60 分钟把 09:05/09:31 两次独立会话误并入
# 「至少需要 2 次采集」假报；10 分钟保留重试级重复（秒~分钟）丢弃，跨越
# 10 分钟的两次采集视为真实间隔会话。
SAME_SESSION_WINDOW_MINUTES = 10


def _parse_fetched_at(raw: Any) -> datetime | None:
    """解析 fetched_at ISO 串 → aware UTC；失败返回 None。

    兼容历史变体：'Z' 后缀 / '+HH:MM' 偏移 / naive（按 UTC 假定——存量
    数据全由 _assemble_result 以 UTC 生成，与 shared_dates.fmt_fetched_at
    同一不变式）。SQLite 中 fetched_at 是字符串列，时间比较须在 Python 侧。
    """
    if not isinstance(raw, str):
        return None
    s = raw.strip()
    if not s:
        return None
    if s.endswith(("Z", "z")):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _is_same_session(ts: Any, now: datetime | None = None) -> bool:
    """fetched_at 距今是否处于同会话窗口；解析失败 → False（保守保留不跳过）。"""
    dt = _parse_fetched_at(ts)
    if dt is None:
        return False
    now = now or datetime.now(timezone.utc)
    return (now - dt) < timedelta(minutes=SAME_SESSION_WINDOW_MINUTES)


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
        _safe_close(c)


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
        _safe_close(c)


def clear_all() -> None:
    init_db()
    c = _conn()
    try:
        c.execute("BEGIN")
        c.execute("DELETE FROM findings")
        c.execute("DELETE FROM collections")
        c.execute("DELETE FROM pipeline_states")
        c.commit()
    finally:
        _safe_close(c)


# ---- Pipeline 断点续跑状态 ----

def save_pipeline_step(symbol: str, step: str, state: dict | None = None) -> None:
    """保存流水线步骤状态。"""
    init_db()
    c = _conn()
    try:
        now = shanghai_now().strftime("%Y-%m-%d %H:%M:%S")
        state_json = (
            json.dumps(state, ensure_ascii=False, default=json_default) if state else None
        )
        c.execute(
            "INSERT OR REPLACE INTO pipeline_states (symbol, step, state_json, completed_at) VALUES (?, ?, ?, ?)",
            (symbol, step, state_json, now),
        )
        c.commit()
    finally:
        _safe_close(c)


def load_pipeline_step(symbol: str, step: str) -> dict | None:
    """加载流水线步骤状态。返回 state dict 或 None。"""
    init_db()
    c = _conn()
    try:
        row = c.execute(
            "SELECT state_json, completed_at FROM pipeline_states WHERE symbol = ? AND step = ?",
            (symbol, step),
        ).fetchone()
        if row is None:
            return None
        result: dict = {"completed_at": row["completed_at"]}
        if row["state_json"]:
            try:
                result["state"] = json.loads(row["state_json"])
            except (json.JSONDecodeError, TypeError):
                result["state"] = {}
        else:
            result["state"] = {}
        return result
    finally:
        _safe_close(c)


def get_pipeline_progress(symbol: str) -> dict[str, bool]:
    """获取某 symbol 的流水线进度。返回 {step: completed}。"""
    init_db()
    c = _conn()
    try:
        rows = c.execute(
            "SELECT step, completed_at FROM pipeline_states WHERE symbol = ?",
            (symbol,),
        ).fetchall()
        return {row["step"]: row["completed_at"] is not None for row in rows}
    finally:
        _safe_close(c)


# ---- Diff 快照对比 ----

def get_collection(collection_id: int) -> dict | None:
    """按 ID 获取单次采集的完整 raw_json。"""
    init_db()
    c = _conn()
    try:
        row = c.execute(
            "SELECT id, symbol, name, fetched_at, raw_json FROM collections WHERE id=?",
            (collection_id,)).fetchone()
        if not row:
            return None
        d = dict(row)
        raw = d["raw_json"]
        if isinstance(raw, str):
            try:
                d["raw_json"] = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                logger.warning("get_collection(%d): raw_json parse failed, returning raw string", collection_id)
        else:
            d["raw_json"] = raw
        return d
    finally:
        _safe_close(c)


def get_latest_two(symbol: str) -> tuple[dict, dict] | None:
    """获取指定股票最近两次采集记录。

    优先比较两次 kind='collect' 的真实采集（report/watchlist 快照可能
    与 collect 同会话、互相掩盖跨会话变化，review #9 第二轮）；仅当
    symbol 无任何 collect 行（纯 report 用户）才回退全部 kind。

    注意：collect 恰好 1 条时不再回退混入 report 行——新用户同会话
    collect --store + report 会得到 [report, collect] 的同会话配对，
    diff 恒显「几乎无变化」，掩盖真实跨会话变化（review #9 移除过的
    自我比较问题在回退路径复现，code-review 第三轮）；须到第二次
    collect 会话才有配对。

    v0.2.7 P2-1：行序基础上再按 60 分钟同会话窗口过滤——同会话多次
    collect（31 秒间隔）也会被配对，过滤后回到「上次调研会话」语义。
    P2-1 修正（code-review 第四轮）：窗口锚定最新行而非 now()——锚定 now()
    时「采集后立即 diff」会把最新快照自身排除（配对退化/None），见函数内注释。

    Returns:
        (older, newer) tuple，仅 1 条记录时返回 None。
    """
    init_db()
    c = _conn()
    try:
        # 两阶段查询（code-review 第五轮）：先按轻量列（id/fetched_at）过滤
        # 窗口——LIMIT 200 防窗口内密集行被 50 条截断；窗内只剩 ≤2 行后
        # 才按 id 取 raw_json BLOB（原实现 LIMIT 50 × 全量 BLOB 反序列化）。
        meta = c.execute(
            "SELECT id, fetched_at FROM collections "
            "WHERE symbol=? AND kind='collect' ORDER BY fetched_at DESC LIMIT 200",
            (symbol,)).fetchall()
        if len(meta) == 0:
            # 旧库/纯 report 用户兼容：回退全部 kind（仅限无 collect 行，
            # 1 条 collect + 同会话 report 混排会复现自我比较问题）
            meta = c.execute(
                "SELECT id, fetched_at FROM collections "
                "WHERE symbol=? ORDER BY fetched_at DESC LIMIT 200",
                (symbol,)).fetchall()
        # 窗口锚定最新行而非 datetime.now()（code-review 第四轮修正）：
        # 锚定 now() 时，「采集后立即 diff」会把最新快照自身排除 → 配对退化或
        # None（实测场景：Tue 09:05 采集、09:06 diff，Tue 行被排 → cmd_diff
        # 报「至少需要 2 次采集」，尽管已有 2 次跨会话采集）。锚定最新行仅剔除
        # 其 10 分钟前的同会话重复行（同会话多次 collect 只留最后一条），
        # 最新行恒为 newer 侧。
        if meta:
            anchor = _parse_fetched_at(meta[0]["fetched_at"])
        else:
            anchor = None
        if anchor is None:
            if meta:
                # 解析失败不静默：与 _parse_fetched_at 其它消费方一致，守卫
                # 失效必须有日志（fetch 时间形态异常 = 数据层问题）
                logger.warning(
                    "get_latest_two(%s): 最新行 fetched_at 无法解析（%r），窗口过滤跳过",
                    symbol, meta[0]["fetched_at"])
            room = meta
        else:
            room = [meta[0]]
            for r in meta[1:]:
                dt_r = _parse_fetched_at(r["fetched_at"])
                if dt_r is None or (anchor - dt_r) >= timedelta(minutes=SAME_SESSION_WINDOW_MINUTES):
                    room.append(r)
        if len(room) < 2:
            return None

        def _fetch_full(rid: int) -> dict | None:
            row = c.execute("SELECT * FROM collections WHERE id=?", (rid,)).fetchone()
            return dict(row) if row is not None else None

        newer = _fetch_full(room[0]["id"])
        older = _fetch_full(room[1]["id"])
        for d in (newer, older):
            if d is None:
                logger.warning("get_latest_two(%s): row vanished between queries", symbol)
                return None
            raw = d.get("raw_json")
            if isinstance(raw, str):
                try:
                    d["raw_json"] = json.loads(raw)
                except (json.JSONDecodeError, TypeError):
                    logger.warning("get_latest_two(%s): raw_json parse failed, returning raw string", symbol)
        return (older, newer)
    finally:
        _safe_close(c)


def diff_collections(old: dict, new: dict) -> dict:
    """对比两次采集结果，生成结构化 diff。

    Args:
        old: 较早的 collection（含 raw_json 或本身就是 raw_json）
        new: 较新的 collection

    Returns:
        dict 含 changed, unchanged, skipped 列表。
    """
    old_raw = _unwrap_raw_json(old)
    new_raw = _unwrap_raw_json(new)

    old_dims = _index_dims(old_raw)
    new_dims = _index_dims(new_raw)

    old_id = old.get("id", old_raw.get("id"))
    new_id = new.get("id", new_raw.get("id"))
    old_at = old.get("fetched_at", old_raw.get("fetched_at", ""))
    new_at = new.get("fetched_at", new_raw.get("fetched_at", ""))
    symbol = new_raw.get("symbol", old_raw.get("symbol", "?"))

    changed: list[dict] = []
    unchanged: list[dict] = []
    skipped: list[dict] = []

    all_dims = sorted(set(list(old_dims.keys()) + list(new_dims.keys())))

    for dn in all_dims:
        od = old_dims.get(dn)
        nd = new_dims.get(dn)

        if od is None:
            skipped.append({"dimension": dn, "reason": "旧快照不含此维度"})
            continue
        if nd is None:
            skipped.append({"dimension": dn, "reason": "新快照不含此维度"})
            continue

        o_data = od.get("data")
        n_data = nd.get("data")

        if o_data is None and n_data is None:
            skipped.append({"dimension": dn, "reason": "两端均无数据"})
            continue
        if o_data is None:
            skipped.append({"dimension": dn, "reason": "旧快照数据为空"})
            continue
        if n_data is None:
            skipped.append({"dimension": dn, "reason": "新快照数据为空"})
            continue

        # 按数据类型 diff
        dim_changes = _diff_data(dn, o_data, n_data)
        changed.extend(dim_changes)

        # 未变化的维度
        # 简单标记（避免输出过大）
        if not dim_changes:
            unchanged.append({"dimension": dn, "display": nd.get("display", dn)})

    return {
        "symbol": symbol,
        "old_id": old_id,
        "new_id": new_id,
        "old_at": old_at,
        "new_at": new_at,
        "changed": changed,
        "unchanged": [u["dimension"] for u in unchanged],
        "skipped": skipped,
    }


def _unwrap_raw_json(record: dict) -> dict:
    """从 collection 行或裸 raw_json dict 提取可 diff 的 payload。"""
    if not isinstance(record, dict):
        return {}
    raw = record.get("raw_json")
    if isinstance(raw, dict):
        return raw
    if "dimensions" in record:
        return record
    return {}


def _index_dims(raw: dict) -> dict[str, dict]:
    """将 raw_json 中的 dimensions 列表转为 dict。委托 schema.index_dimensions。"""
    return index_dimensions(raw)


def _dim_data(raw: dict, name: str) -> Any:
    d = _index_dims(raw).get(name)
    return d.get("data") if d else None


def _yoy_from_fina_rows(rows: list[dict], field: str) -> float | None:
    from lib.financials import normalize_end_date, prior_year_end_date

    if not rows:
        return None
    sorted_rows = sorted(rows, key=lambda r: str(r.get("end_date", "")))
    latest = sorted_rows[-1]
    cur = latest.get(field)
    if cur is None:
        return None
    try:
        cur_f = float(cur)
    except (TypeError, ValueError):
        return None
    if cur_f <= 0:
        return None
    ed = str(latest.get("end_date", ""))
    norm_ed = normalize_end_date(ed)
    if len(norm_ed) < 8:
        return None
    prev_ed = prior_year_end_date(norm_ed)
    prev_v = None
    for r in reversed(sorted_rows[:-1]):
        if normalize_end_date(str(r.get("end_date", ""))) == prev_ed:
            try:
                prev_v = float(r.get(field))
            except (TypeError, ValueError):
                logger.debug("unparseable %s=%s for %s, trying next record", field, r.get(field), r.get("end_date", ""))
                continue
            break  # Found a valid value
    if prev_v is None or prev_v <= 0:
        return None
    return round((cur_f - prev_v) / prev_v * 100, 2)


def _events_count_from_summary(summary: dict) -> int:
    """从 events_summary 或快照 events 块读取窗口内事件数。"""
    if not summary:
        return 0
    if "event_count" in summary:
        return int(summary["event_count"])
    days = summary.get("window_days", 30)
    return int(summary.get(f"count_{days}d", summary.get("count_30d", 0)))


def extract_key_snapshot(raw: dict) -> dict:
    """从采集 raw_json 提取高信号关键字段快照（on-the-fly，不落库）。"""
    body = raw.get("raw_json", raw) if isinstance(raw, dict) else {}
    snap: dict[str, Any] = {
        "symbol": body.get("symbol", "?"),
        "fetched_at": body.get("fetched_at", ""),
        "valuation": {},
        "financials": {},
        "capital_flow": {},
        "technical": {},
        "risk": {},
    }

    val_data = _dim_data(body, "valuation")
    if isinstance(val_data, dict):
        for k in ("pe_pct", "pb_pct", "pe_ttm", "pb"):
            if val_data.get(k) is not None:
                snap["valuation"][k] = val_data[k]
    elif isinstance(val_data, list) and val_data:
        from lib.technical import sort_kline_asc
        from lib.valuation import valuation_summary, valuation_window_label
        vs = sort_kline_asc(val_data)
        summary = valuation_summary(
            [r.get("pe_ttm") for r in vs], [r.get("pb") for r in vs],
            window_label=valuation_window_label(len(vs)),
        )
        pe, pb = summary.get("pe", {}), summary.get("pb", {})
        if pe.get("pct") is not None:
            snap["valuation"]["pe_pct"] = pe["pct"]
        if pb.get("pct") is not None:
            snap["valuation"]["pb_pct"] = pb["pct"]
        if pe.get("current") is not None:
            snap["valuation"]["pe_ttm"] = pe["current"]
        if pb.get("current") is not None:
            snap["valuation"]["pb"] = pb["current"]

    fin = _dim_data(body, "financials")
    if isinstance(fin, list) and fin:
        latest = sorted(fin, key=lambda r: str(r.get("end_date", "")))[-1]
        if latest.get("roe") is not None:
            snap["financials"]["roe"] = latest["roe"]
        ry = _yoy_from_fina_rows(fin, "revenue")
        if ry is not None:
            snap["financials"]["revenue_yoy"] = ry
        npy = _yoy_from_fina_rows(fin, "net_profit")
        if npy is not None:
            snap["financials"]["net_profit_yoy"] = npy

    ms = body.get("market_structure") or {}
    nb = ms.get("northbound")
    if not isinstance(nb, dict):
        nb = _dim_data(body, "northbound")
    if isinstance(nb, dict) and nb.get("net_sum_10d") is not None:
        snap["capital_flow"]["northbound_net"] = nb["net_sum_10d"]

    margin = ms.get("margin")
    if not isinstance(margin, dict):
        margin = _dim_data(body, "margin")
    if isinstance(margin, dict):
        recs = margin.get("records")
        if isinstance(recs, list) and recs:
            bal = recs[-1].get("rzye")
            if bal is not None:
                snap["capital_flow"]["margin_balance"] = bal

    kline = _dim_data(body, "kline")
    if isinstance(kline, list) and kline:
        from lib.technical import compute
        tech = compute(kline)
        trend = tech.get("trend") or {}
        if trend.get("alignment", {}).get("trend_label"):
            snap["technical"]["ma_alignment"] = trend["alignment"]["trend_label"]
        rsi_map = (tech.get("overbought_oversold") or {}).get("rsi") or {}
        for period in ("6", "12", "24"):
            rv = rsi_map.get(period, {}).get("value")
            if rv is not None:
                snap["technical"]["rsi"] = rv
                break

    risk = body.get("risk_scan") or body.get("risk_data")
    if isinstance(risk, dict):
        snap["risk"]["triggered_count"] = risk.get("triggered_count", 0)
        signals = risk.get("signals") or []  # .get() 在 key 存在但值为 null 时返回 None
        triggered = [s.get("id") for s in signals if s.get("triggered")]
        snap["risk"]["triggered_signals"] = triggered

    # Events
    events_summary = body.get("_meta", {}).get("events_summary", {})
    if events_summary:
        snap["events"] = {
            "event_count": _events_count_from_summary(events_summary),
            "window_days": events_summary.get("window_days", 30),
            "latest_date": events_summary.get("latest_date"),
            "top_types": events_summary.get("top_types", []),
        }

    return snap


_KEY_DIFF_ALWAYS = frozenset({
    "pe_pct", "pb_pct", "ma_alignment", "triggered_count", "triggered_signals",
})
_KEY_DIFF_THRESHOLD_PCT = 1.0

CATEGORY_LABELS = {
    "valuation": "估值",
    "financials": "财务",
    "capital_flow": "资金",
    "technical": "技术",
    "risk": "风险",
}


def format_key_diff_markdown_lines(key_diff: dict) -> list[str]:
    """将 diff_key_snapshots 结果格式化为 Markdown 列表行。"""
    categories = key_diff.get("categories") or {}
    if not categories:
        return ["- 关键字段无显著变化"]
    lines: list[str] = []
    for cat, items in categories.items():
        label = CATEGORY_LABELS.get(cat, cat)
        for item in items:
            field = item.get("field", "?")
            old_v, new_v = item.get("old"), item.get("new")
            pct = item.get("pct")
            pct_str = f" ({pct:+.1f}%)" if pct is not None else ""
            lines.append(f"- **{label}** {field}: {old_v} → {new_v}{pct_str}")
    return lines


def load_key_diff_vs_stored(symbol: str, current: dict) -> dict | None:
    """对比当前采集与 store 中最新快照的关键字段变化（供报告模块 1 使用）。

    跳过 fetched_at 距今 10 分钟内的行（同会话窗口）——否则「相对上次调研
    变化」比较的是几分钟前的同会话快照，恒无变化（review #9 第二轮残留：
    原实现只跳过 fetched_at 全等行，微秒精度下同会话两次采集恒不相等）。
    另一路跳过：fetched_at 与 current 全等（--resume 恢复的就是最新 stored
    行，仅靠窗口守卫时该行陈旧到窗口外 → 自比较成幻影「无显著变化」，
    code-review 第五轮恢复全等跳过）。
    """
    rows = list_collections(limit=20, symbol=symbol)
    prev = None
    cur_at = current.get("fetched_at") if isinstance(current, dict) else None
    for row in rows:
        if cur_at is not None and row.get("fetched_at") == cur_at:
            continue  # 当前快照自身（--resume 恢复行的自比较防御）
        if _is_same_session(row.get("fetched_at")):
            continue  # 同会话行（10 分钟窗口）
        prev = get_collection(row["id"])
        if prev:
            break
    if not prev:
        return None
    return diff_key_snapshots(prev, current)


def _key_field_changed(field: str, old: Any, new: Any) -> bool:
    if old == new:
        return False
    if field in _KEY_DIFF_ALWAYS:
        return True
    if isinstance(old, (int, float)) and isinstance(new, (int, float)):
        if old == 0:
            return new != 0
        return abs((new - old) / abs(old) * 100) >= _KEY_DIFF_THRESHOLD_PCT
    return True


def diff_key_snapshots(old_raw: dict, new_raw: dict) -> dict:
    """对比两次采集的关键字段快照，按类别输出变化。"""
    old_snap = extract_key_snapshot(old_raw)
    new_snap = extract_key_snapshot(new_raw)
    categories: dict[str, list[dict]] = {}
    unchanged: list[str] = []

    for cat in ("valuation", "financials", "capital_flow", "technical", "risk"):
        o_cat, n_cat = old_snap.get(cat, {}), new_snap.get(cat, {})
        all_fields = sorted(set(list(o_cat.keys()) + list(n_cat.keys())))
        cat_changes: list[dict] = []
        for field in all_fields:
            ov, nv = o_cat.get(field), n_cat.get(field)
            if not _key_field_changed(field, ov, nv):
                unchanged.append(f"{cat}.{field}")
                continue
            change: dict[str, Any] = {"field": field, "old": ov, "new": nv}
            if isinstance(ov, (int, float)) and isinstance(nv, (int, float)) and ov != 0:
                change["pct"] = round((nv - ov) / abs(ov) * 100, 2)
            cat_changes.append(change)
        if cat_changes:
            categories[cat] = cat_changes

    # Events comparison
    old_events = old_snap.get("events") or {}
    new_events = new_snap.get("events") or {}
    events_diff: dict[str, Any] | None = None
    if old_events or new_events:
        old_window = old_events.get("window_days", 30)
        new_window = new_events.get("window_days", 30)
        count_change = 0
        if old_window == new_window:
            old_count = _events_count_from_summary(old_events)
            new_count = _events_count_from_summary(new_events)
            count_change = new_count - old_count

        window_days_changed: dict[str, int] | None = None
        if old_window != new_window:
            window_days_changed = {"old": old_window, "new": new_window}

        old_types = {t.get("type", "") for t in old_events.get("top_types", []) if t.get("type")}
        new_types = {t.get("type", "") for t in new_events.get("top_types", []) if t.get("type")}
        added_types = sorted(new_types - old_types)
        removed_types = sorted(old_types - new_types)

        if count_change != 0 or added_types or removed_types or window_days_changed:
            events_diff = {
                "count_change": count_change,
                "new_types": added_types,
                "removed_types": removed_types,
            }
            if window_days_changed:
                events_diff["window_days_changed"] = window_days_changed

    return {
        "symbol": new_snap.get("symbol", old_snap.get("symbol", "?")),
        "old_at": old_snap.get("fetched_at", ""),
        "new_at": new_snap.get("fetched_at", ""),
        "categories": categories,
        "unchanged": unchanged,
        "events": events_diff,
    }


# ═══════════════════════════════════════════════════════════════
# Valuations (v0.2.0 — 科学估值持久化)
# ═══════════════════════════════════════════════════════════════

def save_valuation(result: dict) -> int:
    """保存估值结果到 valuations 表。

    Args:
        result: ValuationResult.to_dict() 的输出

    Returns:
        新插入的 id
    """
    init_db()
    sc = result.get("scenarios", {})
    sc_data = sc.get("scenarios", {}) if isinstance(sc, dict) else {}

    def _sc_range(key):
        s = sc_data.get(key, {})
        m = s.get("methods", {}) if isinstance(s, dict) else {}
        prices = [
            p for p in [m.get("price_pe"), m.get("price_pb"), m.get("price_earnings_yield")]
            if isinstance(p, (int, float)) and p < 99999
        ]
        return (min(prices), max(prices)) if prices else (None, None)

    bull_low, bull_high = _sc_range("bull")
    base_low, base_high = _sc_range("base")
    bear_low, bear_high = _sc_range("bear")

    # 从 ttm/bvps_data/percentile 提取结构化字段
    ttm = result.get("ttm", {}) or {}
    bv = result.get("bvps_data", {}) or {}
    roe = result.get("roe_data", {}) or {}
    ocf = result.get("ocf_quality", {}) or {}
    pct = result.get("percentile", {}) or {}

    c = _conn()
    try:
        cur = c.execute(
            """INSERT INTO valuations
               (symbol, price, ttm_eps, bvps, ttm_pe, pb, rf, erp,
                roe_annualized, ocf_ratio,
                pe_median, pb_median, pe_pct, pb_pct,
                bull_low, bull_high, base_low, base_high, bear_low, bear_high,
                result_json)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                result.get("symbol", "?"),
                result.get("price"),
                ttm.get("ttm_eps"),
                bv.get("bvps"),
                result.get("price") / ttm["ttm_eps"] if result.get("price") is not None and ttm.get("ttm_eps") is not None and ttm["ttm_eps"] != 0 else None,
                result.get("price") / bv["bvps"] if result.get("price") is not None and bv.get("bvps") is not None and bv["bvps"] != 0 else None,
                result.get("rf_china_10y"),
                result.get("erp"),
                roe.get("roe_annualized"),
                ocf.get("ocf_np_ratio"),
                pct.get("pe_median"),
                pct.get("pb_median"),
                pct.get("pe_pct"),
                pct.get("pb_pct"),
                bull_low, bull_high,
                base_low, base_high,
                bear_low, bear_high,
                dumps_json(result),
            ),
        )
        cid = cur.lastrowid
        c.commit()
        return cid
    finally:
        _safe_close(c)


def list_valuations(symbol: str | None = None, limit: int = 20) -> list[dict]:
    """列出估值历史记录。

    Args:
        symbol: 股票代码，None 表示全部
        limit: 最大返回条数
    """
    init_db()
    c = _conn()
    try:
        if symbol:
            rows = c.execute(
                "SELECT id, symbol, price, ttm_eps, bvps, ttm_pe, pb, rf, erp, "
                "roe_annualized, ocf_ratio, pe_pct, pb_pct, "
                "bull_low, bull_high, base_low, base_high, bear_low, bear_high, "
                "created_at FROM valuations WHERE symbol=? ORDER BY created_at DESC LIMIT ?",
                (symbol, limit),
            ).fetchall()
        else:
            rows = c.execute(
                "SELECT id, symbol, price, ttm_eps, bvps, ttm_pe, pb, rf, erp, "
                "roe_annualized, ocf_ratio, pe_pct, pb_pct, "
                "bull_low, bull_high, base_low, base_high, bear_low, bear_high, "
                "created_at FROM valuations ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        _safe_close(c)


def _diff_data(dimension: str, old_data: Any, new_data: Any) -> list[dict]:
    """递归对比两个维度的 data，返回变化列表。"""
    changes: list[dict] = []

    if isinstance(old_data, dict) and isinstance(new_data, dict):
        all_keys = set(list(old_data.keys()) + list(new_data.keys()))
        for key in sorted(all_keys):
            ov = old_data.get(key)
            nv = new_data.get(key)
            if ov != nv:
                change = {
                    "path": f"{dimension}.{key}",
                    "old": ov,
                    "new": nv,
                }
                # 数值型计算百分比变化
                if isinstance(ov, (int, float)) and isinstance(nv, (int, float)) and ov != 0:
                    pct = (nv - ov) / abs(ov) * 100
                    change["pct"] = round(pct, 2)
                changes.append(change)

    elif isinstance(old_data, list) and isinstance(new_data, list):
        # 列表对比：用最后一条记录（最新）或逐条对比
        if old_data and new_data:
            # 尝试用 trade_date/end_date 对齐
            old_by_date = _index_by_date(old_data)
            new_by_date = _index_by_date(new_data)

            if old_by_date and new_by_date:
                # 对齐后对比
                common_dates = set(old_by_date.keys()) & set(new_by_date.keys())
                for date_key in sorted(common_dates):
                    sub = _diff_data(f"{dimension}[{date_key}]",
                                    old_by_date[date_key], new_by_date[date_key])
                    changes.extend(sub)
                # 新增的日期
                new_dates = set(new_by_date.keys()) - set(old_by_date.keys())
                if new_dates:
                    changes.append({
                        "path": f"{dimension}",
                        "description": f"新增 {len(new_dates)} 条记录",
                        "new_dates": sorted(new_dates)[-5:],
                    })
            else:
                # 无法按日期对齐（缺少 trade_date/end_date 字段），
                # 跳过按位置对比，避免将不相关时期误报为差异
                if len(new_data) != len(old_data):
                    changes.append({
                        "path": f"{dimension}",
                        "description": f"记录数变化: {len(old_data)} -> {len(new_data)}（无法按日期对齐）",
                    })

    return changes


def _index_by_date(data: list[dict]) -> dict[str, dict]:
    """尝试用 trade_date 或 end_date 索引列表。

    对 shareholders 等同一日期有多条记录的维度，使用 holder_name 或序号构建复合键，
    避免静默覆盖（H2 修复）。同名同日期多条经 _unique_key 递增后缀区分，杜绝
    复合键二次碰撞（review fix #8）。输入先按 (date, holder, 内容) 稳定排序，
    保证同名同日期记录跨快照键一致，diff 不报假变化（review fix #12）。
    """
    def _unique_key(key: str, used: set[str]) -> str:
        candidate = key
        n = 2
        while candidate in used:
            candidate = f"{key}_{n}"
            n += 1
        used.add(candidate)
        return candidate

    items = [it for it in data if isinstance(it, dict)]
    items.sort(key=lambda r: (
        str(r.get("trade_date") or r.get("end_date") or ""),
        str(r.get("holder_name") or ""),
        tuple(sorted(f"{k}={r.get(k)}" for k in r)),  # 内容稳定序
    ))

    result: dict[str, dict] = {}
    composite_dates: set[str] = set()  # 已转为复合键的日期，避免第 3+ 条覆盖
    used: set[str] = set()  # 所有已占用 key，保证同名同日期记录不互相覆盖
    for i, item in enumerate(items):
        base_key = item.get("trade_date") or item.get("end_date") or str(i)
        holder = item.get("holder_name")
        # 检查是否已有同键记录，或该日期已转入复合键模式
        if base_key in result or base_key in composite_dates:
            if base_key in result:
                existing = result.pop(base_key)
                eh = existing.get("holder_name")
                suffix = eh if eh else "0"
                result[_unique_key(f"{base_key}_{suffix}", used)] = existing
                composite_dates.add(base_key)
            if holder:
                base_key = f"{base_key}_{holder}"
            else:
                base_key = f"{base_key}_{i}"
        result[_unique_key(str(base_key), used)] = item
    return result


# ---- v0.1.9: thesis tracker ----

# E4 (v0.2.7): invalidated_at / triggered_at 为 CLI --invalidate / --trigger-redline
# 写入时的日期戳（YYYY-MM-DD，上海口径），存放于 JSON 结构内（schema 无独立列）。
# 存量数据可能缺该字段，读取方必须 .get() 防御，展示为「日期未记录」。
_DEFAULT_ASSUMPTIONS = [
    {"id": "a1", "statement": "盈利增速可持续", "confidence": 0.7, "last_check_date": None, "valid": True, "invalidated_at": None},
    {"id": "a2", "statement": "行业景气维持", "confidence": 0.6, "last_check_date": None, "valid": True, "invalidated_at": None},
    {"id": "a3", "statement": "估值溢价有基本面支撑", "confidence": 0.5, "last_check_date": None, "valid": True, "invalidated_at": None},
]

_DEFAULT_RED_LINES = [
    {"id": "r1", "condition": "单季营收同比 < -10%", "triggered": False, "triggered_at": None},
    {"id": "r2", "condition": "毛利率同比降 > 5pp", "triggered": False, "triggered_at": None},
]


def _thesis_health(assumptions: list[dict], red_lines: list[dict]) -> tuple[float, str]:
    a_total = len(assumptions) or 1
    a_valid = sum(1 for a in assumptions if a.get("valid", True))
    r_total = len(red_lines) or 1
    r_triggered = sum(1 for r in red_lines if r.get("triggered"))
    score = a_valid / a_total * 0.6 + (1 - r_triggered / r_total) * 0.4
    if score >= 0.75:
        state = "完整"
    elif score >= 0.55:
        state = "边际弱化"
    elif score >= 0.35:
        state = "受损"
    else:
        state = "破裂"
    return round(score, 3), state


def thesis_init(symbol: str) -> dict[str, Any]:
    init_db()
    assumptions = [dict(a) for a in _DEFAULT_ASSUMPTIONS]
    red_lines = [dict(r) for r in _DEFAULT_RED_LINES]
    score, state = _thesis_health(assumptions, red_lines)
    c = _conn()
    try:
        c.execute(
            """INSERT OR REPLACE INTO thesis
               (symbol, assumptions_json, red_lines_json, health_score, state, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (symbol, dumps_json(assumptions), dumps_json(red_lines), score, state,
             shanghai_now().strftime("%Y-%m-%d %H:%M:%S")),
        )
        c.commit()
    finally:
        _safe_close(c)
    return {"symbol": symbol, "health_score": score, "state": state, "action": "init"}


def thesis_get(symbol: str) -> dict[str, Any] | None:
    init_db()
    c = _conn()
    try:
        row = c.execute("SELECT * FROM thesis WHERE symbol=?", (symbol,)).fetchone()
        if not row:
            return None
        try:
            assumptions = json.loads(row["assumptions_json"] or "[]")
        except (json.JSONDecodeError, TypeError):
            logger.warning("get_thesis(%s): assumptions_json parse failed", symbol)
            assumptions = []
        try:
            red_lines = json.loads(row["red_lines_json"] or "[]")
        except (json.JSONDecodeError, TypeError):
            logger.warning("get_thesis(%s): red_lines_json parse failed", symbol)
            red_lines = []
        return {
            "symbol": row["symbol"],
            "assumptions": assumptions,
            "red_lines": red_lines,
            "health_score": row["health_score"],
            "state": row["state"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
    finally:
        _safe_close(c)


def thesis_update(symbol: str, assumptions: list[dict] | None = None,
                  red_lines: list[dict] | None = None) -> dict[str, Any]:
    existing = thesis_get(symbol)
    if not existing:
        return thesis_init(symbol)
    a = assumptions if assumptions is not None else existing["assumptions"]
    r = red_lines if red_lines is not None else existing["red_lines"]
    score, state = _thesis_health(a, r)
    c = _conn()
    try:
        c.execute(
            """UPDATE thesis SET assumptions_json=?, red_lines_json=?,
               health_score=?, state=?, updated_at=? WHERE symbol=?""",
            (dumps_json(a), dumps_json(r), score, state,
             shanghai_now().strftime("%Y-%m-%d %H:%M:%S"), symbol),
        )
        c.commit()
    finally:
        _safe_close(c)
    return {"symbol": symbol, "health_score": score, "state": state, "action": "update"}


# ---------------------------------------------------------------------------
# 宏观日快照（macro_snapshots 表，v0.2.4）
#
# 放置于 store.py 而非 journal lib：journal 侧 db.py 直连 env.STORE_DB，
# 不 honor _db_override，写入若在 journal 侧将无法测试隔离（污染真实库）。
# 日期用上海口径（宏观指标无交易日概念，LPR/PMI 月度、VIX/SOX 日频，
# 非交易日也写入，与 market_snapshots 的交易日跳过策略不同）。
# ---------------------------------------------------------------------------

_MACRO_INDICATOR_KEYS = ("pmi", "cpi", "ppi", "lpr", "money_supply", "loan", "vix", "sox")


def _macro_safe_float(v: Any) -> float | None:
    """宏观指标值转 float；dict 形态（{value, source, signal}）取 value。

    数值语义委托 lib.nums.safe_float（None / NaN / ±inf / 非数字 → None；
    NaN 若写入 sqlite 会绑定为 NULL，穿透到分位计算会污染序列）。
    """
    if isinstance(v, dict):
        v = v.get("value")
    return safe_float(v)


def _merge_macro_raw_json(c: sqlite3.Connection, date: str, indicators: dict) -> dict:
    """同日 raw_json 按指标键粒度预合并：本次成功重取的键用新信封，
    未成功（None/NaN/不可解析，与数值列 COALESCE 同判定）的键保留旧信封。

    背景：dumps_json 对非空 indicators 恒返回非 NULL 字符串，upsert 的
    COALESCE(excluded.raw_json, table.raw_json) 恒取新值——同日部分写入
    （失败键为 None）会把完整溯源信封整块覆盖，vix/sox 的 source/signal
    永久丢失（数值列因逐列 COALESCE 幸存）。此处按键合并使 raw_json 与
    数值列保持一致（code-review 第三轮）。
    """
    merged = dict(indicators)
    existing = c.execute(
        "SELECT raw_json FROM macro_snapshots WHERE date=?", (date,)).fetchone()
    if not existing or not existing["raw_json"]:
        return merged
    try:
        old_raw = json.loads(existing["raw_json"])
    except (json.JSONDecodeError, TypeError):
        return merged
    if not isinstance(old_raw, dict):
        return merged
    for k in _MACRO_INDICATOR_KEYS:
        if _macro_safe_float(indicators.get(k)) is None and k in old_raw:
            merged[k] = old_raw[k]
    return merged


def save_macro_snapshot(macro_context: dict) -> str | None:
    """宏观指标日快照入库（by date 幂等，按指标键合并写）。

    兼容两种形态：collector 结果 ``{"indicators": {...}}`` 或
    ``collect_macro_context`` 原样 ``{key: {value, source, signal}}``。
    全部指标无值 → 返回 None 不写。返回写入日期（上海口径 YYYYMMDD）。

    合并语义：同一天第二次写入（部分指标 fetch 失败为 None、或 7d TTL
    缓存旧值）不整行覆盖——新值非 NULL 覆盖，NULL 保留旧值，避免冲掉
    早先写入的好值（v0.2.4 review #2）；raw_json 溯源信封按指标键同步
    合并（_merge_macro_raw_json，code-review 第三轮）。
    """
    if not isinstance(macro_context, dict) or not macro_context:
        return None
    indicators = macro_context.get("indicators")
    if not isinstance(indicators, dict) or not indicators:
        indicators = macro_context
    row = {k: _macro_safe_float(indicators.get(k)) for k in _MACRO_INDICATOR_KEYS}
    if all(v is None for v in row.values()):
        return None
    date = shanghai_now().strftime("%Y%m%d")
    init_db()  # 全新库/旧库上建表（review #1：此前缺失导致 journal 首启静默丢快照）
    c = _conn()
    try:
        raw = _merge_macro_raw_json(c, date, indicators)
        upsert_daily_rows(
            c, "macro_snapshots",
            [{"date": date, **row, "raw_json": dumps_json(raw)}],
            pk=("date",), merge=True,
        )
        c.commit()
    finally:
        _safe_close(c)
    return date


def load_macro_history(days: int = 365) -> list[dict]:
    """macro_snapshots 近 N 日记录，按 date ASC（宏快照的读路径，供调用方按需消费）。"""
    init_db()
    c = _conn()
    try:
        return load_recent_rows(c, "macro_snapshots", limit=int(days))
    except sqlite3.OperationalError:
        return []  # 表不可用（DB 被替换等）→ 空历史而非抛异常
    finally:
        _safe_close(c)


def save_futures_daily(rows: list[dict]) -> int:
    """futures_daily 批量写入（v0.2.6 F 系列数据层）。merge=True 幂等。"""
    if not rows:
        return 0
    init_db()
    c = _conn()
    try:
        n = upsert_daily_rows(c, "futures_daily", rows, pk=("date", "symbol"), merge=True)
        c.commit()
        return n
    finally:
        _safe_close(c)


def clear_futures_daily() -> int:
    """清空 futures_daily（--force 全量重建用）。返回删除行数。"""
    init_db()
    c = _conn()
    try:
        n = c.execute("DELETE FROM futures_daily").rowcount
        c.commit()
        return n
    finally:
        _safe_close(c)


def load_futures_daily(symbol: str | None = None, limit: int = 5000) -> list[dict]:
    """futures_daily 读取（symbol=None 全品种，date ASC）。"""
    init_db()
    c = _conn()
    try:
        if symbol:
            rows = c.execute(
                "SELECT * FROM futures_daily WHERE symbol=? ORDER BY date DESC LIMIT ?",
                (symbol.upper(), int(limit)),
            ).fetchall()
        else:
            rows = c.execute(
                "SELECT * FROM futures_daily ORDER BY date DESC LIMIT ?", (int(limit),)
            ).fetchall()
        return [dict(r) for r in reversed(rows)]
    finally:
        _safe_close(c)


def latest_futures_date() -> str | None:
    """futures_daily 最新交易日。"""
    init_db()
    c = _conn()
    try:
        row = c.execute("SELECT MAX(date) AS d FROM futures_daily").fetchone()
        return row["d"] if row and row["d"] else None
    finally:
        _safe_close(c)


def futures_contracts() -> set[str]:
    """已入库合约集合（断点续跑判定）。"""
    init_db()
    c = _conn()
    try:
        rows = c.execute("SELECT DISTINCT contract FROM futures_daily").fetchall()
        return {str(r["contract"]) for r in rows}
    finally:
        _safe_close(c)


def futures_dates_by_symbol() -> dict[str, set[str]]:
    """{symbol: {date}} 已入库日期集合（sina 降级 fill-only 判定）。"""
    init_db()
    c = _conn()
    try:
        rows = c.execute("SELECT symbol, date FROM futures_daily").fetchall()
        out: dict[str, set[str]] = {}
        for r in rows:
            out.setdefault(str(r["symbol"]), set()).add(str(r["date"]))
        return out
    finally:
        _safe_close(c)


def save_market_daily(rows: list[dict]) -> int:
    """market_daily 批量写入（v0.2.6 全市场分位数据层）。

    rows: [{date, ts_code, open, high, low, close, pre_close, pct_chg, vol, amount, turnover_rate}]
    merge=True：同日二次写入非 NULL 覆盖、NULL 保留旧值（对齐日快照三件套）。
    """
    if not rows:
        return 0
    init_db()
    c = _conn()
    try:
        n = upsert_daily_rows(c, "market_daily", rows, pk=("date", "ts_code"), merge=True)
        c.commit()
        return n
    finally:
        _safe_close(c)


def latest_market_daily_date() -> str | None:
    """market_daily 最新交易日（无数据返回 None）。"""
    init_db()
    c = _conn()
    try:
        row = c.execute("SELECT MAX(date) AS d FROM market_daily").fetchone()
        return row["d"] if row and row["d"] else None
    finally:
        _safe_close(c)


def market_daily_dates() -> set[str]:
    """market_daily 已有交易日集合（用于增量缺日计算）。"""
    init_db()
    c = _conn()
    try:
        rows = c.execute("SELECT DISTINCT date FROM market_daily").fetchall()
        return {str(r["date"]) for r in rows}
    finally:
        _safe_close(c)


def load_market_daily(days: int | None = None, dates: list[str] | None = None) -> list[dict]:
    """market_daily 明细行读取。

    days=N → 最近 N 个交易日全市场行；dates=[...] → 指定交易日全市场行（date ASC）。
    两参数互斥；均缺省时返回最近 20 个交易日（分位默认窗）。
    """
    init_db()
    c = _conn()
    try:
        if dates is not None:
            if not dates:
                return []
            ph = ",".join("?" * len(dates))
            rows = c.execute(
                f"SELECT * FROM market_daily WHERE date IN ({ph}) ORDER BY date ASC", tuple(dates)
            ).fetchall()
            return [dict(r) for r in rows]
        limit = int(days) if days is not None else 20
        return load_recent_rows(c, "market_daily", limit=limit * 6000, order_col="date")
    finally:
        _safe_close(c)
