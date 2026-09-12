"""交易日志数据库层。表建在 invest-a-stock 的 research.db 中。

v0.2.1: direction / linked_journal_id / evaluation_json 三字段。
v0.2.4: attribution 复盘归因字段（环境/能力/运气三分归因）。
v0.2.4: 时间戳统一 UTC 存储（曾误写上海墙钟，与 schema DEFAULT 混用时区，
        导致 ORDER BY created_at 排序错误；写入侧一律 datetime('now') UTC）。
"""

from __future__ import annotations

import json
import logging
import sqlite3

from _invest_path import ensure_invest_a_scripts_on_path

logger = logging.getLogger(__name__)

ensure_invest_a_scripts_on_path()

from db_util import connect_db, safe_close  # noqa: E402
from lib import env  # noqa: E402

DB_PATH = env.STORE_DB


def _conn() -> sqlite3.Connection:
    return connect_db(DB_PATH)


def _safe_close(c: sqlite3.Connection) -> None:
    safe_close(c)


# ---------------------------------------------------------------------------
# Schema init + migration
# ---------------------------------------------------------------------------

def init_db() -> None:
    """初始化表结构 + v0.2.1 / v0.2.4 字段迁移。"""
    c = _conn()
    try:
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA synchronous=NORMAL")
        c.executescript("""
            CREATE TABLE IF NOT EXISTS trade_journals (
                id INTEGER PRIMARY KEY,
                symbol TEXT NOT NULL,
                asset_type TEXT,
                driver TEXT,
                hypothesis TEXT,
                wrong_conditions TEXT,
                target_period TEXT,
                target_return TEXT,
                max_loss_amount TEXT,
                position_pct REAL,
                entry_price REAL,
                entry_date TEXT,
                exit_price REAL,
                exit_date TEXT,
                actual_result REAL,
                wrong_triggered INTEGER DEFAULT 0,
                lessons TEXT,
                reviewed INTEGER DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
            );
        """)
        c.commit()
    finally:
        _safe_close(c)

    # v0.2.1 migration（幂等）
    _migrate_v021()
    # v0.2.4 migration（幂等）
    _migrate_v024()
    # v0.2.6 migration（幂等，§4.3 结构化字段）
    _migrate_v026()


def _migrate_v021() -> None:
    """添加 v0.2.1 三字段：direction, linked_journal_id, evaluation_json。"""
    c = _conn()
    try:
        for col, col_def in [
            ("direction", "TEXT DEFAULT 'buy'"),
            ("linked_journal_id", "INTEGER"),
            ("evaluation_json", "TEXT"),
        ]:
            try:
                c.execute(f"ALTER TABLE trade_journals ADD COLUMN {col} {col_def}")
            except sqlite3.OperationalError:
                pass  # 列已存在
        c.commit()
    finally:
        _safe_close(c)


def _migrate_v024() -> None:
    """添加 v0.2.4 字段：attribution（复盘归因：环境/能力/运气三分）。

    幂等：列已存在时 duplicate column 错误静默忽略（与 _migrate_v021 同模式）。
    """
    c = _conn()
    try:
        try:
            c.execute("ALTER TABLE trade_journals ADD COLUMN attribution TEXT DEFAULT ''")
        except sqlite3.OperationalError:
            pass  # 列已存在
        c.commit()
    finally:
        _safe_close(c)


