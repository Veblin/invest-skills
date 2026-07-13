"""SQLite 研究记录持久化。支持采集结果存储、查询和统计。

数据库: ~/.local/share/investment/research.db
WAL 模式安全并发。轻量 Schema 迁移。

v0.1.9: thesis 表（假设追踪）
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

from . import env
from .json_util import dumps_json, json_default
from .schema import index_dimensions

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
    dims = result.get("dimensions")
    if not isinstance(dims, list):
        dims = []
    sm = result.get("summary", {})
    name = next((d["data"].get("name", "") for d in dims
                 if d.get("dimension") == "basic_info" and d.get("data")), "")
    c = _conn()
    try:
        cur = c.execute(
            "INSERT INTO collections (symbol,name,fetched_at,dimensions_total,dimensions_ok,raw_json) VALUES (?,?,?,?,?,?)",
            (symbol, name, result.get("fetched_at", ""), sm.get("total", 0), sm.get("available", 0),
             dumps_json(result)))
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
        c.execute("DELETE FROM pipeline_states")
        c.commit()
    finally:
        c.close()


# ---- Pipeline 断点续跑状态 ----

def save_pipeline_step(symbol: str, step: str, state: dict | None = None) -> None:
    """保存流水线步骤状态。"""
    init_db()
    c = _conn()
    try:
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        state_json = (
            json.dumps(state, ensure_ascii=False, default=json_default) if state else None
        )
        c.execute(
            "INSERT OR REPLACE INTO pipeline_states (symbol, step, state_json, completed_at) VALUES (?, ?, ?, ?)",
            (symbol, step, state_json, now),
        )
        c.commit()
    finally:
        c.close()


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
        c.close()


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
        c.close()


def clear_pipeline_state(symbol: str) -> None:
    """清除某 symbol 的全部流水线状态。"""
    init_db()
    c = _conn()
    try:
        c.execute("DELETE FROM pipeline_states WHERE symbol = ?", (symbol,))
        c.commit()
    finally:
        c.close()


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
        d["raw_json"] = json.loads(d["raw_json"]) if isinstance(d["raw_json"], str) else d["raw_json"]
        return d
    finally:
        c.close()


def get_latest_two(symbol: str) -> tuple[dict, dict] | None:
    """获取指定股票最近两次采集记录。

    Returns:
        (older, newer) tuple，仅 1 条记录时返回 None。
    """
    init_db()
    c = _conn()
    try:
        rows = c.execute(
            "SELECT id, symbol, name, fetched_at, raw_json FROM collections "
            "WHERE symbol=? ORDER BY fetched_at DESC LIMIT 2",
            (symbol,)).fetchall()
        if len(rows) < 2:
            return None
        newer = dict(rows[0])
        older = dict(rows[1])
        newer["raw_json"] = json.loads(newer["raw_json"]) if isinstance(newer["raw_json"], str) else newer["raw_json"]
        older["raw_json"] = json.loads(older["raw_json"]) if isinstance(older["raw_json"], str) else older["raw_json"]
        return (older, newer)
    finally:
        c.close()


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
        triggered = [s.get("id") for s in risk.get("signals", []) if s.get("triggered")]
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
    """对比当前采集与 store 中最新快照的关键字段变化（供报告模块 1 使用）。"""
    rows = list_collections(limit=1, symbol=symbol)
    if not rows:
        return None
    prev = get_collection(rows[0]["id"])
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
    避免静默覆盖（H2 修复）。
    """
    result: dict[str, dict] = {}
    for i, item in enumerate(data):
        if not isinstance(item, dict):
            continue
        base_key = item.get("trade_date") or item.get("end_date") or str(i)
        holder = item.get("holder_name")
        # 若已有同键记录，说明存在多记录同日期，对全部记录改用复合键
        if base_key in result:
            existing = result.pop(base_key)
            eh = existing.get("holder_name")
            # 回写已存在记录（无 holder_name 时用序号兜底）
            suffix = eh if eh else "0"
            result[f"{base_key}_{suffix}"] = existing
            if holder:
                base_key = f"{base_key}_{holder}"
            else:
                base_key = f"{base_key}_{i}"
        result[str(base_key)] = item
    return result


# ---- v0.1.9: thesis tracker ----

_DEFAULT_ASSUMPTIONS = [
    {"id": "a1", "statement": "盈利增速可持续", "confidence": 0.7, "last_check_date": None, "valid": True},
    {"id": "a2", "statement": "行业景气维持", "confidence": 0.6, "last_check_date": None, "valid": True},
    {"id": "a3", "statement": "估值溢价有基本面支撑", "confidence": 0.5, "last_check_date": None, "valid": True},
]

_DEFAULT_RED_LINES = [
    {"id": "r1", "condition": "单季营收同比 < -10%", "triggered": False},
    {"id": "r2", "condition": "毛利率同比降 > 5pp", "triggered": False},
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
               VALUES (?, ?, ?, ?, ?, datetime('now'))""",
            (symbol, dumps_json(assumptions), dumps_json(red_lines), score, state),
        )
        c.commit()
    finally:
        c.close()
    return {"symbol": symbol, "health_score": score, "state": state, "action": "init"}


def thesis_get(symbol: str) -> dict[str, Any] | None:
    init_db()
    c = _conn()
    try:
        row = c.execute("SELECT * FROM thesis WHERE symbol=?", (symbol,)).fetchone()
        if not row:
            return None
        assumptions = json.loads(row["assumptions_json"] or "[]")
        red_lines = json.loads(row["red_lines_json"] or "[]")
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
        c.close()


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
               health_score=?, state=?, updated_at=datetime('now') WHERE symbol=?""",
            (dumps_json(a), dumps_json(r), score, state, symbol),
        )
        c.commit()
    finally:
        c.close()
    return {"symbol": symbol, "health_score": score, "state": state, "action": "update"}
