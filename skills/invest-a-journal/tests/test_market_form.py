"""R-A01 市场形态三主态 + R-A02 分化度字段 — 离线单测。

纪律（源文档 C1/C2/C4 裁决）：
- **只作事后标注，禁止方向语义**（不得出现「将/预期/看多」等字样）
- 输出必须带 **Kirby 注记**：「状态标签不蕴含收益可预测性」
- 「杀跌后观察」是**突破选择期语境下的条件性上下文**，**不是第四形态**
- `avg_correlation` 是**工程 proxy（指数级滚动相关）**，与 Pollet-Wilson (2010)
  的个股日收益平均相关口径**不同**，不得称为「Pollet-Wilson 口径」
- 全部数值 Python 计算（P0）：vector 的每一项都能从输入复算
"""
from __future__ import annotations

import pytest

import market_microstructure as mm


def _snap(**over):
    base = {"date": "2026-09-12", "ad_ratio": 1.2, "lu_ld_ratio": 3.0,
            "limit_up_count": 40, "limit_down_count": 5, "total_turnover": 9000.0}
    base.update(over)
    return base


def _history(n=30, *, ad=1.2, turnover=9000.0):
    return [{"date": f"2026-08-{i + 1:02d}", "ad_ratio": ad, "lu_ld_ratio": 3.0,
             "total_turnover": turnover, "limit_up_count": 40, "limit_down_count": 5}
            for i in range(n)]


def _index_series(n=90):
    """两个指数的日收益序列（第二个更波动）——用于分化度。"""
    a = [0.001 * ((i % 5) - 2) for i in range(n)]
    b = [0.004 * ((i % 7) - 3) for i in range(n)]
    return {"IDX_A": a, "IDX_B": b}


# ── R-A01 三主态 ─────────────────────────────────────────────────────────

def test_forms_are_exactly_three():
    assert mm.MARKET_FORMS == ("普涨共振", "宽幅震荡轮动", "突破选择期")


def test_form_vector_is_recomputable_from_input():
    """P0：vector 每项须能从输入复算（不得是引擎内部另算的数）。"""
    snap, hist = _snap(), _history()
    out = mm.compute_market_form(snap, hist)
    assert out["vector"]["ad_ratio"] == snap["ad_ratio"]
    assert out["vector"]["lu_ld_ratio"] == snap["lu_ld_ratio"]
    assert out["vector"]["limit_up_count"] == snap["limit_up_count"]


def test_broad_rally_form_on_wide_breadth_and_expanding_turnover():
    snap = _snap(ad_ratio=3.0, lu_ld_ratio=8.0, total_turnover=12000.0)
    out = mm.compute_market_form(snap, _history(turnover=9000.0))
    assert out["form"] == "普涨共振"


def test_rotation_form_on_flat_breadth():
    snap = _snap(ad_ratio=1.0, lu_ld_ratio=1.0, total_turnover=9000.0)
    out = mm.compute_market_form(snap, _history())
    assert out["form"] == "宽幅震荡轮动"


def test_breakout_choice_is_the_default_fill_on_missing_breadth():
    """第三类作**默认填充态**（C2 裁决）——数据不足时归此态而非编造方向。"""
    snap = _snap(ad_ratio=None, lu_ld_ratio=None)
    out = mm.compute_market_form(snap, _history())
    assert out["form"] == "突破选择期"


def test_sub_form_only_under_rotation():
    wide = mm.compute_market_form(_snap(ad_ratio=1.0, lu_ld_ratio=1.0, total_turnover=15000.0),
                                  _history(turnover=9000.0))
    assert wide["form"] == "宽幅震荡轮动" and wide["sub_form"] == "宽幅轮动"
    thin = mm.compute_market_form(_snap(ad_ratio=1.0, lu_ld_ratio=1.0, total_turnover=3000.0),
                                  _history(turnover=9000.0))
    assert thin["sub_form"] == "缩量电风扇"
    rally = mm.compute_market_form(_snap(ad_ratio=3.0, lu_ld_ratio=8.0, total_turnover=12000.0),
                                   _history(turnover=9000.0))
    assert rally["sub_form"] is None, "子状态只在震荡轮动下出现"


