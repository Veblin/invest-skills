"""Tests for skills/lib/report_qc.py — offline QC checks, no network."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

_SKILLS_LIB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SKILLS_LIB))  # 无条件插 0：防其他 skill 目录先行入 path 遮蔽同名模块

from report_qc import (  # noqa: E402
    detect_report_type,
    format_qc_result,
    main,
    qc_directory,
    qc_file,
    qc_latest,
    _check_etf_derived,
    _check_sourcing,
    _compute_overall,
    _run_verify_layers,
)

# ── 可复用的合规样例（含 [事实]/[分析]/[证据强度] + 风险声明）──

COMPLIANT_STOCK = """# 600176 中国巨石 研究备忘录

> ⚠️ 本备忘录由 AI 辅助生成，不构成投资建议。

## 1. 当前状态快照

[事实]
- 2026Q1 营收 131.38 亿（+52.7%）[来源: Tushare fina_indicator]

[分析]
利润增速远超收入增速，反映规模效应释放。

[证据强度: ✅ 强 🌐 多源 🕐 近 30 日 ✓✓ Tushare+akshare 一致]
"""

COMPLIANT_ETF = """# 588000 科创50ETF 研究备忘录

> ⚠️ 本备忘录由 AI 辅助生成，不构成投资建议。

## 1. 产品快照
| 最新价 | 1.5 | fund_etf_spot_em |

## 3. 跟踪质量
| NAV vs MA20 偏离 | **-15.36%** |
| 日均波动率 | **16.38%** |

[事实]
- NAV 1.5 [来源: engine]

[分析]
- NAV 偏离 MA20，处于箱体下沿。

[证据强度: ✅ 强 🌐 多源 🕐 近 30 日 ✓✓ 一致]
"""

COMPLIANT_JOURNAL = """# 交易日志：588000 科创50ETF

## 买入: 科创50ETF (588000) — ETF

### 方案摘要
| 驱动逻辑 | 核心假设 | 失效条件 | 仓位 | 最大亏损 |
|----------|---------|---------|------|---------|
| … | … | … | … | … |

## 逻辑完整性: ✅
## 数据盲点: ⚠️
## 仓位匹配: ✅
## 风险收益比: ✅

### 环境盲点提示（护栏 v1）
"""

COMPLIANT_GAP = """# 跳空缺口扫描报告 20260730

## 扫描摘要
| 日期 | 指数 | 命中数 |
|------|------|--------|
| 2026-07-30 | 沪深300 | 12 |

