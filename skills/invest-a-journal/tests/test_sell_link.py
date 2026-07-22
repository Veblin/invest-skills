"""Tests for sell auto-link + search symbol normalization."""

from __future__ import annotations

from pathlib import Path

import pytest

from _invest_path import ensure_invest_a_scripts_on_path

ensure_invest_a_scripts_on_path()

from lib import env as invest_env  # noqa: E402

import db  # noqa: E402


@pytest.fixture()
def tmp_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_path = tmp_path / "research.db"
    monkeypatch.setattr(invest_env, "STORE_DB", db_path)
    monkeypatch.setattr(db, "DB_PATH", db_path)
    db.init_db()
    return db_path


class TestSellAutoLink:
    def test_save_sell_links_latest_buy(self, tmp_db):
        buy_id = db.save_journal({
            "symbol": "563300",
            "direction": "buy",
            "driver": "first",
            "entry_date": "2026-01-01",
        })
        db.save_journal({
            "symbol": "563300",
            "direction": "buy",
            "driver": "second",
            "entry_date": "2026-02-01",
        })
        # older buy first; latest should be second — but created_at order matters.
        # Both just inserted; second has higher id / later created_at.
        sell_id = db.save_journal({
            "symbol": "563300",
            "direction": "sell",
            "driver": "exit",
            "entry_date": "2026-03-01",
        })
        sell = db.get_journal(sell_id)
        assert sell is not None
        assert sell["linked_journal_id"] is not None
        assert sell["linked_journal_id"] != buy_id  # linked to newer buy
        linked = db.get_journal(sell["linked_journal_id"])
        assert linked["driver"] == "second"

    def test_explicit_link_not_overwritten(self, tmp_db):
        buy_id = db.save_journal({
            "symbol": "600519",
            "direction": "buy",
            "entry_date": "2026-01-01",
        })
        sell_id = db.save_journal({
            "symbol": "600519",
            "direction": "sell",
            "linked_journal_id": buy_id,
            "entry_date": "2026-02-01",
        })
        assert db.get_journal(sell_id)["linked_journal_id"] == buy_id

    def test_search_normalizes_case(self, tmp_db):
        db.save_journal({"symbol": "563300", "direction": "buy", "entry_date": "2026-01-01"})
        hits = db.search_by_symbol("563300")
        hits_lower = db.search_by_symbol("563300".lower())  # same digits
        # mixed case letter symbols if any — use Alpha-like
        db.save_journal({"symbol": "abctest", "direction": "buy", "entry_date": "2026-01-01"})
        assert len(db.search_by_symbol("ABCTest")) == 1
        assert len(hits) == 1
        assert len(hits_lower) == 1

    def test_empty_string_link_triggers_auto_link(self, tmp_db):
        buy_id = db.save_journal({"symbol": "563300", "direction": "buy"})
        sell_id = db.save_journal({
            "symbol": "563300",
            "direction": "sell",
            "linked_journal_id": "",  # Claude/JSON 常见「未设置」
        })
        assert db.get_journal(sell_id)["linked_journal_id"] == buy_id

    def test_wrong_conditions_list_serialized(self, tmp_db):
        jid = db.save_journal({
            "symbol": "600519",
            "direction": "buy",
            "wrong_conditions": ["跌破前低", "两融连降"],
        })
        row = db.get_journal(jid)
        assert isinstance(row["wrong_conditions"], str)
        import json
        assert json.loads(row["wrong_conditions"]) == ["跌破前低", "两融连降"]

    def test_update_journal_rowcount_and_allowlist(self, tmp_db):
        jid = db.save_journal({"symbol": "510300", "direction": "buy", "driver": "x"})
        assert db.update_journal(jid, {"reviewed": 1, "driver": "y"}) is True
        row = db.get_journal(jid)
        assert row["reviewed"] == 1
        assert row["driver"] == "y"
        with pytest.raises(ValueError, match="disallowed"):
            db.update_journal(jid, {"reviewed=1, driver": "hacked"})
        # smuggle attempt must not change driver
        assert db.get_journal(jid)["driver"] == "y"

    def test_update_evaluation_json_dict(self, tmp_db):
        jid = db.save_journal({"symbol": "510300", "direction": "buy"})
        assert db.update_journal(jid, {"evaluation_json": {"logic": {"level": "✅"}}})
        row = db.get_journal(jid)
        assert row["evaluation_json"]["logic"]["level"] == "✅"

    def test_delete_buy_nullifies_sell_link(self, tmp_db):
        buy_id = db.save_journal({
            "symbol": "563300",
            "direction": "buy",
            "entry_date": "2026-01-01",
        })
        sell_id = db.save_journal({
            "symbol": "563300",
            "direction": "sell",
            "entry_date": "2026-02-01",
        })
        assert db.get_journal(sell_id)["linked_journal_id"] == buy_id
        assert db.delete_journal(buy_id) is True
        assert db.get_journal(buy_id) is None
        sell = db.get_journal(sell_id)
        assert sell is not None
        assert sell["linked_journal_id"] is None
