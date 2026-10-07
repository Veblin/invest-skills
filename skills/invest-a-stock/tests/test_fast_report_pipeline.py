"""Report input checks and independent macro collection on the critical path."""
from __future__ import annotations

import threading

import pytest


def test_validate_analysis_cli_reports_all_errors_without_collection(tmp_path, monkeypatch, capsys):
    import invest

    path = tmp_path / "analysis.json"
    path.write_text('[{"module": "events"}]', encoding="utf-8")
    monkeypatch.setattr(invest.collector, "collect_all", lambda *a, **k: pytest.fail("collected"))

    args = invest.build_parser().parse_args(["validate-analysis", str(path)])
    assert invest.CMD_DISPATCH[args.command](args) == 2
    assert "missing:title" in capsys.readouterr().err


def test_validate_analysis_cli_accepts_valid_payload(tmp_path):
    import invest

    path = tmp_path / "analysis.json"
    path.write_text(
        '[{"module":"events","title":"事件","facts_md":"事实",'
        '"analysis_md":"分析","evidence_tag":"B","position":"events"}]',
        encoding="utf-8",
    )
    args = invest.build_parser().parse_args(["validate-analysis", str(path)])
    assert invest.CMD_DISPATCH[args.command](args) == 0


def test_report_rejects_invalid_analysis_before_collection(tmp_path, monkeypatch):
    import invest

    path = tmp_path / "analysis.json"
    path.write_text('[{"module": "events"}]', encoding="utf-8")
    monkeypatch.setattr(invest.collector, "collect_all", lambda *a, **k: pytest.fail("collected"))
    args = invest.build_parser().parse_args(["report", "600176", "--analysis", str(path)])

    assert invest.cmd_report(args) == 2


