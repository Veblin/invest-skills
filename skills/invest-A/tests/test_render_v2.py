"""render_report_v2 模板测试。

覆盖 v0.1.2 八段结构、合规禁词、估值降级文案。
"""

from __future__ import annotations

import pytest

from conftest import FORBIDDEN_SIGNAL_WORDS
from fixtures.collections import (
    collection_kline_insufficient,
    collection_v2_minimal,
    collection_valuation_snapshot_only,
)


class TestRenderReportV2Structure:
    def test_eight_sections_present(self):
        from lib.render import render_report_v2

        text = render_report_v2(collection_v2_minimal(), "600176")
        for heading in (
            "## 一、公司画像",
            "## 二、经营质量",
            "## 三、估值位置",
            "## 四、资金与筹码",
            "## 五、技术结构",
            "## 六、事件催化",
            "## ⚡ 核心矛盾",
            "## 📚 引用来源",
        ):
            assert heading in text, f"缺少章节: {heading}"

    def test_risk_disclaimer_head_and_tail(self):
        from lib.render import render_report_v2

        text = render_report_v2(collection_v2_minimal(), "600176")
        assert text.count("风险提示") >= 1
        assert "免责声明" in text
        assert "不构成任何投资建议" in text

    def test_render_md_routes_to_v3(self):
        from lib.render import render

        text = render(collection_v2_minimal(), "600176", "md")
        assert "## 0. 研究问题卡" in text
        assert "## 6. 左侧/右侧概率判断" in text

    def test_render_compact_routes_to_v2(self):
        from lib.render import render

        text = render(collection_v2_minimal(), "600176", "compact")
        assert "## 一、公司画像" in text
        assert "# 600176" in text

    def test_render_md_differs_from_compact(self):
        from lib.render import render

        c = collection_v2_minimal()
        assert render(c, "600176", "md") != render(c, "600176", "compact")

    def test_financials_roe_trend_uses_latest_periods(self):
        """升序 financials：趋势句应基于最近两期（非最旧两期）。"""
        from lib.render import render_report_v2

        text = render_report_v2(collection_v2_minimal(), "600176")
        assert "18.5% → 20.2%" in text or "20.2%" in text
        assert "上升" in text

    def test_valuation_pe_and_ps_rendered(self):
        from lib.render import render_report_v2

        text = render_report_v2(collection_v2_minimal(), "600176")
        assert "PE(TTM)" in text
        assert "PS(TTM)" in text

    def test_technical_section_with_descending_kline(self):
        """降序 K 线输入时，技术块仍应基于最新收盘价计算。"""
        from lib.render import render_report_v2

        c = collection_v2_minimal(kline_descending=True)
        text = render_report_v2(c, "600176")
        assert "## 五、技术结构" in text
        assert "MACD" in text
        # 升序后最新 close ≈ 100 + 59*0.5 = 129.5
        assert "129.5" in text

    def test_valuation_descending_series_current_pe(self):
        """降序 daily_basic：当前 PE 应取最新交易日而非最旧。"""
        from lib.render import render_report_v2

        c = collection_v2_minimal(kline_descending=True)
        text = render_report_v2(c, "600176")
        # 序列末条 pe_ttm = 20 + 49*0.1 = 24.9
        assert "24.90x" in text or "24.9" in text

    def test_percentile_wording_matches_zone(self):
        """估值分位文案与 zone 标签语义一致（pct 越高 zone 越高）。"""
        from lib.render import render_report_v2

        text = render_report_v2(collection_v2_minimal(), "600176")
        # pct=98 → zone=偏高 → 分位文案不应出现「低于」反转
        assert "98.0% 分位" in text
        assert "偏低" not in text.split("**PE(TTM):**")[1].split("区间")[0] if "PE(TTM)" in text else True
        # zone 应与分位文案同向
        pe_section = text.split("**PE(TTM):**")[1].split("\n")[0] if "**PE(TTM):**" in text else ""
        if "偏高" in pe_section:
            assert "低于" not in pe_section

    def test_thesis_placeholder_uses_latest_with_descending(self):
        """降序数据时核心矛盾卡片应取最新值（排序后末位）。"""
        from lib.render import render_report_v2

        c = collection_v2_minimal(kline_descending=True)
        text = render_report_v2(c, "600176")
        # ROE 应取 20.2%（最新），不是 18.5%（降序末位）
        assert "ROE=20.2%" in text
        # PE 应取 ~24.9x（最新），不是 20.0x（降序末位）
        assert "PE=24.9x" in text


