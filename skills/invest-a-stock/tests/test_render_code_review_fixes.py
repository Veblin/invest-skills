"""code-review render 层 5 缺陷修复回归测试（本地计算 + mock，零活体网络）。

覆盖：
- 缺陷1: val_cache 贯通（增强器条件 / 风险报告 / 自定义未知共享缓存，分位只全量算一次）
- 缺陷2: confidence_matrix 死代码删除（意图注释保留在 scoring.py，渲染层零引用）
- 缺陷3: R12g-A 均线系统表/连板结构 注册表单一来源 + TOC 包含两段
- 缺陷4: lib.render facade 延迟解析（patch lib.render.<name> / 真实模块均生效）
- 缺陷5: render_risk 覆盖总数取自 risk_scanner 返回结构（无 /17 与 >=15 幻数）
"""

from __future__ import annotations

import inspect
import re
import sys
from pathlib import Path
from unittest.mock import patch

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from fixtures.collections import make_daily_basic_series, make_kline_rows  # noqa: E402


def _collection(*, with_zt_lhb: bool = False) -> dict:
    """最小渲染集合：kline + valuation（可选 zt_pool/lhb 供连板结构渲染）。"""
    dims = [
        {"dimension": "valuation", "display": "估值", "data": make_daily_basic_series(60),
         "status": "available", "_meta": {"source": "test", "multi_source": True}},
        {"dimension": "kline", "display": "行情", "data": make_kline_rows(60),
         "status": "available", "_meta": {"source": "test"}},
    ]
    if with_zt_lhb:
        dims.extend([
            {"dimension": "zt_pool", "display": "涨停池", "data": {
                "date": "2026-08-05", "total": 103, "max_board": 6,
                "board_dist": {1: 78, 2: 15, 3: 6, 4: 3, 6: 1}},
             "status": "available", "_meta": {"source": "test"}},
            {"dimension": "lhb", "display": "龙虎榜", "data": {
                "seats": {"has_seats": True, "top_buy": []}},
             "status": "available", "_meta": {"source": "test"}},
        ])
    return {
        "symbol": "600176",
        "fetched_at": "2026-08-06T12:00:00+00:00",
        "dimensions": dims,
        "summary": {"available": len(dims), "total": len(dims)},
    }


# ══════════════════════════════════════════════════════════════════
# 缺陷1: val_cache 备忘录命中 —— 增强器/风险报告/自定义未知共享缓存
# ══════════════════════════════════════════════════════════════════

class TestValCacheShared:
    """同 collection 二次调用 → 5 年 PE/PB/PS 分位全量计算（valuation_summary）仅 1 次。"""

    def test_enhancer_and_risk_report_share_val_cache(self):
        from lib.render import _index_dims, _v3_build_risk_report
        from lib.render_markdown._concise import setup_default_enhancers
        import lib.valuation as valuation_mod

        coll = _collection()
        val_cache: dict = {}
        calls = {"n": 0}
        real_summary = valuation_mod.valuation_summary

        def counting(*args, **kwargs):
            calls["n"] += 1
            return real_summary(*args, **kwargs)

        with patch("lib.valuation.valuation_summary", counting):
            enhancer = setup_default_enhancers(coll, val_cache)
            enhancer.apply()  # valuation_high_alert 条件 → _is_valuation_extreme（首次全量计算）
            _v3_build_risk_report(
                coll, _index_dims(coll), coll.get("market_structure") or {},
                val_cache=val_cache,
            )
        assert calls["n"] == 1

    def test_full_report_renders_percentiles_once(self):
        """full 模式整份报告：增强器→风险报告→自定义未知→各 section 全程命中同一缓存。"""
        from test_v013_phase3 import _collection_phase3
        import lib.valuation as valuation_mod

        coll = _collection_phase3()
        calls = {"n": 0}
        real_summary = valuation_mod.valuation_summary

        def counting(*args, **kwargs):
            calls["n"] += 1
            return real_summary(*args, **kwargs)

        with (
            patch("lib.valuation.valuation_summary", counting),
            # DCF beta 的 HS300 基准为懒加载网络调用——mock 数据源保持零活体网络
            patch("akshare.stock_zh_index_daily_em", return_value=None),
            # store 快照 diff 会对「已存快照」另算一次 valuation_summary（合法独立路径），
            # 排除之，使断言聚焦于本次渲染热路径的分位缓存
            patch("lib.render_markdown._v3._load_report_key_diff", return_value=None),
        ):
            from lib.render import render_report_v3
            text = render_report_v3(coll, "600176", mode="full")
        assert "## 1." in text  # 渲染成功
        assert calls["n"] == 1, f"valuation_summary 应只全量计算 1 次，实际 {calls['n']} 次"


