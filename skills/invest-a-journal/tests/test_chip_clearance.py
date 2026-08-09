"""v0.2.5 D3: compute_chip_clearance 四信号 + 阶段判定（合成 fixture，无网络）。

测试清单（execution-plan.md §2 D3）：
① 四信号数值断言（固定序列手算期望值） ② 阶段分支 ③ confirmation_window 边界
④ 历史 <20 行 → 数据不足 + 降级标注 ⑤ 防双计 ⑥ 无动作词 ⑦ snap=None 降级 snapshot()
⑧ 修复回归：I-1 SSE 空值过滤 / I-2 空快照+足量历史→数据不足 / SSE 降级成功 /
   平局峰值（末次）/ 负去杠杆 / 恰 20 行主路径 / confirmation_window 断言

口径锚点：
- 信号②用 total_turnover（深交所口径，决策 D3-1），不用 total_turnover_est
- 企稳确认 = 从业者惯例代理（ad_ratio≥2.0 且放量≥30日中位数），非严格 90% Upside Day
- 不落库：本测试不触碰真实 DB（load_history 一律 monkeypatch）
"""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest

_LIB = Path(__file__).resolve().parent.parent / "scripts" / "lib"
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

import market_microstructure  # noqa: E402
from market_microstructure import compute_chip_clearance  # noqa: E402


# ---------------------------------------------------------------------------
# fixture 构造（手算期望值的固定序列）
# ---------------------------------------------------------------------------

def make_history(n=60, *, margin_base=20000.0, margin_step=-1,
                 confirm_idx=None, low_ad=True):
    """n 日合成历史。默认模式为测试① 的手算锚点：

    - margin_balance[i] = margin_base + margin_step*i（默认 20000−i，峰值在首日 20000）
    - total_turnover[i] = 5000+i（近 30 日窗口 i∈[30,59] 中位数 = (5044+5045)/2 = 5044.5）
    - 割肉盘日（ad_ratio=0.8 < 1.0）：i ∈ {32,35,37,40,41,44,45,48,50,53,56,58}，
      其中放量（turnover ≥ 5044.5，即 i≥45）的为 {45,48,50,53,56,58} → 6 日
    - limit_down_count：i∈[41,48) 为 40，其余 20；今日 30 → 20 日分位 (12+1)/20 = 65.0
    - confirm_idx 指定放量上涨日（ad_ratio=2.5）
    """
    rows = []
    start = date(2026, 5, 1)
    for i in range(n):
        rows.append({
            "date": (start + timedelta(days=i)).strftime("%Y%m%d"),
            "margin_balance": margin_base + margin_step * i,
            "ad_ratio": 1.5,
            "total_turnover": 5000.0 + i,
            "limit_down_count": 20,
        })
    if low_ad:
        for i in (32, 35, 37, 40, 41, 44, 45, 48, 50, 53, 56, 58):
            if i < n:
                rows[i]["ad_ratio"] = 0.8
    for i in range(41, min(48, n)):
        rows[i]["limit_down_count"] = 40
    if confirm_idx is not None and confirm_idx < n:
        rows[confirm_idx]["ad_ratio"] = 2.5
    return rows


def make_snap(**over):
    """当日快照（测试① 手算锚点：margin 15000 → 去杠杆 (20000−15000)/20000 = 25.0%）。"""
    snap = {
        "date": "20260730",
        "margin_balance": 15000.0,
        "total_turnover": 5050.0,
        "ad_ratio": 1.2,
        "limit_down_count": 30,
        "limit_down_20d_pct": None,
        "margin_20d_change": None,
        "_errors": [],
    }
    snap.update(over)
    return snap


def _patch_history(monkeypatch, rows):
    """load_history 指向合成 fixture（可中途换值，供防双计测试复用）。"""
    holder = {"rows": rows}
    monkeypatch.setattr(
        market_microstructure, "load_history",
        lambda days=120: holder["rows"])
    return holder


def _patch_margin_sse_fail(monkeypatch):
    """akshare stock_margin_sse 降级路径模拟失败（无网络）。"""

    def _fail(*args, **kwargs):
        raise RuntimeError("akshare stock_margin_sse unavailable (test)")

    monkeypatch.setattr("akshare.stock_margin_sse", _fail)


# ---------------------------------------------------------------------------
# ① 四信号数值断言（固定序列，期望值手算）
# ---------------------------------------------------------------------------

