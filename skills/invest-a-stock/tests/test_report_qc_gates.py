"""report_qc 可读性门禁测试（R-A1/R-A2/R-A6）——全部指标 Python 计算。

v0.3.0 A3：这些检查已从 invest-a-stock/scripts/lib/report_qc.py（旧 228 行
模块，已删除）移植进共享版 skills/lib/report_qc.py，使 `invest.py qc-report`
与第 0 层准出走**同一实现**。本文件随之改为导入共享版——注意 findings 现在是
`LayerResult.details` 同款的 dict（`{"id","severity","line","message","context"}`），
不再是 QcFinding dataclass。
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

# 与 skills/lib/tests/test_report_qc.py 同款：无条件插 0，防其他 skill 目录
# 先行入 path 遮蔽同名模块（pytest prepend-mode 坑）。
_SKILLS_LIB = Path(__file__).resolve().parents[2] / "lib"
sys.path.insert(0, str(_SKILLS_LIB))

from report_qc import (  # noqa: E402
    READABILITY_MAX_CHARS,
    READABILITY_LONG_SENT_CHARS,
    _check_conclusion_evidence,
    _check_readability,
    conclusion_evidence_findings,
    fact_analysis_pair_findings,
    readability_findings,
    readability_metrics,
)


def _good_report() -> str:
    return """## 主要结论
- 营收连续增长（数据：近4年 +12.3%/年 [来源: engine financials]），毛利率 42% 维持（逻辑：规模效应传导 [来源: Python calc: revenue_cagr]），分歧点在于海外占比上升的汇率敏感性，风险点在于资本开支 3 年翻倍。
[证据强度: ✅ 强 🌐 多源 🕐 近 30 日 ✓✓ 跨源可验证]
"""


def _run_all(md: str) -> list[dict]:
    return (readability_findings(md) + conclusion_evidence_findings(md)
            + fact_analysis_pair_findings(md))


def _ids(findings: list[dict]) -> set[str]:
    return {f["id"] for f in findings}


class TestReadabilityMetrics:
    def test_metrics_all_python_computed(self):
        md = "报告" * 200 + "。"
        m = readability_metrics(md)
        assert set(m) == {"total_chars", "sentences", "long_sentence_ratio",
                          "term_density_permille", "summary_elements"}
        assert m["total_chars"] == len(md)
        assert m["sentences"] == 1
        assert isinstance(m["long_sentence_ratio"], float)

    def test_long_sentence_ratio_threshold(self):
        # 单句 > 45 字（READABILITY_LONG_SENT_CHARS）→ 长句占比 > 0.5
        long = ("这是一句用于测试长句判定的句子，它的长度必须显著超过阈值，"
                "否则测试将无法验证长句占比的计算是否准确。") * 20
        m = readability_metrics(long)
        assert m["long_sentence_ratio"] > 0.5

    def test_summary_elements_missing_flagged(self):
        bad = """## 主要结论
- 公司增长稳健。[来源: engine]
"""
        assert "readability-summary-elements" in _ids(_run_all(bad))


class TestConclusionEvidence:
    def test_missing_evidence_tag_flagged(self):
        bad = """## 主要结论
