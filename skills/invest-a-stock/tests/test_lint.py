"""Tests for compliance lint rule loading."""

from __future__ import annotations

import io
from pathlib import Path
from unittest.mock import patch

import pytest


class TestLintRulesLoad:
    def test_load_rules_raises_when_pyyaml_missing(self):
        from lib import lint as lint_mod

        lint_mod._RULES_CACHE = None
        with patch.object(lint_mod, "yaml", None):
            with pytest.raises(lint_mod.RulesLoadError, match="pyyaml"):
                lint_mod.load_rules()
        lint_mod._RULES_CACHE = None

    def test_lint_file_raises_when_rules_unavailable(self, tmp_path):
        from lib import lint as lint_mod

        report = tmp_path / "report.md"
        report.write_text("# test\n", encoding="utf-8")
        lint_mod._RULES_CACHE = None
        with patch.object(lint_mod, "load_rules", side_effect=lint_mod.RulesLoadError("test")):
            with pytest.raises(lint_mod.RulesLoadError):
                lint_mod.lint_file(report)
        lint_mod._RULES_CACHE = None

    def test_load_rules_succeeds_with_yaml(self):
        from lib import lint as lint_mod

        lint_mod._RULES_CACHE = None
        rules = lint_mod.load_rules()
        assert isinstance(rules, list)
        assert len(rules) > 0
        lint_mod._RULES_CACHE = None


