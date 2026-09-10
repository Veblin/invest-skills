"""共享时效工具（v0.3.0 R1-H2）：行集容差比较 + 交易日历感知的滞后判定。

使用方：sector_flow（T7-1/2）、industry_snapshot（T7-3）。
设计要点（R1 验收审查 F7/F15 修正）：
- 滞后一律按**交易日**计（自然日口径在长假后必然误报「数据停更」）；
- 日历不可用 → 降级为自然日粗判并返回 degraded=True，调用方**必须在提示文本
  中显式标注**（守卫不得静默消失）；
- 行集比较收敛为单点实现（原 sector/weekly 两份近重复 helper 语义已分叉）。
"""

from __future__ import annotations

import math
from typing import Any

DEFAULT_TOL = 1e-9


def values_equal(a: Any, b: Any, *, tol: float = DEFAULT_TOL) -> bool:
    """数值或数值元组逐项容差相等；任一侧 None → False（NULL 永不判等）。"""
    if a is None or b is None:
        return False
    if isinstance(a, tuple) or isinstance(b, tuple):
        ta = a if isinstance(a, tuple) else (a,)
        tb = b if isinstance(b, tuple) else (b,)
        return len(ta) == len(tb) and all(
            values_equal(x, y, tol=tol) for x, y in zip(ta, tb)
        )
    try:
        return math.isclose(float(a), float(b), rel_tol=tol, abs_tol=tol)
    except (TypeError, ValueError):
        return a == b


def maps_equal(a: dict, b: dict, *, tol: float = DEFAULT_TOL) -> bool:
    """两层映射全等：键集一致 + 逐值 values_equal（值可为数或数值元组）。"""
    if set(a) != set(b):
        return False
    return all(values_equal(a[k], b[k], tol=tol) for k in a)


def trading_day_lag(as_of: str, session: str) -> tuple[int | None, bool]:
    """(as_of, session] 之间的交易日数 → (lag, degraded)。

    as_of/session 均为 YYYYMMDD（as_of 为数据日期，session 为最近交易日）。
    日历（lib.trade_cal）不可用 → (自然日差, True)；参数不可解析 → (None, True)。
    例外一律降级不抛——degraded=True 时调用方须在提示中标注「日历不可用」。
    """
    try:
        from datetime import datetime, timedelta

        d0 = datetime.strptime(str(as_of), "%Y%m%d").date()
        d1 = datetime.strptime(str(session), "%Y%m%d").date()
        native = (d1 - d0).days
        if native <= 0:
            return native, False
        try:
            from lib.trade_cal import fetch_trade_cal

            dates, _estimated = fetch_trade_cal(
                (d0 + timedelta(days=1)).strftime("%Y%m%d"), d1.strftime("%Y%m%d")
            )
            return len(set(dates)), False
        except Exception:
            return native, True
    except Exception:
        return None, True