def test_selloff_context_is_not_a_fourth_form():
    """「杀跌后观察」须是**突破选择期**下的条件性 context，不得成为独立形态。"""
    snap = _snap(ad_ratio=0.1, lu_ld_ratio=0.2, limit_down_count=200, total_turnover=30000.0)
    out = mm.compute_market_form(snap, _history())
    assert out["context"] == "杀跌后观察"
    assert out["form"] == "突破选择期"
    assert out["form"] != "杀跌后观察"
    assert out["form"] in mm.MARKET_FORMS


def test_no_directional_wording_anywhere():
    """事后标注：输出不得含方向/预测语义。"""
    out = mm.compute_market_form(_snap(), _history())
    label = f"{out['form']}{out['sub_form'] or ''}{out['context'] or ''}{out['kirby_note']}"
    for banned in ("将", "预期", "看多", "看空", "上涨", "下跌", "目标"):
        assert banned not in label, f"形态输出含方向语义：{banned}"


def test_kirby_note_always_present():
    for snap in (_snap(), _snap(ad_ratio=None), _snap(ad_ratio=3.0)):
        out = mm.compute_market_form(snap, _history())
        assert "不蕴含收益可预测性" in out["kirby_note"], "Kirby 注记强制"
        assert "Kirby" in out["kirby_note"]


def test_unavailable_when_history_too_short():
    out = mm.compute_market_form(_snap(), _history(n=3))
    assert out["available"] is False and out["missing"], "历史不足须显式不可得"
    assert out["form"] == "突破选择期", "不可得时仍给默认填充态（不臆造）"


# ── R-A01 历史频次/持续期 ────────────────────────────────────────────────

def test_form_history_frequency_and_duration():
    hist = []
    for i in range(60):
        hist.append({"date": f"2026-{(i // 28) + 1:02d}-{(i % 28) + 1:02d}",
                     "ad_ratio": 3.0 if i < 30 else 1.0,
                     "lu_ld_ratio": 8.0 if i < 30 else 1.0,
                     "total_turnover": 12000.0 if i < 30 else 9000.0,
                     "limit_up_count": 40, "limit_down_count": 5})
    out = mm.market_form_history(history=hist)
    assert out["n_days"] == 60
    freq = out["freq"]
    assert pytest.approx(sum(v["pct"] for v in freq.values()), abs=0.1) == 100.0
    assert sum(v["days"] for v in freq.values()) == 60
    assert freq["普涨共振"]["days"] > 0 and freq["宽幅震荡轮动"]["days"] > 0
    assert "durations" in out and out["durations"]["普涨共振"]["median"] >= 1


def test_form_history_declares_hindsight_caveat():
    out = mm.market_form_history(history=_history(60))
    assert "事后" in out["caveat"]


# ── R-A02 分化度 ─────────────────────────────────────────────────────────

def test_dispersion_three_fields():
    out = mm.compute_dispersion(_snap(), _history(), index_series=_index_series())
    assert out["available"] is True
    for key in ("index_dispersion", "rotation_speed", "avg_correlation"):
        assert key in out, f"缺字段 {key}"
        assert "value" in out[key] and "pctile" in out[key]


def test_avg_correlation_is_labelled_engineering_proxy():
    """工程 proxy 标注强制——不得被称作 Pollet-Wilson 口径。"""
    out = mm.compute_dispersion(_snap(), _history(), index_series=_index_series())
    note = out["avg_correlation"]["proxy_note"]
    assert "proxy" in note.lower()
    assert "非 Pollet-Wilson 口径" in note or "不是 Pollet-Wilson" in note
    assert out["avg_correlation"]["window"] >= 20


def test_dispersion_descriptive_only_no_direction():
    out = mm.compute_dispersion(_snap(), _history(), index_series=_index_series())
    text = str(out)
    for banned in ("将", "预期", "看多", "看空"):
        assert banned not in text, f"分化度含方向语义：{banned}"


