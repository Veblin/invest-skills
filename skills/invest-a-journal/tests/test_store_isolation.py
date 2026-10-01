"""issue #33 守卫：journal 测试套件与生产 research.db 的隔离契约。

判别矩阵（哪种半修/错修会使本文件变红）：
- S1 只 patch store._db_override（journal db.py 未收口）→ T1/T2/T3 红：写入仍落生产库；
- S2 只 patch db.DB_PATH → T1 红：两路径不等，store 侧（建表 / futures 读）仍指生产；
- S3 双 patch 到不同文件 → T1/T2 红：行落不到目标文件（且写入异常被 _auto_persist 吞掉）；
- S4 conftest 未生效（非 autouse / 未加载）→ T1 红；
- S5 连接越界（journal 连接回退到生产路径等）→ T6/T7 红：边界守卫必须在任何
  建表/建文件之前抛错。注意 T3 不构成「fixture 已建表」的证明——`save_journal()`
  自身会调 `init_db()`；fixture 建表性质由 T2（snapshot 路径需要 store 侧已建表）
  与守卫共同覆盖。

设计要点：T1 先断言隔离指针、再触发任何写入 —— 因此契约破坏时「红但零写入」。
T6/T7 是 P2 负控制（独立验收 2026-10-01）：错误目标指向已有哨兵时指纹不变、
指向不存在文件时不得创建文件、journal 连接回退场景必须先于建表被拦截。

T4 是方法论负控制：证明「行存在 + 无 DB write failed 告警」这组断言确实能捕获
_auto_persist 吞异常的路径（而不是碰巧）。
"""

from __future__ import annotations

import logging
import sqlite3
import sys
from pathlib import Path

import pytest

_LIB = Path(__file__).resolve().parent.parent / "scripts" / "lib"
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

from _invest_path import ensure_invest_a_scripts_on_path  # noqa: E402

ensure_invest_a_scripts_on_path()

import market_microstructure as mm  # noqa: E402
import db as journal_db  # noqa: E402
from lib import env as invest_env  # noqa: E402
from lib import store as store_mod  # noqa: E402

# snapshot() 内部调用的全部采集函数（与 test_snapshot_status.py::_FETCHERS 同口径）
_FETCHERS = (
    "_fetch_margin", "_fetch_ad_ratio", "_fetch_limit_pools",
    "_fetch_turnover", "_fetch_erp", "_fetch_pcr",
    "_fetch_below_book_pct", "_fetch_northbound",
    "_fetch_futures",
)


def _fail_all(monkeypatch) -> None:
    for name in _FETCHERS:
        monkeypatch.setattr(
            mm, name,
            lambda result, _n=name: result["_errors"].append(f"{_n}: boom"),
        )


def _fake_ad_ratio_12(monkeypatch) -> None:
    def _fake(result):
        result["ad_ratio"] = 1.2

    monkeypatch.setattr(mm, "_fetch_ad_ratio", _fake)


def _main_db_file(conn: sqlite3.Connection) -> Path:
    for _seq, name, file in conn.execute("PRAGMA database_list"):
        if name == "main":
            return Path(file).resolve()
    raise AssertionError("连接未暴露 main 数据库")


def test_journal_conn_and_store_resolve_same_file(tmp_path):
    """单一真源契约：journal 写入连接与 store 路径必须同一文件且落在 tmp。

    本用例只读（PRAGMA），不触发任何写入 —— 未修复时红且零污染。
    """
    expected = (tmp_path / "test_research.db").resolve()
    assert store_mod._get_path().resolve() == expected, (
        "store._db_override 未生效：conftest 隔离 fixture 缺失或未 autouse"
    )
    c = journal_db._conn()
    try:
        assert _main_db_file(c) == expected, (
            "journal 连接未跟随 store._get_path()：双路径错位（S2/S4），"
            "写入会落到生产库或静默失败"
        )
    finally:
        c.close()
    assert expected != Path(invest_env.STORE_DB).resolve(), "隔离文件不得是生产库"


def test_snapshot_autopersist_lands_in_isolated_db(monkeypatch, caplog, tmp_path):
    """snapshot() 的 _auto_persist 必须把行写进隔离库（且写入成功，非静默失败）。"""
    import data_bridge
    from cache import DataCache

    monkeypatch.setattr(data_bridge, "_cache", DataCache(cache_dir=tmp_path / "cache"))
    _fail_all(monkeypatch)
    _fake_ad_ratio_12(monkeypatch)

    with caplog.at_level(logging.WARNING, logger="market_microstructure"):
        snap = mm.snapshot()

    assert "DB write failed" not in caplog.text, (
        "写入异常被 _auto_persist 吞掉：隔离文件缺少 market_snapshots 表或路径错位"
    )
    row_date = snap["date"]
    expected_file = (tmp_path / "test_research.db").resolve()
    for mod in (journal_db, store_mod):
        c = mod._conn()
        try:
            assert _main_db_file(c) == expected_file
            row = c.execute(
                "SELECT ad_ratio FROM market_snapshots WHERE date=?", (row_date,)
            ).fetchone()
        finally:
            mod._safe_close(c)
        assert row is not None and row[0] == 1.2, (
            f"{mod.__name__} 侧读不到隔离库中的当日行（S1/S2/S3 半修特征）"
        )


