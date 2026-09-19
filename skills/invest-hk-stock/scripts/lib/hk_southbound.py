"""南向资金（HK-3 / T11-1）——港股通（沪/深）每日净买入。

**研究视角的市场结构观察，非交易信号**（LAW 6）。

口径纪律（2026-09-12 实测，勿按直觉改，详见 `round-plans/r3-20260912.md` §1.1）：

- 首选 akshare ``stock_hsgt_hist_em(symbol="港股通沪"/"港股通深")``——日频 2713 行，
  ``当日成交净买额`` 为**亿元**且是**唯一可用净额列**；``当日资金流入`` / ``当日余额``
  两列实测**恒 NaN**，故解析层不产出对应键（填 0 会被读成「当日零流入」这一事实断言）。
- 降级 tushare ``moneyflow_hsgt``——⚠️ **累计口径**：``ggt_ss``（沪）/ ``ggt_sz``（深）/
  ``south_money``（合计）均为累计值，**必须差分**才是当日净额。直接引用会把
  44 亿的当日净买入说成 550 亿（2026-09-12 同日实测：``32089.12 − 32057.2 = 31.92``
  与 akshare 沪向 31.9191 吻合）。
- 汇总 ``stock_hsgt_fund_flow_summary_em()`` 的 ``交易状态`` / ``资金净流入`` /
  ``当日资金余额`` 三列**语义未核实**（``资金净流入`` 实测恒 420.0，疑为**每日额度**
  而非净流入）→ 登记进 ``unused_fields``，不猜语义、不上报告。
"""
from __future__ import annotations

import datetime as _dt
import logging

logger = logging.getLogger(__name__)

DIRECT_SYMBOLS = ("港股通沪", "港股通深")

# akshare 日频列名（2026-09-12 实测）
_DATE_COL = "日期"
_NET_COL = "当日成交净买额"
_BUY_COL = "买入成交额"
_SELL_COL = "卖出成交额"
_CUM_COL = "历史累计净买额"
_HSI_COL = "恒生指数"
_HSI_CHG_COL = "恒生指数-涨跌幅"

# 实测：`历史累计净买额` 单位为**万亿元**（3.208912 ×1e4 = 32089.12 亿元，
# 与 tushare 同日 ggt_ss 32089.12 精确吻合）
_CUM_TRILLION_TO_YI = 1e4

# akshare 汇总列名 + 语义未核列（显式登记，不上报告）
_SUMMARY_SOURCE_COL = "板块"
_SUMMARY_DIRECTION_COL = "资金方向"
_SUMMARY_DATE_COL = "交易日"
_SUMMARY_BOARDS = {"港股通(沪)": "sh", "港股通(深)": "sz"}
UNUSED_SUMMARY_FIELDS = ("交易状态", "资金净流入", "当日资金余额")

# tushare 累计口径字段
_TS_DATE_COL = "trade_date"
_TS_FIELDS = ("ggt_ss", "ggt_sz", "south_money")
_TS_SOURCE = "tushare.moneyflow_hsgt（累计口径差分）"
_CALIBER_NOTE = ("⚠️ tushare 为**累计口径**，本表数值为相邻可得行的**差分**"
                 "（差值 = 当日净买额）；直接引用源值会得到 550 亿级的量级错误。")

_CROSS_TOL_YI = 0.05   # 同日双源容差（亿元）


# ---------------------------------------------------------------------------
# 解析层（纯函数，离线可测）
# ---------------------------------------------------------------------------

def _to_float(v) -> float | None:
    """数值 → float；None / 非数值 / NaN → None（三态，**不填 0**）。"""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f:          # NaN
        return None
    return f


