"""同花顺行业资金流 3/5/10 日快照 + 单时点窗口分解 + 每日积累趋势（v0.2.5 R15）。

数据源：ak.stock_fund_flow_industry（排行类 3日/5日/10日 + 即时，大单口径，净额单位亿元；
东财资金流端点 2026-08 起断连，本模块为独立源）。
趋势双轨：单时点窗口分解（近端 1-3 日 vs 中段 4-10 日，四象限语义标签）+ 每日快照
积累后的逐日序列（≥6 日可算 5 日变化率/转向）。
**资金流是证据非信号**：标签只描述方向/强度事实，不做方向性预测。

依赖 invest-a-stock 的 lib.nums / lib.proxy / lib.store / lib.db_util
（经 _invest_path shim，照 industry_snapshot.py 模式）。
"""

from __future__ import annotations

import json
import logging
import math
import sqlite3
from typing import Any

from _invest_path import ensure_invest_a_scripts_on_path

ensure_invest_a_scripts_on_path()

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 申万一级 sw_code → THS 90 申万细分行业（R15 C6：报告层以 THS 为权威）
# 覆盖 ETF_TO_SW_INDUSTRY 全部 sw_code；801720/801180/801780 为推断项，
# 首次在线采集后经 check_mapping_coverage 自检核对
# ---------------------------------------------------------------------------

SW_TO_THS_INDUSTRY: dict[str, list[str]] = {
    "801770": ["通信设备", "通信服务"],                                      # 通信
    "801080": ["半导体", "元件", "消费电子", "光学光电子", "其他电子", "电子化学品"],  # 电子
    "801750": ["计算机设备", "软件开发", "IT服务"],                          # 计算机
    "801730": ["电池", "光伏设备", "风电设备", "电网设备", "电机", "其他电源设备"],  # 电力设备
    "801740": ["军工电子", "军工装备"],                                      # 国防军工
    "801720": ["建筑装饰"],                                                # 建筑装饰（在线自检修正：THS 90 仅「建筑装饰」）
    "801120": ["白酒", "饮料制造", "食品加工制造"],                          # 食品饮料
    "801150": ["医疗服务", "化学制药", "生物制品", "中药", "医疗器械", "医药商业"],  # 医药生物
    "801180": ["房地产"],                                                  # 房地产（在线自检修正：THS 90 仅「房地产」）
    "801780": ["银行"],                                                    # 银行
    "801790": ["证券", "保险", "多元金融"],                                  # 非银金融
    "801050": ["贵金属", "小金属", "工业金属", "能源金属", "金属新材料"],      # 有色金属
}

# akshare symbol → 入库 window_days
_WINDOW_MAP: dict[str, int] = {"即时": 1, "3日排行": 3, "5日排行": 5, "10日排行": 10}

_MIN_HISTORY = 6  # 5 日变化率需 6 个快照点

