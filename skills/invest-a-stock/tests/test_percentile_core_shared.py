"""A05-3②：≤ 口径分位四处收敛到共享核心（各自预处理/守卫保留）。

四处（A05-3 卡 ②）：skills/lib/stats.percentile_rank_inclusive、
agent_facts.percentile_rank、insight_model._percentile、macro DGS30 分位。
共享核心 = skills/lib/stats.le_share_pct（本文件锁定前三处的委托等价；
macro 侧由 test_macro_extended 的输出测试覆盖——DGS30 路径需 FRED 夹具）。
"""

from __future__ import annotations

import pytest


def test_core_formula_and_empty_guards():
    from stats import le_share_pct

    assert le_share_pct([1.0, 2.0, 3.0, 4.0], 2.0) == 50.0  # 含边界（<=）
    assert le_share_pct([], 2.0) is None
    assert le_share_pct([1.0], None) is None
    # 最大值 → 100%（含边界）；值域外 → 0/100
    assert le_share_pct([1.0, 2.0], 2.0) == 100.0
    assert le_share_pct([1.0, 2.0], 0.5) == 0.0


def test_stats_inclusive_delegates_to_core():
    from stats import le_share_pct, percentile_rank_inclusive

    seq = [1.0, None, 3.0, float("nan")]
    valid = [1.0, 3.0, float("nan")]  # 仅剔 None（预处理保留）
    assert percentile_rank_inclusive(seq, 3.0) == le_share_pct(valid, 3.0)
    assert percentile_rank_inclusive(seq, 3.0, round_to=0) == round(
        le_share_pct(valid, 3.0), 0)


def test_agent_facts_delegates_keeping_its_preprocessing():
    """num() 预处理（bool→None、NaN→None）保留；计算 == 核心（同清洗后输入）。"""
    import sys
    from pathlib import Path

    scripts = Path(__file__).resolve().parent.parent / "scripts"
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    from agent_facts import num, percentile_rank
    from stats import le_share_pct

    series = [10.0, 20.0, True, None, float("nan"), 30.0]
    cleaned = [x for x in (num(v) for v in series) if x is not None]
    assert cleaned == [10.0, 20.0, 30.0]  # bool/None/NaN 剔除
    assert percentile_rank(series, 20.0) == le_share_pct(cleaned, 20.0)


def test_insight_percentile_delegates_keeping_floor_and_rounding():
    from lib.insight_model import _percentile
    from stats import le_share_pct

    values = [float(i) for i in range(19)]
    assert _percentile(values, 9.0) is None  # 样本下限 20 保留
    values20 = [float(i) for i in range(20)]
    assert _percentile(values20, 9.0) == round(le_share_pct(values20, 9.0), 1) == 50.0
    assert _percentile(values20, 9.4) == pytest.approx(50.0, abs=0.1)
