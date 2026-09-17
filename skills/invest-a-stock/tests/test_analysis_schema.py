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


# ── v0.3.1 #4：facts 绑定校验 ────────────────────────────────────────────────
# 缺陷背景：此前 analysis.json 无 fact_id 字段，段内数字未经任何来源校验——
# Claude 写的任意数字可直接进入最终 MD/HTML（SKILL.md 自认）。本组测试锁定
# 新增的**可选** facts 契约：无 facts 向后兼容；有 facts 则公式必须复算得上。

def _with_facts(facts, facts_md=None, analysis_md=None):
    sec = _valid()[0]
    if facts is not None:
        sec["facts"] = facts
    if facts_md is not None:
        sec["facts_md"] = facts_md
    if analysis_md is not None:
        sec["analysis_md"] = analysis_md
    return [sec]


class TestFactsBinding:

    def test_no_facts_still_valid(self):
        """向后兼容：不声明 facts 的段照常通过（存量报告不受影响）。"""
        assert validate_sections(_valid()) == []

    def test_well_formed_facts_pass(self):
        assert validate_sections(_with_facts([
            {"id": "F1", "value": -36.7, "formula": "(16.6297/26.2793-1)*100"},
            {"id": "F2", "value": 52.83, "formula": "8.02-(-44.81)"},
            {"id": "F3", "value": 3, "formula": "3"},
            {"id": "F4", "value": 1, "formula": "1"},
            {"id": "F5", "value": 18, "formula": "18"},
        ])) == []

    def test_unit_mismatch_ratio_vs_percent_is_caught(self):
        """单位错配也是「公式算不出这个数」——比值 vs 百分数必须一致。"""
        errs = validate_sections(_with_facts([
            {"id": "F1", "value": -36.7, "formula": "16.6297/26.2793-1"},
        ]))
        assert any("复算" in e for e in errs), errs

    def test_formula_not_matching_value_is_error(self):
        """核心 P0 检查：标了公式但公式算不出这个数 → 未实跑标注，拦截。"""
        errs = validate_sections(_with_facts([
            {"id": "F1", "value": -30.0, "formula": "16.6297/26.2793-1"},
        ]))
        assert any("复算" in e and "≠" in e for e in errs), errs

    def test_formula_unevaluable_is_error(self):
        errs = validate_sections(_with_facts([
            {"id": "F1", "value": 1.0, "formula": "调用外部接口()"},
        ]))
        assert any("不可求值" in e for e in errs), errs

    def test_formula_division_by_zero_is_error(self):
        errs = validate_sections(_with_facts([
            {"id": "F1", "value": 1.0, "formula": "1/0"},
        ]))
        assert any("除零" in e for e in errs), errs

    def test_formula_overflow_is_validation_error(self):
        """有效语法的数值溢出不得让 validate_sections 崩溃。"""
        errs = validate_sections(_with_facts([
            {"id": "F1", "value": 1.0, "formula": "1e308 ** 2"},
        ]))
        assert any("溢出" in e for e in errs), errs

    def test_formula_dunder_escape_is_rejected(self):
        """ast 白名单：属性访问/调用/下标一律拒绝（不是 eval）。"""
        errs = validate_sections(_with_facts([
            {"id": "F1", "value": 1.0, "formula": "__import__('os').getcwd()"},
        ]))
        assert any("不可求值" in e for e in errs), errs

    def test_value_must_be_numeric(self):
        errs = validate_sections(_with_facts([
            {"id": "F1", "value": "约 36%", "formula": "16.6297/26.2793-1"},
        ]))
        assert any("value 必为数值" in e for e in errs), errs

    def test_duplicate_and_missing_id_are_errors(self):
        errs = validate_sections(_with_facts([
            {"id": "F1", "value": 1.0, "formula": "1.0"},
            {"id": "F1", "value": 2.0, "formula": "2.0"},
            {"value": 3.0, "formula": "3.0"},
        ]))
        assert any("id 重复" in e for e in errs) and any("缺 id" in e for e in errs)

    def test_facts_must_be_list(self):
        errs = validate_sections(_with_facts({"id": "F1"}))
        assert any("必须为数组" in e for e in errs)

    def test_dangling_fact_reference_is_error(self):
        errs = validate_sections(_with_facts(
            [{"id": "F1", "value": 1.0, "formula": "1.0"}],
            analysis_md="见 [[F2]] 的推导。（证据 B）",
        ))
        assert any("[[F2]]" in e for e in errs), errs

    def test_resolvable_fact_reference_passes(self):
        errs = validate_sections(_with_facts(
            [{"id": "F1", "value": 1.0, "formula": "1.0"}],
            facts_md="事实 [来源: engine]",
            analysis_md="见 [[F1]] 的推导。（证据 B）",
        ))
        assert errs == [], errs

    def test_unbound_ordinary_number_is_error(self):
        """有一个合法 F1 也不能让正文里的 999% 绕过绑定。"""
        errs = validate_sections(_with_facts(
            [{"id": "F1", "value": 1.0, "formula": "1.0"},
             {"id": "F2", "value": 3, "formula": "3"},
             {"id": "F3", "value": 18, "formula": "18"}],
            analysis_md="2026 年增长 999%。（证据 B）",
        ))
        assert any("999" in e and "未绑定" in e for e in errs), errs

    def test_bound_ordinary_number_passes(self):
        errs = validate_sections(_with_facts(
            [{"id": "F1", "value": 1.0, "formula": "1.0"},
             {"id": "F2", "value": 3, "formula": "3"},
             {"id": "F3", "value": 18, "formula": "18"}],
            analysis_md="增长 18%。见 [[F1]]。（证据 B）",
        ))
        assert errs == [], errs

    def test_bracket_text_without_facts_is_not_checked(self):
        """未声明 facts 的段不校验 [[..]]（可能是普通文本，向后兼容优先）。"""
        errs = validate_sections(_with_facts(None, analysis_md="见 [[F9]]。（证据 B）"))
        assert errs == [], errs

    def test_validation_does_not_mutate_section(self):
        """D7：校验不得写回传入的段字典（来自共享的 load_analysis_json）。"""
        sec = _with_facts([{"id": "F1", "value": 1.0, "formula": "1.0"}])
        validate_sections(sec)
        assert "_fact_ids" not in sec[0]


