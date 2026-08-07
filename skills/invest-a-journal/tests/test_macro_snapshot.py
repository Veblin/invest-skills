"""v0.2.4：macro_snapshots 宏观日快照（store 侧 API + journal 触发点）。

隔离说明：save_macro_snapshot / load_macro_history 位于 invest-a-stock 的
store（honor _db_override）；journal 侧 db.py 直连真实库，但本测试不触碰
journal db 函数，仅通过 store 的 override 隔离。
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Iterator

import pytest

_LIB = Path(__file__).resolve().parent.parent / "scripts" / "lib"
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

from _invest_path import ensure_invest_a_scripts_on_path  # noqa: E402

ensure_invest_a_scripts_on_path()


@pytest.fixture
def isolated_store(tmp_path: Path) -> Iterator[Any]:
    """临时 SQLite 隔离（store 侧 honor _db_override）。"""
    from lib import store as store_mod

    previous = store_mod._db_override
    store_mod._db_override = tmp_path / "test_research.db"
    try:
        store_mod.init_db()
        yield store_mod
    finally:
        store_mod._db_override = previous


class TestSaveMacroSnapshot:
    def test_save_writes_row(self, isolated_store):
        from lib import store

        ctx = {"pmi": {"value": 50.1, "source": "akshare", "signal": "扩张"},
               "vix": 15.0}
        date = store.save_macro_snapshot(ctx)
        assert date is not None

        hist = store.load_macro_history(7)
        assert len(hist) == 1
        assert hist[0]["pmi"] == 50.1
        assert hist[0]["vix"] == 15.0
        assert hist[0]["raw_json"] is not None

    def test_save_idempotent_same_day(self, isolated_store):
        from lib import store

        store.save_macro_snapshot({"pmi": {"value": 50.1}})
        store.save_macro_snapshot({"pmi": {"value": 50.2}})
        hist = store.load_macro_history(7)
        assert len(hist) == 1
        assert hist[0]["pmi"] == 50.2  # 同日逐列合并：非 None 后写覆盖

    def test_partial_second_write_preserves_earlier_values(self, isolated_store):
        """同日第二次写入（部分指标为 None）不整行覆盖（review #2）。"""
        from lib import store

        store.save_macro_snapshot({"pmi": {"value": 50.1}, "vix": 15.0, "sox": 5600.0})
        # 第二次写入只带 pmi（模拟部分 fetch 失败 / 7d TTL 缓存旧值）
        store.save_macro_snapshot({"pmi": {"value": 50.3}})
        hist = store.load_macro_history(7)
        assert len(hist) == 1
        assert hist[0]["pmi"] == 50.3  # 新值覆盖
        assert hist[0]["vix"] == 15.0  # 旧值保留，未被 NULL 冲掉
        assert hist[0]["sox"] == 5600.0

    def test_merge_preserves_other_indicators(self, isolated_store):
        """先 pmi 后 cpi 两次部分写入 → 两个值都在。"""
        from lib import store

        store.save_macro_snapshot({"pmi": {"value": 50.1}})
        store.save_macro_snapshot({"cpi": {"value": 0.3}})
        hist = store.load_macro_history(7)
        assert len(hist) == 1
        assert hist[0]["pmi"] == 50.1
        assert hist[0]["cpi"] == 0.3

    def test_cold_start_without_init_db(self, tmp_path):
        """全新库未跑 init_db 时 save/load 不崩（review #1：此前静默丢快照）。"""
        from lib import store as store_mod

        previous = store_mod._db_override
        store_mod._db_override = tmp_path / "fresh.db"  # 不调 init_db()
        try:
            assert store_mod.load_macro_history(7) == []
            assert store_mod.save_macro_snapshot({"pmi": {"value": 50.1}}) is not None
            hist = store_mod.load_macro_history(7)
            assert len(hist) == 1 and hist[0]["pmi"] == 50.1
        finally:
            store_mod._db_override = previous

    def test_save_skips_all_null(self, isolated_store):
        from lib import store

        assert store.save_macro_snapshot({}) is None
        assert store.save_macro_snapshot({"pmi": None, "vix": None}) is None
        assert store.load_macro_history(7) == []

    def test_save_accepts_indicators_wrapped(self, isolated_store):
        """collector 结果形态 {"indicators": {...}} 兼容。"""
        from lib import store

        ctx = {"status": "ok",
               "indicators": {"cpi": {"value": 0.3, "source": "akshare"}}}
        assert store.save_macro_snapshot(ctx) is not None
        hist = store.load_macro_history(7)
        assert hist[0]["cpi"] == 0.3

    def test_load_history_asc(self, isolated_store):
        from lib import store
        from lib.store import _conn, _safe_close

        c = _conn()
        try:
            for d, v in [("20260801", 50.0), ("20260802", 51.0), ("20260803", 52.0)]:
                c.execute(
                    "INSERT OR REPLACE INTO macro_snapshots (date, pmi) VALUES (?, ?)",
                    (d, v),
                )
            c.commit()
        finally:
            _safe_close(c)
        hist = store.load_macro_history(7)
        dates = [r["date"] for r in hist]
        assert dates == ["20260801", "20260802", "20260803"]


class TestJournalTrigger:
    def test_process_macro_persists(self, isolated_store):
        """journal 每次评估必经的 _process_macro 顺带写宏观日快照。"""
        import query_data

        from lib import store
        result = {"macro": {"status": "ok", "indicators": {
            "pmi": {"value": 50.1, "source": "akshare", "signal": ""},
            "vix": {"value": 15.0, "source": "fred", "signal": ""},
        }}}
        query_data._process_macro(result)
        assert result["macro_snapshot"]["pmi"]["value"] == 50.1
        assert len(store.load_macro_history(7)) == 1

    def test_process_macro_missing_indicators_no_persist(self, isolated_store):
        import query_data

        from lib import store
        query_data._process_macro({"macro": {"status": "missing"}})
        assert store.load_macro_history(7) == []