# ══════════════════════════════════════════════════════════════════
# 缺陷2: confidence_matrix 死代码删除（意图注释保留）
# ══════════════════════════════════════════════════════════════════

class TestConfidenceMatrixDeadCode:
    def test_removed_with_intent_comment_and_no_render_reference(self):
        import lib.scoring as scoring
        import lib.render as facade

        assert not hasattr(scoring, "confidence_matrix")
        assert not hasattr(scoring, "_dimension_confidence")
        # 删除位置保留意图注释（CHANGELOG v0.2.1「报告精简」依据）
        src = inspect.getsource(scoring)
        assert "AI 分析置信度矩阵（已移除，CHANGELOG v0.2.1「报告精简」）" in src
        assert "不得恢复该段" in src
        # 渲染层（含 facade 延迟解析）零引用 → 无零调用死代码
        for name in ("confidence_matrix", "_dimension_confidence"):
            assert not hasattr(facade, name)
            assert not hasattr(scoring, name)


# ══════════════════════════════════════════════════════════════════
# 缺陷3: R12g-A 均线系统表/连板结构 —— 注册表单一来源 + TOC 包含
# ══════════════════════════════════════════════════════════════════

class TestR12gTocAndRegistry:
    def test_toc_contains_r12g_sections(self):
        from lib.render_markdown._base import _R12G_HEADER_SECTIONS
        from lib.render_markdown._v3 import _report_toc

        labels = [label for label, _fn in _R12G_HEADER_SECTIONS]
        # 2026-09-16：均线系统表下沉 §8 技术指标附录（与 §8 既有「趋势」行重复），
        # 不再占头部注册表条目；注册表只剩连板结构（条件渲染）。
        assert labels == ["连板结构"]
        toc = _report_toc(_collection(with_zt_lhb=True))
        for label in labels:
            assert f"- {label}" in toc
        assert "- 均线系统表" not in toc

    def test_toc_omits_limit_streak_when_not_triggered(self):
        """batch-test P1-3：未触发连板（无 zt_pool/lhb）→ TOC 不得列出
        「连板结构」条目（目录引用不存在的章节）。"""
        from lib.render_markdown._v3 import _report_toc

        toc = _report_toc(_collection(with_zt_lhb=False))
        assert "- 连板结构" not in toc
        # 均线系统表已不在头部/TOC，任何情况下都不应出现
        assert "- 均线系统表" not in toc

    def test_header_excludes_demoted_engine_sections(self):
        """头部只放带结论的行（用户审阅 2026-09-16）。

        无结论的引擎自检与纯描述性技术行必须离开头部：
        多源融合/证据可信度 → 附录；均线系统表/近端价格结构 → §8。

        注意 R4「未覆盖行业」披露**不在此列**：它是覆盖范围说明而非数据罗列，
        且 brief/concise 无附录区承接（摘掉即删除），故保留在头部。
        """
        from lib.render_markdown._base import _render_engine_extras

        coll = _collection(with_zt_lhb=True)
        joined = "\n".join(_render_engine_extras(coll))
        assert "**[连板结构]**" in joined
        for banned in ("均线系统表", "近端价格结构", "多源融合", "证据可信度"):
            assert banned not in joined, f"头部不得再出现「{banned}」"

    def test_demoted_sections_still_present_in_full_report(self):
        """下沉 ≠ 删除：均线表/近端结构必须真的出现在 §8，附录必须真的渲染。"""
        from lib.render import render_report_v3
        from lib.render_markdown._base import (
            _ENGINE_SELFCHECK_LABEL,
            _render_engine_selfcheck_appendix,
            _render_ma_system,
            _render_price_structure,
        )

        coll = _collection(with_zt_lhb=True)
        text = render_report_v3(coll, "600176", mode="full")
        assert "## 8. 技术指标附录" in text
        appendix_section = text.split("## 8. 技术指标附录", 1)[1]
        expected = _render_ma_system(coll) + _render_price_structure(coll)
        assert expected, "fixture 应能产出均线表/近端结构，否则本测试空转"
        for line in expected:
            assert line in appendix_section, f"§8 缺少下沉内容: {line}"

        # 附录：有内容时必须渲染；无内容时不得留空标题（TOC 与正文同判据）
        heading = f"## {_ENGINE_SELFCHECK_LABEL}"
        selfcheck = _render_engine_selfcheck_appendix(coll)
        if selfcheck:
            assert selfcheck in text
        else:
            assert heading not in text

    def test_full_report_toc_and_header_consistent(self):
        from test_v013_phase3 import _collection_phase3
        from lib.render import render_report_v3

        # 未触发连板（phase3 fixture 无 zt_pool/lhb）：TOC 无该条目（batch-test P1-3）
        text = render_report_v3(_collection_phase3(), "600176", mode="full")
        assert "- 连板结构" not in text
        # 触发场景：TOC 与头部区块均出现（全渲染输出中 TOC 位于 ## 目录 后）
        triggered = render_report_v3(
            _collection(with_zt_lhb=True), "600176", mode="full")
        assert "- 连板结构" in triggered


