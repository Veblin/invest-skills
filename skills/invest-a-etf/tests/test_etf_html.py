"""etf_html 渲染器测试（离线 fixture payload 驱动，D13：断言于渲染产物）。

fixture 脱敏（无真实分析内容），字段形状对齐引擎 JSON（2026-08-29 真实
report --json 复核）。合规检查：无 CDN / .disc 剥离后无禁止词 / 风险提示
先于免责声明 / md 原样嵌入。
"""

import re
from pathlib import Path

import pytest

from etf_html import render_etf_html

# ── 离线 fixture（字段形状与引擎 JSON 一致；数字均为占位） ──

MD_TEXT = """# 测试（600000）研究备忘

> ⚠️ 此为渲染器离线测试 md，不含分析内容。**渲染唯一串**用于验证原样嵌入。

## 1. 产品快照

| 指标 | 值 | 来源 |
|------|-----|------|
| 最新价 | 1.000（占位） | quote.price |

仅供参考，不构成投资建议。概率为分析情景权重（假设≠预测）。

## 风险声明（尾部）

> ⚠️ 本备忘录为测试文本，原文保留。
"""


def _fixture_payload() -> dict:
    return {
        "skill": "invest-a-etf",
        "generated_at": "2026-08-29T00:00:00+08:00",
        "symbol": "515050",
        "index_code": "931079",
        "profile": {
            "symbol": "515050",
            "category": {"category": "行业", "label": "通信ETF", "source": "占位"},
            "index_pe": 54.58,
            "index_pe_pct": 51.5,
            "index_pe_status": "mapped",
            "index_pe_note": "来源: csindex 931079，单窗 20 条历史",
            "industry_pe": None,
            "industry_pe_note": None,
            "valuation_guide": {"industry": "通信", "sub_sector": "光模块",
                                "primary": "跟踪运营商 CAPEX", "secondary": "光模块出口增速",
                                "pe_timing": False},
            "industry_allocation": [{"industry": "制造业", "pct": 96.93}],
            "industry_allocation_date": "2026-06-30",
            "premium_discount": -0.02,
            "aum": 180.26,
            "tracking_error": None,
            "tracking_error_note": "跟踪误差需 ETF 净值与指数点位序列对比，当前引擎未实现；请勿填写估算数字",
            "hedge_coverage": {"index": "931079", "futures": None, "options": None},
            "flags": ["⚠️ 该 ETF 无可用的期货/期权对冲工具"],
            "_errors": [],
        },
        "quote": {"symbol": "515050", "price": 1.036, "change_pct": -1.33,
                  "volume": 8760051.0, "amount": 924429262.0,
                  "premium_discount": -0.02, "status": "available", "_error": None},
        "kline": {"symbol": "515050", "nav_rows": 250, "latest_nav": 1.0344,
                  "latest_nav_date": "2026-08-28", "volatility_annualized": 32.5,
                  "adj_applied": True, "adj_note": "复权=等比前复权",
                  "rsi": 41.2, "rsi_period": 14, "rsi_note": "",
                  "ma20": 1.0276, "ma60": 1.1409,
                  "index_ma20": None, "index_ma60": None,
                  "boll_upper": 1.1128, "boll_mid": 1.0276, "boll_lower": 0.9424,
                  "derived": {"nav_vs_ma20_pct": 0.66, "nav_vs_ma60_pct": -9.33,
                              "nav_vs_boll_mid_pct": 0.66, "boll_position_pct": 53.99,
                              "nav_to_boll_lower_pct": 9.76, "nav_to_boll_upper_pct": -7.05,
                              "boll_bandwidth_pct": 18.08, "daily_volatility_pct": 4.24},
                  "status": "available", "_error": None, "volatility_window": 250},
        "share_history": {"symbol": "515050", "available": True,
                          "date_range": "2026-08-01 ~ 2026-08-28",
                          "summary": {"total_flow_est": -7.17, "avg_daily_flow_est": -0.38,
                                      "trend": "🔴 持续净流出", "row_count": 20,
                                      "recent_flow_est": -0.15, "recent_flow_days": 6,
                                      "avg_amount_e": 11.26, "max_amount_e": 19.36,
                                      "share_total_change": -56800.0, "inflow_days": 5,
                                      "outflow_days": 14, "flat_days": 0,
                                      "inflow_sum_est": 9.08, "outflow_sum_est": -16.25},
                          "rows": [{"date": "2026-08-24", "open": 1.03, "high": 1.04,
                                    "low": 1.02, "close": 1.03, "pct_chg": 0.5,
                                    "amount": 10.5, "turnover_rate": 2.1,
                                    "share_change": -1000.0, "flow_est": -0.5,
                                    "direction": "净流出"},
                                   {"date": "2026-08-25", "open": 1.03, "high": 1.05,
                                    "low": 1.03, "close": 1.04, "pct_chg": 0.9,
                                    "amount": 12.0, "turnover_rate": 2.4,
                                    "share_change": 500.0, "flow_est": 0.25,
                                    "direction": "净流入"},
                                   {"date": "2026-08-26", "open": None, "high": None,
                                    "low": None, "close": 1.05, "pct_chg": None,
                                    "amount": 9.0, "turnover_rate": None,
                                    "share_change": None, "flow_est": None,
                                    "direction": None}]},
        "history": {"history": {"symbol": "515050", "source": "nav", "status": "available",
                                "rows": [{"date": "2025-08-14", "nav": 0.499, "change_pct": -2.21},
                                         {"date": "2026-06-25", "nav": 1.4362, "change_pct": 5.1},
                                         {"date": "2026-08-28", "nav": 1.0344, "change_pct": -1.41}],
                                "requested_days": 250, "note": ""},
                    "stats": {"rows": 253, "date_range": "2025-08-14 ~ 2026-08-28",
                              "annual_high": {"date": "2026-06-25", "close": 1.4362},
                              "annual_low": {"date": "2025-08-14", "close": 0.499},
                              "max_drawdown": {"peak_date": "2026-06-25", "peak_close": 1.4362,
                                               "trough_date": "2026-07-30", "trough_close": 0.8831,
                                               "drawdown_pct": -38.51},
                              "big_move_days": [{"date": "2026-06-15", "change_pct": 6.87},
                                                {"date": "2026-07-17", "change_pct": -9.31}],
                              "big_move_days_count": 2, "big_move_up_days": 1,
                              "big_move_down_days": 1, "ma20": 1.0276, "ma60": 1.1409,
                              "ma120": 1.0547, "current_vs_high_pct": -27.98,
                              "current_vs_low_pct": 107.29, "dist_to_ytd_low_pct": -27.98,
                              "atr14": 0.031, "atr14_pct": 3.0, "atr14_note": ""}},
        "events": {"available": False,
                   "note": "无事件文件（515050.json），跳过（不阻断）",
                   "rows": None, "aligned": None},
        "playbook": {"available": True, "vol_60d_daily_pct": 4.24, "vol_source": "kline.derived",
                     "drawdown_levels": [{"sigma_multiple": 1.0, "level_pct": -4.24,
                                          "verification_depth": "例行记录"},
                                         {"sigma_multiple": None, "level_pct": -12.72,
                                          "verification_depth": "归因核查"}],
                     "checklist": ["步骤一：对照引擎统计核实重大变化",
                                   "步骤二：检验关键假设与证伪条件"],
                     "disclaimer": "三步核查为研究流程规则（核验深度筛选），非操作阈值，不构成投资建议。",
                     "note": None},
        "disclaimer": "占位",
        # ── cmd_html 合并键 ──
        "holdings": {"status": "ok", "report_date": "2026-06-30", "quarter": "2026年2季度",
                     "rows": [{"code": "300502", "name": "新易盛", "pct": 9.91,
                               "shares": 408.17, "amount": 247760.65}],
                     "top1_pct": 9.91, "top5_sum_pct": 41.79, "top10_sum_pct": 62.9,
                     "clusters": [{"cluster": "光模块/光器件", "sum_pct": 22.81,
                                   "members": [{"code": "300502", "name": "新易盛", "pct": 9.91}]}],
                     "note": "前十大持仓合计可能 <100%（非前十大未列）；子环节聚类由引擎按 HOLDINGS_CLUSTER_MAP 聚合",
                     "source": "天天基金(东财 FundArchivesDatas jjcc)"},
        "peers": {"available": True, "peer_source": "etf_to_sw_industry:801770 通信",
                  "flow": {"window_days": 20, "rows": [
                      {"symbol": "515050", "flow_20d_e": -7.17, "flow_5d_e": -0.15,
                       "trend": "🔴 持续净流出", "share_change_pct": -3.16,
                       "share_change_span": 18, "row_count": 20, "note": None}]},
                  "rs": {"rs_latest": 103.48, "rs_window_start": 103.98,
                         "rs_change": -0.51, "rs_change_pct": -0.49, "n": 73,
                         "rank_20d": {"rank": 2, "total": 2, "window": "20 个交易日",
                                      "returns": {"515050": 14.02, "515880": 14.58}}},
                  "names": {"515050": "通信ETF"}, "notes": []},
        "sector_flow": {"available": True, "sw_code": "801770", "sw_name": "通信",
                        "as_of": "20260828",
                        "industries": [{"industry": "通信设备", "net_1d": -47.17,
                                        "net_3d": 48.98, "net_5d": 61.55, "net_10d": 117.55,
                                        "chg_10d": 4.6, "trend_label": "持续净流入",
                                        "trend_detail": "近端加速（日均强度 r=1.67）",
                                        "trend_5d": 32.17, "turn_5d": "方向未变",
                                        "trend_span_days": 14}],
                        "history_days": 8, "notes": []},
    }


