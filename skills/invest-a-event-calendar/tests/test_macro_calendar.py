"""宏观事件日历数据层测试（离线，三源全 mock）。

夹具取自 2026-09-10 实测的真实数据形态：
- 百度源列：日期/时间/地区/事件/公布/预期/前值/重要性（另有 国家/统计周期 两列）
- 同一发布被拆成 月率/年率/年初至今/读数 多条（加拿大 8 月 CPI 实测 10+ 条）
- 中国区 84 行里 60 行是噪音（上期所每日仓单）
- `重要性` 只出现 1/2 两档且 str/float 混型；噪音行同样有值 → 不可作筛选器
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import macro_calendar as mc  # noqa: E402


# ── safe_importance：类型不一致（实测 str '1' / float 1.0 混用）──────────

@pytest.mark.parametrize("raw,expected", [
    ("1", 1), ("2", 2), (1.0, 1), (2.0, 2),
    (None, None), (float("nan"), None), ("nan", None), ("", None), ("x", None),
])
def test_safe_importance_four_forms(raw, expected):
    assert mc.safe_importance(raw) == expected


# ── 归组：同一发布的多口径条目折叠为一个日历条目 ─────────────────────────

_SAME_RELEASE = [
    ("中国8月社会消费品零售总额年率(%)", "2026-09-15", "10:00"),
    ("中国8月社会消费品零售总额月率(%)", "2026-09-15", "10:00"),
    ("中国8月社会消费品零售总额年率-年初至今(%)", "2026-09-15", "10:00"),
]


def test_group_same_release_collapses_but_keeps_subitems():
    """同发布的 年率/月率/年初至今 折叠为 1 条，子项不得丢失。"""
    events = [mc.MacroEvent(date=d, time=t, region="中国", title=title,
                            period="", importance="中", source="baidu", note="")
              for title, d, t in _SAME_RELEASE]
    grouped = mc.group_events(events)
    assert len(grouped) == 1, f"应折叠为 1 条，实际 {len(grouped)}"
    assert len(grouped[0].sub_items) == 3, "子项须完整保留（防折叠即丢信息）"


def test_family_collapse_takes_max_importance():
    """同发布折叠后档位取族内**最高**——发布的重要性等于其最重要分项。

    回归（实测观察到）：取首条使 FRED 标为「高」的 PCE 并入百度分项后降成「中」，
    该发布整个从头部重点段消失。
    """
    events = [
        mc.MacroEvent("2026-09-30", "20:30", "美国", "美国8月PCE物价指数(%)",
                      "", "中", "baidu", "", family="美国PCE"),
        mc.MacroEvent("2026-09-30", "", "美国", "美国PCE与个人收入",
                      "", "高", "fred", "", family="美国PCE"),
    ]
    grouped = mc.group_events(events)
    assert len(grouped) == 1, "同 family 应折叠为一条"
    assert grouped[0].importance == "高", "折叠后应取族内最高档"


def test_family_collapse_label_counts_items():
    """折叠后标题 = family（N 项）；单项时保留原始标题（不显示「1 项」）。"""
    multi = [
        mc.MacroEvent("2026-09-11", "20:30", "美国", "美国8月CPI(%)", "", "高", "baidu",
                      "", family="美国CPI"),
        mc.MacroEvent("2026-09-11", "20:30", "美国", "美国8月核心CPI(%)", "", "高", "baidu",
                      "", family="美国CPI"),
    ]
    assert mc.group_events(multi)[0].title == "美国CPI（2 项）"
    single = [mc.MacroEvent("2026-09-11", "20:30", "美国", "美国8月CPI(%)", "", "高",
                            "baidu", "", family="美国CPI")]
    assert mc.group_events(single)[0].title == "美国8月CPI(%)", "单项应保留原始标题"


def test_group_keeps_distinct_releases_separate():
    """不同发布不得被误折叠（社零 vs 工业增加值）。"""
    events = [
        mc.MacroEvent("2026-09-15", "10:00", "中国", "中国8月社会消费品零售总额年率(%)",
                      "", "中", "baidu", ""),
        mc.MacroEvent("2026-09-15", "10:00", "中国", "中国8月规模以上工业增加值年率-单月(%)",
                      "", "中", "baidu", ""),
    ]
    assert len(mc.group_events(events)) == 2


# ── 噪音过滤：频率检测 + 极窄 pattern ────────────────────────────────────

def _rows(*, name, dates, time="15:10", region="中国"):
    return [{"地区": region, "事件": name, "日期": d, "时间": time, "前值": 1.0}
            for d in dates]


def test_daily_family_detected_by_frequency(monkeypatch):
    """同一事件名出现于 ≥5 个不同日期且时刻相同 → 判每日类噪音。"""
    rows = _rows(name="中国9月15日上期所每日仓单变动-铜(吨)",
                 dates=[f"2026-09-{d:02d}" for d in range(11, 18)])
    kept, dropped, families = mc.filter_noise(rows, patterns=[], min_days=5)
    assert kept == [] and dropped == 7
    assert families and "仓单" in families[0]


def test_weekly_monthly_not_mistaken_for_daily():
    """周频/月频事件在窗口内只出现 1-2 次 → 不得被频率检测误杀。"""
    rows = _rows(name="美国9月ISM制造业PMI", dates=["2026-10-01"], region="美国",
                 time="22:00")
    kept, dropped, _ = mc.filter_noise(rows, patterns=[], min_days=5)
    assert len(kept) == 1 and dropped == 0


def test_narrow_pattern_does_not_kill_real_inventory_event():
    """⚠️ pattern 必须窄：宽泛的「库存」会误杀 EIA 原油库存这类真事件。"""
    rows = _rows(name="美国截至9月5日当周EIA原油库存变动(万桶)",
                 dates=["2026-09-10"], region="美国", time="22:30")
    kept, dropped, _ = mc.filter_noise(rows, patterns=["仓单日报|上期所仓单"], min_days=5)
    assert len(kept) == 1, "EIA 原油库存被误杀——pattern 过宽"
    assert dropped == 0


def test_curated_pattern_matches_known_noise_family():
    rows = _rows(name="中国9月15日上期所每日仓单变动-黄金(千克)", dates=["2026-09-15"])
    kept, dropped, families = mc.filter_noise(rows, patterns=["上期所每日仓单"], min_days=5)
    assert kept == [] and dropped == 1 and families


# ── 百度源：多日状态机（ok/empty/failed）与覆盖边界 ──────────────────────

def _baidu_df(rows):
    return pd.DataFrame(rows, columns=["日期", "时间", "地区", "事件", "公布", "预期",
                                       "前值", "重要性"])


def test_cn_calendar_tracks_coverage_and_failed_days(monkeypatch):
    """coverage_end = 最后一个非空成功日（观测值）；失败日单独记录，不得当空窗。"""
    calls = {}

    def fake(date, cookie=None):
        calls[date] = True
        if date == "20260913":
            raise RuntimeError("HTTPError 502")
        if date == "20260914":
            return _baidu_df([])           # 真空窗
        return _baidu_df([["2026-09-12", "10:00", "中国", "中国8月社会消费品零售总额年率(%)",
                           None, None, 0.6, 2]])

    monkeypatch.setattr(mc, "_fetch_baidu_day", fake)
    res = mc.fetch_baidu_calendar("20260912", "20260914", retries=1)
    assert res.failed_days == ["20260913"], "失败日须显式记录"
    assert res.coverage_end == "2026-09-12", "覆盖边界取最后非空成功日"
    assert res.error is None, "部分失败不改变源可用性"
    assert len(res.events) == 1


def test_cn_calendar_all_failed_reports_error_not_empty(monkeypatch):
    """全失败 → error 非空（渲染层据此出「❌ 不可得」，绝不打印「无事件」）。"""
    def boom(date, cookie=None):
        raise RuntimeError("HTTPError 502")

    monkeypatch.setattr(mc, "_fetch_baidu_day", boom)
    res = mc.fetch_baidu_calendar("20260912", "20260913", retries=1)
    assert res.error is not None
    assert res.events == []
    assert len(res.failed_days) == 2


def test_cn_calendar_all_empty_days_is_explicit_unavailable(monkeypatch):
    """整窗**每天都是合法空窗**（无一失败）→ 仍判不可得，不得渲染成「无排期」。

    源侧空帧（反爬/限流）与「窗口内确实没有排期」在这一层不可区分；池模式对
    同一风险已有同款守卫（全池空返回 → exit 3），本处补齐（R2 review P0）。
    """
    monkeypatch.setattr(mc, "_fetch_baidu_day", lambda date, cookie=None: _baidu_df([]))
    res = mc.fetch_baidu_calendar("20260912", "20260914", retries=1)
    assert res.events == []
    assert res.error and "不等于" in res.error, f"整窗空须判不可得: {res.error!r}"
    assert res.ok_days == 0 and res.empty_days == 3


def test_cn_calendar_one_ok_day_not_judged_unavailable(monkeypatch):
    """只要有一天取到数据 → 照常输出（守卫不得过度触发）。"""
    def fake(date, cookie=None):
        if date == "20260913":
            return _baidu_df([])
        return _baidu_df([["2026-09-12", "10:00", "中国", "中国8月CPI年率(%)",
                           None, None, 0.5, 1]])

    monkeypatch.setattr(mc, "_fetch_baidu_day", fake)
    res = mc.fetch_baidu_calendar("20260912", "20260913", retries=1)
    assert res.error is None and len(res.events) == 1 and res.empty_days == 1


def test_cn_calendar_retries_transient_failure(monkeypatch):
    """单日失败须重试（实测失败率 ~12%）；重试成功则不计入 failed_days。"""
    attempts = {"n": 0}

    def flaky(date, cookie=None):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise RuntimeError("HTTPError 502")
        return _baidu_df([["2026-09-12", "10:00", "中国", "中国8月CPI年率(%)",
                           None, None, 0.5, 1]])

    monkeypatch.setattr(mc, "_fetch_baidu_day", flaky)
    res = mc.fetch_baidu_calendar("20260912", "20260912", retries=2)
    assert res.failed_days == [] and len(res.events) == 1


def test_cn_calendar_region_fallback_and_period_passthrough(monkeypatch):
    """地区列缺失时回退 国家；统计周期若有则透传（不推测）。"""
    def fake(date, cookie=None):
        return pd.DataFrame([
            {"日期": "2026-09-15", "时间": "10:00", "国家": "中国",
             "事件": "中国8月社会消费品零售总额年率(%)", "公布": None, "预期": None,
             "前值": 0.6, "重要性": "2", "统计周期": "2026年8月"},
        ])

    monkeypatch.setattr(mc, "_fetch_baidu_day", fake)
    res = mc.fetch_baidu_calendar("20260915", "20260915", retries=1)
    assert res.events[0].region == "中国"
    assert res.events[0].period == "2026年8月"


def test_cn_calendar_nan_region_falls_back_to_country(monkeypatch):
    """DataFrame 补出的 NaN 是 truthy，仍须按国家列保留该事件。"""
    def fake(date, cookie=None):
        return pd.DataFrame([{
            "日期": "2026-09-15", "时间": "10:00", "地区": float("nan"), "国家": "中国",
            "事件": "中国8月社会消费品零售总额年率(%)", "前值": 0.6, "重要性": "2",
        }])

    monkeypatch.setattr(mc, "_fetch_baidu_day", fake)
    res = mc.fetch_baidu_calendar("20260915", "20260915", retries=1)
    assert [e.region for e in res.events] == ["中国"]


@pytest.mark.parametrize("contents,needle", [
    (None, "缺失"),
    ("[broken", "解析失败"),
    ("- not-a-mapping", "根节点须为映射"),
])
def test_load_rules_fails_loud_for_unusable_config(tmp_path, contents, needle):
    path = tmp_path / "macro.yaml"
    if contents is not None:
        path.write_text(contents, encoding="utf-8")
    with pytest.raises(ValueError, match=needle):
        mc.load_rules(path)


def _load_rules_strict(path: Path) -> dict:
    """按 YAML 加载策展表，**重复键即报错**（PyYAML 默认静默取后者）。"""
    import yaml

    class Strict(yaml.SafeLoader):
        pass

    def _no_dup(loader, node, deep=False):
        seen = set()
        for k, _v in node.value:
            key = loader.construct_object(k, deep=deep)
            if key in seen:
                raise AssertionError(f"策展表重复键: {key!r}（YAML 静默取后者）")
            seen.add(key)
        return yaml.SafeLoader.construct_mapping(loader, node, deep=deep)

    Strict.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _no_dup)
    return yaml.load(path.read_text(encoding="utf-8"), Loader=Strict)


def test_real_rules_table_is_wellformed():
    """真表须无重复键，且每区的 family 指向本区（不得跨区）。

    实测踩过两处：① 美国表里 `个人收入` 重复，后者把前者的档位悄悄覆盖；
    ② 用全局关键词映射批量补 family，使美国表的 CPI 被标成「中国CPI」。
    """
    path = Path(__file__).resolve().parent.parent / "references" / "macro_sources.yaml"
    data = _load_rules_strict(path)
    table = data["region_keywords"]
    assert table, "白名单表为空"
    for region, kws in table.items():
        for kw, spec in kws.items():
            fam = str(spec.get("family") or "")
            assert fam.startswith(region), \
                f"{region} 表的 {kw!r} family={fam!r} 指向了别的区域"


def test_real_rules_noise_min_days_leaves_room_for_weekly():
    """真表的阈值须**大于**窗内单星期几最大出现次数（31 天含端点 → 5），
    否则周频（「初请」「EIA 周报」）会被当每日类噪音丢掉。"""
    path = Path(__file__).resolve().parent.parent / "references" / "macro_sources.yaml"
    data = _load_rules_strict(path)
    assert int(data["noise_min_days"]) > 5, "阈值 ≤5 会误杀周频事件"
    assert mc.DEFAULT_NOISE_MIN_DAYS > 5, "代码默认值同样须留余量"


def test_region_keywords_apply_per_region(monkeypatch):
    """白名单按**区域**分表：日本事件用日本表判档，不得套用中国表。"""
    rules = {"region_keywords": {
        "中国": {"社会消费品零售总额": {"importance": "高"}},
        "日本": {"全国CPI": {"importance": "中"}},
    }}

    def fake(date, cookie=None):
        return pd.DataFrame([
            ["2026-09-15", "10:00", "中国", "中国8月社会消费品零售总额年率(%)",
             None, None, 0.6, 2],
            ["2026-09-15", "07:30", "日本", "日本8月全国CPI年率(%)", None, None, 3.1, 2],
        ], columns=["日期", "时间", "地区", "事件", "公布", "预期", "前值", "重要性"])

    monkeypatch.setattr(mc, "_fetch_baidu_day", fake)
    res = mc.fetch_baidu_calendar("20260915", "20260915", regions=("中国", "日本"),
                                  rules=rules, retries=1)
    got = {(e.region, e.importance) for e in res.events if "社会消费品零售总额" in e.title}
    assert got == {("中国", "高")}
    got_jp = {(e.region, e.importance) for e in res.events if "全国CPI" in e.title}
    assert got_jp == {("日本", "中")}


def test_cn_keyword_mapping_sets_curated_importance(monkeypatch):
    """命中的白名单条目须带**策展档位**；源「重要性」只作备注原值保留。

    源列只有 1/2 两档且噪音行同样有值 → 不能作为强度依据（在 macro_sources.yaml
    的 region_keywords 里策展）。
    """
    rules = {"region_keywords": {"中国": {"社会消费品零售总额": {"label": "中国社零", "importance": "高"}}}}

    def fake(date, cookie=None):
        return _baidu_df([["2026-09-15", "10:00", "中国",
                           "中国8月社会消费品零售总额年率(%)", None, None, 0.6, 2]])

    monkeypatch.setattr(mc, "_fetch_baidu_day", fake)
    res = mc.fetch_baidu_calendar("20260915", "20260915", rules=rules, retries=1)
    assert res.events[0].importance == "高"
    assert "源重要性:2" in res.events[0].note, "源重要性须保留为备注原值"


# ── FRED 源：(release_id, release_name) 对白名单 + 漂移 fail-loud ────────

_RULES = {
    "us_releases": [
        {"id": 10, "name": "Consumer Price Index", "label": "美国CPI", "importance": "高"},
        {"id": 46, "name": "Producer Price Index", "label": "美国PPI", "importance": "高"},
    ],
}


def test_us_calendar_whitelist_excludes_daily_fomc_noise(monkeypatch):
    """白名单命中项进表；「FOMC Press Release」这类每日噪音天然被排除。"""
    payload = {"release_dates": [
        {"release_id": 10, "release_name": "Consumer Price Index", "date": "2026-10-14"},
        {"release_id": 46, "release_name": "Producer Price Index", "date": "2026-10-15"},
        {"release_id": 101, "release_name": "FOMC Press Release", "date": "2026-10-14"},
        {"release_id": 101, "release_name": "FOMC Press Release", "date": "2026-10-15"},
    ]}
    monkeypatch.setattr(mc, "_get_fred_release_dates", lambda start, end, key: payload)
    res = mc.fetch_us_calendar("2026-09-10", "2026-12-31", fred_key="x" * 32, rules=_RULES)
    titles = [e.title for e in res.events]
    assert "美国CPI" in titles and "美国PPI" in titles
    assert not any("FOMC" in t for t in titles), "每日噪音须被白名单排除"


def test_us_calendar_reports_config_drift_fail_loud(monkeypatch):
    """release_id 命中但 name 不符 → 排除该行并报配置漂移（绝不静默纳入错数据）。"""
    payload = {"release_dates": [
        {"release_id": 10, "release_name": "Consumer Price Index (RENAMED)",
         "date": "2026-10-14"},
    ]}
    monkeypatch.setattr(mc, "_get_fred_release_dates", lambda start, end, key: payload)
    res = mc.fetch_us_calendar("2026-09-10", "2026-12-31", fred_key="x" * 32, rules=_RULES)
    assert res.events == [], "name 不符不得纳入"
    assert any("漂移" in n for n in res.notes), "须显式报配置漂移"


def test_us_calendar_without_key_is_explicit_degradation(monkeypatch):
    """无 FRED_API_KEY → 显式降级（error 非空），不得静默返回空表。"""
    res = mc.fetch_us_calendar("2026-09-10", "2026-12-31", fred_key=None, rules=_RULES)
    assert res.error is not None and "FRED_API_KEY" in res.error
    assert res.events == []


def test_us_calendar_no_time_field_not_invented(monkeypatch):
    """FRED 不提供公布时刻 → time 留空并注明，不得用常识补 8:30 ET。"""
    payload = {"release_dates": [
        {"release_id": 10, "release_name": "Consumer Price Index", "date": "2026-10-14"},
    ]}
    monkeypatch.setattr(mc, "_get_fred_release_dates", lambda start, end, key: payload)
    res = mc.fetch_us_calendar("2026-09-10", "2026-12-31", fred_key="x" * 32, rules=_RULES)
    assert res.events[0].time == ""
    assert "时刻" in res.events[0].note


# ── FOMC 策展表：过期/缺失须显式告警，不得渲染成「无议息」 ────────────────

_FOMC_YAML = """
last_verified: "2026-09-10"
source_url: "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"
meetings:
  - {start: "2026-01-27", end: "2026-01-28", sep: false}
  - {start: "2026-09-15", end: "2026-09-16", sep: true}
  - {start: "2026-10-27", end: "2026-10-28", sep: false}
  - {start: "2026-12-08", end: "2026-12-09", sep: true}