class TestEngineSelfcheckAppendix:
    """附录「数据质量与引擎自检」——头部下沉内容的落点。"""

    @staticmethod
    def _coll(**overrides):
        coll = _collection()
        coll.update(overrides)
        return coll

    def test_consensus_weak_is_single_source_not_disagreement(self):
        """fusion 的 weak 是单源分支（max_diff 恒 0），不得渲染成「弱 + 零差异」。"""
        from lib.render_markdown._base import _render_engine_selfcheck_appendix

        out = _render_engine_selfcheck_appendix(self._coll(fusion={
            "kline": {"fused_value": 316.36, "consensus": "weak", "max_diff_pct": 0.0},
            "quote": {"fused_value": 316.36, "consensus": "strong", "max_diff_pct": 0.0},
        }))
        assert "单源，未做交叉验证" in out
        assert "weak" not in out
        assert "双源一致（≤1%）" in out
        # 单源行的差异列必须是「—」，与 0.0% 并列会自相矛盾
        row = next(l for l in out.splitlines() if l.startswith("| kline"))
        assert row.rstrip().endswith("| — |")

    def test_consensus_weak_multisource_shows_diff(self):
        """多源分歧（weak 且 max_diff>0）不得渲染成「单源」，差异列不得清空。

        fusion._consensus_from_diff 把「单源」与「源间差异 >5%」都编码成 weak，
        渲染层若按 consensus 单值推断源数量，跨源冲突会被报告成「只有一个源」，
        且唯一能区分的 max_diff_pct 被抹掉——违反 §2.3「数据冲突并列不裁决」。
        生产存量库已实际发生（weak 且 max_diff>0 的维度行）。
        """
        from lib.render_markdown._base import _render_engine_selfcheck_appendix

        out = _render_engine_selfcheck_appendix(self._coll(fusion={
            "financials": {
                "fused_value": 12.43, "consensus": "weak", "max_diff_pct": 117.89,
                "source_values": {"tushare.fina_indicator": 12.4282,
                                  "akshare.stock_financial_abstract_ths": 3.21},
            },
        }))
        assert "多源分歧" in out
        row = next(ln for ln in out.splitlines() if ln.startswith("| financials"))
        assert "单源" not in row, f"多源分歧被误标为单源: {row}"
        assert "117.89%" in row, "差异列被清空 → 冲突幅度不可见"

    def test_consensus_weak_multisource_diff_without_source_values(self):
        """旧数据无 source_values 键：有差异值即视为多源（保守，不误报单源）。"""
        from lib.render_markdown._base import _render_engine_selfcheck_appendix

        out = _render_engine_selfcheck_appendix(self._coll(fusion={
            "kline": {"fused_value": 1.0, "consensus": "weak", "max_diff_pct": 16.05},
        }))
        row = next(ln for ln in out.splitlines() if ln.startswith("| kline"))
        assert "16.05%" in row
        assert "单源" not in row

    def test_macro_detail_loan_is_not_unit_converted(self):
        """附录列引擎原值：渲染层不得再做 亿→万亿 换算（P0）。

        `_render_macro_detail_lines` 曾按本地阈值换算——3000（亿元）渲染成
        「0.3万亿」、12000 渲染成「12000亿」，同一引擎字段两种单位，且仍挂
        引擎来源标签，第 1 层复检无法与原始 JSON 对值对单位。
        """
        from lib.render_markdown._base import _render_engine_selfcheck_appendix

        out = _render_engine_selfcheck_appendix(self._coll(macro_context={
            "indicators": {"loan": {"value": 3000.0,
                                    "source": "akshare.macro_rmb_loan"}},
        }))
        line = next(ln for ln in out.splitlines() if "新增信贷" in ln)
        assert "万亿" not in line, f"渲染层仍在换算单位: {line}"
        assert "亿元" in line, f"须显式声明单位: {line}"
        assert "3000" in line.replace(",", ""), f"引擎原值未原样出现: {line}"

    def test_ma_table_labels_close_date_not_realtime_price(self):
        """均线表须标明日线收盘口径与日期——与模块 1 实时价不是同一个数。

        报告同时出现两个「现价」（quote 305.48 / kline 收盘 316.36）时，读者会
        把技术段的口径当成实时价（review P1）。
        """
        from lib.render_markdown._base import _render_ma_system

        out = "\n".join(_render_ma_system(_collection()))
        assert "现价" not in out, f"仍以「现价」标注日线收盘值: {out}"
        assert re.search(r"收盘 [\d.]+（\d{8}）", out), f"未标注收盘价与日期: {out}"

    def test_fused_value_is_formatted(self):
        from lib.render_markdown._base import _format_fused_value

        # legacy 路径未 round，曾渲染出 14637.250837439999
        assert _format_fused_value(14637.250837439999) == "14,637.25"
        assert _format_fused_value(316.36) == "316.36"
        assert _format_fused_value(0.12345) == "0.1235"
        assert _format_fused_value(None) == "—"
        assert _format_fused_value(float("nan")) == "—"
        assert _format_fused_value("x") == "x"

    def test_appendix_carries_credibility_caliber_note(self):
        from lib.render_markdown._base import _render_engine_selfcheck_appendix

        out = _render_engine_selfcheck_appendix(self._coll(
            credibility={"估值分析": 85, "日K线": 55}))
        assert "证据可信度" in out
        assert "非投资含义" in out      # 读者须知道 85 不是好消息也不是坏消息

    def test_macro_detail_lists_values_with_sources(self):
        from lib.render_markdown._base import _render_engine_selfcheck_appendix

        out = _render_engine_selfcheck_appendix(self._coll(macro_context={
            "status": "ok",
            "indicators": {"dgs10": {"value": 4.96, "signal": "高位", "source": "FRED.DGS10"}},
        }))
        assert "美10Y: 4.96%" in out
        assert "FRED.DGS10" in out
        # 定性判断由头部标签的确定性规则统一给出，明细不得再挂无阈值的定性词
        assert "高位" not in out

    def test_empty_selfcheck_renders_nothing(self):
        """无自检数据 → 空串（不留空标题，TOC 也不列）。"""
        from lib.render_markdown._base import _render_engine_selfcheck_appendix

        assert _render_engine_selfcheck_appendix(_collection()) == ""

    def test_demoted_sections_render_in_all_three_modes_or_not_at_all(self):
        """下沉内容只在 full 有承接方；brief/concise 无 §8/附录，属**删除**。

        本测试把这个取舍显性化：如果将来要恢复 brief/concise 的均线信息，
        必须显式改这里，而不是以为「下沉」在三模式都成立了。
        """
        from lib.render import render_report_v3

        coll = _collection(with_zt_lhb=True)
        coll["success_factors"] = {"industry": "电气设备", "covered": False, "factors": []}
        for mode in ("brief", "concise"):
            text = render_report_v3(coll, "600176", mode=mode)
            for dropped in ("均线系统表", "近端价格结构", "多源融合", "证据可信度"):
                assert dropped not in text, f"{mode} 不应有 {dropped}"
            # 覆盖披露必须仍在——摘掉它等于删除信息，不是降噪
            assert "无行业成功因素定义" in text, f"{mode} 丢了覆盖披露"


