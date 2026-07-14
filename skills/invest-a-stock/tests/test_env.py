"""测试环境配置管理器。"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest


class TestEnv:
    def test_project_root_anchors_from_env_module_file(self):
        """PROJECT_ROOT must come from env.py location, not process CWD."""
        import lib.env as env_mod

        root = env_mod._find_project_root()
        assert (root / "pyproject.toml").is_file()
        # even if CWD is elsewhere, file-anchored walk still finds the repo
        assert root == env_mod.PROJECT_ROOT

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

    def test_diagnose_includes_proxy_fields(self):
        from lib.env import diagnose

        result = diagnose({"TUSHARE_TOKEN": "a" * 32, "FRED_API_KEY": ""})
        assert "proxy_detected" in result
        assert "proxy_bypass_effective" in result
        assert "proxy_user_action_needed" in result
        assert "clash_rules_hint" in result
        assert isinstance(result["proxy_detected"], bool)

    def test_tencent_available_uses_no_proxy_session(self, monkeypatch):
        """diagnose 腾讯探针与 collector 一致，强制直连。"""
        from contextlib import contextmanager

        calls: list[bool] = []

        class _FakeSess:
            def get(self, url, timeout=3):
                calls.append(True)
                class _R:
                    status_code = 200
                    text = "x~y"
                return _R()

            def close(self):
                pass

        @contextmanager
        def _fake_no_proxy():
            yield _FakeSess()

        monkeypatch.setattr("lib.proxy.no_proxy_session", _fake_no_proxy)
        from lib.env import is_tencent_available

        assert is_tencent_available() is True
        assert calls


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


class TestEnsureEnvLoaded:
    def test_loads_global_then_project(self, monkeypatch, tmp_path: Path):
        import lib.env as env_mod

        global_file = tmp_path / "global.env"
        project_file = tmp_path / "project.env"
        global_file.write_text("FROM_GLOBAL=g\nSHARED=global\n", encoding="utf-8")
        project_file.write_text("FROM_PROJECT=p\nSHARED=project\n", encoding="utf-8")

        monkeypatch.setattr(env_mod, "GLOBAL_CONFIG_FILE", global_file)
        monkeypatch.setattr(env_mod, "PROJECT_ENV_FILE", project_file)
        monkeypatch.delenv("FROM_GLOBAL", raising=False)
        monkeypatch.delenv("FROM_PROJECT", raising=False)
        monkeypatch.delenv("SHARED", raising=False)
        monkeypatch.setenv("ALREADY_SET", "os")

        env_mod.ensure_env_loaded()
        assert os.environ["FROM_GLOBAL"] == "g"
        assert os.environ["FROM_PROJECT"] == "p"
        assert os.environ["SHARED"] == "project"  # project wins over global
        assert os.environ["ALREADY_SET"] == "os"  # os.environ never overwritten
