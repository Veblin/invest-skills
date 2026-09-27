"""v0.3.1 A4：默认报告（full）主阅读面与审计底稿的分区回归。

用户裁决形态「单文件双段式」：主阅读面只留判断链路（报告说明 → 重要发现/overview
判断句 → 校验警示），九模块 / 12 题 / DCF / Bull-Bear / 技术读数 / 引擎自检 /
分析详情收进**单层** `<details>` 审计底稿。

边界约定：`md.index("<details>")` —— 该行之前为主阅读面，之后为审计底稿。
折叠对 lint 与 report_qc 透明（两者都按行首 `^## ` 工作），故本节断言不依赖
任何渲染函数重写；篇幅口径由 `report_qc._body_lines` 跳过折叠跨度配套。

对齐的人工标尺：`reports/300750-宁德时代/样稿-20260924/04-样稿-读者版.md`
（主阅读面 2,898 汉字 / 6 个 H2 / 零 H3）。该文件属个股产出、在 reports/ 下
被 gitignore，**不得在本测试中引用**，故标尺以常量 + 注释形式固化。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))
_SHARED_LIB_DIR = _SCRIPTS_DIR.parent.parent / "lib"
if str(_SHARED_LIB_DIR) not in sys.path:
    sys.path.insert(0, str(_SHARED_LIB_DIR))

from fixtures.collections import collection_v2_minimal  # noqa: E402
from lib.render_markdown._concise import render_report_v3  # noqa: E402
from lib.analysis_schema import validate_sections  # noqa: E402
from report_qc import readability_metrics  # noqa: E402

_SYMBOL = "600176"
# 人工样稿主阅读面 2,898 汉字 → 上限取 3,000（含 H2 标题与表格行）
_READING_SURFACE_MAX_CJK = 3000
# 底稿必须保留的节（防「折叠时顺手删」）
_BASEMENT_REQUIRED = (
    "## 目录",
    "## 0.",
    "## 4.",
    "## D. ",
    "## 5.",
    "## 8.",
    "## 附录：数据质量与引擎自检",
)
# 主阅读面不得出现的底稿节
_SURFACE_FORBIDDEN = (
    "## 目录",
    "## 0.",
    "## 1.",
    "## 2.",
    "## 3.",
    "## 4.",
    "## 5.",
    "## 6.",
    "## 7.",
    "## 8.",
    "## D. ",
    "## 参与者行为扫描",
    "## 分析详情",
)


@pytest.fixture(autouse=True)
def _offline_render(monkeypatch: Any) -> None:
    """render_dcf 的 beta 会拉沪深300 基准；打桩走降级分支（同 P0-4 回归）。

    不打桩则每次渲染多一次 socket 超时，离线 CI 上表现为秒级抖动。
    """
    from lib import collector

    monkeypatch.setattr(collector, "_akshare_hs300_dated_closes", lambda **_kw: [])


def _reader_collection() -> dict[str, Any]:
    """最小 collection + 让四个条件宿主与自检附录全部渲染。

    fusion / credibility 是「附录：数据质量与引擎自检」的渲染条件（两者与
    macro_context 皆空时该附录整体返回空串）——底稿完整性断言需要它存在。
    """
    coll = collection_v2_minimal()
    coll["market_structure"] = {
        "moneyflow": {"net_sum_5d": 1.5e8, "source": "test.fixture"},
    }
    coll["events"] = [
        {"date": "2026-06-11", "type": "buyback", "impact_dimension": "估值",
         "duration": "中长期变量", "title": "测试股份:关于回购公司A股股份的公告"},
    ]
    coll["fusion"] = {
        "financials": {"consensus": "strong", "fused_value": 12.34,
                       "source_values": {"tushare": 12.3, "akshare": 12.4}},
    }
    coll["credibility"] = {"financials": 88}
    coll.setdefault("_meta", {})["analysis_cards"] = {
        "event_classifications": [
            {"event_type": "buyback", "event_label": "回购",
             "events": [{"date": "2026-06-11"}]},
        ],
    }
    return coll


def _sec(module: str, position: str, title: str, marker: str) -> dict:
    return {"module": module, "position": position, "title": title,
            "facts_md": f"事实 {marker} [来源: engine]",
            "analysis_md": f"分析 {marker}", "evidence_tag": "B"}


def _reader_analysis() -> list[dict]:
    """四段主阅读面（overview）+ 四个就地槽位 + 一段非槽位（进分析详情）。"""
    return [
        _sec("overview", "overview",
             "盈利兑现与估值收缩同时发生，本次要解释的是两者谁先拐", "标记OVW1"),
        _sec("overview", "overview", "支撑判断的三项证据及其期间", "标记OVW2"),
        _sec("overview", "overview", "竞争解释：份额失守与成本上行尚未被数据区分", "标记OVW3"),
        _sec("overview", "overview", "下一步最值得核验的披露项", "标记OVW4"),
        _sec("participant_scan", "analysis", "参与方方向不一致", "标记PS"),
        _sec("event_classification", "analysis", "本期事件以程序性文件为主", "标记EC"),
        _sec("mda_narrative", "analysis", "管理层论述与报表方向一致", "标记MDA"),
        _sec("bear_chain", "analysis", "空头链依赖单价假设", "标记BEAR"),
        _sec("valuation", "valuation", "估值方法的取舍与局限", "标记DETAIL"),
    ]


def _main_surface(md: str) -> str:
    return md[: md.index("<details>")] if "<details>" in md else md


def _cjk(text: str) -> int:
    return len(re.findall(r"[一-鿿]", text))


@pytest.fixture
def reader_md(_offline_render: None) -> str:
    return render_report_v3(_reader_collection(), _SYMBOL, mode="full",
                            analysis=_reader_analysis())


def test_main_surface_within_reading_budget(reader_md: str):
    """主阅读面汉字数 ≤ 3,000，且非空（防空表面对标尺空转通过）。"""
    surface = _main_surface(reader_md)
    n = _cjk(surface)
    assert n >= 200, f"主阅读面过短（{n} 汉字），夹具或装配可能失效"
    assert n <= _READING_SURFACE_MAX_CJK, (
        f"主阅读面 {n} 汉字超预算 {_READING_SURFACE_MAX_CJK}（样稿标尺 2,898）"
    )


def test_main_surface_excludes_basement_sections(reader_md: str):
    surface = _main_surface(reader_md)
    leaked = [h for h in _SURFACE_FORBIDDEN if h in surface]
    assert not leaked, f"底稿节泄漏进主阅读面：{leaked}"


def test_basement_keeps_audit_sections(reader_md: str):
    """底稿能力不得因折叠而丢失（反向守卫）。"""
    boundary = reader_md.index("<details>")
    for head in _BASEMENT_REQUIRED:
        assert head in reader_md, f"底稿节缺失：{head}"
        assert reader_md.index(head) > boundary, f"{head} 未收进折叠区"
        assert reader_md.index(head) < reader_md.rindex("</details>"), f"{head} 落在折外"
    assert reader_md.index("## 📚 引用来源") > reader_md.rindex("</details>")
    assert reader_md.index("免责声明", reader_md.rindex("</details>")) > reader_md.rindex("</details>")


def test_basement_is_single_disclosure_level(reader_md: str):
    """全篇只有审计底稿一层折叠：折内不得再套 `<details>`。

    评审 P2：12 题 / 风险与不确定性 / 引擎自动空头链原先各自折叠，套进底稿层
    后变成二级套娃——展开底稿仍看不到它们。
    """
    assert reader_md.count("<details>") == 1, "折内出现二级折叠"
    assert reader_md.count("</details>") == 1
    assert "<summary>审计底稿" in reader_md


def test_each_analysis_section_rendered_once(reader_md: str):
    """同一结论只说一次（逐段 [分析] 唯一）。

    就地槽位（participant_scan / bear_chain）由宿主自带 [事实] 表，段自身的
    `facts_md` 不渲染，故这些段只锁 `analysis_md`。
    """
    for marker in ("标记OVW1", "标记OVW2", "标记OVW3", "标记OVW4",
                   "标记PS", "标记MDA", "标记BEAR", "标记DETAIL"):
        token = f"分析 {marker}"
        assert reader_md.count(token) == 1, (
            f"{token} 出现 {reader_md.count(token)} 次（同一结论被重写）"
        )
    # overview 段与尾部注记段的 [事实] 由渲染器直接出，同样只许一次
    for marker in ("标记OVW1", "标记OVW2", "标记OVW3", "标记OVW4",
                   "标记MDA", "标记DETAIL"):
        token = f"事实 {marker}"
        assert reader_md.count(token) == 1, (
            f"{token} 出现 {reader_md.count(token)} 次（同一事实被重写）"
        )
    # 已知遗留（重复面 7，2026-09-27 裁决「跳过」）：`event_classification`
    # 有两个宿主（§3a 事件时间线 + 管理层决策时间线单元格），同一段渲染两次。
    # 这里**显式锁定现状**而非静默放过：修掉该重复后本断言应改为 == 1 并删本注释。
    assert reader_md.count("分析 标记EC") == 2, (
        "event_classification 宿主数量变化——需重审重复面 7 的裁决"
    )


def test_overview_headings_are_h2_judgments(reader_md: str):
    """主阅读面由 H2 判断句构成（对齐样稿：6 个 H2 / 零 H3）。"""
    surface = _main_surface(reader_md)
    assert "## 重要发现（5 分钟阅读区）" in surface
    for title in ("盈利兑现与估值收缩同时发生，本次要解释的是两者谁先拐",
                  "支撑判断的三项证据及其期间"):
        assert f"## {title}" in surface
    overview = surface[surface.index("## 重要发现（5 分钟阅读区）"):]
    assert "### " not in overview, "重要发现区应由 H2 判断句组成"


def test_readability_length_excludes_basement(reader_md: str):
    """篇幅口径：折叠区不计阅读篇幅，故默认 full 报告不因底稿长度被标超限。"""
    met = readability_metrics(reader_md)
    unfolded = reader_md.replace("<summary>审计底稿", "<summary>展开底稿", 1)
    assert readability_metrics(unfolded)["total_chars"] - met["total_chars"] > 5000, (
        "折叠前后计数差异过小，底稿可能并未从阅读篇幅中剔除"
    )
    assert met["total_chars"] < 20_000, (
        f"正文篇幅 {met['total_chars']} 仍超限——折叠跨度未从阅读篇幅中剔除"
    )


def test_analysis_cannot_close_basement_early():
    analysis = _reader_analysis()
    analysis[4]["analysis_md"] += "\n</details>\n边界探针"
    assert validate_sections(analysis) == []
    md = render_report_v3(_reader_collection(), _SYMBOL, mode="full", analysis=analysis)
    assert md.count("<details>") == md.count("</details>") == 1
    assert "&lt;/details&gt;" in md
    assert md.index("边界探针") < md.index("## D. ") < md.index("</details>")
    assert md.index("## 📚 引用来源") > md.index("</details>")


def test_rigor_warning_may_have_h3_outside_basement(monkeypatch):
    from types import SimpleNamespace
    from lib import render_extras

    monkeypatch.setattr(render_extras, "cross_validate", lambda _coll: [
        SimpleNamespace(deviation_pct=2.1, field="probe", detail="跨源差异"),
    ])
    md = render_report_v3(_reader_collection(), _SYMBOL, mode="full",
                          analysis=_reader_analysis())
    assert "### 数据验算警示" in _main_surface(md)


def test_moneyflow_still_has_module_three_when_northbound_missing():
    md = render_report_v3(_reader_collection(), _SYMBOL, mode="full")
    assert "### 资金态度" in md
    assert "全档资金（moneyflow）近5日全档净额" in md
    assert "北向个股资金流：不可得" in md


def test_stale_northbound_is_labeled_in_module_three():
    coll = _reader_collection()
    coll["market_structure"]["northbound"] = {
        "net_sum_10d": None, "staleness_note": "北向披露源停更", "source": "fixture",
    }
    md = render_report_v3(coll, _SYMBOL, mode="full")
    assert "### 资金态度" in md
    assert "北向披露源停更" in md
    assert "全档资金（moneyflow）近5日全档净额" in md


def test_human_bear_chain_keeps_origin_label(reader_md: str):
    assert "**补充空头链（analysis.json 注入）**" in reader_md


def test_no_analysis_still_folds_basement():
    """无 analysis 的基线报告：主阅读面只剩「报告说明」，底稿照常折叠。"""
    md = render_report_v3(_reader_collection(), _SYMBOL, mode="full")
    assert "<details>" in md
    surface = _main_surface(md)
    assert "## 报告说明" in surface
    assert all(h not in surface for h in _SURFACE_FORBIDDEN)
    assert "## 0." in md and "## 附录：数据质量与引擎自检" in md
