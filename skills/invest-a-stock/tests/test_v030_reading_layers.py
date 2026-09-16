"""v0.3.0 阅读分层与槽位接线回归。

覆盖四项修复：
1. 执行摘要/核心矛盾的报告期锚定 —— financials 维度 Tushare 源为**降序**，
   裸取 `[-1]` 曾锚定最旧一期（执行摘要显示 2022Q3 的 ROE/EPS）。
2. brief 模式消费 `--analysis`（此前完全忽略 payload）。
3. 四个就地槽位（participant_scan / event_classification / mda_narrative /
   bear_chain）替换引擎占位串 —— 该四处是 QC
   `completion-template-placeholder` / `completion-empty-basis` 的 error 级命中项。
4. 方案 A：overview 槽位前置为「执行摘要（5 分钟阅读区）」，
   其余段进「分析详情」，两层互斥不重复。
"""
from __future__ import annotations

import pytest

from fixtures.collections import collection_v2_minimal
from lib.analysis_schema import (
    BEAR_CHAIN_KEYS,
    EVENT_CLASSIFICATION_KEYS,
    INLINE_SLOT_KEYS,
    MDA_NARRATIVE_KEYS,
    OVERVIEW_KEYS,
    PARTICIPANT_SCAN_KEYS,
    find_section,
    is_inline_slotted,
    split_overview,
    validate_sections,
)
from lib.render_markdown._concise import render_report_v3


# ── 1. 报告期锚定：降序输入也必须取到最新一期 ──

def test_financials_fixture_descending_is_really_descending():
    """前置守卫：降序 fixture 的首行确实是最新期（否则下面的测试无意义）。"""
    rows = [d for d in collection_v2_minimal(kline_descending=True)["dimensions"]
            if d["dimension"] == "financials"][0]["data"]
    assert rows[0]["end_date"] == "20241231", "降序 fixture 首行应为最新报告期"
    assert rows[-1]["end_date"] == "20240331", "降序 fixture 末行应为最旧报告期"


@pytest.mark.parametrize("descending", [False, True])
def test_executive_summary_anchors_newest_period(descending: bool):
    """执行摘要的 ROE 必须取最新期（20241231 的 20.2），与输入行序无关。"""
    coll = collection_v2_minimal(kline_descending=descending)
    md = render_report_v3(coll, "600176", mode="brief")
    assert "ROE=20.2%" in md, f"应锚定最新期 ROE 20.2（descending={descending}）"
    assert "ROE=18.5%" not in md, "不得锚定最旧期 18.5"


@pytest.mark.parametrize("descending", [False, True])
def test_core_contradictions_anchor_newest_period(descending: bool):
    """核心矛盾行同样必须锚定最新期（同一 bug 的第二处实例）。"""
    coll = collection_v2_minimal(kline_descending=descending)
    md = render_report_v3(coll, "600176", mode="brief")
    assert "ROE 20.2%" in md, f"核心矛盾应锚定最新期（descending={descending}）"
    assert "ROE 18.5%" not in md


# ── 2. 槽位判定 helper ──

def test_find_section_matches_module_or_position():
    sec = {"module": "bear_chain", "position": "analysis", "title": "t",
           "facts_md": "f", "analysis_md": "a", "evidence_tag": "B"}
    assert find_section([sec], BEAR_CHAIN_KEYS) is sec
    assert find_section([sec], MDA_NARRATIVE_KEYS) is None


def test_find_section_is_case_insensitive():
    sec = {"module": "Bear_Chain", "position": "analysis", "title": "t",
           "facts_md": "f", "analysis_md": "a", "evidence_tag": "B"}
    assert find_section([sec], BEAR_CHAIN_KEYS) is sec


def test_find_section_empty_and_malformed_inputs():
    assert find_section(None, BEAR_CHAIN_KEYS) is None
    assert find_section([], BEAR_CHAIN_KEYS) is None
    assert find_section(["not-a-dict"], BEAR_CHAIN_KEYS) is None


def test_split_overview_partitions_and_preserves_order():
    ov = {"module": "overview", "title": "o", "facts_md": "f",
          "analysis_md": "a", "evidence_tag": "B", "position": "overview"}
    other = {"module": "events", "title": "e", "facts_md": "f",
             "analysis_md": "a", "evidence_tag": "B", "position": "events"}
    got_ov, got_rest = split_overview([ov, other])
    assert got_ov == [ov] and got_rest == [other]


