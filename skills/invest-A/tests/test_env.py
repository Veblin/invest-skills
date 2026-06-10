"""测试环境配置管理器。"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest


class TestEnv:
    def test_load_env_file_missing(self):
        from lib.env import load_env_file
        assert load_env_file(Path("/nonexistent/.env")) == {}

    def test_load_env_file_simple(self):
        from lib.env import load_env_file
        with tempfile.TemporaryDirectory() as d:
            env_path = Path(d) / ".env"
            env_path.write_text("KEY=value\nFOO=bar\n")
            env = load_env_file(env_path)
            assert env["KEY"] == "value"
            assert env["FOO"] == "bar"

    def test_load_env_file_skip_comments(self):
        from lib.env import load_env_file
        with tempfile.TemporaryDirectory() as d:
            env_path = Path(d) / ".env"
            env_path.write_text("# comment\nKEY=value\n")
            env = load_env_file(env_path)
            assert "KEY" in env
            assert "# comment" not in str(env)

    def test_tushare_token_detection(self):
        from lib.env import is_tushare_available
        # Valid 32-char token
        assert is_tushare_available({"TUSHARE_TOKEN": "a" * 32})
        # Empty
        assert not is_tushare_available({"TUSHARE_TOKEN": ""})
        assert not is_tushare_available({})
        # Too short
        assert not is_tushare_available({"TUSHARE_TOKEN": "short"})

    def test_fred_key_detection(self):
        from lib.env import is_fred_available
        # Valid 32-char alphanumeric
        assert is_fred_available({"FRED_API_KEY": "a" * 32})
        # Uppercase is also valid
        assert is_fred_available({"FRED_API_KEY": "A" + "a" * 31})
        # Empty
        assert not is_fred_available({"FRED_API_KEY": ""})

    def test_diagnose_returns_dict(self):
        from lib.env import diagnose
        result = diagnose({"TUSHARE_TOKEN": "a" * 32, "FRED_API_KEY": ""})
        assert "sources" in result
        assert result["sources"]["tushare"] is True
        assert result["sources"]["fred"] is False
        assert result["available_count"] >= 1


class TestStore:
    def test_init_and_save(self):
        from lib.store import init_db, save_collection, list_collections, get_stats, clear_all

        # Use temp db
        import lib.store as store_mod
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            store_mod._db_override = Path(d) / "test.db"

            mock_result = {
                "symbol": "600176",
                "fetched_at": "2026-06-10T00:00:00",
                "dimensions": [
                    {"dimension": "basic_info", "display": "基本信息",
                     "data": {"name": "测试公司", "industry": "测试"},
                     "_meta": {"source": "tushare", "confidence": "high"},
                     "status": "available"},
                    {"dimension": "financials", "display": "财务",
                     "data": [{"end_date": "20251231", "roe": 10.5}],
                     "_meta": {"source": "tushare", "confidence": "high"},
                     "status": "available"},
                ],
                "summary": {"total": 2, "available": 2, "degraded": 0, "missing": 0},
            }

            cid = save_collection(mock_result)
            assert cid > 0

            records = list_collections()
            assert len(records) == 1
            assert records[0]["symbol"] == "600176"

            stats = get_stats()
            assert stats["total_collections"] == 1
            assert stats["unique_symbols"] == 1

            clear_all()
            assert get_stats()["total_collections"] == 0

            store_mod._db_override = None
