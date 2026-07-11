"""Tests for lib.report_audit (v0.1.9)."""

from __future__ import annotations

from pathlib import Path


SAMPLE_MD = """# 测试报告

## 财务
- 2026Q1 营收 131.38 亿
- 归母净利 11.10 亿
- PE(TTM): 20.5x
- PB: 2.1x
- ROE: 12.5%
"""


class TestReportAudit:
    def test_extract_points(self, tmp_path: Path):
        from lib.report_audit import extract_report

        p = tmp_path / "report.md"
        p.write_text(SAMPLE_MD, encoding="utf-8")
        out = extract_report(p)
        assert out["total_points"] >= 3
        assert out["sampled_points"] >= 1
        assert Path(out["output"]).exists()

    def test_verdict_pass(self, tmp_path: Path):
        from lib.report_audit import extract_report, verdict_report

        p = tmp_path / "report.md"
        p.write_text(SAMPLE_MD, encoding="utf-8")
        extract_report(p)
        checklist = p.with_suffix(".audit_checklist.json")
        import json
        data = json.loads(checklist.read_text(encoding="utf-8"))
        for c in data["checks"]:
            c["fetched_value"] = c["reported_value"]
            c["fetched_source"] = "test"
        checklist.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        v = verdict_report(p)
        assert v["verdict"] == "PASS"

    def test_verdict_revisions_needed(self, tmp_path: Path):
        from lib.report_audit import extract_report, verdict_report

        p = tmp_path / "report.md"
        p.write_text(SAMPLE_MD, encoding="utf-8")
        extract_report(p)
        v = verdict_report(p)
        assert v["verdict"] == "REVISIONS_NEEDED"
