"""invest-a-futures-link 纯函数测试（离线，不触网）。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from futures_link import judge, series_stats  # noqa: E402


def _s(last: float, ago5: float | None, ago20: float | None) -> dict:
    return {
        "last": last,
        "chg5_pct": round((last / ago5 - 1) * 100, 2) if ago5 else None,
        "chg20_pct": round((last / ago20 - 1) * 100, 2) if ago20 else None,
        "vs_ma20_pct": 0.0,
    }


def test_series_stats_insufficient():
    out = series_stats([1.0, 2.0, 3.0])
    assert out["chg20_pct"] is None and out["chg5_pct"] is None
    out = series_stats([float(i) for i in range(1, 10)])
    assert out["chg5_pct"] is not None and out["chg20_pct"] is None  # 6 点够 5d、不够 20d


def test_series_stats_normal():
    closes = [10.0 * (1 + 0.01 * i) for i in range(45)]  # 线性日增 1%
    out = series_stats(closes)
    assert 15.0 < out["chg20_pct"] < 17.0   # 20 日窗: 14.4/12.4-1 ≈ +16.1%
    assert 3.0 < out["chg5_pct"] < 4.0      # 5 日窗: 14.4/13.9-1 ≈ +3.6%
    assert out["vs_ma20_pct"] > 0


def test_judge_resonance_and_divergence():
    fut_up = _s(110, 105, 100)      # +10%
    stk_up = _s(11, 10.5, 10)       # +10%
    stk_down = _s(9.5, 10.0, 10.5)  # -9.5%
    assert judge(fut_up, stk_up, inverse=False) == "共振↑"
    assert judge(fut_up, stk_down, inverse=False) == "背离"
    # 反向链：期货涨 + 股票跌 = 逻辑一致（成本端）
    assert judge(fut_up, stk_down, inverse=True) == "共振↑(成本反向)"


def test_judge_fallback_to_5d_and_insufficient():
    fut_5d_only = _s(110, 100, None)   # 20d 缺 → 回落 5d
    stk_up = _s(11, 10.5, 10)
    assert judge(fut_5d_only, stk_up, inverse=False) == "共振↑"
    empty = _s(1.0, None, None)
    assert judge(empty, stk_up, inverse=False) == "样本不足"


def test_judge_divergence_direction_signs():
    # 期货 -10% 股票 -10% → 共振↓
    fut_dn = _s(90, 95, 100)
    stk_dn = _s(9, 9.5, 10)
    assert judge(fut_dn, stk_dn, inverse=False) == "共振↓"


def test_judge_flat_band_no_false_divergence():
    """±0.5% 死区（review F3）：平盘不参与方向，消除单侧比较的符号误标。"""
    # 期货 +2% / 股票 +0.3%（都向上，股票只是死区内）→ 分化（非「背离」）
    assert judge(_s(102, 100, 100), _s(10.03, 10, 10), inverse=False) == "分化"
    # 镜像对称：期货 -2% / 股票 -0.3% → 分化（此前会与上行例矛盾地判「背离 vs 分化」）
    assert judge(_s(98, 100, 100), _s(9.97, 10, 10), inverse=False) == "分化"
    # 两侧均死区 → 平盘
    assert judge(_s(100.3, 100, 100), _s(9.97, 10, 10), inverse=False) == "平盘"
    # 股票强动 + 期货死区 → 分化（非背离）
    assert judge(_s(100.3, 100, 100), _s(10.5, 10, 10), inverse=False) == "分化"
    # 强背离仍判背离：期货 +2% / 股票 -0.3%（-0.3% 死区内 → 分化而非背离——修正后语义）
    assert judge(_s(102, 100, 100), _s(9.97, 10, 10), inverse=False) == "分化"
    # 真实背离边界：期货 +2% / 股票 -3% → 背离
    assert judge(_s(102, 100, 100), _s(9.7, 10, 10), inverse=False) == "背离"