def test_draft_slot_preflight_blocks_report_before_collection(tmp_path, monkeypatch, capsys):
    import invest

    analysis = tmp_path / "analysis.json"
    analysis.write_text(
        '[{"module":"events","title":"事件","facts_md":"事实",'
        '"analysis_md":"分析","evidence_tag":"B","position":"events"}]',
        encoding="utf-8",
    )
    draft = tmp_path / "draft.md"
    draft.write_text(
        "分析提示（Claude 填写）\n"
        "[待 Claude 填充管理层论述解读]\n"
        "[待 Claude 核对多头依据]\n"
        "当前数据未形成明确多头逻辑链\n"
        "当前数据未形成明确空头逻辑链\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(invest.collector, "collect_all", lambda *a, **k: pytest.fail("collected"))
    args = invest.build_parser().parse_args([
        "report", "600176", "--analysis", str(analysis), "--draft", str(draft),
    ])

    assert invest.cmd_report(args) == 2
    err = capsys.readouterr().err
    for slot in ("bull_chain", "bear_chain", "mda_narrative", "participant_scan"):
        assert slot in err


def test_draft_slot_markers_still_exist_in_their_producers():
    """闸门判据表的占位串必须在**产出它的模块**里仍然存在。

    `missing_draft_slots` 的字面量是渲染层的镜像：渲染层改了措辞而表没跟着改，
    闸门静默退化为 no-op——`--draft` 照旧打印「✅ 校验通过」，占位流入终稿
    （report_qc 用的是宽容正则 `_EMPTY_BASIS_RE`，多半仍会命中，所以更难发现）。
    上面那个 preflight 测试手工把占位串写进 draft，只测 schema 侧，测不出这类漂移。

    用源码级断言而非渲染级：产出点分布在 4 个模块、各自的渲染条件不同
    （如 mda 卡需 financials、A-5 单元格需管理层时间线），逐个铺 fixture 只会让
    测试依赖更多无关前提，而契约本身是「这串字面量还在」。
    """
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "scripts"
    producers = {
        "bull_chain": "lib/render_risk.py",
        "bear_chain": "lib/render_risk.py",
        "mda_narrative": "lib/analysis_templates.py",
        "participant_scan": "lib/participant_scan.py",
        "event_classification": "lib/render_markdown/_v3.py",
    }
    from lib.analysis_schema import DRAFT_SLOT_MARKERS

    for slot, marker, _keys in DRAFT_SLOT_MARKERS:
        src = (root / producers[slot]).read_text(encoding="utf-8")
        assert marker in src, (
            f"{producers[slot]} 里找不到闸门依赖的占位串 {marker!r}（槽位 {slot}）——"
            "渲染层改了措辞却没同步 analysis_schema.DRAFT_SLOT_MARKERS，"
            "--draft 预检已静默失效"
        )


def test_draft_slot_markers_appear_in_rendered_report():
    """渲染实际输出里确实带这些占位串（源码级断言的端到端佐证）。

    只覆盖本次最小 collection 真会渲染的宿主：5b 空头节在引擎**已给出**逻辑链时
    渲染链本身、不打「未形成明确空头逻辑链」声明（见 test_v030_reading_layers
    的同名分支测试），故 bear_chain 在此排除，由上一个测试的源码断言兜底。
    """
    from fixtures.collections import collection_v2_minimal
    from lib.analysis_schema import DRAFT_SLOT_MARKERS
    from lib.render_markdown._concise import render_report_v3

    coll = collection_v2_minimal()
    coll["market_structure"] = {"moneyflow": {"net_sum_5d": 1.5e8, "source": "t"}}
    coll["events"] = [{
        "date": "2026-06-11", "type": "buyback", "title": "回购",
        "impact_dimension": "估值", "duration": "中长期变量",
    }]
    coll.setdefault("_meta", {})["analysis_cards"] = {
        "event_classifications": [
            {"event_type": "buyback", "event_label": "回购", "events": [{"date": "2026-06-11"}]},
        ],
        "mda_narrative": {
            "generated_at": "2026-06-11T12:00:00+00:00", "revenue_growth_yoy": 12.0,
            "profit_growth_yoy": 8.0, "gross_margin": 30.0, "net_margin": 10.0,
            "narrative_slot": "[待 Claude 填充管理层论述解读]",
        },
    }
    md = render_report_v3(coll, "600176", mode="full")

    absent = [
        (slot, marker)
        for slot, marker, _ in DRAFT_SLOT_MARKERS
        if slot != "bear_chain" and marker != "当前数据未形成明确多头逻辑链"
        and marker not in md
    ]
    assert absent == [], f"渲染输出里缺少闸门依赖的占位串：{absent}"


def test_macro_starts_before_dimension_fanout_finishes(monkeypatch):
    from lib.collector import _orchestrate as orchestration

    macro_started = threading.Event()

    def macro(_symbol, _enabled):
        macro_started.set()
        return {"status": "ok"}

    def dimensions(_symbol, _dims, _kline_kwargs):
        assert macro_started.wait(1), "macro must start while dimensions are running"
        return {}

    monkeypatch.setattr(orchestration, "_collect_macro_context_block", macro)
    monkeypatch.setattr(orchestration, "_collect_dims_fanout", dimensions)
    monkeypatch.setattr(orchestration, "_collect_industry_pricing_block", lambda *a: None)
    monkeypatch.setattr(orchestration, "_fuse_dimensions", lambda *a: {})
    monkeypatch.setattr(orchestration, "_score_credibility", lambda *a: {})
    monkeypatch.setattr(orchestration, "_collect_chain_context_block", lambda *a: {})
    for name in (
        "_attach_sector_sync_block", "_attach_phase2_block", "_attach_events_block",
        "_attach_analysis_cards_block", "_attach_manifest_block", "_attach_news_pack_block",
    ):
        monkeypatch.setattr(orchestration, name, lambda *a: None)

    result = orchestration.collect_all("600176", ["basic_info"], with_macro=True)
    assert result["macro_context"] == {"status": "ok"}


def test_no_macro_request_does_not_start_macro_worker(monkeypatch):
    from lib.collector import _orchestrate as orchestration

    monkeypatch.setattr(orchestration, "_collect_macro_context_block",
                        lambda *a: pytest.fail("macro worker started"))
    monkeypatch.setattr(orchestration, "_collect_dims_fanout", lambda *a: {})
    monkeypatch.setattr(orchestration, "_collect_industry_pricing_block", lambda *a: None)
    monkeypatch.setattr(orchestration, "_fuse_dimensions", lambda *a: {})
    monkeypatch.setattr(orchestration, "_score_credibility", lambda *a: {})
    monkeypatch.setattr(orchestration, "_collect_chain_context_block", lambda *a: {})
    for name in (
        "_attach_sector_sync_block", "_attach_phase2_block", "_attach_events_block",
        "_attach_analysis_cards_block", "_attach_manifest_block", "_attach_news_pack_block",
    ):
        monkeypatch.setattr(orchestration, name, lambda *a: None)

    assert orchestration.collect_all("600176", ["basic_info"])["macro_context"] == {}
