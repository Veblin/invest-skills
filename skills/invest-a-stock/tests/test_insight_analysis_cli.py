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


@pytest.mark.parametrize("pe", [0.0, -3.0])
def test_insight_cli_preserves_event_provenance_and_pe_limits(_invest, monkeypatch, tmp_path, pe):
    """真实事件聚合与 report 入口：公告失败、分红回退成功，核对完整成品。"""
    from datetime import datetime, timezone
    from lib import events
    from report_qc import qc_file

    collection = collection_v2_minimal()
    for dim in collection["dimensions"]:
        if dim["dimension"] == "valuation":
            dim["data"][-1]["pe_ttm"] = pe
        elif dim["dimension"] == "financials":
            dim["data"][-1]["net_profit"] = None
        elif dim["dimension"] == "quote":
            dim["data"]["change_pct"] = -6.0
    monkeypatch.setattr(events, "_shanghai_now", lambda: datetime(2026, 6, 11, tzinfo=timezone.utc))
    monkeypatch.setattr(events, "_fetch_notice_events", lambda symbol: None)
    dividend = {"date": "2026-06-10", "type": "dividend", "title": "测试分红回退标记",
                "source": "akshare stock_dividend_cninfo"}
    monkeypatch.setattr(events, "_fetch_dividend_events", lambda symbol: [
        dict(dividend), dict(dividend),  # 同源重复只算一次
        {**dividend, "date": "2020-01-01", "source": "expired.source"}])
    monkeypatch.setattr(events, "_fetch_shareholder_events", lambda symbol: [])
    events.attach_events(collection, "600176")
    assert collection["_meta"]["events_legs"]["notice"] == "failed"
    assert len(collection["events"]) == 1
    monkeypatch.setattr(_invest.collector, "collect_all", lambda *a, **k: collection)

    assert _invest.cmd_report(_args(tmp_path, emit="html")) == 0
    report = next((tmp_path / "reports").rglob("*.insight.md"))
    sidecar = json.loads(report.with_suffix(".facts.json").read_text(encoding="utf-8"))
    fact = next(f for f in sidecar["facts"] if f["id"] == "events.count")
    assert fact["value"] == 1
    assert fact["source_ids"] == ["events.akshare.stock_dividend_cninfo"]
    assert "expired.source" not in json.dumps(sidecar)
    for artifact in (report, report.with_suffix(".html")):
        text = artifact.read_text(encoding="utf-8")
        assert "近窗口事件 1 条" in text and "近窗口公告" not in text
        assert "stock_dividend_cninfo" in text and "个股公告" not in text
        assert "非正值" in text and "为负值（亏损期）" not in text
    result = qc_file(report, fail_on="error")
    assert next(layer for layer in result.layers if layer.layer == "insight-contract").status == "pass"


def test_insight_report_invalid_analysis_exits_2(_invest, tmp_path):
    bad = tmp_path / "bad.analysis.json"
    bad.write_text(json.dumps([{"module": "x"}], ensure_ascii=False), encoding="utf-8")

    assert _invest.cmd_report(_args(tmp_path, analysis=str(bad))) == 2
    assert not list((tmp_path / "reports").rglob("*.insight.md"))


def test_empty_analysis_array_is_treated_as_absent(_invest, tmp_path):
    """空数组不携带分析段 → 必须与「未传」一致，不得留下自相矛盾的产物。

    曾用 `is not None` 判断：写出空侧车 + 登记 manifest，而报告写着「未注入」
    ——审计者按 manifest 回查会拿到一份自称未注入却挂着分析侧车的产物。
    full 模式的 analysis_payload_status 一直按空=未注入处理，此处对齐。
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


# ── v0.3.0 D5：--mode insight --save-raw 必须真的存档 ──────────────────────


def test_insight_save_raw_archives(_invest, tmp_path, monkeypatch):
    """旧实现把 `--save-raw` 的存档块只写在 full 分支尾部（render 之后），而
    insight 分支在它之前就 `return 0`（json / compact 出口更早）→ 参数被解析、
    被接受、然后**静默忽略**（最坏的一种失败：看起来生效了）。抽成
    `_maybe_save_raw` 并在每个出口调用后修复。
    """
    import lib.archiver as archiver

    seen: list[str] = []
    monkeypatch.setattr(
        archiver, "archive_collection",
        lambda symbol, result: (seen.append(symbol), str(tmp_path / "raw.json"))[1])

    assert _invest.cmd_report(_args(tmp_path, save_raw=True)) == 0
    assert seen == ["600176"], "--mode insight 下 --save-raw 未触发存档"


def test_insight_without_save_raw_does_not_archive(_invest, tmp_path, monkeypatch):
    """对照：未传 --save-raw 不得存档。"""
    import lib.archiver as archiver

    seen: list[str] = []
    monkeypatch.setattr(
        archiver, "archive_collection",
        lambda symbol, result: (seen.append(symbol), "x")[1])

    assert _invest.cmd_report(_args(tmp_path)) == 0
    assert seen == []
