"""同行池排序用全市场市值表（`_peer_market_caps`）——交易日回退与缓存键。

背景：同行 Top-N 由市值序决定（按代码字母序截断是本模块修掉的旧缺陷），
而市值表按**单个交易日**的 `daily_basic` 整市场取数。交易日盘中该日尚未发布
收盘数据 → 空表 → 调用点 `if caps:` 为假 → 静默退回字母序，且无日志、无告警，
报告仍以申万 L3 池口径呈现分位（分母其实是任意 N 家）。
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))


def _rows(*codes: str) -> MagicMock:
    m = MagicMock()
    m.empty = not codes
    m.iterrows = MagicMock(return_value=iter(
        (i, {"ts_code": c, "total_mv": 1.0e5 + i}) for i, c in enumerate(codes)
    ))
    return m


class _Cache:
    def __init__(self, seed: dict | None = None) -> None:
        self.store = dict(seed or {})
        self.writes: list[tuple[str, Any]] = []

    def get(self, dim: str, key: str) -> Any:
        return self.store.get((dim, key))

    def set(self, dim: str, key: str, val: Any, **kw: Any) -> None:
        self.store[(dim, key)] = val
        self.writes.append((key, val))


def _tc_by_date(by_date: dict[str, MagicMock]) -> MagicMock:
    """伪 client：对指定 trade_date 返回对应表，未列出的日期返回空表。"""
    tc = MagicMock()
    tc.is_permission_denied = MagicMock(return_value=False)
    tc.last_error = None
    seen: list[str] = []

    def _query(api: str, **kw: Any) -> MagicMock:
        if api != "daily_basic":
            return _rows()
        date = str(kw.get("trade_date") or "")
        seen.append(date)
        return by_date.get(date, _rows())

    tc.query.side_effect = _query
    tc.seen = seen
    return tc


def test_intraday_falls_back_to_previous_trade_date():
    """今日（盘中未收盘）无数据 → 回退到上一交易日，而非返回空表。

    返回空表会让调用点静默退回代码字母序选样，正是本模块修掉的旧缺陷。
    """
    from lib.collector import _orchestrate as orch

    cache = _Cache()
    tc = _tc_by_date({"20260915": _rows("000001.SZ", "000002.SZ", "000003.SZ")})
    with patch.object(orch, "_sw_cache", lambda: cache), \
         patch("lib.trade_cal.last_trade_dates", return_value=["20260916", "20260915"]):
        caps = orch._peer_market_caps(tc)

    assert len(caps) == 3, f"未回退取数: {caps}"
    assert tc.seen == ["20260916", "20260915"], "应先试今日、空则回退上一交易日"
    assert cache.writes and cache.writes[0][0] == "20260915", \
        "缓存键须是实际使用的日期（避免盘中脏读整个交易日）"


def test_all_dates_empty_returns_empty_without_caching():
    """两日皆空（极端情况）→ 返回空且不写缓存，下次仍会重试网络。"""
    from lib.collector import _orchestrate as orch

    cache = _Cache()
    tc = _tc_by_date({})
    with patch.object(orch, "_sw_cache", lambda: cache), \
         patch("lib.trade_cal.last_trade_dates", return_value=["20260916", "20260915"]):
        caps = orch._peer_market_caps(tc)

    assert caps == {}
    assert cache.writes == [], "空结果不得写缓存（D6）"


def test_cache_hit_on_previous_date_skips_query():
    """缓存中已有上一交易日的表 → 不再发查询，直接用缓存。"""
    from lib.collector import _orchestrate as orch

    cache = _Cache(seed={("daily_basic_all", "20260915"): {"000001.SZ": 1.0e5}})
    tc = _tc_by_date({})
    with patch.object(orch, "_sw_cache", lambda: cache), \
         patch("lib.trade_cal.last_trade_dates", return_value=["20260916", "20260915"]):
        caps = orch._peer_market_caps(tc)

    assert caps == {"000001.SZ": 1.0e5}
    # 今日仍会试一次（盘后应优先用当日数据）；命中的那日不得再发查询
    assert "20260915" not in tc.seen, "命中缓存不得再查该日"