@pytest.fixture(scope="module")
def html_text() -> str:
    return render_etf_html(_fixture_payload(), md_text=MD_TEXT)


def test_sections_all_present(html_text):
    for sec in ("overview", "valuation", "holdings", "quality", "history", "flows",
                "research", "refs"):
        assert f'id="{sec}"' in html_text


def test_chart_js_vendored(html_text):
    assert "Chart.js v4.4.0" in html_text  # 本地资产内联（无 CDN）


def test_size_large(html_text):
    assert len(html_text) > 100_000  # chart.umd 内联 + md 嵌入


def test_script_shape(html_text):
    assert "function renderCharts(){" in html_text
    assert "const report=" in html_text
    assert "{{" not in html_text  # 字符串拼接，无 f-string 花括号残留
    assert "{%" not in html_text


def test_risk_banner_before_disclaimer(html_text):
    assert html_text.index("风险提示") < html_text.index("免责声明")


def test_canvases(html_text):
    # clustersChart 已移除：聚类占比与 gauge 横条重复，保留横条（簇名+数值并列可直读）
    for cid in ("navChart", "historyChart", "shareFlowChart", "sectorFlowChart"):
        assert f'id="{cid}"' in html_text
    assert 'id="clustersChart"' not in html_text


def test_data_direct_mapping(html_text):
    # 引擎字段直映：quote.price=1.036 → HTML 出现；None 字段 → '—' 不崩溃（D1）
    assert "1.036" in html_text
    assert "—" in html_text
    assert "9.91" in html_text  # holdings.top1_pct
    assert "-38.51" in html_text  # stats.max_drawdown.drawdown_pct