def _norm_date(v) -> str | None:
    """``YYYY-MM-DD`` / ``YYYYMMDD`` / date / datetime → ISO 日期串；不可解析 → None。

    ⚠️ 走共享 `dates.parse_date`，**不自实现 isinstance 分支**：pandas `NaT` 是
    `datetime` 的伪子类，`NaT.strftime()` 会抛 `ValueError`——自实现时该异常会穿透
    `southbound_summary()`（其 try 只包住取数）一路冒到 `cmd_report`，整份报告崩掉
    且不留档。`parse_date` 在 datetime 分支内显式判 NaT。
    """
    if v is None:
        return None
    try:
        from _invest_path import ensure_invest_a_scripts_on_path

        ensure_invest_a_scripts_on_path()
        from dates import parse_date

        d = parse_date(v)
        return d.strftime("%Y-%m-%d") if d else None
    except Exception:  # noqa: BLE001 —— 日期脏值不得崩报告：一律按不可解析处理
        logger.warning("日期解析失败，按不可解析处理：%r", v)
        return None


def _cal_days(d0: str, d1: str) -> int | None:
    """两个 ISO 日期之间的自然日数；不可解析 → None。"""
    try:
        return (_dt.date.fromisoformat(d1) - _dt.date.fromisoformat(d0)).days
    except (TypeError, ValueError):
        return None


def parse_hist_rows(df) -> list[dict]:
    """akshare 日频帧 → 行列表（按日期升序）。

    每行：``date`` / ``net_buy_yi``（亿元）/ ``buy_yi`` / ``sell_yi`` /
    ``cum_yi``（累计净买额，亿元）/ ``hsi`` / ``hsi_chg_pct``。
    恒 NaN 的两列**不产出键**（三态由 ``None`` 表达，绝不填 0）。
    """
    if df is None or getattr(df, "empty", True):
        return []
    rows: list[dict] = []
    for _, r in df.iterrows():
        date = _norm_date(r.get(_DATE_COL))
        if not date:
            continue
        cum = _to_float(r.get(_CUM_COL))
        rows.append({
            "date": date,
            "net_buy_yi": _to_float(r.get(_NET_COL)),
            "buy_yi": _to_float(r.get(_BUY_COL)),
            "sell_yi": _to_float(r.get(_SELL_COL)),
            "cum_yi": None if cum is None else cum * _CUM_TRILLION_TO_YI,
            "hsi": _to_float(r.get(_HSI_COL)),
            "hsi_chg_pct": _to_float(r.get(_HSI_CHG_COL)),
        })
    return sorted(rows, key=lambda x: x["date"])


def diff_cumulative(rows: list[dict], field: str) -> list[dict]:
    """累计口径序列 → 当日净额（**相邻可得行**相减）。

    返回 ``[{"date", "prev_date", "value", "cal_days", "span"}]``。
    ``cal_days`` 为两行之间的**自然日**数（跨周末/长假 > 1），仅作标注——
    港股通只在两地同时开市时交易，故相邻行的差值恰为一个交易日的净额。

    缺行**不补 0 行**：补 0 会把「区间累计」拆成伪造的逐日数据。
    该 field 某行为 None 时**断开链**（不拿不可用点当基准）。
    空输入 raise ``ValueError``（D5：静默返回空列表会被读成「无净买入」）。
    """
    if not rows:
        raise ValueError("累计口径差分需要至少 2 个点；空输入是调用方 bug，不是「无净买入」")
    out: list[dict] = []
    prev: tuple[str, float] | None = None
    for r in sorted(rows, key=lambda x: str(x.get("date") or "")):
        date = _norm_date(r.get("date"))
        v = _to_float(r.get(field))
        if date is None or v is None:
            prev = None          # 不可用点：断开链，不参与差分
            continue
        if prev is None:
            prev = (date, v)
            continue
        prev_date, prev_v = prev
        cal = _cal_days(prev_date, date)
        out.append({
            "date": date,
            "prev_date": prev_date,
            "value": v - prev_v,
            "cal_days": cal,
            "span": "1 日" if cal == 1 else (f"跨 {cal} 自然日" if cal else "间隔未知"),
        })
        prev = (date, v)
    return out


