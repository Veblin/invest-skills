"""Tests for evidence table generation (R-07)."""
import json
import pytest
from lib.evidence import (
    EvidenceRow,
    build_evidence_table,
    render_evidence_table,
    _format_value,
    _confidence_label,
)


class TestFormatValue:
    def test_none(self):
        assert _format_value(None) == "无数据"

    def test_float(self):
        assert "15.50" in _format_value(15.5)

    def test_int(self):
        assert _format_value(42) == "42"

    def test_dict_with_pe(self):
        assert "pe_ttm=15.50" in _format_value({"pe_ttm": 15.5})

    def test_dict_with_name(self):
        result = _format_value({"name": "测试公司", "industry": "制造业"})
        assert "name=测试公司" in result
        assert "industry=制造业" in result

    def test_list(self):
        assert "3条记录" in _format_value([1, 2, 3])


class TestConfidenceLabel:
    def test_high(self):
        assert "高" in _confidence_label("high")

    def test_medium(self):
        assert "中" in _confidence_label("medium")

    def test_low(self):
        assert "低" in _confidence_label("low")


class TestEvidenceRow:
    def test_create(self):
        row = EvidenceRow(
            dimension="估值",
            channel="tushare.daily_basic",
            value_summary="PE=15.50",
            confidence="🟢 高",
            source_count=2,
            cross_validation="✅ 一致",
        )
        assert row.dimension == "估值"
        assert row.source_count == 2


class TestBuildEvidenceTable:
    def test_empty_dimensions(self):
        rows = build_evidence_table([])
        assert rows == []

    def test_single_dimension_with_sources(self):
        dims = [{
            "dimension": "valuation",
            "display": "估值分析",
            "data": {"pe_ttm": 15.5},
            "_meta": {
                "source": "tushare.daily_basic",
                "confidence": "high",
                "all_sources": [
                    {"source": "tushare.daily_basic", "data_available": True, "confidence": "high"},
                    {"source": "tencent_finance", "data_available": True, "confidence": "medium"},
                ],
                "cross_validation": "convergence",
            },
        }]
        rows = build_evidence_table(dims)
        assert len(rows) == 2  # two sources
        assert rows[0].source_count == 2
        assert "一致" in rows[0].cross_validation

    def test_dimension_with_failed_source(self):
        dims = [{
            "dimension": "kline",
            "display": "日K线",
            "data": [{"close": 100.0}],
            "_meta": {
                "source": "baostock.kline",
                "confidence": "medium",
                "all_sources": [
                    {"source": "tushare.daily", "data_available": True, "confidence": "high"},
                    {"source": "akshare.stock_zh_a_hist", "data_available": False, "confidence": "low",
                     "error": "Connection refused"},
                ],
            },
        }]
        rows = build_evidence_table(dims)
        assert len(rows) == 2
        # One success, one failure
        assert any("❌" in r.value_summary for r in rows)
        assert any("Connection refused" in r.value_summary for r in rows)

    def test_no_all_sources(self):
        dims = [{
            "dimension": "quote",
            "display": "实时行情",
            "data": {"price": 50.0},
            "_meta": {
                "source": "tencent_finance",
                "confidence": "medium",
            },
        }]
        rows = build_evidence_table(dims)
        assert len(rows) == 1
        assert "单源" in rows[0].cross_validation


class TestRenderEvidenceTable:
    def test_md_format(self):
        rows = [
            EvidenceRow("估值分析", "tushare.daily_basic", "PE=15.50",
                        "🟢 高", 2, "✅ 一致"),
        ]
        output = render_evidence_table(rows, "md")
        assert "证据表" in output
        assert "| 维度 | 渠道 |" in output
        assert "tushare.daily_basic" in output

    def test_json_format(self):
        rows = [
            EvidenceRow("估值分析", "tushare.daily_basic", "PE=15.50",
                        "🟢 高", 2, "✅ 一致"),
        ]
        output = render_evidence_table(rows, "json")
        data = json.loads(output)
        assert len(data) == 1
        assert data[0]["dimension"] == "估值分析"

    def test_empty_rows(self):
        assert "证据表" in render_evidence_table([], "md")
        assert json.loads(render_evidence_table([], "json")) == []
