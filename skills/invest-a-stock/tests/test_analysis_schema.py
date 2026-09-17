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


class TestStripRenderState:
    """review C4：渲染期登记键（id() 堆地址）不得落进 collections.raw_json。"""

    def test_strip_removes_key_without_mutating_caller(self):
        from lib.analysis_schema import (CONSUMED_IDS_KEY, mark_inline_consumed,
                                         strip_render_state)

        c: dict = {}
        mark_inline_consumed(c, {"module": "risk", "title": "t", "position": "conclusion"})
        assert CONSUMED_IDS_KEY in c

        out = strip_render_state(c)
        assert CONSUMED_IDS_KEY not in out
        assert CONSUMED_IDS_KEY in c, "不得就地修改调用方 collection（渲染仍要用）"

    def test_identical_data_serializes_identically_after_strip(self):
        """同一份数据的两次采集 → 剥离后 raw_json 必须逐字节相同。

        前置断言（未剥离时确实不同）复现的是实测缺陷：report 行 116-119 各带
        一组互不相同的堆地址。段对象须保活，否则地址可能被分配器复用。
        """
        from lib.analysis_schema import mark_inline_consumed, strip_render_state
        from lib.json_util import dumps_json

        def _collect():
            c = {"symbol": "600176", "dimensions": []}
            sec = {"module": "risk", "title": "t", "position": "conclusion"}
            mark_inline_consumed(c, sec)
            return c, sec  # 保活 sec，避免 id 复用

        a, sec_a = _collect()
        b, sec_b = _collect()
        assert id(sec_a) != id(sec_b)

        assert dumps_json(a) != dumps_json(b), "前置：未剥离时确实每次不同"
        assert dumps_json(strip_render_state(a)) == dumps_json(strip_render_state(b))