- 公司增长稳健。
"""
        findings = conclusion_evidence_findings(bad)
        assert findings and findings[0]["severity"] == "error"

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
        assert any(f["id"] == "structure-fact-analysis-pair" and f["severity"] == "error"
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
        assert any(f["id"] == "structure-fact-analysis-pair"
                   for f in fact_analysis_pair_findings(bad))


# ── 全量审查 P0-2：核心结论标题/结构行排除/D 级判定 ──

class TestFullReviewConclusionGate:
    def test_core_conclusion_heading_recognized(self):
        """真实模板 `## 核心结论` 被识别——旧 regex 只匹配主要/结论 → 门禁
        对 210/210 真实报告失效（误报缺要素且检不到结论段）。"""
        md = ("## 核心结论\n"
              "基本面数据稳健，净利同比 +5.6% [来源: engine]。\n"
              "市场分歧在于估值消化节奏 [来源: engine]。\n"
              "若需求放缓则存在下行风险 [来源: engine]。\n")
        assert _run_all(md) == []

    def test_structure_lines_not_assertions(self):
        """表行/引用行不算断言（FP 源）；带 B 级证据断言通过。"""
        md = ("## 主要结论\n"
              "| 指标 | 值 |\n|---|---|\n| 营收 | 382 亿 |\n"
              "> 以上不构成投资建议\n"
              "该标的有望走强 [证据: B]。\n")
        assert conclusion_evidence_findings(md) == []

    def test_d_grade_conclusion_reports_level_error(self):
        """D 级断言触发 level error（旧死代码 tagged==0 分支不可达——D 级
        全标 tagged 却零拦截）。"""
        md = "## 核心结论\n该标的有望走强 [证据: D 推测]。\n"
        f = conclusion_evidence_findings(md)
        assert any(x["id"] == "wording-conclusion-evidence-level" for x in f)

    def test_summary_elements_absent_when_no_conclusion_heading(self):
        """无结论段标题 → 四要素不误报缺（对前置引擎输出）。"""
        md = "## 估值分析\n估值分位数据齐全。\n"
        f = readability_findings(md)
        assert not any(x["id"] == "readability-summary-elements" for x in f)


# ── v0.3.0 A3：层状态映射契约 ──

class TestLayerStatusContract:
    def test_readability_layer_capped_at_warn(self):
        """§3.4 契约锁：R-A1 是**软建议**——「不触发 lint error，不作合规阻断」。

        readability-length 的 finding 是 error 级，但层状态必须封顶 warn；
        否则 `_compute_overall` 会判 FAIL → exit 2 不得交付，把软建议升级成
        交付阻断（旧通道对任何 severity 都只返回 1，故从未违反该契约）。
        """
        layer = _check_readability("报告正文。" * 5000)   # > READABILITY_MAX_CHARS
        assert any(d["severity"] == "error" for d in layer.details), "前置：确有 error 级 finding"
        assert layer.status == "warn"
        assert layer.layer == "readability"

    def test_conclusion_evidence_layer_fails_on_error(self):
        """R-A2/R-A6 是 error 级实质缺陷 → 保留 error→fail 映射。"""
        layer = _check_conclusion_evidence("## 主要结论\n- 公司增长稳健。\n")
        assert layer.status == "fail"
        assert layer.layer == "conclusion-evidence"

    def test_layers_pass_when_clean(self):
        # _good_report 的单句 >45 字 → 合法触发 readability-long-sentence（warn），
        # 它本就是为 R-A2 构造的夹具。
        assert _check_conclusion_evidence(_good_report()).status == "pass"
        short = ("## 主要结论\n"
                 "营收同比 +12.3% [来源: engine]。\n"
                 "因此毛利改善 [来源: engine]。\n"
                 "分歧在于汇率 [来源: engine]。\n"
                 "风险是资本开支 [来源: engine]。\n")
        assert _check_readability(short).status == "pass"


class TestQcReportUsesSharedImplementation:
    """A3 接线锁：`invest.py qc-report` 必须走共享版（含第 0 层各闸门），
    不得再解析到已删除的旧模块。"""

    def test_cli_prints_shared_layers_and_uses_gate_exit_codes(self, tmp_path: Path):
        from invest import cmd_qc_report

        report = tmp_path / "2026-01-01-00-00-00.md"
        report.write_text("# 测试 研究快照\n正文。\n", encoding="utf-8")
        args = SimpleNamespace(path=str(report), fail_on="error")

        import io
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = cmd_qc_report(args)
        out = buf.getvalue()

        # 旧模块只打印「✅ report_qc 通过」/「❌ report_qc 发现 N 项」且无层名
        assert "sourcing" in out, "须含共享版独有的层名"
        assert "❌ report_qc 发现" not in out
        # 第 0 层退出码契约：FAIL → 2（旧实现对 FAIL 只返回 1）
        assert rc in (0, 1, 2)