# ══════════════════════════════════════════════════════════════════
# 缺陷4: lib.render facade 延迟解析 —— monkeypatch 生效（内部走真实模块）
# ══════════════════════════════════════════════════════════════════

class TestFacadeLazyResolution:
    def test_monkeypatch_facade_name_takes_effect(self, monkeypatch):
        """patch lib.render.<name> → 内部 render 路径（facade-aware wrapper）命中。"""
        from lib.render_markdown import _v3_valuation_percentiles as wrapper

        monkeypatch.setattr(
            "lib.render._v3_valuation_percentiles",
            lambda dims, cache: (91.0, 40.0, "偏高"),
        )
        assert wrapper({}, {}) == (91.0, 40.0, "偏高")

    def test_source_module_patch_propagates_to_facade(self, monkeypatch):
        """patch 真实定义模块 → 经 facade 的运行期查找命中新值（旧 eager 拷贝会遮蔽）。"""
        import lib.render as facade

        monkeypatch.setattr("lib.render_markdown._report_toc", lambda: "FAKE_TOC")
        assert facade._report_toc() == "FAKE_TOC"
        # 非 wrapper 名经 facade 查找同样延迟解析（不拷贝到 facade 命名空间）
        assert "_report_toc" not in facade.__dict__

    def test_from_import_still_works(self):
        from lib.render import _report_toc, render_report_v3

        assert callable(_report_toc)
        assert callable(render_report_v3)


