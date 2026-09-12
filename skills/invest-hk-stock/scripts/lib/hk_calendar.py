"""港股交易日历变体（T11-2）。

与 A 股 `dates._trade_days()` 同构（模块级缓存 + TTL + 降级），但**换源**：
A 股走 sina（`tool_trade_date_hist_sina`），港股走 tushare `hk_tradecal`
（2026-09-12 实测：22 行/月，字段 `cal_date`/`is_open`/`pretrade_date`）。

为什么不能复用 A 股日历：两市场假日不同（中秋翌日、国庆、佛诞、耶稣受难等），
用 A 股日历会把「港股休市日」当成交易日 → 报告里的「距上次数据 N 个交易日」
系统性算错（本仓库已知缺陷类：日历口径混用）。

降级语义（与 `dates` 一致）：日历不可得 → 周末近似的最近工作日 + `degraded=True`，
调用方**必须**据此在输出里标注「日历不可用，按自然日粗判」——不得静默当交易日算。
"""
from __future__ import annotations

import datetime as _dt
import logging
import time

logger = logging.getLogger(__name__)

HK_CAL_TTL_DAYS = 7
_CAL_TTL_SECONDS = HK_CAL_TTL_DAYS * 86400
# 取数窗口：前后各 400 自然日（覆盖约 ±1 年）
_WINDOW_DAYS = 400

_cache: dict = {"fetched_at": 0.0, "days": None}
_degraded = False


def _reset_cache() -> None:
    """清缓存与降级标记（测试/排障用）。"""
    global _degraded
    _cache.update({"fetched_at": 0.0, "days": None})
    _degraded = False


def _parse(ymd: str) -> _dt.date:
    s = str(ymd).strip()
    if len(s) != 8 or not s.isdigit():
        raise ValueError(f"非法日期（需 YYYYMMDD）：{ymd!r}")
    return _dt.date(int(s[:4]), int(s[4:6]), int(s[6:8]))


def _fetch_from_tushare() -> list[str] | None:
    """tushare `hk_tradecal` → 港股交易日（YYYYMMDD 升序）；失败 → None。"""
    from _invest_path import ensure_invest_a_scripts_on_path

    ensure_invest_a_scripts_on_path()
    try:
        from dates import shanghai_today
        from lib.tushare_client import TushareClient

        today = _parse(shanghai_today())
        df = TushareClient().query(
            "hk_tradecal",
            start_date=(today - _dt.timedelta(days=_WINDOW_DAYS)).strftime("%Y%m%d"),
            end_date=(today + _dt.timedelta(days=_WINDOW_DAYS)).strftime("%Y%m%d"),
            is_open="1",
        )
    except Exception as exc:  # noqa: BLE001 —— 日历失败必须降级而非中断报告
        logger.warning("hk_tradecal 取数失败：%s", exc)
        return None
    if df is None or getattr(df, "empty", True):
        logger.warning("hk_tradecal 空返回（非「无交易日」）")
        return None
    try:
        return sorted(str(v) for v in df["cal_date"].tolist())
    except Exception:  # noqa: BLE001 —— 列名漂移
        logger.warning("hk_tradecal 列缺失（疑接口变更）")
        return None


def hk_trade_days(*, force_refresh: bool = False) -> list[str] | None:
    """港股交易日（YYYYMMDD 升序）；TTL 内复用缓存；不可得 → None 并置 degraded。"""
    global _degraded
    now = time.time()
    if (not force_refresh and _cache["days"]
            and now - _cache["fetched_at"] < _CAL_TTL_SECONDS):
        return _cache["days"]
    days = _fetch_from_tushare()
    if not days:
        _degraded = True
        return None
    _degraded = False
    _cache.update({"fetched_at": now, "days": days})
    return days


def hk_calendar_degraded() -> bool:
    """上次取数是否走了降级（调用方据此标注「日历不可用，按自然日粗判」）。"""
    return _degraded


def _weekday_approx(today: str) -> str:
    """日历不可用时的近似：回退到最近的周五（周末 → 周五；周中 → 当天）。"""
    d = _parse(today)
    while d.weekday() >= 5:
        d -= _dt.timedelta(days=1)
    return d.strftime("%Y%m%d")


def hk_session_date(today: str | None = None) -> str:
    """最近港股交易日（≤ today，北京时区口径）。日历不可用 → 周末近似 + degraded。"""
    if today is None:
        from dates import shanghai_today

        today = shanghai_today()
    days = hk_trade_days()
    if days:
        past = [d for d in days if d <= today]
        if past:
            return past[-1]
        logger.warning("hk_tradecal 窗口未覆盖 %s（疑缓存过旧），改用周末近似", today)
    return _weekday_approx(today)


def hk_trading_day_lag(as_of: str, session: str) -> tuple[int | None, bool]:
    """``(as_of, session]`` 间**港股**交易日数；日历不可用 → (自然日差, True)。

    语义与 `freshness.trading_day_lag` 一致（后者是 A 股口径），差别只在日历源。
    """
    try:
        a, s = _parse(as_of), _parse(session)
    except (ValueError, TypeError):
        return None, True
    days = hk_trade_days()
    if not days:
        return (s - a).days, True
    n = 0
    for d in days:
        try:
            dd = _parse(d)
        except ValueError:
            continue
        if a < dd <= s:
            n += 1
    return n, False