def cross_check(ak_row: dict | None, ts_row: dict | None, tol: float = _CROSS_TOL_YI) -> dict:
    """同日双源对照：akshare 日频 ``total_yi`` ↔ tushare 差分 ``total_yi``。

    只在**同为一日**时可比——跨日/区间累计与单日值不同口径，不可对照（不裁决）。
    返回 ``{comparable, delta_yi, consistent, note}``；不可比时 ``consistent=None``
    （三态——「不知道」不得写成「不一致」）。
    """
    if not ak_row or not ts_row:
        return {"comparable": False, "delta_yi": None, "consistent": None,
                "note": "交叉源单侧缺失——不可比，不裁决"}
    a_date, t_date = _norm_date(ak_row.get("date")), _norm_date(ts_row.get("date"))
    if not a_date or a_date != t_date:
        return {"comparable": False, "delta_yi": None, "consistent": None,
                "note": f"日期不同（akshare {a_date} / tushare {t_date}）——不可比，不裁决"}
    a, t = _to_float(ak_row.get("total_yi")), _to_float(ts_row.get("total_yi"))
    if a is None or t is None:
        return {"comparable": False, "delta_yi": None, "consistent": None,
                "note": "任一侧合计值缺失——不可比，不裁决"}
    delta = a - t
    return {"comparable": True, "delta_yi": delta, "consistent": abs(delta) <= tol,
            "note": f"同日双源对照（容差 {tol} 亿）"}


# ---------------------------------------------------------------------------
# 取数层（叶子函数——测试注入点）
# ---------------------------------------------------------------------------

def _fetch_hist_df(symbol: str):
    """akshare 南向日频帧（按日期升序）；失败 → raise（降级链上一级捕获）。"""
    from _invest_path import ensure_invest_a_scripts_on_path

    ensure_invest_a_scripts_on_path()
    from lib.proxy import akshare_direct_session

    with akshare_direct_session():
        import akshare as ak

        df = ak.stock_hsgt_hist_em(symbol=symbol)
    if df is None or getattr(df, "empty", True):
        return None
    try:
        return df.sort_values(_DATE_COL).reset_index(drop=True)
    except Exception:  # noqa: BLE001 —— 列名漂移时原样返回，交由解析层三态
        logger.warning("南向日频帧无 %s 列，无法排序", _DATE_COL)
        return df


def _fetch_summary_df():
    """akshare 沪深港通当日汇总帧；失败 → raise。"""
    from _invest_path import ensure_invest_a_scripts_on_path

    ensure_invest_a_scripts_on_path()
    from lib.proxy import akshare_direct_session

    with akshare_direct_session():
        import akshare as ak

        df = ak.stock_hsgt_fund_flow_summary_em()
    if df is None or getattr(df, "empty", True):
        return None
    return df


def _fetch_tushare_df(days: int = 20):
    """tushare ``moneyflow_hsgt`` 原始帧（**累计口径**）；空返回 → raise。"""
    from _invest_path import ensure_invest_a_scripts_on_path

    ensure_invest_a_scripts_on_path()
    from dates import shanghai_today
    from lib.tushare_client import TushareClient

    today = _dt.date.fromisoformat(
        f"{shanghai_today()[:4]}-{shanghai_today()[4:6]}-{shanghai_today()[6:8]}")
    start = (today - _dt.timedelta(days=days * 2 + 15)).strftime("%Y%m%d")
    client = TushareClient()
    df = client.query("moneyflow_hsgt", start_date=start, end_date=today.strftime("%Y%m%d"))
    if df is None or getattr(df, "empty", True):
        raise RuntimeError(f"moneyflow_hsgt 空返回（last_error={client.last_error}）")
    return df


