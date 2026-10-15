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

## 目录

- [产品快照](#1-产品快照)

## 1. 产品快照

| 指标 | 值 | 来源 |
|------|-----|------|
| 最新价 | 1.000（占位） | quote.price |

仅供参考，不构成投资建议。概率为分析情景权重（假设≠预测）。

## 2. 持仓透视（R12，`holdings`）

### 表内细标题

占位

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
            "hedge_coverage": {"index": "931079", "futures": None, "options": None,
                               "coverage": "none"},
            "flags": ["⚠️ 静态表未收录该 ETF 的期货/期权对冲工具（未核；不表示不存在）"],
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
    "无视风险", "满仓", "梭哈", "重仓出击", "抄底", "逃顶", "看涨", "看跌",
]


def test_no_forbidden_words_outside_disc(html_text):
    # 剥离 .disc 风险/免责块（合规豁免区），其余文本不得含禁止词
    stripped = re.sub(r'<div class="disc[^>]*>.*?</div>', "", html_text, flags=re.S)
    assert "<div class=\"disc" not in stripped
    for w in _FORBIDDEN:
        assert w not in stripped, f"禁止词出现在非免责区: {w}"


def test_md_missing_null_safe():
    # md_text=None：研究节为空、无 h2 子项但整体不崩溃
    html = render_etf_html(_fixture_payload())
    assert '<article class="md-body">' in html
    assert "研究备忘" in html
    assert 'class="sbi sbi-sub"' not in html  # 无 md → 无子项


# ── 头部摘要 hero ──


def _hero_slice(html: str) -> str:
    start = html.index('id="hero-summary"')
    end = html.index('<section id="overview"')
    return html[start:end]


def test_summary_hero_present(html_text):
    hero = _hero_slice(html_text)
    assert 'id="hero-summary"' in html_text
    # 8 格关键数字直映 fixture 值（价格/AUM/PE+历史位置/回撤/份额流/RSI）
    for v in ("1.036", "180.26", "54.58x", "历史位置 51.5%", "-38.5", "-7.17", "41.2"):
        assert v in hero, f"hero 缺字段值: {v}"


def _strip_tags(html: str) -> str:
    return re.sub(r"<[^>]+>", "", html)


def test_summary_ks_field_sources(html_text):
    # 2026-10-13 读者呈现：引擎字段路径不再作可见副标题，落到 data-source 属性
    # （可追溯，仍完整保留）；可见正文不得出现裸字段路径。
    hero = _hero_slice(html_text)
    attrs = re.findall(r'data-source="([^"]*)"', hero)
    for k in ("quote.price", "profile.aum", "kline.volatility_annualized",
              "history.stats.max_drawdown.drawdown_pct",
              "share_history.summary.total_flow_est", "profile.index_pe_pct"):
        assert any(k in a for a in attrs), f"hero 缺来源绑定: {k}"
    visible = _strip_tags(hero)
    for k in ("quote.", "profile.", "kline.", "history.", "share_history."):
        assert k not in visible, f"可见正文出现内部字段路径: {k}"


def test_summary_points_no_direction_words(html_text):
    hero = _hero_slice(html_text)
    # R7：位置句按 history 序列口径（净值位置），不再写「价格位置：最新价」
    # （交易报价 ≠ 净值序列距离）。R11：本 fixture 的 history 块无 adj_applied
    # （kline.adj_applied=True 属另一链路，不在此消费）→ 复权状态未知，不得标复权。
    assert "净值位置：" in hero and "净值位置：净值（复权状态未知）" in hero
    assert "复权净值" not in hero and "未复权净值" not in hero
    assert "份额流：" in hero
    assert "对冲覆盖：" not in hero  # R16：未核对冲线索不进读者展示
    assert "价格位置：" not in hero
    # 状态要点为引擎字段直映，禁用方向性措辞
    for w in ("看涨", "看跌", "抄底", "逃顶", "低估", "高估", "超卖", "超买"):
        assert w not in hero, f"hero 状态要点出现方向词: {w}"


def test_summary_hero_tracked_index_prefers_identity():
    # 2026-10-13（B03/B06 消费者缺陷）：**已核身份**（verification=verified_original）
    # 下跟踪名称取 identity.tracked_index，不得被静态 hedge_coverage.index 覆盖——
    # 两者可不一致（真实 510880 现场：静态「红利指数」vs 身份「上证红利指数」）。
    payload = _fixture_payload()
    payload["profile"]["identity"] = {
        "tracked_index": {"name": "上证红利指数", "code": "000015"},
        "verification": {"status": "verified_original", "note": "原文核验"}}
    payload["profile"]["hedge_coverage"] = {
        "index": "红利指数", "futures": None, "options": None, "coverage": "none",
        "verification": {"status": "static_map_only", "note": "静态映射，未逐条原文核验"}}
    html = render_etf_html(payload, md_text=MD_TEXT)
    hero = _hero_slice(html)
    assert "跟踪 上证红利指数" in hero
    assert "跟踪 红利指数" not in hero
    assert "标的待核" not in hero


