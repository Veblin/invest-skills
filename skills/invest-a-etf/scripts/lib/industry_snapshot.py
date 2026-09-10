"""申万行业 PE/PB 周度快照采集与查询（G2）。

采集侧：每周五收盘后调 ``index_analysis_weekly_sw``，写入 SQLite。
查询侧：提供最新行业快照列表 + 单行业 PE 查询。

依赖 invest-a-stock 的 lib.proxy / lib.store。
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 采集
# ---------------------------------------------------------------------------

def collect_industry_weekly() -> dict[str, Any]:
    """调 akshare ``index_analysis_weekly_sw``，写入 ``industry_weekly`` 表。

    Returns
    -------
    dict
        {date, industries_saved: int, error: str|None}
    """
    from dates import shanghai_today
    from lib.nums import coalesce_field as _safe_col
    from lib.proxy import akshare_direct_session
    from lib.store import _conn, _safe_close, init_db

    today = shanghai_today()
    result: dict[str, Any] = {
        "date": today,
        "industries_saved": 0,
        "error": None,
    }

    try:
        import akshare as ak

        with akshare_direct_session():
            df = ak.index_analysis_weekly_sw(symbol="一级行业")
    except Exception as exc:
        result["error"] = f"akshare index_analysis_weekly_sw failed: {exc}"
        logger.warning(result["error"])
        return result

    if df is None or df.empty:
        result["error"] = "empty response from index_analysis_weekly_sw"
        return result

    init_db()
    c = _conn()
    saved = 0
    try:
        for _, row in df.iterrows():
            idx_code = str(row.get("指数代码", ""))
            idx_name = str(row.get("指数名称", ""))
            if not idx_code:
                continue
            # 使用 init 阶段固定的 today 避免跨午夜日期不一致
            # 字段映射（index_analysis_weekly_sw 实际列名可能略有变化，兼容常见变体）
            pe = _safe_col(row, "市盈率", "pe", "PE")
            pb = _safe_col(row, "市净率", "pb", "PB")
            chg = _safe_col(row, "涨跌幅", "chg_pct")
            turnover = _safe_col(row, "换手率", "turnover_pct")
            div_yield = _safe_col(row, "股息率", "dividend_yield")
            mkt_cap = _safe_col(row, "流通市值", "mkt_cap")

            c.execute(
                "INSERT OR REPLACE INTO industry_weekly "
                "(index_code, index_name, date, pe, pb, chg_pct, turnover_pct, dividend_yield, mkt_cap) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (idx_code, idx_name, today, pe, pb, chg, turnover, div_yield, mkt_cap),
            )
            saved += 1
        c.commit()
        result["industries_saved"] = saved
        logger.info("industry_weekly: saved %d industries for %s", saved, today)
    except Exception as exc:
        c.rollback()
        result["error"] = f"db write failed: {exc}"
        logger.warning(result["error"])
    finally:
        _safe_close(c)

    return result


# ---------------------------------------------------------------------------
# 查询
# ---------------------------------------------------------------------------

WEEKLY_STALE_DAYS = 7  # 周频快照滞后阈值（自然日，T7-3）


def weekly_unchanged_vs_previous(date: str) -> bool | None:
    """date 与上一已存日期的行业行集 (pe,pb,chg_pct,turnover_pct) 全同 → True。

    None = 无上一日/不可比；False = 有差异（或名单增删）。仅提示语义（T7-3）。
    """
    import math
    import sqlite3

    from lib.store import _conn, _safe_close

    c = _conn()
    try:
        rows = c.execute(
            "SELECT DISTINCT date FROM industry_weekly ORDER BY date DESC LIMIT 2"
        ).fetchall()
        dates = [r["date"] for r in rows]
        if len(dates) < 2 or dates[0] != date:
            return None
        prev = dates[1]
        cur = c.execute(
            "SELECT index_code, pe, pb, chg_pct, turnover_pct FROM industry_weekly WHERE date = ?",
            (date,),
        ).fetchall()
        old = c.execute(
            "SELECT index_code, pe, pb, chg_pct, turnover_pct FROM industry_weekly WHERE date = ?",
            (prev,),
        ).fetchall()
    except sqlite3.OperationalError:
        return None
    finally:
        _safe_close(c)
    cmap = {r["index_code"]: (r["pe"], r["pb"], r["chg_pct"], r["turnover_pct"]) for r in cur}
    omap = {r["index_code"]: (r["pe"], r["pb"], r["chg_pct"], r["turnover_pct"]) for r in old}
    if not cmap or not omap or set(cmap) != set(omap):
        return False
    for k, vals in cmap.items():
        for a, b in zip(vals, omap[k]):
            if a is None or b is None or not math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-9):
                return False
    return True


def industry_snapshot_stale_note(date: str | None) -> str | None:
    """快照 date 距最近交易日滞后 > WEEKLY_STALE_DAYS → 提示文本；否则 None（T7-3）。"""
    if not date:
        return None
    try:
        from datetime import datetime

        from dates import shanghai_session_date

        d_snap = datetime.strptime(str(date), "%Y%m%d").date()
        d_sess = datetime.strptime(str(shanghai_session_date()), "%Y%m%d").date()
    except Exception:
        return None
    lag = (d_sess - d_snap).days
    if lag > WEEKLY_STALE_DAYS:
        return (f"行业 PE 快照日期 {date} 滞后 {lag} 天（阈值 {WEEKLY_STALE_DAYS} 天），"
                "疑采集未跑/数据源停更——请先 collect-weekly")
    return None


def list_industry_snapshot() -> list[dict[str, Any]]:
    """返回所有 28 个申万一级行业的最新 PE/PB/涨跌幅快照（按 PE 降序）。"""
    import sqlite3

    from lib.store import _conn, _safe_close

    c = _conn()
    try:
        rows = c.execute("""
            SELECT i.* FROM industry_weekly i
            INNER JOIN (
                SELECT index_code, MAX(date) as max_date
                FROM industry_weekly GROUP BY index_code
            ) latest ON i.index_code = latest.index_code AND i.date = latest.max_date
            ORDER BY i.pe DESC
        """).fetchall()
    except sqlite3.OperationalError:
        return []
    finally:
        _safe_close(c)

    return [dict(r) for r in rows]
