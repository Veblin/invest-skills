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
    """风格档案读取（monkeypatch STORE_DIR）。写入口 save_style 已删
    （无生产调用方；R10 档案写入后续补 CLI 入口）。"""

    def test_missing_file_returns_none(self, monkeypatch, tmp_path):
        monkeypatch.setattr("lib.env.STORE_DIR", tmp_path)
        assert load_style() is None

    def test_roundtrip_via_direct_file_write(self, monkeypatch, tmp_path):
        """档案文件存在 → load_style 解析成功（写路径用文件直写模拟）。"""
        monkeypatch.setattr("lib.env.STORE_DIR", tmp_path)
        import json
        (tmp_path / "user_style.json").write_text(
            json.dumps({"style": "趋势"}, ensure_ascii=False), encoding="utf-8")
        assert load_style() == "趋势"


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
        assert "**[风格匹配]** 匹配" in joined
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


# ---------- R2/T9-2：R1 证据装配唯一入口 + report 侧一致性 ----------


def _collection(dim_rows, *, industry="银行"):
    """构造 report 侧 collection（dimensions 列表形态）。"""
    return {"dimensions": [
        {"dimension": "financials", "data": dim_rows},
        {"dimension": "basic_info", "data": [{"industry": industry}]},
    ]}


_ANNUAL_ROWS = [
    {"end_date": "20231231", "net_profit": 100.0, "fcff": 10.0},
    {"end_date": "20241231", "net_profit": 90.0, "fcff": -5.0},
    {"end_date": "20251231", "net_profit": 130.0, "fcff": 20.0},
    {"end_date": "20250930", "net_profit": 77.0},        # 非年报期 → 剔除
    {"end_date": "20221231", "net_profit": None},          # 净利缺失 → 剔除
    "not-a-dict",                                          # 脏行 → 跳过不中断
]


class TestIncomeDriverEvidenceUnification:
    """`extract_annual_rows` 是 R1 证据装配的**唯一实现**。

    根因（R2/T9-2 勘察）：report 侧有两个装配点
    （`style_match._driver_from_collection` 与
    `render_markdown/_base._render_income_driver`），此前各自复制同一套
    「年报期 1231 过滤 + net_profit 非空 + float 化」；任一处漂移即造成
    「同一标的两个口径」。此处收敛，并用一致性用例钉死。
    """

    def test_extract_filters_and_survives_dirty_rows(self):
        from lib.income_driver import extract_annual_rows

        annual = extract_annual_rows(_ANNUAL_ROWS)
        assert [a["year"] for a in annual] == ["20231231", "20241231", "20251231"]
        assert annual[0]["net_profit"] == 100.0

    def test_extract_handles_non_numeric_net_profit(self):
        """单行脏值跳过而非掀翻整条链路（两处原实现语义不一致，此处统一为跳过）。"""
        from lib.income_driver import extract_annual_rows

        rows = [{"end_date": "20231231", "net_profit": "N/A"},
                {"end_date": "20241231", "net_profit": 5.0}]
        assert [a["year"] for a in extract_annual_rows(rows)] == ["20241231"]

    def test_both_report_sites_agree_on_same_collection(self):
        """report 侧两个装配点对同一 collection 必须得出**相同 driver**。"""
        from lib.render_markdown._base import _render_income_driver
        from lib.style_match import _driver_from_collection

        coll = _collection(_ANNUAL_ROWS)
        driver_a = _driver_from_collection(coll)
        lines = _render_income_driver(coll)
        assert lines, "R1 块未渲染"
        assert driver_a is not None
        assert driver_a in lines[0], f"两处口径不一致：{driver_a} vs {lines[0]!r}"

    def test_report_block_discloses_missing_dividend_evidence(self):
        """collection 无分红/再融资维度 → 证据缺口必须显式（差异不得静默）。

        2026-09-16：内部键名（dividend/refi）不再直接出口给读者——映射为中文
        语义并说明缺了它影响哪个判断。披露不减，只改可读性。
        """
        from lib.render_markdown._base import _render_income_driver

        lines = _render_income_driver(_collection(_ANNUAL_ROWS))
        joined = "\n".join(lines)
        assert "证据缺口" in joined
        assert "分红记录" in joined and "再融资" in joined
        # 内部键名不得泄漏到读者可见文本
        assert "dividend" not in joined and "refi" not in joined

    def test_style_match_no_longer_swallows_extraction_errors(self):
        """`_driver_from_collection` 不得再静默吞错（R2/T9-2 消除静默降级）。"""
        from lib.style_match import _driver_from_collection

        # 脏行不再让整条链路返回 None
        assert _driver_from_collection(_collection(_ANNUAL_ROWS)) is not None
        # 无法装配（<3 年报）仍返回 None——这是契约，不是吞错
        assert _driver_from_collection(_collection(_ANNUAL_ROWS[:2])) is None