def _latest_common(rows_a: list[dict], rows_b: list[dict]) -> tuple[dict | None, dict | None]:
    """两序列中**共有的最新日期**那一对行；无交集 → ``(None, None)``。"""
    b_by_date = {r.get("date"): r for r in rows_b if r.get("date")}
    for r in sorted(rows_a, key=lambda x: str(x.get("date") or ""), reverse=True):
        other = b_by_date.get(r.get("date"))
        if other is not None:
            return r, other
    return None, None


def _parse_tushare_rows(frame) -> list[dict]:
    """tushare 帧 → ``[{date, ggt_ss, ggt_sz, south_money}]``（升序）。"""
    if frame is None or getattr(frame, "empty", True):
        return []
    out: list[dict] = []
    for _, r in frame.iterrows():
        date = _norm_date(r.get(_TS_DATE_COL))
        if not date:
            continue
        out.append({"date": date, **{f: _to_float(r.get(f)) for f in _TS_FIELDS}})
    return sorted(out, key=lambda x: x["date"])


# ---------------------------------------------------------------------------
# 业务层
# ---------------------------------------------------------------------------

def southbound_daily(days: int = 20) -> dict:
    """港股通沪/深日频合并 → ``{available, rows, source, note, warnings}``。

    单侧缺失 → 该侧与合计均为 ``None``（**不得拿沪向冒充合计**）。
    """
    warnings: list[str] = []
    by_date: dict[str, dict] = {}
    for symbol in DIRECT_SYMBOLS:
        try:
            parsed = parse_hist_rows(_fetch_hist_df(symbol))
        except Exception as exc:  # noqa: BLE001 —— 分侧降级，不阻断另一侧
            warnings.append(f"{symbol} 日频取数失败：{type(exc).__name__}")
            continue
        if not parsed:
            warnings.append(f"{symbol} 日频返回空（源停更或参数漂移？）")
            continue
        side = "sh_yi" if symbol == DIRECT_SYMBOLS[0] else "sz_yi"
        side_buy = f"{side[:2]}_buy_yi"
        side_sell = f"{side[:2]}_sell_yi"
        for r in parsed:
            slot = by_date.setdefault(r["date"], {
                "date": r["date"], "sh_yi": None, "sz_yi": None, "total_yi": None,
                "buy_yi": None, "sell_yi": None,
                "hsi": None, "hsi_chg_pct": None, "cum_yi": None})
            slot[side] = r["net_buy_yi"]
            slot[side_buy], slot[side_sell] = r["buy_yi"], r["sell_yi"]
            if r["hsi"] is not None:
                slot["hsi"], slot["hsi_chg_pct"] = r["hsi"], r["hsi_chg_pct"]
            if r["cum_yi"] is not None:
                slot["cum_yi"] = r["cum_yi"]

    rows = [by_date[d] for d in sorted(by_date)][-days:]
    for r in rows:
        if r["sh_yi"] is not None and r["sz_yi"] is not None:
            r["total_yi"] = r["sh_yi"] + r["sz_yi"]      # Python 求和（P0）
        # 买卖成交额：两侧齐备才求和（缺一侧即为 None——不拿单侧冒充两市合计）
        for out_key, sh_key, sz_key in (("buy_yi", "sh_buy_yi", "sz_buy_yi"),
                                        ("sell_yi", "sh_sell_yi", "sz_sell_yi")):
            if r.get(sh_key) is not None and r.get(sz_key) is not None:
                r[out_key] = r[sh_key] + r[sz_key]
    available = any(r["sh_yi"] is not None or r["sz_yi"] is not None for r in rows)
    return {
        "available": available,
        "rows": rows,
        "source": "akshare.stock_hsgt_hist_em（港股通沪/深）",
        "note": None if available else "港股通沪/深日频均不可得",
        "warnings": warnings,
    }


