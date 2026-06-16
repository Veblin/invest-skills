"""SQLite 研究记录持久化。支持采集结果存储、查询和统计。

数据库: ~/.local/share/investment/research.db
WAL 模式安全并发。轻量 Schema 迁移。
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
    old_raw = old.get("raw_json", old) if isinstance(old, dict) else {}
    new_raw = new.get("raw_json", new) if isinstance(new, dict) else {}

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


def _index_dims(raw: dict) -> dict[str, dict]:
    """将 raw_json 中的 dimensions 列表转为 dict。"""
    dims = raw.get("dimensions", [])
    return {d.get("dimension", ""): d for d in dims}


def _dim_data(raw: dict, name: str) -> Any:
    d = _index_dims(raw).get(name)
    return d.get("data") if d else None


def _yoy_from_fina_rows(rows: list[dict], field: str) -> float | None:
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
    if len(ed) < 8 or not ed[:4].isdigit():
        return None
    prev_ed = f"{int(ed[:4]) - 1}{ed[4:8]}"
    prev_v = None
    for r in reversed(sorted_rows[:-1]):
        if str(r.get("end_date", "")) == prev_ed:
            try:
                prev_v = float(r.get(field))
            except (TypeError, ValueError):
                logger.debug("unparseable %s=%s for %s, trying next record", field, r.get(field), r.get("end_date", ""))
                continue
            break  # Found a valid value
    if prev_v is None or prev_v <= 0:
        return None
    return round((cur_f - prev_v) / prev_v * 100, 2)


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

    return {
        "symbol": new_snap.get("symbol", old_snap.get("symbol", "?")),
        "old_at": old_snap.get("fetched_at", ""),
        "new_at": new_snap.get("fetched_at", ""),
        "categories": categories,
        "unchanged": unchanged,
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