def test_is_inline_slotted_covers_all_slot_keys():
    for keys in (OVERVIEW_KEYS, BEAR_CHAIN_KEYS, MDA_NARRATIVE_KEYS,
                 EVENT_CLASSIFICATION_KEYS, PARTICIPANT_SCAN_KEYS):
        key = next(iter(keys))
        assert is_inline_slotted({"module": key, "position": "analysis"}) is True
    # 非槽位段仍走尾部注记
    assert is_inline_slotted({"module": "valuation", "position": "valuation"}) is False
    assert INLINE_SLOT_KEYS >= OVERVIEW_KEYS


# ── 3. 方案 A：overview 前置、两层互斥 ──

def _overview_sec(title: str = "首要判断") -> dict:
    return {"module": "overview", "position": "overview", "title": title,
            "facts_md": "事实一 [来源: engine]", "analysis_md": "判断一",
            "evidence_tag": "B"}


def _events_sec() -> dict:
    return {"module": "events", "position": "events", "title": "事件分析",
            "facts_md": "事实二 [来源: engine]", "analysis_md": "判断二",
            "evidence_tag": "C"}


def test_overview_is_front_placed_before_toc_and_detail():
    md = render_report_v3(collection_v2_minimal(), "600176",
                          analysis=[_overview_sec(), _events_sec()])
    i_front = md.index("执行摘要（5 分钟阅读区）")
    i_toc = md.index("## 目录")
    i_detail = md.index("分析详情（analysis.json 注入）")
    assert i_front < i_detail, "5 分钟判断区须在分析详情之前"
    assert i_front < i_toc, "5 分钟判断区须在目录之前"


def test_overview_content_appears_exactly_once():
    """两层互斥：同一段不得既前置又进详情。"""
    md = render_report_v3(collection_v2_minimal(), "600176",
                          analysis=[_overview_sec(), _events_sec()])
    assert md.count("判断一") == 1
    assert md.count("判断二") == 1


def test_no_analysis_means_no_reading_layer_headings():
    """零回归：无 analysis → 两层标题都不出现。"""
    md = render_report_v3(collection_v2_minimal(), "600176")
    assert "执行摘要（5 分钟阅读区）" not in md
    assert "分析详情（analysis.json 注入）" not in md


# ── 4. brief 模式消费 analysis（此前完全忽略）──

def test_brief_mode_consumes_analysis_payload():
    md = render_report_v3(collection_v2_minimal(), "600176", mode="brief",
                          analysis=[_overview_sec(), _events_sec()])
    assert "执行摘要（5 分钟阅读区）" in md
    assert "判断一" in md and "判断二" in md


def test_brief_mode_matches_full_for_analysis_layers():
    """brief 与 full 必须共用同一组槽位语义（不得各写一套）。"""
    payload = [_overview_sec(), _events_sec()]
    brief = render_report_v3(collection_v2_minimal(), "600176", mode="brief",
                             analysis=payload)
    full = render_report_v3(collection_v2_minimal(), "600176", mode="full",
                            analysis=payload)
    for probe in ("执行摘要（5 分钟阅读区）", "分析详情（analysis.json 注入）"):
        assert probe in brief and probe in full


# ── 5. 四个就地槽位替换引擎占位串 ──

def _slot(module: str, analysis_md: str) -> dict:
    return {"module": module, "position": "analysis", "title": f"{module} 分析",
            "facts_md": "槽位事实 [来源: engine]", "analysis_md": analysis_md,
            "evidence_tag": "B"}


def _coll_with_moneyflow() -> dict:
    """最小 collection + 主力资金行（参与者行为节需至少一行才渲染表格）。"""
    coll = collection_v2_minimal()
    coll["market_structure"] = {
        "moneyflow": {"net_sum_5d": 1.5e8, "source": "test.fixture"},
    }
    return coll


def _coll_with_cards(**cards) -> dict:
    """最小 collection + 分析卡片（事件分类摘要 / MD&A 叙事解读的来源）。"""
    coll = collection_v2_minimal()
    coll.setdefault("_meta", {})["analysis_cards"] = dict(cards)
    return coll


