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
