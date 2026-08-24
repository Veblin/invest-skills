"""Tests for lib.chain — industry chain mapping and futures integration."""

from __future__ import annotations



class TestMatchChainKeyword:
    """Keyword matching with length-descending priority."""

    def test_exact_match(self):
        from lib.chain import _match_chain_keyword
        mapping = {"银行": "financial", "汽车": "auto"}
        assert _match_chain_keyword("银行", mapping) == "financial"

    def test_substring_match(self):
        from lib.chain import _match_chain_keyword
        mapping = {"汽车": "auto", "新能源": "energy"}
        assert _match_chain_keyword("汽车零部件", mapping) == "auto"

    def test_longest_match_priority(self):
        """新能源汽车 should match 新能源汽车, not 汽车 or 新能源."""
        from lib.chain import _match_chain_keyword
        mapping = {"新能源汽车": "new_energy_auto", "新能源": "energy", "汽车": "auto"}
        assert _match_chain_keyword("新能源汽车", mapping) == "new_energy_auto"

    def test_no_match_returns_none(self):
        from lib.chain import _match_chain_keyword
        mapping = {"银行": "financial"}
        assert _match_chain_keyword("纺织服装", mapping) is None

    def test_empty_industry(self):
        from lib.chain import _match_chain_keyword
        mapping = {"银行": "financial"}
        assert _match_chain_keyword("", mapping) is None


class TestGetFuturesForIndustry:
    """Industry-to-futures mapping."""

    def test_known_industry(self):
        from lib.chain import get_futures_for_industry
        result = get_futures_for_industry("化工")
        assert len(result) > 0
        assert all(isinstance(pair, tuple) and len(pair) == 2 for pair in result)

    def test_sub_industry_match(self):
        from lib.chain import get_futures_for_industry
        result = get_futures_for_industry("化工")  # direct match
        assert len(result) > 0

    def test_new_energy_auto_not_matched_as_auto(self):
        """新能源汽车 should get lithium/carbonate futures, not auto ones."""
        from lib.chain import get_futures_for_industry
        result = get_futures_for_industry("新能源汽车")
        # 新能源汽车 → 碳酸锂 + 工业硅
        symbols = [pair[1] for pair in result]
        assert "LC" in symbols or "SI" in symbols

    def test_unknown_industry(self):
        from lib.chain import get_futures_for_industry
        result = get_futures_for_industry("纺织服装")
        assert result == []


class TestCollectChainContext:
    """Chain context collection."""

    def test_with_explicit_industry(self):
        from lib.chain import collect_chain_context
        result = collect_chain_context("000001", industry="银行")
        assert result["status"] == "ok"
        assert result["chain_position"] == "金融"
        assert len(result["global_peers"]) > 0

    def test_with_basic_data(self):
        from lib.chain import collect_chain_context
        result = collect_chain_context(
            "000001", basic_data={"industry": "股份制银行"}
        )
        assert result["status"] == "ok"
        assert result["chain_position"] is not None

    def test_unknown_industry_graceful(self):
        from lib.chain import collect_chain_context
        result = collect_chain_context("000001", industry="未知行业XYZ")
        assert result["status"] == "ok"
        assert result["chain_position"] is None
        assert result["upstream"] == []
        assert result["downstream"] == []

    def test_no_industry_no_basic_data(self):
        """Without industry info, should attempt collector and handle gracefully."""
        from lib.chain import collect_chain_context
        result = collect_chain_context("600000")
        # May succeed or fail depending on network, but should always return a dict
        assert isinstance(result, dict)
        assert "status" in result