def test_four_signals_values(monkeypatch):
    """固定序列手算期望值（见 make_history/make_snap 注释）：

    deleveraging_pct    = (20000−15000)/20000×100 = 25.0
    turnover_60d_pct    = 51/60×100 = 85.0（今日 5050；历史末 59 日 5001..5059）
    down_volume_days_30d= 6（ad<1.0 且放量≥5044.5 的 30 日窗口日数）
    limit_down_20d_pct  = 13/20×100 = 65.0
    days_since_margin_peak = 59（峰值在首日，末次峰值距最新一行 59 行）
    confirmation        = True（i=57 为 ad_ratio 2.5 的放量上涨日，窗口内）
    """
    _patch_history(monkeypatch, make_history(confirm_idx=57))
    out = compute_chip_clearance(snap=make_snap())
    s = out["signals"]
    assert out["date"] == "20260730"
    assert out["available"] is True
    assert s["deleveraging_pct"] == 25.0
    assert s["turnover_60d_pct"] == 85.0
    assert s["down_volume_days_30d"] == 6
    assert s["limit_down_20d_pct"] == 65.0
    assert s["days_since_margin_peak"] == 59
    assert s["confirmation"] is True
    assert out["stage"] == "企稳确认"
    # D3-3：企稳确认口径标注必须存在
    assert any("非严格 90% Upside Day" in n for n in out["calc_notes"])


# ---------------------------------------------------------------------------
# ② 阶段分支
# ---------------------------------------------------------------------------

def test_stage_branches(monkeypatch):
    # 数据不足：历史为空 + margin 降级失败 → 全信号缺失
    _patch_margin_sse_fail(monkeypatch)
    _patch_history(monkeypatch, [])
    out = compute_chip_clearance(snap={"date": "20260730"})
    assert out["stage"] == "数据不足"
    assert out["available"] is False

    # 企稳确认：confirmation=True 优先于去杠杆条件
    _patch_history(monkeypatch, make_history(confirm_idx=57))
    out = compute_chip_clearance(snap=make_snap())
    assert out["stage"] == "企稳确认"

    # 去杠杆中：margin_20d_change < -1（无企稳日）
    _patch_history(monkeypatch, make_history())
    out = compute_chip_clearance(snap=make_snap(margin_20d_change=-5.0))
    assert out["stage"] == "去杠杆中"

    # 磨底中：margin 处于峰值（未低于峰值）、20 日变化未深跌、无企稳日
    _patch_history(monkeypatch, make_history(margin_base=10000.0, margin_step=1))
    out = compute_chip_clearance(
        snap=make_snap(margin_balance=10059.0, margin_20d_change=0.5))
    assert out["stage"] == "磨底中"


# ---------------------------------------------------------------------------
# ③ confirmation_window 边界（第 6 日不算）
# ---------------------------------------------------------------------------

def test_confirmation_window_boundary(monkeypatch):
    """窗口=5 时第 6 天（i=54）的放量上涨日不得触发 confirmation；窗口=6 则触发。"""
    _patch_history(monkeypatch,
                   make_history(margin_base=10000.0, margin_step=1, confirm_idx=54))
    snap = make_snap(margin_balance=10059.0, margin_20d_change=0.5)

    out5 = compute_chip_clearance(snap=snap, confirmation_window=5)
    assert out5["signals"]["confirmation"] is False
    assert out5["stage"] == "磨底中"

    out6 = compute_chip_clearance(snap=snap, confirmation_window=6)
    assert out6["signals"]["confirmation"] is True
    assert out6["stage"] == "企稳确认"


# ---------------------------------------------------------------------------
# ④ 历史 <20 行 → 数据不足 + 降级标注
# ---------------------------------------------------------------------------

def test_short_history_degrades_to_data_insufficient(monkeypatch):
    """10 行历史 → margin 序列 <20 触发 akshare 降级；降级失败 → 数据不足 + 标注。"""
    _patch_margin_sse_fail(monkeypatch)
    _patch_history(monkeypatch, make_history(n=10))
    out = compute_chip_clearance(snap=make_snap())

    assert out["stage"] == "数据不足"
    assert out["available"] is False
    s = out["signals"]
    assert s["deleveraging_pct"] is None
    assert s["turnover_60d_pct"] is None
    assert s["days_since_margin_peak"] is None
    # calc_notes 含降级说明（akshare stock_margin_sse，SSE 口径）
    assert any("stock_margin_sse" in n for n in out["calc_notes"])
    assert any("margin_fallback" in e for e in out["_errors"])


# ---------------------------------------------------------------------------
# ⑤ 防双计（history 含今日行 vs 剔除后结果一致）
# ---------------------------------------------------------------------------

