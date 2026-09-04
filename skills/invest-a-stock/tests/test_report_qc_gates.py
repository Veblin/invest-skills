"""report_qc 可读性门禁测试（R-A1）——全部指标 Python 计算。"""
from __future__ import annotations

import re

import pytest

from lib.report_qc import (
    READABILITY_MAX_CHARS,
    READABILITY_LONG_SENT_CHARS,
    conclusion_evidence_findings,
    fact_analysis_pair_findings,
    readability_metrics,
    run_report_qc,
)


def _good_report() -> str:
    return """## 主要结论
- 营收连续增长（数据：近4年 +12.3%/年 [来源: engine financials]），毛利率 42% 维持（逻辑：规模效应传导 [来源: Python calc: revenue_cagr]），分歧点在于海外占比上升的汇率敏感性，风险点在于资本开支 3 年翻倍。
[证据强度: ✅ 强 🌐 多源 🕐 近 30 日 ✓✓ 跨源可验证]
"""


class TestReadabilityMetrics:
    def test_metrics_all_python_computed(self):
        md = "报告" * 200 + "。"
        m = readability_metrics(md)
        assert set(m) == {"total_chars", "sentences", "long_sentence_ratio", "term_density_permille", "summary_elements"}
        assert m["total_chars"] == len(md)
        assert m["sentences"] == 1
        assert isinstance(m["long_sentence_ratio"], float)

    def test_long_sentence_ratio_threshold(self):
        # 单句 > 45 字（READABILITY_LONG_SENT_CHARS）→ 长句占比 > 0.5
        long = "这是一句用于测试长句判定的句子，它的长度必须显著超过阈值，否则测试将无法验证长句占比的计算是否准确。" * 20
        m = readability_metrics(long)
        assert m["long_sentence_ratio"] > 0.5

    def test_summary_elements_missing_flagged(self):
        bad = """## 主要结论
- 公司增长稳健。[来源: engine]
"""
        findings = run_report_qc(bad)
        ids = {f.rule_id for f in findings}
        assert "readability-summary-elements" in ids


class TestConclusionEvidence:
    def test_missing_evidence_tag_flagged(self):
        bad = """## 主要结论
- 公司增长稳健。
"""
        findings = conclusion_evidence_findings(bad)
        assert findings and findings[0].severity == "error"

    def test_sufficient_evidence_pass(self):
        assert conclusion_evidence_findings(_good_report()) == []


class TestFactAnalysisPair:
    """R-A6：[分析] 节段内须有前置 [事实] 块（50 行回溯 + ##/### 边界停止）。"""

    def test_analysis_without_fact_flagged(self):
        bad = """## 指数估值
[结论] PE 处历史高位。
[分析] 指数 PE 高企，需关注均值回归。
"""
        findings = fact_analysis_pair_findings(bad)
        assert any(f.rule_id == "structure-fact-analysis-pair" and f.severity == "error"
                   for f in findings)

    def test_analysis_with_fact_passes(self):
        good = """## 指数估值
[事实] 指数 PE 18.5x [来源: engine index_pe_snapshot]
[分析] 指数 PE 高企，需关注均值回归。
"""
        assert fact_analysis_pair_findings(good) == []

    def test_fact_from_previous_section_not_counted(self):
        """上一节段内的 [事实] 不满足本节的 [分析]（节段边界阻断）。"""
        bad = """## 产品快照
[事实] 最新价 1.23 [来源: engine]
---
## 指数估值
[分析] 指数 PE 高企，需关注均值回归。
"""
        assert any(f.rule_id == "structure-fact-analysis-pair" for f in
                   fact_analysis_pair_findings(bad))


# ── 全量审查 P0-2：核心结论标题/结构行排除/D 级判定 ──

class TestFullReviewConclusionGate:
    def test_core_conclusion_heading_recognized(self):
        """真实模板 `## 核心结论` 被识别——旧 regex 只匹配主要/结论 → 门禁
        对 210/210 真实报告失效（误报缺要素且检不到结论段）。"""
        from lib.report_qc import run_report_qc

        md = ("## 核心结论\n"
              "基本面数据稳健，净利同比 +5.6% [来源: engine]。\n"
              "市场分歧在于估值消化节奏 [来源: engine]。\n"
              "若需求放缓则存在下行风险 [来源: engine]。\n")
        assert run_report_qc(md) == []

    def test_structure_lines_not_assertions(self):
        """表行/引用行不算断言（FP 源）；带 B 级证据断言通过。"""
        from lib.report_qc import conclusion_evidence_findings

        md = ("## 主要结论\n"
              "| 指标 | 值 |\n|---|---|\n| 营收 | 382 亿 |\n"
              "> 以上不构成投资建议\n"
              "该标的有望走强 [证据: B]。\n")
        assert conclusion_evidence_findings(md) == []

    def test_d_grade_conclusion_reports_level_error(self):
        """D 级断言触发 level error（旧死代码 tagged==0 分支不可达——D 级
        全标 tagged 却零拦截）。"""
        from lib.report_qc import conclusion_evidence_findings

        md = "## 核心结论\n该标的有望走强 [证据: D 推测]。\n"
        f = conclusion_evidence_findings(md)
        assert any(x.rule_id == "wording-conclusion-evidence-level"
                   for x in f)

    def test_summary_elements_absent_when_no_conclusion_heading(self):
        """无结论段标题 → 四要素不误报缺（对前置引擎输出）。"""
        from lib.report_qc import readability_findings

        md = "## 估值分析\n估值分位数据齐全。\n"
        f = readability_findings(md)
        assert not any(x.rule_id == "readability-summary-elements" for x in f)