def test_summary_hero_unverified_identity_name_not_affirmed():
    # 反例（实际渲染）：identity.tracked_index 有名称，但身份 verification 不是
    # verified_original（此处完全缺 verification）→ 不得肯定「跟踪 X」，
    # 名称只能作为待核映射线索。
    payload = _fixture_payload()
    payload["profile"]["identity"] = {
        "tracked_index": {"name": "上证红利指数", "code": "000015"}}
    payload["profile"]["hedge_coverage"] = {
        "index": "红利指数", "futures": None, "options": None, "coverage": "none",
        "verification": {"status": "static_map_only", "note": "静态映射，未逐条原文核验"}}
    html = render_etf_html(payload, md_text=MD_TEXT)
    hero = _hero_slice(html)
    assert "跟踪 上证红利指数" not in hero
    assert "跟踪 红利指数" not in hero
    assert "标的待核：上证红利指数" in hero


def test_summary_hero_tracked_index_falls_back_to_hedge():
    # 反例（实际渲染）：identity 缺失，仅剩静态 hedge_coverage.index（static_map_only）
    # → 不得肯定「跟踪 931079」；输出待核映射线索。
    payload = _fixture_payload()
    payload["profile"].pop("identity", None)
    html = render_etf_html(payload, md_text=MD_TEXT)
    hero = _hero_slice(html)
    assert "跟踪 931079" not in hero
    assert "标的待核：931079" in hero


# ── 研究备忘 h2 子目录 ──


def test_sidebar_subitems_present(html_text):
    h2_ids = set(re.findall(r'<h2 id="([^"]+)">', html_text))
    sub_hrefs = set(re.findall(r'class="sbi sbi-sub" href="#([^"]+)"', html_text))
    assert sub_hrefs, "无子目录项"
    # 子目录 = 渲染 h2 全集 − 「## 目录」（md 自带，非章节）
    assert sub_hrefs == h2_ids - {"目录"}, (sub_hrefs, h2_ids)
    assert 'href="#1-产品快照"' in html_text
    assert 'href="#风险声明尾部"' in html_text


def test_sidebar_subitems_exclude_dir_h3(html_text):
    assert 'href="#目录"' not in html_text   # md 自带「## 目录」不进侧栏
    assert 'href="#表内细标题"' not in html_text  # 只收 h2，h3 不纳入


def test_sidebar_subitem_long_title(html_text):
    # 长标题：行内 code 剥除后上 title 全文；CSS ellipsis 截断
    assert 'title="2. 持仓透视（R12，holdings）"' in html_text
    assert "text-overflow:ellipsis" in html_text


# ── hero 降级 ──


def test_summary_degrade_all_none():
    payload = _fixture_payload()
    payload["kline"]["rsi"] = None
    payload["kline"]["rsi_period"] = None
    payload["share_history"] = {"available": False, "note": "份额数据不可得"}
    payload["history"] = {"history": {"status": "available", "rows": []}}
    html = render_etf_html(payload, md_text=MD_TEXT)
    assert 'id="hero-summary"' in html  # 不崩
    hero = _hero_slice(html)
    assert "—" in hero  # 缺失字段降级为 '—'
    # 任一引用字段缺失 → 整句丢弃，无空模板句
    assert "净值位置：" not in hero and "历史收盘位置：" not in hero
    assert "份额流：" not in hero
    assert "波动/回撤：" not in hero
    assert "对冲覆盖：" not in hero  # R16：未核对冲不假设「有/无」
    assert "RSI(" not in hero or "RSI(14)" not in hero  # rsi_period=None → 不显示周期


def test_script_observer_extended(html_text):
    assert 'section[id], article.md-body h2[id]' in html_text


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


# ── A3/A4 消费链（Codex 阻塞项）：窗口标签与旧 payload 兼容 ──

def test_flow_labels_use_actual_window_days_120(tmp_path):
    """120 日报告的首屏/要点/总结不得标「20 日」；标题与共同起止用实际值。"""
    import copy

    payload = copy.deepcopy(_fixture_payload())
    summ = payload["share_history"]["summary"]
    summ["window_days"] = 120
    summ["flow_window"] = {"start": "20260410", "end": "20261009",
                           "segments": 120, "trading_days": 121,
                           "missing_share_days": 0, "confirmable": True}
    summ["total_flow_est"] = -24.53
    payload["peers"]["flow"]["window_days"] = 120
    html = render_etf_html(payload, md_text=MD_TEXT)
    assert "120 日份额流（亿）" in html
    # 「20 日份额流」是「120 日份额流」的子串 → 用负向后顾断言独立标签不存在
    assert re.search(r"(?<!\d)20 日份额流", html) is None
    assert "共同起止 20260410~20261009" in html
    assert "份额资金流与收盘价（近 120 日" in html
    assert "120 日 -24.53 亿" in html          # 首屏要点
    assert re.search(r"(?<!\d)20 日 -", html) is None  # 不得残留旧窗口金额标签


