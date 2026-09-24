"""公告类型映射（v0.3.1）——把交易所口径的「公告类型」接进事件分类。

背景：源（`akshare stock_individual_notice_report`）自带上百类结构化「公告类型」，
此前只被当作正则的第二段 haystack，词汇量约 20 条 → 实测近一年 122/178（**68.5%**）
落进 `other`。本模块的映射表把该字段接成一级分类，并**显式区分低信号两桶**以降噪。

实测（600176，近一年 178 条，2026-09-24 Python 复算）：实质 **80** / 程序性 **70** /
未分类 **28**。两桶语义不同、不得互相代称——`procedural` 是源**显式标注**的程序性类型，
源自带的兜底类型「其他」归 `other`（未分类）。
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_ROOT / "skills" / "invest-a-stock" / "scripts"))

from lib import events as E  # noqa: E402


def _cls(title: str, raw_type: str = "") -> str:
    return E._classify_event({"title": title, "raw_type": raw_type})["event_type"]


# ---------------------------------------------------------------- 循环闸门

def test_notice_type_map_targets_all_exist_in_taxonomy():
    """映射表的值必须都在 taxonomy 里——否则元数据会静默回落到 other 的默认值。"""
    taxonomy = E.load_event_taxonomy().get("event_types", {})
    for src_type, target in E._NOTICE_TYPE_MAP.items():
        assert target in taxonomy, f"{src_type!r} → {target!r} 不在 event_type_taxonomy.yaml"


def test_low_signal_types_are_declared_in_taxonomy():
    taxonomy = E.load_event_taxonomy().get("event_types", {})
    for t in E._LOW_SIGNAL_TYPES:
        assert t in taxonomy, f"低信号类型 {t!r} 缺 taxonomy 元数据"


def test_no_duplicate_keys_in_map():
    """字面重复的键在 dict 里会静默覆盖（实测已抓到一次「股权转让」重复）。

    用 AST 取字面量，不做源码文本切割——后者在任意重排（改注解/换行/闭合括号缩进）
    下会抛 IndexError，把一次无关的格式化变成假失败。
    """
    tree = ast.parse(Path(E.__file__).read_text(encoding="utf-8"))
    keys: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign) and getattr(node.target, "id", None) == "_NOTICE_TYPE_MAP":
            assert isinstance(node.value, ast.Dict), "_NOTICE_TYPE_MAP 须是字面量 dict"
            keys = [k.value for k in node.value.keys if isinstance(k, ast.Constant)]
    assert keys, "未找到 _NOTICE_TYPE_MAP 字面量"
    dupes = sorted({k for k in keys if keys.count(k) > 1})
    assert not dupes, f"映射表有重复键：{dupes}"


# ---------------------------------------------------------------- 漏判回归

def _regex_only(title: str, raw_type: str) -> str:
    """只跑正则、**不查映射表**的对照实现（用于证明用例真的依赖映射表）。"""
    for pattern, etype in E._CLASSIFICATION_RULES:
        if pattern.search(title):
            return etype
    return "other"


@pytest.mark.parametrize("title,raw_type,expected", [
    # 标题中性但类型明确——这些才是映射表**独占**覆盖的场景
    ("XX公司关于签订战略合作协议的公告", "签订协议", "major_contract"),
    ("XX公司2025年度分配方案实施公告", "分配方案实施", "dividend"),
    ("XX公司关于股东部分股份质押的公告", "股份质押、冻结", "pledge"),
    ("XX公司关于限售股份上市流通的公告", "限售股份上市流通", "unlock"),
    ("XX公司关于收到警示函的公告", "警示函公告", "regulatory"),
    ("XX公司股票交易异常波动公告", "股票交易异常波动", "market_anomaly"),
    ("XX公司关于对外投资的公告", "对外项目投资", "investment"),
    ("XX公司关于为子公司提供担保的公告", "提供/对外担保公告", "guarantee"),
    ("XX公司简式权益变动报告书", "权益变动报告书", "holder_change"),
    ("XX公司关于关联交易的公告", "关联交易", "related_party"),
])
def test_types_only_the_map_can_classify(title, raw_type, expected):
    assert _cls(title, raw_type) == expected


@pytest.mark.parametrize("raw_type", [
    "签订协议", "分配方案实施", "股份质押、冻结", "限售股份上市流通",
    "警示函公告", "股票交易异常波动", "对外项目投资",
    "提供/对外担保公告", "权益变动报告书", "关联交易",
])
def test_map_cases_are_not_already_covered_by_regex(raw_type):
    """**防假信心守卫**：上表的每一行都必须靠映射表才能判对。

    反例（曾经写进本测试、现已移除）：`一季度报告全文`、`分配预案` —— 标题里的
    「季度报告」「利润分配」本就命中正则，删掉映射表这两行照样通过，不能证明任何事。
    """
    title = "XX公司某某事项公告"
    assert _regex_only(title, raw_type) == "other", (
        f"{raw_type!r} 在标题中性时已被正则命中 —— 该用例不能证明映射表的作用"
    )
    assert _cls(title, raw_type) != "other"


# ---------------------------------------------------------------- 优先级

def test_source_type_wins_when_specific():
    """源类型是实质类型时优先——即便标题里没有对应关键词。"""
    assert _cls("XX公司公告", "回购进展情况") == "buyback"
    assert _cls("XX公司公告", "股权激励计划") == "equity_incentive"


@pytest.mark.parametrize("coarse_type", ["其他", "专项说明/独立意见", "调研活动"])
def test_coarse_source_type_does_not_demote_title_derived_signal(coarse_type):
    """源类型比标题更粗时**不得**降级。

    实测回归：早期实现让源类型无条件优先，近一年有 19 条本已被正则判对的事件
    （equity_incentive 12 / earnings_report 4 / buyback 2 / holder_increase 1）
    被降成程序性。
    """
    assert _cls("XX公司关于股份回购实施结果的公告", coarse_type) == "buyback"
    assert _cls("XX公司2025年年度报告", coarse_type) == "earnings_report"
    assert _cls("XX公司关于股东增持股份的公告", coarse_type) == "holder_increase"


def test_falls_back_to_procedural_only_when_source_says_so():
    """程序性只在源明确标注、且标题也无实质关键词时才落。"""
    assert _cls("XX公司关于召开股东大会的通知", "召开股东大会通知") == "procedural"
    # 源标程序性但标题有实质事件 → 取实质
    assert _cls("XX公司关于股份回购的公告", "召开股东大会通知") == "buyback"


def test_unknown_type_still_falls_back_to_other():
    """既非映射、又非正则、源也没标程序性 → 仍是 other（不臆测）。"""
    assert _cls("XX公司某事项", "前所未有的新类型") == "other"


# ---------------------------------------------------------------- 降噪

def test_summary_signal_ranking_excludes_low_signal():
    """因子矩阵那行只取前 3——程序性公告不得占据信号榜。"""
    events = [
        {"date": "2026-09-01", "type": "procedural"} for _ in range(50)
    ] + [{"date": "2026-09-01", "type": "buyback"}]

    s = E._build_summary(events, 30)
    types = [t["type"] for t in s["top_types"]]
    assert "procedural" not in types and "other" not in types
    assert types[0] == "buyback"
    assert s["low_signal_count"] == 50, "低信号数量仍须可追溯"
    assert s["procedural_count"] == 50
    assert s["unclassified_count"] == 0
    assert s["event_count"] == 51


@pytest.mark.parametrize("types,expected", [
    (["other", "other"], "未分类公告 2 条"),
    (["procedural", "procedural"], "程序性公告 2 条"),
    (["other", "procedural"], "程序性公告 1 条、未分类公告 1 条"),
])
def test_low_signal_summary_and_report_keep_classes_distinct(types, expected):
    from lib.render_markdown._v3 import _section_dynamic_drivers

    events = [{"date": "2026-09-01", "type": t, "title": "公告"} for t in types]
    summary = E._build_summary(events, 30)
    assert summary["procedural_count"] == types.count("procedural")
    assert summary["unclassified_count"] == types.count("other")
    assert summary["low_signal_count"] == len(types)

    report = _section_dynamic_drivers(
        {"events": events, "_meta": {"events_summary": summary}}, "600176", {}, {},
    )
    event_row = next(line for line in report.splitlines() if "| 事件催化 |" in line)
    assert expected in event_row
    if "other" in types:
        assert "均为程序性公告" not in event_row


def test_legacy_summary_uses_event_types_instead_of_combined_count():
    from lib.render_markdown._v3 import _section_dynamic_drivers

    events = [{"date": "2026-09-01", "type": "other", "title": "公告"}]
    report = _section_dynamic_drivers(
        {"events": events, "_meta": {"events_summary": {
            "window_days": 30, "top_types": [], "low_signal_count": 1,
        }}}, "600176", {}, {},
    )
    event_row = next(line for line in report.splitlines() if "| 事件催化 |" in line)
    assert "未分类公告 1 条" in event_row