## 命中列表
| 代码 | 名称 | 缺口率 |
|------|------|--------|
| 600176 | 中国巨石 | 3.2% |
"""


def _write(tmp_path: Path, subdir: str, name: str, content: str) -> Path:
    d = tmp_path / subdir
    d.mkdir(parents=True, exist_ok=True)
    p = d / name
    p.write_text(content, encoding="utf-8")
    return p


# ── 报告类型检测 ──────────────────────────────────────────────────────────


class TestDetectReportType:
    def test_stock_nested_path(self, tmp_path: Path):
        p = _write(tmp_path, "600176-中国巨石", "2026-08-02-10-00-00.md", "# x\n")
        assert detect_report_type(p) == "stock"

    def test_etf_nested_path(self, tmp_path: Path):
        p = _write(tmp_path, "588000-科创50ETF", "2026-08-02-10-00-00.md", "# x\n")
        assert detect_report_type(p) == "etf"

    def test_etf_159_prefix(self, tmp_path: Path):
        p = _write(tmp_path, "159206-卫星ETF", "2026-08-02-10-00-00.md", "# x\n")
        assert detect_report_type(p) == "etf"

    def test_stock_flat_old_format(self, tmp_path: Path):
        p = tmp_path / "600176-中国巨石-20260624.md"
        p.write_text("# x\n", encoding="utf-8")
        assert detect_report_type(p) == "stock"

    def test_gap_scan_dir(self, tmp_path: Path):
        p = _write(tmp_path, "gap-scan", "20260730.md", "# x\n")
        assert detect_report_type(p) == "gap_scan"

    def test_journal_dir(self, tmp_path: Path):
        p = _write(tmp_path, "journal", "2026-07-21-588000-买入.md", "# x\n")
        assert detect_report_type(p) == "journal"

    def test_pulse_dir(self, tmp_path: Path):
        p = _write(tmp_path, "pulse", "2026-08-02.md", "# x\n")
        assert detect_report_type(p) == "pulse"

    def test_unknown_nonstandard(self, tmp_path: Path):
        p = tmp_path / "some-weird-file.md"
        p.write_text("# x\n", encoding="utf-8")
        assert detect_report_type(p) == "unknown"


# ── qc_file 基础（offline：lint + structure）──────────────────────────────


class TestQcFileOffline:
    def test_stock_report_pass(self, tmp_path: Path):
        p = _write(tmp_path, "600176-中国巨石", "2026-08-02-10-00-00.md", COMPLIANT_STOCK)
        r = qc_file(p)
        assert r.report_type == "stock"
        assert r.overall in ("PASS", "WARN")  # precommit 下结构规则可能跳过
        assert any(l.layer == "lint" for l in r.layers)
        assert any(l.layer == "structure" for l in r.layers)

    def test_stock_with_wording_violation_fails(self, tmp_path: Path):
        # claude profile 启用全部 36 条规则（含 law6-*）；precommit 会跳过 law6
        text = COMPLIANT_STOCK.replace("不构成投资建议", "建议买入并加仓，目标价 25.0 元")
        p = _write(tmp_path, "600176-中国巨石", "2026-08-02-10-00-00.md", text)
        r = qc_file(p, profile="claude")
        lint = next(l for l in r.layers if l.layer == "lint")
        assert lint.status == "fail"
        assert r.overall == "FAIL"

    def test_etf_report(self, tmp_path: Path):
        p = _write(tmp_path, "588000-科创50ETF", "2026-08-02-10-00-00.md", COMPLIANT_ETF)
        r = qc_file(p)
        assert r.report_type == "etf"
        derived = next(l for l in r.layers if l.layer == "derived")
        assert derived.status == "pass"

    def test_gap_scan_report(self, tmp_path: Path):
        p = _write(tmp_path, "gap-scan", "20260730.md", COMPLIANT_GAP)
        r = qc_file(p)
        assert r.report_type == "gap_scan"
        structure = next(l for l in r.layers if l.layer == "structure")
        assert structure.status == "pass"

    def test_journal_report(self, tmp_path: Path):
        p = _write(tmp_path, "journal", "2026-07-21-588000-买入.md", COMPLIANT_JOURNAL)
        r = qc_file(p)
        assert r.report_type == "journal"
        structure = next(l for l in r.layers if l.layer == "structure")
        assert structure.status == "pass"

    def test_missing_file(self, tmp_path: Path):
        r = qc_file(tmp_path / "nope.md")
        assert r.overall == "FAIL"

    def test_empty_file(self, tmp_path: Path):
        p = _write(tmp_path, "600176-中国巨石", "2026-08-02-10-00-00.md", "")
        r = qc_file(p)
        assert r.overall in ("PASS", "WARN")


# ── 结构层 ────────────────────────────────────────────────────────────────


class TestStructureChecks:
    def test_stock_missing_evidence_tag_warns(self, tmp_path: Path):
        text = COMPLIANT_STOCK.replace("[证据强度: ✅ 强 🌐 多源 🕐 近 30 日 ✓✓ Tushare+akshare 一致]", "")
        p = _write(tmp_path, "600176-中国巨石", "2026-08-02-10-00-00.md", text)
        r = qc_file(p)
        structure = next(l for l in r.layers if l.layer == "structure")
        assert structure.status == "warn"
        assert any(d["id"] == "structure-evidence" for d in structure.details)

    def test_stock_missing_fact_tag_warns(self, tmp_path: Path):
        text = COMPLIANT_STOCK.replace("[事实]", "[数据]")
        p = _write(tmp_path, "600176-中国巨石", "2026-08-02-10-00-00.md", text)
        r = qc_file(p)
        structure = next(l for l in r.layers if l.layer == "structure")
        assert structure.status == "warn"
        assert any(d["id"] == "structure-fact" for d in structure.details)

    def test_journal_missing_risk_rr_warns(self, tmp_path: Path):
        text = COMPLIANT_JOURNAL.replace("## 风险收益比: ✅", "")
        p = _write(tmp_path, "journal", "2026-07-21-588000-买入.md", text)
        r = qc_file(p)
        structure = next(l for l in r.layers if l.layer == "structure")
        assert structure.status == "warn"
        assert any(d["id"] == "journal-rr" for d in structure.details)

    def test_journal_sell_path_four_dimensions_pass(self, tmp_path: Path):
        """v0.2.5 缺陷 5 防回归：卖出路径四维（一致性/情绪化检测/
        参考点独立性/机会成本）结构检查必须通过（report_qc 曾只认买入四维）。"""
        text = """# 交易日志：588000 科创50ETF

## 卖出: 科创50ETF (588000) — ETF

### 方案摘要
| 方向 | 标的 | 理由 | 重述后独立依据 |
|------|------|------|------|
| 卖出 | 588000 | 落袋为安 | 跌破前低 |

### 1. 与入场逻辑的一致性（Consistency）
评估文字

### 2. 情绪化检测（Emotion Check）
评估文字

### 3. 参考点独立性核对（Reference-Point Check）
- 关键问题：如果这笔交易不是你的持仓，你还会做这个决定吗？

### 4. 机会成本（Opportunity Cost）
评估文字

> 本评估不构成投资建议。
"""
        p = _write(tmp_path, "journal", "2026-08-10-588000-卖出.md", text)
        r = qc_file(p)
        structure = next(l for l in r.layers if l.layer == "structure")
        assert structure.status == "pass", structure.details


# ── 股票报告交付完成度（P0）──────────────────────────────────────────────


_AUTOMATED_STOCK_SNAPSHOT = """# 600176 中国巨石 研究快照

> ⚠️ 本报告由自动化引擎生成，仅供研究备忘录参考，不构成任何投资建议。

## 研究摘要
[事实] 财务数据来自引擎 [来源: engine]
[分析] 已完成事实到结论的推演。
[证据强度: ✅ 强]

### 多头逻辑链
- 盈利增长与现金流改善相互印证 [来源: engine]

