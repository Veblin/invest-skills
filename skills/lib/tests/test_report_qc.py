"""Tests for skills/lib/report_qc.py — offline QC checks, no network."""

from __future__ import annotations

import json
import sys
from pathlib import Path

_SKILLS_LIB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SKILLS_LIB))  # 无条件插 0：防其他 skill 目录先行入 path 遮蔽同名模块

from report_qc import (  # noqa: E402
    QCResult,
    detect_report_type,
    format_qc_result,
    qc_directory,
    qc_file,
    qc_latest,
    _check_etf_derived,
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
        # 显式设置 mtime 保证顺序（macOS tmp 可能同秒）
        older.touch(); newer.touch()
        r = qc_latest(tmp_path)
        assert r is not None
        assert r.report_path.endswith("2026-08-02-10-00-00.md")

    def test_missing_dir(self, tmp_path: Path):
        assert qc_latest(tmp_path / "nope") is None


# ── 920xxx 北交所股票（F11）──────────────────────────────────────────────


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
