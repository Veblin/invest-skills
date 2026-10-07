"""A/H 交易日对齐测试（HK-2 D1/D2 修复，2026-09-17）——纯离线。

缺陷回归（三条全部来自 2026-09-17 宁德时代 A/H 实测）：
- D1 跨交易日错配：A 价取 9/16 收盘、H 价取 9/15 收盘 → 引擎当时仍算出 -31.33%，
  正确值（双侧 9/16 收盘）为 -29.27%。差值 2.06pp 全部来自错误对齐。
- D2 竞价指示价：09:00 港股未开盘，r_hk 的 price 是跳动中的竞价参考
  （516.0 → 490.0 → 485.0 一分钟内），被直接当作「H 价」。
- D3 市值口径：r_hk 下标 44 对纯港股是总市值、对 A+H 是 H 股部分——见
  hk_ah.MCAP_SCOPE_NOTE 的断言测试。

对齐纪律：溢价率仅在 **同交易日 + 同状态族 + 盘中满足 as_of 容差** 时计算；否则拒绝出数。

O-23（2026-09-23 裁决）：**同态不足以保证时点可比**——两侧同处「交易中」时价格仍在
变动，须校验两个快照的 as_of 间隔，容差 ±5 分钟（= 5 × 1 分钟**比较粒度**；两侧时间戳
本身带秒位，但本模块比对只到分钟，探针依据见
`host-docs/v0.3.1/G-a-repro-v0.3.1_20260923.md` §1.1）。收盘终值/午间停摆不适用；
混合状态（A 收盘 / H 交易）按裁决保留可比但须显示实际时差。
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
    """同日两侧均在交易时段且**时点相近**（此处相隔 0 分钟）→ 可比。"""
    a = {"price": 300.0, "ts": "20260917103000"}
    h = {"price": 495.0, "ts": "2026/09/17 10:30:00"}
    al = hk_ah.align_quotes(a, h)
    assert al["comparable"] is True
    assert "盘中" in al["basis"]
    assert al["tolerance_applied"] is True      # O-23：盘中须经容差判定
    assert al["as_of_gap_min"] == 0


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
    """**真·双侧午间休市**（12:00–13:00 重叠段）：价格停摆 → 可比且不适用容差。

    注：11:30–12:00 不是双侧午间——A 股 11:30 起休市、港股仍交易至 12:00，
    该窗口属**混合状态**，见 `test_align_lunch_trading_window_mixed_is_comparable`。
    """
    a = {"price": 305.87, "ts": "20260917121000"}         # A 12:10 午间休市
    h = {"price": 504.0, "ts": "2026/09/17 12:25:00"}     # H 12:25 午间休市
    al = hk_ah.align_quotes(a, h)
    assert al["comparable"] is True
    assert al["a_state"] == al["h_state"] == hk_ah._STATE_LUNCH
    assert al["tolerance_applied"] is False
    assert "午间休市" in al["basis"] and "不适用盘中容差" in al["basis"]


def test_align_lunch_trading_window_mixed_is_comparable():
    """11:30–12:00 窗口：A 已休市、港股仍在交易 → **混合状态**，保留可比并标注时差。"""
    a = {"price": 305.87, "ts": "20260917114800"}
    h = {"price": 504.0, "ts": "2026/09/17 11:59:57"}
    al = hk_ah.align_quotes(a, h)
    assert al["comparable"] is True
    assert al["a_state"] == hk_ah._STATE_LUNCH
    assert al["h_state"] == hk_ah._STATE_TRADING
    assert al["tolerance_applied"] is False
    assert "混合状态" in al["basis"]


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


def test_align_quotes_emits_no_share_count_or_combined_cap():
    """D3：缺股本结构时**不给推断值**——对齐结果里不得出现股数或 A+H 合计市值。

    实测背景（需求文档 §2.1.1）：H 股数 2.1829 亿股取自前十大流通股东的
    HKSCC NOMINEES 持仓，属**推断值**；引擎未采集股本结构字段。该推断数一旦
    进入结构化输出就会被下游当事实消费。
    """
    a = {"price": 305.48, "ts": A_TS_0916}
    h = {"price": 501.00, "ts": H_TS_0916_CLOSE, "prev_close": 516.00,
         "mcap_hkd_yi": 1070.0}
    al = hk_ah.align_quotes(a, h)
    for key in al:
        assert "share" not in key and "股本" not in key, key
        assert "mcap" not in key and "市值" not in key, key
    assert not any("2.1829" in str(v) for v in al.values())


# ── O-23 盘中 as_of 容差（2026-09-23 裁决：±5 分钟）─────────────────────

def test_tolerance_value_is_five_minutes():
    """冻结值：±5 分钟（= 5 × 1 分钟**比较粒度**，依据见 G-a 复现表 §1.1）。"""
    assert hk_ah._INTRADAY_TOLERANCE_MIN == 5


def test_align_intraday_within_tolerance_is_comparable():
    a = {"price": 300.0, "ts": "20260917103000"}          # A 10:30
    h = {"price": 495.0, "ts": "2026/09/17 10:33:00"}     # H 10:33 → 相隔 3 分钟
    al = hk_ah.align_quotes(a, h)
    assert al["comparable"] is True
    assert al["tolerance_applied"] is True
    assert al["as_of_gap_min"] == 3
    assert "3 分钟" in al["basis"] and "容差" in al["basis"]


def test_align_intraday_exactly_at_tolerance_is_comparable():
    """边界：恰等于容差 → 通过（判据是 ``<=``）。"""
    a = {"price": 300.0, "ts": "20260917103000"}
    h = {"price": 495.0, "ts": "2026/09/17 10:35:00"}     # 相隔 5 分钟
    al = hk_ah.align_quotes(a, h)
    assert al["as_of_gap_min"] == 5
    assert al["comparable"] is True


def test_align_intraday_beyond_tolerance_refused():
    """O-23 回归：两侧同为「交易中」但相隔 37 分钟 → 拒绝出数并给可诊断原因。

    这是「均交易中即可比」旧判据的失效形态：状态相同不等于时刻相同。
    """
    a = {"price": 300.0, "ts": "20260917103000"}          # A 10:30
    h = {"price": 495.0, "ts": "2026/09/17 11:07:00"}     # H 11:07 → 相隔 37 分钟
    al = hk_ah.align_quotes(a, h)
    assert al["comparable"] is False
    assert al["tolerance_applied"] is True
    assert al["as_of_gap_min"] == 37
    assert "37 分钟" in al["basis"] and "5 分钟容差" in al["basis"]
    assert "10:30" in al["basis"] and "11:07" in al["basis"]
    # 两侧状态本身仍是「交易中」——拒绝的理由只能是时点，不能是状态
    assert al["a_state"] == al["h_state"] == hk_ah._STATE_TRADING


def test_align_closed_not_subject_to_tolerance():
    """两侧已收盘：价格为终值，不再变动 → 不适用容差（时差仍记录）。"""
    a = {"price": 305.48, "ts": A_TS_0916}                # A 16:14
    h = {"price": 501.00, "ts": H_TS_0916_CLOSE, "prev_close": 516.00}   # H 16:08
    al = hk_ah.align_quotes(a, h)
    assert al["comparable"] is True
    assert al["tolerance_applied"] is False
    assert al["as_of_gap_min"] == 6
    assert "不适用盘中容差" in al["basis"]


def test_align_mixed_state_keeps_comparable_and_shows_gap():
    """混合状态按 2026-09-23 裁决**保留可比**，但须把实际时差写给读者。"""
    a = {"price": 305.48, "ts": "20260916150030"}         # A 15:00 收盘
    h = {"price": 495.0, "ts": "2026/09/16 15:30:00"}     # H 15:30 仍在交易
    al = hk_ah.align_quotes(a, h)
    assert al["comparable"] is True
    assert al["tolerance_applied"] is False
    assert al["as_of_gap_min"] == 30
    assert "混合状态" in al["basis"] and "30 分钟" in al["basis"]


# ── O-23 可观测性：as_of 字段 ────────────────────────────────────────────

def test_align_exposes_as_of_datetime_for_both_sides():
    """行情时点必须是完整 ``YYYY-MM-DD HH:MM``——只给状态无法判断相隔多久。"""
    a = {"price": 305.48, "ts": A_TS_0916}
    h = {"price": 501.00, "ts": H_TS_0916_CLOSE, "prev_close": 516.00}
    al = hk_ah.align_quotes(a, h)
    assert al["a_as_of"] == "2026-09-16 16:14"
    assert al["h_as_of"] == "2026-09-16 16:08"


def test_align_cross_day_gap_is_none_not_zero():
    """跨日报价：钟点差**无意义**，不得算出「相隔 0 分钟」。

    2026-09-23 验收发现的缺陷：gap 原先只看钟点、不看日期，A 9/16 10:30 与
    H 9/15 10:30 会报 0 分钟——而两个价格实际相差一个交易日。溢价率本就正确
    拒算，但报告里的这个假数字会误导读者以为两侧是同一时刻。
    """
    a = {"price": 305.48, "ts": "20260916103000"}              # A 9/16 10:30
    h = {"price": 501.00, "ts": "2026/09/15 10:30:00",         # H 9/15 10:30
         "prev_close": 516.00}
    al = hk_ah.align_quotes(a, h)
    assert al["comparable"] is False, "跨日必须拒算"
    assert al["a_date"] == "2026-09-16" and al["h_date"] == "2026-09-15"
    assert al["as_of_gap_min"] is None, "跨日不得给出钟点差"
    assert "2026-09-16" in al["basis"] and "2026-09-15" in al["basis"]


def test_align_as_of_none_when_ts_unparseable():
    al = hk_ah.align_quotes({"price": 1.0, "ts": "N/A"}, {"price": 2.0, "ts": None})
    assert al["a_as_of"] is None and al["h_as_of"] is None
    assert al["as_of_gap_min"] is None
    assert al["comparable"] is False


def test_as_of_gap_min_helper_edges():
    assert hk_ah._as_of_gap_min("10:30", "11:07") == 37
    assert hk_ah._as_of_gap_min("11:07", "10:30") == 37   # 对称
    assert hk_ah._as_of_gap_min(None, "10:30") is None
    assert hk_ah._as_of_gap_min("bad", "10:30") is None