def test_today_row_excluded(monkeypatch):
    """snapshot→_auto_persist→load_history 后 history 已含今日行，结果须一致。"""
    rows = make_history(confirm_idx=57)
    holder = _patch_history(monkeypatch, rows)
    snap = make_snap()

    out_a = compute_chip_clearance(snap=snap)

    # 追加与 snap 同日的已持久化行（真实双计场景）
    holder["rows"] = rows + [{
        "date": snap["date"],
        "margin_balance": 15000.0,
        "ad_ratio": 1.2,
        "total_turnover": 5050.0,
        "limit_down_count": 30,
    }]
    out_b = compute_chip_clearance(snap=snap)

    assert out_a == out_b
    assert out_a["signals"]["turnover_60d_pct"] == 85.0  # 双计会偏移分位


# ---------------------------------------------------------------------------
# ⑥ 输出无动作词 grep 断言
# ---------------------------------------------------------------------------

def test_no_action_words(monkeypatch):
    """stage/signals/calc_notes/_errors 全文不得含买卖建议类动作词（LAW 6）。"""
    _patch_history(monkeypatch, make_history(confirm_idx=57))
    out = compute_chip_clearance(snap=make_snap())

    texts = [out["stage"], str(out["signals"])]
    texts += out["calc_notes"] + out["_errors"]
    joined = " ".join(texts)
    for word in ("建议", "买入", "卖出", "加仓", "减仓", "止损",
                 "止盈", "抄底", "逃顶", "仓位", "目标价"):
        assert word not in joined, f"动作词「{word}」出现在输出中: {joined}"


# ---------------------------------------------------------------------------
# ⑦ snap=None 且 data_bridge 不可用 → 降级 snapshot()（monkeypatch 全失败）
# ---------------------------------------------------------------------------

def test_snap_none_bridge_down_falls_back_to_snapshot(monkeypatch):
    """data_bridge 不可用 → 降级直采 snapshot()；采集全失败 → all_failed 信封。"""
    import data_bridge

    def _bridge_down(*args, **kwargs):
        raise RuntimeError("bridge down")

    monkeypatch.setattr(data_bridge, "get_microstructure", _bridge_down)
    _patch_margin_sse_fail(monkeypatch)
    _patch_history(monkeypatch, [])

    # 模块级 _fetch_* 全失败 → snapshot() 返回 all_failed（无网络）
    for name in ("_fetch_margin", "_fetch_ad_ratio", "_fetch_limit_pools",
                 "_fetch_turnover", "_fetch_erp", "_fetch_pcr",
                 "_fetch_below_book_pct", "_fetch_northbound"):
        monkeypatch.setattr(
            market_microstructure, name,
            lambda result, _n=name: result["_errors"].append(f"{_n}: boom"))

    out = compute_chip_clearance()
    assert out["date"]
    assert out["available"] is False
    assert out["stage"] == "数据不足"
    # 降级 snapshot() 的采集错误已传播 → 证明走了 snapshot() 路径
    assert any("margin: boom" in e for e in out["_errors"])


# ---------------------------------------------------------------------------
# ⑧ 修复回归（code-review findings I-1 / I-2 / Minor 5 + 降级成功路径补齐）
# ---------------------------------------------------------------------------

def test_empty_snapshot_with_margin_history_is_data_insufficient(monkeypatch):
    """I-2 回归：今日快照全缺失 + 足量 margin 历史 → 数据不足。

    不得仅凭历史 days_since_margin_peak（不含今日，无法定位当前周期位置）
    跳过数据不足、断言"磨底中/去杠杆中"。
    """
    _patch_history(monkeypatch, make_history())  # 60 行 margin 历史
    out = compute_chip_clearance(snap={"date": "20260730"})

    assert out["stage"] == "数据不足"
    assert out["available"] is False
    s = out["signals"]
    assert s["deleveraging_pct"] is None
    assert s["turnover_60d_pct"] is None
    # 历史信息保留（days_since_margin_peak 仍可计算），但不足以支撑阶段断言
    assert s["days_since_margin_peak"] is not None


def test_sse_degradation_success(monkeypatch):
    """历史 <20 行 → akshare SSE 降级成功：margin_peak 与 margin_now 均取自 SSE 序列。"""
    _patch_history(monkeypatch, make_history(n=10))  # 10 行 → 触发降级
    df = pd.DataFrame({
        "信用交易日期": ["20260701", "20260702", "20260703"],
        "融资余额": [100.0e8, 120.0e8, 90.0e8],  # 元；峰值 120 在倒数第 2 行
    })
    monkeypatch.setattr("akshare.stock_margin_sse", lambda **kw: df)

    out = compute_chip_clearance(snap=make_snap())
    s = out["signals"]
    assert s["deleveraging_pct"] == 25.0       # (120−90)/120×100，SSE 口径
    assert s["days_since_margin_peak"] == 1    # 末次峰值距最新一行 1 行
    assert out["available"] is True
    assert any("stock_margin_sse" in n for n in out["calc_notes"])