def test_peers_flow_total_e_with_legacy_fallback():
    """新键优先；旧 payload（无 flow_total_e）回退 flow_20d_e；
    **键存在但为 None** 不得回退造数（显示占位）。"""
    import copy

    payload = copy.deepcopy(_fixture_payload())
    rows = payload["peers"]["flow"]["rows"]
    rows[0] = {"symbol": "515050", "flow_total_e": -8.88, "flow_20d_e": -7.17,
               "flow_5d_e": -0.15, "trend": "🔴", "share_change_pct": -3.16}
    rows.append({"symbol": "515790", "flow_total_e": None, "flow_20d_e": -1.11,
                 "flow_5d_e": None, "trend": None, "share_change_pct": None})
    html = render_etf_html(payload, md_text=MD_TEXT)
    # 只看渲染表格/正文（head），embedded payload 里的原始数值不算渲染结果
    head = html.split("<script", 1)[0]
    # 新键优先：-8.88 → 渲染 "-8.9"（若误用旧别名 -7.17 会显示 "-7.2"）
    assert "-8.9" in head and "-7.2" not in head
    # flow_total_e 键存在但 None → 不回退显示 -1.11（渲染占位 —）
    assert "-1.1" not in head


def test_peers_window_comparison_limitation_is_visible():
    payload = _fixture_payload()
    note = "组内比较受限：产品窗口起止不一致，极值/排名不可直接比较"
    payload["peers"]["flow"]["common_window_note"] = note
    html = render_etf_html(payload, md_text=MD_TEXT)
    assert note in html.split("<script", 1)[0]


def test_rs_pill_neutral_is_100():
    html = render_etf_html(_fixture_payload(), md_text=MD_TEXT)
    assert "100 = 与基准持平" in html
    assert "101 = 与基准持平" not in html
@pytest.mark.parametrize("note", ["未登记份额跳变，已排除相关区段", ""])
def test_unconfirmable_flow_is_partial_in_every_summary(tmp_path, note):
    payload = _fixture_payload()
    s = payload["share_history"]["summary"]
    s.update(total_flow_est=0, trend="资金面平稳", window_days=3,
             flow_window={"start": "2026-04-17", "end": "2026-04-22",
                          "confirmable": False, "note": note})
    html = render_etf_html(payload, md_text=MD_TEXT)
    (tmp_path / "partial.html").write_text(html, encoding="utf-8")
    hero = _hero_slice(html)
    flows = html.split('<section id="flows">', 1)[1].split('</section>', 1)[0]
    for section in (hero, flows):
        assert "不可确认" in section
        assert "可确认段小计" in section
        assert "资金面平稳" not in section.split("同赛道资金流对比", 1)[0]
        if note:
            assert note in section


@pytest.mark.parametrize("verification", [None, {}, {"reached_levels": None},
                                         {"reached_levels": 0}])
@pytest.mark.parametrize("has_drawdown", [False, True])
def test_legacy_playbook_verification_is_unknown(tmp_path, verification, has_drawdown):
    payload = _fixture_payload()
    payload["playbook"].pop("current_drawdown", None)
    if has_drawdown:
        payload["playbook"]["current_drawdown"] = {"current_drawdown_pct": 0}
    payload["playbook"]["current_verification"] = verification
    html = render_etf_html(payload, md_text=MD_TEXT)
    (tmp_path / "legacy.html").write_text(html, encoding="utf-8")
    block = html.split('id="playbook"', 1)[1].split('</section>', 1)[0]
    if not has_drawdown:
        assert "当前回撤不可得" in block
    assert "当前核验深度：未知" in block
    assert "各档均未达" not in block


def test_computed_zero_drawdown_verification_is_not_unknown(tmp_path):
    from etf_playbook import current_drawdown, current_verification, drawdown_levels
    payload = _fixture_payload()
    pb = payload["playbook"]
    pb["current_drawdown"] = current_drawdown([
        {"date": "2026-04-20", "close": 1.0},
        {"date": "2026-04-21", "close": 1.1}])
    pb["drawdown_levels"] = drawdown_levels(pb["current_drawdown"]["current_drawdown_pct"])
    pb["current_verification"] = current_verification(pb["drawdown_levels"])
    html = render_etf_html(payload, md_text=MD_TEXT)
    block = html.split('id="playbook"', 1)[1].split('</section>', 1)[0]
    assert "各档均未达" in block
    assert "当前核验深度：未知" not in block


# ── R7（2026-10-11 复查）：hero 窗口位置句与 history 序列同口径 ──


def _r7_payload(*, source="nav", rows=None, high=None, high_date="2026-01-29",
                vs_high=-15.6, quote_price=1.421, adj_applied=None) -> dict:
    """R7/R11 fixture：`adj_applied` 显式为 None 时**不写入**该键（= 无留痕），
    期望「复权/未复权」标注的用例必须显式传 True/False（R11：不得让测试
    在字段缺失时仍断言已复权）。"""
    payload = _fixture_payload()
    payload["quote"]["price"] = quote_price
    if rows is None:
        rows = [{"date": "2025-09-26", "nav": 1.6147, "change_pct": -1.36},
                {"date": "2026-10-09", "nav": 1.4301, "change_pct": 1.8}]
    hist = {"symbol": payload["symbol"], "source": source,
            "status": "available" if rows else "missing", "rows": rows}
    if adj_applied is not None:
        hist["adj_applied"] = adj_applied
    payload["history"] = {
        "history": hist,
        "stats": {**payload["history"]["stats"],
                  "current_vs_high_pct": vs_high,
                  "annual_high": ({"date": high_date, "close": high}
                                  if high is not None else None)},
    }
    return payload


