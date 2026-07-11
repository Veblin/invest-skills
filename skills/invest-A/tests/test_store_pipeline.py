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


# ── v0.1.9: thesis tracker ──


class TestThesisTracker:
    def test_thesis_health_all_valid(self):
        """全部假设有效，无线触发 → score ≥ 0.75，state=完整."""
        from lib.store import _thesis_health

        assumptions = [
            {"id": "a1", "valid": True}, {"id": "a2", "valid": True},
            {"id": "a3", "valid": True},
        ]
        red_lines = [
            {"id": "r1", "triggered": False}, {"id": "r2", "triggered": False},
        ]
        score, state = _thesis_health(assumptions, red_lines)
        assert score == 1.0
        assert state == "完整"

    def test_thesis_health_all_triggered(self):
        """全部假设无效，全部线触发 → state=破裂."""
        from lib.store import _thesis_health

        assumptions = [
            {"id": "a1", "valid": False}, {"id": "a2", "valid": False},
        ]
        red_lines = [
            {"id": "r1", "triggered": True}, {"id": "r2", "triggered": True},
        ]
        score, state = _thesis_health(assumptions, red_lines)
        assert score < 0.35
        assert state == "破裂"

    def test_thesis_health_mixed(self):
        """混合状态 → 边际弱化."""
        from lib.store import _thesis_health

        assumptions = [
            {"id": "a1", "valid": True}, {"id": "a2", "valid": True},
            {"id": "a3", "valid": False},
        ]
        red_lines = [
            {"id": "r1", "triggered": False}, {"id": "r2", "triggered": True},
        ]
        score, state = _thesis_health(assumptions, red_lines)
        # valid=2/3*0.6 + not_triggered=1/2*0.4 = 0.4 + 0.2 = 0.6
        assert 0.55 <= score < 0.75
        assert state == "边际弱化"

    def test_thesis_health_empty_defaults(self):
        """空列表不除零."""
        from lib.store import _thesis_health

        score, state = _thesis_health([], [])
        # a_total=1(or), a_valid=0 → 0/1*0.6=0; r_total=1(or), r_triggered=0 → 1*0.4=0.4
        assert score == 0.4
        assert state == "受损"

    def test_thesis_init_and_get(self, isolated_store):
        """初始化 → 读取 往返."""
        from lib.store import thesis_init, thesis_get

        result = thesis_init("TST001")
        assert result["symbol"] == "TST001"
        assert result["action"] == "init"
        assert 0.0 <= result["health_score"] <= 1.0

        loaded = thesis_get("TST001")
        assert loaded is not None
        assert loaded["symbol"] == "TST001"
        assert len(loaded["assumptions"]) == 3
        assert len(loaded["red_lines"]) == 2

    def test_thesis_get_nonexistent(self, isolated_store):
        """不存在的标的返回 None."""
        from lib.store import thesis_get

        result = thesis_get("NOSUCH")
        assert result is None

    def test_thesis_update(self, isolated_store):
        """更新假设和红线 → 分数和状态变化."""
        from lib.store import thesis_init, thesis_update

        thesis_init("TST002")
        result = thesis_update(
            "TST002",
            assumptions=[
                {"id": "a1", "valid": False}, {"id": "a2", "valid": False},
            ],
            red_lines=[
                {"id": "r1", "triggered": True}, {"id": "r2", "triggered": True},
            ],
        )
        assert result["symbol"] == "TST002"
        assert result["action"] == "update"
        assert result["state"] == "破裂"

    def test_thesis_update_on_nonexistent_falls_back_to_init(self, isolated_store):
        """对不存在的标的 update → 自动 init."""
        from lib.store import thesis_update

        result = thesis_update("TST003")
        assert result["action"] == "init"
        assert result["symbol"] == "TST003"

    def test_thesis_partial_update_preserves_other_fields(self, isolated_store):
        """只更新 assumptions，red_lines 保持不变."""
        from lib.store import thesis_init, thesis_update, thesis_get

        thesis_init("TST004")
        thesis_update("TST004", assumptions=[
            {"id": "a1", "valid": False},
        ])
        loaded = thesis_get("TST004")
        assert loaded is not None
        assert len(loaded["red_lines"]) == 2  # unchanged