def test_participant_scan_slot_replaces_placeholder():
    coll = _coll_with_moneyflow()
    without = render_report_v3(coll, "600176", mode="full")
    assert "分析提示（Claude 填写）" in without, "无分析段时应保留占位（门禁照常拦截）"
    with_ = render_report_v3(coll, "600176", mode="full",
                             analysis=[_slot("participant_scan", "参与方方向不一致")])
    assert "分析提示（Claude 填写）" not in with_
    assert "参与方方向不一致" in with_


def test_event_classification_slot_replaces_placeholder():
    coll = _coll_with_cards(event_classifications=[
        {"event_type": "buyback", "event_label": "回购", "events": [{"date": "2026-06-11"}]},
    ])
    # 事件时间线是分类摘要的宿主节：events 为空时整节（含摘要）不渲染。
    coll["events"] = [{"date": "2026-06-11", "type": "buyback",
                       "title": "测试股份:关于回购公司A股股份的公告",
                       "impact_dimension": "估值", "duration": "中长期变量"}]
    without = render_report_v3(coll, "600176", mode="full")
    assert "待 Claude 验证" in without, "无分析段时应保留占位"
    with_ = render_report_v3(coll, "600176", mode="full",
                             analysis=[_slot("event_classification", "分类复核结论")])
    assert "待 Claude 验证" not in with_
    assert "分类复核结论" in with_


def test_mda_narrative_slot_replaces_placeholder():
    coll = _coll_with_cards(mda_narrative={
        "generated_at": "2026-06-11T12:00:00+00:00", "revenue_growth_yoy": 12.0,
        "profit_growth_yoy": 8.0, "gross_margin": 30.0, "net_margin": 10.0,
        "narrative_slot": "[待 Claude 填充管理层论述解读]",
    })
    without = render_report_v3(coll, "600176", mode="full")
    assert "[待 Claude 填充管理层论述解读]" in without, "无分析段时应保留占位"
    with_ = render_report_v3(coll, "600176", mode="full",
                             analysis=[_slot("mda_narrative", "叙事解读结论")])
    assert "[待 Claude 填充管理层论述解读]" not in with_
    assert "叙事解读结论" in with_


def test_bear_chain_slot_appended_when_engine_generated_chains():
    """引擎已生成空头链时，槽位段必须追加而非静默丢弃。

    is_inline_slotted 已把槽位段排除出「分析详情」，若 5b 不渲染它就无处可去
    ——段内容会整体消失（设计缺陷，非仅测试问题）。
    """
    coll = collection_v2_minimal()
    md = render_report_v3(coll, "600176", mode="full",
                          analysis=[_slot("bear_chain", "空头逻辑唯一标记")])
    assert "空头逻辑唯一标记" in md
    assert md.count("空头逻辑唯一标记") == 1, "槽位段不得同时出现在 5b 与分析详情"


def test_bear_chain_slot_renders_when_engine_has_no_chain():
    """引擎无空头链 → 槽位段取代「未形成明确空头逻辑链」空依据声明。"""
    from lib.render_risk import _section_bull_bear
    coll = collection_v2_minimal()
    dims = {d["dimension"]: d for d in coll["dimensions"]}
    base = _section_bull_bear(coll, "600176", dims, {}, {})
    assert "### 5b. 空头逻辑链" in base
    empty_placeholder = "当前数据未形成明确空头逻辑链" in base or "#### 空头逻辑" in base
    assert empty_placeholder, "5b 应为「无链声明」或「引擎链」之一"
    # 直接对渲染器注入槽位段，验证两条分支都消费 analysis
    injected = _section_bull_bear(coll, "600176", dims, {}, {},
                                  analysis=[_slot("bear_chain", "空头逻辑唯一标记")])
    assert "空头逻辑唯一标记" in injected


def test_slot_sections_do_not_leak_into_detail_layer():
    """槽位段是就地渲染，不得再进「分析详情」造成重复。"""
    md = render_report_v3(collection_v2_minimal(), "600176", mode="full",
                          analysis=[_slot("bear_chain", "空头逻辑唯一标记")])
    assert "分析详情（analysis.json 注入）" not in md, \
        "仅含槽位段时不应产生分析详情层"


# ── 6. [事实] 标签（QC structure-analysis-without-fact 的引擎侧防线）──

