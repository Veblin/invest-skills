"""R-D01 题材日历记账 — 离线单测（无网络）。

纪律（hypothesis-registry C9/C10）：
- **证实锚点 = 官方/权威来源**；**题材热度见顶 ≠ 证实**
- **只记账不预测扩散路径**（不得有 expected/target/will_ 类字段）
- 多概念归属 = **拥挤度加总**，不含「托底」语义
- 状态文件与其他模块共用 → 写入须**保留其他键**，损坏时**不静默清空**
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import theme_calendar as tc  # noqa: E402


@pytest.fixture
def state(tmp_path):
    return tmp_path / "state.json"


def _reg(state, theme="示例题材", stage="首波", source="发改委公告",
         anchor="某规划正式发布", **kw):
    return tc.register_theme(theme, stage=stage, anchor=anchor,
                             anchor_source=source, state_file=state, **kw)


# ── 阶段与校验 ───────────────────────────────────────────────────────────

def test_stages_are_exactly_four():
    assert tc.STAGES == ("首波", "扩散", "延伸", "兑现")


def test_register_and_load_roundtrip(state):
    rec = _reg(state, concepts=["概念甲", "概念乙"])
    assert rec["stage"] == "首波"
    themes = tc.load_themes(state_file=state)
    assert len(themes) == 1
    assert themes[0]["theme"] == "示例题材"
    assert themes[0]["concepts"] == ["概念甲", "概念乙"]


def test_invalid_stage_rejected(state):
    with pytest.raises(ValueError):
        _reg(state, stage="起飞")


def test_empty_anchor_rejected(state):
    with pytest.raises(ValueError):
        _reg(state, anchor="")


@pytest.mark.parametrize("bad", ["涨停家数第一", "板块热度见顶", "人气榜前列", "成交额榜居首"])
def test_heat_is_not_confirmation(state, bad):
    """热度类「来源」不构成证实——这是 C9 裁决的核心语义。"""
    with pytest.raises(ValueError) as exc:
        _reg(state, source=bad)
    assert "热度" in str(exc.value) or "不等于证实" in str(exc.value)


def test_official_sources_accepted():
    for ok in ("发改委公告", "公司公告", "交易所披露", "国家统计局", "工信部文件"):
        assert tc.is_official_source(ok), f"应接受官方来源：{ok}"
    assert not tc.is_official_source("")
    assert not tc.is_official_source("题材热度第一")


def test_confirmed_stage_requires_date(state):
    with pytest.raises(ValueError):
        _reg(state, stage="兑现")
    rec = _reg(state, stage="兑现", confirmed_date="2026-10-01")
    assert rec["confirmed_date"] == "2026-10-01"


# ── 无预测语义 ───────────────────────────────────────────────────────────

def test_record_has_no_prediction_fields(state):
    rec = _reg(state)
    for banned in ("expected", "target", "will_", "predict", "forecast"):
        assert not any(banned in k for k in rec), f"记录含预测类字段：{banned}"


def test_rendered_output_has_no_path_prediction_wording(state):
    _reg(state, concepts=["概念甲"])
    text = tc.render_themes(state_file=state)
    for banned in ("下一轮扩散", "预计", "将传导", "扩散路径预测", "目标位"):
        assert banned not in text, f"输出含路径预测语义：{banned}"
    assert "只记账不预测扩散路径" in text


# ── 回放与拥挤度 ─────────────────────────────────────────────────────────

def test_replay_returns_full_sequence(state):
    _reg(state, stage="首波")
    _reg(state, stage="扩散", anchor="订单落地")
    _reg(state, stage="兑现", anchor="官方确认放量", confirmed_date="2026-10-01")
    seq = tc.replay_theme("示例题材", state_file=state)
    assert [r["stage"] for r in seq] == ["首波", "扩散", "兑现"]


def test_crowding_is_a_sum_not_a_boost(state):
    """多概念 = 拥挤度**加总**；不得出现「更强/托底」语义。

    ⚠️ 口径以 C10 预注册为准：加总 = Σ 概念在**其他已登记题材**中的出现次数
    → 查 A 时须 `exclude_theme="A"`（自身登记不计）。
    """
    t1 = {"theme": "A", "stage": "首波", "concepts": ["甲", "乙"]}
    t2 = {"theme": "B", "stage": "扩散", "concepts": ["甲"]}
    out = tc.crowding_field(["甲", "乙"], [t1, t2], exclude_theme="A")
    assert out["crowding_sum"] == 1, "加总须只计**其他**题材：B 命中 1"
    # ⚠️ 只扫**结论性字段**：note 里必然出现「不含『托底』语义」这句禁令本身，
    # 整体子串扫描会把禁令判成违规（文档要禁止某措辞就必须引用它）
    claim_text = f"{out['matched_themes']}{out['concepts']}"
    assert "托底" not in claim_text
    assert "加总" in out["note"] and "托底" in out["note"]


def test_crowding_empty_when_no_match():
    out = tc.crowding_field(["丙"], [{"theme": "A", "concepts": ["甲"]}])
    assert out["crowding_sum"] == 0 and out["matched_themes"] == []


# ── 证实日入日历 ─────────────────────────────────────────────────────────

def test_confirmed_event_days_only_from_confirmed_stage(state):
    _reg(state, stage="首波")
    _reg(state, theme="乙题材", stage="兑现", anchor="官方确认", confirmed_date="2026-10-02")
    assert tc.confirmed_event_days(state_file=state) == ["2026-10-02"]


def test_confirmed_event_days_dedupes_and_sorts(state):
    for d in ("2026-10-03", "2026-10-01", "2026-10-03"):
        _reg(state, theme=f"T{d}", stage="兑现", anchor="官方确认", confirmed_date=d)
    assert tc.confirmed_event_days(state_file=state) == ["2026-10-01", "2026-10-03"]


# ── 状态文件安全 ─────────────────────────────────────────────────────────

def test_write_preserves_other_keys(state):
    """与解禁模块共用状态文件——写 themes 不得清掉 symbols。"""
    state.write_text(json.dumps({"updated": "2026-09-01",
                                 "symbols": {"600176": {"batches": [1, 2]}}}),
                     encoding="utf-8")
    _reg(state)
    data = json.loads(state.read_text(encoding="utf-8"))
    assert data["symbols"] == {"600176": {"batches": [1, 2]}}, "既有 symbols 被清空"
    assert len(data["themes"]) == 1


def test_corrupt_state_fails_loud_without_clobber(state):
    """损坏 → **拒绝写入**（不静默清空既有记录）。"""
    state.write_text("{ not json", encoding="utf-8")
    with pytest.raises(ValueError) as exc:
        _reg(state)
    assert "损坏" in str(exc.value)
    assert state.read_text(encoding="utf-8") == "{ not json", "损坏文件不得被覆盖"


def test_load_themes_on_corrupt_state_fails_loud(state):
    state.write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError):
        tc.load_themes(state_file=state)


def test_missing_state_is_first_run(state):
    assert tc.load_themes(state_file=state) == []
    assert "尚无登记" in tc.render_themes(state_file=state)


# ── CLI ──────────────────────────────────────────────────────────────────

def test_cli_registers_and_prints(state, capsys):
    assert tc.main(["--theme", "示例题材", "--stage", "首波",
                    "--anchor", "某规划发布", "--anchor-source", "发改委公告",
                    "--concepts", "甲,乙", "--state-file", str(state)]) == 0
    out = capsys.readouterr().out
    assert "已登记" in out and "只记账不预测扩散路径" in out


def test_cli_rejects_heat_source(state, capsys):
    assert tc.main(["--theme", "T", "--stage", "首波", "--anchor", "A",
                    "--anchor-source", "热度见顶", "--state-file", str(state)]) == 2
    assert "❌" in capsys.readouterr().err


def test_cli_sample_is_non_individual_stock(state, capsys):
    """验收要求：样例题材用**教学数据、不涉个股**——登记内容里不得出现个股代码。"""
    tc.main(["--theme", "示例题材", "--stage", "首波", "--anchor", "某规划发布",
             "--anchor-source", "发改委公告", "--state-file", str(state)])
    text = tc.render_themes(state_file=state)
    import re

    assert not re.search(r"\b\d{6}\b", text), "样例不得含个股代码"


# ── CLI 模式接线（unlock_calendar --theme）────────────────────────────────

def test_unlock_cli_theme_mode(state, capsys, monkeypatch):
    import unlock_calendar as uc

    # unlock_calendar.main() 走 sys.argv（与既有 test_macro_cli 同型）
    monkeypatch.setattr(sys, "argv", [
        "unlock_calendar.py", "--theme", "示例题材", "--stage", "首波",
        "--anchor", "某规划发布", "--anchor-source", "发改委公告",
        "--concepts", "甲,乙", "--state-file", str(state)])
    rc = uc.main()
    assert rc == 0
    out = capsys.readouterr().out
    assert "已登记题材" in out
    assert tc.load_themes(state_file=state)[0]["theme"] == "示例题材"


@pytest.mark.parametrize("extra", [["--macro"], ["--pool-file", "/tmp/x.txt"]])
def test_unlock_cli_theme_mode_is_mutually_exclusive(state, extra, capsys, monkeypatch):
    """三向互斥：--theme 与 --macro/--pool-file 同给须显式报错（不得静默取其一）。"""
    import unlock_calendar as uc

    monkeypatch.setattr(sys, "argv", [
        "unlock_calendar.py", "--theme", "T", "--stage", "首波", "--anchor", "A",
        "--anchor-source", "发改委公告", "--state-file", str(state)] + extra)
    with pytest.raises(SystemExit) as exc:
        uc.main()
    assert exc.value.code == 2


def test_unlock_cli_theme_requires_anchor_fields(state, monkeypatch):
    import unlock_calendar as uc

    monkeypatch.setattr(sys, "argv",
                        ["unlock_calendar.py", "--theme", "T", "--state-file", str(state)])
    with pytest.raises(SystemExit) as exc:
        uc.main()
    assert exc.value.code == 2


# ── R4 评审修复回归 ───────────────────────────────────────────────────────

def test_pool_mode_preserves_themes(state, capsys, monkeypatch):
    """池模式落盘不得清空 themes（共用状态文件；此前 `load_state` 因缺 symbols 键
    判「结构异常」→ save_state 整份覆写 → themes 永久消失，已实跑复现）。"""
    import unlock_calendar as uc

    _reg(state, stage="兑现", anchor="官方确认", confirmed_date="2026-10-01")
    st = uc.load_state(state)
    uc.save_state(state, st)          # 模拟池模式落盘
    assert tc.confirmed_event_days(state_file=state) == ["2026-10-01"], "themes 被池模式清空"
    assert "symbols" in json.loads(state.read_text(encoding="utf-8"))


@pytest.mark.parametrize("bad", ["股吧传言", "微博热搜", "某自媒体爆料", "市场传闻", "板块热度见顶"])
def test_unreliable_sources_rejected(state, bad):
    """非官方渠道同样不构成证实——只做热度黑名单会放行「股吧传言」（R4 评审实跑复现）。"""
    with pytest.raises(ValueError):
        _reg(state, source=bad)


def test_no_state_flag_respected_in_theme_mode(state, capsys, monkeypatch):
    """`--no-state`（help：不读写状态）须在所有模式生效——此前仅池模式遵守。"""
    import unlock_calendar as uc

    monkeypatch.setattr(sys, "argv", [
        "unlock_calendar.py", "--theme", "T", "--stage", "首波", "--anchor", "A",
        "--anchor-source", "发改委公告", "--no-state", "--state-file", str(state)])
    assert uc.main() == 0
    assert not state.exists(), "--no-state 下不得写状态文件"


# ── R-D02 需求窗口 vs 炒作窗口（分字段，禁止合并）─────────────────────────

def test_demand_window_requires_source():
    w = tc.make_demand_window("欧洲冬季 10 月下旬–次年 2-3 月", source="WMO 季节划分",
                              lit_note="商品基本面文献：取暖油季节性")
    assert w["text"] and w["source"] and w["lit_note"]
    with pytest.raises(ValueError):
        tc.make_demand_window("某窗口", source="")      # 需求窗口须带来源
    with pytest.raises(ValueError):
        tc.make_demand_window("", source="某来源")


def test_hype_window_always_carries_convention_label():
    """标注由构造保证；**出处不可考也须写标签**而非省略（report-conventions §3.5 约束 3）。"""
    w = tc.make_hype_window("每年 10 月启动", source="某直播")
    assert "从业者惯例，非学术验证" in w["label"]
    w2 = tc.make_hype_window("每年 10 月启动")           # 无 source
    assert "从业者惯例，非学术验证" in w2["label"]
    assert "出处不可考" in w2["label"], "无出处时须写「出处不可考」，不得省略标签"


@pytest.mark.parametrize("bad", ["预期收益 20%", "目标位 15 元", "涨幅预期可观", "收益预测 +30%"])
def test_hype_window_rejects_return_expectation(bad):
    """炒作窗口**不带收益预期权重**（R-D02 验收：无「预期收益」类描述）。"""
    with pytest.raises(ValueError) as exc:
        tc.make_hype_window(f"每年 10 月启动，{bad}", source="某直播")
    assert "收益" in str(exc.value) or "预期" in str(exc.value)


def test_windows_never_merged_in_render(state):
    """两者**禁止合并输出**——渲染须分节且显式声明不可合并。"""
    _reg(state, demand_window=tc.make_demand_window("欧洲冬季", source="WMO"),
         hype_window=tc.make_hype_window("每年 10 月启动", source="某直播"))
    text = tc.render_themes(state_file=state)
    assert "需求窗口" in text and "炒作窗口" in text
    assert "禁止合并" in text or "不得合并" in text
    # 两个窗口的文本须各自出现在**不同行**
    lines = [l for l in text.splitlines() if "欧洲冬季" in l or "每年 10 月启动" in l]
    assert len(lines) == 2, f"两个窗口被渲染到同一行（合并了）：{lines}"


def test_windows_roundtrip_through_state(state):
    _reg(state, demand_window=tc.make_demand_window("需求窗", source="来源A"),
         hype_window=tc.make_hype_window("炒作窗", source="凭条B"))
    rec = tc.load_themes(state_file=state)[0]
    assert rec["demand_window"]["text"] == "需求窗"
    assert rec["hype_window"]["text"] == "炒作窗"


# ── R-D03 可跟踪度字段 ───────────────────────────────────────────────────

def test_tractability_is_ratio_times_coverage():
    out = tc.tractability_field(hard_catalysts=6, total_catalysts=10,
                                analyst_coverage=10, announcement_freq=6.0, media_mentions=20)
    assert out["hard_catalyst_ratio"] == pytest.approx(0.6)
    assert out["coverage_sufficiency"] == pytest.approx(1.0)
    assert out["tractability"] == pytest.approx(0.6)
    assert out["available"] is True


def test_low_coverage_emits_symmetric_warning():
    """低覆盖须给**对称**风险提示（坏消息消化慢），而非「干扰小」类单向措辞。"""
    out = tc.tractability_field(hard_catalysts=5, total_catalysts=10,
                                analyst_coverage=0, announcement_freq=0.2, media_mentions=0)
    assert out["low_coverage"] is True
    assert "坏消息" in out["warning"] and "更慢" in out["warning"]
    assert "干扰小" not in out["warning"]


def test_tractability_has_no_subjective_wording():
    for kw in (5, 10):
        out = tc.tractability_field(hard_catalysts=kw, total_catalysts=10,
                                    analyst_coverage=3, announcement_freq=2.0, media_mentions=2)
        s = str(out)
        for banned in ("干扰小", "小作文少", "易跟踪"):
            assert banned not in s, f"含主观措辞：{banned}"


def test_tractability_unavailable_without_coverage_inputs():
    out = tc.tractability_field(hard_catalysts=3, total_catalysts=5,
                                analyst_coverage=None, announcement_freq=None, media_mentions=None)
    assert out["coverage_sufficiency"] is None
    assert out["tractability"] is None
    assert out["available"] is False and out["missing"]


def test_tractability_zero_denominator_fails_loud():
    with pytest.raises(ValueError):
        tc.tractability_field(hard_catalysts=0, total_catalysts=0,
                              analyst_coverage=1, announcement_freq=1.0, media_mentions=1)


# ── 拥挤度口径（轮末评审修复 2026-09-13）───────────────────────────────────

def test_crowding_excludes_own_registration():
    """自身登记**不得**计入加总（C10 冻结口径：只计「其他已登记题材」）。

    ⚠️ 原实现把自己也算进去：单题材 [甲,乙] 单独登记 → 加总 2（按定义应为 0）。
    后果：「无跨题材拥挤」这一状态永远表达不出来，且回填裁决检验的量与
    `C10_预注册.md` 的口径不同（预注册写死的是「在其他题材中」）。
    """
    t1 = {"theme": "A", "stage": "首波", "concepts": ["甲", "乙"]}
    out = tc.crowding_field(["甲", "乙"], [t1], exclude_theme="A")
    assert out["crowding_sum"] == 0 and out["matched_themes"] == []


def test_crowding_sums_across_other_themes():
    t1 = {"theme": "A", "stage": "首波", "concepts": ["甲", "乙"]}
    t2 = {"theme": "B", "stage": "扩散", "concepts": ["甲"]}
    t3 = {"theme": "C", "stage": "延伸", "concepts": ["乙"]}
    out = tc.crowding_field(["甲", "乙"], [t1, t2, t3], exclude_theme="A")
    assert out["crowding_sum"] == 2, "B 命中甲 + C 命中乙"
    assert out["matched_themes"] == ["B（扩散）", "C（延伸）"]


def test_render_omits_crowding_section_without_cross_theme(state):
    """无跨题材共享 → **不出拥挤度小节**（该节的存在本身就是信号）。"""
    _reg(state, concepts=["甲", "乙"])
    assert "多题材拥挤度" not in tc.render_themes(state_file=state)


def test_render_crowding_section_excludes_self(state):
    """出节时，加总与「其他题材」列都**不得**含自身。"""
    _reg(state, theme="甲题材", concepts=["甲", "乙"])
    _reg(state, theme="乙题材", concepts=["甲"])
    text = tc.render_themes(state_file=state)
    assert "### 多题材拥挤度" in text
    sec = text.split("### 多题材拥挤度", 1)[1]
    line = next(l for l in sec.splitlines() if l.startswith("| 甲题材 |"))
    cells = [c.strip() for c in line.strip("|").split("|")]
    assert cells[2] == "1", f"甲题材加总应为 1（仅乙题材共享「甲」），实得 {cells}"
    assert "甲题材" not in cells[3], "「其他题材」列不得含自身"


def test_hype_label_uses_verbatim_conventions_template():
    """炒作窗口标注须**逐字**用 §3.5 固定模板（含方括号）。

    ⚠️ 原实现产出无括号的「从业者惯例，非学术验证：X」——与条文模板不一致，
    而 lint 的 skip 只匹配子串「从业者惯例」故照样放行（机器查不出来）。
    """
    w = tc.make_hype_window("会后两周", source="券商策略会")
    assert w["label"] == "[从业者惯例，非学术验证：券商策略会]"
    w2 = tc.make_hype_window("会后两周")
    assert w2["label"] == "[从业者惯例，非学术验证：出处不可考]"


def test_rendered_hype_label_keeps_brackets(state):
    _reg(state, hype_window=tc.make_hype_window("会后两周", source="券商策略会"))
    text = tc.render_themes(state_file=state)
    assert "[从业者惯例，非学术验证：券商策略会]" in text