def _hero_position_sentence(html: str) -> str:
    hero = _hero_slice(html)
    idx = hero.find("净值位置：")
    if idx < 0:
        idx = hero.find("历史收盘位置：")
    assert idx >= 0, "hero 缺位置句"
    return hero[idx:hero.find("</li>", idx) if "</li>" in hero[idx:] else idx + 220]


def test_hero_position_uses_history_last_value_not_quote_price():
    """159920 现场：报价 1.421 ≠ 净值序列末值 1.4301——距离%按序列算，
    位置句必须引用序列末值，不得混写最新价；复权标注按 history 留痕
    （R11：本用例显式 adj_applied=True，非按 source 推断）。"""
    payload = _r7_payload(rows=[{"date": "2025-09-26", "nav": 1.6147},
                                {"date": "2026-10-09", "nav": 1.4301}],
                          high=1.6944, vs_high=-15.6, adj_applied=True)
    html = render_etf_html(payload, md_text=MD_TEXT)
    sentence = _hero_position_sentence(html)
    assert "净值位置：复权净值" in sentence
    assert "1.4301" in sentence and "2026-10-09" in sentence
    assert "1.421" not in sentence and "最新价" not in sentence
    assert "-15.6" in sentence and "1.6944" in sentence


def test_hero_position_baostock_series_labeled_close():
    """R11：前复权收盘价标注仅当 history 自身留痕 adj_applied=True。"""
    payload = _r7_payload(
        source="baostock",
        rows=[{"date": "2025-09-26", "close": 1.6147},
              {"date": "2026-10-09", "close": 1.4301}],
        high=1.6944, vs_high=-15.6, adj_applied=True)
    html = render_etf_html(payload, md_text=MD_TEXT)
    sentence = _hero_position_sentence(html)
    assert "历史收盘位置：前复权收盘价" in sentence
    assert "1.4301" in sentence and "最新价" not in sentence


@pytest.mark.parametrize("source,rows", [
    ("none", [{"date": "2026-10-09", "nav": 1.4301}]),          # 来源未知
    ("", [{"date": "2026-10-09", "nav": 1.4301}]),              # 缺来源
    ("nav", []),                                                # 缺末值
    ("nav", [{"date": "2026-10-09", "nav": None}]),             # 末值 None
])
def test_hero_position_omitted_when_source_or_last_value_unknown(source, rows):
    """缺末值/来源未知 → 省略该位置断言（不拿报价或 kline 最新净值顶替）。"""
    payload = _r7_payload(source=source, rows=rows, high=1.6944, vs_high=-15.6)
    html = render_etf_html(payload, md_text=MD_TEXT)
    hero = _hero_slice(html)
    assert "净值位置：" not in hero and "历史收盘位置：" not in hero
    assert "价格位置：" not in hero


# ── R11（2026-10-11 复查）：hero 复权标注只认 history 自身留痕，不按 source 硬编码 ──


def _r11_nav_rows():
    return [{"date": "2025-09-26", "nav": 1.6147},
            {"date": "2026-10-09", "nav": 1.4301}]


def _r11_bst_rows():
    return [{"date": "2025-09-26", "close": 1.6147},
            {"date": "2026-10-09", "close": 1.4301}]


@pytest.mark.parametrize("source,adj,expected,unexpected", [
    # 确认 True → 保留复权标注（R7 正确部分）
    ("nav", True, "净值位置：复权净值", ["未复权净值", "复权状态未知"]),
    # 显式 False → 未复权标注（旧实现按 source 误标「复权净值」）
    ("nav", False, "净值位置：未复权净值", ["净值位置：复权净值", "复权状态未知"]),
    # 缺失（无留痕）→ 中性 + 复权状态未知（不得断言复权或未复权）
    ("nav", None, "净值位置：净值（复权状态未知）", ["净值位置：复权净值", "未复权净值"]),
    ("baostock", True, "历史收盘位置：前复权收盘价", ["未复权收盘价", "复权状态未知"]),
    ("baostock", False, "历史收盘位置：未复权收盘价", ["前复权收盘价", "复权状态未知"]),
    ("baostock", None, "历史收盘位置：历史收盘价（复权状态未知）",
     ["前复权收盘价", "未复权收盘价"]),
])
def test_hero_adjustment_label_follows_history_adj_applied(
        source, adj, expected, unexpected):
    """R11：nav/baostock 三态（True/False/缺失）标注均由 history.adj_applied 决定。"""
    rows = _r11_bst_rows() if source == "baostock" else _r11_nav_rows()
    payload = _r7_payload(source=source, rows=rows, high=1.6944, vs_high=-15.6,
                          adj_applied=adj)
    if adj is None:
        assert "adj_applied" not in payload["history"]["history"]  # 缺失而非 False
    html = render_etf_html(payload, md_text=MD_TEXT)
    sentence = _hero_position_sentence(html)
    assert expected in sentence
    assert "1.4301" in sentence and "2026-10-09" in sentence  # R7 末值/日期保留
    for bad in unexpected:
        assert bad not in sentence, (bad, sentence)


