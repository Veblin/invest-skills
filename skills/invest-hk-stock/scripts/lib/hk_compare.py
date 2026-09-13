"""双标的港股对照（T11-3 / HK-1 v2）——纯函数层。

**对照 ≠ 裁决**：本模块只把两侧的引擎字段**并列搬运**，不做「谁更便宜」之类的
结论（沿用 A 股 compare 的「口径差异，不裁决」纪律，也守住 LAW 6）。

P0 约束（由测试钉住）：每个单元格的值必须能在输入 payload 中原样找到；唯一例外是
**显式声明为派生**的行（元 → 亿），这类行在 ``calc`` 字段里带公式，渲染时输出
``[来源: Python calc: …]``。
"""
from __future__ import annotations

# (维度, 指标, payload 取值路径, 单位, 派生公式|None)
_ROW_SPECS: tuple[tuple[str, str, tuple[str, ...], str, str | None], ...] = (
    ("快照", "现价", ("snapshot", "price"), "HKD", None),
    ("快照", "涨跌幅", ("snapshot", "chg_pct"), "%", None),
    ("快照", "PE(TTM)", ("snapshot", "pe_ttm"), "x", None),
    ("快照", "总市值", ("snapshot", "mcap_hkd_yi"), "亿 HKD", None),
    ("快照", "52 周高", ("snapshot", "high_52w"), "HKD", None),
    ("快照", "52 周低", ("snapshot", "low_52w"), "HKD", None),
    ("估值位置", "PE 序列分位", ("valuation_pctl", "pe", "pct"), "%", None),
    ("估值位置", "PE 序列中位", ("valuation_pctl", "pe", "median"), "x", None),
    ("估值位置", "PE 序列交易日数", ("valuation_pctl", "pe", "n"), "日", None),
    ("估值位置", "PB 序列分位", ("valuation_pctl", "pb", "pct"), "%", None),
    # 分位**不得单独出现**（CLAUDE.md 估值分位使用规则 3：必须伴随中位数/均值）——
    # 缺这行会让 PB 分位成为无基准的孤立数字，且 lint 的
    # `percentile-without-median`（行级）会命中
    ("估值位置", "PB 序列中位", ("valuation_pctl", "pb", "median"), "x", None),
    ("估值位置", "PB 序列交易日数", ("valuation_pctl", "pb", "n"), "日", None),
    ("技术结构", "MA5", ("technical", "ma", "5"), "HKD", None),
    ("技术结构", "MA20", ("technical", "ma", "20"), "HKD", None),
    ("技术结构", "MA60", ("technical", "ma", "60"), "HKD", None),
    ("技术结构", "MACD DIF", ("technical", "macd", "dif"), "", None),
    ("技术结构", "MACD DEA", ("technical", "macd", "dea"), "", None),
    ("技术结构", "RSI(12)", ("technical", "rsi"), "", None),
    ("财务摘要", "最新报告期", ("financials", "latest", "report_date"), "", None),
    ("财务摘要", "营收", ("financials", "latest", "revenue_yi"), "亿", "源值元/1e8"),
    ("财务摘要", "归母净利", ("financials", "latest", "net_profit_yi"), "亿", "源值元/1e8"),
    ("财务摘要", "ROE", ("financials", "latest", "roe"), "%", None),
)

DIMENSIONS = tuple(dict.fromkeys(spec[0] for spec in _ROW_SPECS))


def _dig(obj, path: tuple[str, ...]):
    """按路径取值；任一层缺失/非 dict → None（三态，不抛）。"""
    cur = obj
    for key in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
        if cur is None:
            return None
    return cur


def _fmt(v, unit: str) -> str:
    """单元格文本；None → 「—」（三态，**不渲染 0**）。"""
    if v is None or v == "":
        return "—"
    if isinstance(v, str):
        return v
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    if unit == "%":
        return f"{f:+.1f}%"
    if unit == "日":
        return f"{f:,.0f}"
    return f"{f:,.2f}"


def build_compare(left: dict, right: dict) -> dict:
    """两侧 payload → 对照行。

    payload 形状（``hk.py:_collect_side`` 产出）::

        {"code", "name",
         "snapshot": {...}, "valuation_pctl": {"pe"|"pb": {...}|None},
         "financials": {"latest": {...}|None}, "technical": {...}}

    返回 ``{left_code, right_code, left_name, right_name, rows, unavailable}``；
    ``rows`` 每项 = ``{dimension, metric, unit, calc, left_raw, right_raw, left, right}``。
    """
    rows: list[dict] = []
    for dimension, metric, path, unit, calc in _ROW_SPECS:
        lv, rv = _dig(left, path), _dig(right, path)
        rows.append({"dimension": dimension, "metric": metric, "unit": unit, "calc": calc,
                     "left_raw": lv, "right_raw": rv,
                     "left": _fmt(lv, unit), "right": _fmt(rv, unit)})
    # 一个维度的首行只是展示顺序，不是可用性哨兵。例如 PE 分位缺失而 PB 分位
    # 已有值时，若只检查首行会把整块「估值位置」误标为不可得，并让 CLI 错退 1。
    unavailable = [d for d in DIMENSIONS
                   if not any(row["dimension"] == d
                              and (row["left_raw"] is not None
                                   or row["right_raw"] is not None)
                              for row in rows)]
    return {"left_code": left.get("code"), "right_code": right.get("code"),
            "left_name": left.get("name"), "right_name": right.get("name"),
            "rows": rows, "unavailable": unavailable}


def render_compare_table(cmp: dict) -> list[str]:
    """对照行 → markdown lines（含口径与免责；无方向性表述）。"""
    lc, rc = cmp.get("left_code") or "—", cmp.get("right_code") or "—"
    ln, rn = cmp.get("left_name") or "—", cmp.get("right_name") or "—"
    lines = [f"| 维度 | 指标 | {lc} {ln} | {rc} {rn} |", "|---|---|---|---|"]
    for r in cmp["rows"]:
        unit = f"（{r['unit']}）" if r["unit"] else ""
        # 派生标签放**指标列**（一次即涵盖左右两侧）；挂在单元格末尾会让左列看起来像
        # 引擎原值而右列才是派生值——同一指标两列口径不一致（实测踩坑）
        calc = f" [来源: Python calc: {r['calc']}]" if r.get("calc") else ""
        lines.append(f"| {r['dimension']} | {r['metric']}{unit}{calc} | {r['left']} | {r['right']} |")
    return lines