def _migrate_v026() -> None:
    """v0.2.6 结构化字段（ABCD §4.3 全量）：

    - stop_price REAL：买入止损位（用户填写）
    - expected_loss_pct REAL：预期亏损%（引擎计算 = |stop/entry − 1|）
    - proceeds_destination TEXT：卖出款去向（转出/换仓/空仓，Odean 1999 审计）
    - stop_moved_count INTEGER：止损位被移动次数（Fischbacher 2017 承诺失效审计）
    - stop_hit_count INTEGER：止损触发次数
    - extracted_amount REAL：已提取金额（利润提取纪律，house money 切断）

    幂等：列已存在时 duplicate column 错误静默忽略（与 _migrate_v021 同模式）。
    """
    c = _conn()
    try:
        for col, col_def in [
            ("stop_price", "REAL"),
            ("expected_loss_pct", "REAL"),
            ("proceeds_destination", "TEXT DEFAULT ''"),
            ("stop_moved_count", "INTEGER DEFAULT 0"),
            ("stop_hit_count", "INTEGER DEFAULT 0"),
            ("extracted_amount", "REAL"),
        ]:
            try:
                c.execute(f"ALTER TABLE trade_journals ADD COLUMN {col} {col_def}")
            except sqlite3.OperationalError:
                pass  # 列已存在
        c.commit()
    finally:
        _safe_close(c)


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

# update_journal 允许写入的列（禁止把 key 直接拼进 SQL）
_ALLOWED_UPDATE_COLS: frozenset[str] = frozenset({
    "symbol", "asset_type", "driver", "hypothesis", "wrong_conditions",
    "target_period", "target_return", "max_loss_amount", "position_pct",
    "entry_price", "entry_date", "exit_price", "exit_date",
    "actual_result", "wrong_triggered", "lessons", "reviewed",
    "direction", "linked_journal_id", "evaluation_json",
    "attribution",
    "stop_price", "expected_loss_pct", "proceeds_destination",
    "stop_moved_count", "stop_hit_count", "extracted_amount",
})


def _serialize_wrong_conditions(value: object) -> str:
    """list/dict → JSON 字符串；其余转 str；None → '[]'。"""
    if value is None:
        return "[]"
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _link_is_unset(value: object) -> bool:
    """True = 视为未设置关联（None / 空串 / 空白）。"""
    if value is None:
        return True
    if isinstance(value, str) and value.strip() == "":
        return True
    return False


