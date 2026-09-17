"""Tests for compliance lint rule loading."""

from __future__ import annotations

import io
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

    def test_yiding_skips_neutral_quantifier_context(self, tmp_path):
        """'有一定X'/'这一定价' 是中性描述，不是绝对化断言（v0.2.3 修复）。"""
        from lib import lint as lint_mod

        report = tmp_path / "report.md"
        report.write_text(
            "下方有一定承接，但短期缺乏催化剂。\n"
            "公司在 CW 布局有一定对冲。\n"
            "市场已有一定预期。\n"
            "这一定价隐含了对增速骤降的担忧。\n"
            "这一定性判断需要 Q2-Q3 业绩验证。\n",
            encoding="utf-8",
        )
        lint_mod._RULES_CACHE = None
        findings = lint_mod.lint_file(report, profile="claude")
        lint_mod._RULES_CACHE = None
        assert not any(f.rule_id == "wording-absolute-yiding" for f in findings)

    def test_yiding_keeps_assertions_and_skips_conditionals(self, tmp_path):
        """绝对化断言保留；'如果一定要'条件句豁免。"""
        from lib import lint as lint_mod

        report = tmp_path / "report.md"
        report.write_text(
            "该模式一定要持续有效。\n"
            "这种结构一定会在下季度兑现。\n"
            "如果一定要减仓，唯一站得住脚的规则是估值分位减仓。\n",
            encoding="utf-8",
        )
        lint_mod._RULES_CACHE = None
        findings = lint_mod.lint_file(report, profile="claude")
        lint_mod._RULES_CACHE = None
        ids = {f.rule_id for f in findings}
        assert "wording-absolute-yiding" in ids  # 前两行仍报
        # 条件句行被豁免，但断言行仍报 → 至少 1 条
        assert len([f for f in findings if f.rule_id == "wording-absolute-yiding"]) == 1

    def test_biran_skips_negative_context(self, tmp_path):
        """'不代表/不等于必然' 是否定语境，不是绝对化断言（v0.2.3 修复）。"""
        from lib import lint as lint_mod

        report = tmp_path / "report.md"
        report.write_text(
            "目标价不代表股价必然到达。\n"
            "风险不等于必然发生。\n",
            encoding="utf-8",
        )
        lint_mod._RULES_CACHE = None
        findings = lint_mod.lint_file(report, profile="claude")
        lint_mod._RULES_CACHE = None
        assert not any(f.rule_id == "wording-absolute-biran" for f in findings)

    def test_biran_keeps_assertions(self, tmp_path):
        """分析性绝对化断言'必然'仍报。"""
        from lib import lint as lint_mod

        report = tmp_path / "report.md"
        report.write_text(
            "拥挤度极致后必然均值回归。\n"
            "车企在价格战中必然压价电池供应商。\n",
            encoding="utf-8",
        )
        lint_mod._RULES_CACHE = None
        findings = lint_mod.lint_file(report, profile="claude")
        lint_mod._RULES_CACHE = None
        assert any(f.rule_id == "wording-absolute-biran" for f in findings)

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


class TestConclusionEvidenceFallback:
    """R-A2 兜底（v0.2.8）：结论断言行前 60 行无证据标签 → error。

    引擎为行级 lookback（无 forward/section scope）：本规则拦截「主要结论：…」
    这类裸断言行（非 `## 标题`、非 `**结论：**` 行——后两者由 Python 侧
    report_qc.conclusion_evidence_findings 承担结构级检查）。
    """

    def test_conclusion_line_without_nearby_evidence_flagged(self, tmp_path):
        from lib import lint as lint_mod

        report = tmp_path / "report.md"
        report.write_text("主要结论：公司增长稳健，估值处于历史高位。\n", encoding="utf-8")
        lint_mod._RULES_CACHE = None
        findings = lint_mod.lint_file(report)
        lint_mod._RULES_CACHE = None
        assert any(f.rule_id == "wording-conclusion-evidence-fallback" for f in findings)

    def test_conclusion_line_with_evidence_pass(self, tmp_path):
        from lib import lint as lint_mod

        report = tmp_path / "report.md"
        report.write_text(
            "- 营收 +12.3% [来源: engine financials]\n"
            "主要结论：公司增长稳健。\n",
            encoding="utf-8",
        )
        lint_mod._RULES_CACHE = None
        findings = lint_mod.lint_file(report)
        lint_mod._RULES_CACHE = None
        assert not any(f.rule_id == "wording-conclusion-evidence-fallback" for f in findings)