def test_md_embedded_verbatim(html_text):
    assert "渲染唯一串" in html_text
    assert "风险声明（尾部）" in html_text
    assert "表头" not in html_text.replace("｜", "")  # md 无改动余地说明：检查标题 id 级嵌入
    assert '<article class="md-body">' in html_text


def test_no_cdn(html_text):
    assert "<script src" not in html_text
    assert 'href="http' not in html_text
    assert "<link" not in html_text


# 禁止词表（report-conventions.md §3.1/§3.2 高频项；仅校验 .disc 之外——措辞禁令
# 不适用于免责/风险声明块，后者本身在说"不构成"）
_FORBIDDEN = [
    "建议买入", "建议卖出", "建议持有", "建议加仓", "建议减仓", "建议止损",
    "止损", "目标价", "崩盘", "极度高估", "极度低估", "全线创新高", "整体创新高",
    "长期方向不变", "理论上无顶部", "强烈信号", "一致看多", "安全边际吃尽",
    "无视风险", "满仓", "梭哈", "重仓出击",
]


def test_no_forbidden_words_outside_disc(html_text):
    # 剥离 .disc 风险/免责块（合规豁免区），其余文本不得含禁止词
    stripped = re.sub(r'<div class="disc[^>]*>.*?</div>', "", html_text, flags=re.S)
    assert "<div class=\"disc" not in stripped
    for w in _FORBIDDEN:
        assert w not in stripped, f"禁止词出现在非免责区: {w}"


def test_md_missing_null_safe():
    # md_text=None：研究节为空但整体不崩溃
    html = render_etf_html(_fixture_payload())
    assert '<article class="md-body">' in html
    assert "研究备忘" in html


def test_false_placeholder_sections():
    # holdings/peers/sector_flow 不可得时（available False）各节占位不崩溃
    payload = _fixture_payload()
    payload["holdings"] = {"available": False, "note": "持仓数据不可得", "status": "err"}
    payload["peers"] = {"available": False, "notes": ["同赛道不可用"]}
    payload["sector_flow"] = {"available": False, "notes": ["行业资金流不可得"]}
    payload["share_history"] = {"available": False, "note": "份额数据不可得"}
    html = render_etf_html(payload, md_text=MD_TEXT)
    assert "持仓数据不可得" in html
    assert "同赛道不可用" in html
    assert "行业资金流不可得" in html
