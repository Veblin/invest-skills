"""CLI 级契约：``report --mode insight --analysis``。

此前只有 full 分支对 ``--analysis`` 有 CLI 级覆盖；insight 分支曾「校验后静默
忽略」——参数被解析、被校验、然后什么都不做，属最坏的一种失败（看起来生效了）。
本文件锁死三件事：

1. 校验通过的 analysis 必须随 insight 产物**同代落盘**，并被渲染进「分析合成」分区
2. 未传 ``--analysis`` 时输出契约不变（不报错、不写分析侧车、不渲染该分区）
3. 非法 analysis 必须 fail-loud（exit 2），不得静默降级
"""
from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path

import pytest

from fixtures.collections import collection_v2_minimal

_ANALYSIS = [{
    "module": "financials",
    "title": "单位盈利下滑的性质",
    "facts_md": "综合毛利率 25.02% → 23.93%。",
    "analysis_md": "**结论：** 判别变量是三季报境内毛利率。",
    "evidence_tag": "B",
    "position": "financials",
}]


def _args(tmp_path: Path, **overrides) -> Namespace:
    base = dict(symbol="600176", store=False, dims="basic_info,quote",
                with_macro=False, deep=False, plan="", save_raw=False,
                resume=False, emit="md", mode="insight",
                outdir=str(tmp_path / "reports"), strict_rigor=False,
                material_gap=False, with_news_pack=False, analysis=None)
    base.update(overrides)
    return Namespace(**base)


@pytest.fixture()
def _invest(monkeypatch):
    import invest

    monkeypatch.setattr(invest, "_HAS_STORE", False)
    monkeypatch.setattr(invest.collector, "collect_all",
                        lambda *a, **k: collection_v2_minimal())
    return invest


def test_insight_report_writes_same_generation_analysis_sidecar(_invest, tmp_path):
    upstream = tmp_path / "upstream.analysis.json"
    upstream.write_text(json.dumps(_ANALYSIS, ensure_ascii=False), encoding="utf-8")

    assert _invest.cmd_report(_args(tmp_path, analysis=str(upstream))) == 0

    report = next((tmp_path / "reports").rglob("*.insight.md"))
    sidecar = report.with_suffix(".analysis.json")
    assert sidecar.is_file(), "分析段必须随产物同代落盘，否则「已注入」无从追溯"
    assert json.loads(sidecar.read_text(encoding="utf-8")) == _ANALYSIS

    md = report.read_text(encoding="utf-8")
    assert "## 分析合成（Claude 撰写）" in md
    assert _ANALYSIS[0]["title"] in md
    assert "分析合成：** 已注入（1 段）" in md

    manifest = json.loads(
        report.with_suffix(".report.json").read_text(encoding="utf-8"))
    assert manifest["analysis_sidecar"] == sidecar.name


def test_insight_report_without_analysis_keeps_contract(_invest, tmp_path):
    assert _invest.cmd_report(_args(tmp_path)) == 0

    report = next((tmp_path / "reports").rglob("*.insight.md"))
    assert not report.with_suffix(".analysis.json").exists()
    md = report.read_text(encoding="utf-8")
    assert "## 分析合成" not in md
    assert "分析合成：** 未注入（仅引擎结论）" in md
    for suffix in (".facts.json", ".insight.json", ".report.json"):
        assert report.with_suffix(suffix).is_file()


def test_insight_report_invalid_analysis_exits_2(_invest, tmp_path):
    bad = tmp_path / "bad.analysis.json"
    bad.write_text(json.dumps([{"module": "x"}], ensure_ascii=False), encoding="utf-8")

    assert _invest.cmd_report(_args(tmp_path, analysis=str(bad))) == 2
    assert not list((tmp_path / "reports").rglob("*.insight.md"))


def test_empty_analysis_array_is_treated_as_absent(_invest, tmp_path):
    """空数组不携带分析段 → 必须与「未传」一致，不得留下自相矛盾的产物。

    曾用 `is not None` 判断：写出空侧车 + 登记 manifest，而报告写着「未注入」
    ——审计者按 manifest 回查会拿到一份自称未注入却挂着分析侧车的产物。
    full 模式的 _has_valid_analysis_payload 一直按空=未注入处理，此处对齐。
    """
    empty = tmp_path / "empty.analysis.json"
    empty.write_text("[]", encoding="utf-8")

    assert _invest.cmd_report(_args(tmp_path, analysis=str(empty))) == 0

    report = next((tmp_path / "reports").rglob("*.insight.md"))
    assert not report.with_suffix(".analysis.json").exists()
    manifest = json.loads(
        report.with_suffix(".report.json").read_text(encoding="utf-8"))
    assert manifest["analysis_sidecar"] is None
    md = report.read_text(encoding="utf-8")
    assert "## 分析合成" not in md
    assert "分析合成：** 未注入（仅引擎结论）" in md


def test_insight_emit_json_and_compact_write_no_sidecars(_invest, tmp_path):
    """无 md 可绑定 → 不写任何侧车（现状契约，锁住防漂移）。"""
    for emit in ("json", "compact"):
        assert _invest.cmd_report(_args(tmp_path, emit=emit)) == 0
    reports = tmp_path / "reports"
    created = list(reports.rglob("*")) if reports.exists() else []
    assert created == []