def test_exogenous_shock_block_labels_its_table_as_facts():
    from lib.render_extras import section_exogenous_shock
    coll = collection_v2_minimal()
    coll["news"] = {"cards": [{"date": "2026-06-11", "direction": "neutral",
                               "credibility": "official", "credibility_score": 0.95,
                               "title": "某公告", "source": "notice",
                               "url": "https://example.invalid/a"}]}
    out = section_exogenous_shock(coll)
    assert "[事实]" in out
    assert out.index("[事实]") < out.index("[分析]"), "先事实后分析"


def test_participant_scan_labels_its_table_as_facts():
    """参与者节自身的 [事实] 标签（QC structure-analysis-without-fact 的防线）。

    切片须止于下一个 H2：截到 EOF 时，断言会被后文任一节的 [事实] 满足而不空转。
    """
    coll = _coll_with_moneyflow()   # 有资金行才会渲染参与者表（否则是空数据分支）
    out = render_report_v3(coll, "600176", mode="full")
    assert "## 参与者行为扫描" in out, "夹具应产出参与者节"
    rest = out[out.index("## 参与者行为扫描"):]
    nxt = rest.find("\n## ", 1)
    section = rest if nxt < 0 else rest[:nxt]
    assert "[事实]" in section, "参与者节自身缺少 [事实] 标签"


# ── 7. schema 允许槽位的 position 取值 ──

def test_slot_sections_pass_schema_with_allowed_positions():
    sections = [_overview_sec()] + [
        _slot(m, "内容") for m in
        ("participant_scan", "event_classification", "mda_narrative", "bear_chain")
    ]
    assert validate_sections(sections) == []


# ── 8. 资金流口径：全档 vs 大单+特大单并列，标签不得称「主力」 ──

def _mf_row(**kw) -> dict:
    row = {"trade_date": "20260915", "net_mf_amount": 0.0}
    row.update(kw)
    return row


def test_flow_lg_elg_yuan_computes_net_from_four_buckets():
    from lib.collector._sources import _flow_lg_elg_yuan
    # 万元口径：(买大 300 + 买特大 200 − 卖大 100 − 卖特大 50) 万 = 350 万元
    got = _flow_lg_elg_yuan(_mf_row(buy_lg_amount=300.0, sell_lg_amount=100.0,
                                    buy_elg_amount=200.0, sell_elg_amount=50.0))
    assert got == pytest.approx(350.0 * 10_000)


def test_flow_lg_elg_yuan_returns_none_on_partial_buckets():
    """任一字段缺失 → None：不部分求和，避免把残缺口径当完整口径输出。"""
    from lib.collector._sources import _flow_lg_elg_yuan
    assert _flow_lg_elg_yuan(_mf_row(buy_lg_amount=300.0, sell_lg_amount=100.0,
                                     buy_elg_amount=200.0)) is None
    assert _flow_lg_elg_yuan(_mf_row()) is None
    assert _flow_lg_elg_yuan(_mf_row(buy_lg_amount="x", sell_lg_amount=1.0,
                                     buy_elg_amount=1.0, sell_elg_amount=1.0)) is None


def test_flow_lg_elg_yuan_treats_nan_bucket_as_missing():
    """pandas DataFrame 的缺失值是 NaN 而非 None：NaN 不得参与分档求和。

    原判定链 `any(v is None)` 拦不住 NaN（float('nan') 是合法 float），
    求和结果为 NaN 且非 None，会被下游 fmt_amount 渲染成字面量「nan」。
    """
    from lib.collector._sources import _flow_lg_elg_yuan
    nan = float("nan")
    assert _flow_lg_elg_yuan(_mf_row(buy_lg_amount=300.0, sell_lg_amount=nan,
                                     buy_elg_amount=200.0,
                                     sell_elg_amount=50.0)) is None
    assert _flow_lg_elg_yuan(_mf_row(buy_lg_amount=nan, sell_lg_amount=nan,
                                     buy_elg_amount=nan,
                                     sell_elg_amount=nan)) is None


def test_flow_amount_yuan_treats_nan_as_missing():
    """全档净额同样：NaN 形参必须按缺失处理，禁止让 NaN 穿透到报告。"""
    from lib.collector._sources import _flow_amount_yuan
    assert _flow_amount_yuan(_mf_row(net_mf_amount=float("nan"))) is None