def test_hero_adjustment_absent_not_inferred_from_kline_adjusted():
    """负例：kline.adj_applied=True（另一链路）不构成 history 序列的复权证据，
    历史块无留痕 → 不得标「复权净值」，只能标复权状态未知。"""
    payload = _r7_payload(source="nav", rows=_r11_nav_rows(),
                          high=1.6944, vs_high=-15.6)  # 缺省 = 无 adj 留痕
    assert payload["kline"]["adj_applied"] is True    # 另一链路确为已复权
    html = render_etf_html(payload, md_text=MD_TEXT)
    sentence = _hero_position_sentence(html)
    assert "净值位置：净值（复权状态未知）" in sentence
    assert "净值位置：复权净值" not in sentence and "未复权净值" not in sentence


def test_hero_adjustment_explicit_false_overrides_kline_true():
    """负例：history 自身留痕 False → 未复权标注（不得采用 kline 的 True）。"""
    payload = _r7_payload(source="nav", rows=_r11_nav_rows(),
                          high=1.6944, vs_high=-15.6, adj_applied=False)
    assert payload["kline"]["adj_applied"] is True
    html = render_etf_html(payload, md_text=MD_TEXT)
    sentence = _hero_position_sentence(html)
    assert "净值位置：未复权净值" in sentence
    assert "净值位置：复权净值" not in sentence


def test_hero_adjustment_explicit_none_is_unknown():
    """显式 adj_applied=None（新 payload 未留痕形态）→ 未知，不按 falsy 判未复权。"""
    payload = _r7_payload(source="nav", rows=_r11_nav_rows(),
                          high=1.6944, vs_high=-15.6)
    payload["history"]["history"]["adj_applied"] = None
    html = render_etf_html(payload, md_text=MD_TEXT)
    sentence = _hero_position_sentence(html)
    assert "净值位置：净值（复权状态未知）" in sentence
    assert "未复权净值" not in sentence


# ── R8（2026-10-11 复查）：名称/身份摘要消费已封存登记，未知不冒充名称 ──


def _r8_html(html: str) -> tuple[str, str, str]:
    title = re.search(r"<title>([^<]*)</title>", html).group(1)
    hero = _hero_slice(html)
    oneliner = re.search(r'<div class="sp-t" style="margin-top[^>]*>([^<]*)</div>', hero)
    overview = html.split('id="overview"', 1)[1].split("</section>", 1)[0]
    return title, (oneliner.group(1) if oneliner else ""), overview


def test_name_falls_back_to_sealed_identity_when_spot_category_unknown():
    """159920 现场：Spot 缺失 → category.label=未知，但 identity.fund_name 已核
    ——标题/顶栏/hero/产品快照须消费登记名称，不再显示「未知」。"""
    payload = _fixture_payload()
    payload["profile"]["category"] = {"category": "unknown", "label": "未知",
                                      "source": "none"}
    payload["profile"]["identity"] = {
        "fund_name": "恒生交易型开放式指数证券投资基金",
        "verification": {"status": "verified_original", "verified_at": "2026-10-10"},
        "labels": {"asset_type": "股票", "channel": "跨境", "strategy": "宽基"},
    }
    html = render_etf_html(payload, md_text=MD_TEXT)
    title, oneliner, overview = _r8_html(html)
    assert "恒生交易型开放式指数证券投资基金" in title
    assert "恒生交易型开放式指数证券投资基金" in oneliner
    assert "未知" not in title and "未知" not in oneliner
    assert "未知" not in overview.split("产品快照", 1)[1][:120]


def test_name_unknown_identity_shows_pending_not_unknown():
    """159796 现场：identity 未核且无 fund_name → 明确「身份待核」，
    不反推名称/不宣称身份（封存登记字段全缺保持未知）。"""
    payload = _fixture_payload()
    payload["profile"]["category"] = {"category": "unknown", "label": "未知",
                                      "source": "none"}
    payload["profile"]["identity"] = {
        "labels": {"asset_type": "未知", "channel": "未知", "strategy": "未知"},
        "verification": {"status": "unknown", "verified_at": None},
    }
    html = render_etf_html(payload, md_text=MD_TEXT)
    title, oneliner, _ = _r8_html(html)
    assert "身份待核" in title and "身份待核" in oneliner
    assert "未知" not in title


def test_name_known_category_label_still_wins():
    """已知 category.label（常态短名）优先于已封存 fund_name——不改变既有呈现。"""
    payload = _fixture_payload()
    payload["profile"]["identity"] = {
        "fund_name": "国泰中证全指通信设备ETF",
        "verification": {"status": "verified_original"},
    }
    html = render_etf_html(payload, md_text=MD_TEXT)
    title, oneliner, _ = _r8_html(html)
    assert "通信ETF" in title and "通信ETF" in oneliner


def test_name_legacy_payload_without_identity_degrades_transparently():
    """旧代 payload（无 identity）→ 不引入「身份待核」，按原 label/空值降级。"""
    payload = _fixture_payload()
    payload["profile"].pop("identity", None)
    payload["profile"]["category"] = {"category": "unknown", "label": "未知",
                                      "source": "none"}
    html = render_etf_html(payload, md_text=MD_TEXT)
    assert "身份待核" not in html
    assert "<title>" in html  # 渲染不崩