_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS sector_flow_snapshots (
    date TEXT NOT NULL,
    industry TEXT NOT NULL,
    window_days INTEGER NOT NULL,
    net_flow REAL,
    in_flow REAL,
    out_flow REAL,
    chg_pct REAL,
    leader TEXT,
    leader_chg REAL,
    net_merged INTEGER NOT NULL DEFAULT 0,
    collected_at TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (date, industry, window_days)
);"""

# 模块级 kv 元数据（R16：漂移基线等）
_META_DDL = """
CREATE TABLE IF NOT EXISTS sector_flow_meta (
    key TEXT PRIMARY KEY,
    value TEXT
);"""

# 读侧高频查询索引（industry + window_days + date）——避免 WHERE industry 全表扫描
_IDX_DDL = (
    "CREATE INDEX IF NOT EXISTS idx_sfs_ind "
    "ON sector_flow_snapshots(industry, window_days, date)"
)


def _str_col(row: dict, *names: str) -> str | None:
    """字符串列（含 ''/nan/'None'/pd.NA 守卫，大小写不敏感）。"""
    for name in names:
        v = row.get(name)
        if v is None:
            continue
        s = str(v).strip()
        if not s or s.lower() in ("nan", "-nan", "none", "<na>", "nat"):
            continue
        return s
    return None


def _col_pct(row: dict, *names: str) -> float | None:
    """涨跌幅列：排行类为带 % 字符串（如 "16.96%"），先 rstrip % 再解析。"""
    from lib.nums import safe_float

    v = _str_col(row, *names)
    if v is None:
        return None
    return safe_float(v.rstrip("%"))


# ---------------------------------------------------------------------------
# 采集信封
# ---------------------------------------------------------------------------


def fetch_sector_flow_snapshot() -> dict[str, Any]:
    """调 ak.stock_fund_flow_industry（即时/3日/5日/10日 四窗口）。

    Returns
    -------
    dict
        {date: "YYYYMMDD", available: bool,
         industries: {行业名: {1: {net,in,out,chg,leader,leader_chg},
                               3: {net,in,out,chg}, 5: {...}, 10: {...}}},
         errors: [str]}   # 单窗口失败记入 errors 不整体失败；全部失败 → available=False
    """
    from dates import shanghai_today
    from lib.nums import coalesce_field
    from lib.proxy import akshare_direct_session

    today = shanghai_today()
    industries: dict[str, dict] = {}
    errors: list[str] = []
    try:
        import akshare as ak

        with akshare_direct_session():
            for symbol, wd in _WINDOW_MAP.items():
                try:
                    df = ak.stock_fund_flow_industry(symbol=symbol)
                except Exception as exc:
                    errors.append(f"{symbol}: {exc}")
                    continue
                if df is None or df.empty:
                    errors.append(f"{symbol}: empty")
                    continue
                for _, row in df.iterrows():
                    name = _str_col(row, "行业")
                    if not name:
                        continue
                    d = industries.setdefault(name, {})
                    d[wd] = {
                        "net": coalesce_field(row, "净额", "主力净流入"),
                        "in": coalesce_field(row, "流入资金"),
                        "out": coalesce_field(row, "流出资金"),
                        "chg": _col_pct(row, "阶段涨跌幅", "行业-涨跌幅"),
                        "leader": _str_col(row, "领涨股") if wd == 1 else None,
                        "leader_chg": coalesce_field(row, "领涨股-涨跌幅") if wd == 1 else None,
                    }
    except Exception as exc:
        errors.append(f"akshare: {exc}")
    return {
        "date": today,
        "available": bool(industries),
        "industries": industries,
        "errors": errors,
    }


# ---------------------------------------------------------------------------
# 写库（C2：DDL 模块内自包含，不改 store.py init_db；C5：非交易日全等检测）
# ---------------------------------------------------------------------------


def _ensure_table() -> None:
    """自包含建表 + 读侧索引 + meta 表。仅写入路径调用；读路径按 OperationalError 降级。"""
    from lib.store import _connection

    with _connection() as c:
        c.execute(_TABLE_DDL)
        c.execute(_IDX_DDL)
        c.execute(_META_DDL)
        # R16：net_merged 列（既有表幂等补列，不重建）
        try:
            c.execute(
                "ALTER TABLE sector_flow_snapshots "
                "ADD COLUMN net_merged INTEGER NOT NULL DEFAULT 0"
            )
        except sqlite3.OperationalError:
            pass  # 列已存在（新库 DDL 已含）
        c.commit()


def _read_log(exc: Exception) -> None:
    """读路径异常分级：表不存在（首次运行）静默；库损坏/锁等真异常记日志。"""
    if isinstance(exc, sqlite3.OperationalError) and "no such table" in str(exc):
        return
    logger.warning("sector_flow read failed: %s", exc)


def _latest_saved_date() -> str | None:
    """表内最大 date（YYYYMMDD 字典序）；表不存在/无行 → None。"""
    from lib.store import _connection

    with _connection() as c:
        try:
            row = c.execute(
                "SELECT MAX(date) AS d FROM sector_flow_snapshots"
            ).fetchone()
            return row["d"] if row and row["d"] else None
        except Exception as exc:
            _read_log(exc)
            return None


def _rows_for_date(date: str) -> dict[int, dict[str, float | None]]:
    """date 日全部 (industry, window_days) 行 → {wd: {industry: net_flow}}（一次查询）。"""
    from lib.store import _connection

    with _connection() as c:
        try:
            rows = c.execute(
                "SELECT industry, window_days, net_flow "
                "FROM sector_flow_snapshots WHERE date = ?",
                (date,),
            ).fetchall()
        except Exception as exc:
            _read_log(exc)
            return {}
    out: dict[int, dict[str, float | None]] = {}
    for r in rows:
        out.setdefault(r["window_days"], {})[r["industry"]] = r["net_flow"]
    return out


def _is_trading_day(d: str) -> bool | None:
    """日历判定交易日（C5）：True=交易日 / False=权威日历非交易日 / None=日历不可用。

    估算日历（无 token，is_estimated=True）对「非交易日」不信任 → None，
    由调用方走原全等跳过（防调休工作日被估算误判为休市而丢数据）。
    """
    try:
        from lib.trade_cal import fetch_trade_cal
    except Exception:
        return None
    try:
        dates, estimated = fetch_trade_cal(d, d)
    except Exception as exc:
        logger.warning("trade_cal 查询失败，回退全等判定: %s", exc)
        return None
    if d in dates:
        return True
    return None if estimated else False


def _same_as_latest(
    industries: dict[str, dict],
    wd: int,
    saved_by_wd: dict[int, dict[str, float | None]],
) -> bool | None:
    """本窗口与最近已存日期全等比较（C5 非交易日检测）。

    Returns
    -------
    bool | None
        True   当前窗口有数据、行业名单与最近快照完全一致且净额全部相等（容差内）；
        False  名单增删、任一净额不等、任一侧净额缺失、或最近快照缺本窗口
              （上次部分写入 → 本次恢复/补齐必须写入，防 C5 门丢弃恢复数据）；
        None   当前快照本窗口无数据（取数失败）→ 不可比，不参与判定。
    """
    saved = saved_by_wd.get(wd)
    if not saved:
        # 最近快照缺本窗口（上次部分写入）→ 有变化，应写入。若判为 None（不可比），
        # 其余窗口全等时 `all(comparable)` 会错误跳过，恢复的窗口数据被静默丢弃
        return False
    cur = {
        ind: (windows.get(wd) or {}).get("net")
        for ind, windows in industries.items()
        if wd in windows
    }
    if not cur:
        return None
    # 名单增减（THS 行业改名/新增/消失）→ 有变化，保守不跳过（防数据丢失）
    if set(saved) != set(cur):
        return False
    # 任一侧净额缺失 → 视为有变化（永不因 NULL 跳过，防序列冻结）
    if any(saved[ind] is None or v is None for ind, v in cur.items()):
        return False
    # 浮点位漂移容差（数据两位小数，1e-9 亿远低于显示精度）
    return all(
        math.isclose(saved[ind], v, rel_tol=1e-9, abs_tol=1e-9)
        for ind, v in cur.items()
    )


def save_sector_flow_snapshot(
    snapshot: dict[str, Any] | None = None, *, date: str | None = None
) -> dict[str, Any]:
    """采集 + 写库（信封缺省内部 fetch）。非交易日/无变化 → skipped。

    Returns {date, rows_saved, skipped, error, note}；
    skipped=True 时 rows_saved=0（可比窗口全部与最近快照全等：日历判定非交易日
    或疑似盘前未刷新；窗口取数失败 → 该窗口不可比；行业名单变化/任一侧净额缺失
    → 判定不等 → 正常写入）。
    """
    from lib.db_util import upsert_daily_rows
    from lib.store import _connection, init_db

    if snapshot is None:
        snapshot = fetch_sector_flow_snapshot()
    d = (date or snapshot.get("date") or "").strip()
    if not (d.isdigit() and len(d) == 8):
        return {"date": d, "rows_saved": 0, "skipped": False,
                "error": f"date 非法: {d!r}（需 YYYYMMDD）", "note": None}
    if not snapshot.get("available"):
        return {
            "date": d,
            "rows_saved": 0,
            "skipped": False,
            "error": "同花顺行业资金流不可用: " + "; ".join(snapshot.get("errors") or []),
            "note": None,
        }

    try:
        init_db()
        _ensure_table()
    except Exception as exc:
        return {"date": d, "rows_saved": 0, "skipped": False,
                "error": f"db init failed: {exc}", "note": None}
    industries: dict = snapshot["industries"]

    # C5：可比窗口全部与最近已存日期全等 → 疑似非交易日/盘前未刷新，跳过写入
    latest = _latest_saved_date()
    if latest:
        saved_by_wd = _rows_for_date(latest)
        results = [
            _same_as_latest(industries, wd, saved_by_wd)
            for wd in _WINDOW_MAP.values()
        ]
        comparable = [r for r in results if r is not None]
        if comparable and all(comparable):
            # 日历仅用于确认跳过（权威非交易日）；估算/不可用 → 原全等判定
            if _is_trading_day(d) is False:
                note = "非交易日（日历判定），跳过写入"
            else:
                note = f"数据与 {latest} 全等，无变化（疑似盘前未刷新），跳过写入"
            return {
                "date": d,
                "rows_saved": 0,
                "skipped": True,
                "error": None,
                "note": note,
            }

    # merge upsert：同日重跑时新值非 NULL 覆盖、NULL 保留旧值（防 NaN 解析
    # 冲掉当日早先采集的有效值；collected_at 冲突时保留首写值）
    rows: list[dict[str, Any]] = [
        {
            "date": d, "industry": ind, "window_days": wd,
            "net_flow": v.get("net"), "in_flow": v.get("in"),
            "out_flow": v.get("out"), "chg_pct": v.get("chg"),
            "leader": v.get("leader"), "leader_chg": v.get("leader_chg"),
        }
        for ind, windows in industries.items()
        for wd, v in windows.items()
    ]
    # R16 merge 陈旧值标记：净额解析失败（None）且同日已存行有非 None 净额
    # → 该行将由 COALESCE 填充旧值，net_merged=1（趋势侧剔除，防陈旧值当
    # 有效快照）。后写入真实值 → 探测不到填充 → 标记归零，净额与标记同源。
    with _connection() as c:
        try:
            existing = {
                (r["date"], r["industry"], r["window_days"]): r["net_flow"]
                for r in c.execute(
                    "SELECT date, industry, window_days, net_flow "
                    "FROM sector_flow_snapshots WHERE date = ?",
                    (d,),
                ).fetchall()
            }
        except Exception as exc:
            _read_log(exc)
            existing = {}
    for row in rows:
        pk = (row["date"], row["industry"], row["window_days"])
        row["net_merged"] = (
            1 if row["net_flow"] is None and pk in existing and existing[pk] is not None
            else 0
        )
    with _connection() as c:
        try:
            saved = upsert_daily_rows(
                c, "sector_flow_snapshots", rows,
                pk=("date", "industry", "window_days"), merge=True,
            )
            c.commit()
        except Exception as exc:
            c.rollback()
            return {"date": d, "rows_saved": 0, "skipped": False,
                    "error": f"db write failed: {exc}", "note": None}
    return {"date": d, "rows_saved": saved, "skipped": False, "error": None,
            "note": None}


# ---------------------------------------------------------------------------
# 读侧：积累序列 + 单时点分解 + 查询编排
# ---------------------------------------------------------------------------


def load_sector_flow_history(
    industry: str, window_days: int = 3, days: int = 10
) -> list[dict[str, Any]]:
    """积累序列（date ASC）。表不存在/无行 → []（读侧不建表，降级同 etf_share_flow）。"""
    from lib.db_util import load_recent_rows
    from lib.store import _connection

    with _connection() as c:
        try:
            rows = load_recent_rows(
                c, "sector_flow_snapshots", limit=days, order_col="date",
                where="industry = ? AND window_days = ?",
                params=(industry, window_days),
            )
        except Exception as exc:
            _read_log(exc)
            return []
    return [
        {"date": r["date"], "net": r["net_flow"], "in": r["in_flow"],
         "out": r["out_flow"], "chg": r["chg_pct"],
         "net_merged": r.get("net_merged", 0)}  # 旧库（未补列）读路径缺省 0
        for r in rows
    ]


# 浮点零判定与强度判定下限
_ZERO_EPS = 1e-9
_FLOW_EPS = 1.0  # 日均强度判定下限（亿/日）：微量级不输出「加速/减速」


def _fmt_opt(v: float | None) -> str:
    return f"{v}" if v is not None else "—"


def decompose_flow(d3: float | None, d5: float | None, d10: float | None) -> dict[str, Any]:
    """单时点窗口分解：近端=d3（近 3 日净额），中段=mid=d10-d3（第 4-10 日，7 日净额）。

    判定规则（sign 组合 4 象限；零值边界统一用 _ZERO_EPS）：
      (+,+) → 持续净流入；detail 按日均强度比 r=(d3/3)/(|mid|/7)：
              r≥1.2 近端加速 / r≤0.8 近端减速 / 否则 节奏平稳
      (+,-) → 近端回流（近端转正，资金回流）；detail=中段净流出 |mid| 亿
      (-,+) → 近端退潮（近端转负，资金退潮）；detail=中段净流入 |mid| 亿
      (-,-) → 持续净流出；detail 强度规则同 (+,+)
      d3≈0 且 mid≈0 → 净额近零（两端均无方向，不做流入/流出断言）
      d3≈0 且 |mid| 有量 → 近端归零（方向只由中段决定，标注零值边界；量级守卫看中段日均）
      mid≈0 且 |d3| 有量 → 中段归零：方向由近端决定（避免 (+,0) 误判「近端回流」；量级守卫看近端日均）
      任一日均强度 < _FLOW_EPS → 标注「金额量级小，强度不适用」（所有方向分支，含两个归零分支）
      d3/d10 任一 None → 数据不足
    d5 仅作信息字段（消费方自 latest 行直取），本函数不参与判定也不回传。
    """
    if d3 is None or d10 is None:
        return {
            "mid_7d": None,
            "label": "数据不足",
            "label_detail": f"缺 3 日({_fmt_opt(d3)})/10 日({_fmt_opt(d10)})窗口净额",
        }
    mid = d10 - d3
    if abs(d3) < _ZERO_EPS and abs(mid) < _ZERO_EPS:
        # 近端与中段均 ≈ 0：无方向事实，不做「流入/流出」断言
        return {"mid_7d": 0.0, "label": "净额近零",
                "label_detail": "近端与中段净额均 ≈ 0"}
    if abs(d3) < _ZERO_EPS:
        # 近端归零（d3 无方向，零值边界）：方向只由中段决定；量级守卫看主导侧（中段）
        mid_dir = "净流入" if mid > 0 else "净流出"
        note = "" if abs(mid) / 7 >= _FLOW_EPS else "（金额量级小，强度不适用）"
        return {"mid_7d": round(mid, 2), "label": "近端归零",
                "label_detail": f"近端净额 ≈ 0（零值边界），中段{mid_dir} {abs(mid):.2f} 亿{note}"}
    if abs(mid) < _ZERO_EPS:
        # 中段 7 日净额 ≈ 0：方向由近端决定；「回流/退潮」语义要求中段有方向，不适用
        label = "持续净流入" if d3 > 0 else "持续净流出"
        note = "" if abs(d3) / 3 >= _FLOW_EPS else "（金额量级小，强度不适用）"
        detail = f"中段 7 日净额 ≈ 0.00 亿（近端 {d3:+.2f} 亿），近端主导{note}"
        return {"mid_7d": 0.0, "label": label, "label_detail": detail}
    s3 = 1 if d3 > 0 else -1
    sm = 1 if mid > 0 else -1

    def _strength() -> str:
        daily_mid = abs(mid) / 7
        daily_d3 = abs(d3) / 3
        if daily_mid < _FLOW_EPS or daily_d3 < _FLOW_EPS:
            return "节奏平稳（金额量级小，强度不适用）"
        r = daily_d3 / daily_mid
        if r >= 1.2:
            return f"近端加速（日均强度 r={r:.2f}）"
        if r <= 0.8:
            return f"近端减速（日均强度 r={r:.2f}）"
        return f"节奏平稳（日均强度 r={r:.2f}）"

    def _magnitude_note() -> str:
        daily_mid = abs(mid) / 7
        daily_d3 = abs(d3) / 3
        if daily_mid < _FLOW_EPS or daily_d3 < _FLOW_EPS:
            return "（金额量级小，强度不适用）"
        return ""

    if s3 > 0 and sm > 0:
        label, detail = "持续净流入", _strength()
    elif s3 > 0 and sm < 0:
        label, detail = "近端回流", f"中段净流出 {abs(mid):.2f} 亿{_magnitude_note()}"
    elif s3 < 0 and sm > 0:
        label, detail = "近端退潮", f"中段净流入 {abs(mid):.2f} 亿{_magnitude_note()}"
    else:
        label, detail = "持续净流出", _strength()
    return {"mid_7d": round(mid, 2), "label": label, "label_detail": detail}


def _sequence_trend(industry: str, window_days: int = 3) -> dict[str, Any]:
    """积累序列趋势：≥6 日 → {change_5d, turn_5d, span_days}；不足 → sufficient=False。

    change_5d = net[最新] - net[第 6 旧]（亿元）；turn_5d = 5 日前 vs 最新符号翻转。
    span_days = 基线快照至最新快照的自然日跨度（标准日采 6 快照 = 5 交易日
    ≈ 7 自然日；≠7 说明有缺采/长假，报告层须标注）。valid_days = 剔除 NULL 及
    net_merged（merge 填充的陈旧值）后的有效快照数（history_days 为含 NULL 的
    采集天数，两语义分离；R16 起陈旧值不参与趋势，剔除后不足则诚实缺省）。
    """
    from datetime import datetime

    hist = load_sector_flow_history(industry, window_days, days=_MIN_HISTORY)
    valid = [
        (r["date"], r["net"]) for r in hist
        if r["net"] is not None and not r.get("net_merged")
    ]
    nets = [net for _, net in valid]
    if len(nets) < _MIN_HISTORY:
        return {"change_5d": None, "turn_5d": None, "history_days": len(hist),
                "valid_days": len(nets), "span_days": None, "sufficient": False}
    latest, older = nets[-1], nets[-_MIN_HISTORY]
    change_5d = round(latest - older, 2)
    if older > 0 and latest < 0:
        turn = "转向流出"
    elif older < 0 and latest > 0:
        turn = "转向流入"
    else:
        turn = "方向未变"
    span_days = (
        datetime.strptime(valid[-1][0], "%Y%m%d")
        - datetime.strptime(valid[-_MIN_HISTORY][0], "%Y%m%d")
    ).days
    return {"change_5d": change_5d, "turn_5d": turn, "history_days": len(hist),
            "valid_days": len(nets), "span_days": span_days, "sufficient": True}


def _history_days() -> int:
    """表内积累交易日数（COUNT(DISTINCT date)）；表不存在 → 0。"""
    from lib.store import _connection

    with _connection() as c:
        try:
            row = c.execute(
                "SELECT COUNT(DISTINCT date) AS n FROM sector_flow_snapshots"
            ).fetchone()
            return int(row["n"]) if row and row["n"] else 0
        except Exception as exc:
            _read_log(exc)
            return 0


def query_sector_flow(symbol: str) -> dict[str, Any]:
    """ETF → sw_code（etf_data.ETF_TO_SW_INDUSTRY）→ THS 细分 → 逐行业资金流 + 趋势。

    Returns
    -------
    dict
        {symbol, sw_code, sw_name, available, as_of,
         industries: [{industry, net_1d, net_3d, net_5d, net_10d, chg_10d,
                       trend_label, trend_detail, trend_5d, turn_5d,
                       trend_span_days}],
         history_days, notes}

    ETF 未映射或 THS 行业映射缺失 → available=False + note；
    单行业序列积累不足 → notes 行级提示「积累中」；单行业缺失不阻断。
    """
    from etf_data import ETF_TO_SW_INDUSTRY

    sw = ETF_TO_SW_INDUSTRY.get(symbol)
    if not sw:
        return {
            "symbol": symbol,
            "sw_code": None,
            "sw_name": None,
            "available": False,
            "as_of": None,
            "industries": [],
            "history_days": 0,
            "notes": ["未映射申万行业（ETF_TO_SW_INDUSTRY 无此代码）"],
        }
    ths = SW_TO_THS_INDUSTRY.get(sw["sw_code"])
    if not ths:
        return {
            "symbol": symbol,
            "sw_code": sw["sw_code"],
            "sw_name": sw["sw_name"],
            "available": False,
            "as_of": None,
            "industries": [],
            "history_days": 0,
            "notes": [f"THS 行业映射缺失（SW_TO_THS_INDUSTRY 无 {sw['sw_code']}），请补充映射"],
        }
    notes: list[str] = []
    history_days = _history_days()
    if history_days == 0:
        notes.append("无采集数据，先运行 collect-sector-flow（盘后每日触发）")
    elif history_days < _MIN_HISTORY:
        notes.append(f"序列积累中（{history_days} 日 < {_MIN_HISTORY} 日），5 日变化率/转向待积累")

    # 最新日期行一次查出（避免每行业 MAX(date) 子查询的 N+1）
    as_of = _latest_saved_date()
    latest_by_ind: dict[str, dict[int, dict[str, Any]]] = {}
    if as_of:
        from lib.store import _connection

        with _connection() as c:
            try:
                rows = c.execute(
                    "SELECT industry, window_days, net_flow, chg_pct "
                    "FROM sector_flow_snapshots WHERE date = ?",
                    (as_of,),
                ).fetchall()
            except Exception as exc:
                _read_log(exc)
                rows = []
        for r in rows:
            latest_by_ind.setdefault(r["industry"], {})[r["window_days"]] = {
                "net": r["net_flow"], "chg": r["chg_pct"]}

    industries: list[dict[str, Any]] = []
    for ind in ths:
        latest = latest_by_ind.get(ind)
        if latest:
            dec = decompose_flow(
                (latest.get(3) or {}).get("net"),
                (latest.get(5) or {}).get("net"),
                (latest.get(10) or {}).get("net"),
            )
        else:
            # 最新快照缺失（THS 改名/删行业等）→ 趋势字段缺省并提示，
            # 不与旧历史趋势混排（防单行自相矛盾）
            dec = {"label": "数据不足", "label_detail": "无最新快照"}
            notes.append(f"「{ind}」无最新快照（可能映射漂移/改名），趋势缺省")
        seq = _sequence_trend(ind)
        if history_days >= _MIN_HISTORY and not seq["sufficient"]:
            notes.append(
                f"「{ind}」序列积累中（{seq['valid_days']} 个有效快照 "
                f"< {_MIN_HISTORY}，含缺失日）"
            )
        industries.append(
            {
                "industry": ind,
                "net_1d": (latest.get(1) or {}).get("net") if latest else None,
                "net_3d": (latest.get(3) or {}).get("net") if latest else None,
                "net_5d": (latest.get(5) or {}).get("net") if latest else None,
                "net_10d": (latest.get(10) or {}).get("net") if latest else None,
                "chg_10d": (latest.get(10) or {}).get("chg") if latest else None,
                "trend_label": dec["label"],
                "trend_detail": dec["label_detail"],
                "trend_5d": seq["change_5d"] if latest else None,
                "turn_5d": seq["turn_5d"] if latest else None,
                "trend_span_days": seq.get("span_days") if latest else None,
            }
        )
    return {
        "symbol": symbol,
        "sw_code": sw["sw_code"],
        "sw_name": sw["sw_name"],
        "available": True,
        "as_of": as_of,
        "industries": industries,
        "history_days": history_days,
        "notes": notes,
    }


def check_mapping_coverage(snapshot: dict[str, Any]) -> list[str]:
    """SW_TO_THS_INDUSTRY 中不在最新快照名单的行业名（首次在线采集后自检）。"""
    wanted = {ind for lst in SW_TO_THS_INDUSTRY.values() for ind in lst}
    have = set(snapshot.get("industries") or {})
    return sorted(wanted - have)


def _unmapped_industries(snapshot: dict[str, Any]) -> set[str]:
    """快照名单中不在 SW_TO_THS_INDUSTRY 的行业（漂移检测共同基）。"""
    wanted = {ind for lst in SW_TO_THS_INDUSTRY.values() for ind in lst}
    have = set(snapshot.get("industries") or {})
    return have - wanted


_DRIFT_BASELINE_KEY = "sector_flow_drift_baseline"


def load_drift_baseline() -> set[str] | None:
    """漂移基线（已确认的未映射行业集）；无基线/读取失败 → None（调用方重新建立）。"""
    from lib.store import _connection

    with _connection() as c:
        try:
            row = c.execute(
                "SELECT value FROM sector_flow_meta WHERE key = ?",
                (_DRIFT_BASELINE_KEY,),
            ).fetchone()
        except Exception as exc:
            _read_log(exc)
            return None
    if not row or not row["value"]:
        return None
    return set(json.loads(row["value"]))


def save_drift_baseline(snapshot: dict[str, Any]) -> int | None:
    """建立/更新漂移基线 = 当前快照未映射行业全集；返回基线行业数，失败 → None。"""
    from lib.store import _connection, init_db

    baseline = sorted(_unmapped_industries(snapshot))
    try:
        init_db()
        _ensure_table()
        with _connection() as c:
            c.execute(
                "INSERT INTO sector_flow_meta (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (_DRIFT_BASELINE_KEY, json.dumps(baseline)),
            )
            c.commit()
    except Exception as exc:
        logger.warning("save_drift_baseline failed: %s", exc)
        return None
    return len(baseline)


def check_snapshot_drift(
    snapshot: dict[str, Any], baseline: set[str] | None = None
) -> list[str]:
    """快照名单中不在 SW_TO_THS_INDUSTRY 的行业（THS 新增/改名/拆分，反向漂移）。

    与 check_mapping_coverage 互补：正向检查映射表行业是否缺采，本函数检查
    THS 侧出现的新行业名。baseline 为 save_drift_baseline 建立的已知未映射集：
    传 None → 返回全量未映射（一次性核对）；传基线 → 仅报告相对基线的**新增**
    行业，使真实漂移（改名/拆分产生的新行业名）与设计内的大量未映射行业
    （映射表仅 39 行业，THS 快照约 90）可区分，不再每次全量刷警告。
    告警级，不阻断采集。
    """
    drift = _unmapped_industries(snapshot)
    if baseline:
        drift -= set(baseline)
    return sorted(drift)