def test_ms_fetch_moneyflow_omits_net_when_window_has_nan():
    """窗口内任一日净额为 NaN → 不给 net_sum_5d（禁止以 0.0 冒充缺失）。

    `net_sum = sum(v for v in ... if v is not None)` 在整窗缺失时得 0.0，
    会被读成「近 5 日零净流入」——与 `_v3` P0-1「不得以 0.0 代替参与判定」同类。
    """
    from unittest.mock import MagicMock, patch

    from lib.collector._orchestrate import _ms_fetch_moneyflow

    nan = float("nan")
    rows = [{"trade_date": f"2026010{i}", "net_mf_amount": nan} for i in range(1, 6)]
    with patch("lib.collector._orchestrate._q_tushare_moneyflow", return_value=rows):
        out = _ms_fetch_moneyflow(MagicMock(), "600176")
    assert "net_sum_5d" not in out
    assert "net_sum_5d_lg_elg" not in out
    assert out["records"], "原始记录仍须保留供追溯"


def test_ms_fetch_moneyflow_rejects_partial_window():
    """5 日窗口缺 1 日 → 不得用 4 日和冒充「近5日全档净额」。"""
    from unittest.mock import MagicMock, patch

    from lib.collector._orchestrate import _ms_fetch_moneyflow

    nan = float("nan")
    rows = [
        {"trade_date": "20260101", "net_mf_amount": 100.0},
        {"trade_date": "20260102", "net_mf_amount": 200.0},
        {"trade_date": "20260103", "net_mf_amount": 300.0},
        {"trade_date": "20260104", "net_mf_amount": 400.0},
        {"trade_date": "20260105", "net_mf_amount": nan},
    ]
    with patch("lib.collector._orchestrate._q_tushare_moneyflow", return_value=rows):
        out = _ms_fetch_moneyflow(MagicMock(), "600176")
    assert "net_sum_5d" not in out, "缺日窗口不得部分求和（与 lg_elg 同规则）"


def test_moneyflow_labels_do_not_claim_main_force_caliber():
    """标签必须写「全档」，不得写「主力」——后者会把全档值读成主力值。"""
    from lib.participant_scan import moneyflow_signal_label
    for key in ("net_sum_5d", "net_sum_10d", "net_mf_amount", None):
        label = moneyflow_signal_label(key)
        assert "主力" not in label, f"{key} 标签仍称主力: {label}"
        assert "全档" in label


def test_participant_section_reports_both_calibers():
    """第六节须同时给出全档与大单+特大单两个口径，且方向由数据决定。"""
    from lib.participant_scan import build_participant_behavior_section
    coll = collection_v2_minimal()
    ms = {"moneyflow": {"net_sum_5d": 17.96e8, "net_sum_5d_lg_elg": -15.24e8,
                        "source": "test.fixture"}}
    out = build_participant_behavior_section(coll, "600176", ms, {})
    assert "全档" in out
    assert "大单+特大单" in out
    assert "主力（大单代理）" not in out


def test_participant_section_omits_lg_elg_when_absent():
    """缺第二口径时不得回落到全档值冒充（宁缺勿错）。"""
    from lib.participant_scan import build_participant_behavior_section
    coll = collection_v2_minimal()
    ms = {"moneyflow": {"net_sum_5d": 17.96e8, "source": "test.fixture"}}
    out = build_participant_behavior_section(coll, "600176", ms, {})
    assert "大单+特大单" not in out


def test_cv7_wording_names_caliber_not_main_force():
    from lib.render_utils import _v3_cv7_assessment
    got = _v3_cv7_assessment(10.0, 1.0e8)
    assert got is not None
    assert "主力" not in got[1], f"CV-7 措辞仍称主力: {got[1]}"
    assert "全档" in got[1]


# ── 10b. [事实] 窗口必须写实际窗口，不得硬编码 ──

def _coll_with_event_classification(window_days: int) -> dict:
    coll = _coll_with_cards(event_classifications=[
        {"event_type": "buyback", "event_label": "回购",
         "events": [{"date": "2026-06-11"}]},
    ])
    coll["events"] = [{"date": "2026-06-11", "type": "buyback",
                       "title": "测试股份:关于回购公司A股股份的公告",
                       "impact_dimension": "估值", "duration": "中长期变量"}]
    coll.setdefault("_meta", {})["events_summary"] = {
        "event_count": 1, "window_days": window_days,
    }
    return coll