def southbound_summary() -> dict:
    """当日汇总（沪/深两行 + 涨跌家数 + 恒指涨跌幅）。

    ``unused_fields`` 显式登记语义未核列——不猜语义、不上报告。
    """
    note = ("`交易状态`/`资金净流入`/`当日资金余额` 三列语义未核实"
            "（`资金净流入` 实测恒 420.0，疑为每日额度而非净流入）→ 本模块不展示")
    try:
        df = _fetch_summary_df()
    except Exception as exc:  # noqa: BLE001
        logger.warning("南向汇总取数失败：%s", exc)
        return {"available": False, "source": "akshare.stock_hsgt_fund_flow_summary_em",
                "date": None, "sh": None, "sz": None, "hsi": None, "hsi_chg_pct": None,
                "unused_fields": list(UNUSED_SUMMARY_FIELDS),
                "note": f"南向汇总不可得：{type(exc).__name__}"}
    if df is None or getattr(df, "empty", True):
        return {"available": False, "source": "akshare.stock_hsgt_fund_flow_summary_em",
                "date": None, "sh": None, "sz": None, "hsi": None, "hsi_chg_pct": None,
                "unused_fields": list(UNUSED_SUMMARY_FIELDS),
                "note": "南向汇总空返回（源停更或列名漂移？）"}

    latest = None
    out: dict = {"available": True,
                 "source": "akshare.stock_hsgt_fund_flow_summary_em",
                 "date": None, "sh": None, "sz": None, "hsi": None, "hsi_chg_pct": None,
                 "unused_fields": list(UNUSED_SUMMARY_FIELDS), "note": note}
    for _, r in df.iterrows():
        if str(r.get(_SUMMARY_DIRECTION_COL) or "").strip() != "南向":
            continue
        board = str(r.get(_SUMMARY_SOURCE_COL) or "").strip()
        side = _SUMMARY_BOARDS.get(board)
        if side is None:
            continue
        date = _norm_date(r.get(_SUMMARY_DATE_COL))
        latest = date if latest is None or (date and date > latest) else latest
        out[side] = {
            "net_buy_yi": _to_float(r.get("成交净买额")),
            "up": _to_float(r.get("上涨数")),
            "flat": _to_float(r.get("持平数")),
            "down": _to_float(r.get("下跌数")),
        }
        chg = _to_float(r.get("指数涨跌幅"))
        if chg is not None:
            out["hsi_chg_pct"] = chg
    out["date"] = latest
    if out["sh"] is None and out["sz"] is None:
        out["available"] = False
        out["note"] = "南向汇总帧内无港股通沪/深行（结构漂移？）"
    return out


def fetch_tushare_cross(days: int = 20) -> dict:
    """tushare 交叉源（**累计口径差分**）→ 与 akshare 日频同形的行。"""
    try:
        parsed = _parse_tushare_rows(_fetch_tushare_df(days))
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "rows": [], "source": _TS_SOURCE,
                "caliber_note": _CALIBER_NOTE,
                "reason": f"tushare moneyflow_hsgt 取数失败：{type(exc).__name__}",
                "warnings": [f"tushare 交叉源不可得：{type(exc).__name__}"]}
    if len(parsed) < 2:
        return {"available": False, "rows": [], "source": _TS_SOURCE,
                "caliber_note": _CALIBER_NOTE,
                "reason": f"tushare 返回 {len(parsed)} 行，不足 2 点无法差分",
                "warnings": ["tushare 交叉源行数不足"]}

    d_sh = {r["date"]: r for r in diff_cumulative(parsed, "ggt_ss")}
    d_sz = {r["date"]: r for r in diff_cumulative(parsed, "ggt_sz")}
    rows: list[dict] = []
    for date in sorted(set(d_sh) | set(d_sz)):
        sh, sz = d_sh.get(date), d_sz.get(date)
        sh_v = sh["value"] if sh else None
        sz_v = sz["value"] if sz else None
        rows.append({
            "date": date,
            "prev_date": (sh or sz or {}).get("prev_date"),
            "sh_yi": sh_v,
            "sz_yi": sz_v,
            "total_yi": (sh_v + sz_v) if (sh_v is not None and sz_v is not None) else None,
            "cal_days": (sh or sz or {}).get("cal_days"),
            "span": (sh or sz or {}).get("span"),
            "hsi": None, "hsi_chg_pct": None, "cum_yi": None,
        })
    if not rows:
        # 帧可解析、日期也存在，但全部累计字段无法转成数值，通常是上游字段漂移。
        # 若返回 reason=None，调用方会把这次交叉校验当作「未尝试」而静默略过。
        reason = "tushare 累计字段无可用值（ggt_ss/ggt_sz 疑缺失或字段漂移）"
        return {"available": False, "rows": [], "source": _TS_SOURCE,
                "caliber_note": _CALIBER_NOTE, "reason": reason,
                "warnings": [f"tushare 交叉源不可得：{reason}"]}
    return {"available": bool(rows), "rows": rows[-days:], "source": _TS_SOURCE,
            "caliber_note": _CALIBER_NOTE, "reason": None, "warnings": []}


