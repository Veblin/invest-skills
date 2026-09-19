"""`--macro` 模式的渲染与 CLI 契约测试（离线，三源全 mock）。

本文件的重点是仓库的历史缺陷模式「把源不可得渲染成无事件」——
T1/T2/T3/T6 专门防它（R1-F8 空窗鉴别、R1-F13 静默缺失同源）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import macro_calendar as mc  # noqa: E402
import unlock_calendar as uc  # noqa: E402


def _ev(date="2026-09-15", time="10:00", region="中国", title="中国8月社会消费品零售总额",
        importance="高", source="baidu", note="", period=""):
    return mc.MacroEvent(date, time, region, title, period, importance, source, note)


@pytest.fixture
def stub_sources(monkeypatch):
    """三源默认替身：可逐项改写返回值/异常。"""
    state = {
        "cn": mc.SourceResult("百度财经日历", [_ev()], coverage_end="2026-10-10", ok_days=25),
        "us": mc.SourceResult("FRED", [_ev("2026-10-14", "", "美国", "美国CPI",
                                           "高", "fred", "源不提供公布时刻")],
                              coverage_end="2026-12-31"),
        # v0.3.0 D6：日期须与**被测 CLI** 同源（uc._beijing_today）。原先硬编码
        # 2026-09-16，-macro 窗口是 [今天, 今天+days] 的**未来窗** → 该桩事件被滤掉
        # → FOMC 行落到「— 窗口内无排期」分支，连带 3 个可用性用例误挂（另有
        # test_fomc_appears_once_per_section 同因）。本文件 :253-255 已记录该修法。
        "fomc": ([_ev(uc._beijing_today().isoformat(), "", "美国", "FOMC 议息会议",
                      "高", "fomc", "官方注：暂定")], []),
    }
    monkeypatch.setattr(mc, "fetch_baidu_calendar", lambda *a, **k: state["cn"])
    monkeypatch.setattr(mc, "fetch_us_calendar", lambda *a, **k: state["us"])
    monkeypatch.setattr(mc, "load_fomc_meetings", lambda *a, **k: state["fomc"])
    # 关键：让 --macro 路径不依赖真实策展表/网络
    monkeypatch.setattr(mc, "load_rules", lambda *a, **k: {})
    return state


def _run(monkeypatch, capsys, *extra):
    monkeypatch.setattr(sys, "argv", ["unlock_calendar.py", "--macro", "--no-out", *extra])
    rc = uc.main()
    cap = capsys.readouterr()
    return rc, cap.out + cap.err      # 退出 3 的说明按仓库惯例走 stderr


# ── T1：源不可得 ≠ 无事件 ────────────────────────────────────────────────

def test_source_unavailable_renders_unavailable_not_empty(stub_sources, monkeypatch, capsys):
    """中国源全失败 → 必须出现「不可得」且**不得**出现「无日程/无事件 ✅」。"""
    stub_sources["cn"] = mc.SourceResult(
        "百度财经日历", [], error="全部 30 天取数失败（疑源故障/网络）",
        failed_days=["20260913"], ok_days=0)
    rc, out = _run(monkeypatch, capsys)
    assert rc == 0, "美国源仍可用 → 部分降级不改变退出码"
    assert "不可得" in out
    # 禁的是**肯定性全清**（会把「取数失败」读成「确实没有」）；
    # 允许并鼓励写「不可得 ≠ 无事件」这类诚实说明。
    for bad in ("无日程 ✅", "无事件 ✅", "窗口内无"):
        assert bad not in out, f"不得把不可得渲染成 {bad!r}"


def test_all_sources_unavailable_exits_3(stub_sources, monkeypatch, capsys):
    """全部宏观源不可得 → 无内容可报，退出码 3（不硬编）。"""
    stub_sources["cn"] = mc.SourceResult("百度财经日历", [], error="源故障")
    stub_sources["us"] = mc.SourceResult("FRED", [], error="未配置 FRED_API_KEY")
    stub_sources["fomc"] = ([], ["FOMC 策展表不可得（文件缺失）"])
    rc, out = _run(monkeypatch, capsys)
    assert rc == 3


def test_unusable_macro_rules_exit_3_explicitly(monkeypatch, capsys):
    """规则表不可读时不能把其造成的空筛选说成无排期。"""
    def _bad_rules(*args, **kwargs):
        raise ValueError("宏观策展规则表缺失：/tmp/macro.yaml")

    monkeypatch.setattr(mc, "load_rules", _bad_rules)
    rc, out = _run(monkeypatch, capsys)
    assert rc == 3
    assert "策展规则不可用" in out and "缺失" in out


def test_source_with_rows_but_filtered_out_is_labelled(stub_sources, monkeypatch, capsys):
    """源有返回但筛选后为空 → 覆盖矩阵须说清成因（R2 review P0 的渲染侧）。

    否则「白名单/区域过滤把条目全滤掉了」看起来与「源里没有排期」完全一样，
    而前者要改配置、后者什么都不用做。
    """
    stub_sources["cn"] = mc.SourceResult("百度财经日历", [], ok_days=31, empty_days=0)
    rc, out = _run(monkeypatch, capsys)
    assert rc == 0
    assert "筛选后无排期" in out and "源 31 天有数据" in out
    assert "— 窗口内无排期" not in out, "不得把「被筛掉」渲染成「源里没有」"


def test_all_empty_days_source_rendered_unavailable_not_no_schedule(stub_sources,
                                                                    monkeypatch, capsys):
    """整窗零成功日的源（自述 ok_days=0/empty_days=31）→ 覆盖矩阵不得出「窗口内无排期」。

    渲染层契约：源结果自述没取到任何一天数据时，「无排期」这句话是**不可说**的。
    """
    stub_sources["cn"] = mc.SourceResult("百度财经日历", [], ok_days=0, empty_days=31)
    rc, out = _run(monkeypatch, capsys)
    assert rc == 0
    assert "整窗 31 天返回空" in out and "≠ 无事件" in out
    assert "— 窗口内无排期" not in out
    assert "不可得" in out


# ── T2：部分失败须计数且由 len() 得出 ────────────────────────────────────

def test_partial_failure_banner_counts_days(stub_sources, monkeypatch, capsys):
    stub_sources["cn"] = mc.SourceResult(
        "百度财经日历", [_ev()], coverage_end="2026-10-10",
        ok_days=22, empty_days=5, failed_days=["20260913", "20260914", "20260915"])
    rc, out = _run(monkeypatch, capsys)
    assert rc == 0
    assert "3" in out and "取数失败" in out, "部分失败须显式计数"


# ── T3：无 FRED key 是显式降级，不拖垮中国段 ─────────────────────────────

def test_missing_fred_key_degrades_us_only(stub_sources, monkeypatch, capsys):
    stub_sources["us"] = mc.SourceResult(
        "FRED", [], error="未配置 FRED_API_KEY —— 美国长窗口日程不可得（≠ 无事件）")
    rc, out = _run(monkeypatch, capsys)
    assert rc == 0
    assert "FRED_API_KEY" in out
    assert "中国8月社会消费品零售总额" in out, "中国段须照常渲染"


# ── T5：噪音过滤留痕（过滤发生了必须让人知道）────────────────────────────

def test_noise_filter_is_disclosed(stub_sources, monkeypatch, capsys):
    stub_sources["cn"] = mc.SourceResult(
        "百度财经日历", [_ev()], coverage_end="2026-10-10", ok_days=25,
        filtered=60, filtered_families=["中国9月15日上期所每日仓单变动-铜(吨)"])
    rc, out = _run(monkeypatch, capsys)
    assert "60" in out and "过滤" in out, "过滤须留痕（否则无法区分「源没有」与「被滤掉」）"


# ── T6：FOMC 表过期 → 「不含议息」而非「无议息」─────────────────────────

def test_fomc_expired_says_absent_not_none(stub_sources, monkeypatch, capsys):
    stub_sources["fomc"] = ([], ["FOMC 策展表已过期（覆盖至 2026-12-09，最后核对 2026-09-10）"
                                 "——本次**不含**议息会议，请更新 references/fomc_meetings.yaml"])
    rc, out = _run(monkeypatch, capsys)
    assert rc == 0, "FOMC 是静态文件，不应把整份报告判失败"
    assert "过期" in out and "不含" in out
    assert "无议息" not in out


# ── T11：模式互斥 / 退出码 / 不得污染状态文件 ────────────────────────────

def test_macro_and_pool_file_are_mutually_exclusive(tmp_path, monkeypatch, capsys):
    """--macro 与 --pool-file 同传必须报错——现有 `if args.pool_file` 在前会静默忽略。"""
    pool = tmp_path / "pool.txt"
    pool.write_text("600176\n", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["unlock_calendar.py", "--macro",
                                      "--pool-file", str(pool), "--no-out"])
    with pytest.raises(SystemExit) as exc:
        uc.main()
    assert exc.value.code == 2


def test_macro_does_not_touch_state_file(stub_sources, tmp_path, monkeypatch, capsys):
    """macro 模式绝不能碰解禁的状态文件（会把 symbols 键写脏）。"""
    state = tmp_path / "state.json"
    _run(monkeypatch, capsys, "--state-file", str(state))
    assert not state.exists(), "macro 模式创建了状态文件"


def test_macro_days_must_be_positive(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["unlock_calendar.py", "--macro",
                                      "--macro-days", "0", "--no-out"])
    with pytest.raises(SystemExit) as exc:
        uc.main()
    assert exc.value.code == 2


# ── 渲染结构：源覆盖矩阵 + 分区段 ────────────────────────────────────────

def test_timeline_groups_by_date_across_regions(stub_sources, monkeypatch, capsys):
    """日期为统一轴：同一天的中/日/美事件进**同一行同一格**，不再按区域分段。"""
    stub_sources["cn"] = mc.SourceResult("百度财经日历", [
        _ev("2026-09-15", "10:00", "中国", "中国8月社会消费品零售总额"),
        _ev("2026-09-15", "07:30", "日本", "日本8月全国CPI"),
    ], coverage_end="2026-10-10", ok_days=25)
    rc, out = _run(monkeypatch, capsys)
    rows = [ln for ln in out.splitlines() if ln.startswith("| 2026-09-15")]
    assert rows, "时间轴缺 2026-09-15 行"
    row = rows[0]
    assert "中国8月社会消费品零售总额" in row and "日本8月全国CPI" in row, \
        "同日的中国/日本事件须在同一行"
    assert "🇨🇳" in row and "🇯🇵" in row, "格内须带区域标识"
    # 旧的按区域分段已移除
    assert "## 🇨🇳 中国" not in out and "## 🇺🇸 美国" not in out


def test_highlight_section_lists_high_impact_before_full_timeline(stub_sources,
                                                                  monkeypatch, capsys):
    """头部「重点事件」只含策展档位「高」，且位于完整日程**之前**（全集不因此减少）。"""
    stub_sources["cn"] = mc.SourceResult("百度财经日历", [
        _ev("2026-09-15", "10:00", "中国", "中国8月社会消费品零售总额", "高"),
        _ev("2026-09-27", "09:30", "中国", "中国8月规模以上工业企业利润", "中"),
    ], coverage_end="2026-10-10", ok_days=25)
    rc, out = _run(monkeypatch, capsys)

    assert "⭐ 重点事件" in out and "📅 日程" in out
    assert out.index("⭐ 重点事件") < out.index("📅 日程"), "重点段须在完整日程之前"
    highlight = out.split("⭐ 重点事件")[1].split("📡")[0]
    assert "社会消费品零售总额" in highlight, "高影响事件须入重点段"
    assert "工业企业利润" not in highlight, "中档事件不应入重点段"
    assert "工业企业利润" in out.split("📅 日程")[1], "完整日程须仍是全集"
    # 重点段必须随附降级提示（覆盖边界同样约束重点段）
    assert "覆盖" in highlight


def test_highlight_section_flags_unavailable_sources(stub_sources, monkeypatch, capsys):
    """源不可得时重点段不得给出「无高影响事件」的全清结论。"""
    stub_sources["cn"] = mc.SourceResult("百度财经日历", [], error="全部 30 天取数失败")
    stub_sources["us"] = mc.SourceResult("FRED", [], error="未配置 FRED_API_KEY")
    stub_sources["fomc"] = ([], [])
    rc, out = _run(monkeypatch, capsys)
    assert rc == 3


def test_fomc_appears_once_per_section(stub_sources, monkeypatch, capsys):
    """议息在**每个区块内**各出现一次。

    重点段与完整日程是同一批事件的两种视图（高影响事件两处都出现是设计），
    但区块内不得重复——按区域分段时期曾出现同段内重复。
    """
    rc, out = _run(monkeypatch, capsys)
    highlight = out.split("⭐ 重点事件")[1].split("📡")[0]
    timeline = out.split("📅 日程")[1]
    assert highlight.count("FOMC 议息会议") == 1, "重点段内重复"
    assert timeline.count("FOMC 议息会议") == 1, "完整日程内重复"


def test_today_events_past_their_time_are_marked(stub_sources, monkeypatch, capsys):
    """当天且时刻已过的事件须标注——否则分不清「今天这条出没出」。

    实测触发场景：报告生成于北京 22:50 时，当日 20:30 的 PPI 已出、次日 20:30 的
    CPI 未出，但表上两者看起来一样。
    """
    # 桩数据的「今天」须与**被测 CLI** 同源（_beijing_today）：用宿主本地日期会在
    # 本地日期 ≠ 北京日期的时段（UTC+13/+14，或 UTC 主机 16:00 后）必挂——本版
    # 早前几笔提交清的正是这类时间炸弹（R2 review P2）。
    today = uc._beijing_today().isoformat()
    stub_sources["cn"] = mc.SourceResult("百度财经日历", [
        _ev(today, "00:00", "中国", "中国8月社会消费品零售总额", "高"),   # 当日已过
        _ev(today, "23:59", "中国", "中国8月工业增加值", "高"),          # 当日未到
    ], coverage_end="2026-10-10", ok_days=25)
    rc, out = _run(monkeypatch, capsys)
    row = next(ln for ln in out.splitlines() if ln.startswith(f"| {today}"))
    assert "✓已过时点" in row, "当日已过时点的事件未标注"
    after = row.split("工业增加值")[1]
    assert "✓已过时点" not in after, "未到时刻的事件不应被标注"


def test_report_states_time_caliber_explicitly(stub_sources, monkeypatch, capsys):
    """须写明「时刻列 = 北京时间」——否则读者无法判断跨市场时刻是否可比。

    实测依据：百度源 61/61 个美国事件的时刻 = 公认美东发布时刻 +12h
    （8:30 ET → 20:30 北京）。这条口径不能只靠「北京口径」四个字暗示。
    """
    rc, out = _run(monkeypatch, capsys)
    line = [ln for ln in out.splitlines() if "时刻" in ln and "北京时间" in ln]
    assert line, "缺少「时刻列 = 北京时间」的显式口径说明"


def test_report_has_coverage_matrix_and_sections(stub_sources, monkeypatch, capsys):
    rc, out = _run(monkeypatch, capsys)
    assert "覆盖" in out, "须有源覆盖矩阵"
    assert "2026-10-10" in out, "须写明中国区实测覆盖边界"
    assert "议息" in out, "FOMC 须独立成段"
    # 三条呈现铁律之一：窗口外与不可得是两种不同文案
    assert "不等于无事件" in out or "≠ 无事件" in out or "不可得" in out


# ── R-D04 政治窗口接线（轮末评审修复 2026-09-13）───────────────────────────

def test_macro_report_includes_political_windows(stub_sources, monkeypatch, capsys, tmp_path):
    """宏观报告须带**政治/宏观不确定性窗口**小节（R-D04 接线）。

    ⚠️ `load_political_windows` / `render_political_windows` 此前**零调用方**
    → 策展表里登记的 2026-11-03 美国中期选举窗口从未出现在任何输出里，
    而 SKILL.md 把 R-D04 列为已交付能力。
    """
    pol = tmp_path / "political.yaml"
    pol.write_text(
        "mechanism_note: 不确定性窗口：风险溢价可能抬升，方向未知\n"
        "windows:\n"
        "  - name: 美国中期选举\n    region: 美国\n    type: 选举\n"
        "    start: 2026-11-03\n    end: 2026-11-03\n", encoding="utf-8")
    monkeypatch.setattr(mc, "_POLITICAL_DEFAULT", pol)
    rc, out = _run(monkeypatch, capsys)
    assert rc == 0
    assert "政治/宏观不确定性窗口" in out
    assert "美国中期选举" in out
    assert "方向未知" in out, "机制注记须随输出（C4：只支持不确定性窗口，不支持方向）"
    assert "状态" in out


def test_macro_report_marks_political_unavailable_not_silent(
        stub_sources, monkeypatch, capsys, tmp_path):
    """策展表缺失 → 渲染「不可得」而非静默省略小节（维护纪律）。"""
    monkeypatch.setattr(mc, "_POLITICAL_DEFAULT", tmp_path / "missing.yaml")
    rc, out = _run(monkeypatch, capsys)
    assert rc == 0
    assert "政治/宏观不确定性窗口" in out and "不可得" in out


def test_fomc_unavailable_rendered_as_unavailable_not_no_schedule(
        stub_sources, monkeypatch, capsys):
    """v0.3.0 C3：策展表不可得（文件缺失/解析失败/已过期）须走 error → ❌ 不可得。

    旧实现丢弃 `load_fomc_meetings` 的 warnings、仍构造 error=None 的 SourceResult
    → 覆盖矩阵同时渲染「— 窗口内无排期」与「⚠ FOMC 策展表不可得」，把**不可得**
    写成**无事件**（LAW 5；与本文件 T1/T2/T3/T6 同族的历史缺陷模式）。
    """
    stub_sources["fomc"] = ([], ["FOMC 策展表不可得（文件缺失）"])
    rc, out = _run(monkeypatch, capsys)
    assert rc == 0, "其余源可用 → 部分降级不改变退出码"
    assert "❌ FOMC 策展表：FOMC 策展表不可得（文件缺失）" in out
    fomc_lines = [ln for ln in out.splitlines() if "FOMC 策展表" in ln]
    assert fomc_lines, "FOMC 须出现在覆盖矩阵/降级清单"
    for ln in fomc_lines:
        assert "窗口内无排期" not in ln, f"不可得被渲染成无事件：{ln}"
