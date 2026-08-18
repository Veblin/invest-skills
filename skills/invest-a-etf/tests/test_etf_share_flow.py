"""etf_share_flow 回归：T+1 语义标注（lag_note）+ 读取语义（缺陷修复）。

缺陷背景：快照表 date=采集日墙钟、shares=akshare spot 最新披露值（T+1 延迟，
spot 源无 trade_date 字段，实际对应交易日未知）——读取端必须如实标注，不得
推断实际交易日。

测试隔离：本地 isolated_store fixture（仿 test_index_pe_snapshot：store.
_db_override 临时路径 + init_db；etf_share_flow 函数内 from lib.store import
_conn 每次调用读取 override）。无网络：直接向隔离库 INSERT 构造快照行。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any, Iterator

import pytest

_ETF_LIB = Path(__file__).resolve().parent.parent / "scripts" / "lib"
if str(_ETF_LIB) not in sys.path:
    sys.path.insert(0, str(_ETF_LIB))


def _canonical_etf_data():
    """确保 sys.modules["etf_data"] 是 canonical（invest-a-etf）而非 journal shim。

    全量跑时 journal scripts/lib 的 etf_data shim 可能先被 import。仅当当前
    模块非 canonical 时覆盖（同 test_index_pe_snapshot）。
    """
    name = "etf_data"
    mod = sys.modules.get(name)
    if mod is not None:
        cur = Path(getattr(mod, "__file__", "") or "")
        if cur.resolve().parent == _ETF_LIB.resolve():
            return mod
    spec = importlib.util.spec_from_file_location(name, _ETF_LIB / "etf_data.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def isolated_store(tmp_path: Path) -> Iterator[Any]:
    """临时 SQLite 隔离（仿 test_index_pe_snapshot.isolated_store）。"""
    from lib import store as store_mod

    previous = store_mod._db_override
    store_mod._db_override = tmp_path / "test_research.db"
    try:
        store_mod.init_db()
        yield store_mod
    finally:
        store_mod._db_override = previous


def _insert_snapshots(store_mod, rows):
    c = store_mod._conn()
    try:
        c.executemany(
            "INSERT INTO etf_share_snapshots (date, symbol, shares, price, aum) "
            "VALUES (?, ?, ?, ?, ?)",
            rows,
        )
        c.commit()
    finally:
        store_mod._safe_close(c)


def test_share_flow_returns_lag_note_with_t1_semantics(isolated_store):
    """返回结构旧键全保留 + lag_note 如实标注 T+1 语义（不推断实际交易日）。"""
    etf_data = _canonical_etf_data()
    _insert_snapshots(isolated_store, [
        ("20260811", "515050", 4_900_000_000.0, 4.00, 196.0),
        ("20260812", "515050", 4_950_000_000.0, 4.10, 202.95),
    ])
    out = etf_data.etf_share_flow("515050", days=60)
    # 旧键全部保留（只增不减）
    for k in ("symbol", "date", "shares_current", "aum_current",
              "share_change_5d", "share_change_20d", "share_change_60d",
              "flow_est_5d", "flow_est_20d", "flow_est_60d", "history_count"):
        assert k in out
    # 语义：date=存储的采集日列原样；shares/aum=该采集时披露值
    assert out["date"] == "20260812"
    assert out["shares_current"] == 4_950_000_000.0
    assert out["aum_current"] == 202.95
    assert out["history_count"] == 2
    # 2 行历史 < 6 行 → 窗口变化待积累（顺带覆盖 None 路径）
    assert out["share_change_5d"] is None
    assert out["flow_est_5d"] is None
    # lag_note 精确措辞：只陈述事实（date=墙钟、值=采集时披露值、T+1 延迟、
    # 实际对应交易日未知），不推断份额实际是 N 天前数据
    assert out["lag_note"] == (
        "date 为采集日墙钟日期；shares/price/aum 为采集时 akshare 最新披露值，"
        "份额披露存在 T+1 延迟，实际对应交易日未知"
    )


def test_share_flow_empty_history_keeps_note_only(isolated_store):
    """空历史：保持返回 note 键（与 lag_note 语义分离），无 lag_note。"""
    etf_data = _canonical_etf_data()
    out = etf_data.etf_share_flow("515050", days=60)
    assert out == {"symbol": "515050", "history_count": 0, "note": "无历史数据"}
    assert "lag_note" not in out
