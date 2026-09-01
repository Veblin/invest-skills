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

    def test_echarts_embedded(self):
        from lib.render import render_html

        html = render_html(collection_v2_minimal(), "600176")
        assert "getInstanceByDom" in html          # 适配层存在（echarts 语义）
        assert "cdn.jsdelivr.net" not in html      # 无 CDN 外链
        assert "registry.npmmirror.com" not in html
        assert len(html) > 100_000

    def test_no_holder_history_chart(self):
        from lib.render import render_html

        html = render_html(collection_v2_minimal(), "600176")
        assert "holderPeriods" not in html
        assert "holderChart" not in html
        assert "多期对比" not in html
        assert 'id="holders"' in html

    def test_echarts_valid_braces(self):
        """f-string 不应向浏览器输出 {{ 导致 SyntaxError。"""
        from lib.render import render_html

        html = render_html(collection_v2_minimal(), "600176")
        scripts = re.findall(r"<script>(.*?)</script>", html, re.DOTALL)
        app_script = scripts[-1]
        assert "function renderCharts(){" in app_script
        assert "echarts.init" in app_script
        assert "{{" not in app_script

    def test_echarts_asset_missing_fallback(self):
        """资产缺失（_load_echarts_js 返回空串）→ 图表 disabled、页面完整（R-B4）。"""
        from lib import render_html as rh

        html = rh.render_html(collection_v2_minimal(), "600176")
        assert "<body" in html and "</html>" in html  # 页面完整

    def test_echarts_asset_missing_fallback_monkeypatched(self, monkeypatch):
        """资产缺失路径：_load_echarts_js 空串 → 无资产注入但页面完整。"""
        from lib import render_html as rh

        monkeypatch.setattr(rh, "_load_echarts_js", lambda: "")
        html = rh.render_html(collection_v2_minimal(), "600176")
        assert "<body" in html and "</html>" in html  # 页面完整
        assert "function renderCharts(" in html       # 适配层仍在（_load 空串即 disable）
        assert "echarts.min.js" not in html           # 无资产内容注入

    def test_insufficient_kline_no_crash(self):
        from lib.render import render_html

        html = render_html(collection_kline_insufficient(), "600176")
        assert len(html) > 0
        assert "技术指标" in html

    def test_kline_chart_wired(self):
        """T3-4：≥30 日 kline → technicals 段 data-echart K 线 div + data-opts 解析含 candlestick。"""
        import html as _h
        import json

        from lib.render import render_html

        html = render_html(collection_v2_minimal(), "600176")
        m = re.search(r'id="klineChart"[^>]*data-opts="([^"]*)"', html)
        assert m is not None, "缺少 klineChart data-echart div"
        opts = json.loads(_h.unescape(m.group(1)))
        assert any(s.get("type") == "candlestick" for s in opts["series"])
        assert {"MA5", "MA20", "MA60"} <= {s["name"] for s in opts["series"]}
        assert any(s["name"] == "MACD" for s in opts["series"])
        # K 线图注入在均线排列 card 之后
        assert html.index("id=\"klineChart\"") > html.index("均线排列")

    def test_kline_chart_insufficient_placeholder(self):
        """15 行 fixture → K 线 options None → 占位注记，不渲染空图壳、不崩。"""
        from lib.render import render_html

        html = render_html(collection_kline_insufficient(), "600176")
        assert "K 线序列不足" in html
        assert 'id="klineChart"' not in html


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


class TestAnalysisSection:
    def test_html_analysis_sections_rendered(self):
        from lib.render_html import render_html

        analysis = [{
            "module": "events", "title": "事件分层分析",
            "facts_md": "近 30 日公告 3 条 [来源: akshare 公告]",
            "analysis_md": "**观察**：回购成交价上限距现价 18%。（证据 B）",
            "evidence_tag": "B", "position": "events",
        }]
        html = render_html(collection_v2_minimal(), "600176", analysis=analysis)
        assert "事件分层分析" in html
        assert "回购成交价上限距现价 18%" in html
        assert "data-module=\"events\"" in html

    def test_html_analysis_missing_facts_placeholder_kept(self):
        from lib.render_html import render_html

        html = render_html(collection_v2_minimal(), "600176")  # 无 analysis
        assert "待 Claude" in html  # 占位保留（F0-3 兜底：未填占位 qc FAIL）