# ══════════════════════════════════════════════════════════════════
# 缺陷5: render_risk 覆盖总数取自 risk_scanner（无 /17 与 >=15 幻数）
# ══════════════════════════════════════════════════════════════════

class TestRiskCoverageNoMagic17:
    def test_section_uses_coverage_total(self):
        from lib.render_risk import _section_risk_uncertainty

        risk_data = {
            "coverage": {"auto": 24, "total": 25},
            "triggered_count": 3,
            "signals": [],
            "known_unknowns": [],
        }
        text = _section_risk_uncertainty({}, "600176", {}, {}, risk_data)
        assert "自动判定覆盖 24/25 信号" in text  # judgment
        assert "自动判定覆盖：**24/25** 信号" in text  # 正文行
        assert "自动判定 24/25 项" in text  # 证据块
        assert "/17" not in text

    def test_evidence_marker_scales_with_total(self):
        from lib.render_risk import _section_risk_uncertainty

        def render(auto: int, total: int) -> str:
            return _section_risk_uncertainty(
                {}, "600176", {}, {},
                {"coverage": {"auto": auto, "total": total},
                 "triggered_count": 0, "signals": [], "known_unknowns": []},
            )

        # total=17: auto=16 → ✅（原 >=15 语义，total-2 阈值等价）
        assert "✅ 自动判定 16/17 项" in render(16, 17)
        assert "⚠️ 自动判定 14/17 项" in render(14, 17)
        # total=25: 阈值随 total 缩放（>=23 才 ✅）
        assert "✅ 自动判定 24/25 项" in render(24, 25)
        assert "⚠️ 自动判定 20/25 项" in render(20, 25)

    def test_no_magic_numbers_in_source(self):
        import lib.render_risk as rr

        src = inspect.getsource(rr)
        assert "/17" not in src
        assert ">= 15" not in src
        assert "auto_n >= 15" not in src


class TestRiskImpliedMcUsesLatestRow:
    """review #3：valuation 维度列表按 trade_date 升序（cff62c3 约定），
    Bull/Bear 隐含市值必须取最新行——此前 for+break 取首行 = 锚定约 5 年前
    市值（600176 实例 980 亿 vs 最新 396 亿）。"""

    def test_implied_mcap_uses_latest_row(self):
        from lib.render_risk import _section_bull_bear

        dims = {
            "valuation": {"data": [
                {"trade_date": "2021-08-01", "pe_ttm": 40.0, "total_mv": 980.0},
                {"trade_date": "2026-08-01", "pe_ttm": 20.0, "total_mv": 396.0},
            ]},
            "financials": {"data": [
                {"end_date": "20251231", "roe": 0.15, "net_profit": 2e9},
            ]},
        }
        text = _section_bull_bear({}, "600176", dims, {}, {"signals": []})
        assert "当前市值 396.00亿" in text
        assert "980.00亿" not in text

    def test_implied_mcap_tolerates_missing_latest_row(self):
        """最新行 total_mv 缺失（NaN/None）→ 取最近的有效行。"""
        from lib.render_risk import _section_bull_bear

        dims = {
            "valuation": {"data": [
                {"trade_date": "2021-08-01", "pe_ttm": 40.0, "total_mv": 980.0},
                {"trade_date": "2026-08-01", "pe_ttm": 20.0, "total_mv": None},
            ]},
            "financials": {"data": [
                {"end_date": "20251231", "roe": 0.15, "net_profit": 2e9},
            ]},
        }
        text = _section_bull_bear({}, "600176", dims, {}, {"signals": []})
        assert "当前市值 980.00亿" in text