class TestLintBehaviorParity:
    def test_print_results_can_fail_on_warning_threshold(self):
        from lib.lint import LintFinding, print_results

        findings = [
            LintFinding(
                line=1,
                rule_id="wording-crash",
                severity="warning",
                message="禁止使用'崩盘'",
                context="若发生崩盘",
            )
        ]
        out = io.StringIO()
        assert print_results("report.md", findings, fail_on="warning", file=out) == 1
        assert print_results("report.md", findings, fail_on="error", file=io.StringIO()) == 0

    def test_structure_analysis_without_fact_looks_back_50_lines(self, tmp_path):
        from lib import lint as lint_mod

        report = tmp_path / "report.md"
        filler = "\n".join(f"第{i}行" for i in range(1, 10))
        report.write_text(f"[事实]\n数据点\n{filler}\n[分析]\n结论\n", encoding="utf-8")
        lint_mod._RULES_CACHE = None
        findings = lint_mod.lint_file(report)
        lint_mod._RULES_CACHE = None
        assert not any(f.rule_id == "structure-analysis-without-fact" for f in findings)

    def test_structure_analysis_without_fact_fails_when_too_far(self, tmp_path):
        from lib import lint as lint_mod

        report = tmp_path / "report.md"
        filler = "\n".join(f"第{i}行" for i in range(60))
        report.write_text(f"[事实]\n数据点\n{filler}\n[分析]\n结论\n", encoding="utf-8")
        lint_mod._RULES_CACHE = None
        findings = lint_mod.lint_file(report)
        lint_mod._RULES_CACHE = None
        assert any(f.rule_id == "structure-analysis-without-fact" for f in findings)

    def test_wording_rules_skip_disclaimer_and_source_lines(self, tmp_path):
        from lib import lint as lint_mod

        report = tmp_path / "report.md"
        report.write_text(
            "风险提示：文中禁止使用'崩盘'一词。\n"
            "[来源: 示例] 这里引用'经典周期顶部信号'原文。\n",
            encoding="utf-8",
        )
        lint_mod._RULES_CACHE = None
        findings = lint_mod.lint_file(report)
        lint_mod._RULES_CACHE = None
        assert not any(f.rule_id in {"wording-crash", "wording-classic-top"} for f in findings)

    def test_price_drop_rule_skips_conditional_context(self, tmp_path):
        from lib import lint as lint_mod

        report = tmp_path / "report.md"
        report.write_text(
            "若盈利路径不及预期、且估值回到周期中枢：\n"
            "股价也可能下跌 60-80%\n",
            encoding="utf-8",
        )
        lint_mod._RULES_CACHE = None
        findings = lint_mod.lint_file(report)
        lint_mod._RULES_CACHE = None
        assert not any(f.rule_id == "wording-certain-price-drop" for f in findings)

    def test_placeholder_zhanwei_is_reported(self, tmp_path):
        from lib import lint as lint_mod

        report = tmp_path / "report.md"
        report.write_text("这里先占位，稍后补充。\n", encoding="utf-8")
        lint_mod._RULES_CACHE = None
        findings = lint_mod.lint_file(report)
        lint_mod._RULES_CACHE = None
        assert any(f.rule_id == "placeholder-zhanwei" for f in findings)

    def test_known_violation_ting_pai_allows_suggest_watch(self, tmp_path):
        from lib import lint as lint_mod

        report = tmp_path / "report.md"
        report.write_text("建议关注行业政策变化。\n", encoding="utf-8")
        lint_mod._RULES_CACHE = None
        findings = lint_mod.lint_file(report)
        lint_mod._RULES_CACHE = None
        assert not any(f.rule_id == "known-violation-ting-pai" for f in findings)

    def test_precommit_profile_skips_evidence_tag_warning(self, tmp_path):
        from lib import lint as lint_mod

        report = tmp_path / "report.md"
        report.write_text("风险提示：这里引用崩盘一词。\n", encoding="utf-8")
        lint_mod._RULES_CACHE = None
        findings = lint_mod.lint_file(report, profile="precommit")
        lint_mod._RULES_CACHE = None
        assert not any(f.rule_id == "structure-missing-evidence-tag" for f in findings)

    def test_precommit_skips_law6_standalone(self, tmp_path):
        from lib import lint as lint_mod

        report = tmp_path / "report.md"
        report.write_text("子问题：谁在卖出、为什么？\n", encoding="utf-8")
        lint_mod._RULES_CACHE = None
        findings = lint_mod.lint_file(report, profile="precommit")
        lint_mod._RULES_CACHE = None
        assert not any(f.rule_id.startswith("law6-") for f in findings)

    def test_precommit_skips_law6_explicit_advice(self, tmp_path):
        from lib import lint as lint_mod

        report = tmp_path / "report.md"
        report.write_text("建议买入该标的。\n", encoding="utf-8")
        lint_mod._RULES_CACHE = None
        findings = lint_mod.lint_file(report, profile="precommit")
        lint_mod._RULES_CACHE = None
        assert not any(f.rule_id.startswith("law6-") for f in findings)

    def test_precommit_skips_known_violation(self, tmp_path):
        from lib import lint as lint_mod

        report = tmp_path / "report.md"
        report.write_text("建议买入。\n", encoding="utf-8")
        lint_mod._RULES_CACHE = None
        findings = lint_mod.lint_file(report, profile="precommit")
        lint_mod._RULES_CACHE = None
        assert not any(f.rule_id.startswith("known-violation") for f in findings)

    def test_claude_law6_sell_allows_who_sells_question(self, tmp_path):
        from lib import lint as lint_mod

        report = tmp_path / "report.md"
        report.write_text("子问题：谁在卖出、为什么？\n", encoding="utf-8")
        lint_mod._RULES_CACHE = None
        findings = lint_mod.lint_file(report, profile="claude")
        lint_mod._RULES_CACHE = None
        assert not any(f.rule_id == "law6-sell-standalone" for f in findings)

    def test_precommit_skips_placeholder_rules(self, tmp_path):
        from lib import lint as lint_mod

        report = tmp_path / "report.md"
        report.write_text("| 管理层 | 定性维度待补充 |\n", encoding="utf-8")
        lint_mod._RULES_CACHE = None
        findings = lint_mod.lint_file(report, profile="precommit")
        lint_mod._RULES_CACHE = None
        assert not any(f.rule_id.startswith("placeholder-") for f in findings)

    def test_precommit_skips_law16(self, tmp_path):
        from lib import lint as lint_mod

        report = tmp_path / "report.md"
        report.write_text("当前处于左侧。\n", encoding="utf-8")
        lint_mod._RULES_CACHE = None
        findings = lint_mod.lint_file(report, profile="precommit")
        lint_mod._RULES_CACHE = None
        assert not any(f.rule_id.startswith("law16") for f in findings)

    def test_unknown_severity_counts_as_blocking(self):
        from lib.lint import LintFinding, _count_by_severity

        findings = [
            LintFinding(
                line=1,
                rule_id="future-critical",
                severity="critical",
                message="unknown severity",
                context="test",
            )
        ]
        assert _count_by_severity(findings, "error") == 1
        assert _count_by_severity(findings, "warning") == 1

    def test_directory_lint_summary_reflects_fail_on_warning(self, tmp_path, capsys):
        from lib import lint as lint_mod

        d = tmp_path / "reports"
        d.mkdir()
        (d / "a.md").write_text("若发生崩盘。\n", encoding="utf-8")
        (d / "b.md").write_text("# clean\n", encoding="utf-8")

        lint_mod._RULES_CACHE = None
        with patch.object(lint_mod, "lint_directory") as mock_dir:
            mock_dir.return_value = {
                "a.md": [
                    lint_mod.LintFinding(
                        line=1,
                        rule_id="wording-crash",
                        severity="warning",
                        message="禁止使用'崩盘'",
                        context="若发生崩盘",
                    )
                ],
                "b.md": [],
            }
            from invest import cmd_lint
            from argparse import Namespace

            args = Namespace(
                target=d,
                profile="engine",
                fail_on="warning",
            )
            code = cmd_lint(args)
        lint_mod._RULES_CACHE = None
        captured = capsys.readouterr()
        assert code == 1
        assert "1 个文件存在违规（含警告）" in captured.out

    def test_structure_analysis_stops_at_section_header(self, tmp_path):
        from lib import lint as lint_mod

        report = tmp_path / "report.md"
        report.write_text(
            "## 第一节\n"
            "[事实]\n"
            "数据点\n"
            "\n"
            "## 第二节\n"
            "[分析]\n"
            "结论\n",
            encoding="utf-8",
        )
        lint_mod._RULES_CACHE = None
        findings = lint_mod.lint_file(report)
        lint_mod._RULES_CACHE = None
        assert any(f.rule_id == "structure-analysis-without-fact" for f in findings)
