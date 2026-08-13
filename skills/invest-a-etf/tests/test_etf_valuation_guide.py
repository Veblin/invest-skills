"""v0.2.6 清理：query_sector_valuation_guide 由死变活（_attach_valuation_guide 收敛调用）。"""

from __future__ import annotations

import pytest

from etf_data import (
    ETF_TO_SW_INDUSTRY,
    SECTOR_VALUATION_MAP,
    _attach_valuation_guide,
    query_sector_valuation_guide,
)


def test_query_sector_valuation_guide_returns_map_entry():
    guide = query_sector_valuation_guide("电子")
    assert guide is not None
    assert guide["primary"] == SECTOR_VALUATION_MAP["电子"]["primary"]
    assert set(guide) == {"primary", "secondary", "pe_timing", "reason"}


def test_query_sector_valuation_guide_unknown_returns_none():
    assert query_sector_valuation_guide("不存在的行业") is None


def test_attach_valuation_guide_uses_shared_lookup():
    """_attach_valuation_guide 与 query_sector_valuation_guide 同源（死 API 变活）。"""
    symbol = "512480"  # ETF_TO_SW_INDUSTRY → 电子
    sw_name = ETF_TO_SW_INDUSTRY[symbol]["sw_name"]
    result: dict = {"symbol": symbol}
    _attach_valuation_guide(result)
    expected = query_sector_valuation_guide(sw_name)
    assert expected is not None
    assert result["valuation_guide"]["primary"] == expected["primary"]
    assert result["valuation_guide"]["industry"] == sw_name


def test_attach_valuation_guide_unmapped_symbol_no_key(monkeypatch):
    """未映射 symbol 不产出 valuation_guide 键（现状回归）。"""
    monkeypatch.setattr(
        "etf_data.ETF_TO_SW_INDUSTRY", {"000000": {"sw_name": "未知行业", "sub": ""}})
    result: dict = {"symbol": "000000"}
    _attach_valuation_guide(result)
    assert "valuation_guide" not in result