def test_event_classification_window_follows_events_window():
    """--deep 下事件窗为 90 日，分类摘要的 [事实] 行不得仍写「近 30 日」。

    窗口是采集侧参数（`_meta.events_summary.window_days`，events.py 实测 90），
    渲染写死 30 会把「回购 (1条)」的覆盖区间缩成实际的三分之一。
    """
    md = render_report_v3(_coll_with_event_classification(90), "600176",
                          mode="full", analysis=[_slot("event_classification", "分类复核")])
    lines = [ln for ln in md.splitlines() if "按类型归类如下" in ln]
    assert lines, "应渲染事件分类摘要的 [事实] 行"
    assert "近 90 日" in lines[0], f"窗口未跟随实际事件窗: {lines[0]}"


def test_event_classification_window_default_is_thirty():
    """守卫：默认事件窗口（30 日）输出与既有基线一致。"""
    md = render_report_v3(_coll_with_event_classification(30), "600176",
                          mode="full", analysis=[_slot("event_classification", "分类复核")])
    lines = [ln for ln in md.splitlines() if "按类型归类如下" in ln]
    assert lines and "近 30 日" in lines[0]


# ── 10. 就地槽位消费是**动态**的：宿主未渲染时段必须落进「分析详情」 ──
#
# 槽位的静态谓词 is_inline_slotted 只说明「若命中则就地渲染」，而三个宿主都是
# **条件渲染**：participant_scan 无扫描行、event_classification 无事件卡/无事件、
# mda_narrative 无 MD&A 卡时宿主整体不输出；brief 模式更是根本不调用这三个宿主。
# 此时段既无正文落点、又被静态剔除 → --analysis 内容零落点丢失。

def test_slots_without_rendered_host_fall_through_to_detail_layer():
    """三个条件宿主在最小 collection 下均不渲染 → 段须进「分析详情」。"""
    md = render_report_v3(collection_v2_minimal(), "600176", mode="full",
                          analysis=[_slot("participant_scan", "参与方标记AAA"),
                                    _slot("event_classification", "事件分类标记BBB"),
                                    _slot("mda_narrative", "叙事标记CCC")])
    for probe in ("参与方标记AAA", "事件分类标记BBB", "叙事标记CCC"):
        assert probe in md, f"宿主未渲染时槽位段静默丢失: {probe}"
    assert "分析详情（analysis.json 注入）" in md


def test_concise_mode_consumes_analysis():
    """concise 此前完全忽略 --analysis（解析→校验→丢弃后 exit 0）。

    brief 在 fix② 接了线，concise（Hermes/OpenClaw 对话模式）漏掉：同一份
    payload 在 brief/full 有落点、在 concise 一个字都不出现。
    """
    md = render_report_v3(collection_v2_minimal(), "600176", mode="concise",
                          analysis=[_overview_sec(), _events_sec()])
    assert "执行摘要（5 分钟阅读区）" in md, "overview 段未前置"
    assert "判断一" in md, "overview 段内容丢失"
    assert "判断二" in md, "非槽位段在 concise 下零落点丢失"


def test_concise_without_analysis_stays_clean():
    """守卫：无 analysis 时 concise 基线不得出现两个分析层标记。"""
    md = render_report_v3(collection_v2_minimal(), "600176", mode="concise")
    assert "执行摘要（5 分钟阅读区）" not in md
    assert "分析详情（analysis.json 注入）" not in md


def test_brief_mode_keeps_slots_brief_never_renders():
    """brief 不渲染事件时间线/参与者节/MD&A → 这三个槽位段须进分析详情。

    brief 的 parts 里没有这三个宿主，静态剔除会把它们全部丢掉。
    """
    brief = render_report_v3(collection_v2_minimal(), "600176", mode="brief",
                             analysis=[_slot("participant_scan", "参与方标记DDD"),
                                       _slot("event_classification", "事件标记EEE"),
                                       _slot("mda_narrative", "叙事标记FFF")])
    for probe in ("参与方标记DDD", "事件标记EEE", "叙事标记FFF"):
        assert probe in brief, f"brief 下槽位段静默丢失: {probe}"