def test_dispersion_unavailable_without_index_series():
    out = mm.compute_dispersion(_snap(), _history(), index_series=None)
    assert out["available"] is False
    assert out["index_dispersion"]["value"] is None
    assert out["missing"], "缺输入须列明"


def test_dispersion_rotation_speed_from_snapshot_history():
    out = mm.compute_dispersion(_snap(), _history(), index_series=None)
    assert "rotation_speed" in out, "轮动速度可仅由快照历史得出"


def test_dispersion_recomputable():
    """P0：指数离散度须可由输入序列复算。"""
    series = _index_series()
    out = mm.compute_dispersion(_snap(), _history(), index_series=series)
    import statistics

    expected = statistics.pstdev([sum(v) / len(v) for v in series.values()])
    assert out["index_dispersion"]["value"] == pytest.approx(expected, rel=1e-6)


# ── 落库联动：env_label 携带 market_form / dispersion ────────────────────

def test_env_label_carries_market_form_and_dispersion():
    """R-A01 要求「现有 env_label 扩展 market_form 字段」——须真进落库 JSON。"""
    import json

    snap = _snap(total_turnover=9000.0)
    snap["date"] = "2026-09-12"
    mm._compute_labels_v2(snap, _history())
    env = json.loads(snap["env_label"])
    assert "market_form" in env, "env_label 未携带 market_form"
    assert env["market_form"]["form"] in mm.MARKET_FORMS
    assert "不蕴含收益可预测性" in env["market_form"]["kirby_note"]
    assert "dispersion" in env
    assert "proxy_note" in env["dispersion"]["avg_correlation"]


def test_dispersion_proxy_note_reaches_serialized_env():
    """proxy 说明须随字段文档输出（不得只在内存里）。"""
    import json

    snap = _snap()
    snap["date"] = "2026-09-12"
    mm._compute_labels_v2(snap, _history())
    env = json.loads(snap["env_label"])
    assert "非 Pollet-Wilson 口径" in env["dispersion"]["avg_correlation"]["proxy_note"]


# ── 自审三处（与评审同族缺陷）────────────────────────────────────────────

def test_today_row_not_double_counted():
    """历史含今日行时不得双计（模块既有 `hist_ex_today` 惯例）。
    双计会让今日同时进「历史序列」与「当前值」，分位被自身拉偏。"""
    snap = _snap(date="2026-08-30", ad_ratio=1.0, lu_ld_ratio=1.0)
    hist = _history(30) + [_snap(date="2026-08-30", ad_ratio=1.0, lu_ld_ratio=1.0)]
    trend_with_dup = mm.compute_market_form(snap, hist)["vector"]["turnover_trend"]
    hist_clean = _history(30)
    trend_clean = mm.compute_market_form(snap, hist_clean)["vector"]["turnover_trend"]
    assert trend_with_dup == pytest.approx(trend_clean), "今日行被双计"


def test_form_history_does_not_double_count_current_row():
    """market_form_history 逐行用 `prior = rows[:i]`——自身不得出现在自己的历史里。"""
    rows = _history(40)
    out = mm.market_form_history(history=rows)
    assert out["n_days"] == 40


def test_rotation_speed_ignores_negative_index():
    """轮动速度的窗口须从 k=2 起（`range(1,…)` + `k>=2` 会先算 lu_hist[-1]）。"""
    # ⚠️ 序列须**真单调**：`[10…100]*3` 会在块边界回落（100→10），那是 4 次真实切换
    hist = [{"date": f"2026-08-{i + 1:02d}", "limit_up_count": i + 1} for i in range(30)]
    out = mm.compute_dispersion(_snap(limit_up_count=31), hist, index_series=None)
    assert out["rotation_speed"]["value"] == 0.0, "单调序列不应有方向切换"

    # 反向对照：锯齿序列应有切换（防「恒 0」假绿）
    saw = [{"date": f"2026-08-{i + 1:02d}", "limit_up_count": 10 if i % 2 else 50}
           for i in range(30)]
    out2 = mm.compute_dispersion(_snap(limit_up_count=50), saw, index_series=None)
    assert out2["rotation_speed"]["value"] > 0