# _valid() 自带的 facts_md 为「近 30 日公告 3 条：回购公告 1 条…」——其中 "30"
# 由「近…日」结构性豁免，"1"/"3" 须由 facts 覆盖；否则报错来自被继承的 facts_md
# 而非本用例的 analysis_md（既有用例同样以 F1/F2 覆盖）。
_BASE_FACTS = [{"id": "F1", "value": 1.0, "formula": "1.0"},
               {"id": "F2", "value": 3, "formula": "3"}]


class TestStructuralNumberExemption:
    """v0.3.0 A1/D2：结构性数字豁免族。

    缺陷三连（同一函数）：
      ① `len(token)==4` 分支直接 `int(token)` → 4 字符小数（`12.5`/`36.7`）
         抛未捕获 ValueError —— invest.py 只捕 AnalysisSchemaError → traceback；
         共享 report_qc 的 `except Exception` 转成 error 级
         `completion-analysis-sidecar-invalid` → **合格报告 exit 2 不得交付**。
      ② ISO 日期（`2026-09-17`）的首/尾片段报「未绑定」。
      ③ 版本号（`v0.3.1`）末位片段报「未绑定」。
      ④ 千分位（`12,345`）被切成 `12`/`345`，两侧都匹配不上事实值。
    """

    def test_four_char_decimal_does_not_raise(self):
        """① 不得抛异常（此前的崩溃点）。"""
        errs = validate_sections(_with_facts(
            _BASE_FACTS,
            analysis_md="PE 12.5 倍，处于常态。（证据 B）",
        ))
        assert isinstance(errs, list)

    def test_four_char_decimal_still_must_be_bound(self):
        """① 修的是崩溃，不是放宽——无对应事实值时仍须报未绑定。"""
        errs = validate_sections(_with_facts(
            _BASE_FACTS,
            analysis_md="PE 12.5 倍，处于常态。（证据 B）",
        ))
        assert any("12.5" in e and "未绑定" in e for e in errs), errs

    def test_iso_date_fully_exempt(self):
        """② 年/月/日三段均不再报未绑定。"""
        errs = validate_sections(_with_facts(
            _BASE_FACTS,
            analysis_md="截至 2026-09-17 披露完毕。（证据 B）",
        ))
        assert errs == [], errs

    def test_version_string_fully_exempt(self):
        """③ 含末位补丁号。"""
        errs = validate_sections(_with_facts(
            _BASE_FACTS,
            analysis_md="口径见 v0.3.1 规范。（证据 B）",
        ))
        assert errs == [], errs

    def test_thousand_separator_binds_to_fact(self):
        """④ 千分位是**一个**量值 token → 照常绑定（而非被豁免）。"""
        errs = validate_sections(_with_facts(
            _BASE_FACTS + [{"id": "F3", "value": 12345, "formula": "12345"}],
            analysis_md="成交额 12,345 万元。（证据 B）",
        ))
        assert errs == [], errs

    def test_thousand_separator_unbound_is_error(self):
        """④ 绑定语义未被放宽：无对应事实值仍报未绑定（且报整段而非碎片）。"""
        errs = validate_sections(_with_facts(
            _BASE_FACTS,
            analysis_md="成交额 12,345 万元。（证据 B）",
        ))
        assert any("12,345" in e and "未绑定" in e for e in errs), errs

    def test_numeric_range_is_not_masked(self):
        """掩码只吃日期/版本号形态——`9-10 倍` 这类数值区间仍须绑定。"""
        errs = validate_sections(_with_facts(
            _BASE_FACTS,
            analysis_md="增长 9-10 倍。（证据 B）",
        ))
        assert any("未绑定" in e for e in errs), errs

    def test_negative_decimal_not_exempt(self):
        """`-36.7%` 的 token 是 `36.7`（正则不含负号）→ 不得被豁免。"""
        errs = validate_sections(_with_facts(
            _BASE_FACTS,
            analysis_md="回撤 -36.7%。（证据 B）",
        ))
        assert any("36.7" in e and "未绑定" in e for e in errs), errs
