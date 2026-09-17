"""A/H 交易日对齐测试（HK-2 D1/D2 修复，2026-09-17）——纯离线。

缺陷回归（三条全部来自 2026-09-17 宁德时代 A/H 实测）：
- D1 跨交易日错配：A 价取 9/16 收盘、H 价取 9/15 收盘 → 引擎当时仍算出 -31.33%，
  正确值（双侧 9/16 收盘）为 -29.27%。差值 2.06pp 全部来自错误对齐。
- D2 竞价指示价：09:00 港股未开盘，r_hk 的 price 是跳动中的竞价参考
  （516.0 → 490.0 → 485.0 一分钟内），被直接当作「H 价」。
- D3 市值口径：r_hk 下标 44 对纯港股是总市值、对 A+H 是 H 股部分——见
  hk_ah.MCAP_SCOPE_NOTE 的断言测试。

对齐纪律：溢价率仅在 **同交易日 + 同状态族** 时计算；否则拒绝出数。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts" / "lib"))

import hk_ah  # noqa: E402

# ── 实测原文（2026-09-17 抓取）────────────────────────────────────────────
A_TS_0916 = "20260916161421"            # A 股：9/16 16:14（收盘后）
H_TS_0917_AUCTION = "2026/09/17 09:00:30"   # H 股：9/17 竞价时段（未开盘）
H_TS_0916_CLOSE = "2026/09/16 16:08:06"     # H 股：9/16 16:08（收盘后）


# ── parse_quote_ts ───────────────────────────────────────────────────────

def test_parse_a_share_compact_format():
    assert hk_ah.parse_quote_ts(A_TS_0916) == ("2026-09-16", "16:14")


def test_parse_hk_slash_format():
    assert hk_ah.parse_quote_ts(H_TS_0917_AUCTION) == ("2026-09-17", "09:00")


def test_parse_unparseable_returns_none_pair():
    for bad in (None, "", "N/A", "--", "2026", "abcdef"):
        assert hk_ah.parse_quote_ts(bad) == (None, None), bad


# ── market_state ─────────────────────────────────────────────────────────

def test_market_state_a_share_sessions():
    assert hk_ah.market_state(A_TS_0916, "A") == hk_ah._STATE_CLOSED       # 16:14
    assert hk_ah.market_state("20260917090000", "A") == hk_ah._STATE_PREOPEN
    assert hk_ah.market_state("20260917092000", "A") == hk_ah._STATE_AUCTION
    assert hk_ah.market_state("20260917103000", "A") == hk_ah._STATE_TRADING
    assert hk_ah.market_state("20260917120000", "A") == hk_ah._STATE_LUNCH
    assert hk_ah.market_state("20260917140000", "A") == hk_ah._STATE_TRADING
    assert hk_ah.market_state("20260917150000", "A") == hk_ah._STATE_CLOSED


def test_market_state_hk_sessions():
    # 09:00 是港股开市前竞价起点——D2 缺陷现场
    assert hk_ah.market_state(H_TS_0917_AUCTION, "HK") == hk_ah._STATE_AUCTION
    assert hk_ah.market_state(H_TS_0916_CLOSE, "HK") == hk_ah._STATE_CLOSED
    assert hk_ah.market_state("2026/09/17 08:30:00", "HK") == hk_ah._STATE_PREOPEN
    assert hk_ah.market_state("2026/09/17 10:00:00", "HK") == hk_ah._STATE_TRADING
    assert hk_ah.market_state("2026/09/17 12:30:00", "HK") == hk_ah._STATE_LUNCH
    assert hk_ah.market_state("2026/09/17 15:00:00", "HK") == hk_ah._STATE_TRADING


def test_market_state_unknown_never_guessed_as_closed():
    """时间戳不可解析 → 未知。不得回退成「已收盘」（会把坏输入读成可比）。"""
    assert hk_ah.market_state(None, "A") == hk_ah._STATE_UNKNOWN
    assert hk_ah.market_state("garbage", "HK") == hk_ah._STATE_UNKNOWN
    assert hk_ah.market_state(A_TS_0916, "US") == hk_ah._STATE_UNKNOWN


# ── align_quotes ─────────────────────────────────────────────────────────

def test_align_same_day_both_closed_is_comparable():
    a = {"price": 305.48, "ts": A_TS_0916}
    h = {"price": 501.00, "ts": H_TS_0916_CLOSE, "prev_close": 516.00}
    al = hk_ah.align_quotes(a, h)
    assert al["comparable"] is True
    assert "收盘" in al["basis"]
    assert al["a_date"] == al["h_date"] == "2026-09-16"


def test_align_same_day_both_trading_is_comparable():
    a = {"price": 300.0, "ts": "20260917103000"}
    h = {"price": 495.0, "ts": "2026/09/17 10:30:00"}
    al = hk_ah.align_quotes(a, h)
    assert al["comparable"] is True
    assert "盘中" in al["basis"]


def test_align_d1_regression_cross_day_refused():
    """D1 缺陷回归：A 9/16 收盘 vs H 9/17 竞价——引擎当时仍算出 -31.33%。

    正确行为：拒绝出数，且原因里同时给出两侧日期。
    """
    a = {"price": 305.48, "ts": A_TS_0916}
    h = {"price": 516.0, "ts": H_TS_0917_AUCTION, "prev_close": 501.00}
    al = hk_ah.align_quotes(a, h)
    assert al["comparable"] is False
    assert "2026-09-16" in al["basis"] and "2026-09-17" in al["basis"]
    assert al["a_state"] == hk_ah._STATE_CLOSED
    assert al["h_state"] == hk_ah._STATE_AUCTION


def test_align_same_day_both_lunch_is_comparable():
    """午间休市两侧均为当日成交价 → 可比（12:00–13:00 只是暂停撮合）。"""
    a = {"price": 305.87, "ts": "20260917114800"}
    h = {"price": 504.0, "ts": "2026/09/17 11:59:57"}
    al = hk_ah.align_quotes(a, h)
    assert al["comparable"] is True
    assert "午间休市" in al["basis"]


def test_align_same_day_mixed_closed_and_trading_is_comparable():
    """A 已收盘（15:00）+ H 仍在交易（到 16:00）→ 同为当日成交价，可比（混合口径）。"""
    a = {"price": 305.48, "ts": "20260916150030"}
    h = {"price": 495.0, "ts": "2026/09/16 15:30:00"}
    al = hk_ah.align_quotes(a, h)
    assert al["comparable"] is True
    assert "混合状态" in al["basis"]


def test_align_same_day_both_auction_refused():
    """D2 边界：同日两侧都在竞价时段 → 竞价参考价不是成交价，仍拒绝。"""
    a = {"price": 308.0, "ts": "20260917092000"}
    h = {"price": 505.0, "ts": "2026/09/17 09:20:00"}
    al = hk_ah.align_quotes(a, h)
    assert al["comparable"] is False
    assert "竞价参考价不是成交价" in al["basis"]


def test_align_unparseable_ts_refused():
    al = hk_ah.align_quotes({"price": 1.0, "ts": None}, {"price": 2.0, "ts": None})
    assert al["comparable"] is False
    assert "不可解析" in al["basis"]


def test_align_missing_quote_dicts_do_not_crash():
    """D2（development-rules）：None/缺键不得抛异常。"""
    for a, h in ((None, None), ({}, {}), ({"price": 1.0}, {"price": 2.0})):
        al = hk_ah.align_quotes(a, h)
        assert al["comparable"] is False


def test_align_fallback_uses_h_prev_close_with_premise_label():
    """不可比时给出回退口径，但前提必须显式标为待验证（不代为断言）。"""
    a = {"price": 305.48, "ts": A_TS_0916}
    h = {"price": 490.0, "ts": H_TS_0917_AUCTION, "prev_close": 501.00}
    al = hk_ah.align_quotes(a, h)
    assert al["fallback"]["h_price"] == 501.00
    assert "不代为断言" in al["fallback"]["premise"]


def test_align_fallback_refuses_a_auction_indication():
    """回退口径称 A 价为收盘，故 A 竞价时即使有价格也不得发布回退值。"""
    a = {"price": 308.0, "ts": "20260917092000"}
    h = {"price": 505.0, "ts": "2026/09/17 09:20:00", "prev_close": 501.0}
    assert hk_ah.align_quotes(a, h)["fallback"] is None


def test_align_no_fallback_without_h_prev_close():
    a = {"price": 305.48, "ts": A_TS_0916}
    h = {"price": 490.0, "ts": H_TS_0917_AUCTION}
    assert hk_ah.align_quotes(a, h)["fallback"] is None


# ── 端到端：对齐后的溢价率与引擎当时的错值不同 ─────────────────────────────

def test_aligned_premium_differs_from_engine_wrong_value():
    """回归锚点：双侧 9/16 收盘的正确溢价率 ≠ 引擎当时输出的 -31.33%。"""
    fx = 0.86210
    pct_correct = hk_ah.premium_pct(305.48, 501.00, fx)
    pct_wrong = hk_ah.premium_pct(305.48, 516.00, fx)   # H 取到 9/15 收盘
    assert pct_correct is not None and pct_wrong is not None
    assert abs(pct_correct - (-29.27)) < 0.02
    assert abs(pct_wrong - (-31.33)) < 0.02
    assert pct_correct != pct_wrong


# ── D3 市值口径 ──────────────────────────────────────────────────────────

def test_mcap_scope_note_states_incomparable():
    note = hk_ah.MCAP_SCOPE_NOTE
    assert "H 股部分" in note
    assert "不得与 A 股口径总市值并列" in note
