"""R11c 情景预案：回撤档位 σ 分级 + 三步核查 + LAW 6a（全部 mock，零活体网络）。

用例：
    ① σ 分级表引擎计算（构造 closes 使 60 日日均波动 ≈4.01%，断言 -8% → ≈2.0σ）
    ② 三步核查为固定模板（内容断言）
    ③ 输出 grep 断言无「建议卖出」「止损」「无动作」「如何应对」
    ④ LAW6A_DISCLAIMER 存在且含「操作决策由用户自行做出」
"""

from __future__ import annotations

import pytest

from etf_playbook import (
    LAW6A_DISCLAIMER,
    daily_vol_pct,
    drawdown_levels,
    three_step_checklist,
)


def _closes_from_returns(returns: list[float], start: float = 1.0) -> list[float]:
    """由日收益率序列构造收盘价序列（cumprod）。"""
    closes = [start]
    for r in returns:
        closes.append(closes[-1] * (1.0 + r))
    return closes


# ---------------------------------------------------------------------------
# ① σ 分级表引擎计算
# ---------------------------------------------------------------------------

def test_drawdown_levels_sigma_mapping():
    # 交替 ±4.01% 收益率：60 日日均波动 = 4.01%（标准差口径）
    returns = [0.0401 if i % 2 == 0 else -0.0401 for i in range(60)]
    closes = _closes_from_returns(returns)
    vol = daily_vol_pct(closes, window=60)
    assert vol is not None
    assert vol == pytest.approx(4.01, abs=0.01)

    levels = drawdown_levels(closes, vol)
    assert [lv["level_pct"] for lv in levels] == [-3.0, -5.0, -8.0, -12.0]
    assert [lv["verification_depth"] for lv in levels] == [
        "例行记录", "归因核查", "三步核查全流程", "框架重估",
    ]
    # -8% ÷ 4.01% ≈ 1.995 → 2.0σ（588000 实例口径）
    by_level = {lv["level_pct"]: lv for lv in levels}
    assert by_level[-8.0]["sigma_multiple"] == pytest.approx(2.0, abs=0.05)
    # 其余档位 = |档位%| ÷ 4.01
    assert by_level[-3.0]["sigma_multiple"] == pytest.approx(3.0 / 4.01, abs=0.05)
    assert by_level[-5.0]["sigma_multiple"] == pytest.approx(5.0 / 4.01, abs=0.05)
    assert by_level[-12.0]["sigma_multiple"] == pytest.approx(12.0 / 4.01, abs=0.05)


def test_drawdown_levels_vol_missing():
    # vol_60d_daily 不可用 → sigma_multiple 为 None，不抛异常
    levels = drawdown_levels([1.0], None)
    assert len(levels) == 4
    assert all(lv["sigma_multiple"] is None for lv in levels)


def test_daily_vol_pct_insufficient():
    assert daily_vol_pct([1.0] * 30, window=60) is None
    assert daily_vol_pct([], window=60) is None


# ---------------------------------------------------------------------------
# ② 三步核查为固定模板（内容断言）
# ---------------------------------------------------------------------------

def test_three_step_checklist_fixed_template():
    steps = three_step_checklist()
    assert len(steps) == 3
    # 固定编号与主题
    assert steps[0].startswith("STEP 1 归因核查")
    assert steps[1].startswith("STEP 2 框架对照")
    assert steps[2].startswith("STEP 3 框架重估")
    # STEP 1：行业/结构/个股 三分类 + 原因不明回撤
    assert "行业" in steps[0] and "结构" in steps[0] and "个股" in steps[0]
    assert "原因不明回撤" in steps[0]
    # STEP 2：盈利证据链 / 叙事前提 / 资金先行指标
    assert "盈利证据链" in steps[1]
    assert "叙事前提" in steps[1]
    assert "资金先行指标" in steps[1]
    # STEP 3：未失效 / 需验证 / 已证伪 三态
    assert "未失效" in steps[2] and "需验证" in steps[2] and "已证伪" in steps[2]


# ---------------------------------------------------------------------------
# ③ 输出 grep 断言无动作化表述
# ---------------------------------------------------------------------------

def test_playbook_output_no_action_phrases():
    vol = 4.01
    closes = _closes_from_returns([0.04 if i % 2 == 0 else -0.04 for i in range(60)])
    levels = drawdown_levels(closes, vol)
    text = " ".join(
        [str(lv) for lv in levels] + three_step_checklist() + [LAW6A_DISCLAIMER]
    )
    for forbidden in ("建议卖出", "止损", "无动作", "如何应对"):
        assert forbidden not in text, f"输出含禁止动作化表述: {forbidden}"
    # 分级含义 = 触发核验深度（非动作指令）
    assert "触发核验深度" in text
    assert "研究流程规则" in LAW6A_DISCLAIMER


# ---------------------------------------------------------------------------
# ④ LAW6A_DISCLAIMER 存在且含操作决策由用户自行做出
# ---------------------------------------------------------------------------

def test_law6a_disclaimer_present_and_user_decides():
    assert isinstance(LAW6A_DISCLAIMER, str) and LAW6A_DISCLAIMER
    assert "操作决策由用户根据自身持有周期与仓位自行做出" in LAW6A_DISCLAIMER
    assert "买卖指令" in LAW6A_DISCLAIMER
    # 免责声明不包含任何买卖方向词（非指令性）
    assert "建议买入" not in LAW6A_DISCLAIMER
