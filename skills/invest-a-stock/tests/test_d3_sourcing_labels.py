"""P0-3：D-③ 派生值来源标注回归（真实渲染器 + 真实 QC 规则）。

`_section_4d_valuation_expectation` 此前整个函数零 ``[来源:]`` 标签，其中
``约 X%`` 形态的行被 report_qc 的 F2 规则判为「派生表述缺来源」（warn）。
F2 的放行窗口是 ``_F2_SOURCE_WINDOW`` 个**物理行**（report_qc.py），因此
本文件用端到端回归（渲染 → `_check_sourcing`）而非逐行字符串断言：
只要有人插入行把标签挤出窗口，或增删标签，findings_count 就会变。

基线（修复前）实测 warn(2)，与真实报告
``reports/300750-宁德时代/2026-09-14-13-41-24.md`` 的 sourcing 层一致。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))
_SHARED_LIB_DIR = _SCRIPTS_DIR.parent.parent / "lib"
if str(_SHARED_LIB_DIR) not in sys.path:
    sys.path.insert(0, str(_SHARED_LIB_DIR))

from fixtures.collections import collection_v2_minimal  # noqa: E402
from lib.render import render_report_v3  # noqa: E402
from report_qc import _F2_PATTERN, _check_sourcing  # noqa: E402


def _render_offline(collection: dict) -> str:
    """真实渲染器产出 full 报告（离线；HS300 基准取数打桩，避免 socket 超时）。

    render_dcf 的 beta 计算会 import 并调用该取数函数；打桩为空序列即走
    「HS300 基准数据不可得」降级分支，结果与离线默认值一致且无网络等待。
    """
    from lib import collector

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(collector, "_akshare_hs300_dated_closes", lambda **_kw: [])
        return render_report_v3(collection, "600176", mode="full")


@pytest.fixture(scope="module")
def full_render() -> str:
    """无可比 CAGR 的 collection → 命中 d3_pitfall 第二分支。"""
    return _render_offline(collection_v2_minimal())


@pytest.fixture(scope="module")
def full_render_with_cagr() -> str:
    """含可比 CAGR 的 collection → 命中 d3_pitfall 第一分支（带 CAGR 的措辞）。"""
    from test_v013_phase2 import _collection_phase2

    return _render_offline(_collection_phase2())


def test_full_report_has_no_unsourced_derived_claims(full_render: str) -> None:
    """主回归：full 模式报告的 F2 告警归零。"""
    layer = _check_sourcing(full_render)
    assert layer.findings_count == 0, [
        (d["id"], d["line"], d["message"]) for d in layer.details
    ]
    assert layer.status == "pass"


def _f2_lines(full_render: str, needle: str) -> list[str]:
    return [ln for ln in full_render.splitlines() if needle in ln and _F2_PATTERN.search(ln)]


def test_g_implied_line_is_tagged(full_render: str) -> None:
    """防漂移：被标注的必须正是 F2 会命中的那一行。

    只断言 findings_count == 0 不够——标签若加错行，主回归可能偶然通过。
    这里同时锁定「该行确实命中 F2」与「该行自身带来源标签」。
    """
    hits = _f2_lines(full_render, "市场隐含增长率 g_implied")
    assert hits, "预期存在命中 F2 的 g_implied 主行；渲染器文案可能已变更"
    for line in hits:
        assert "[来源" in line, line


@pytest.mark.parametrize("fixture_name", ["full_render", "full_render_with_cagr"])
def test_d3_pitfall_blockquote_is_tagged(fixture_name: str, request) -> None:
    """`_law10_hint` 渲染的误区行是独立 blockquote，3 行窗口够不到上方标签。

    该行必须在自身行内带来源标签，否则一旦上方排版变动就会重新告警。
    两个分支（缺 CAGR / 有 CAGR）措辞不同，各自都要标注——只测一条会漏掉
    另一条的拼接错误。
    """
    text = request.getfixturevalue(fixture_name)
    hits = _f2_lines(text, "常见分析误区")
    assert hits, f"{fixture_name}: 预期存在命中 F2 的分析误区行；渲染器文案可能已变更"
    for line in hits:
        assert line.lstrip().startswith(">"), line
        assert "[来源" in line, line


def test_both_pitfall_branches_are_exercised(full_render: str, full_render_with_cagr: str) -> None:
    """守住上面参数化的前提：两份 fixture 确实落在不同分支上。"""
    assert "但缺少可比 CAGR" in full_render
    assert "但缺少可比 CAGR" not in full_render_with_cagr
    assert "营收 CAGR" in full_render_with_cagr
