"""R10/R12g-B 风格-标的匹配三态测试（纯函数 + 渲染 + 档案持久化）。

覆盖验收点（R10 验收原文）：
- 三态单元测试：构造 匹配/中性/混搭 三样本（含"无 journal 记录 → 不出现混搭提示"路径）
- 报告头部含三态字段（渲染 grep）
- 混搭提示仅在有 journal Q1 记录且冲突时出现，其余情况不打扰
- 风格档案持久化（save/load 往返；写失败 → 降级不抛）
"""

from __future__ import annotations

from lib.style_match import (
    MATCHING_PAIRS,
    format_match_hint,
    load_style,
    match_style,
    save_style,
)


class TestMatchStyle:
    """① 三态样本各断言。"""

    def test_matching_value_pair(self):
        m = match_style("价值", "估值股息回归")
        assert m["state"] == "匹配"
        assert m["hint"] is None

    def test_matching_growth_pair(self):
        assert match_style("成长", "成长兑现")["state"] == "匹配"

    def test_matching_trend_unknown_informed(self):
        m = match_style("趋势", "暂无法判定")
        assert m["state"] == "匹配"
        assert "信息深度不足" in m["reason"]

    def test_neutral_style_missing(self):
        m = match_style(None, "估值股息回归")
        assert m["state"] == "中性"
        assert "风格未填写" in m["reason"]

    def test_neutral_driver_unknown(self):
        m = match_style("价值", "暂无法判定")
        assert m["state"] == "中性"

    def test_neutral_unmapped_pair(self):
        """价值 × 成长兑现（未定义映射）→ 中性，不自动推断。"""
        m = match_style("价值", "成长兑现")
        assert m["state"] == "中性"

    def test_mix_risk_journal_conflict(self):
        """混搭：journal=趋势跟随 × R1=估值股息回归。"""
        m = match_style("价值", "估值股息回归", journal_driver="趋势跟随")
        assert m["state"] == "混搭风险"
        assert m["hint"] is not None
        assert "不能把基本面的投资手册当成趋势投资的航海指南" in m["hint"]

    def test_mix_risk_wins_over_style(self):
        m = match_style(None, "估值股息回归", journal_driver="趋势跟随")
        assert m["state"] == "混搭风险"

    def test_no_journal_record_no_hint(self):
        """② 无 journal 记录 → 不出现混搭提示。"""
        m = match_style("价值", "估值股息回归", journal_driver=None)
        assert m["state"] == "匹配"
        assert m["hint"] is None

    def test_hint_only_on_conflict(self):
        """③ 混搭提示仅在有记录且冲突时出现。"""
        assert format_match_hint("估值股息回归", "趋势跟随")
        m1 = match_style("价值", "估值股息回归", journal_driver="均值回归")
        assert m1["state"] == "匹配"  # journal 与 driver 同向 → 无提示
        assert m1["hint"] is None

    def test_matching_pairs_invariants(self):
        for style, driver in MATCHING_PAIRS.items():
            assert match_style(style, driver)["state"] == "匹配"


class TestStylePersistence:
    """风格档案持久化（monkeypatch STORE_DIR）。"""

    def test_save_load_roundtrip(self, monkeypatch, tmp_path):
        monkeypatch.setattr("lib.env.STORE_DIR", tmp_path)
        assert save_style("趋势") is True
        assert load_style() == "趋势"

    def test_missing_file_returns_none(self, monkeypatch, tmp_path):
        monkeypatch.setattr("lib.env.STORE_DIR", tmp_path)
        assert load_style() is None

    def test_write_failure_degrades_gracefully(self, monkeypatch, tmp_path):
        monkeypatch.setattr("lib.env.STORE_DIR", tmp_path / "no_such_dir" / "x")
        # STORE_DIR 指向深层不存在目录 → mkdir 失败场景模拟为 False（不抛）
        assert save_style("价值") is True  # mkdir parents=True 会创建，改测只读场景
        # 只读目录场景
        import os
        ro_dir = tmp_path / "ro"
        ro_dir.mkdir()
        os.chmod(ro_dir, 0o500)
        try:
            monkeypatch.setattr("lib.env.STORE_DIR", ro_dir)
            assert save_style("价值") is False
        finally:
            os.chmod(ro_dir, 0o700)


class TestRenderStyleMatch:
    """④ _render_style_match 渲染行 grep。"""

    def _collection(self, cfg: dict) -> dict:
        return {"symbol": "600176", "dimensions": [], "summary": {},
                "style_match": cfg}

    def test_renders_state_line(self):
        from lib.render_markdown._base import _render_style_match

        lines = _render_style_match(self._collection({
            "style": "价值", "driver": "估值股息回归", "journal_driver": None,
            "state": "匹配", "reason": "价值 × 估值股息回归", "hint": None,
        }))
        joined = "\n".join(lines)
        assert "**[风格-标的匹配（R10）]** 匹配" in joined
        assert "自评风格 价值" in joined
        assert "估值股息回归" in joined

    def test_renders_mix_hint(self):
        from lib.render_markdown._base import _render_style_match

        lines = _render_style_match(self._collection({
            "style": "价值", "driver": "估值股息回归", "journal_driver": "趋势跟随",
            "state": "混搭风险",
            "reason": "journal Q1=「趋势跟随」 vs R1=「估值股息回归」指向不同方法论",
            "hint": format_match_hint("估值股息回归", "趋势跟随"),
        }))
        joined = "\n".join(lines)
        assert "混搭风险" in joined
        assert "不能把基本面的投资手册当成趋势投资的航海指南" in joined

    def test_no_cfg_renders_nothing(self):
        from lib.render_markdown._base import _render_style_match

        assert _render_style_match({"symbol": "600176", "dimensions": []}) == []