def test_duplicate_slot_extra_section_falls_through_to_detail_layer():
    """同槽位多段：就地只消费首个（find_section 语义），其余不得静默丢失。"""
    coll = _coll_with_cards(event_classifications=[
        {"event_type": "buyback", "event_label": "回购",
         "events": [{"date": "2026-06-11"}]},
    ])
    coll["events"] = [{"date": "2026-06-11", "type": "buyback",
                       "title": "测试股份:关于回购公司A股股份的公告",
                       "impact_dimension": "估值", "duration": "中长期变量"}]
    md = render_report_v3(coll, "600176", mode="full",
                          analysis=[_slot("event_classification", "首个段GGG"),
                                    _slot("event_classification", "重复段HHH")])
    assert "首个段GGG" in md, "首个命中段应就地渲染"
    assert "重复段HHH" in md, "第二个同槽位段无处落点 → 内容静默丢失"


def test_consumed_slot_is_not_duplicated_into_detail_layer():
    """守卫：宿主确实渲染了就地槽位时，段不得再进「分析详情」（防过度保留）。"""
    coll = _coll_with_moneyflow()
    md = render_report_v3(coll, "600176", mode="full",
                          analysis=[_slot("participant_scan", "参与方唯一标记III")])
    assert "参与方唯一标记III" in md
    assert md.count("参与方唯一标记III") == 1, "就地渲染 + 尾部注记 = 重复出现"


# ── 9. CV-4 口径标签：比较值与 CV-7 同为全档，文案不得称「主力」 ──

def _coll_with_northbound_and_moneyflow() -> dict:
    """最小 collection + 北向与全档净额（CV-4 需两侧都有值才渲染）。"""
    coll = collection_v2_minimal()
    coll["market_structure"] = {
        "northbound": {"net_sum_10d": -1.0e9, "days": 10, "source": "test.fixture"},
        "moneyflow": {"net_sum_5d": 1.5e8, "source": "test.fixture"},
    }
    return coll


def test_cv4_wording_names_caliber_not_main_force():
    """CV-4 的 mf 侧取自 resolve_moneyflow 默认键（全档），文案不得称「主力」。

    否则同一段里 867 行写「全档资金」、CV-4 写「主力大单」，读者拿到的
    比较基准与实际口径不符（CV-7 已修同类问题，CV-4 是漏网实例）。
    """
    md = render_report_v3(_coll_with_northbound_and_moneyflow(), "600176",
                          mode="full")
    lines = [ln for ln in md.splitlines() if "CV-4" in ln]
    assert lines, "应渲染 CV-4 行"
    for ln in lines:
        assert "主力" not in ln, f"CV-4 仍称主力: {ln}"
        assert "全档" in ln


def test_participant_cv_note_names_caliber_not_main_force():
    """参与者节的 CV 备注是同一比较的第二处实例，句中不得自称「主力」。

    原句「北向与主力净流入方向相反（北向近10日 vs 全档近5日）」：主语称主力、
    括注称全档，同一句自相矛盾且主语口径错误。
    """
    md = render_report_v3(_coll_with_northbound_and_moneyflow(), "600176",
                          mode="full")
    notes = [ln for ln in md.splitlines() if ln.startswith("- 北向与")]
    assert notes, "应渲染参与者 CV 备注"
    for ln in notes:
        assert "主力" not in ln, f"参与者 CV 备注仍称主力: {ln}"


def test_divergence_line_names_caliber_not_main_force():
    """「资金流向背离」段用的是 resolve_moneyflow 的全档净额，不得称「主力」。"""
    md = render_report_v3(_coll_with_northbound_and_moneyflow(), "600176",
                          mode="full")
    lines = [ln for ln in md.splitlines() if "资金流向背离" in ln]
    assert lines, "应渲染资金流向背离段"
    for ln in lines:
        assert "主力" not in ln, f"背离段仍称主力: {ln}"
        assert "全档" in ln


def test_moneyflow_cv_window_fallback_names_full_caliber():
    """未知键的兜底标签须写「全档」——调用方传入的键集全部是全档口径。"""
    from lib.participant_scan import moneyflow_cv_window
    for key in ("net_sum_5d", "net_sum_10d", "net_mf_amount", None, "unknown"):
        label = moneyflow_cv_window(key)
        assert "主力" not in label, f"CV 窗口标签仍称主力: {key} → {label}"


def test_moneyflow_driver_unavailable_names_full_caliber():
    """资金因子不可得时的标签须与可得时（资金（全档））一致，不得退回「主力」。"""
    coll = collection_v2_minimal()
    coll["market_structure"] = {}
    md = render_report_v3(coll, "600176", mode="full")
    assert "资金（主力）" not in md
    assert "资金（全档）" in md
