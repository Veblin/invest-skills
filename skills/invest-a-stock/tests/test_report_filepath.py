"""Tests for invest._report_filepath timestamp naming."""

from __future__ import annotations

import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))


class TestReportFilepath:
    def test_full_workflow_marks_draft_and_final_artifacts(self, tmp_path: Path):
        import invest

        ts = "2026-09-30-13-33-50"
        draft = invest._report_filepath(
            tmp_path, "600519-贵州茅台", ts, invest._report_stage("full", None))
        final = invest._report_filepath(
            tmp_path, "600519-贵州茅台", ts, invest._report_stage("full", [{"module": "overview"}]))
        html = invest._html_report_path(tmp_path, "600519-贵州茅台", ts, "final")
        assert draft.name == f"{ts}.draft.md"
        assert final.name == f"{ts}.final.md"
        assert html.name == f"{ts}.final.html"
        assert draft != final

    def test_non_full_modes_and_empty_analysis_keep_no_final_suffix(self, tmp_path: Path):
        """非 full 模式与「无分析段」都不得被标成 final。

        锁定三点：`brief/concise/insight` 直接不参与 draft/final 命名（返回 None）；
        full + 空分析列表（falsy）判为 draft；stage 为 None 时文件名不带任何后缀。
        """
        import invest

        for mode in ("brief", "concise", "insight"):
            assert invest._report_stage(mode, [{"module": "overview"}]) is None
        assert invest._report_stage("full", []) == "draft"

        ts = "2026-09-30-13-33-50"
        assert invest._report_filepath(
            tmp_path, "600519-贵州茅台", ts, None).name == f"{ts}.md"
        assert invest._report_filepath(
            tmp_path, "600519-贵州茅台", ts, "unknown-stage").name == f"{ts}.md"

    def test_uses_full_timestamp_not_date_only(self, tmp_path: Path):
        import invest

        path = invest._report_filepath(tmp_path, "301165-锐捷网络", "2026-07-22-14-30-05")
        assert path.name == "2026-07-22-14-30-05.md"
        assert path.parent.name == "301165-锐捷网络"
        assert path.parent.is_dir()

    def test_same_day_different_timestamps_do_not_collide(self, tmp_path: Path):
        import invest

        subdir = "301165-锐捷网络"
        morning = invest._report_filepath(tmp_path, subdir, "2026-07-22-09-15-00")
        afternoon = invest._report_filepath(tmp_path, subdir, "2026-07-22-16-45-30")
        assert morning != afternoon
        assert morning.parent == afternoon.parent
        morning.write_text("morning", encoding="utf-8")
        afternoon.write_text("afternoon", encoding="utf-8")
        assert morning.read_text(encoding="utf-8") == "morning"
        assert afternoon.read_text(encoding="utf-8") == "afternoon"