def test_sse_margin_empty_strings_filtered(monkeypatch):
    """I-1 回归：SSE 原始数据含 "" / NaN 空值行 → 过滤后正常计算，不抛错。"""
    _patch_history(monkeypatch, make_history(n=10))
    df = pd.DataFrame({
        "信用交易日期": ["20260701", "20260702", "20260703", "20260704"],
        "融资余额": [100.0e8, "", None, 90.0e8],
    })
    monkeypatch.setattr("akshare.stock_margin_sse", lambda **kw: df)

    out = compute_chip_clearance(snap=make_snap())
    s = out["signals"]
    assert s["deleveraging_pct"] == 10.0       # (100−90)/100×100，空值行已过滤
    assert s["days_since_margin_peak"] == 1
    assert not out["_errors"]


def test_sse_margin_empty_df_degrades_gracefully(monkeypatch):
    """SSE 返回空 DataFrame → 不抛未捕获异常，走数据不足 + 降级错误标注。"""
    _patch_history(monkeypatch, make_history(n=10))
    df = pd.DataFrame({"信用交易日期": [], "融资余额": []})
    monkeypatch.setattr("akshare.stock_margin_sse", lambda **kw: df)

    out = compute_chip_clearance(snap=make_snap())
    assert out["stage"] == "数据不足"
    assert out["signals"]["deleveraging_pct"] is None
    assert any("margin_fallback" in e for e in out["_errors"])


def test_plateau_peak_uses_last_occurrence(monkeypatch):
    """平局峰值：多个相同最大值时取末次峰值（_days_since_peak 语义）。"""
    margins = list(range(19000, 19020))              # idx 0-19：19000..19019
    margins += [20000, 19500, 20000, 19000, 15000]   # idx 20-24：峰值 20000 在 20、22
    rows = []
    start = date(2026, 5, 1)
    for i, m in enumerate(margins):
        rows.append({
            "date": (start + timedelta(days=i)).strftime("%Y%m%d"),
            "margin_balance": float(m),
            "ad_ratio": 1.5,
            "total_turnover": 5000.0 + i,
            "limit_down_count": 20,
        })
    _patch_history(monkeypatch, rows)

    out = compute_chip_clearance(snap=make_snap(margin_balance=12000.0))
    s = out["signals"]
    assert s["days_since_margin_peak"] == 2          # 25−1−22（末次峰值 idx 22）
    assert s["deleveraging_pct"] == 40.0             # (20000−12000)/20000×100


def test_negative_deleveraging_margin_new_high(monkeypatch):
    """margin 创新高（margin_now > margin_peak）→ deleveraging_pct 为负。"""
    _patch_history(monkeypatch, make_history(margin_base=10000.0, margin_step=1))
    out = compute_chip_clearance(snap=make_snap(margin_balance=11000.0))
    s = out["signals"]
    assert s["deleveraging_pct"] == round((10059 - 11000) / 10059 * 100, 2)
    assert s["deleveraging_pct"] < 0
    assert s["days_since_margin_peak"] == 0          # 峰值即最新一行


def test_exactly_20_history_rows_uses_main_path(monkeypatch):
    """恰 20 行 margin 历史 → 走主路径（历史口径），不降级 akshare。"""
    _patch_margin_sse_fail(monkeypatch)  # 若误走降级必失败 → 断言即抓出
    _patch_history(monkeypatch, make_history(n=20))
    out = compute_chip_clearance(snap=make_snap())
    s = out["signals"]
    assert s["deleveraging_pct"] == 25.0             # 主路径：peak=20000（首行）
    assert s["days_since_margin_peak"] == 19         # 20−1−0
    assert not any("stock_margin_sse" in n for n in out["calc_notes"])
    assert not any("margin_fallback" in e for e in out["_errors"])


def test_confirmation_window_zero_asserts(monkeypatch):
    """Minor 5：confirmation_window ≤ 0 必须在函数入口断言拒绝（-0 切片扩至全历史）。"""
    _patch_history(monkeypatch, make_history())
    with pytest.raises(AssertionError):
        compute_chip_clearance(snap=make_snap(), confirmation_window=0)
    with pytest.raises(AssertionError):
        compute_chip_clearance(snap=make_snap(), confirmation_window=-1)
