"""R11c/A2 情景预案：绝对回撤档位 → 核验深度 + 当前状态（全部离线）。

A2 修订语义（2026-10-10）：
    - 取消 σ 倍数/60 日日均波动口径（不得换算 σ、正态尾概率或 σ×√n）；
    - 档位保留为普通研究核查分档，并输出当前回撤与「已达/未达」状态——
      当前回撤已越过全部档时，报告不得仍按四个未来触发呈现。

用例：
    ① 档位表 = 固定四档 + 触发核验深度（无 σ 字段）
    ② current_drawdown 定义（自窗口峰值回撤）+ 159920 原 payload 247 行回归
    ③ 各档「已达/未达」与当前核验深度归结
    ④ 三步核查固定模板（内容断言）
    ⑤ 输出无动作化表述、无 σ/概率措辞
    ⑥ LAW6A_DISCLAIMER 存在且含「操作决策由用户自行做出」
"""

from __future__ import annotations

import json
from pathlib import Path

from etf_playbook import (
    LAW6A_DISCLAIMER,
    LEVELS_BASIS,
    current_drawdown,
    current_verification,
    drawdown_levels,
    three_step_checklist,
)

_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "159920_history_247.json"


def _rows_from_closes(closes: list[float]) -> list[dict]:
    return [{"date": f"2026-01-{i + 1:02d}", "nav": c} for i, c in enumerate(closes)]


# ---------------------------------------------------------------------------
# ① 档位表：固定四档 + 核验深度（A2：无 σ）
# ---------------------------------------------------------------------------

def test_drawdown_levels_fixed_absolute_tiers():
    levels = drawdown_levels(-4.0, rows_observed=100)
    assert [lv["level_pct"] for lv in levels] == [-3.0, -5.0, -8.0, -12.0]
    assert [lv["verification_depth"] for lv in levels] == [
        "例行记录", "归因核查", "三步核查全流程", "框架重估",
    ]
    # A2：档位不含 σ/概率字段
    assert all("sigma" not in k and "sigma_multiple" not in lv for lv in levels for k in lv)
    assert all(lv["rows_observed"] == 100 for lv in levels)


def test_drawdown_levels_state_reached_not_reached():
    levels = {lv["level_pct"]: lv["state"] for lv in drawdown_levels(-4.0)}
    assert levels[-3.0] == "已达"      # -4% ≤ -3%
    assert levels[-5.0] == "未达"
    assert levels[-8.0] == "未达"
    assert levels[-12.0] == "未达"
    # 恰好在档位上 = 已达
    assert {lv["level_pct"]: lv["state"] for lv in drawdown_levels(-3.0)}[-3.0] == "已达"


def test_drawdown_levels_state_none_when_current_unknown():
    levels = drawdown_levels(None)
    assert all(lv["state"] is None for lv in levels)  # 不可算不猜


# ---------------------------------------------------------------------------
# ② current_drawdown：定义 + 真实序列回归
# ---------------------------------------------------------------------------

def test_current_drawdown_definition():
    out = current_drawdown(_rows_from_closes([1.0, 1.10, 1.05, 1.02]))
    assert out["current_drawdown_pct"] == round((1.02 / 1.10 - 1) * 100, 2)
    assert out["peak_date"] == "2026-01-02" and out["peak_close"] == 1.10
    assert out["latest_date"] == "2026-01-04" and out["latest_close"] == 1.02
    assert out["rows_observed"] == 4
    # 峰值即最新（新高）→ 回撤 0%
    at_high = current_drawdown(_rows_from_closes([1.0, 1.2]))
    assert at_high["current_drawdown_pct"] == 0.0
    # 无有效行 / 全零负值 → None
    assert current_drawdown([]) is None
    assert current_drawdown([{"date": "x", "nav": 0}, {"date": "y", "nav": None}]) is None


def test_current_drawdown_159920_payload_regression():
    """159920 原 payload 封存的 247 行历史：当前回撤 = -15.6%（与独立复算
    baseline-recalc.json 一致）；峰值 2026-01-29 @1.6944，最新 2026-10-09 @1.4301。"""
    rows = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    assert len(rows) == 247
    out = current_drawdown(rows)
    assert out["current_drawdown_pct"] == -15.6
    assert out["peak_date"] == "2026-01-29" and out["peak_close"] == 1.6944
    assert out["latest_date"] == "2026-10-09" and out["latest_close"] == 1.4301