class TestRenderValuationDegraded:
    def test_snapshot_shows_history_unavailable(self):
        from lib.render import render_report_v2

        text = render_report_v2(collection_valuation_snapshot_only(), "600176")
        assert "历史分位不可得" in text
        assert "25.80x" in text or "25.8" in text


def _analysis_body_without_legal_disclaimers(text: str) -> str:
    """剔除风险/免责声明行后再做禁词检查（声明中可出现「不构成…目标价预测」）。"""
    kept: list[str] = []
    for line in text.splitlines():
        if line.startswith(">") and any(
            p in line for p in ("不构成", "免责声明", "风险提示", "非交易信号")
        ):
            continue
        kept.append(line)
    return "\n".join(kept)


class TestFinancialsSortOrder:
    def test_descending_financials_roe_trend_uses_latest(self):
        """降序 financials 时 ROE 趋势句仍基于最近两期（升序后末两位）。"""
        from lib.render import render_report_v2

        c = collection_v2_minimal(kline_descending=True)
        text = render_report_v2(c, "600176")
        # 升序后最新两期: 20240331(roe=18.5) → 20241231(roe=20.2)
        assert "上升" in text

    def test_ascending_financials_roe_trend_uses_latest(self):
        """升序 financials 时 ROE 趋势句基于最近两期。"""
        from lib.render import render_report_v2

        c = collection_v2_minimal(kline_descending=False)
        text = render_report_v2(c, "600176")
        assert "上升" in text


class TestTechnicalInsufficientData:
    def test_kline_insufficient_shows_unavailable(self):
        """K 线 < 26 条时 MACD 应标注不可得。"""
        from lib.render import render_report_v2

        text = render_report_v2(collection_kline_insufficient(), "600176")
        assert "## 五、技术结构" in text
        assert "不可得" in text or "数据不足" in text

    def test_kline_insufficient_no_crash(self):
        """K 线不足时渲染不抛异常。"""
        from lib.render import render_report_v2

        text = render_report_v2(collection_kline_insufficient(), "600176")
        assert len(text) > 0


class TestDvRatioUnit:
    def test_dv_ratio_display_is_percentage_scale(self):
        """dv_ratio 为百分比值（如 0.42 表示 0.42%），直接显示不加倍乘。

        Tushare daily_basic.dv_ratio 字段为「股息率（%）」，取值如 0.42。
        """
        from lib.render import render_report_v2

        text = render_report_v2(collection_v2_minimal(), "600176")
        # dv_ratio=0.42 → 直接显示 0.42%
        assert "0.42%" in text

    def test_dv_ratio_not_double_scaled(self):
        """dv_ratio 不应被重复缩放。"""
        from lib.render import render_report_v2

        text = render_report_v2(collection_v2_minimal(), "600176")
        assert "42.00%" not in text
        assert "0.00%" not in text


class TestRenderCompliance:
    def test_no_forbidden_words_in_analysis_body(self):
        from lib.render import render_report_v2

        text = render_report_v2(collection_v2_minimal(), "600176")
        body = _analysis_body_without_legal_disclaimers(text)
        for word in FORBIDDEN_SIGNAL_WORDS:
            assert word not in body, f"分析正文含禁止词: {word}"

    def test_technical_uses_descriptive_cross_language(self):
        from lib.render import render_report_v2

        text = render_report_v2(collection_v2_minimal(), "600176")
        # 允许描述性用语，不应出现信号词
        if "DIF" in text:
            assert "上穿" in text or "下穿" in text or "位于" in text


class TestSanitizeError:
    def test_distinguishes_tun_from_proxy_failure(self):
        from lib.collector import _EASTMONEY_PROXY_MSG, _EASTMONEY_TUN_OR_CDN_MSG
        from lib.render import sanitize_error

        tun = sanitize_error(_EASTMONEY_TUN_OR_CDN_MSG)
        proxy = sanitize_error(_EASTMONEY_PROXY_MSG)
        assert "TUN" in tun or "push2" in tun
        assert tun != proxy
        assert "Clash DIRECT" in proxy or "HTTP 代理" in proxy
