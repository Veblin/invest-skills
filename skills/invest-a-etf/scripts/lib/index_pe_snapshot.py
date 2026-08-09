"""宽基指数 PE 历史快照持久化与查询（csindex → index_pe_history 表）。

采集侧：从 data_bridge L2 缓存信封（etf_index_pe 维度）提取。缓存命中零
网络调用；miss 时 data_bridge 回源。「零额外开销」仅在信封 status=="ok"
时成立：missing 信封（非交易日/取数失败）不被 data_bridge 缓存，report 的
persist 与自身取数各回源一次（失败日可能双次请求 csindex，见 data_bridge
_FAILURE_STATUSES 注释）。
csindex 单窗约 20 条日数据，全量写入（INSERT OR REPLACE 按日去重），
「周频」体现在采集纪律（collect-weekly / report 顺带写），表本身与触发
频率解耦。

消费侧：宽基 ETF 报告计算「指数 PE 分位」（index_pe_history 累积 ≥20 个
有效 PE 值时才有分位可算，与 csindex 单窗历史深度一致）。

依赖 invest-a-stock 的 lib.store / skills/lib data_bridge。
"""

from __future__ import annotations

import logging
from typing import Any

from _invest_path import ensure_invest_a_scripts_on_path

ensure_invest_a_scripts_on_path()

from lib.nums import safe_float  # noqa: E402 — canonical（NaN/±inf → None）
from lib.stats import percentile_rank_inclusive  # noqa: E402

logger = logging.getLogger(__name__)

# csindex 单窗约 20 条，首次入库即有分位可算（计有效 PE 值，NULL 行不占名额）
_INDEX_PE_MIN_HISTORY = 20


def _norm_index_code(code: Any) -> str:
    """指数代码规范化：信封内为整型（300），CSINDEX_MAP 为 6 位字符串（000300）。

    统一按数值规范化（去前导零）："000300" / 300 → "300"，"399006" → "399006"。
    None / NaN / ±inf / 空或纯空白 → ""（调用方据此回退 idx_code，防
    "None"/"nan" 毒键落库，review #7）。
    """
    if code is None:
        return ""
    if isinstance(code, str):
        code = code.strip()
        if not code:
            return ""
    try:
        f = float(code)
    except (TypeError, ValueError):
        return str(code)
    if f != f or f in (float("inf"), float("-inf")):  # NaN / ±inf
        return ""
    return str(int(f)) if f.is_integer() else str(code)


# ---------------------------------------------------------------------------
# 采集（从 data_bridge 缓存信封提取，零新增网络调用）
# ---------------------------------------------------------------------------

def _bridge_envelope(idx_code: str) -> dict[str, Any] | None:
    """取 etf_index_pe 缓存信封（best-effort；data_bridge 不可用时 None）。

    C15 收敛：委托 etf_data._bridge_get（同目录 peer 的通用惰性包装）；
    保留函数名与 dict 过滤——6 处测试 monkeypatch 该函数名。
    """
    from etf_data import _bridge_get

    env = _bridge_get("get_etf_index_pe", idx_code)
    return env if isinstance(env, dict) else None


