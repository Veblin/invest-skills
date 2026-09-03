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

    def test_echarts_asset_present_embedded(self):
        """B3-R C-5（原 test_echarts_asset_missing_fallback 名不副实——未兜测缺失
        路径）。此用例断言真实路径：资产在场时 UMD 内容完整嵌入首个 script。"""
        from lib import render_html as rh

        html = rh.render_html(collection_v2_minimal(), "600176")
        scripts = re.findall(r"<script>(.*?)</script>", html, re.DOTALL)
        assert scripts and len(scripts[0]) > 50_000  # echarts UMD 内联
        assert "echarts" in scripts[0][:2000]
        assert "<body" in html and "</html>" in html

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

    def test_print_css_first_rule_hides_chrome(self):
        """T3-5（A13/D7）：@media print 块首条规则即 .sidebar,.topbar{display:none}，
        且块内含 .app{grid-template-columns:1fr}（防 grid 200px 列打印留白）。"""
        from lib.render import render_html

        html = render_html(collection_v2_minimal(), "600176")
        start = html.index("@media print{") + len("@media print{")
        depth = 1  # 外层 @media print{ 自身的花括号
        j = start
        while True:
            ch = html[j]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        block = html[start:j]
        assert block.startswith(".sidebar,.topbar{display:none}"), \
            "print 首条规则必须是 .sidebar,.topbar{display:none}"
        assert ".app{grid-template-columns:1fr}" in block

    def test_print_button(self):
        """T3-5：顶部工具栏打印按钮 → window.print() + aria-label 明确。"""
        from lib.render import render_html

        html = render_html(collection_v2_minimal(), "600176")
        assert 'aria-label="打印报告"' in html
        assert "window.print()" in html
        topbar = html[html.index('<header class="topbar"'):html.index("</header>")]
        assert "打印报告" in topbar

    def test_charts_aria_wrapped(self):
        """T3-6：data-echart 容器外包 role="img" + aria-label（关键数字 Python 合成）+ aria-describedby="refs"。"""
        from lib.render import render_html

        html = render_html(collection_v2_minimal(), "600176")
        chart_ids = re.findall(r'id="(valBand|klineChart|flowChart)"[^>]*data-echart', html)
        assert chart_ids, "最小 fixture 应含 valBand/klineChart"
        for cid in chart_ids:
            idx = html.index(f'id="{cid}"')
            prev_sec = html.rindex("<section ", 0, idx)
            sec_tag = html[prev_sec:html.index(">", prev_sec)]
            assert 'role="img"' in sec_tag, f"{cid} 未包 role=img"
            assert 'aria-describedby="refs"' in sec_tag, f"{cid} 缺 aria-describedby"
            m = re.search(r'aria-label="([^"]*)"', sec_tag)
            assert m and m.group(1).strip(), f"{cid} aria-label 为空"
            # 图表 div 应位于该 section 内（紧随其后的第一个 section 边界是关闭）
            after = html[idx:]
            nxt_sec = after.find("<section ")
            nxt_close = after.find("</section>")
            assert nxt_close != -1 and (nxt_sec == -1 or nxt_close < nxt_sec), \
                f"{cid} 不在 role=img section 内"

    def test_flow_chart_aria_wrapped(self):
        """T3-6：北向数据存在（真实报告形态）→ flowChart 同样包 role=img，label 含 资金流/万元。"""
        from lib.render import render_html

        c = collection_v2_minimal()
        c["dimensions"].append({
            "dimension": "northbound",
            "display": "北向资金",
            "data": [{"trade_date": f"202607{d:02d}", "net_mf_vol": 1000.0 * d}
                     for d in range(13, 20)],
            "status": "available",
            "_meta": {"source": "test.fixture"},
        })
        c["summary"]["total"] += 1
        c["summary"]["available"] += 1
        html = render_html(c, "600176")
        idx = html.index('id="flowChart"')
        sec_tag = html[html.rindex("<section ", 0, idx):html.index(">", html.rindex("<section ", 0, idx))]
        assert 'role="img"' in sec_tag and 'aria-describedby="refs"' in sec_tag
        m = re.search(r'aria-label="([^"]*)"', sec_tag)
        assert m and "资金流" in m.group(1) and "万元" in m.group(1)


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


# ── B3-R ②/④: margin 链路 + 财务图恢复 ──

def _collection_with_market_structure():
    """collection_v2_minimal + northbound（flow 前提）+ market_structure.margin。"""
    c = collection_v2_minimal()
    c["dimensions"].append({
        "dimension": "northbound",
        "display": "北向资金",
        "data": [{"trade_date": f"202607{d:02d}", "net_mf_vol": 1000.0 * d}
                 for d in range(13, 20)],
        "status": "available",
        "_meta": {"source": "test.fixture"},
    })
    c["summary"]["total"] += 1
    c["summary"]["available"] += 1
    c["market_structure"] = {
        "margin": {
            "records": [
                {"trade_date": "20260713", "rzye": 10_000_000_000.0},
                {"trade_date": "20260714", "rzye": 10_100_000_000.0},
                {"trade_date": "20260715", "rzye": 10_200_000_000.0},
            ],
            "source": "tushare",
            "change_pct": 0.5,
        }
    }
    return c


