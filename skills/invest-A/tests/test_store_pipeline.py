"""Tests for pipeline state persistence (R-13)."""
from __future__ import annotations


class TestPipelineState:
    def test_save_and_load(self, isolated_store):
        isolated_store.save_pipeline_step("TEST999", "plan", {"intent": "deep_analysis"})
        result = isolated_store.load_pipeline_step("TEST999", "plan")
        assert result is not None
        assert result["state"]["intent"] == "deep_analysis"
        assert result["completed_at"] is not None

    def test_load_nonexistent(self, isolated_store):
        result = isolated_store.load_pipeline_step("TEST999", "nonexistent")
        assert result is None

    def test_overwrite_step(self, isolated_store):
        isolated_store.save_pipeline_step("TEST999", "collect", {"dims": ["quote"]})
        isolated_store.save_pipeline_step("TEST999", "collect", {"dims": ["quote", "kline"]})
        result = isolated_store.load_pipeline_step("TEST999", "collect")
        assert result["state"]["dims"] == ["quote", "kline"]

    def test_get_progress(self, isolated_store):
        isolated_store.save_pipeline_step("TEST999", "plan")
        isolated_store.save_pipeline_step("TEST999", "collect")
        progress = isolated_store.get_pipeline_progress("TEST999")
        assert progress.get("plan") is True
        assert progress.get("collect") is True
        assert progress.get("evidence") is not True

    def test_clear(self, isolated_store):
        isolated_store.save_pipeline_step("TEST999", "plan")
        isolated_store.clear_pipeline_state("TEST999")
        assert isolated_store.load_pipeline_step("TEST999", "plan") is None

    def test_none_state(self, isolated_store):
        isolated_store.save_pipeline_step("TEST999", "plan", None)
        result = isolated_store.load_pipeline_step("TEST999", "plan")
        assert result is not None
        assert result["state"] == {}
