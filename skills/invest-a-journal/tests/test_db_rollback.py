"""Transaction rollback on write-path failures."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from _invest_path import ensure_invest_a_scripts_on_path

ensure_invest_a_scripts_on_path()

import db  # noqa: E402


@pytest.fixture()
def mock_conn(monkeypatch: pytest.MonkeyPatch):
    conn = MagicMock()
    conn.execute.side_effect = RuntimeError("execute failed")
    monkeypatch.setattr(db, "_conn", lambda: conn)
    monkeypatch.setattr(db, "init_db", lambda: None)
    return conn


class TestWritePathRollback:
    def test_update_journal_rollback_on_execute_error(self, mock_conn):
        with pytest.raises(RuntimeError, match="execute failed"):
            db.update_journal(1, {"driver": "x"})

        mock_conn.rollback.assert_called_once()
        mock_conn.commit.assert_not_called()

    def test_delete_journal_rollback_on_execute_error(self, mock_conn):
        with pytest.raises(RuntimeError, match="execute failed"):
            db.delete_journal(1)

        mock_conn.rollback.assert_called_once()
        mock_conn.commit.assert_not_called()