def test_all_levels_reached_does_not_stay_future_triggers():
    """当前回撤 -15.6% 已越过全部四档：状态全为「已达」，
    当前核验深度 = 框架重估（最深档），不是四个未来触发。"""
    levels = drawdown_levels(-15.6, rows_observed=247)
    assert all(lv["state"] == "已达" for lv in levels)
    cv = current_verification(levels)
    assert cv["reached_levels"] == 4
    assert cv["deepest_reached_level_pct"] == -12.0
    assert cv["deepest_reached_depth"] == "框架重估"


# ---------------------------------------------------------------------------
# ③ current_verification 边界
# ---------------------------------------------------------------------------

def test_current_verification_none_reached():
    cv = current_verification(drawdown_levels(-1.0))
    assert cv["reached_levels"] == 0
    assert cv["deepest_reached_level_pct"] is None
    assert cv["deepest_reached_depth"] is None


def test_current_verification_unknown_current_returns_none():
    assert current_verification(drawdown_levels(None)) is None
    assert current_verification([]) is None


# ---------------------------------------------------------------------------
# ④ 三步核查固定模板
# ---------------------------------------------------------------------------

def test_three_step_checklist_fixed_template():
    steps = three_step_checklist()
    assert len(steps) == 3
    assert steps[0].startswith("STEP 1 归因核查")
    assert steps[1].startswith("STEP 2 框架对照")
    assert steps[2].startswith("STEP 3 框架重估")
    assert "行业" in steps[0] and "结构" in steps[0] and "个股" in steps[0]
    assert "原因不明回撤" in steps[0]
    assert "盈利证据链" in steps[1]
    assert "叙事前提" in steps[1]
    assert "资金先行指标" in steps[1]
    assert "未失效" in steps[2] and "需验证" in steps[2] and "已证伪" in steps[2]


# ---------------------------------------------------------------------------
# ⑤ 输出无动作化表述；无 σ/统计概率措辞（A2）
# ---------------------------------------------------------------------------

def test_playbook_output_no_action_phrases():
    levels = drawdown_levels(-15.6, rows_observed=247)
    cv = current_verification(levels)
    text = " ".join(
        [str(lv) for lv in levels] + [str(cv)] + three_step_checklist()
        + [LAW6A_DISCLAIMER, LEVELS_BASIS]
    )
    for forbidden in ("建议卖出", "止损", "无动作", "如何应对"):
        assert forbidden not in text, f"输出含禁止动作化表述: {forbidden}"
    assert "触发核验深度" in text
    assert "研究流程规则" in LAW6A_DISCLAIMER


def test_no_sigma_or_probability_framing():
    """A2：不得以 σ / 正态尾概率 / σ×√n 重述档位。

    档位行与核验深度内不得出现任何 σ/倍数/概率词；口径说明只允许**否定式**
    提及（「非统计概率」「不设 σ/尾部概率口径」），不得给出任何倍数或概率数字。
    """
    levels = drawdown_levels(-15.6, rows_observed=247)
    text = " ".join(str(lv) for lv in levels)
    for forbidden in ("σ", "sigma", "标准差", "正态", "概率", "倍"):
        assert forbidden not in text, f"档位行含统计概率口径: {forbidden}"
    # 口径说明：出现 σ/概率处必须是显式否定，且不含任何倍数/概率数字
    assert "非统计概率" in LEVELS_BASIS
    assert "不设 σ/尾部概率口径" in LEVELS_BASIS
    assert "倍数" not in LEVELS_BASIS


# ---------------------------------------------------------------------------
# ⑥ LAW6A_DISCLAIMER
# ---------------------------------------------------------------------------

def test_law6a_disclaimer_present_and_user_decides():
    assert isinstance(LAW6A_DISCLAIMER, str) and LAW6A_DISCLAIMER
    assert "操作决策由用户根据自身持有周期与仓位自行做出" in LAW6A_DISCLAIMER
    assert "买卖指令" in LAW6A_DISCLAIMER
    assert "建议买入" not in LAW6A_DISCLAIMER
