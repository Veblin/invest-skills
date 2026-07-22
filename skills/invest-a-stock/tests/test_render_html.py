"""render_html 模板测试。"""

from __future__ import annotations

import re

from stock_testutil import FORBIDDEN_SIGNAL_WORDS
from fixtures.collections import collection_kline_insufficient, collection_v2_minimal


def _analysis_body_without_legal(html: str) -> str:
    """剔除免责声明块后再做禁词检查。"""
    return re.sub(
        r'<div class="disc"[^>]*>.*?</div>',
        "",
        html,
        flags=re.DOTALL,
    )


class TestRenderHtmlStructure:
    def test_core_sections_present(self):
        from lib.render import render_html

        html = render_html(collection_v2_minimal(), "600176")
        for section_id in (
            "overview", "valuation", "financials", "technicals",
            "northbound", "holders", "events", "refs",
        ):
            assert f'id="{section_id}"' in html, f"缺少 section: {section_id}"

    def test_risk_banner_and_disclaimer(self):
        from lib.render import render_html

        html = render_html(collection_v2_minimal(), "600176")
        assert "风险提示" in html
        assert "免责声明" in html
        assert html.index("风险提示") < html.index("免责声明")

    def test_chart_js_embedded(self):
        from lib.render import render_html

        html = render_html(collection_v2_minimal(), "600176")
        assert "chart.umd" in html or "Chart(" in html
        assert len(html) > 100_000

    def test_no_holder_history_chart(self):
        from lib.render import render_html

        html = render_html(collection_v2_minimal(), "600176")
        assert "holderPeriods" not in html
        assert "holderChart" not in html
        assert "多期对比" not in html
        assert 'id="holders"' in html

    def test_chart_js_valid_braces(self):
        """f-string 不应向浏览器输出 {{ 导致 SyntaxError。"""
        from lib.render import render_html

        html = render_html(collection_v2_minimal(), "600176")
        scripts = re.findall(r"<script>(.*?)</script>", html, re.DOTALL)
        app_script = scripts[-1]
        assert "const tt={" in app_script
        assert "const tt={{" not in app_script
        assert "new Chart(document.getElementById('roeChart'),{type:" in app_script
        assert "function renderCharts(){" in app_script

    def test_insufficient_kline_no_crash(self):
        from lib.render import render_html

        html = render_html(collection_kline_insufficient(), "600176")
        assert len(html) > 0
        assert "技术指标" in html


class TestRenderHtmlCompliance:
    def test_no_forbidden_words_in_body(self):
        from lib.render import render_html

        html = render_html(collection_v2_minimal(), "600176")
        body = _analysis_body_without_legal(html)
        for word in FORBIDDEN_SIGNAL_WORDS:
            assert word not in body, f"HTML 正文含禁止词: {word}"


class TestNorthboundNormalization:
    def test_tushare_wan_to_yuan(self):
        from lib.collector import _normalize_northbound_records

        rows = [{"trade_date": "20260101", "net_mf_amount": 1500.0}]
        out = _normalize_northbound_records(rows, "tushare.moneyflow")
        assert out[0]["net_mf_amount"] == 15_000_000.0
        assert out[0]["net_mf_vol"] == 15_000_000.0

    def test_akshare_unchanged(self):
        from lib.collector import _normalize_northbound_records

        rows = [{"trade_date": "20260101", "net_mf_vol": 1.5e8}]
        out = _normalize_northbound_records(rows, "akshare.northbound")
        assert out[0]["net_mf_vol"] == 1.5e8

    def test_moneyflow_does_not_scale_net_mf_vol_fallback(self):
        """moneyflow net_mf_vol is volume(手), must not get 万元×10000."""
        from lib.collector import _normalize_northbound_records

        rows = [{"trade_date": "20260101", "net_mf_vol": 100.0}]
        out = _normalize_northbound_records(rows, "tushare.moneyflow")
        # no net_mf_amount → leave row alone (no invented yuan figure)
        assert out[0].get("net_mf_amount") is None
        assert out[0]["net_mf_vol"] == 100.0
