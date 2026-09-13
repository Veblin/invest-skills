"""R-F02 配置方法回测框架 — 离线单测。

证据纪律（hypothesis-registry C17）：
- DCA = **行为工程非收益优化**（不提高期望收益）
- **网格无学术文献**，与「容忍带再平衡 + 止损」只有**松散对应**
- 黄金避险角色**文献冲突 → 并列不裁决**
- 引用 Yu-Wang (2026) 方向结论前须**原文表格核验**
- 输出**不含建议**（LAW 6）
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

# ⚠️ **按文件路径加载，不动 sys.path**：本目录（`skills/lib`）自身含 `__init__.py`
# → 无论 insert 还是 append 到 sys.path，都会干扰 `import lib` 的解析顺序，
# 使**其它技能**的 `import lib.tushare_client` / `lib.proxy` 变成
# ModuleNotFoundError（实测：本文件跑在 discover-scan/event-calendar 之前时必现，
# 单跑本文件不复现——属「只在全仓顺序下暴露」的一类）
_spec = importlib.util.spec_from_file_location(
    "config_backtest_under_test", Path(__file__).resolve().parent.parent / "config_backtest.py")
cb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cb)


_UP = [100.0 * (1.002 ** i) for i in range(200)]        # 单调上行
# ⚠️ 震荡幅度须**超过带宽**才会触发再平衡：±4.5% 的窄幅在 5% 带下永不触发
# （这是正确行为，但夹具若取自窄幅序列，测试会在「0 次再平衡」上空转）
_FLAT = [100.0 + 25.0 * __import__("math").sin(i / 5.0) for i in range(200)]


# ── 三种方法 ─────────────────────────────────────────────────────────────

def test_lump_sum_beats_dca_on_monotonic_uptrend():
    """单调上行序列中，一次投入**必然优于** DCA（现金拖累）——这是方法的固有属性。"""
    ls = cb.lump_sum(_UP, amount=10000.0)
    dc = cb.dca(_UP, amount_per_period=1000.0, every=20)
    assert ls["return_pct"] > dc["return_pct"]


def test_dca_note_says_behavioural_not_return_optimising():
    dc = cb.dca(_UP)
    assert "行为工程" in dc["note"] and "不提高期望收益" in dc["note"]


def test_tolerance_rebalance_counts_triggered_bands():
    rb = cb.tolerance_rebalance(_FLAT, band=0.05, amount=10000.0)
    assert rb["available"] is True
    assert rb["n_rebalances"] >= 1, "震荡序列应触发多次再平衡"


def test_tolerance_rebalance_declares_zero_cash_rate_simplification():
    """现金端 0 利率是**简化**——会低估再平衡表现，须随输出标注。"""
    rb = cb.tolerance_rebalance(_FLAT)
    assert rb["cash_rate"] == 0.0
    assert "0 利率" in rb["note"] and "低估" in rb["note"]


def test_tolerance_rebalance_rejects_bad_weights():
    with pytest.raises(ValueError):
        cb.tolerance_rebalance(_FLAT, weights=(0.6, 0.6))


def test_short_series_is_three_state():
    for fn in (cb.dca, cb.lump_sum, cb.tolerance_rebalance):
        out = fn([100.0])
        assert out["available"] is False and out["reason"]


# ── 证据注记 ─────────────────────────────────────────────────────────────

def test_grid_note_says_no_academic_literature_and_loose_correspondence():
    out = cb.config_backtest(_FLAT)
    note = out["grid_equivalence"]
    assert "网格策略无学术文献" in note or "无学术文献" in note
    assert "松散对应" in note
    assert "容忍带再平衡" in note and "止损" in note


def test_gold_hedge_conflict_is_juxtaposed_not_adjudicated():
    note = cb.config_backtest(_FLAT)["gold_hedge"]
    assert "文献冲突" in note
    assert "并列" in note and "不裁决" in note


def test_backtest_declares_no_ranking_conclusion():
    out = cb.config_backtest(_FLAT)
    assert "非建议" in out["caveat"]
    assert "不产出" in out["caveat"]


@pytest.mark.parametrize("banned", ["建议采用", "推荐 DCA", "更优选择", "应当配置", "建议定投"])
def test_no_advice_language(banned):
    assert banned not in str(cb.config_backtest(_FLAT))


def test_three_methods_present_in_comparison():
    out = cb.config_backtest(_UP)
    assert set(out["methods"]) == {"dca", "lump_sum", "tolerance_rebalance"}
    for k, v in out["methods"].items():
        assert "return_pct" in v, f"{k} 缺 return_pct"
