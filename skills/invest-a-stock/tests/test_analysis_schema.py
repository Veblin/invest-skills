"""analysis.json schema 校验（R-B1）。"""
from __future__ import annotations

import pytest

from lib.analysis_schema import AnalysisSchemaError, load_analysis_json, validate_sections


def _valid():
    return [{
        "module": "events",
        "title": "事件分层分析",
        "facts_md": "近 30 日公告 3 条：回购公告 1 条、收购终止 [来源: akshare 公告]",
        "analysis_md": "回购与收购终止并行，体现管理层资金安排分歧；**观察**：回购成交价上限距现价 18%。（证据 B）",
        "evidence_tag": "B",
        "position": "events",
    }]


def test_valid_passes():
    assert validate_sections(_valid()) == []


def test_required_field_missing():
    bad = [d for d in _valid()]
    del bad[0]["analysis_md"]
    assert any("analysis_md" in e for e in validate_sections(bad))


def test_evidence_tag_pattern():
    bad = _valid()
    bad[0]["evidence_tag"] = "如上所述"
    assert any("evidence_tag" in e for e in validate_sections(bad))


@pytest.mark.parametrize("bad_md", ["```python\nx=1\n```", "![图](x.png)", "###### 六级"])
def test_markdown_subset_rejected(bad_md):
    bad = _valid()
    bad[0]["analysis_md"] = bad_md
    assert any("markdown" in e for e in validate_sections(bad))


def test_load_missing_file():
    with pytest.raises(AnalysisSchemaError):
        load_analysis_json("/tmp/does-not-exist-028.json")