class TestNegativeSoftenedRules:
    """R-A5（v0.2.8）：负面数据仅正化表述 → 必须带方向性数字 + 来源。"""

    def test_negative_softened_without_number_flagged(self, tmp_path):
        from lib import lint as lint_mod

        report = tmp_path / "report.md"
        report.write_text("净流出规模收窄，市场情绪回暖。\n", encoding="utf-8")
        lint_mod._RULES_CACHE = None
        findings = lint_mod.lint_file(report)
        lint_mod._RULES_CACHE = None
        assert any(
            f.rule_id == "wording-negative-softened-no-number" and f.severity == "error"
            for f in findings
        )

    def test_negative_softened_with_number_passes(self, tmp_path):
        from lib import lint as lint_mod

        report = tmp_path / "report.md"
        report.write_text("净流出收窄至 -12.3%，较上月改善 5.1 亿。\n", encoding="utf-8")
        lint_mod._RULES_CACHE = None
        findings = lint_mod.lint_file(report)
        lint_mod._RULES_CACHE = None
        assert not any(
            f.rule_id == "wording-negative-softened-no-number" for f in findings
        )


class TestFilenameFormatLint:
    def test_recommended_datetime_filename_has_no_filename_findings(self, tmp_path):
        from lib import lint as lint_mod

        report = tmp_path / "2026-07-22-14-30-05.md"
        report.write_text("# ok\n", encoding="utf-8")
        lint_mod._RULES_CACHE = None
        findings = lint_mod.lint_file(report, profile="engine")
        lint_mod._RULES_CACHE = None
        assert not any(f.rule_id.startswith("filename-format-") for f in findings)

    def test_date_only_filename_gets_datetime_recommendation(self, tmp_path):
        from lib import lint as lint_mod

        report = tmp_path / "2026-07-22.md"
        report.write_text("# ok\n", encoding="utf-8")
        lint_mod._RULES_CACHE = None
        findings = lint_mod.lint_file(report, profile="engine")
        lint_mod._RULES_CACHE = None
        filename_findings = [f for f in findings if f.rule_id.startswith("filename-format-")]
        assert len(filename_findings) == 1
        assert filename_findings[0].rule_id == "filename-format-datetime"


