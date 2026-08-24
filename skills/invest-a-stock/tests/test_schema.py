"""schema 与 collector 辅助逻辑测试。"""

from __future__ import annotations

import pytest


class TestSourceConfidence:
    def test_tushare_high_except_quote(self):
        from lib.schema import source_confidence

        assert source_confidence("tushare.stock_basic", "basic_info") == "high"
        assert source_confidence("tushare.daily", "quote") == "high"

    def test_tencent_medium_for_quote(self):
        from lib.schema import source_confidence

        assert source_confidence("tencent_finance", "quote") == "medium"


class TestDimensionResult:
    def test_quote_prefers_tushare_over_tencent(self):
        from lib.schema import DimensionResult, SourceResult

        sources = [
            SourceResult("tushare.daily", [{"close": 10}], "quote"),
            SourceResult("tencent_finance", {"price": 10.5}, "quote"),
        ]
        dim = DimensionResult("quote", sources)
        assert dim.primary_source == "tushare.daily"
        assert dim.primary_data == [{"close": 10}]

    def test_basic_info_prefers_tushare(self):
        from lib.schema import DimensionResult, SourceResult

        sources = [
            SourceResult("akshare.stock_individual_info_em", {"name": "A"}, "basic_info"),
            SourceResult("tushare.stock_basic", {"name": "B"}, "basic_info"),
        ]
        dim = DimensionResult("basic_info", sources)
        assert dim.primary_source == "tushare.stock_basic"


class TestBaostockCode:
    def test_shanghai(self):
        from lib.collector import _baostock_code, _qp_baostock

        assert _baostock_code("600176") == "sh.600176"
        qp = _qp_baostock("600176", "20250101", "20250601")
        assert "sh.600176" in qp

    def test_shenzhen(self):
        from lib.collector import _baostock_code

        assert _baostock_code("000858") == "sz.000858"

    def test_beijing(self):
        from lib.collector import _baostock_code

        assert _baostock_code("430047") == "bj.430047"
        assert _baostock_code("835185") == "bj.835185"


class TestAkshareKeyMapping:
    def test_kline_key_mapping(self):
        from lib.collector import _map_akshare_kline_keys

        cn = {"日期": "2026-01-15", "开盘": 10.5, "最高": 11.0, "最低": 10.0, "收盘": 10.8, "成交量": 12345678}
        en = _map_akshare_kline_keys(cn)
        assert en["trade_date"] == "2026-01-15"
        assert en["open"] == 10.5
        assert en["high"] == 11.0
        assert en["low"] == 10.0
        assert en["close"] == 10.8
        assert en["vol"] == 12345678

    def test_financial_key_mapping(self):
        from lib.collector import _map_akshare_financial_keys

        cn = {"报告期": "2025-12-31", "净资产收益率": 15.5, "基本每股收益": 3.2,
              "扣非净利润": 500000000, "营业总收入": 5000000000, "净利润": 480000000}
        en = _map_akshare_financial_keys(cn)
        assert en["end_date"] == "2025-12-31"
        assert en["roe"] == 15.5
        assert en["eps"] == 3.2
        assert en["profit_dedt"] == 500000000

    def test_parse_akshare_num_string_formats(self):
        import numpy as np
        from lib.collector import _map_akshare_financial_keys, _parse_akshare_num

        assert _parse_akshare_num("8.37%") == 8.37
        assert _parse_akshare_num("17.88亿") == 1.788e9
        assert _parse_akshare_num("2.35万亿") == pytest.approx(2.35e12)
        assert _parse_akshare_num("2,456.78万") == pytest.approx(24567800.0)
        assert _parse_akshare_num(None) is None
        assert _parse_akshare_num("n/a") is None
        assert _parse_akshare_num(np.int64(500000000)) == 500000000.0

        cn = {"报告期": "2025-12-31", "净资产收益率": "8.37%",
              "基本每股收益": "1.23", "扣非净利润": "17.88亿",
              "营业总收入": "100.5亿", "净利润": "15.2亿"}
        en = _map_akshare_financial_keys(cn)
        assert en["roe"] == 8.37
        assert en["eps"] == 1.23
        assert en["profit_dedt"] == 1.788e9

        cn_numpy = {"报告期": "2025-12-31", "净资产收益率": np.float64(15.5),
                    "基本每股收益": 3.2, "扣非净利润": np.int64(500000000),
                    "营业总收入": "2.35万亿", "净利润": np.int64(480000000)}
        en_numpy = _map_akshare_financial_keys(cn_numpy)
        assert en_numpy["profit_dedt"] == 500000000.0
        assert en_numpy["revenue"] == pytest.approx(2.35e12)

    def test_northbound_key_mapping(self):
        from lib.collector import _map_akshare_northbound_keys

        cn = {"持股日期": "2026-01-15", "今日增持资金": 1.5e8}
        en = _map_akshare_northbound_keys(cn)
        assert en["trade_date"] == "2026-01-15"
        assert en["net_mf_vol"] == 1.5e8

    def test_akshare_top10_code_sh(self):
        from lib.collector import _akshare_top10_code
        assert _akshare_top10_code("600519") == "sh600519"

    def test_akshare_top10_code_sz(self):
        from lib.collector import _akshare_top10_code
        assert _akshare_top10_code("000858") == "sz000858"

    def test_akshare_top10_code_bj(self):
        from lib.collector import _akshare_top10_code
        assert _akshare_top10_code("430047") == "bj430047"

    def test_akshare_top10_code_bshare(self):
        from lib.collector import _akshare_top10_code
        assert _akshare_top10_code("900901") == "sh900901"

    def test_ts_code_bshare(self):
        from lib.collector import _ts_code
        assert _ts_code("900901") == "900901.SH"

    def test_baostock_code_bshare(self):
        from lib.collector import _baostock_code
        assert _baostock_code("900901") == "sh.900901"


class TestExtractL2ScalarOrFallback:
    """fusion 三形态统一 helper（code-review 清理）：五分支语义。"""

    def _helper(self, data, scalar_value, dim_name, allow):
        from lib.schema import _extract_l2_scalar_or_fallback
        return _extract_l2_scalar_or_fallback(
            data, scalar_value, dim_name, allow_scalar_fallback=allow)

    def test_l2_with_data_whitelist_extract(self):
        v = self._helper({"total_mv": 100.0, "pe_ttm": 5.0}, 999.0, "valuation", False)
        assert v == 100.0

    def test_l2_no_data_allow_false_returns_none(self):
        """形态 1：L2 维度 data 缺失绝不回退 scalar（600206 修复语义）。"""
        assert self._helper(None, 999.0, "valuation", False) is None

    def test_l2_no_data_allow_true_falls_back_scalar(self):
        """形态 3：无 data 时回退 scalar_value（兼容手写/旧快照）。"""
        assert self._helper(None, 999.0, "valuation", True) == 999.0

    def test_l2_data_present_but_no_whitelist_key_no_fallback(self):
        """L2 且 data 存在但无白名单键 → None，即使 allow=True 也不回退。"""
        v = self._helper({"pe_ttm": 5.0}, 999.0, "valuation", True)
        assert v is None

    def test_non_l2_returns_scalar_value(self):
        assert self._helper({"close": 10.0}, 888.0, "quote", False) == 888.0
        assert self._helper(None, None, "quote", False) is None