def fetch_southbound(days: int = 20) -> dict:
    """降级链入口：akshare 日频 → tushare 差分 → 仅当日汇总 → 三态不可得。

    返回 ``{available, rows, summary, cross, source, caliber_note, warnings, reason}``。
    ``available=False`` 时 ``rows == []`` 且 ``reason`` 非空——**不产空表冒充「零净买入」**。
    """
    daily = southbound_daily(days)
    summary = southbound_summary()
    ts = fetch_tushare_cross(days)
    warnings: list[str] = list(daily["warnings"]) + list(ts.get("warnings") or [])

    cross = None
    caliber_note = None
    if daily["available"]:
        rows, source = daily["rows"], daily["source"]
        if ts["available"] and rows:
            # 用**双方共有的最新日期**对照，而非各自末行——两源末行日期常不一致
            # （tushare 交收/发布节奏与 akshare 不同），拿末行直接比会让
            # cross_check 永久返回「不可比」，交叉核对形同虚设
            a_row, t_row = _latest_common(rows, ts["rows"])
            if a_row is not None:
                cross = cross_check(a_row, t_row)
    elif ts["available"]:
        rows, source = ts["rows"], ts["source"]
        caliber_note = _CALIBER_NOTE
        warnings.append("akshare 日频不可得 → 降级 tushare 累计口径差分")
    elif summary.get("available") and (
            (summary.get("sh") or {}).get("net_buy_yi") is not None
            or (summary.get("sz") or {}).get("net_buy_yi") is not None):
        # ⚠️ 汇总帧「有行」不等于「有净额」：净额列缺失时若照走此分支，
        # 会产出 available=True 但全 None 的行 → 报告出现
        # 「南向资金：可得（…合计 — 亿）」，反而**抑制了 LAW 5 的不可得标注**
        sh = (summary.get("sh") or {}).get("net_buy_yi")
        sz = (summary.get("sz") or {}).get("net_buy_yi")
        rows = [{"date": summary.get("date"), "sh_yi": sh, "sz_yi": sz,
                 "total_yi": (sh + sz) if (sh is not None and sz is not None) else None,
                 "hsi": None, "hsi_chg_pct": summary.get("hsi_chg_pct"),
                 "cum_yi": None, "prev_date": None, "cal_days": None, "span": None}]
        source = summary["source"]
        warnings.append("日频序列不可得 → 仅用当日汇总（无历史序列）")
    else:
        return {"available": False, "rows": [], "summary": summary, "cross": None,
                "source": None, "caliber_note": None, "warnings": warnings,
                "reason": "南向数据源全部不可得（akshare 日频/汇总 + tushare 交叉均失败）"}
    return {"available": True, "rows": rows, "summary": summary, "cross": cross,
            "source": source, "caliber_note": caliber_note, "warnings": warnings,
            "reason": None}