class TestP3CostAnchorGuardrail:
    """P-3（v0.2.9）：成本锚定护栏——账户历史字段不得作为决策理由。

    设计依据：p-domain-behavioral-foundations-2026-09-05.md §7（L0 词面/L1 理由连接）。
    """

    _POS = [
        ("回到成本就卖", "p3-cost-anchor-threshold"),
        ("等回本再说", "p3-cost-anchor-l0"),
        ("赚够了走人", "p3-cost-anchor-threshold"),
        ("涨到 30% 就走人", "p3-cost-anchor-threshold"),
        ("回本才走", "p3-cost-anchor-threshold"),
    ]

    _NEG = [
        "当前价格高于买入成本分布区间",      # 成本分布 = 筹码结构事实
        "若盈利路径不及预期、估值回到周期中枢",  # 估值中枢 ≠ 回本
        "该策略的成本优势来自规模效应",        # 无关语境
        "跌破均线后按计划止损",              # 市场结构参考点（白名单语义）
        "先做假设检查，不等回本（P-3）",       # 护栏自身词汇（规则内指令，不自触发）
        "买入成本 10 元，现在 15 元",         # 成本事实陈述（无动作）
        "无需等回本再评估，先检查假设是否失效",  # P-3 自身要求的纪律措辞（F6）
        "价格回到成本线附近，构成支撑区",       # 市场结构技术位描述（F6）
        "成本线附近有筹码支撑",               # 同上（F6）
        "| 持仓成本 | 决策理由 | 执行 |",      # markdown 表格行（F10 skip ^\|）
    ]

    def test_p3_positive_lines(self, tmp_path):
        from lib import lint as lint_mod

        for text, rule_id in self._POS:
            report = tmp_path / "report.md"
            report.write_text(f"# t\n\n{text}\n", encoding="utf-8")
            lint_mod._RULES_CACHE = None
            findings = lint_mod.lint_file(report)
            lint_mod._RULES_CACHE = None
            assert any(f.rule_id == rule_id for f in findings), f"应命中 {rule_id}: {text}"

    def test_p3_negative_lines(self, tmp_path):
        from lib import lint as lint_mod

        for text in self._NEG:
            report = tmp_path / "report.md"
            report.write_text(f"# t\n\n{text}\n", encoding="utf-8")
            lint_mod._RULES_CACHE = None
            findings = lint_mod.lint_file(report)
            lint_mod._RULES_CACHE = None
            p3 = [f for f in findings if f.rule_id.startswith("p3-")]
            assert not p3, f"不应命中 P-3: {text} → {[f.context for f in p3]}"

    def test_p3_l1_reason_connector_line(self, tmp_path):
        """L1 理由连接层（line scope，F10）：『账户历史字段 → 动作词』同现 → warning。"""
        from lib import lint as lint_mod

        report = tmp_path / "report.md"
        report.write_text("亏损超过两成就止损\n", encoding="utf-8")
        lint_mod._RULES_CACHE = None
        findings = lint_mod.lint_file(report)
        lint_mod._RULES_CACHE = None
        assert any(f.rule_id == "p3-account-history-action" for f in findings)

    def test_p3_meta_mention_not_self_triggered(self, tmp_path):
        """规则元叙述（'禁止使用'回本''句式）不自触发（同 wording 规则 skip 惯例）。"""
        from lib import lint as lint_mod

        report = tmp_path / "report.md"
        report.write_text("风险提示：文中禁止使用'回本'一词。\n", encoding="utf-8")
        lint_mod._RULES_CACHE = None
        findings = lint_mod.lint_file(report)
        lint_mod._RULES_CACHE = None
        p3 = [f for f in findings if f.rule_id.startswith("p3-")]
        assert not p3, f"元叙述不应命中 P-3: {[f.context for f in p3]}"


