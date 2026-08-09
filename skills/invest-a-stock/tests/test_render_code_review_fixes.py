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
        assert labels == ["均线系统表（R12g）", "连板结构（R12g）"]
        # 连板结构触发时（zt_pool/lhb 存在）→ TOC 含两段
        toc = _report_toc(_collection(with_zt_lhb=True))
        for label in labels:
            assert f"- {label}" in toc
        # TOC 顺序与注册表一致（消除漂移）
        idxs = [toc.index(f"- {label}") for label in labels]
        assert idxs == sorted(idxs)

    def test_toc_omits_limit_streak_when_not_triggered(self):
        """batch-test P1-3：未触发连板（无 zt_pool/lhb）→ TOC 不得列出
        「连板结构」条目（目录引用不存在的章节）。"""
        from lib.render_markdown._v3 import _report_toc

        toc = _report_toc(_collection(with_zt_lhb=False))
        assert "- 均线系统表（R12g）" in toc
        assert "- 连板结构（R12g）" not in toc

    def test_extras_rendered_from_registry(self):
        from lib.render_markdown._base import _R12G_HEADER_SECTIONS, _render_engine_extras

        coll = _collection(with_zt_lhb=True)
        joined = "\n".join(_render_engine_extras(coll))
        for label, _fn in _R12G_HEADER_SECTIONS:
            assert label in joined  # 注册表标签即渲染输出前缀
        assert "**[均线系统表（R12g）]**" in joined
        assert "**[连板结构（R12g）]**" in joined

    def test_full_report_toc_and_header_consistent(self):
        from test_v013_phase3 import _collection_phase3
        from lib.render import render_report_v3

        # 未触发场景（phase3 fixture 无 zt_pool/lhb）：TOC 只有均线表，
        # 无连板结构条目（batch-test P1-3）
        text = render_report_v3(_collection_phase3(), "600176", mode="full")
        assert "- 均线系统表（R12g）" in text
        assert "- 连板结构（R12g）" not in text
        # 触发场景：TOC 与头部区块均出现（全渲染输出中 TOC 位于 ## 目录 后）
        triggered = render_report_v3(
            _collection(with_zt_lhb=True), "600176", mode="full")
        assert "- 均线系统表（R12g）" in triggered
        assert "- 连板结构（R12g）" in triggered


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