class TestBullBearDefaultRAnnotation:
    """review #13：模块 5 在 r 为默认假设 2.5%（FRED/akshare 不可得）时须标注
    [推测，待验证] 且方向性对比加盖警示——与模块 4 D-③（_v3.py:3487-3491）口径一致。"""

    def test_default_r_annotated_in_5c_and_5d(self):
        from lib.render_risk import _section_bull_bear

        dims = {
            "valuation": {"data": [
                {"trade_date": "2024-01-01", "pe_ttm": 12.0, "total_mv": 300.0},
                {"trade_date": "2025-01-01", "pe_ttm": 20.0, "total_mv": 396.0},
            ]},
            "financials": {"data": [
                {"end_date": "20231231", "revenue": 1.5e9, "net_profit": 1.5e8},
                {"end_date": "20241231", "revenue": 1.7e9, "net_profit": 1.8e8},
                {"end_date": "20251231", "revenue": 2.0e9, "net_profit": 2.0e8},
            ]},
        }
        # market_structure={} → erp.dgs10 不可得 → risk_free=0.025 默认假设路径
        text = _section_bull_bear({}, "600176", dims, {}, {"signals": []})
        assert "[推测，待验证" in text
        assert "方向仅供参考" in text


class TestAnalysisStatusUnavailable:
    """review C2：校验组件不可导入时不得写成「分析合成未完成」。

    后者是关于报告内容的事实性断言；工具故障时它不成立——产物必须外显故障，
    而不是断言内容缺失。absent / invalid 的既有文案被 5 处测试钉死，不得改动。
    """

    _VALID = [{
        "module": "risk", "title": "风险提示", "position": "conclusion",
        "facts_md": "近 30 日公告 3 条。", "analysis_md": "分类复核结论。",
        "evidence_tag": "B",
    }]

    @staticmethod
    def _force_unavailable():
        # None in sys.modules → `from lib.analysis_schema import ...` 抛 ImportError
        return patch.dict(sys.modules, {"lib.analysis_schema": None})

    def test_markdown_card_reports_unavailable_not_content_absence(self):
        from lib.render_markdown._concise import _full_mode_identity_status

        with self._force_unavailable():
            out = _full_mode_identity_status("600176", self._VALID)
        assert "分析合成状态无法校验" in out, f"未外显工具故障: {out}"
        assert "分析合成未完成" not in out, f"工具故障被写成内容缺失: {out}"

    def test_html_card_reports_unavailable_not_content_absence(self):
        from lib.render_html import _html_full_mode_identity_status

        with self._force_unavailable():
            out = _html_full_mode_identity_status("600176", "full", self._VALID)
        assert "分析合成状态无法校验" in out, f"未外显工具故障: {out}"
        assert "分析合成未完成" not in out, f"工具故障被写成内容缺失: {out}"

    def test_valid_payload_still_reports_injected(self):
        from lib.render_markdown._concise import _full_mode_identity_status

        out = _full_mode_identity_status("600176", self._VALID)
        assert "分析合成已注入" in out, out

    def test_absent_and_invalid_keep_pinned_wording(self):
        """空数组与畸形段仍按「未完成」——文案被 test_v015_fixes 等 5 处钉死。"""
        from lib.render_markdown._concise import _full_mode_identity_status

        for payload in ([], [{"analysis_md": "缺必填字段"}]):
            out = _full_mode_identity_status("600176", payload)
            assert "数据底稿（分析合成未完成）" in out, (payload, out)