def test_journal_crud_lands_in_isolated_db(tmp_path):
    """journal CRUD 写 trade_journals 必须落隔离库。

    注：`save_journal()` 自身会调 `init_db()`，故本用例**不**构成「fixture 建表」的
    独立证明（独立验收 2026-10-01 指出）；它证明的是写入落点与读回一致性。
    """
    jid = journal_db.save_journal(
        {"symbol": "600176", "direction": "buy", "entry_price": 10.0}
    )
    expected_file = (tmp_path / "test_research.db").resolve()
    c = store_mod._conn()
    try:
        assert _main_db_file(c) == expected_file
        row = c.execute(
            "SELECT symbol FROM trade_journals WHERE id=?", (jid,)
        ).fetchone()
    finally:
        store_mod._safe_close(c)
    assert row is not None and row[0] == "600176", (
        "trade_journals 未建在隔离库：conftest 漏调 db.init_db()（S5）或路径错位"
    )


def test_auto_persist_silent_failure_is_observable(monkeypatch, caplog, tmp_path):
    """负控制：写入连接指向无表的库时，_auto_persist 必须留下 'DB write failed'。

    证明上一条用例的「行存在 + 无告警」组合能真正区分写入成功与静默失败。
    """
    import data_bridge
    from cache import DataCache

    monkeypatch.setattr(data_bridge, "_cache", DataCache(cache_dir=tmp_path / "cache"))
    _fail_all(monkeypatch)
    _fake_ad_ratio_12(monkeypatch)
    monkeypatch.setattr(mm, "_conn", lambda: sqlite3.connect(":memory:"))

    with caplog.at_level(logging.WARNING, logger="market_microstructure"):
        mm.snapshot()  # 不得抛异常

    assert "DB write failed" in caplog.text, (
        "静默失败路径未被观测到：负控制失效，正例断言不可信"
    )


def test_legacy_db_path_patch_is_not_a_second_source(monkeypatch, tmp_path):
    """路线 B 契约：db.DB_PATH 不再是连接来源，patch 它不得制造第二个写入目标。"""
    poison = tmp_path / "poison.db"
    monkeypatch.setattr(journal_db, "DB_PATH", poison)

    jid = journal_db.save_journal({"symbol": "000001", "direction": "sell"})
    expected_file = (tmp_path / "test_research.db").resolve()
    c = journal_db._conn()
    try:
        assert _main_db_file(c) == expected_file
        assert _main_db_file(c) != poison.resolve()
    finally:
        journal_db._safe_close(c)
    assert not poison.exists(), "db.DB_PATH 仍被用作写入路径（路线 A 残留）"
    assert jid > 0


def test_connect_guard_blocks_out_of_tmp_targets(guarded_connect_factory, tmp_path):
    """P2 负控制（独立验收 2026-10-01）：越界目标必须在建立连接之前被拒绝。

    覆盖两条判据：①指向已有哨兵库 → 指纹不变；②指向不存在文件 → 不得创建文件。
    """
    import hashlib
    import sqlite3

    outside = tmp_path.parent / (tmp_path.name + "-outside")
    outside.mkdir()
    sentinel = outside / "sentinel.db"
    c = sqlite3.connect(sentinel)
    c.execute("CREATE TABLE t (x INTEGER)")
    c.execute("INSERT INTO t VALUES (1)")
    c.commit()
    c.close()

    def _fp(p: Path):
        return (p.stat().st_size, p.stat().st_mtime_ns,
                hashlib.sha256(p.read_bytes()).hexdigest())

    before = _fp(sentinel)
    guard = guarded_connect_factory(sqlite3.connect)

    with pytest.raises(AssertionError, match="边界守卫"):
        guard(str(sentinel))
    assert _fp(sentinel) == before, "守卫拒绝前已接触哨兵库（P2 回归）"

    missing = outside / "missing.db"
    with pytest.raises(AssertionError, match="边界守卫"):
        guard(str(missing))
    assert not missing.exists(), "守卫拒绝前已创建文件（P2 回归）"

    # 正对照：允许根内目标正常放行（守卫不是无条件拒绝）
    inside = tmp_path / "ok.db"
    conn = guard(str(inside))
    try:
        assert _main_db_file(conn) == inside.resolve()
    finally:
        conn.close()


def test_journal_conn_falling_back_to_prod_is_blocked(monkeypatch):
    """P2 负控制：journal 连接回退到生产路径的回归场景，必须在建表前被拦截。

    模拟 `db.py` 路径解析回退（get_db_path 被替换为生产库）——守卫必须在
    建立任何连接之前抛错，生产库文件指纹不得变化。
    """
    prod = Path(invest_env.STORE_DB)
    before = (prod.stat().st_size, prod.stat().st_mtime_ns) if prod.exists() else None

    monkeypatch.setattr(store_mod, "get_db_path", lambda: prod)
    with pytest.raises(AssertionError, match="边界守卫"):
        journal_db._conn()  # _conn → get_db_path（已回退）→ connect_db（守卫拦截）

    if before is not None:
        after = (prod.stat().st_size, prod.stat().st_mtime_ns)
        assert after == before, "守卫拒绝前已接触生产库（P2 回归）"