class TestReview2Guardrail:
    """code-review max round2：L1 跨行恢复 / 表格豁免 / 否定句 L0。"""

    def test_p3_l1_cross_line_cooccurrence(self, tmp_path):
        """review2 A-2：浮盈状态一行、动作下一行（近距跨行伪装结构）→ paragraph 命中。"""
        from lib import lint as lint_mod

        report = tmp_path / "report.md"
        report.write_text("- 浮盈 20%\n- 止盈卖出\n", encoding="utf-8")
        lint_mod._RULES_CACHE = None
        findings = lint_mod.lint_file(report)
        lint_mod._RULES_CACHE = None
        assert any(f.rule_id == "p3-account-history-action" for f in findings)

    def test_p3_l1_table_paragraph_skipped(self, tmp_path):
        """表格段（^\\| 起始）豁免——段落引擎 skip 生效。"""
        from lib import lint as lint_mod

        report = tmp_path / "report.md"
        report.write_text("| 项目 | 数值 |\n|---|---|\n| 亏损 | 止损执行 |\n", encoding="utf-8")
        lint_mod._RULES_CACHE = None
        findings = lint_mod.lint_file(report)
        lint_mod._RULES_CACHE = None
        p3 = [f for f in findings if f.rule_id.startswith("p3-")]
        assert not p3

    def test_p3_negation_variants_do_not_fire(self, tmp_path):
        """review2 A-3：不要/不必/不用 等回本 = P-3 纪律措辞 → 不命中 L0 error。"""
        from lib import lint as lint_mod

        for text in ("不要等回本，先查假设", "不必等回本再评估", "不用等回本，直接看逻辑失效"):
            report = tmp_path / "r.md"
            report.write_text(text + "\n", encoding="utf-8")
            lint_mod._RULES_CACHE = None
            findings = lint_mod.lint_file(report)
            lint_mod._RULES_CACHE = None
            p3 = [f for f in findings if f.rule_id.startswith("p3-")]
            assert not p3, f"纪律措辞不应命中: {text}"

    def test_p3_back_to_cost_line_action_fires(self, tmp_path):
        """review2 A-3：回到成本线之后就卖（连接词间隔）→ threshold 命中。"""
        from lib import lint as lint_mod

        report = tmp_path / "report.md"
        report.write_text("回到成本线之后就卖\n", encoding="utf-8")
        lint_mod._RULES_CACHE = None
        findings = lint_mod.lint_file(report)
        lint_mod._RULES_CACHE = None
        assert any(f.rule_id == "p3-cost-anchor-threshold" for f in findings)

    def test_p3_skip_line_does_not_exempt_whole_paragraph(self, tmp_path):
        """review #9：⚠️ 行只豁免自身行，同段其余行仍参与跨行检测（FN 修复——
        旧整段豁免使 3 行 bullet 中 1 行带 ⚠️ 即整段失明）。"""
        from lib import lint as lint_mod

        report = tmp_path / "report.md"
        report.write_text("- 数据不足，部分指标缺省 ⚠️\n- 本批浮盈 20%\n- 破位止损离场\n",
                          encoding="utf-8")
        lint_mod._RULES_CACHE = None
        findings = lint_mod.lint_file(report)
        lint_mod._RULES_CACHE = None
        assert any(f.rule_id == "p3-account-history-action" for f in findings)

    def test_p3_title_plus_table_paragraph_skipped(self, tmp_path):
        """review #9：标题行 + 表格行同一段（无空行）→ 表格行逐行豁免（FP 修复——
        旧实现 '^\\|' 只匹配段落首行，标题行开头导致整段不豁免、误报命中）。"""
        from lib import lint as lint_mod

        report = tmp_path / "report.md"
        report.write_text("### 持仓表现\n| 亏损比例 | 是否止损 |\n|---|---|\n| 20% | 是 |\n",
                          encoding="utf-8")
        lint_mod._RULES_CACHE = None
        findings = lint_mod.lint_file(report)
        lint_mod._RULES_CACHE = None
        p3 = [f for f in findings if f.rule_id.startswith("p3-")]
        assert not p3


