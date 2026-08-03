"""Tests for skills/lib/codes.py — pure mapping, no network."""

from __future__ import annotations

import sys
from pathlib import Path

_SKILLS_LIB = Path(__file__).resolve().parents[1]
if str(_SKILLS_LIB) not in sys.path:
    sys.path.insert(0, str(_SKILLS_LIB))

from codes import (  # noqa: E402
    classify_board,
    etf_symbol_to_ts_code,
    exchange_code,
    market_label,
    symbol_to_ts_code,
)


class TestSymbolToTsCode:
    def test_shanghai_main(self):
        assert symbol_to_ts_code("600176") == "600176.SH"

    def test_shanghai_star(self):
        assert symbol_to_ts_code("688001") == "688001.SH"

    def test_shanghai_b_share(self):
        assert symbol_to_ts_code("900901") == "900901.SH"

    def test_shenzhen_main(self):
        assert symbol_to_ts_code("000001") == "000001.SZ"

    def test_chinext(self):
        assert symbol_to_ts_code("300750") == "300750.SZ"

    def test_beijing(self):
        assert symbol_to_ts_code("830799") == "830799.BJ"
        assert symbol_to_ts_code("430047") == "430047.BJ"

    def test_bse_920_series(self):
        # BSE 920xxx（2025-10 起交易）以 '9' 开头，须路由到 BJ 而非 SH
        assert symbol_to_ts_code("920002") == "920002.BJ"

    def test_padded(self):
        assert symbol_to_ts_code("1") == "000001.SZ"

    def test_invalid(self):
        assert symbol_to_ts_code("xxx") == ""


class TestExchangeCode:
    def test_shanghai_formats(self):
        codes = exchange_code("600176")
        assert codes == {
            "tushare": "600176.SH",
            "baostock": "sh.600176",
            "akshare": "sh600176",
        }

    def test_beijing_formats(self):
        codes = exchange_code("830799")
        assert codes["tushare"] == "830799.BJ"

    def test_bse_920_formats(self):
        codes = exchange_code("920002")
        assert codes == {
            "tushare": "920002.BJ",
            "baostock": "bj.920002",
            "akshare": "bj920002",
        }


class TestEtfSymbolToTsCode:
    def test_shanghai_5_prefix(self):
        assert etf_symbol_to_ts_code("510300") == "510300.SH"

    def test_shanghai_6_prefix(self):
        assert etf_symbol_to_ts_code("588000") == "588000.SH"

    def test_shenzhen(self):
        assert etf_symbol_to_ts_code("159915") == "159915.SZ"
        assert etf_symbol_to_ts_code("161725") == "161725.SZ"

    def test_beijing(self):
        assert etf_symbol_to_ts_code("830799") == "830799.BJ"

    def test_bse_920_series(self):
        assert etf_symbol_to_ts_code("920002") == "920002.BJ"

    def test_invalid(self):
        assert etf_symbol_to_ts_code("xxx") == ""


class TestClassifyBoard:
    def test_from_market_field(self):
        assert classify_board("600176.SH", "创业板") == "创业板"

    def test_from_ts_code(self):
        assert classify_board("688001.SH") == "科创板"
        assert classify_board("300750.SZ") == "创业板"
        assert classify_board("600176.SH") == "主板"


class TestMarketLabel:
    def test_chinese(self):
        assert market_label("主板") == "主板"

    def test_numeric(self):
        assert market_label("0") == "主板"
        assert market_label("1") == "创业板"

    def test_empty_string(self):
        assert market_label("") == "未知"

    def test_none(self):
        assert market_label(None) == "未知"

    def test_unknown(self):
        assert market_label("99") == "未知(99)"