def find_latest_buy(symbol: str) -> dict | None:
    """同标的最近一条买入日志（symbol 大小写不敏感）。"""
    init_db()
    sym = (symbol or "").strip().upper()
    if not sym:
        return None
    c = _conn()
    try:
        row = c.execute(
            """SELECT id, symbol, entry_date, direction, driver, created_at
               FROM trade_journals
               WHERE UPPER(symbol)=? AND direction='buy'
               ORDER BY created_at DESC, id DESC
               LIMIT 1""",
            (sym,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        _safe_close(c)


def resolve_sell_link(entry: dict) -> dict:
    """卖出自动关联：direction=sell 且未设 linked_journal_id 时，挂最近同标的 buy。

    返回可能被改写的 entry 副本（不原地修改调用方 dict 时可再 copy）。
    """
    out = dict(entry)
    direction = (out.get("direction") or "buy").lower()
    out["direction"] = direction
    if direction != "sell":
        return out
    # None / "" / 空白均视为未设置，触发自动关联
    if not _link_is_unset(out.get("linked_journal_id")):
        return out
    buy = find_latest_buy(str(out.get("symbol", "")))
    if buy:
        out["linked_journal_id"] = buy["id"]
    else:
        out["linked_journal_id"] = None  # 清空空串，避免 INTEGER 列写入 ''
    return out


def save_journal(entry: dict) -> int:
    """保存新日志，返回 id。

    - symbol 统一 upper
    - direction=sell 且无 linked_journal_id → 自动关联最近同标的 buy
    - wrong_conditions 支持 list（自动 JSON 序列化）
    """
    init_db()
    entry = resolve_sell_link(entry)
    symbol = str(entry.get("symbol", "")).strip().upper()
    c = _conn()
    try:
        # evaluation_json 如果是 dict 则序列化
        eval_raw = entry.get("evaluation_json")
        if isinstance(eval_raw, dict):
            eval_raw = json.dumps(eval_raw, ensure_ascii=False)

        wrong_raw = _serialize_wrong_conditions(
            entry.get("wrong_conditions", "[]"),
        )

        link_id = entry.get("linked_journal_id")
        if _link_is_unset(link_id):
            link_id = None

        # expected_loss_pct：引擎计算（|stop/entry − 1|×100）——用户填 stop_price 后
        # 由保存方（或调用方）算好传入；未提供时留 NULL（P0：不在此处心算）
        cur = c.execute(
            """INSERT INTO trade_journals
               (symbol, asset_type, driver, hypothesis, wrong_conditions,
                target_period, target_return, max_loss_amount, position_pct,
                entry_price, entry_date,
                direction, linked_journal_id, evaluation_json, attribution,
                stop_price, expected_loss_pct, proceeds_destination,
                stop_moved_count, stop_hit_count, extracted_amount)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                symbol,
                entry.get("asset_type", ""),
                entry.get("driver", ""),
                entry.get("hypothesis", ""),
                wrong_raw,
                entry.get("target_period", ""),
                entry.get("target_return", ""),
                entry.get("max_loss_amount", ""),
                entry.get("position_pct"),
                entry.get("entry_price"),
                entry.get("entry_date", ""),
                entry.get("direction", "buy"),
                link_id,
                eval_raw,
                entry.get("attribution", ""),
                entry.get("stop_price"),
                entry.get("expected_loss_pct"),
                entry.get("proceeds_destination", ""),
                entry.get("stop_moved_count", 0),
                entry.get("stop_hit_count", 0),
                entry.get("extracted_amount"),
            ),
        )
        c.commit()
        return cur.lastrowid
    except Exception:
        logger.warning("save_journal failed for symbol=%s — rolling back", entry.get("symbol", "?"), exc_info=True)
        c.rollback()
        raise
    finally:
        _safe_close(c)


def update_journal(journal_id: int, updates: dict) -> bool:
    """更新日志字段。

    - 仅允许白名单列名（防 SQL 标识符注入）
    - evaluation_json 为 dict、wrong_conditions 为 list/dict 时自动序列化
    - 返回是否影响 ≥1 行（用 Cursor.rowcount，不用 Connection）
    """
    init_db()
    if not updates:
        return False

    prepared: dict[str, object] = {}
    for key, val in updates.items():
        if key not in _ALLOWED_UPDATE_COLS:
            raise ValueError(f"disallowed update column: {key!r}")
        if key == "evaluation_json" and isinstance(val, dict):
            prepared[key] = json.dumps(val, ensure_ascii=False)
        elif key == "wrong_conditions":
            prepared[key] = _serialize_wrong_conditions(val)
        elif key == "linked_journal_id" and _link_is_unset(val):
            prepared[key] = None
        else:
            prepared[key] = val

    if not prepared:
        return False

    c = _conn()
    try:
        cols = list(prepared.keys())
        set_clause = ", ".join(f"{k}=?" for k in cols)
        values = [prepared[k] for k in cols]
        cur = c.execute(
            f"UPDATE trade_journals SET {set_clause}, updated_at=datetime('now') WHERE id=?",
            values + [journal_id],
        )
        c.commit()
        return cur.rowcount > 0
    except Exception:
        logger.warning("update_journal failed for id=%s — rolling back", journal_id, exc_info=True)
        c.rollback()
        raise
    finally:
        _safe_close(c)


def list_journals(limit: int = 20) -> list[dict]:
    """列出最近的日志条目。"""
    init_db()
    c = _conn()
    try:
        rows = c.execute(
            "SELECT * FROM trade_journals ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        _safe_close(c)


def get_journal(journal_id: int) -> dict | None:
    """获取单条日志。evaluation_json 自动反序列化。"""
    init_db()
    c = _conn()
    try:
        row = c.execute(
            "SELECT * FROM trade_journals WHERE id=?",
            (journal_id,),
        ).fetchone()
        if not row:
            return None
        d = dict(row)
        # 反序列化 evaluation_json
        if d.get("evaluation_json") and isinstance(d["evaluation_json"], str):
            try:
                d["evaluation_json"] = json.loads(d["evaluation_json"])
            except json.JSONDecodeError:
                pass
        return d
    finally:
        _safe_close(c)


def delete_journal(journal_id: int) -> bool:
    """删除日志；先清空指向该 id 的 linked_journal_id，避免孤儿关联。"""
    init_db()
    c = _conn()
    try:
        c.execute(
            "UPDATE trade_journals SET linked_journal_id=NULL, updated_at=datetime('now') "
            "WHERE linked_journal_id=?",
            (journal_id,),
        )
        cur = c.execute("DELETE FROM trade_journals WHERE id=?", (journal_id,))
        c.commit()
        return cur.rowcount > 0
    except Exception:
        c.rollback()
        raise
    finally:
        _safe_close(c)


# ---------------------------------------------------------------------------
# v0.2.1 新增
# ---------------------------------------------------------------------------

def search_by_symbol(symbol: str) -> list[dict]:
    """按标的代码搜索日志（用于卖出关联）。

    symbol 大小写不敏感（统一 upper 后匹配）。
    v0.2.1：一对一 linked_journal_id。分批买卖多对多延至 v0.2.2。
    """
    init_db()
    sym = (symbol or "").strip().upper()
    c = _conn()
    try:
        rows = c.execute(
            "SELECT id, symbol, entry_date, direction, driver, created_at, "
            "linked_journal_id "
            "FROM trade_journals WHERE UPPER(symbol)=? ORDER BY created_at DESC",
            (sym,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        _safe_close(c)


def journal_stats() -> dict:
    """日志统计：总数、买卖分布、复盘率。"""
    init_db()
    c = _conn()
    try:
        total = c.execute("SELECT COUNT(*) FROM trade_journals").fetchone()[0]
        buy_cnt = c.execute(
            "SELECT COUNT(*) FROM trade_journals WHERE direction='buy'"
        ).fetchone()[0]
        sell_cnt = c.execute(
            "SELECT COUNT(*) FROM trade_journals WHERE direction='sell'"
        ).fetchone()[0]
        reviewed = c.execute(
            "SELECT COUNT(*) FROM trade_journals WHERE reviewed=1"
        ).fetchone()[0]
        return {
            "total": total,
            "buy": buy_cnt,
            "sell": sell_cnt,
            "reviewed": reviewed,
        }
    finally:
        _safe_close(c)


# ---------------------------------------------------------------------------
# R-C01 错频计数（连续亏损 + 个人自证数据）
# ---------------------------------------------------------------------------

DEFAULT_LOSS_STREAK_THRESHOLD = 3
# 阈值语义：**纪律惯例**（直播建议值 2-3 次），**非实证阈值**——
# 固定阈值与计时冷却均无学术证据支持（Hopfgartner et al. 2023：短期自排除无效）。
_THRESHOLD_LABEL = "纪律惯例（直播建议值），非实证阈值；可配置"


def _result_rows(*, asset_type: str | None = None, rows: list[dict] | None = None) -> list[dict]:
    """ журнал 行（按时间升序）。``rows`` 显式传入时走离线路径（测试/回放）。"""
    if rows is not None:
        return sorted(rows, key=lambda r: str(r.get("created_at") or r.get("entry_date") or ""))
    init_db()
    c = _conn()
    try:
        sql = "SELECT symbol, actual_result, entry_date, created_at FROM trade_journals"
        params: tuple = ()
        if asset_type:
            sql += " WHERE asset_type = ?"
            params = (asset_type,)
        sql += " ORDER BY created_at ASC"
        return [dict(r) for r in c.execute(sql, params).fetchall()]
    finally:
        _safe_close(c)


def _day_of(row: dict) -> str | None:
    raw = str(row.get("entry_date") or row.get("created_at") or "")[:10]
    return raw or None


def _num(v) -> float | None:
    """数值 → float；None / 非数值 / NaN → None（D1：0 是合法结果，不得被 falsy 吞）。"""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else f


def consecutive_loss_streak(*, asset_type: str | None = None,
                            threshold: int = DEFAULT_LOSS_STREAK_THRESHOLD,
                            rows: list[dict] | None = None) -> dict:
    """连续亏损笔数（store 聚合，**严格 `actual_result < 0`**）。

    ``actual_result is None`` 的行**剔除不计**——它不是一个结果（未平仓/未记录），
    既不算亏损也不算盈利，也**不打断**连续亏损（否则「没记结果」会被读成「没事」）。
    """
    data = _result_rows(asset_type=asset_type, rows=rows)
    resolved = [(r, _num(r.get("actual_result"))) for r in data]
    resolved = [(r, v) for r, v in resolved if v is not None]
    streak = 0
    last_loss_at = None
    for r, v in reversed(resolved):
        if v < 0:
            streak += 1
            if last_loss_at is None:
                last_loss_at = _day_of(r)
        else:
            break
    return {"streak": streak, "last_loss_at": last_loss_at, "window_n": len(resolved),
            "threshold": threshold, "threshold_label": _THRESHOLD_LABEL,
            "triggered": streak >= threshold,
            "basis": "actual_result < 0（严格小于；0/保本与 None 均不计）"}


def loss_after_loss_evidence(*, lookback: int = 200,
                             rows: list[dict] | None = None) -> dict:
    """个人自证数据：「亏损后立即再交易 N 次、其中 M 次续亏」。

    用**用户自己的** journal 数据校准自己的阈值（外部最优阈值无证据）。
    分母 = 亏损后**仍有下一笔**的次数；None 行不构成「下一笔」。
    """
    data = _result_rows(rows=rows)[-lookback:]
    seq = [(r, _num(r.get("actual_result"))) for r in data]
    seq = [(r, v) for r, v in seq if v is not None]
    n_after = n_continued = 0
    gaps: list[float] = []
    for i, (r, v) in enumerate(seq):
        if v >= 0 or i + 1 >= len(seq):
            continue
        n_after += 1
        nxt = seq[i + 1][1]
        if nxt < 0:
            n_continued += 1
        d0, d1 = _day_of(r), _day_of(seq[i + 1][0])
        if d0 and d1:
            try:
                import datetime as _dt

                gaps.append((_dt.date.fromisoformat(d1) - _dt.date.fromisoformat(d0)).days)
            except ValueError:
                pass
    median_gap = None
    if gaps:
        gaps.sort()
        mid = len(gaps) // 2
        median_gap = (gaps[mid] if len(gaps) % 2 else (gaps[mid - 1] + gaps[mid]) / 2)
    rate = (n_continued / n_after * 100) if n_after else None
    return {"n_after_loss": n_after, "n_continued_loss": n_continued,
            "rate_pct": rate, "median_gap_days": median_gap,
            "sample": f"最近 {len(seq)} 笔已了结记录", "available": n_after > 0,
            "note": "" if n_after else "样本不足：你的记录中尚无「亏损后继续交易」的完整配对"}


def render_wrong_frequency(*, asset_type: str | None = None,
                           threshold: int = DEFAULT_LOSS_STREAK_THRESHOLD,
                           rows: list[dict] | None = None) -> str:
    """错频提示（触发时）——**冷却 = 启动结构化复盘流程，不是禁止交易**。"""
    st = consecutive_loss_streak(asset_type=asset_type, threshold=threshold, rows=rows)
    if not st["triggered"]:
        return (f"错频检查：当前连续亏损 {st['streak']} 笔（阈值 {st['threshold']}，"
                f"{_THRESHOLD_LABEL}）——未触发。")
    ev = loss_after_loss_evidence(rows=rows)
    lines = [f"⚠️ 错频提示：当前连续亏损 {st['streak']} 笔"
             f"（阈值 {st['threshold']}，{_THRESHOLD_LABEL}）。"]
    if ev["available"]:
        lines.append(
            f"   个人自证数据：你历史上亏损后立即再交易 {ev['n_after_loss']} 次，"
            f"其中 {ev['n_continued_loss']} 次续亏（{ev['rate_pct']:.0f}%）"
            f" [来源: Python calc: {ev['n_continued_loss']}/{ev['n_after_loss']}]"
            + (f"；两次之间中位间隔 {ev['median_gap_days']:.0f} 天" if ev["median_gap_days"] is not None else ""))
    else:
        lines.append(f"   个人自证数据：{ev['note']}")
    lines.append("   建议动作（**流程**，非交易指令）：先完成一次结构化复盘"
                 "（触发日期 / 实际路径 / 判断对错 / 错在哪条 / 修订内容，"
                 "见 references/scenario-plans.md 复盘字段），再考虑下一笔。")
    return "\n".join(lines)


def stop_audit_stats() -> dict:
    """止损纪律聚合（v0.2.6 §4.3 审计信号，仿 journal_stats 跨行聚合）。

    返回 {sells_total, stop_hit_sells, stop_moved_sells, buys_with_stop,
          buys_without_stop}——卖出路径一致性核对用（Fischbacher 2017：
    止损移动 = 承诺失效审计信号）。
    """
    init_db()
    c = _conn()
    try:
        sells = c.execute("SELECT COUNT(*) FROM trade_journals WHERE direction='sell'").fetchone()[0]
        hits = c.execute(
            "SELECT COUNT(*) FROM trade_journals WHERE direction='sell' AND stop_hit_count > 0"
        ).fetchone()[0]
        moved = c.execute(
            "SELECT COUNT(*) FROM trade_journals WHERE direction='sell' AND stop_moved_count > 0"
        ).fetchone()[0]
        with_stop = c.execute(
            "SELECT COUNT(*) FROM trade_journals WHERE direction='buy' AND stop_price IS NOT NULL"
        ).fetchone()[0]
        without_stop = c.execute(
            "SELECT COUNT(*) FROM trade_journals WHERE direction='buy' AND stop_price IS NULL"
        ).fetchone()[0]
        return {
            "sells_total": sells,
            "stop_hit_sells": hits,
            "stop_moved_sells": moved,
            "buys_with_stop": with_stop,
            "buys_without_stop": without_stop,
        }
    finally:
        _safe_close(c)


def extracted_amount_mtd(month: str | None = None) -> dict:
    """当月利润提取聚合（v0.2.6 纪律③：提取 ≠ 止盈，house money 切断）。

    month: 'YYYY-MM'（默认当前月，上海口径）。返回
    {month, sum_extracted, n_records, cooldown_violations}。
    cooldown_violations：提取后 10 个自然日内同标的出现新 buy 的次数
    （日历日粗算 + 注记——设计 §4.2 ③ 的冷静期审计，D4 标注口径）。
    """
    from dates import shanghai_today  # noqa: E402 — 共享 skills/lib 口径（同 market_microstructure）

    if month is None:
        month = shanghai_today().strftime("%Y-%m")
    init_db()
    c = _conn()
    try:
        rows = c.execute(
            "SELECT symbol, entry_date, extracted_amount FROM trade_journals "
            "WHERE extracted_amount IS NOT NULL AND substr(entry_date, 1, 7)=?",
            (month,),
        ).fetchall()
        if not rows:
            return {"month": month, "sum_extracted": 0.0, "n_records": 0, "cooldown_violations": 0}
        total = sum(float(r["extracted_amount"] or 0) for r in rows)
        violations = 0
        import datetime as dt

        for r in rows:
            if not r["symbol"] or not r["entry_date"]:
                continue
            try:
                d = dt.datetime.strptime(r["entry_date"], "%Y-%m-%d").date()
            except ValueError:
                continue
            rebuys = c.execute(
                "SELECT COUNT(*) FROM trade_journals WHERE direction='buy' "
                "AND UPPER(symbol)=? AND entry_date>? AND entry_date<=?",
                (str(r["symbol"]).upper(), d.isoformat(), (d + dt.timedelta(days=10)).isoformat()),
            ).fetchone()[0]
            violations += rebuys
        return {
            "month": month,
            "sum_extracted": round(total, 2),
            "n_records": len(rows),
            "cooldown_violations": violations,
            "note": "冷却期口径为日历日粗算（10 个自然日），交易日口径待校准",
        }
    finally:
        _safe_close(c)
