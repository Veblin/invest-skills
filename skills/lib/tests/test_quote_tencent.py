"""skills/lib/quote_tencent 单元测试 — 全 fixture 数据，不联网。

覆盖 v0.2.7 收敛契约：统一市场路由（含北交所豁免）、命名下标、
单位换算语义（amount 万元→元 / total_mv_yi 亿元不换算）、
不可用标记 → None（D1：0.0 是合法值）、price 缺失判无效。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_SKILLS_LIB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SKILLS_LIB))  # 无条件插 0：防其他 skill 目录先行入 path 遮蔽同名模块

from quote_tencent import (  # noqa: E402
    IDX_AMOUNT_WAN,
    IDX_CHANGE_PCT,
    IDX_HIGH,
    IDX_LOW,
    IDX_PB,
    IDX_PE,
    IDX_PRICE,
    IDX_TOTAL_MV_YI,
    IDX_TURNOVER_RATE,
    IDX_VOLUME,
    build_tencent_quote_url,
    fetch_tencent_quote,
    is_tencent_unsupported,
    parse_tencent_quote,
    tencent_market,
)


def _payload(fields: dict[int, str], n: int = 50) -> str:
    """按下标构造 qt.gtimg.cn 风格响应文本（其余字段填空串）。"""
    p = [""] * n
    for idx, val in fields.items():
        p[idx] = val
    return "~".join(p)


class TestRouting:
    """统一路由裁决：5/6/9 → sh；其余 → sz；4/8/920 → None（北交所豁免）。"""

    @pytest.mark.parametrize("symbol,expected", [
        ("600000", "sh"), ("688981", "sh"), ("900901", "sh"),
        ("512660", "sh"), ("560050", "sh"),          # 沪 ETF/LOF（etf 份独有前缀，已并入）
        ("000001", "sz"), ("300750", "sz"), ("159915", "sz"), ("002466", "sz"),
        ("600176.SH", "sh"), ("300750.SZ", "sz"),    # 带后缀也路由正确
    ])
    def test_market(self, symbol, expected):
        assert tencent_market(symbol) == expected

    @pytest.mark.parametrize("symbol", [
        "920001", "830799", "430047", "920001.BJ", "830799.NQ", "400001",
    ])
    def test_north_exchange_unsupported(self, symbol):
        assert is_tencent_unsupported(symbol) is True
        assert tencent_market(symbol) is None

    @pytest.mark.parametrize("symbol,expected", [
        ("600000", "http://qt.gtimg.cn/q=sh600000"),
        ("512660", "http://qt.gtimg.cn/q=sh512660"),
        ("159915", "http://qt.gtimg.cn/q=sz159915"),
        ("600176.SH", "http://qt.gtimg.cn/q=sh600176"),  # 收敛：剥离后缀（旧 valuation 直接拼后缀必失败）
    ])
    def test_build_url(self, symbol, expected):
        assert build_tencent_quote_url(symbol) == expected

    def test_build_url_unsupported_returns_none(self):
        assert build_tencent_quote_url("920001") is None
        assert build_tencent_quote_url("") is None
        assert build_tencent_quote_url(None) is None


class TestParse:
    def test_full_payload_all_fields(self):
        text = _payload({
            IDX_PRICE: "18.52",
            IDX_CHANGE_PCT: "-1.23",
            IDX_HIGH: "19.10",
            IDX_LOW: "18.30",
            IDX_VOLUME: "1768251",
            IDX_AMOUNT_WAN: "323831",
            IDX_TURNOVER_RATE: "2.34",
            IDX_PE: "25.1",
            IDX_TOTAL_MV_YI: "952.7",
            IDX_PB: "3.05",
        })
        q = parse_tencent_quote(text)
        assert q is not None
        assert q["price"] == 18.52
        assert q["change_pct"] == -1.23
        assert q["high"] == 19.10
        assert q["low"] == 18.30
        assert q["volume"] == 1768251.0
        assert q["turnover_rate"] == 2.34
        assert q["pe_ratio"] == 25.1
        assert q["total_mv_yi"] == 952.7
        assert q["pb"] == 3.05

    def test_amount_wan_to_yuan(self):
        """下标 37 成交额（万元）→ amount（元）×1e4（F1-1 单位对齐）。"""
        text = _payload({IDX_PRICE: "1.152", IDX_AMOUNT_WAN: "323831"})
        q = parse_tencent_quote(text)
        assert q is not None
        assert q["amount"] == pytest.approx(323831 * 1e4)

    def test_total_mv_stays_yi(self):
        """下标 45 总市值（亿元）不换算（键名 total_mv_yi 显式带单位）。"""
        text = _payload({IDX_PRICE: "18.52", IDX_TOTAL_MV_YI: "952.7"})
        q = parse_tencent_quote(text)
        assert q is not None
        assert q["total_mv_yi"] == 952.7

    def test_zero_price_is_valid(self):
        """D1：0.0 是合法价格，不得被当缺失。"""
        text = _payload({IDX_PRICE: "0", IDX_CHANGE_PCT: "0"})
        q = parse_tencent_quote(text)
        assert q is not None
        assert q["price"] == 0.0
        assert q["change_pct"] == 0.0

    def test_unavailable_markers_to_none(self):
        """--/N/A/空/— 占位 → None（与真实 0 区分）。"""
        text = _payload({
            IDX_PRICE: "18.52",
            IDX_VOLUME: "--",
            IDX_TURNOVER_RATE: "N/A",
            IDX_PE: "",
            IDX_TOTAL_MV_YI: "—",
        })
        q = parse_tencent_quote(text)
        assert q is not None
        assert q["volume"] is None
        assert q["turnover_rate"] is None
        assert q["pe_ratio"] is None
        assert q["total_mv_yi"] is None
        assert q["price"] == 18.52

    def test_price_marker_means_invalid(self):
        """price 占位（停牌/无效）→ 整包无效（统一判据）。"""
        text = _payload({IDX_PRICE: "--", IDX_TURNOVER_RATE: "2.3"})
        assert parse_tencent_quote(text) is None

    def test_short_payload_invalid(self):
        """字段数 < 46（覆盖不到下标 45）→ None。"""
        text = _payload({IDX_PRICE: "18.52"}, n=45)
        assert parse_tencent_quote(text) is None

    def test_no_tilde_invalid(self):
        assert parse_tencent_quote("v_sh600000=hello") is None
        assert parse_tencent_quote("") is None
        assert parse_tencent_quote(None) is None

    def test_pb_absent_when_len_46(self):
        """len == 46 时 pb（下标 46）→ None，其余字段可用。"""
        text = _payload({IDX_PRICE: "18.52"}, n=46)
        q = parse_tencent_quote(text)
        assert q is not None
        assert q["pb"] is None
        assert q["price"] == 18.52


class _FakeResp:
    def __init__(self, text: str, status_code: int = 200):
        self.text = text
        self.status_code = status_code


class _FakeSess:
    def __init__(self, resp):
        self.resp = resp
        self.calls: list[tuple[str, float]] = []

    def get(self, url, timeout):
        self.calls.append((url, timeout))
        return self.resp


class TestFetch:
    def test_success_with_injected_session(self):
        text = _payload({IDX_PRICE: "18.52", IDX_AMOUNT_WAN: "323831"})
        sess = _FakeSess(_FakeResp(text))
        q = fetch_tencent_quote("600000", session=sess)
        assert q is not None
        assert q["price"] == 18.52
        assert q["amount"] == pytest.approx(323831 * 1e4)
        assert sess.calls == [("http://qt.gtimg.cn/q=sh600000", 5.0)]

    def test_unsupported_never_requests(self):
        sess = _FakeSess(_FakeResp(""))
        assert fetch_tencent_quote("920001", session=sess) is None
        assert sess.calls == []

    def test_non_200_returns_none(self):
        sess = _FakeSess(_FakeResp("x", status_code=500))
        assert fetch_tencent_quote("600000", session=sess) is None

    def test_transport_exception_propagates(self):
        """传输层异常上抛（调用方各自按失败契约处理，D5 不静默吞）。"""

        class _BoomSess:
            def get(self, url, timeout):
                raise RuntimeError("connection refused")

        with pytest.raises(RuntimeError):
            fetch_tencent_quote("600000", session=_BoomSess())
