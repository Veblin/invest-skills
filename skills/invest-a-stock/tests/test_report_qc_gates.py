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
    _check_stock_completion,
    conclusion_evidence_findings,
    event_analysis_evidence_findings,
    fact_analysis_pair_findings,
    readability_findings,
    readability_metrics,
)


def _good_report() -> str:
    return """## 主要结论
- 营收连续增长（数据：近4年 +12.3%/年 [来源: engine financials]），毛利率 42% 维持（逻辑：规模效应传导 [来源: Python calc: revenue_cagr]），分歧点在于海外占比上升的汇率敏感性，风险点在于资本开支 3 年翻倍。
[证据强度: ✅ 强 🌐 多源 🕐 近 30 日 ✓✓ 跨源一致]
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


class TestConclusionEvidenceR12:
    """R12 round-4：证据强度标签只是元数据、不构成证据——`_evidence_ge_c`
    不再接受任意 `[证据强度: ✅` 行；标签命名空间（含非法/嵌套形态）内的
    来源/事实字样也不作数。负控 = Codex 复检形态及变体，正控 = 合法纯标签
    与真正绑定事实的断言。"""

    @pytest.mark.parametrize("line", [
        "[证据强度: ✅ 公司盈利增长999%]",                       # 原恶意标签
        "[证据强度: ✅ 强 🌐多源 🕐近30日 公司盈利增长999%]",       # 标签内偷塞
        "[证据强度: ✅ 强 🌐多源 🕐近30日 ✓✗] 公司盈利增长999%",    # 尾接同断言
        "[证据强度：✅强🌐多源🕐近30日✓✗]公司盈利增长999%",         # 中英文标点/空白变体
        "[证据强度: 🌐多源 🕐近30日 ✓✗ 公司盈利增长999%]",         # 缺维度
        "[证据强度: ✅ 强 ✅ 强 公司盈利增长999%]",                # 重复维度
        "[证据强度: ✅ 强 [来源: 假] 公司盈利增长999%]",            # 伪造来源包入标签
        "[证据强度: ✅ 强 来源: 公司公告显示盈利增长999%]",          # 标签内来源文字
    ])
    def test_strength_tag_cannot_certify_assertion(self, line):
        md = "## 主要结论\n" + line + "\n"
        findings = conclusion_evidence_findings(md, fact_ids=frozenset({"F1"}))
        assert any(f["id"] == "wording-conclusion-evidence-level"
                   and f["severity"] == "error" for f in findings), (line, findings)

    def test_plain_assertion_still_flagged(self):
        md = "## 主要结论\n公司盈利增长999%\n"
        assert any(f["id"] == "wording-conclusion-evidence"
                   for f in conclusion_evidence_findings(md))

    def test_legal_pure_tags_exempt(self):
        """正控：实际交付件使用的合法纯四维标签行（含 R10 新中性档）不报。"""
        md = ("## 主要结论\n"
              "[证据强度: ⚠️ 中 🌐多源 📅近季度 ✓✗]\n"
              "[证据强度: ⚠️ 中 📡单源 🗄️滞后 > 1 年 —]\n"
              "[证据强度: ⚠️ 中 🌐多源 📅报告期已注明 ✓✗]\n")
        assert conclusion_evidence_findings(md, fact_ids=frozenset({"F1"})) == []

    def test_bound_fact_and_real_source_pass(self):
        """正控：标签外真正绑定事实 / 真实来源标注的断言照常 ≥C。"""
        md = ("## 主要结论\n"
              "盈利增长 999%[事实: F1]\n"
              "营收增长 42%[来源: engine financials]\n"
              "[证据强度: ✅ 强 🌐多源 🕐近30日 ✓✗] 判断来自同段上游 [来源: engine]\n")
        assert conclusion_evidence_findings(md, fact_ids=frozenset({"F1"})) == []

    def test_fake_fact_id_not_evidence(self):
        md = "## 主要结论\n公司盈利增长999%[事实: F999999]\n"
        assert any(f["id"] == "wording-conclusion-evidence-level"
                   for f in conclusion_evidence_findings(md, fact_ids=frozenset({"F1"})))


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
        """表行/引用行不算断言（FP 源）；带 B 级证据断言通过。

        R14 round-8：表行正控改用行内绑定形态——混合行（含文字格）不再按
        长度豁免（Codex table_numeric_mixed）。"""
        md = ("## 主要结论\n"
              "| 指标 | 值 |\n|---|---|\n| 营收 | 382 亿[来源: engine] |\n"
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


# ── R2（#36）：渲染器自出标签行与标题行后缀的误报豁免（2026-10-01 核查记录，10-02 修复）──

class TestRendererLabelExemption:
    """渲染器按各宿主体例及字段是否非空输出 `**[事实]**`/`**[分析]**`/`**证据等级：** {标签}`
    （`_render_analysis_overview`、`render_risk` 等），`## 结论…` 标题的后缀文字
    也不属段内正文。两者此前都被 R-A2 当缺标签断言误报（以「结论」开头的卡
    含三行渲染标签且未被子标题截断时会报 3-4 条 error）。本组锁：误报清零 + 正文证据门不放松。"""

    @staticmethod
    def _card(title: str) -> str:
        """按 `lib/render_markdown/_concise.py::_render_analysis_overview` 的
        实际输出体例构造（标签行独占一行，正文均带合规标签）。"""
        return (
            f"## {title}\n\n"
            "**[事实]**\n\n"
            "今日收盘价 12.34 元，PE(TTM) 18.2 倍。[来源: kline 2026-10-02]\n\n"
            "**[分析]**\n\n"
            "估值处于近五年 45% 分位，低于行业中位。[证据: B]\n\n"
            "**证据等级：** B\n"
        )

    def test_suffixed_conclusion_titles_pass(self):
        """带后缀标题（「：」与无「：」两形态）：修复前 3-4 条 error → 0。"""
        for title in ("结论", "结论：估值处于历史中位", "结论与交易结构"):
            assert conclusion_evidence_findings(self._card(title)) == [], title

    def test_evidence_label_legal_values_exempt(self):
        """证据等级行合法结构（R12 round-5）：等级（A-D / L1-L4，同
        analysis_schema `_EVIDENCE_RE`）+ 可选**四维齐全序列**（强度→来源→
        时效→交叉、图标-注解配对；词表唯一来源 evidence_tags）。"""
        for ev in ("B", "L1",
                   "B ✅ 强 🌐 多源 🕐 近30日 ✓✓ 跨源一致",
                   "C ⚠️ 中 📡 单源 🕐 近 30 日 —",
                   "B ✅强 🌐多源 📅报告期已注明 ✓✗",
                   "B ❓弱 🔮推测 🗄️滞后 > 1 年 —"):
            md = self._card("结论：估值处于历史中位").replace(
                "**证据等级：** B\n", f"**证据等级：** {ev}\n")
            assert conclusion_evidence_findings(md) == [], ev

    def test_evidence_label_partial_or_mismatched_tail_not_exempt(self):
        """半截序列 / 图标注解矛盾 / 词表外文字 / 单维简式 → **不豁免**
        （R12 round-5 收紧；round-4 及更早这些形态曾被当结构行放过）。"""
        for ev in ("C ⚠️中 📡单源 🔮框架估计",
                   "B ✅强 📡多源 📅最新期 ✓✓",
                   "B 🕐 近 30 日",
                   "B 🗄️ 滞后 >1 年",
                   "B ✅ 强",
                   "B ✅该标的有望走强"):
            md = self._card("结论：估值处于历史中位").replace(
                "**证据等级：** B\n", f"**证据等级：** {ev}\n")
            assert any(x["id"] == "wording-conclusion-evidence"
                       for x in conclusion_evidence_findings(md)), ev

    def test_missing_source_body_still_fails(self):
        """正文缺来源 → 仍报 error；行号须落在正文行（标题行不计入）。"""
        md = self._card("结论：估值处于历史中位").replace(
            "今日收盘价 12.34 元，PE(TTM) 18.2 倍。[来源: kline 2026-10-02]",
            "今日收盘价 12.34 元，PE(TTM) 18.2 倍。")
        f = conclusion_evidence_findings(md)
        assert [x["id"] for x in f] == ["wording-conclusion-evidence"]
        assert f[0]["line"] == md.splitlines().index(
            "今日收盘价 12.34 元，PE(TTM) 18.2 倍。") + 1

    def test_d_grade_body_still_fails(self):
        md = self._card("结论：估值处于历史中位").replace("[证据: B]", "[证据: D 推测]")
        assert any(x["id"] == "wording-conclusion-evidence-level"
                   for x in conclusion_evidence_findings(md))

    def test_label_with_appended_prose_not_exempt(self):
        """标签行后跟实际正文 → 不豁免；标记后紧跟词表外文字同样不豁免
        （2026-10-02 复审反例：`B ✅该标的有望走强` / `B —该标的有望走强`）。"""
        base = self._card("结论：估值处于历史中位")
        variants = [
            base.replace("**[分析]**\n", "**[分析]** 该标的有望走强。\n"),
            base.replace("**证据等级：** B\n", "**证据等级：** B 该标的有望走强\n"),
            base.replace("**证据等级：** B\n", "**证据等级：** B ✅该标的有望走强\n"),
            base.replace("**证据等级：** B\n", "**证据等级：** B —该标的有望走强\n"),
        ]
        for md in variants:
            assert any(x["id"] == "wording-conclusion-evidence"
                       for x in conclusion_evidence_findings(md)), md


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


class TestV031MainJudgmentGate:
    """C1-c（2026-10-04）：主判断/结论段标题族 + 全段扫描 + 无对象不 PASS。

    反例：600519 `2026-10-02-22-32-48.final.md` 主判断标题为 `## 主判断：…`，
    旧 regex 只认「(主要|核心)?结论」开头 → 整段未被扫描、层静默 pass（D04
    「主判断未被 QC 定位」）。修复后跑该报告：层由 pass(0) 变为 fail，
    且 finditer 只命中真正缺证据引用的一行（L44 判断句）。
    """

    _MAIN_JUDGMENT_MD = """## 重要发现（5 分钟阅读区）