class TestV030ConventionRuleBehavior:
    """R-E01/E03/E04 的**行为级**验证（2026-09-13 轮末评审补）。

    ⚠️ 原测试只验「规则已注册 + 条文在文档里」，不跑引擎——于是两个缺陷静默通过：
    ① R-E04 是 error 级块级拦截，却**不在 `precommit` profile 的放行清单**里，
       而法定第 0 层正是 `report_qc.py <file> --fail-on error`（默认 profile=precommit）
       → 承诺的拦截在强制流程里永不触发；
    ② `wording-message-no-tristate` 的 skip 含「传言」，而不带标注的「市场传言…」
       同样含该词 → 该规则存在的**唯一理由**（未标注传闻）永不命中。
    """

    @staticmethod
    def _lint(tmp_path, body: str, profile: str = "claude"):
        from lib.lint import lint_file

        f = tmp_path / "probe.md"
        f.write_text(body, encoding="utf-8")
        return {x.rule_id for x in lint_file(f, profile=profile)}

    _FACT_BLOCK = "# 测试\n\n[事实] 本标的缩量跌不动，量能萎缩至 0.4 倍。\n"

    def test_r_e04_fires_under_claude_profile(self, tmp_path):
        assert "structure-convention-in-fact-block" in self._lint(tmp_path, self._FACT_BLOCK)

    def test_r_e04_fires_under_precommit_profile(self, tmp_path):
        """法定第 0 层跑的是 precommit —— 块级拦截须在此生效。"""
        got = self._lint(tmp_path, self._FACT_BLOCK, profile="precommit")
        assert "structure-convention-in-fact-block" in got, \
            f"R-E04 在 precommit profile 下被跳过（承诺的 error 级拦截失效）：{got}"

    def test_r_e04_covers_stop_loss_wording(self, tmp_path):
        """词表须与 §3.5 / R-E03 同表——曾漏「必带止损」。"""
        body = "# 测试\n\n[事实] 本次记录必带止损。\n"
        assert "structure-convention-in-fact-block" in self._lint(tmp_path, body)

    # ── R-E04/R-E03 词义碰撞 FP 修复回归（reports/ 652 篇实测：20 → 0）────────
    # 「出货」在产业文本中是「出货量」义，「托底」另有经济学义；两者都无市场主体
    # 施动者。收窄为**要求施动者**——这才是误报归零的真正原因，与来源标注无关
    # （v0.3.0 A6 撤回了「来源标注豁免」，见下）。

    def test_r_e04_sourced_shipment_line_not_a_convention(self, tmp_path):
        """产业义「出货」（无市场主体施动者）不命中——带不带来源都一样。

        该行曾因带 [来源: section_3] 被豁免，掩盖了「pattern 本就不匹配」这一
        真实原因；A6 撤回来源豁免后此对照用例说明豁免并非必要。
        """
        for src in (" [来源: section_3]", ""):
            body = ("# 测试\n\n**[事实]** 行业量价：高盛两度上修 800G 出货至 3350 万只"
                    + src + "。\n")
            assert "structure-convention-in-fact-block" not in self._lint(
                tmp_path, body, profile="precommit"), src

    def test_r_e04_sourced_convention_line_now_flagged(self, tmp_path):
        """v0.3.0 A6 语义变更：带 [来源:] **不再**豁免。

        旧行为（2026-09-14 引入）：该行因带 `[来源: 用户陈述]` 被豁免。但 §3.5
        约束 1 管的是**放置位置**——惯例型表述放进 [事实] 块即违规，与其是否带
        来源无关；旧豁免还使「追加一个 [来源:]」成为绕过 error 级门禁的通用手法
        （文档规定的门是 `--fail-on error`）。合法出路：补 §3.5 固定标注，或把
        该表述移出 [事实] 块。
        """
        body = ("# 测试\n\n**[事实]** 用户驱动逻辑：(a) 国家队托底 + 科技战略定位 → 政策底"
                "[来源: 用户陈述 / 2026-07-21]。\n")
        assert "structure-convention-in-fact-block" in self._lint(
            tmp_path, body, profile="precommit")

    def test_r_e04_convention_exempt_only_with_section35_tag(self, tmp_path):
        """唯一合法豁免是 §3.5 固定标注「从业者惯例，非学术验证：{出处}」。"""
        body = ("# 测试\n\n**[事实]** 用户驱动逻辑：(a) 国家队托底 + 科技战略定位 → 政策底"
                "（从业者惯例，非学术验证：用户陈述 / 2026-07-21）。\n")
        assert "structure-convention-in-fact-block" not in self._lint(
            tmp_path, body, profile="precommit")

    def test_r_e04_shipment_wording_not_flagged(self, tmp_path):
        """「出货」的产业义（无市场主体施动者）→ 不命中（单行与多行块都试）。"""
        for body in ("# 测试\n\n[事实] 储能锂电池出货量同比 +139%。\n",
                     "# 测试\n\n[事实]\n- 全球 AI 服务器出货量预计 370 万台\n"
                     "- 800G 出货至 3350 万只\n"):
            assert "structure-convention-in-fact-block" not in self._lint(
                tmp_path, body, profile="precommit"), body

    def test_r_e04_economics_floor_wording_not_flagged(self, tmp_path):
        """「托底」的经济学义（结构性/高股息/政策）→ 不命中。"""
        for body in ("# 测试\n\n[事实] ② 央行购金（结构性托底）。\n",
                     "# 测试\n\n[事实] 高股息托底，但缺乏政策托底。\n"):
            assert "structure-convention-in-fact-block" not in self._lint(
                tmp_path, body, profile="precommit"), body

    def test_r_e04_still_fires_on_unlabeled_convention(self, tmp_path):
        """防失效：无来源标签的真惯例表述必须仍命中（按收窄后词表逐一核对）。"""
        for c in ("[事实] 本标的缩量跌不动，量能萎缩至 0.4 倍。",
                  "[事实] 本次记录必带止损。",
                  "[事实] 回踩低吸区间已到。",
                  "[事实] 明显洗盘。",
                  "[事实] 主力吸筹。",
                  "[事实] 主力出货迹象明显。",
                  "[事实] 北向资金出货。",
                  "[事实] 国家队托底形成政策底。",
                  "[事实] 缩量电风扇行情。",
                  "[事实] 右稳左可结构。"):
            got = self._lint(tmp_path, "# 测试\n\n" + c + "\n", profile="precommit")
            assert "structure-convention-in-fact-block" in got, f"漏拦：{c}"

    def test_r_e04_multiline_source_on_other_line_still_fires(self, tmp_path):
        """skip 是**行级**：来源标签在别的行时，惯例词行仍须命中（规则本意不受损）。"""
        body = ("# 测试\n\n[事实]\n- 主力出货迹象明显\n- 尾盘放量\n"
                "[来源: 龙虎榜 2026-08-05]\n")
        assert "structure-convention-in-fact-block" in self._lint(
            tmp_path, body, profile="precommit")

    def test_r_e03_shipment_wording_not_flagged(self, tmp_path):
        """R-E03（warning）与 R-E04 同词表：出货量义不再刷 warning。"""
        assert "wording-practitioner-convention" not in self._lint(
            tmp_path, "# 测试\n\n全球 AI 服务器出货量预计 370 万台。\n")

    def test_r_e03_agent_convention_still_flagged(self, tmp_path):
        """R-E03 收窄后仍拦真惯例语义（施动者形态）。"""
        for body in ("# 测试\n\n估值透支 + 主力出货。\n",
                     "# 测试\n\n国家队托底 + 国家科技战略。\n"):
            assert "wording-practitioner-convention" in self._lint(tmp_path, body), body

    def test_tristate_rule_fires_on_unlabeled_rumor(self, tmp_path):
        """不带三态标注的传闻是**该规则存在的唯一理由**，必须命中。"""
        body = "# 测试\n\n市场传言公司将获注资。\n"
        assert "wording-message-no-tristate" in self._lint(tmp_path, body)

    def test_tristate_rule_exempts_labeled_rumor(self, tmp_path):
        """已带三态标注（传言/事实/证实）的行须豁免，不得误伤。"""
        body = "# 测试\n\n（传言）公司将获注资，尚未证实。\n"
        assert "wording-message-no-tristate" not in self._lint(tmp_path, body)


