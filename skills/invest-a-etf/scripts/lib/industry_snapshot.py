"""申万行业 PE/PB 周度快照采集与查询（G2）。

采集侧：每周五收盘后调 ``index_analysis_weekly_sw``，写入 SQLite。
查询侧：提供最新行业快照列表 + 单行业 PE 查询。

依赖 invest-a-stock 的 lib.proxy / lib.store。
"""

from __future__ import annotations

import logging
from typing import Any

from _invest_path import ensure_invest_a_scripts_on_path, ensure_skills_lib_on_path

ensure_invest_a_scripts_on_path()
ensure_skills_lib_on_path()

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
    src_date: str | None = None
    try:
        # R1 审查 F2：源发布日期须持久化（采集日 ≠ 源日期；实测该源长期冻结在
        # 2022-11-04 而采集日每周在变，缺少 src_date 时陈旧检测完全失效）
        try:
            c.execute("ALTER TABLE industry_weekly ADD COLUMN src_date TEXT")
        except Exception:
            pass  # 列已存在
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
            src_date = _normalize_src_date(row.get("发布日期") or row.get("日期"))

            c.execute(
                "INSERT OR REPLACE INTO industry_weekly "
                "(index_code, index_name, date, src_date, pe, pb, chg_pct, turnover_pct, dividend_yield, mkt_cap) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (idx_code, idx_name, today, src_date, pe, pb, chg, turnover, div_yield, mkt_cap),
            )
            saved += 1
        c.commit()
        result["industries_saved"] = saved
        logger.info("industry_weekly: saved %d industries for %s (src %s)",
                    saved, today, src_date)
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

WEEKLY_STALE_DAYS = 7  # 源数据日期距最近交易日阈值（**交易日**口径，T7-3）


def _normalize_src_date(raw: Any) -> str | None:
    """源发布日期归一为 YYYYMMDD（兼容 2022-11-04 / 20221104 / 空值 → None）。"""
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    digits = "".join(ch for ch in s if ch.isdigit())
    return digits[:8] if len(digits) >= 8 else None


def weekly_unchanged_vs_previous(date: str) -> bool | None:
    """date 与上一已存日期的行业行集 (pe,pb,chg_pct,turnover_pct) 全等 → True。

    None = 无上一日或任一侧无行（不可比）；False = 有差异/名单增删。
    仅提示语义（T7-3；R1 审查 F15：行集比较收敛到共享 freshness.maps_equal）。
    """
    import sqlite3

    from freshness import maps_equal
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
    if not cmap or not omap:
        return None
    return maps_equal(cmap, omap)


def industry_snapshot_stale_note(date: str | None, *, src_date: str | None = None) -> str | None:
    """源数据日期（优先 src_date）距最近交易日滞后 > 阈值 → 提示文本；否则 None。

    R1 审查 F2：必须以**源发布日期**判滞后（采集日不等于源日期——实测该源长期
    冻结在 2022-11-04 而采集日每周在变）；src_date 缺失 → 回退采集日并在文本中
    显式标注「源发布日期缺失，回退粗判」。交易日口径（F7）。
    """
    if not src_date and not date:
        return None
    try:
        from dates import shanghai_session_date

        session = str(shanghai_session_date())
    except Exception:
        return None
    from freshness import trading_day_lag

    use = str(src_date) if src_date else str(date)
    lag, degraded = trading_day_lag(use, session)
    if lag is None or lag <= WEEKLY_STALE_DAYS:
        return None
    prefix = "（日历不可用，按自然日粗判）" if degraded else ""
    unit = "天(自然日粗判)" if degraded else "个交易日"
    src_note = f"源数据日期 {use}" if src_date else f"采集日期 {use}（源发布日期缺失，回退粗判）"
    return (f"行业 PE 快照{src_note}距最近交易日 {lag} {unit}"
            f"（阈值 {WEEKLY_STALE_DAYS} 交易日）{prefix}——疑采集未跑/数据源冻结，"
            "请先 collect-weekly 并核对源页面")


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