> 结论先行区：以下 5 段是本次分析的核心判断，数据底稿与其余分析注记见文末。

## 主判断：PE 19.32x 处 4.1% 分位

**[事实]**

**行情（最新收盘 2026-09-30）**
- 现价 1258.62 元[事实: F1]（+1.86%[事实: F2]）

**[分析]**

**判断：低分位是读数，不是保护——分子在缩。**

- 逻辑链：PE 19.32x[事实: F3] → 归母净利同比 -1.95%[事实: F12]（数据）。

**证据强度：✅ 强**🌐多源🕐近 30 日✓✗（估值/盈利序列为封存字段直读）
**证据等级：** A
"""

    def test_real_new_style_section_located_and_single_missing_line_flagged(self):
        findings = conclusion_evidence_findings(self._MAIN_JUDGMENT_MD)
        flagged = [f for f in findings if f["id"] == "wording-conclusion-evidence"]
        assert len(flagged) == 1, [f.get("context") for f in flagged]
        assert "判断：低分位" in flagged[0]["context"]

    def test_bare_assertion_under_main_judgment_fails(self):
        md = "## 主判断：该标的有望走强\n\n估值便宜，值得研究。\n"
        findings = conclusion_evidence_findings(md)
        assert any(
            f["id"] == "wording-conclusion-evidence" and f["severity"] == "error"
            for f in findings
        )

    def test_insight_available_conclusion_scanned(self):
        ok = "## 可得结论\n\n- 结论 [来源: engine facts]。\n"
        assert not [
            f for f in conclusion_evidence_findings(ok)
            if f["id"] == "wording-conclusion-evidence"
        ]
        bad = "## 可得结论\n\n- 该标的有望走强。\n"
        assert [
            f for f in conclusion_evidence_findings(bad)
            if f["id"] == "wording-conclusion-evidence"
        ]

    def test_all_matched_sections_scanned(self):
        md = "## 主判断：A\n\n裸断言一。\n\n## 主要结论\n\n裸断言二。\n"
        flagged = [
            f for f in conclusion_evidence_findings(md)
            if f["id"] == "wording-conclusion-evidence"
        ]
        assert len(flagged) == 2

    def test_missing_object_warns_for_stock_not_others(self):
        md = "# 600519 贵州茅台 研究快照\n\n只有数据，没有任何结论段。\n"
        layer = _check_conclusion_evidence(md, "stock")
        assert layer.status == "warn"
        assert any(
            f["id"] == "wording-conclusion-section-missing" for f in layer.details
        )
        other = _check_conclusion_evidence(md, "etf")
        assert not any(
            f["id"] == "wording-conclusion-section-missing" for f in other.details
        )

    def test_completion_title_drift_is_error(self, tmp_path):
        text = "本报告由自动化引擎生成\n# 600519 贵州茅台 评测稿\n"
        layer = _check_stock_completion(tmp_path / "x.md", text)
        assert any(
            f["id"] == "completion-snapshot-title-unrecognized"
            and f["severity"] == "error"
            for f in layer.details
        )
        assert layer.status == "fail"


class TestV031R2StructuralExemption:
    """R2（2026-10-04 独立复检）：结构行豁免必须封闭——同一无来源断言不得
    因裸写/加粗/证据强度尾接而改变验收结果；合法结构正例不误报。

    反例来源：Codex 复检对真实成品结论容器插入同一断言：
    `公司盈利持续改善` FAIL / `**公司盈利持续改善**` PASS /
    `**证据强度：✅ 强**公司盈利持续改善` PASS（旧实现，已修复）。
    """

    _CONTAINER = "## 主判断：测试\n\n"

    @pytest.mark.parametrize("assertion", [
        "公司盈利持续改善",
        "**公司盈利持续改善**",
        "**证据强度：✅ 强**公司盈利持续改善",
    ])
    def test_unsourced_assertion_fails_in_all_three_forms(self, assertion):
        findings = conclusion_evidence_findings(self._CONTAINER + assertion + "\n")
        assert any(f["id"] == "wording-conclusion-evidence"
                   and f["severity"] == "error" for f in findings), assertion

    @pytest.mark.parametrize("assertion", [
        "**证据强度：✅ 强，公司盈利持续改善**",   # 标签内容被掺入断言
        "**证据强度： 公司盈利持续改善**",          # 标签不合封闭语法
        "**公司盈利持续改善（机构预测）**",          # 括注无元数据信号
        "**公司盈利持续改善：**",                    # 冒号加尾式绕过
    ])
    def test_structural_lookalikes_do_not_exempt(self, assertion):
        findings = conclusion_evidence_findings(self._CONTAINER + assertion + "\n")
        assert any(f["id"] == "wording-conclusion-evidence" for f in findings), assertion

    @pytest.mark.parametrize("line", [
        # 证据强度行（实测语料）：四维令牌 + 元数据括注 / 双档并列
        "**证据强度：✅ 强**",
        "**证据强度：✅ 强**🌐多源🕐近 30 日✓✗（估值/盈利序列为封存字段直读）",
        "**证据强度：⚠️ 中**（价格/估值/盈利均可复核；机制归因为解释，非观测）",
        "**证据强度：✅ 强**（估值、盈利、历史序列均为封存字段直读，可逐项复算）"
        "／⚠️ 中（ROE 数值受跨源差异影响）",
        # 加粗小标题（实测语料）：主体+元数据括注 / 冒号表头 / 封闭清单
        "**盈利（中报累计口径，报告期 2026-06-30）**",
        "**本次问题与数据截止（2026-09-30 收盘；2026-10-02 采集，固定快照回放）**",
        "**截至 2026-09-24 收盘的并列量值：**",
        "**边界（如实披露）：**",
        "**关键观察节点**",
        "**盲点**",
        "**逻辑链**",
        "**明示缺口**",
    ])
    def test_corpus_structural_lines_still_exempt(self, line):
        assert conclusion_evidence_findings(self._CONTAINER + line + "\n") == [], line

    def test_missing_conclusion_heading_warns_even_with_fact_ref(self):
        """无结论标题时不得因其他位置的 [事实: F…] 而静默 pass(0)。"""
        layer = _check_conclusion_evidence(
            "## 研究摘要\n公司盈利持续改善[事实: F1]\n", "stock")
        assert layer.status == "warn"
        assert any(f["id"] == "wording-conclusion-section-missing"
                   for f in layer.details)

    def test_style_mismatch_warns_against_profile(self, tmp_path):
        """C2-c：正文自评风格 ≠ 同代 profile.style → warning（反例 600519 L329）。"""
        text = (
            "本报告由自动化引擎生成\n# 600519 贵州茅台 研究快照\n"
            "**[风格匹配]** 中性：自评风格 成长 × 收益驱动 暂无法判定\n"
        )
        md = tmp_path / "a.final.md"
        (tmp_path / "a.final.profile.json").write_text(
            '{"profile": {"style": "价值"}}', encoding="utf-8")
        layer = _check_stock_completion(md, text)
        assert any(
            f["id"] == "completion-style-mismatch" and f["severity"] == "warning"
            for f in layer.details
        )
        # 一致 → 无该 finding（侧车嵌套结构）
        md2 = tmp_path / "b.final.md"
        (tmp_path / "b.final.profile.json").write_text(
            '{"profile": {"style": "成长"}}', encoding="utf-8")
        layer2 = _check_stock_completion(md2, text)
        assert not any(f["id"] == "completion-style-mismatch" for f in layer2.details)


class TestV031R14HierarchyTablesEvents:
    """R14（2026-10-05 第三批 round-6）：按层级全子段扫描 + 表格断言检查 +
    事件分析段扫描。反例来源：pre-review/review-probes.json 四份真实变异
    （H3/表包装无来源断言均 PASS）与 round-5 银行 final 事件段（R13）。"""

    @pytest.mark.parametrize("md", [
        # ① H3 子段注入（原实现遇 H3 停止 → PASS）
        "## 重要发现（5 分钟阅读区）\n\n### 竞争解释\n\n公司盈利持续改善\n",
        # 同级更深：H4 子段（核心判断摘要各卡片）
        "### 核心判断摘要\n\n#### 盈利结构\n\n[结论] 公司盈利持续改善\n",
    ])
    def test_subsection_assertion_flagged(self, md):
        f = conclusion_evidence_findings(md)
        assert any(x["id"] == "wording-conclusion-evidence"
                   and x["severity"] == "error" for x in f), md

    @pytest.mark.parametrize("row", [
        "| 公司盈利持续改善 |",       # 非数字无来源断言
        "| 公司盈利增长999% |",       # 数字断言
        "| 判断 | 公司盈利增长999% |",
        # R14 round-7（Codex table_placeholder）：短断言 + 占位符不得按数据行放行
        "| 公司盈利持续改善 | — |",
        # R14 round-7（Codex table_fake_source）：强度标签内嵌假来源不得按数据/证据放行
        "| 公司盈利持续改善[证据强度: ✅ 强 [来源: fake]] |",
    ])
    def test_table_wrapped_assertion_flagged(self, row):
        md = ("## 主判断：测试\n\n"
              "| 判断 |\n|---|\n" + row + "\n")
        f = conclusion_evidence_findings(md)
        assert any(x["id"].startswith("wording-conclusion-table-evidence")
                   and x["severity"] == "error" for x in f), row

    def test_label_plus_number_row_requires_binding(self):
        """R14 round-8（Codex table_numeric_mixed）：`| 断言 | 999 |` 混排行
        曾被 round-7「短标签+数值」分支按数据行放行——preflight 仅因 999 未绑定
        拦截（validate 面），final QC 仍 WARN。现混合行只认行内有效证据绑定；
        同一形态加绑定即通过（正控）。"""
        flagged = ("## 重要发现（5 分钟阅读区）\n\n"
                   "| 判断 | 值 |\n|---|---|\n| 公司盈利持续改善 | 999 |\n")
        f = conclusion_evidence_findings(flagged)
        assert any(x["id"] == "wording-conclusion-table-evidence"
                   and x["severity"] == "error" for x in f)
        bound = ("## 重要发现（5 分钟阅读区）\n\n"
                 "| 指标 | 值 |\n|---|---|\n| 营收 | 382 亿[事实: F1] |\n")
        assert conclusion_evidence_findings(
            bound, fact_ids=frozenset({"F1"})) == []

    def test_table_claim_above_source_line_not_exempt(self):
        """R14 round-7：邻近（非行内）来源字符串不得给整表免审（反例：把断言行
        写在事件来源行上方即被吞掉）。"""
        md = ("## 重要发现（5 分钟阅读区）\n\n"
              "| 判断 |\n|---|\n| 公司盈利持续改善 |\n\n"
              "[来源: akshare stock_individual_notice_report / 5 条事件]\n")
        f = conclusion_evidence_findings(md)
        assert any(x["id"] == "wording-conclusion-table-evidence"
                   and x["severity"] == "error" for x in f)

    def test_real_data_table_not_flagged(self):
        """正控：真数据表不误拦——**全数据格**数据行（渲染器指标表形态）。

        R14 round-8：原「短标签+数值/日期」正控随判据收紧撤下（Codex
        table_numeric_mixed：短标签+数字无法与短断言区分）。真实渲染器数据表
        为全数值行（如业绩全景 `| 20240930 | 26.833 | 48.42 | 1207.76亿 |`），
        照常放行；带行内绑定的标签行走 `test_label_plus_number_row_requires_binding`
        正控。"""
        md = ("## 主要结论\n"
              "| 报告期 | ROE(%) | EPS | 营收 |\n|---|---|---|---|\n"
              "| 20240930 | 26.833 | 48.42 | 1207.76亿 |\n"
              "| 20250630 | 19.2486 | 36.18 | 893.89亿 |\n")
        assert conclusion_evidence_findings(md) == []

    def test_in_row_fact_bound_scenario_not_flagged(self):
        """正控：行内绑定 [事实: F…] 的情景表照常放行（不依赖表级/邻近来源）。"""
        md = ("## 主要结论\n"
              "| 情景 | 读数 |\n|---|---|\n"
              "| 中性 | 1145–1851 元[事实: F1] |\n\n"
              "[来源: Python calc: scenario_table]\n")
        assert conclusion_evidence_findings(md, fact_ids=frozenset({"F1"})) == []

    def test_event_table_renderer_structure_exempt(self):
        """正控：渲染器事件表（逐字表头）数据行按结构核验放行——含长封存标题、
        原始英文键（未映射回落）与 >15 条的截断行。

        R14 round-8：整块豁免已撤（Codex event_copied_header：复制表头注入
        「确定影响」断言曾被整块放行），改为逐行结构核验。"""
        md = ("## 3a. 事件时间线\n\n"
              "| 日期 | 类型 | 公告标题 | 涉及维度（类型默认） |\n"
              "|------|------|---------|---------|\n"
              "| 2026-10-01 | 程序性公告 | 招商银行:招商银行股份有限公司关于非执行董事离任的公告 | 治理 |\n"
              "| 2026-09-30 | unknown_legacy | 关于某事项的提示性公告 | 估值 |\n"
              "| ... | ... | （另有 3 条事件未展示） | ... | ... |\n\n"
              "[来源: akshare stock_individual_notice_report / 5 条事件]\n")
        assert event_analysis_evidence_findings(md) == []

    def test_copied_event_header_rows_checked(self):
        """R14 round-8（Codex event_copied_header）：复制事件表头并在类型/维度
        格写「确定影响」断言的构造不构成数据行（validate/preflight=0 时
        final QC 必须拦）。"""
        body = ("| 日期 | 类型 | 公告标题 | 涉及维度（类型默认） |\n"
                "|---|---|---|---|\n"
                "| 2026-10-01 | 治理 | 公司盈利持续改善 | 治理变动不影响盈利与估值 |\n")
        f = event_analysis_evidence_findings("## 3a. 事件时间线\n\n" + body)
        assert any(x["id"] == "wording-event-analysis-table-evidence"
                   and x["severity"] == "error" for x in f)
        # 行内有效来源绑定照常放行（不因表种一律拒收）
        ok = ("## 3a. 事件时间线\n\n"
              "| 日期 | 类型 | 公告标题 | 涉及维度（类型默认） |\n"
              "|---|---|---|---|\n"
              "| 2026-10-01 | 程序性公告 | 关于董事离任的公告[来源: 封存 events 字段] | 治理 |\n")
        assert event_analysis_evidence_findings(ok) == []

    def test_event_analysis_assertion_flagged(self):
        """R13 连线：事件段无证据断言（含「不改变盈利路径」式影响判断）须拦。"""
        md = ("## 3a. 事件时间线\n\n"
              "**[分析]**\n\n"
              "治理变动本身不改变盈利路径与估值锚点\n")
        f = event_analysis_evidence_findings(md)
        assert any(x["id"] == "wording-event-analysis-evidence"
                   and x["severity"] == "error" for x in f)

    @pytest.mark.parametrize("body", [
        # Codex batch3 探针 event_bare / event_unverified
        "治理变动本身不改变盈利路径与估值锚点。",
        "公告原文未逐条读取；治理变动不影响盈利与估值。",
        # Codex batch3 探针 event_title_source：标题来源不是原文已核证据
        "仅复核公告标题；治理变动不影响盈利与估值。[来源: akshare 公告标题]",
        # 同族变体：把「原文未读取」写进标签仍不构成依据
        "公告原文未逐条读取；治理变动不影响盈利与估值。[来源: akshare 公告标题；公告原文未读取]",
        # 表格包装 + 标题来源
        "| 判断 | 治理变动不影响盈利与估值。[来源: akshare 公告标题] |",
        # R13 round-7（Codex event_absent_source）：自述未读的来源标签
        "治理变动不影响盈利与估值。[来源: 公告原文未读取]",
        "治理变动不影响盈利与估值。[来源: 公告原文未获取]",
    ])
    def test_event_title_or_no_source_assertion_flagged(self, body):
        md = f"## 3a. 事件时间线\n\n**[分析]**\n\n{body}\n"
        f = event_analysis_evidence_findings(md)
        assert any(x["severity"] == "error" for x in f), body

    def test_event_original_text_basis_passes(self):
        """正控：引原文档级依据（notice-body 留档）的断言行不误拦。"""
        md = ("## 3a. 事件时间线\n\n**[分析]**\n\n"
              "公告原文已逐条读取（notice-body 留档）——原文之外的影响仍无法判定。"
              "[来源: 公告原文（notice-body 留档）]\n")
        assert event_analysis_evidence_findings(md) == []

    def test_event_section_sourced_and_structural_pass(self):
        """正控：事件段的结构行/来源绑定行/数据表不误拦。"""
        md = ("## 3a. 事件时间线\n\n"
              "| 日期 | 类型 | 公告标题 | 涉及维度（类型默认） |\n"
              "|------|------|---------|---------|\n"
              "| 2026-10-01 | 程序性公告 | 关于董事离任的公告 | 治理 |\n\n"
              "[来源: akshare stock_individual_notice_report / 1 条事件]\n\n"
              "**[事实]** 近 30 日公告按类型归类如下：\n\n"
              "  - **程序性公告** (1条) [来源: _meta.analysis_cards.event_classifications.0.event_label]\n\n"
              "**[分析]**\n\n"
              "分类线索仅作检索提示[来源: 封存 events 字段（类型/分类）]\n\n"
              "[证据强度: ⚠️ 中 📡单源 📅报告期已注明 —]\n\n"
              "⏭️ **行业事件**: ⏭️ 来源缺口：暂无稳定 API\n")
        assert event_analysis_evidence_findings(md) == []

    def test_event_layer_wired_into_conclusion_evidence(self):
        layer = _check_conclusion_evidence(
            "## 3a. 事件时间线\n\n**[分析]**\n\n公司盈利持续改善\n", "stock")
        assert layer.status == "fail"
        assert any(x["id"] == "wording-event-analysis-evidence"
                   for x in layer.details)


class TestEventTableVocabSyncWithProducer:
    """R14 round-8 生产者-检查器一致：`report_qc` 事件表结构核验的类型/维度
    词表由 taxonomy YAML（生产者单一源 `event_type_label`/`_event_dimension`
    的数据源）锁定——taxonomy 增改类型/维度而词表未同步时本组先红。"""

    _TAXONOMY = (Path(__file__).resolve().parents[1]
                 / "scripts" / "references" / "event_type_taxonomy.yaml")

    def _taxonomy(self) -> dict:
        yaml = pytest.importorskip("yaml")
        return yaml.safe_load(self._TAXONOMY.read_text(encoding="utf-8"))

    def test_type_labels_match_taxonomy(self):
        from report_qc import _EVENT_TYPE_LABELS
        types = self._taxonomy().get("event_types", {})
        labels = {v.get("label") for v in types.values()
                  if isinstance(v, dict) and v.get("label")}
        assert set(_EVENT_TYPE_LABELS) == labels

    def test_dimension_labels_match_taxonomy(self):
        from report_qc import _EVENT_DIMENSION_LABELS
        types = self._taxonomy().get("event_types", {})
        dims = {v.get("impact_dimension") for v in types.values()
                if isinstance(v, dict) and v.get("impact_dimension")}
        # `_event_dimension` 缺省回落后维度为「治理」——词表须含（taxonomy 值集并集）
        assert set(_EVENT_DIMENSION_LABELS) == dims | {"治理"}


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
