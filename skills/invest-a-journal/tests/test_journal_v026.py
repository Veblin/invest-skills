"""journal v0.2.6 结构化字段测试 — tmp DB 隔离（仿 test_sell_link.py 模式）。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_SCRIPT_DIR = Path(__file__).resolve().parent.parent / "scripts"
_LIB_DIR = _SCRIPT_DIR / "lib"
for _p in (str(_LIB_DIR), str(_SCRIPT_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from _invest_path import ensure_invest_a_scripts_on_path  # noqa: E402

ensure_invest_a_scripts_on_path()  # `from lib import env` 须命中 invest-a-stock 的 lib 包

from lib import env as invest_env  # noqa: E402
import db  # noqa: E402


@pytest.fixture
def tmp_db(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "test_journal.db"
    monkeypatch.setattr(invest_env, "STORE_DB", str(db_path))
    monkeypatch.setattr(db, "DB_PATH", db_path)
    db.init_db()
    yield db


class TestMigration:
    def test_migrate_v026_idempotent(self, tmp_db):
        # 重复 init_db（fixture 已跑一次）不报错、列存在
        tmp_db.init_db()
        tmp_db.init_db()
        c = tmp_db._conn()
        try:
            cols = {r["name"] for r in c.execute("PRAGMA table_info(trade_journals)").fetchall()}
        finally:
            tmp_db._safe_close(c)
        for col in ("stop_price", "expected_loss_pct", "proceeds_destination",
                    "stop_moved_count", "stop_hit_count", "extracted_amount"):
            assert col in cols, f"缺列 {col}"


class TestSaveRoundtrip:
    def test_new_columns_saved(self, tmp_db):
        jid = tmp_db.save_journal({
            "symbol": "600176", "direction": "buy", "entry_price": 10.0,
            "stop_price": 9.2, "expected_loss_pct": 8.0,
            "proceeds_destination": "转出", "stop_moved_count": 1,
            "stop_hit_count": 0, "extracted_amount": 5000.0,
        })
        row = tmp_db.get_journal(jid)
        assert row["stop_price"] == 9.2
        assert row["expected_loss_pct"] == 8.0
        assert row["proceeds_destination"] == "转出"
        assert row["stop_moved_count"] == 1
        assert row["extracted_amount"] == 5000.0

    def test_update_whitelist_includes_new_cols(self, tmp_db):
        jid = tmp_db.save_journal({"symbol": "000001", "direction": "sell"})
        assert tmp_db.update_journal(jid, {"stop_moved_count": 2, "stop_hit_count": 1})
        row = tmp_db.get_journal(jid)
        assert row["stop_moved_count"] == 2 and row["stop_hit_count"] == 1
        with pytest.raises(ValueError):
            tmp_db.update_journal(jid, {"not_a_column": 1})


class TestAggregates:
    def test_stop_audit_stats(self, tmp_db):
        tmp_db.save_journal({"symbol": "600176", "direction": "buy", "stop_price": 9.0})
        tmp_db.save_journal({"symbol": "600176", "direction": "buy"})
        tmp_db.save_journal({"symbol": "600176", "direction": "sell", "stop_hit_count": 1})
        tmp_db.save_journal({"symbol": "600176", "direction": "sell", "stop_moved_count": 2})
        stats = tmp_db.stop_audit_stats()
        assert stats["sells_total"] == 2
        assert stats["stop_hit_sells"] == 1
        assert stats["stop_moved_sells"] == 1
        assert stats["buys_with_stop"] == 1
        assert stats["buys_without_stop"] == 1

    def test_extracted_amount_mtd(self, tmp_db):
        tmp_db.save_journal({
            "symbol": "600176", "direction": "sell",
            "entry_date": "2026-08-01", "extracted_amount": 1000.0,
        })
        tmp_db.save_journal({
            "symbol": "600176", "direction": "sell",
            "entry_date": "2026-08-10", "extracted_amount": 2000.0,
        })
        r = tmp_db.extracted_amount_mtd("2026-08")
        assert r["sum_extracted"] == 3000.0
        assert r["n_records"] == 2
        # 提取后 10 日内同标的新 buy → 冷静期违规
        tmp_db.save_journal({
            "symbol": "600176", "direction": "buy", "entry_date": "2026-08-05",
        })
        r = tmp_db.extracted_amount_mtd("2026-08")
        assert r["cooldown_violations"] >= 1