class TestV030Law6FalsePositiveFixes:
    """v0.3.0 全量重审 F-U7-1/2/4：law6 与宏观传导链规则的**误报收窄**。

    每组断言都是成对的：**误报不再命中** + **真违规仍被拦**。
    只证前者会让规则变瞎——收窄匹配精度不等于放宽红线（LAW 6 本身不变）。
    全量语料实测背景：这 5 条规则贡献了 253 份报告中约 32% 的 FAIL，且以误报为主。
    """

    @staticmethod
    def _lint(tmp_path, body: str, profile: str = "claude"):
        from lib import lint as lint_mod
        from lib.lint import lint_file

        lint_mod._RULES_CACHE = None  # 保证读到当前 YAML，不吃上一次的缓存
        f = tmp_path / "probe.md"
        f.write_text(body, encoding="utf-8")
        return {x.rule_id for x in lint_file(f, profile=profile)}

    # ---- F-U7-1：law6-hold-standalone 把**文档要求的合规写法**判为违规 ----

    def test_hold_disclaimer_line_exempt(self, tmp_path):
        """报告自带的强制免责句（含「…或持有的行动判断」）不应命中。"""
        body = (
            "# 测试\n\n> 本速览为多维度事实与量化评分的汇总呈现，"
            "不构成投资建议，不代表买卖或持有的行动判断。\n"
        )
        assert "law6-hold-standalone" not in self._lint(tmp_path, body)

    def test_hold_risk_template_line_exempt(self, tmp_path):
        """致命一击模板句「N 个月持有的最大风险」不应命中。"""
        body = "# 测试\n\n> **1 个月持有的最大风险**：修复段位于 BOLL 位置 83%。\n"
        assert "law6-hold-standalone" not in self._lint(tmp_path, body)

    def test_hold_shareholding_disclosure_exempt(self, tmp_path):
        """持股披露「持有 N 万股」不应命中。"""
        body = "# 测试\n\n- 2026 年 7 月：执行董事袁宏明退休（持有 100 万股）\n"
        assert "law6-hold-standalone" not in self._lint(tmp_path, body)

    def test_hold_advice_still_flagged(self, tmp_path):
        """收窄后仍须拦真建议语义（否则规则变瞎）。"""
        for body in ("# 测试\n\n持有该标的。\n", "# 测试\n\n长期持有。\n"):
            assert "law6-hold-standalone" in self._lint(tmp_path, body), body

    # ---- F-U7-2：law6-buy/sell/target-price 无归属/语境豁免 ----

    def test_buy_institutional_rating_exempt(self, tmp_path):
        """第三方机构评级归属非报告自身建议。"""
        body = "# 测试\n\n- 22 家机构全部给予买入/增持/强推评级（一致看多）\n"
        assert "law6-buy-standalone" not in self._lint(tmp_path, body)

    def test_buy_advice_still_flagged(self, tmp_path):
        assert "law6-buy-standalone" in self._lint(tmp_path, "# 测试\n\n买入该标的。\n")

    def test_sell_fund_flow_and_source_exempt(self, tmp_path):
        """引擎资金流字段与带 [来源:] 的引用非建议。"""
        for body in (
            "# 测试\n\n- 6/17 主力资金净卖出 6330 万\n",
            "# 测试\n\n- 成交额：买入 499.62 亿 / 卖出 455.31 亿 "
            "[来源: Python calc: 沪向 + 深向]\n",
        ):
            assert "law6-sell-standalone" not in self._lint(tmp_path, body), body

    def test_sell_advice_still_flagged(self, tmp_path):
        assert "law6-sell-standalone" in self._lint(tmp_path, "# 测试\n\n卖出该标的。\n")

    def test_target_price_third_party_exempt(self, tmp_path):
        """第三方研报目标价与检索 query 串非报告给出的单一目标价。"""
        body = (
            "# 测试\n\n| **美银研报（补充）** | WebSearch | "
            '`query: "美银 中际旭创 目标价 1650"` | ✅ 有数据 |\n'
        )
        assert "law6-target-price" not in self._lint(tmp_path, body)

    def test_target_price_still_flagged(self, tmp_path):
        assert "law6-target-price" in self._lint(tmp_path, "# 测试\n\n目标价：25.5 元\n")

    # ---- F-U7-4：wording-macro-chain-evidence 误伤产业毛利率表行 ----

    def test_macro_chain_gross_margin_table_row_exempt(self, tmp_path):
        """「毛利率」含「利率」子串 + 表格行箭头 → 曾被判为宏观传导链违规。"""
        body = "# 测试\n\n| 商业 | 毛利率下降 | 未触发 | — | 毛利率 2025→2026: 42.04%→46.06% |\n"
        assert "wording-macro-chain-evidence" not in self._lint(tmp_path, body)

    def test_macro_chain_real_chain_still_flagged(self, tmp_path):
        assert "wording-macro-chain-evidence" in self._lint(
            tmp_path, "# 测试\n\n中东→美债→AI 融资→资产价格同向传导。\n"
        )