"""


def test_fomc_filters_past_meetings_and_emits_end_date(tmp_path):
    """只返回未过去的会议；会议以「决议公布日」= 结束日 作为事件日。"""
    p = tmp_path / "fomc.yaml"
    p.write_text(_FOMC_YAML, encoding="utf-8")
    events, warnings = mc.load_fomc_meetings(p, today="2026-09-10")
    assert [e.date for e in events] == ["2026-09-17", "2026-10-29", "2026-12-10"]
    assert warnings == []
    assert "暂定" in events[0].note, "须标注官方的不确定性声明"


def test_fomc_expired_table_warns_and_does_not_claim_none(tmp_path):
    """表已过期 → 显式告警（渲染层据此出「不含议息」而非「无议息」）。"""
    p = tmp_path / "fomc.yaml"
    p.write_text(_FOMC_YAML, encoding="utf-8")
    events, warnings = mc.load_fomc_meetings(p, today="2027-03-01")
    assert events == []
    assert warnings and "过期" in warnings[0]
    assert "2026-12-09" in warnings[0], "告警须写明覆盖至何时"


def test_fomc_date_converted_to_beijing_next_day(tmp_path):
    """议息按**北京日期**归并：美东 14:00 决议 → 北京次日凌晨（跨日）。

    源的日期是「美东决议日」；统一轴为北京日期，必须 +1 天，否则用户按
    北京日期查看时会错过一整天（14:00 ET + 12/13h = 次日 02:00/03:00 北京）。
    """
    p = tmp_path / "fomc.yaml"
    p.write_text(_FOMC_YAML, encoding="utf-8")
    events, _ = mc.load_fomc_meetings(p, today="2026-09-10")
    assert [e.date for e in events] == ["2026-09-17", "2026-10-29", "2026-12-10"], \
        "议息须换算到北京次日"
    assert "美东" in events[0].note and "14:00" in events[0].note, \
        "须注明美东决议时刻与其来源"


def test_fomc_missing_or_corrupt_file_is_explicit(tmp_path):
    """缺失/坏 YAML → 显式不可得，不裸 traceback。"""
    missing = tmp_path / "nope.yaml"
    events, warnings = mc.load_fomc_meetings(missing, today="2026-09-10")
    assert events == [] and warnings and "不可得" in warnings[0]

    bad = tmp_path / "bad.yaml"
    bad.write_text("meetings: [this is: not valid: yaml", encoding="utf-8")
    events2, warnings2 = mc.load_fomc_meetings(bad, today="2026-09-10")
    assert events2 == [] and warnings2 and "不可得" in warnings2[0]


# ── 端到端拼接：短窗（中国）+ 长窗（美国）+ 策展（FOMC）───────────────

def test_build_view_merges_three_sources_and_labels_coverage():
    cn = mc.SourceResult("百度", [mc.MacroEvent("2026-09-15", "10:00", "中国",
                                                "中国8月社会消费品零售总额年率(%)",
                                                "", "中", "baidu", "")],
                         coverage_end="2026-10-10")
    us = mc.SourceResult("FRED", [mc.MacroEvent("2026-10-14", "", "美国", "美国CPI",
                                                "", "高", "fred", "源不提供时刻")],
                         coverage_end="2026-12-31")
    fomc = mc.SourceResult("策展表", [mc.MacroEvent("2026-09-16", "", "美国", "FOMC 议息会议",
                                                    "", "高", "fomc", "")],
                           coverage_end="2026-12-09")
    view = mc.build_view([cn, us, fomc])
    assert view["coverage"]["中国"] == "2026-10-10"
    assert view["coverage"]["美国"] == "2026-12-31"
    regions = {e.region for e in view["events"]}
    assert regions == {"中国", "美国"}
    # 时间升序
    dates = [e.date for e in view["events"]]
    assert dates == sorted(dates)


def test_fred_short_label_subsumed_by_detailed_baidu_entry():
    """同一 (日期, 区域) 内 FRED 短标签若已被百度源更细条目覆盖 → 不重复展示。

    实测：10-02 同时出现「美国9月非农就业人口变动(万)」（百度，带时刻）与
    「美国非农就业」（FRED，仅日期）——重点列表里会读成两条不同事件。
    """
    baidu = mc.SourceResult("百度", [mc.MacroEvent(
        "2026-10-02", "20:30", "美国", "美国9月非农就业人口变动(万)", "", "高", "baidu", "")])
    fred = mc.SourceResult("FRED", [mc.MacroEvent(
        "2026-10-02", "", "美国", "美国非农就业", "", "高", "fred", "美东日期口径")])
    view = mc.build_view([baidu, fred])
    assert [e.title for e in view["events"]] == ["美国9月非农就业人口变动(万)"]


def test_fred_entry_kept_when_no_baidu_counterpart():
    """无百度对应项的 FRED 条目必须保留（长窗能力不能因去重丢失）。"""
    fred = mc.SourceResult("FRED", [mc.MacroEvent(
        "2026-10-14", "", "美国", "美国CPI", "", "高", "fred", "美东日期口径")])
    view = mc.build_view([fred])
    assert [e.title for e in view["events"]] == ["美国CPI"]


def test_dedupe_does_not_cross_dates_or_regions():
    """去重仅限同一 (日期, 区域)——不同日期/区域的短标签不得被误吞。"""
    baidu = mc.SourceResult("百度", [mc.MacroEvent(
        "2026-10-02", "20:30", "美国", "美国9月非农就业人口变动(万)", "", "高", "baidu", "")])
    fred = mc.SourceResult("FRED", [mc.MacroEvent(
        "2026-10-03", "", "美国", "美国非农就业", "", "高", "fred", "")])
    view = mc.build_view([baidu, fred])
    assert len(view["events"]) == 2


def test_build_view_surfaces_source_errors_not_empty():
    """任一源 error 必须出现在 view 的可渲染位置（渲染层据此出「❌ 不可得」）。"""
    bad = mc.SourceResult("FRED", [], error="未配置 FRED_API_KEY")
    view = mc.build_view([bad])
    assert view["errors"] and "FRED_API_KEY" in view["errors"][0]


# ── P0-3：--baidu-cookie 必须真的传下去 ─────────────────────────────────

def test_baidu_cookie_is_forwarded_to_akshare(monkeypatch):
    """`--baidu-cookie` 端到端有效——此前收下就丢，旗标是空操作。

    SKILL.md 称「复用可显著降低逐日失败率」（实测单次失败率 ≈12%，且本会话
    因连续探测被限流 403）；若值不落到 akshare，30–90 次逐日请求仍是无 cookie，
    且**没有任何提示**说明该旗标未生效。
    """
    import sys

    seen: list = []

    class _Ak:
        @staticmethod
        def news_economic_baidu(date, cookie=None):
            seen.append(cookie)
            return _baidu_df([])

    monkeypatch.setitem(sys.modules, "akshare", _Ak())
    mc._fetch_baidu_day("20260915", "BDUSS=abc")
    assert seen == ["BDUSS=abc"], f"cookie 未透传给 akshare: {seen}"


def test_baidu_cookie_flows_through_calendar_fetch(monkeypatch):
    """装配层（fetch_baidu_calendar）到单日取数的链路须保持 cookie。"""
    seen: list = []

    def fake_day(date, cookie=None):
        seen.append(cookie)
        return _baidu_df([])

    monkeypatch.setattr(mc, "_fetch_baidu_day", fake_day)
    mc.fetch_baidu_calendar("20260915", "20260915", cookie="X", retries=1)
    assert seen == ["X"]


# ── P2-2：策展表手工编辑容错（两份表都是年度人工维护资产）─────────────

def test_fomc_unpadded_date_normalized_with_warning(tmp_path):
    """未补零日期（2026-9-16）→ 归一化并告警，**不得 traceback 崩掉 --macro**。

    FOMC 表是文件自身要求用户**每年手工誊录**的资产，手误是预期内的；
    `date.fromisoformat('2026-9-16')` 会抛 ValueError，而资源串比较又会把
    `'2026-9-16'` 按字典序误判为未来——两者都在 try 之外。
    """
    p = tmp_path / "fomc.yaml"
    p.write_text('last_verified: "2026-09-10"\n'
                 'meetings:\n'
                 '  - {start: "2026-09-15", end: "2026-9-16", sep: true}\n',
                 encoding="utf-8")
    events, warnings = mc.load_fomc_meetings(p, today="2026-09-10")
    assert [e.date for e in events] == ["2026-09-17"], "应归一化并换算北京次日"
    assert any(("补零" in w) or ("格式" in w) for w in warnings), f"须告警: {warnings}"


def test_fomc_garbage_date_warns_not_crashes(tmp_path):
    p = tmp_path / "fomc.yaml"
    p.write_text('meetings:\n  - {start: "2026-09-15", end: "garbage", sep: false}\n',
                 encoding="utf-8")
    events, warnings = mc.load_fomc_meetings(p, today="2026-09-10")
    assert events == []
    assert warnings, "无法解析的日期须显式告警"


def test_us_calendar_non_numeric_release_id_is_explicit(monkeypatch):
    """规则表 id 非数字 → 显式报配置问题；合法条目照常纳入，不得整体崩。"""
    payload = {"release_dates": [
        {"release_id": 10, "release_name": "Consumer Price Index", "date": "2026-10-14"}]}
    monkeypatch.setattr(mc, "_get_fred_release_dates", lambda s, e, k: payload)
    rules = {"us_releases": [
        {"id": "unemployment", "name": "Unemployment Rate", "label": "美国失业率"},
        {"id": 10, "name": "Consumer Price Index", "label": "美国CPI"}]}
    res = mc.fetch_us_calendar("2026-09-10", "2026-12-31", fred_key="x" * 32, rules=rules)
    assert [e.title for e in res.events] == ["美国CPI"], "合法条目不得连坐"
    assert any("id" in n for n in res.notes), f"非法 id 须显式报告: {res.notes}"


# ── 策展表健壮性（R2 review P1）：手改 YAML 不得让整份 --macro 消失 ──────
# 该表是**手工维护**资产，手误属预期内；本模块对坏 id 已有 fail-soft 先例，
# 下列三条把同一标准补齐到 非映射行 / 坏正则 / 坏阈值。

def test_us_calendar_scalar_entry_warns_instead_of_crashing(monkeypatch):
    """`us_releases` 混入标量行 → 跳过 + 配置告警；不得 AttributeError 直穿 CLI。"""
    monkeypatch.setattr(mc, "_get_fred_release_dates",
                        lambda s, e, k: {"release_dates": []})
    rules = {"us_releases": ["美国CPI",
                             {"id": 10, "name": "Consumer Price Index", "label": "美国CPI"}]}
    res = mc.fetch_us_calendar("20260901", "20261001", fred_key="k", rules=rules)
    assert res.error is None
    assert any("非映射" in n for n in res.notes), f"标量行须显式告警: {res.notes}"


def test_baidu_bad_noise_rules_degrade_with_note(monkeypatch):
    """坏 noise_patterns（正则不闭合）/ 坏 noise_min_days（非数字）→ 告警 + 降级，不崩。"""
    def fake(date, cookie=None):
        return _baidu_df([["2026-09-12", "10:00", "中国", "中国8月CPI年率(%)",
                           None, None, 0.5, 1]])

    monkeypatch.setattr(mc, "_fetch_baidu_day", fake)
    rules = {"noise_patterns": ["上期所(每日"], "noise_min_days": "abc"}
    res = mc.fetch_baidu_calendar("20260912", "20260912", retries=1, rules=rules)
    assert res.error is None
    assert any("noise_patterns" in n for n in res.notes), f"须报正则不可编译: {res.notes}"
    assert any("noise_min_days" in n for n in res.notes), f"须报阈值不可解析: {res.notes}"
    assert len(res.events) == 1, "配置降级后事件仍须照常输出"


# ── 频率阈值须留余量（R2 review P1）─────────────────────────────────────
# 窗口是 31 天**含端点**（date_range(30)），故某个星期几必然出现 5 次——
# 阈值 5 会把标题/时刻稳定的**周频**序列当成每日类噪音丢弃，而白名单刻意
# 保留「初请」「EIA 周报」。阈值须大于窗口内单星期几的最大出现次数。

def test_weekly_series_with_five_occurrences_survives_frequency_check():
    dates = ["2026-09-03", "2026-09-10", "2026-09-17", "2026-09-24", "2026-10-01"]
    rows = _rows(name="美国截至当周初请失业金人数", dates=dates, region="美国", time="20:30")
    kept, dropped, _ = mc.filter_noise(rows, patterns=[], min_days=mc.DEFAULT_NOISE_MIN_DAYS)
    assert len(kept) == 5 and dropped == 0, "周频事件被当成每日类噪音丢弃"


def test_daily_series_still_filtered_at_default_threshold():
    """提高阈值不得放过真正的每日类噪音（31 天窗内工作日约 22 次）。"""
    dates = [f"2026-09-{d:02d}" for d in range(1, 29)]
    rows = _rows(name="中国9月15日上期所每日仓单变动-铜(吨)", dates=dates)
    kept, dropped, families = mc.filter_noise(rows, patterns=[],
                                              min_days=mc.DEFAULT_NOISE_MIN_DAYS)
    assert kept == [] and dropped == 28 and families


# ── 议息会议：北京日期口径下的退场时机（R2 review P1）──────────────────
# 事件日 = 美东结束日 +1。过滤若拿**结束日**与 today 比，会议会在真正发生的
# 那天（北京当日）从表里消失，并同时谎报「策展表已过期」——而表是最新的。

def test_fomc_meeting_survives_on_its_beijing_day(tmp_path):
    p = tmp_path / "fomc.yaml"
    p.write_text(_FOMC_YAML, encoding="utf-8")
    events, warnings = mc.load_fomc_meetings(p, today="2026-09-17")
    assert [e.date for e in events] == ["2026-09-17", "2026-10-29", "2026-12-10"], \
        "会议在其北京当日（09-17）须仍在表内"
    assert not any("过期" in w for w in warnings), f"谎报策展表过期: {warnings}"


def test_fomc_meeting_retires_after_its_beijing_day(tmp_path):
    """北京日期已过 → 该会议退场（不得永久驻留）。"""
    p = tmp_path / "fomc.yaml"
    p.write_text(_FOMC_YAML, encoding="utf-8")
    events, _ = mc.load_fomc_meetings(p, today="2026-09-18")
    assert "2026-09-17" not in [e.date for e in events]


# ── R-D04 政治/宏观不确定性窗口 ──────────────────────────────────────────

def test_political_windows_load_from_curated_table():
    out = mc.load_political_windows()
    assert out["available"] is True, out.get("reason")
    assert out["windows"], "策展表应有窗口条目"
    for w in out["windows"]:
        assert w["name"] and w["region"] and w["start"]
        assert w["evidence_note"], "每条须带机制注记"


def test_political_window_note_says_direction_unknown():
    """C4 裁决：只支持「不确定性窗口」，**不支持方向** → 注记必须含「方向未知」。"""
    out = mc.load_political_windows()
    for w in out["windows"]:
        assert "方向未知" in w["evidence_note"]
        assert "机制证据" in w["evidence_note"]


def test_political_render_has_no_direction_prediction():
    text = mc.render_political_windows()
    for banned in ("将上涨", "将下跌", "看多", "看空", "利好", "利空", "建议"):
        assert banned not in text, f"政治窗口不得含方向/建议语义：{banned}"


def test_political_table_rejects_rewritten_mechanism_note(tmp_path):
    """机制注记被改写掉「方向未知」→ **拒绝输出**（防静默变成方向性表述）。"""
    f = tmp_path / "p.yaml"
    f.write_text("last_verified: '2026-09-12'\nmechanism_note: '不确定性抬升'\n"
                 "windows:\n  - name: X\n    region: 美国\n    start: '2026-11-03'\n",
                 encoding="utf-8")
    out = mc.load_political_windows(f)
    assert out["available"] is False
    assert "方向未知" in out["reason"]


def test_political_missing_table_is_three_state(tmp_path):
    out = mc.load_political_windows(tmp_path / "nope.yaml")
    assert out["available"] is False and "缺失" in out["reason"]


@pytest.mark.parametrize("contents", ["- name: X", "just-a-scalar"])
def test_political_nonmapping_root_is_explicit_unavailable(tmp_path, contents):
    p = tmp_path / "political.yaml"
    p.write_text(contents, encoding="utf-8")
    out = mc.load_political_windows(p)
    assert out["available"] is False
    assert "根节点须为映射" in out["reason"]


# ── R-D04 政治窗口：fail-soft / 过期标注 / 接线（轮末评审修复 2026-09-13）─────

def _pol(tmp_path, windows_yaml: str, *, note="不确定性窗口：机制证据，方向未知"):
    p = tmp_path / "political.yaml"
    p.write_text(f"mechanism_note: {note}\nwindows:\n{windows_yaml}", encoding="utf-8")
    return p


def test_political_malformed_entry_fails_soft(tmp_path):
    """裸标量条目（YAML 把 `- 2026-11-03` 解析成 date）→ **available=False**，不得 traceback。

    契约写的是「策展表解析失败 → available=False」，而 `w.get(...)` 原在 try 之外
    → AttributeError 直接冒泡，调用方拿到的是异常而不是可渲染的降级结果。
    断言须点名**条目结构异常**——泛化的「解析失败」会被 YAML 语法错误顶替，掩盖该分支。
    """
    p = _pol(tmp_path, "  - 2026-11-03\n")
    out = mc.load_political_windows(p, today="2026-09-13")
    assert out["available"] is False
    assert "结构异常" in out["reason"], out["reason"]
    assert out["windows"] == []


def test_political_status_marks_expired_not_dropped(tmp_path):
    """过期条目须**保留并标注**，不得静默消失（策展表自述的维护纪律）。"""
    p = _pol(tmp_path, (
        "  - name: 去年选举\n    region: 美国\n    type: 选举\n"
        "    start: 2025-11-03\n    end: 2025-11-03\n"
        "  - name: 今年选举\n    region: 美国\n    type: 选举\n"
        "    start: 2026-11-03\n    end: 2026-11-03\n"
        "  - name: 会议期\n    region: 中国\n    type: 政策\n"
        "    start: 2026-09-01\n    end: 2026-09-30\n"))
    out = mc.load_political_windows(p, today="2026-09-13")
    assert out["available"] is True
    st = {w["name"]: w["status"] for w in out["windows"]}
    assert st == {"去年选举": "已过期", "今年选举": "未开始", "会议期": "进行中"}


def test_political_render_shows_status_and_keeps_expired(tmp_path):
    p = _pol(tmp_path, (
        "  - name: 去年选举\n    region: 美国\n    type: 选举\n"
        "    start: 2025-11-03\n    end: 2025-11-03\n"))
    text = mc.render_political_windows(path=p, today="2026-09-13")
    assert "状态" in text and "已过期" in text
    assert "去年选举" in text, "过期窗口不得从输出中消失"
    assert "方向未知" in text, "机制注记须随输出"


def test_political_mechanism_note_without_direction_is_rejected(tmp_path):
    """机制注记缺「方向未知」→ 拒绝输出（防被改写为方向性表述）。"""
    p = _pol(tmp_path, "  - name: 甲\n    start: '2026-11-03'\n", note="'可能上涨'")
    out = mc.load_political_windows(p, today="2026-09-13")
    assert out["available"] is False and "方向未知" in out["reason"]