# ── R13/R16（2026-10-13 复查，B03/B06 + 读者呈现）：未核对冲线索不进读者展示 ──


def _hedge_point(html: str) -> str:
    """抽取 hero 中「对冲覆盖：」状态要点全文（到该要点容器 </div> 为止）。"""
    hero = _hero_slice(html)
    idx = hero.find("对冲覆盖：")
    assert idx >= 0, "hero 缺对冲覆盖句"
    end = hero.find("</div>", idx)
    return hero[idx:end if end >= 0 else idx + 260]


def _render_hedge(hedge_coverage: dict) -> str:
    payload = _fixture_payload()
    payload["profile"]["hedge_coverage"] = hedge_coverage
    return render_etf_html(payload, md_text=MD_TEXT)


@pytest.mark.parametrize("hedge", [
    # B03/B06 现场（真实 588000）：coverage=full 但 verification=unverified
    {"index": "588000", "coverage": "full", "futures": "静态期货线索",
     "verification": {"status": "unverified", "note": "未核原文"}},
    {"coverage": "full", "futures": "静态期货线索",
     "verification": {"status": "static_map_only"}},
    {"coverage": "full", "futures": "静态期货线索"},            # 旧代缺 verification 键
    {"coverage": "partial", "options": "静态期权线索",
     "verification": {"status": "unknown"}},
])
def test_hedge_unverified_not_shown_no_affirmative_assertion(hedge):
    """R13/R16：未核/静态映射线索**不进读者展示**（仅保留于原始数据/诊断记录），
    不产生「对冲覆盖：完整/部分/待核/未知」等已核有/无断言。"""
    hero = _hero_slice(_render_hedge(hedge))
    assert "对冲覆盖：" not in hero
    for claim in ("对冲覆盖：完整", "对冲覆盖：部分",
                  "对冲覆盖：待核", "对冲覆盖：未知"):
        assert claim not in hero


def test_legacy_hedge_fixture_not_claimed_complete(html_text):
    """默认 fixture（coverage=none、无 verification）→ 不展示对冲断言（未核不写成无）。"""
    hero = _hero_slice(html_text)
    assert "对冲覆盖：" not in hero


def test_hedge_verified_original_shows_real_coverage():
    """原文已核 → 正常显示实际覆盖与具体工具（已核工具可正常显示）。"""
    html = _render_hedge({"coverage": "full", "futures": "登记期货",
                          "verification": {"status": "verified_original",
                                           "verified_at": "2026-10-10"}})
    pt = _hedge_point(html)
    assert "对冲覆盖：完整" in pt and "登记期货" in pt
    assert "待核" not in pt and "映射线索" not in pt


def test_hedge_verified_original_none_still_real():
    html = _render_hedge({"coverage": "none",
                          "verification": {"status": "verified_original"}})
    pt = _hedge_point(html)
    assert "对冲覆盖：无" in pt and "待核" not in pt and "未知" not in pt


def test_hedge_unverified_malicious_coverage_not_rendered():
    """攻击用例保留：未核分支不进入读者展示 → 恶意 coverage/工具原文不落地。"""
    payload_coverage = "<img src=x onerror=alert(1)>"
    hero = _hero_slice(_render_hedge({
        "index": "588000", "coverage": payload_coverage,
        "futures": "期货<script>",
        "verification": {"status": "unverified", "note": "未核原文"}}))
    assert "对冲覆盖：" not in hero
    assert payload_coverage not in hero


def test_hedge_verified_malicious_tool_is_escaped():
    """已核分支仍走读者展示：工具原文须转义，且不得二次转义。"""
    pt = _hedge_point(_render_hedge({
        "coverage": "full", "futures": "期货<script>",
        "verification": {"status": "verified_original"}}))
    assert "对冲覆盖：完整" in pt
    assert "期货&lt;script&gt;" in pt
    assert "期货<script>" not in pt
    assert "&amp;lt;" not in pt


# ── R16（2026-10-13 读者呈现）：工程信息不进读者展示，但可追溯绑定保留 ──


def test_generation_card_not_visible_but_meta_kept():
    gen = {"same_generation": True, "payload_hash": "abc123def456",
           "frozen_at": "2026-10-13T11:18:17+08:00",
           "generated_at": "2026-10-13T11:18:17+08:00"}
    html = render_etf_html(_fixture_payload(), md_text=MD_TEXT, generation=gen)
    assert '<meta name="invest-generation" content="abc123def456">' in html
    assert "冻结于" not in html          # 调试/代际卡不再可见
    assert "const report=" in html       # 完整数据 JSON 仍在（可追溯不回采）


def test_quality_engine_note_not_shown_as_product_copy():
    payload = _fixture_payload()
    payload["profile"].pop("tracking", None)
    html = render_etf_html(payload, md_text=MD_TEXT)
    quality = html.split('id="quality"', 1)[1].split("</section>", 1)[0]
    assert "引擎未实现" not in quality
    assert "跟踪误差需 ETF 净值与指数点位序列对比" not in quality
    assert "未判定" in quality