### 空头逻辑链
- 需求波动可能压低利润率，需以下季财报验证 [来源: engine]

### 左侧概率的主要支撑依据
- 估值分位较低是条件性支撑 [来源: engine]

### 右侧概率的主要支撑依据
- 趋势仍弱，尚需价格结构确认 [来源: engine]
"""

_VALID_ANALYSIS_SIDECAR = json.dumps([{
    "module": "research",
    "title": "研究发现",
    "facts_md": "财务事实 [来源: engine]",
    "analysis_md": "分析结论 [证据: B]",
    "evidence_tag": "B",
    "position": "research",
}], ensure_ascii=False)


def _completion_layer(result):
    return next(layer for layer in result.layers if layer.layer == "completion")


class TestStockCompletionGate:
    def test_automated_snapshot_without_same_generation_sidecar_fails(self, tmp_path: Path):
        report = _write(tmp_path, "600176-中国巨石", "2026-09-14-13-41-24.md",
                        _AUTOMATED_STOCK_SNAPSHOT)
        result = qc_file(report, fail_on="error")
        completion = _completion_layer(result)
        assert completion.status == "fail"
        assert any(d["id"] == "completion-analysis-sidecar-missing"
                   for d in completion.details)
        assert result.overall == "FAIL"

    def test_automated_snapshot_with_same_generation_sidecar_passes_completion(self, tmp_path: Path):
        report = _write(tmp_path, "600176-中国巨石", "2026-09-14-13-41-24.md",
                        _AUTOMATED_STOCK_SNAPSHOT)
        report.with_suffix(".analysis.json").write_text(_VALID_ANALYSIS_SIDECAR, encoding="utf-8")
        completion = _completion_layer(qc_file(report, fail_on="error"))
        assert completion.status == "pass", completion.details

    def test_explicit_template_markers_fail_even_with_sidecar(self, tmp_path: Path):
        """真占位仍拦，LAW 10 体例标签不拦——两者在同一份报告里对照。

        `> [分析提示]` 曾是误报来源（`_law10_hint` 的每题固定标签，每份 full
        报告都带），命中它会让门禁对任何报告恒 FAIL。此处同时注入两种文本，
        断言只有真占位被计入。
        """
        report = _write(
            tmp_path, "600176-中国巨石", "2026-09-14-13-41-24.md",
            _AUTOMATED_STOCK_SNAPSHOT.replace(
                "已完成事实到结论的推演。", "[待 Claude report 阶段填充]\n> [分析提示]"
            ),
        )
        report.with_suffix(".analysis.json").write_text(_VALID_ANALYSIS_SIDECAR, encoding="utf-8")
        completion = _completion_layer(qc_file(report, fail_on="error"))
        marker_lines = [d["line"] for d in completion.details
                        if d["id"] == "completion-template-placeholder"]
        assert len(marker_lines) == 1, [d["message"] for d in completion.details]
        flagged = report.read_text(encoding="utf-8").splitlines()[marker_lines[0] - 1]
        assert "[待 Claude report 阶段填充]" in flagged
        assert "分析提示" not in flagged
        assert completion.status == "fail"

    def test_empty_bear_and_left_basis_fail(self, tmp_path: Path):
        report = _write(
            tmp_path, "600176-中国巨石", "2026-09-14-13-41-24.md",
            _AUTOMATED_STOCK_SNAPSHOT.replace(
                "- 需求波动可能压低利润率，需以下季财报验证 [来源: engine]",
                "- 当前数据未形成明确空头逻辑链 [来源: engine]",
            ).replace(
                "- 估值分位较低是条件性支撑 [来源: engine]", ""
            ),
        )
        report.with_suffix(".analysis.json").write_text(_VALID_ANALYSIS_SIDECAR, encoding="utf-8")
        completion = _completion_layer(qc_file(report, fail_on="error"))
        empties = [d for d in completion.details if d["id"] == "completion-empty-basis"]
        assert {"Bear", "左侧"} <= {d["message"].split(" 依据节")[0] for d in empties}
        assert completion.status == "fail"

    def test_empty_or_invalid_sidecar_is_not_completed(self, tmp_path: Path):
        report = _write(tmp_path, "600176-中国巨石", "2026-09-14-13-41-24.md",
                        _AUTOMATED_STOCK_SNAPSHOT)
        report.with_suffix(".analysis.json").write_text("[]\n", encoding="utf-8")
        completion = _completion_layer(qc_file(report, fail_on="error"))
        assert any(d["id"] == "completion-analysis-sidecar-invalid"
                   for d in completion.details)
        assert completion.status == "fail"

    @pytest.mark.parametrize("field,value", [
        ("module", None),
        ("evidence_tag", "未经分级的描述"),
        ("position", "not-an-analysis-position"),
        ("analysis_md", "```python\nx = 1\n```"),
    ])
    def test_sidecar_uses_full_analysis_schema(self, tmp_path: Path, field: str, value):
        report = _write(tmp_path, "600176-中国巨石", "2026-09-14-13-41-24.md",
                        _AUTOMATED_STOCK_SNAPSHOT)
        sidecar = json.loads(_VALID_ANALYSIS_SIDECAR)
        if value is None:
            del sidecar[0][field]
        else:
            sidecar[0][field] = value
        report.with_suffix(".analysis.json").write_text(
            json.dumps(sidecar, ensure_ascii=False), encoding="utf-8")
        completion = _completion_layer(qc_file(report, fail_on="error"))
        assert any(d["id"] == "completion-analysis-sidecar-invalid"
                   for d in completion.details)
        assert completion.status == "fail"

    def test_renderer_unavailable_right_basis_is_not_a_completed_basis(self, tmp_path: Path):
        report = _write(
            tmp_path, "600176-中国巨石", "2026-09-14-13-41-24.md",
            _AUTOMATED_STOCK_SNAPSHOT.replace(
                "- 趋势仍弱，尚需价格结构确认 [来源: engine]",
                "① 右侧参考指标数据不足，证据强度：❓",
            ),
        )
        report.with_suffix(".analysis.json").write_text(_VALID_ANALYSIS_SIDECAR, encoding="utf-8")
        completion = _completion_layer(qc_file(report, fail_on="error"))
        assert any(d["id"] == "completion-empty-basis" and "右侧" in d["message"]
                   for d in completion.details)
        assert completion.status == "fail"

    def test_left_sentinel_with_suffix_is_also_empty_basis(self, tmp_path: Path):
        """左哨兵带「或未达到阈值」尾缀时同样须判为空节（与右侧对称）。

        同一份输入里左侧哨兵零告警、右侧哨兵告警，差异仅来自 6 个字的尾缀——
        渲染器曾靠尾缀让「无实质依据」的节通过 error 级门禁。
        """
        report = _write(
            tmp_path, "600176-中国巨石", "2026-09-14-13-41-24.md",
            _AUTOMATED_STOCK_SNAPSHOT.replace(
                # 替换**左侧**节的内容行（「### 左侧概率的主要支撑依据」之下）
                "- 估值分位较低是条件性支撑 [来源: engine]",
                "① 左侧参考指标数据不足或未达到阈值，证据强度：❓",
            ),
        )
        report.with_suffix(".analysis.json").write_text(_VALID_ANALYSIS_SIDECAR, encoding="utf-8")
        completion = _completion_layer(qc_file(report, fail_on="error"))
        assert any(d["id"] == "completion-empty-basis" and "左侧" in d["message"]
                   for d in completion.details)
        assert completion.status == "fail"

    def test_unavailable_sentinel_with_remaining_basis_is_not_empty(self, tmp_path: Path):
        report = _write(
            tmp_path, "600176-中国巨石", "2026-09-14-13-41-24.md",
            _AUTOMATED_STOCK_SNAPSHOT.replace(
                "- 趋势仍弱，尚需价格结构确认 [来源: engine]",
                "右侧参考指标数据不足，但 MA60 已转正 [来源: engine]",
            ),
        )
        report.with_suffix(".analysis.json").write_text(_VALID_ANALYSIS_SIDECAR, encoding="utf-8")
        completion = _completion_layer(qc_file(report, fail_on="error"))
        assert not any(d["id"] == "completion-empty-basis" and "右侧" in d["message"]
                       for d in completion.details)
        assert completion.status == "pass", completion.details

    def test_manual_stock_and_other_report_types_do_not_require_sidecar(self, tmp_path: Path):
        manual = _write(tmp_path, "600176-中国巨石", "2026-07-16.md", COMPLIANT_STOCK)
        manual_completion = _completion_layer(qc_file(manual, fail_on="error"))
        assert manual_completion.status == "skip"

        etf = _write(tmp_path, "588000-科创50ETF", "2026-09-14.md",
                     COMPLIANT_ETF + "\n[待 Claude report 阶段填充]\n")
        etf_result = qc_file(etf, fail_on="error")
        assert not any(layer.layer == "completion" for layer in etf_result.layers)


# ── derived 层（ETF）──────────────────────────────────────────────────────


class TestEtfDerived:
    def test_all_plausible_pass(self):
        layer = _check_etf_derived(COMPLIANT_ETF)
        assert layer.status == "pass"
        assert layer.findings_count == 0

    def test_implausible_value_warns(self):
        text = COMPLIANT_ETF.replace("-15.36%", "-2500%")
        layer = _check_etf_derived(text)
        assert layer.status == "warn"
        assert any(d["id"] == "derived-nav_vs_ma20_pct" for d in layer.details)

    def test_no_derived_section_skips(self):
        layer = _check_etf_derived("## 产品快照\n无衍生指标\n")
        assert layer.status == "skip"

    def test_field_name_style(self):
        text = "kline.derived.nav_vs_ma20_pct: -15.36 [来源: engine]"
        layer = _check_etf_derived(text)
        assert layer.status == "pass"

    def test_cn_label_style(self):
        text = "| NAV vs MA60 偏离 | **-13.26%** |\n| BOLL 带宽 | **39.21%** |"
        layer = _check_etf_derived(text)
        assert layer.status == "pass"

    def test_drifted_label_variants_still_validate(self):
        # 模板措辞漂移变体仍须提取校验：无"偏离"（515880 式）+ "NAV 距 BOLL 下轨"（588000 式）
        text = ("| NAV vs MA20 | **-15.36%** |\n"
                "| NAV vs MA60 | **-24.35%** |\n"
                "| NAV 距 BOLL 下轨 | **+1.23%** |\n"
                "| NAV 距 BOLL 上轨 | **-27.28%** |")
        layer = _check_etf_derived(text)
        assert layer.status == "pass"
        assert layer.findings_count == 0

    def test_prose_mentions_not_mistaken_for_derived(self):
        # 散文中的指标名词（无表格行上下文）不产生 derived finding（假红防护）
        text = ("[事实] 当前距 BOLL 下轨仅 6.41%，BOLL 带宽 54% 显示极端波动，"
                "日均波动率约 25% 左右。")
        layer = _check_etf_derived(text)
        assert layer.status == "skip"
        assert layer.findings_count == 0

    def test_real_report_shape_table_plus_prose_passes(self):
        # 真实报告形态：表格行 + 同页散文提及，散文不得被误提取
        text = ("| NAV vs MA20 | -16.22% | 净值显著低于20日均线 |\n"
                "[事实] 当前距 BOLL 下轨仅 6.41%，BOLL 带宽 54% 显示极端波动。")
        layer = _check_etf_derived(text)
        assert layer.status == "pass"
        assert layer.findings_count == 0

    def test_drifted_template_warns_unvalidated(self):
        # 未知标签的指标行（present 命中、label-only 不命中）→ 字段未被校验 → warn
        text = "| NAV vs MA5 | -3% |"
        layer = _check_etf_derived(text)
        assert layer.status == "warn"
        assert any(d["id"] == "derived-template-drift" for d in layer.details)

    def test_empty_value_cell_is_not_drift(self):
        # 已知标签行但值缺失（"—"：引擎 derived=None 渲染）→ 合法，不 warn
        text = "| NAV vs MA20 偏离 | — |"
        layer = _check_etf_derived(text)
        assert layer.status == "skip"
        assert layer.findings_count == 0

    def test_cross_cell_number_not_attributed(self):
        # 数值不得跨格归属（"暂无"格 + 第三格数字 → 不提取、不误判）
        text = "| 日均波动率 | 暂无 | 16.381% |"
        layer = _check_etf_derived(text)
        assert layer.status == "skip"
        assert layer.findings_count == 0

    def test_unknown_label_with_valid_rows_warns(self):
        # 未知标签行与有效行并存 → 仍 warn（单行假绿防护）
        text = ("| NAV vs MA20 | -16.22% |\n"
                "| NAV vs MA120 | -5.2% |")
        layer = _check_etf_derived(text)
        assert layer.status == "warn"
        assert any(d["id"] == "derived-template-drift" for d in layer.details)

    def test_info_only_decimals_finding_keeps_pass(self):
        # info 级（未保留两位小数）不翻转层状态（假红防护）
        text = "| 日均波动率 | **16.381%** |"
        layer = _check_etf_derived(text)
        assert layer.status == "pass"
        assert any(d["severity"] == "info" for d in layer.details)

    def test_not_etf_report_skip(self, tmp_path: Path):
        p = _write(tmp_path, "600176-中国巨石", "2026-08-02-10-00-00.md", COMPLIANT_STOCK)
        r = qc_file(p)
        assert not any(l.layer == "derived" for l in r.layers)


# ── derived 层（v0.2.7 E1 板块同步性 6 字段）───────────────────────────────


class TestSectorSyncDerived:
    def test_field_name_style_all_six(self):
        text = "\n".join([
            "sector_beta_60d: 1.23",
            "sector_r2_60d: 0.81",
            "idio_var_share: 0.19",
            "sector_dispersion: 2.35",
            "csad_gamma2: -1.42",
            "downside_corr_gap: 0.08",
        ])
        layer = _check_etf_derived(text)
        assert layer.status == "pass"
        assert layer.findings_count == 0

    def test_cn_label_rows_pass(self):
        text = ("| 板块 Beta(60日) | 1.23 |\n"
                "| 板块 R²(60日) | 0.81 |\n"
                "| 特质方差占比 | 0.19 |\n"
                "| 板块内离散度 | 2.35% |\n"
                "| CSAD γ2 | -1.42 |\n"
                "| 下行相关差 | 0.08 |")
        layer = _check_etf_derived(text)
        assert layer.status == "pass"
        assert layer.findings_count == 0

    def test_r2_out_of_range_warns(self):
        # R² > 1 不可能 → 必须显式登记值域并拦截（不得靠默认域 (-1e9, 1e9) 蒙混）
        text = "sector_r2_60d: 1.5 [来源: 引擎字段名]"
        layer = _check_etf_derived(text)
        assert layer.status == "warn"
        assert any(d["id"] == "derived-sector_r2_60d" for d in layer.details)

    def test_zero_values_valid(self):
        # D1 相关：0.0 是合法值（β 可为 0、特质方差占比可为 0）
        text = "sector_beta_60d: 0.00 [来源: 引擎字段名]"
        layer = _check_etf_derived(text)
        assert layer.status == "pass"

    def test_csad_gamma2_negative_pass(self):
        text = "csad_gamma2: -3.14 [来源: 引擎字段名]"
        layer = _check_etf_derived(text)
        assert layer.status == "pass"

    def test_stock_report_with_sector_fields_checked(self, tmp_path: Path):
        # 验收 #3：stock 报告引用板块同步性字段 → derived lint 识别并校验
        p = _write(tmp_path, "600176-中国巨石", "2026-08-02-10-00-00.md",
                   COMPLIANT_STOCK + "| 板块 Beta(60日) | 1.23 |\n")
        r = qc_file(p)
        derived = next(l for l in r.layers if l.layer == "derived")
        assert derived.status == "pass"

    def test_stock_report_without_sector_fields_skips(self, tmp_path: Path):
        # stock 报告未引用衍生字段 → derived 层不挂载（既有行为不变）
        p = _write(tmp_path, "600176-中国巨石", "2026-08-02-10-00-00.md",
                   COMPLIANT_STOCK)
        r = qc_file(p)
        assert not any(l.layer == "derived" for l in r.layers)

    def test_unknown_sector_label_drift_warns(self):
        # 未登记标签（如 90 日窗口变体）→ present 命中、label 不命中 → drift warn
        text = "| 板块 Beta(90日) | 1.23 |"
        layer = _check_etf_derived(text)
        assert layer.status == "warn"
        assert any(d["id"] == "derived-template-drift" for d in layer.details)

    def test_known_label_empty_value_not_drift(self):
        text = "| 下行相关差 | — |"
        layer = _check_etf_derived(text)
        assert layer.status == "skip"
        assert layer.findings_count == 0


# ── 统一判定 ──────────────────────────────────────────────────────────────


class TestUnifiedVerdict:
    def test_all_pass(self):
        from report_qc import LayerResult

        layers = [
            LayerResult(layer="lint", status="pass"),
            LayerResult(layer="structure", status="pass"),
        ]
        assert _compute_overall(layers) == "PASS"

    def test_warn_when_any_warn(self):
        from report_qc import LayerResult

        layers = [
            LayerResult(layer="lint", status="warn"),
            LayerResult(layer="structure", status="pass"),
        ]
        assert _compute_overall(layers) == "WARN"

    def test_fail_overrides_warn(self):
        from report_qc import LayerResult

        layers = [
            LayerResult(layer="lint", status="fail"),
            LayerResult(layer="structure", status="warn"),
        ]
        assert _compute_overall(layers) == "FAIL"

    def test_all_skip_returns_pass(self):
        from report_qc import LayerResult

        layers = [LayerResult(layer="lint", status="skip")]
        assert _compute_overall(layers) == "PASS"


# ── 输出格式化 ────────────────────────────────────────────────────────────


class TestFormatOutput:
    def test_default_format(self, tmp_path: Path):
        p = _write(tmp_path, "600176-中国巨石", "2026-08-02-10-00-00.md", COMPLIANT_STOCK)
        r = qc_file(p)
        out = format_qc_result(r)
        assert r.overall in out
        assert str(p) in out

    def test_verbose_shows_details(self, tmp_path: Path):
        text = COMPLIANT_STOCK.replace("[证据强度: ✅ 强 🌐 多源 🕐 近 30 日 ✓✓ Tushare+akshare 一致]", "")
        p = _write(tmp_path, "600176-中国巨石", "2026-08-02-10-00-00.md", text)
        r = qc_file(p)
        out = format_qc_result(r, verbose=True)
        assert "structure-evidence" in out

    def test_to_dict_json_serializable(self, tmp_path: Path):
        p = _write(tmp_path, "600176-中国巨石", "2026-08-02-10-00-00.md", COMPLIANT_STOCK)
        r = qc_file(p)
        json.dumps(r.to_dict(), ensure_ascii=False)  # 不应抛异常


# ── 目录批量 / 最新 ───────────────────────────────────────────────────────


class TestQcDirectory:
    def test_multiple_files_aggregated(self, tmp_path: Path):
        _write(tmp_path, "600176-中国巨石", "2026-08-02-10-00-00.md", COMPLIANT_STOCK)
        _write(tmp_path, "588000-科创50ETF", "2026-08-02-10-00-00.md", COMPLIANT_ETF)
        results = qc_directory(tmp_path)
        assert len(results) == 2
        assert {r.report_type for r in results} == {"stock", "etf"}

    def test_empty_dir(self, tmp_path: Path):
        assert qc_directory(tmp_path) == []


class TestQcLatest:
    def test_finds_latest_by_mtime(self, tmp_path: Path):
        older = _write(tmp_path, "600176-中国巨石", "2026-08-01-10-00-00.md", COMPLIANT_STOCK)
        newer = _write(tmp_path, "600176-中国巨石", "2026-08-02-10-00-00.md", COMPLIANT_STOCK)
        # 固定 epoch mtime（差 1h）：touch() 在 CI 粗粒度文件系统下两次可能同秒，
        # max 平局时取 rglob 迭代序首个（CI 曾取到 08-01 导致误报）
        os.utime(older, (1_700_000_000, 1_700_000_000))
        os.utime(newer, (1_700_003_600, 1_700_003_600))
        r = qc_latest(tmp_path)
        assert r is not None
        assert r.report_path.endswith("2026-08-02-10-00-00.md")

    def test_mtime_tie_breaks_by_report_name(self, tmp_path: Path):
        """同 mtime（同秒写入/粗粒度文件系统）时按文件名取新，结果确定。"""
        older = _write(tmp_path, "600176-中国巨石", "2026-08-01-10-00-00.md", COMPLIANT_STOCK)
        newer = _write(tmp_path, "600176-中国巨石", "2026-08-02-10-00-00.md", COMPLIANT_STOCK)
        os.utime(older, (1_700_000_000, 1_700_000_000))
        os.utime(newer, (1_700_000_000, 1_700_000_000))
        r = qc_latest(tmp_path)
        assert r is not None
        assert r.report_path.endswith("2026-08-02-10-00-00.md")

    def test_missing_dir(self, tmp_path: Path):
        assert qc_latest(tmp_path / "nope") is None


# ── 920xxx 北交所股票（F11）──────────────────────────────────────────────


class TestSourcingLayer:
    """F4 §N 交叉引用校验的豁免边界（R1 审查 F4 收窄）。"""

    def test_generic_citation_verbs_not_exempt(self):
        """通用引用动词（说明/参见/详见/遵循）不得豁免 §N 校验。

        回归：豁免正则含这些通用动词 → 「详见 §5」被当作**外部规范**引用放行，
        F4 恰好在最惯用措辞上失明——报告可用最自然的写法引用不存在的章节仍 PASS。
        """
        for text in ("详见 §5。", "参见 §3.2 的对照。", "遵循 §7 规范。", "说明 §9。"):
            assert _check_sourcing(text).findings_count == 1, f"未拦截: {text!r}"

    def test_external_spec_reference_still_exempt(self):
        """指向外部规范的 §N 仍须豁免（repo 内误报均为该形态），且不引入本文误报。"""
        for text in ("见 report-conventions.md §2.3。", "见共享规范 §2.3。", "见附件 §4。"):
            assert _check_sourcing(text).findings_count == 0, f"误报: {text!r}"
        # 本文存在对应标题节 → 不报
        assert _check_sourcing("## 3 数据\n\n详见 §3。\n").findings_count == 0


class TestBseStockClassification:
    def test_920_prefix_classified_as_stock(self, tmp_path: Path):
        p = _write(tmp_path, "920001-北交所公司", "2026-08-02-10-00-00.md", "# x\n")
        assert detect_report_type(p) == "stock"

    def test_159_etf_still_etf(self, tmp_path: Path):
        p = _write(tmp_path, "159206-卫星ETF", "2026-08-02-10-00-00.md", "# x\n")
        assert detect_report_type(p) == "etf"


# ── verify-data 层异常不静默（F10）──────────────────────────────────────


class TestVerifyLayersFailOnException:
    """F10: quality/rigor 各自 try，异常 → fail 而非 skip（防假 PASS）。"""

    @staticmethod
    def _fake_load(failing: str):
        class FakeCollector:
            def collect_all(self, *a, **k):
                if failing == "collect":
                    raise RuntimeError("collect boom")
                return {"dimensions": []}

        class FakeQC:
            def run_quality_check(self, result):
                if failing == "quality":
                    raise RuntimeError("quality boom")
                return {"summary": {"overall": "pass"}, "metrics": []}

        class FakeRigor:
            def run_rigor(self, result):
                if failing == "rigor":
                    raise RuntimeError("rigor boom")
                return []

        def load(name):
            return {"collector": FakeCollector(),
                    "financial_rigor": FakeRigor(),
                    "quality_check": FakeQC()}[name]

        return load

    def test_rigor_exception_fails_not_skip(self, tmp_path: Path, monkeypatch):
        p = _write(tmp_path, "600176-中国巨石", "2026-08-02-10-00-00.md", COMPLIANT_STOCK)
        monkeypatch.setattr("report_qc._load_stock_module", self._fake_load("rigor"))
        layers = _run_verify_layers(p, "stock")
        by_layer = {l.layer: l for l in layers}
        assert by_layer["quality"].status == "pass"
        assert by_layer["rigor"].status == "fail"
        assert _compute_overall(layers) == "FAIL"

    def test_collect_failure_fails_both_layers(self, tmp_path: Path, monkeypatch):
        p = _write(tmp_path, "600176-中国巨石", "2026-08-02-10-00-00.md", COMPLIANT_STOCK)
        monkeypatch.setattr("report_qc._load_stock_module", self._fake_load("collect"))
        layers = _run_verify_layers(p, "stock")
        by_layer = {l.layer: l for l in layers}
        assert by_layer["quality"].status == "fail"
        assert by_layer["rigor"].status == "fail"
        assert _compute_overall(layers) == "FAIL"

    def test_quality_exception_does_not_suppress_rigor(self, tmp_path: Path, monkeypatch):
        p = _write(tmp_path, "600176-中国巨石", "2026-08-02-10-00-00.md", COMPLIANT_STOCK)
        monkeypatch.setattr("report_qc._load_stock_module", self._fake_load("quality"))
        layers = _run_verify_layers(p, "stock")
        by_layer = {l.layer: l for l in layers}
        assert by_layer["quality"].status == "fail"
        assert by_layer["rigor"].status == "pass"  # rigor 仍运行
        assert _compute_overall(layers) == "FAIL"


class TestReviewArtifactType:
    """复盘纪要是**独立产物类型**（R2/T8-3），不套用研报结构检查。

    实测踩过：纪要落在 `reports/515050-通信ETF/` 下被识别成 `etf` →
    structure 稳定产出 4 条误报（[事实]/[分析]/[证据强度]/风险声明）——
    而该纪要**按设计就不含**前三者（它明确不做推演，只对照假设状态）。
    """

    def test_review_md_detected_as_review(self, tmp_path):
        p = tmp_path / "reports" / "515050-通信ETF" / "20260911-review.md"
        p.parent.mkdir(parents=True)
        p.write_text("# 🔍 复盘纪要\n\n> 不构成投资建议。\n", encoding="utf-8")
        assert detect_report_type(p) == "review", "纪要须先于目录/代码前缀判定"

    def test_review_structure_only_requires_risk_statement(self):
        from report_qc import _check_structure

        text = "# 🔍 复盘纪要 — 515050\n\n> 研究工具，非决策工具，不构成投资建议。\n"
        assert _check_structure(text, "review").status == "pass"

    def test_review_without_risk_statement_warns(self):
        from report_qc import _check_structure

        layer = _check_structure("# 复盘纪要\n\n没有声明\n", "review")
        assert layer.findings_count == 1
        assert layer.details[0]["id"] == "structure-risk-statement"


class TestQcLatestSkipsReviewMemo:
    """`--latest` 不得选中复盘纪要（R0~R2 review 修复）。

    回归：`qc_latest` 只过滤 `.audit_checklist`，而 `etf.py review` 把纪要写进
    **同一报告目录**且 mtime 最新 → 闸门在错的文档上给 PASS，最新真报告的 4 项
    结构检查（[事实]/[分析]/[证据强度]/风险声明）不再执行。
    """

    @staticmethod
    def _tree(tmp_path: Path) -> tuple[Path, Path]:
        d = tmp_path / "reports" / "515050-通信ETF"
        d.mkdir(parents=True)
        report = d / "2026-09-10-22-50-00.md"
        report.write_text(COMPLIANT_ETF, encoding="utf-8")
        memo = d / "20260911-review.md"
        memo.write_text("# 🔍 复盘纪要 — 515050\n\n> 不构成投资建议。\n", encoding="utf-8")
        import os

        os.utime(report, (1_600_000_000, 1_600_000_000))
        os.utime(memo, (1_700_000_000, 1_700_000_000))     # 纪要更新
        return report, memo

    def test_latest_picks_real_report_not_memo(self, tmp_path: Path):
        self._tree(tmp_path)
        got = qc_latest(tmp_path / "reports")
        assert got is not None
        assert got.report_path.endswith("2026-09-10-22-50-00.md"), \
            f"--latest 选中了复盘纪要: {got.report_path}"
        assert got.report_type == "etf"

    def test_report_style_timestamp_named_review_is_not_relaxed(self, tmp_path: Path):
        """`-review.md` 规则不得**内容无关**：用户把真报告存成 `2026-09-10-review.md`
        （报告风格时间戳，非本工具的 `YYYYMMDD-review.md`）时应仍按研报校验。"""
        p = tmp_path / "reports" / "515050-通信ETF" / "2026-09-10-review.md"
        p.parent.mkdir(parents=True)
        p.write_text(COMPLIANT_ETF, encoding="utf-8")
        assert detect_report_type(p) == "etf", "报告风格时间戳被误判为复盘纪要"


# ── CLI 默认 profile：第 0 层门禁必须覆盖 LAW 6 ──────────────────────────────
#
# CLAUDE.md 第 0 层「机器准出（必跑）」就是 `report_qc.py <报告> --fail-on error`
# 这条不带 --profile 的命令，因此 **CLI 默认值就是合规门禁本身**。
# 历史默认 precommit 是对齐旧 check_report.sh 的阻断项，会跳过全部 law6-* 与
# known-violation*（实测 73 条规则中 35 条被跳过，含 14 条 error 级）；v0.3.0 把
# 模型撰写的 analysis 正文放进报告首屏后，这条命令便再也拦不住 LAW 6 违规。

class TestCliDefaultProfileCoversLaw6:
    def _report_with_law6_violation(self, tmp_path: Path) -> Path:
        # 用干净合规样例只注入 LAW 6 违规：避免 completion 等其它层先 FAIL，
        # 掩盖「默认 profile 是否拦得住 law6」这一被测结论。
        text = COMPLIANT_STOCK.replace("不构成投资建议",
                                       "建议买入并加仓，目标价 25.0 元")
        return _write(tmp_path, "600176-中国巨石", "2026-08-02-10-00-00.md", text)

    def test_cli_default_catches_law6_violation(self, tmp_path: Path):
        """断言**行为**而非 profile 字符串：默认调用必须 FAIL。"""
        report = self._report_with_law6_violation(tmp_path)
        rc = main([str(report), "--fail-on", "error"])
        assert rc == 2, "默认 profile 未拦截 LAW 6 违规 → 第 0 层门禁失效"

    def test_explicit_precommit_profile_stays_lax(self, tmp_path: Path):
        """显式 precommit 仍是宽松档：.pre-commit-config.yaml 依赖该语义。"""
        report = self._report_with_law6_violation(tmp_path)
        rc = main([str(report), "--fail-on", "error", "--profile", "precommit"])
        assert rc != 2, "precommit 档不应拦截（提交期性能取舍，由 hook 显式声明）"