def persist_index_pe_from_cache(idx_codes: list[str] | None = None) -> dict[str, Any]:
    """把 etf_index_pe 缓存信封的 rows 全量写入 index_pe_history。

    Parameters
    ----------
    idx_codes : list[str] | None
        指数代码列表（csindex 格式，如 ["000300"]）。缺省 = CSINDEX_MAP
        全部指数代码（去重）。

    Returns
    -------
    dict
        {index_codes: int, ok_envelopes: int, rows_saved: int, error: str|None}

    error 仅在异常/CSINDEX_MAP 不可用时非 None；信封全部缺失（非交易日等）
    是正常态，以 ok_envelopes=0 与 warning 日志显式化（D5，review #8）。
    """
    if idx_codes is None:
        try:
            from etf_data import CSINDEX_MAP
            idx_codes = sorted({v for v in CSINDEX_MAP.values() if v})
        except Exception:
            idx_codes = []
    result: dict[str, Any] = {
        "index_codes": len(idx_codes), "ok_envelopes": 0,
        "rows_saved": 0, "error": None,
    }
    if not idx_codes:
        result["error"] = "CSINDEX_MAP 不可用或为空，无指数代码可采集"
        logger.warning(result["error"])
        return result

    from lib.db_util import upsert_daily_rows
    from lib.store import _conn, _safe_close, init_db

    rows_to_write: list[dict[str, Any]] = []
    for idx_code in idx_codes:
        env = _bridge_envelope(idx_code)
        if env is None or env.get("status") != "ok":
            continue
        result["ok_envelopes"] += 1
        fallback_code = _norm_index_code(idx_code)  # 回退必须归一化，否则落 "000300" 桶永不匹配
        for row in env.get("rows") or []:
            if not isinstance(row, dict):
                continue
            date = row.get("日期")
            if not date:
                continue
            if "指数代码" not in row:
                code = fallback_code  # 缺键：设计内的默认回退
            else:
                code = _norm_index_code(row["指数代码"]) or ""
                if not code:  # 值为 None/NaN/空 → 格式漂移告警，防毒键落库
                    logger.warning(
                        "指数代码不可用（%r，date=%s），回退 %s",
                        row["指数代码"], date, idx_code,
                    )
                    code = fallback_code
            rows_to_write.append({
                "index_code": code,
                "index_name": str(row.get("指数中文简称", "") or ""),
                "date": str(date),
                "pe": safe_float(row.get("市盈率1")),
                "pe_circulating": safe_float(row.get("市盈率2")),
                "dividend_yield": safe_float(row.get("股息率1")),
                "dividend_yield_circulating": safe_float(row.get("股息率2")),
            })

    if not rows_to_write:
        if result["ok_envelopes"] == 0:
            logger.warning(
                "index_pe_history: 0 rows from %d codes（信封缺失/非交易日）",
                len(idx_codes),
            )
        return result

    init_db()
    c = _conn()
    try:
        upsert_daily_rows(
            c, "index_pe_history", rows_to_write,
            pk=("index_code", "date"), merge=False,
        )
        c.commit()
        result["rows_saved"] = len(rows_to_write)
        logger.info("index_pe_history: saved %d rows (%d codes)", len(rows_to_write), len(idx_codes))
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

def get_index_pe_history(index_code: str, days: int = 250) -> list[dict]:
    """index_pe_history 近 N 行，按 date ASC（分位计算需升序窗口）。

    查询参数同样经 _norm_index_code 规范化（CSINDEX_MAP 6 位格式 → 存储格式）。
    """
    import sqlite3

    from lib.db_util import load_recent_rows
    from lib.store import _conn, _safe_close

    c = _conn()
    try:
        return load_recent_rows(
            c, "index_pe_history", limit=int(days),
            where="index_code = ?", params=(_norm_index_code(index_code),),
        )
    except sqlite3.OperationalError:
        return []
    finally:
        _safe_close(c)


def index_pe_percentile(rows: list[dict], current_pe: float | None) -> float | None:
    """当前 PE 在历史序列中的含边界分位（复用 lib.stats.percentile_rank_inclusive）。

    有效 PE 值 <20 个或 current_pe 为 None → None（csindex 单窗即约 20 条，
    首次入库即有分位可算；更少时视为数据不足）。守卫计「有效值」而非原始
    行数——亏损期/无 PE 的 NULL 行不占名额（review #5）。
    """
    if current_pe is None:
        return None
    seq = [safe_float(r.get("pe")) for r in rows]
    seq = [v for v in seq if v is not None]
    if len(seq) < _INDEX_PE_MIN_HISTORY:
        return None
    return percentile_rank_inclusive(seq, current_pe, round_to=1)