def test_engineering_flags_filtered_financial_risk_kept():
    payload = _fixture_payload()
    payload["profile"]["flags"] = [
        "⚠️ 静态表未收录该 ETF 的期货/期权对冲工具（未核；不表示不存在）",
        "⚠️ 折价超 2%，交易价相对净值偏离较大（方向相反）",
    ]
    hero = _hero_slice(render_etf_html(payload, md_text=MD_TEXT))
    assert "静态表未收录" not in hero
    assert "折价超 2%" in hero  # 金融风险真实提示仍保留


def test_refs_uses_actual_sources_not_internal_functions():
    payload = _fixture_payload()
    payload["quote"]["source"] = "fund_etf_spot_em"
    payload["kline"]["source"] = "fund_open_fund_info_em"
    payload["profile"]["fees"] = {"status": "ok", "source": "https://example.com/fee.pdf"}
    payload["research"] = {"sources": [
        {"id": "S1", "title": "某公司2026年半年度报告",
         "url": "https://example.com/a.pdf"}]}
    html = render_etf_html(payload, md_text=MD_TEXT)
    refs = html.split('id="refs"', 1)[1].split("</section>", 1)[0]
    assert "etf_data.query_etf_data" not in html  # 不再展示内部函数名
    assert "fund_etf_spot_em" in refs and "fund_open_fund_info_em" in refs
    assert "某公司2026年半年度报告" in refs        # 实际披露来源
    assert "天天基金" in refs                       # 采集渠道如实标注
    assert "https://example.com/fee.pdf" in refs    # 费率材料来源


# ── R05/B06（2026-10-13 复核）：数据来源只认能证明成功的来源 ──

def _slice(html: str, sec: str) -> str:
    return html.split(f'id="{sec}"', 1)[1].split("</section>", 1)[0]


def _no_direct_sources(payload: dict) -> dict:
    payload["quote"]["source"] = None
    payload["quote"]["status"] = "unavailable"
    payload["kline"]["source"] = None
    payload["holdings"]["status"] = "unavailable"
    payload["holdings"].pop("source", None)
    payload["research"] = {"sources": []}
    payload["profile"]["fees"] = {"status": "unverified", "source": None}
    return payload


def test_refs_failed_trace_and_static_channel_not_success_source():
    """反例：仅有静态 channel / 失败 / 未执行的记录，不得出现「本次实际来源」。"""
    payload = _no_direct_sources(_fixture_payload())
    payload["data_status"] = {"dimensions": {
        "quote": {"available": True,
                  "source_record": {"channel": "静态登记渠道 A"},
                  "attempted_sources": ["源A", "源B"],
                  "source_trace": [{"source": "源A", "outcome": "failed", "reason": "网络"},
                                   {"source": "源B", "outcome": "skipped", "reason": "未执行"}]},
        "nav": {"available": False,
                "source_record": {"channel": "静态登记净值源"},
                "source_trace": [{"source": "源C", "outcome": "ok"}]},
    }}
    refs = _slice(render_etf_html(payload, md_text=MD_TEXT), "refs")
    for bad in ("静态登记渠道 A", "源A", "源B", "静态登记净值源", "源C"):
        assert bad not in refs, bad
    assert "未使用外部数据源" not in refs
    assert "来源信息未随此数据提供" in refs


def test_refs_success_trace_registers_source_and_shows_truthfully():
    """成功 trace(outcome=ok) 可登记为实际来源；失败来源不得冒充成功。"""
    payload = _no_direct_sources(_fixture_payload())
    payload["data_status"] = {"dimensions": {
        "share_history": {"available": True,
                          "source_record": {"channel": "静态渠道描述"},
                          "source_trace": [{"source": "Tushare fund_share", "outcome": "ok"},
                                           {"source": "Tushare fund_daily", "outcome": "failed"}]},
    }}
    refs = _slice(render_etf_html(payload, md_text=MD_TEXT), "refs")
    assert "Tushare fund_share" in refs
    assert "Tushare fund_daily" not in refs


def test_refs_fee_dict_formatted_not_raw():
    payload = _fixture_payload()
    payload["profile"]["fees"] = {"status": "ok", "source": {
        "source_type": "基金 2025 年年度报告（报表附注 7.4.10.2）",
        "version": "2025 年年度报告（披露日 2026-03-31）",
        "url": "https://example.com/fee.pdf",
        "locator": "报表附注 7.4.10.2.1 管理费"}}
    refs = _slice(render_etf_html(payload, md_text=MD_TEXT), "refs")
    assert "https://example.com/fee.pdf" in refs
    assert "基金 2025 年年度报告" in refs
    assert "2025 年年度报告（披露日 2026-03-31）" in refs
    assert "报表附注 7.4.10.2.1 管理费" in refs
    assert "source_type" not in refs  # 不裸展示 dict 内部对象


def test_refs_missing_source_info_no_false_no_external_data_claim():
    payload = _no_direct_sources(_fixture_payload())
    refs = _slice(render_etf_html(payload, md_text=MD_TEXT), "refs")
    assert "未使用外部数据源" not in refs           # 元数据缺失 ≠ 无外部数据
    assert "来源信息未随此数据提供" in refs