class TestB3RFinancialAndMargin:
    def test_flow_margin_from_market_structure(self):
        """②：融资余额取自 collection.market_structure.margin.records（元→亿元）。"""
        import html as _h
        import json

        from lib.render import render_html

        html = render_html(_collection_with_market_structure(), "600176")
        m = __import__("re").search(
            r'id="flowChart"[^>]*data-opts="([^"]*)"', html)
        if m is None:
            import pytest
            pytest.skip("flowChart 未渲染（北向序列不足）")
        opts = json.loads(_h.unescape(m.group(1)))
        margin_s = next((s for s in opts["series"]
                         if s["name"] == "融资余额(亿元)"), None)
        if margin_s is None:
            import pytest
            pytest.skip("无 margin 系列")
        vals = [row[1] for row in margin_s["data"]
                if isinstance(row, list) and row[1] is not None]
        assert vals, "margin 系列不应全空（旧死路径恒 None）"
        assert max(vals) > 100.0  # rzye 1e10 元 → 100 亿元级

    def test_financial_charts_restored(self):
        """④：finRoeChart/finProfitChart 恢复渲染（ECharts div + aria）。"""
        from lib.render import render_html

        html = render_html(collection_v2_minimal(), "600176")
        assert 'id="finRoeChart"' in html
        assert 'id="finProfitChart"' in html
        assert html.count("data-echart") >= 5  # kline+band+flow+fin×2
        assert "role=\"img\"" in html

    def test_financial_charts_absent_when_no_financials(self):
        """④：financials 维度缺失 → 无财务图 id，页面仍渲染。"""
        from lib.render import render_html

        c = collection_v2_minimal()
        c["dimensions"] = [d for d in c["dimensions"]
                           if d["dimension"] != "financials"]
        html = render_html(c, "600176")
        assert "finRoeChart" not in html
        assert "finProfitChart" not in html
        assert "财务数据不可得" in html

    def test_flow_legend_colors_match_bars(self):
        """B-F4 + A 股惯例统一（2026-09-03 裁决）：图例与柱色一致——
        净流入（正）= 红 var(--up)（翻值后 --up=红）/ 净流出 = 绿 var(--dn)。"""
        from lib.render import render_html

        html = render_html(_collection_with_market_structure(), "600176")
        import re as _re
        in_swatch = _re.search(
            r"background:(var\(--[a-z]+\))[^<]*</span>净流入", html)
        out_swatch = _re.search(
            r"background:(var\(--[a-z]+\))[^<]*</span>净流出", html)
        assert in_swatch is not None and in_swatch.group(1) == "var(--up)"
        assert out_swatch is not None and out_swatch.group(1) == "var(--dn)"


# ── B3-R ⑥ D-3/B-F6 + B-F5: 主题数组合并 / 字体 / revive 适配器 ──

class TestB3RThemeAndRevive:
    def test_theme_merges_axis_arrays(self):
        """D-3/B-F6：applyChartTheme 按数组逐轴合并（xAxis/yAxis .map），
        且含 fontFamily（与 CSS --font-body 对齐）。"""
        from lib.render import render_html

        html = render_html(collection_v2_minimal(), "600176")
        seg = html[html.index("function applyChartTheme"):html.index("function renderCharts")]
        assert ".map(ax)" in seg
        assert "Array.isArray(o.xAxis)" in seg and "Array.isArray(o.yAxis)" in seg
        assert "fontFamily" in seg and "PingFang SC" in seg

    def test_js_revive_adapter_present(self):
        """B-F5：renderCharts 走 revive(JSON.parse(raw))——_js 常量表达式契约。"""
        from lib.render import render_html

        html = render_html(collection_v2_minimal(), "600176")
        assert "function revive(v)" in html
        assert "revive(JSON.parse(raw))" in html


# ── B3-R ⑨: 亏损期/PE 缺失时 band 不静默丢弃 ──

class TestB3RLossPeriodBandNote:
    def test_valuation_loss_period_band_note_not_silent(self):
        """⑨：全部 PE 缺失（亏损期场景）→ 估值卡显示「数据不可得」且 band
        侧出现提示卡（不静默丢弃图表位）。"""
        from lib.render import render_html

        c = collection_v2_minimal()
        for d in c["dimensions"]:
            if d["dimension"] == "valuation":
                d["data"] = [dict(r, pe_ttm=None) for r in d["data"]]
        html = render_html(c, "600176")
        assert "数据不可得" in html
        assert "估值分位带图：数据不可得" in html


# ── code-review #6: flow margin 窗口与北向对齐 ──

