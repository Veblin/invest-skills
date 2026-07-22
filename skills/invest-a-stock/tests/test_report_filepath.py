"""Tests for invest._report_filepath timestamp naming."""

from __future__ import annotations

import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))


class TestReportFilepath:
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