def test_quality_chart_adjustment_not_affirmed_from_kline():
    """反例：kline 指标链路已复权，但曲线(history)复权未知 → 不得肯定图已复权。"""
    payload = _fixture_payload()
    payload["kline"]["adj_applied"] = True
    payload["history"]["history"].pop("adj_applied", None)
    quality = _slice(render_etf_html(payload, md_text=MD_TEXT), "quality")
    assert "图示为本次净值历史走势" in quality
    for bad in ("复权序列", "引擎复权", "渲染层", "净值曲线直接引用"):
        assert bad not in quality, bad


def test_quality_tracking_gap_no_currency_frequency_window_claim():
    payload = _fixture_payload()
    payload["profile"]["tracking"] = {
        "status": "missing_benchmark", "td_pct": None, "te_pct_annualized": None,
        "attempted_sources": ["a", "b"],
        "price_benchmark_deviation": {"td_pct": 0.61, "te_pct_annualized": 0.36, "sample_n": 245}}
    quality = _slice(render_etf_html(payload, md_text=MD_TEXT), "quality")
    assert "币种/频率/窗口" not in quality
    assert "分红处理不同" in quality
    assert "不作为确认性跟踪误差" in quality
    assert "0.6" in quality and "0.36" in quality     # 研究偏离数金融性质保留
    assert "missing_benchmark" not in quality        # 不打印内部 enum


def test_quality_tracking_unknown_reason_only_no_comparable():
    payload = _fixture_payload()
    payload["profile"]["tracking"] = {"status": "missing_benchmark",
                                      "attempted_sources": ["a"]}
    quality = _slice(render_etf_html(payload, md_text=MD_TEXT), "quality")
    assert "暂无可比跟踪结果" in quality
    assert "币种" not in quality and "分红处理不同" not in quality


def test_research_scope_period_end_not_publish_date():
    payload = _fixture_payload()
    payload["research"] = {"question": "Q",
                           "coverage": {"holdings_as_of": "2026-06-30", "weight_pct": 16.02,
                                        "note": "n"},
                           "sources": []}
    html = render_etf_html(payload, md_text=MD_TEXT)
    assert "持仓期末 2026-06-30" in html
    assert "2026-06-30 披露" not in html


# ── M3c（2026-10-13 定点修正）：按**真实生产者形状**证明成功来源 ──

def _real_identity_payload() -> dict:
    """真实形状：identity 在 profile.identity，evidence 为 list[dict]（含「同上」）。"""
    payload = _no_direct_sources(_fixture_payload())
    payload["profile"]["identity"] = {
        "verification": {"status": "verified_original", "verified_at": "2026-10-13"},
        "evidence": [
            {"claim": "全称/代码", "locator": "§一 产品概况",
             "url": "https://example.com/pds.pdf", "verified_at": "2026-10-13"},
            {"claim": "标的指数", "locator": "§二(一) 投资目标",
             "url": "同上（§二(一)）", "verified_at": "2026-10-13"},
        ],
    }
    payload["data_status"] = {"dimensions": {
        "nav": {"status": "available",
                "source_trace": [{"source": "fund_open_fund_info_em", "outcome": "ok"}]},
        "share_history": {"status": "available",
                          "source_trace": [{"source": "Tushare fund_share", "outcome": "ok"},
                                           {"source": "Tushare fund_daily", "outcome": "failed"}]},
        "index_pe": {"status": "available", "attempted_sources": ["csindex 指数 PE 文件"]},
    }}
    return payload


def test_refs_identity_evidence_list_shape_and_locators():
    """真实形状：profile.identity.evidence 为 list[dict] → 逐条登记 URL/定位；
    url=「同上」承接上一已知 URL（不编造）；不再取顶层 identity。"""
    payload = _real_identity_payload()
    refs = _slice(render_etf_html(payload, md_text=MD_TEXT), "refs")
    assert "https://example.com/pds.pdf" in refs
    assert "§一 产品概况" in refs and "§二(一) 投资目标" in refs
    assert "原文已核" in refs
    assert "同上" not in refs  # 「同上」不落表
    assert "Tushare fund_share" in refs and "Tushare fund_daily" not in refs
    assert "fund_open_fund_info_em" in refs
    assert "csindex" in refs  # index_pe 由 index_pe_note 明示来源登记


def test_refs_real_status_shape_failed_not_success():
    """真实形状反例：status=failed/not_executed + 旧 directsource/attempted
    不得冒充本次成功；未核身份不得标「原文已核」。"""
    payload = _no_direct_sources(_fixture_payload())
    payload["profile"]["identity"] = {
        "verification": {"status": "unknown"},
        "evidence": [{"url": "https://example.com/unverified.pdf", "locator": "§一"}]}
    payload["data_status"] = {"dimensions": {
        "nav": {"status": "failed", "directsource": "旧直连源",
                "attempted_sources": ["源X"],
                "source_trace": [{"source": "源Y", "outcome": "ok"}]},
        "share_history": {"status": "not_executed", "directsource": "旧直连份额源",
                          "attempted_sources": ["Tushare fund_share"]},
    }}
    refs = _slice(render_etf_html(payload, md_text=MD_TEXT), "refs")
    for bad in ("旧直连源", "旧直连份额源", "源X", "源Y", "Tushare fund_share",
                "https://example.com/unverified.pdf", "原文已核"):
        assert bad not in refs, bad
    assert "来源信息未随此数据提供" in refs