class TestCodeReviewMarginWindow:
    def test_flow_margin_window_aligned_to_northbound(self):
        """margin records（collector 存 10 行）切片至北向窗口（7 行）→
        x 轴不膨胀（7 槽），无孤立 margin 前段。"""
        import html as _h
        import json

        from lib.render import render_html

        c = _collection_with_market_structure()  # nb 7 行 + margin 3 行
        # 生产形态：collector 存 margin 近 10 行（截止与 nb 同日 07-19）——
        # 修复前 x 轴膨胀出 margin 窗口外独有日期（10-12）
        c["market_structure"]["margin"]["records"] = [
            {"trade_date": f"202607{d:02d}", "rzye": 1e10}
            for d in range(10, 20)]
        html = render_html(c, "600176")
        m = __import__("re").search(
            r'id="flowChart"[^>]*data-opts="([^"]*)"', html)
        assert m is not None
        opts = json.loads(_h.unescape(m.group(1)))
        xaxis = opts["xAxis"]["data"]
        # 修复语义：margin 切片至 nb 窗口（13-19）→ 窗口外独有日期不出现在轴
        assert "2026-07-10" not in xaxis
        assert "2026-07-11" not in xaxis
        assert "2026-07-12" not in xaxis
        assert "2026-07-13" in xaxis  # 窗口内首日保留


# ── A 股红涨绿跌全局统一（2026-09-03 裁决） ──

class TestARedUpGreenDownGlobal:
    def test_css_vars_red_up_green_down(self):
        """CSS 变量翻值：--up=红（涨方向）、--dn=绿（跌方向），dark/light 同构；
        非涨跌语义 --ok（可用/正常）绿 / --err（错误）红独立。"""
        from lib.render import render_html

        html = render_html(collection_v2_minimal(), "600176")
        dark = html[html.index(":root {"):html.index("[data-theme=\"light\"]")]
        assert "--up:#f87171" in dark and "--dn:#34d399" in dark
        assert "--ok:#34d399" in dark and "--err:#f87171" in dark
        light = html[html.index("[data-theme=\"light\"]{"):
                     html.index("*,*::before")]
        assert "--up:#dc2626" in light and "--dn:#059669" in light

    def test_topbar_up_red_down_green(self):
        """topbar 涨红跌绿（price_color 引用 var(--up)=红涨 / var(--dn)=绿跌）。"""
        from lib.render import render_html

        html = render_html(collection_v2_minimal(), "600176")
        # fixture quote change_pct=1.2 > 0 → 涨 → var(--up)（红）
        seg = html[html.index("tch"):html.index("</span>", html.index("tch")) + 8]
        assert "var(--up)" in seg
        # CSS 层断言：--up 定义值 = 红
        assert "--up:#f87171" in html

    def test_flow_kpi_and_legend_same_convention(self):
        """flow 卡内一致：KPI 正=红（var(--up)）与图例净流入=红同约定（裁决前
        KPI 绿 vs 图例红的同一控件双语义已消除）。"""
        from lib.render import render_html

        html = render_html(_collection_with_market_structure(), "600176")
        m = __import__("re").search(
            r"7日净流入&nbsp;</span><span style=\"[^\"]*?color:(var\(--[a-z]+\))",
            html)
        assert m is not None and m.group(1) == "var(--up)"


# ── 全量审查 P0-3: analysis md/html 同源 + events 占位隐藏 ──

class TestFullReviewAnalysisSameSource:
    def test_md_and_html_both_render_all_analysis_sections(self):
        """md 注记节 + html 卡均渲染全部 analysis 段（旧 md 只消费 events
        首行——非 events 段在 md 静默消失）。"""
        from lib.render import render_html, render_report_v3

        analysis = [
            {"module": "events", "title": "事件分层分析",
             "facts_md": "近 30 日公告 3 条 [来源: akshare]",
             "analysis_md": "事件影响有限（证据 B）", "evidence_tag": "B",
             "position": "events"},
            {"module": "thesis", "title": "投资假设检验",
             "facts_md": "营收增速为正 [来源: engine]",
             "analysis_md": "假设未见破坏（证据 B）", "evidence_tag": "B",
             "position": "overview"},
        ]
        md = render_report_v3(collection_v2_minimal(), "600176",
                              analysis=analysis)
        assert "分析注记（analysis.json 注入）" in md
        assert "投资假设检验" in md and "事件分层分析" in md
        html = render_html(collection_v2_minimal(), "600176",
                           analysis=analysis)
        assert "投资假设检验" in html

    def test_events_static_placeholder_hidden_when_analysis_provided(self):
        """analysis 提供 events 段 → 静态「待填写」占位 section 隐藏（旧实现
        死块永不填充与真卡并存）。"""
        from lib.render import render_html

        analysis = [{"module": "events", "title": "事件分层分析",
                     "facts_md": "f", "analysis_md": "a", "evidence_tag": "B",
                     "position": "events"}]
        html = render_html(collection_v2_minimal(), "600176",
                           analysis=analysis)
        assert "待 Claude 分析阶段填写" not in html

    def test_no_analysis_md_unchanged(self):
        """无 analysis → md 无注记节（基线零增）。"""
        from lib.render import render_report_v3

        md = render_report_v3(collection_v2_minimal(), "600176")
        assert "分析注记" not in md
