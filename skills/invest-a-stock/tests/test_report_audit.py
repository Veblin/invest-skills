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

    def test_extract_empty_report(self, tmp_path: Path):
        """空报告 → total_points=0."""
        from lib.report_audit import extract_report

        p = tmp_path / "empty.md"
        p.write_text("# 空报告\n\n无任何数据。\n", encoding="utf-8")
        out = extract_report(p)
        assert out["total_points"] == 0
        assert out["sampled_points"] == 0

    def test_verdict_missing_checklist(self, tmp_path: Path):
        """无 checklist 文件时 verdict_report 返回 FAIL."""
        from lib.report_audit import verdict_report

        p = tmp_path / "no_checklist.md"
        p.write_text("# 报告\n", encoding="utf-8")
        v = verdict_report(p)
        assert v["verdict"] == "FAIL"
        assert "error" in v

    def test_verdict_fail_on_deviation_above_threshold(self, tmp_path: Path):
        """偏差 > 5% → verdict FAIL."""
        from lib.report_audit import extract_report, verdict_report
        import json

        p = tmp_path / "report.md"
        p.write_text(SAMPLE_MD, encoding="utf-8")
        extract_report(p)
        checklist = p.with_suffix(".audit_checklist.json")
        data = json.loads(checklist.read_text(encoding="utf-8"))
        for c in data["checks"]:
            c["fetched_value"] = c["reported_value"] * 1.10  # 10% 偏差
            c["fetched_source"] = "test"
        checklist.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        v = verdict_report(p)
        assert v["verdict"] == "FAIL"
        assert v["failed"] >= 1

    def test_verdict_pass_when_all_verified(self, tmp_path: Path):
        """全部核验通过 → verdict PASS，pending=0."""
        from lib.report_audit import extract_report, verdict_report
        import json

        p = tmp_path / "report.md"
        p.write_text(SAMPLE_MD, encoding="utf-8")
        extract_report(p)
        checklist = p.with_suffix(".audit_checklist.json")
        data = json.loads(checklist.read_text(encoding="utf-8"))
        for c in data["checks"]:
            c["fetched_value"] = c["reported_value"]
            c["fetched_source"] = "test"
        checklist.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        v = verdict_report(p)
        assert v["verdict"] == "PASS"
        assert v["pending"] == 0

    def test_deviation_pct_near_zero(self):
        """两个几乎相等的浮点数 → 低偏差，不触发 100%."""
        from lib.report_audit import _deviation_pct

        dev = _deviation_pct(12.5, 12.500000000001)
        assert dev < 1.0  # 不应返回 100.0

    def test_deviation_pct_exact_zero(self):
        """完全相等的值 → 偏差 0."""
        from lib.report_audit import _deviation_pct

        dev = _deviation_pct(0.0, 0.0)
        assert dev == 0.0
