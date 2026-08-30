"""report_qc 可读性门禁测试（R-A1）——全部指标 Python 计算。"""
from __future__ import annotations

import re

import pytest

from lib.report_qc import (
    READABILITY_MAX_CHARS,
    READABILITY_LONG_SENT_CHARS,
    conclusion_evidence_findings,
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
