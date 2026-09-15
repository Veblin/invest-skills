"""P0-4：完成度门禁 × 真实渲染器输出的回归。

方案要求为「宁德时代报告」加 golden fixture（必须检出：缺同代 analysis.json /
存在占位 / 反证不足）。真实报告属个股产出，撞 CLAUDE.md「个股产出不进公开仓库」
红线，故改为**渲染器输出回归**：用合成 collection 跑真实渲染器，把渲染出的
文本喂给 `report_qc._check_stock_completion`，断言三类缺陷均被检出。

相比内联合成串（`skills/lib/tests/test_report_qc.py` 的写法），本文件的独特价值
是**追渲染器漂移**：渲染器改了占位句/标题文案、而 QC 正则没同步时，这里会红。
`test_renderer_sentinels_are_covered_by_qc` 把该耦合显式化。

缺陷态全部来自渲染器自身行为，无 mock、无真实个股数据。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Iterator

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))
_SHARED_LIB_DIR = _SCRIPTS_DIR.parent.parent / "lib"
if str(_SHARED_LIB_DIR) not in sys.path:
    sys.path.insert(0, str(_SHARED_LIB_DIR))

from fixtures.collections import collection_v2_minimal  # noqa: E402
from lib.render import render_report_v3  # noqa: E402
from report_qc import (  # noqa: E402
    _EMPTY_BASIS_RE,
    _TEMPLATE_MARKER_PATTERNS,
    _check_stock_completion,
    qc_file,
)

_SYMBOL = "600176"
_DIRNAME = "600176-测试股份"
_FILENAME = "2026-09-14-13-41-24.md"


@pytest.fixture(autouse=True)
def _offline_render(monkeypatch: Any) -> None:
    """render_dcf 的 beta 计算会拉沪深300 基准；打桩为空走降级分支。

    不打桩则每次渲染多一次 socket 超时（离线 CI 上是秒级抖动）。
    """
    from lib import collector

    monkeypatch.setattr(collector, "_akshare_hs300_dated_closes", lambda **_kw: [])


@pytest.fixture(scope="module")
def minimal_text() -> str:
    """最小 collection：数据充分但未注入分析 → 缺 sidecar + 左侧依据不可得。"""
    return render_report_v3(collection_v2_minimal(), _SYMBOL, mode="full")


@pytest.fixture(scope="module")
def events_text() -> str:
    """带公告事件的 collection：A-5 表格渲染出未替换的占位。"""
    coll = collection_v2_minimal()
    coll["events"] = [
        {"date": "2026-08-20", "title": "关于回购公司A股股份的公告", "type": "回购"},
        {"date": "2026-07-15", "title": "2026年半年度业绩预告", "type": "业绩"},
    ]
    return render_report_v3(coll, _SYMBOL, mode="full")


@pytest.fixture(scope="module")
def degraded_text() -> str:
    """退化 collection（仅 basic_info + quote）：多空两侧依据均无数据。"""
    coll = collection_v2_minimal()
    coll["dimensions"] = [
        d for d in coll["dimensions"] if d["dimension"] in ("basic_info", "quote")
    ]
    coll["summary"] = {"total": 2, "available": 2, "degraded": 0, "missing": 0}
    return render_report_v3(coll, _SYMBOL, mode="full")


def _write_report(tmp_path: Path, text: str) -> Path:
    """按 reports/ 目录布局落盘（detect_report_type 依赖目录名）。"""
    path = tmp_path / _DIRNAME / _FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _completion_ids(report_path: Path) -> set[str]:
    layer = _check_stock_completion(report_path, report_path.read_text(encoding="utf-8"))
    return {d["id"] for d in layer.details}


def _completion_details(report_path: Path) -> list[dict]:
    layer = _check_stock_completion(report_path, report_path.read_text(encoding="utf-8"))
    return list(layer.details)


# ── 三类缺陷态：全部由真实渲染器产出 ──────────────────────────────────────


def test_minimal_render_trips_sidecar_and_left_basis(tmp_path: Path, minimal_text: str) -> None:
    """缺同代 analysis.json + 左侧依据不可得。"""
    path = _write_report(tmp_path, minimal_text)
    ids = _completion_ids(path)
    assert "completion-analysis-sidecar-missing" in ids
    assert "completion-empty-basis" in ids


def test_events_render_trips_template_placeholder(tmp_path: Path, events_text: str) -> None:
    """未注入 analysis 时，A-5 表格保留渲染器自身的占位。"""
    path = _write_report(tmp_path, events_text)
    assert "completion-template-placeholder" in _completion_ids(path)
    # 占位必须是渲染器真实产出，而非测试注入
    assert "[待 Claude report 阶段填充]" in events_text


def test_degraded_render_trips_both_directions(tmp_path: Path, degraded_text: str) -> None:
    """反证不足：多空两侧与左侧依据同时为空（该口径由 owner 裁决沿用）。"""
    path = _write_report(tmp_path, degraded_text)
    details = _completion_details(path)
    empty = [d for d in details if d["id"] == "completion-empty-basis"]
    assert {"Bull", "Bear", "左侧"} <= {d["message"].split(" 依据节")[0] for d in empty}


# ── sidecar 存在性：从「缺失」到「合格」再到「不合格」 ────────────────────

_VALID_SIDECAR = json.dumps([{
    "module": "research",
    "title": "研究发现",
    "facts_md": "财务事实 [来源: engine]",
    "analysis_md": "分析结论 [证据: B]",
    "evidence_tag": "B",
    "position": "research",
}], ensure_ascii=False)


def test_valid_sidecar_clears_missing_finding(tmp_path: Path, minimal_text: str) -> None:
    path = _write_report(tmp_path, minimal_text)
    path.with_suffix(".analysis.json").write_text(_VALID_SIDECAR, encoding="utf-8")
    assert "completion-analysis-sidecar-missing" not in _completion_ids(path)


def test_invalid_sidecar_is_reported(tmp_path: Path, minimal_text: str) -> None:
    path = _write_report(tmp_path, minimal_text)
    path.with_suffix(".analysis.json").write_text("[]\n", encoding="utf-8")
    assert "completion-analysis-sidecar-invalid" in _completion_ids(path)


def test_qc_file_surfaces_completion_failure(tmp_path: Path, minimal_text: str) -> None:
    """端到端：qc_file 把完成度问题并入 overall，且不因 --fail-on error 被放过。"""
    path = _write_report(tmp_path, minimal_text)
    result = qc_file(path, fail_on="error")
    completion = next(layer for layer in result.layers if layer.layer == "completion")
    assert completion.status == "fail"
    assert result.overall == "FAIL"


# ── 渲染器 ↔ QC 正则的耦合守卫 ────────────────────────────────────────────


def test_renderer_sentinels_are_covered_by_qc(
    minimal_text: str, events_text: str, degraded_text: str,
) -> None:
    """渲染器产出的哨兵句必须仍被 QC 的正则覆盖。

    这条断言失败时错误信息直指「渲染器改了文案 / QC 正则该同步」，
    比 finding id 断言更易诊断——两边任一处漂移都会在这里先红。
    """
    sentinels = [
        ("- 当前数据未形成明确多头逻辑链", degraded_text),
        ("- 当前数据未形成明确空头逻辑链", degraded_text),
        ("① 左侧参考指标数据不足", degraded_text),
        ("[待 Claude report 阶段填充]", events_text),
        ("① 左侧参考指标数据不足", minimal_text),
    ]
    for needle, text in sentinels:
        assert needle in text, f"渲染器不再产出该哨兵句：{needle}"

    for line in degraded_text.splitlines():
        if "当前数据未形成明确" in line:
            assert _EMPTY_BASIS_RE.search(line), line
    for line in events_text.splitlines():
        if "[待 Claude report 阶段填充]" in line:
            assert any(p.search(line) for p in _TEMPLATE_MARKER_PATTERNS), line


def test_structural_hint_block_is_not_a_placeholder(minimal_text: str) -> None:
    """回归：`> [分析提示]` 是 LAW 10 体例标签，不得被当作未填占位。

    修正前它让完成度门禁对任何 full 报告恒 FAIL（真实报告 14 处），
    与 full 头部「分析合成已注入」的判定自相矛盾。
    """
    assert "> [分析提示]" in minimal_text
    for line in minimal_text.splitlines():
        if line.strip() == "> [分析提示]":
            for pattern in _TEMPLATE_MARKER_PATTERNS:
                assert not pattern.search(line), line
