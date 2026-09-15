"""A1「本次新增发现」与 A5「分析链」的契约测试。

两个区块补的是 0914 方案规定、此前未实现的首屏区块。本文件的断言重点是三件事：

1. **降级一致**：store 不可用 / 无历史 / 无显著变化三条路径渲染**逐字相同**的
   固定串（0914 判定规则 #6），且不泄露内部 reason、不出现「不可得」；
2. **可追溯**：变化的每个数字都原样来自 store 快照对比，且带北京时间来源行；
3. **不写因果**：分析链只描述关系并给出替代解释，`association_status` 仅允许
   `consistent` / `mechanism_unconfirmed`。
"""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest

from fixtures.collections import collection_v2_minimal

_SHARED_LIB_DIR = Path(__file__).resolve().parents[2] / "lib"


# ── fixture 辅助 ────────────────────────────────────────────────────────────


def _set_latest_roe(collection: dict, value: float) -> None:
    """改最新报告期的 ROE，用于构造「有变化」的两份快照。"""
    for dim in collection["dimensions"]:
        if dim["dimension"] == "financials":
            dim["data"] = sorted(dim["data"], key=lambda row: row["end_date"])
            dim["data"][-1] = {**dim["data"][-1], "roe": value}


def _with_prior_year(collection: dict) -> dict:
    """补一行上年同期（20231231）。

    B4 修复后 ``financials.revenue.change`` 取「同报告期上年同期」；共享 fixture
    的 20240331/20241231 之间没有同比关系，因此由此处自造，**不改共享 fixture**
    （会影响其他消费者）。
    """
    result = copy.deepcopy(collection)
    for dim in result["dimensions"]:
        if dim["dimension"] == "financials":
            dim["data"] = list(dim["data"]) + [{
                "end_date": "20231231", "roe": 17.0, "eps": 1.9,
                "profit_dedt": 0.9e8, "revenue": 5.0e9, "net_profit": 1.0e8,
                "n_cashflow_act": 1.2e8, "ocf": 1.2e8,
            }]
    return result


def _model(collection: dict | None = None, *, key_diff=None, diff_reason="no_history"):
    from lib.insight_model import build_report_model

    return build_report_model(collection or collection_v2_minimal(), "600176",
                              key_diff=key_diff, diff_reason=diff_reason)


def _section(markdown: str, title: str) -> str:
    """取 `## {title}` 到下一个 `## ` 之间的正文。"""
    marker = f"## {title}"
    assert marker in markdown, f"缺少章节 {title}"
    body = markdown.split(marker, 1)[1]
    return body.split("\n## ", 1)[0]


# ── A1：本次新增发现 ────────────────────────────────────────────────────────


def test_discoveries_render_actual_changes_with_beijing_source(isolated_store) -> None:
    from lib.insight_model import load_snapshot_diff
    from lib.render_insight import render_insight_markdown

    previous = collection_v2_minimal()
    previous["fetched_at"] = "2026-06-04T12:00:00+00:00"
    _set_latest_roe(previous, 12.0)
    isolated_store.save_collection(previous)

    current = collection_v2_minimal()
    current["fetched_at"] = "2026-06-11T12:00:00+00:00"
    _set_latest_roe(current, 22.0)

    key_diff, reason = load_snapshot_diff("600176", current)
    assert reason == "ok" and key_diff is not None

    model = _model(current, key_diff=key_diff, diff_reason=reason)
    block = model["discoveries"]
    assert block["status"] == "changed"
    assert 1 <= len(block["items"]) <= 3
    assert all(item["old"] != item["new"] for item in block["items"])

    markdown = render_insight_markdown(model)
    body = _section(markdown, "本次新增发现")
    assert "北京时间" in body
    assert "→" in body
    # 可追溯：每个变化值都出现在该区块内，且区块带来源行
    assert "[来源: store 快照对比" in body
    for item in block["items"]:
        assert str(item["old"]) in body and str(item["new"]) in body


@pytest.mark.parametrize("mode", ["empty_store", "store_raises"])
def test_discoveries_degrade_to_identical_fixed_state(isolated_store, monkeypatch, mode) -> None:
    """三条「无」路径必须渲染逐字相同的固定串，且不泄露内部状态。"""
    from lib import store as store_mod
    from lib.insight_model import load_snapshot_diff
    from lib.render_insight import render_insight_markdown

    current = collection_v2_minimal()
    if mode == "store_raises":
        def _boom(*args, **kwargs):
            raise OSError("db locked")

        monkeypatch.setattr(store_mod, "load_key_diff_vs_stored", _boom)

    key_diff, reason = load_snapshot_diff("600176", current)
    assert key_diff is None
    assert reason == ("no_history" if mode == "empty_store" else "store_unavailable")

    body = _section(render_insight_markdown(_model(current, key_diff=key_diff,
                                                   diff_reason=reason)),
                    "本次新增发现")
    assert "无可对比的历史快照" in body   # 不假装「无变化」
    assert "→" not in body               # 不得残留对比箭头
    assert "不可得" not in body           # 不得暴露「不可得」
    assert reason not in body             # reason 只进侧车，渲染器禁读

    # 与另一条降级路径逐字一致
    other = _section(render_insight_markdown(_model(current)), "本次新增发现")
    assert body.strip() == other.strip()


def test_no_material_change_shows_verifiable_baseline() -> None:
    """有基线但无变化 → 必须给出对比窗口与无变化项数，读者可核验。

    （审查意见 #6：只写「无新增发现」而无基线，是无法核验的系统文本。）
    """
    from lib.render_insight import render_insight_markdown

    empty_diff = {"old_at": "2026-06-04T12:00:00+00:00",
                  "new_at": "2026-06-11T12:00:00+00:00",
                  "categories": {},
                  "unchanged": ["valuation.pe_ttm", "financials.roe"], "events": None}
    model = _model(key_diff=empty_diff, diff_reason="ok")
    assert model["discoveries"]["status"] == "none"
    assert model["discoveries"]["reason"] == "no_material_change"
    body = _section(render_insight_markdown(model), "本次新增发现")
    assert "2 项关键字段无显著变化" in body
    assert "2026-06-04" in body and "北京时间" in body   # 基线窗口可见


def test_chain_engineering_note_is_not_rendered() -> None:
    """chain["note"] 属程序规则说明，只留侧车，不进读者报告（审查意见 #3）。"""
    from lib.render_insight import render_insight_html, render_insight_markdown

    model = _model(_with_prior_year(collection_v2_minimal()))
    assert model["analysis_chains"], "前置条件：本 fixture 应产出分析链"
    assert all(c["note"] for c in model["analysis_chains"])   # 侧车里保留
    markdown = render_insight_markdown(model)
    assert "无同行评审先例" not in markdown
    assert "工程约定" not in markdown
    assert "无同行评审先例" not in render_insight_html(model)


@pytest.mark.parametrize("ratio,needle,anti", [
    (1.391, "维持", "回升"),
    (0.45, "回升", "维持"),
])
def test_cash_conversion_verification_matches_branch(ratio, needle, anti) -> None:
    """验证动作必须与分支一致（审查意见 #4）。

    原实现硬编码「确认比值是否回到 0.6 以上」——当比值本就高于 0.6 时该表述不成立。
    """
    from lib.insight_model import build_analysis_chains

    facts = [
        {"id": "financials.ocf_to_np.latest", "value": ratio, "as_of": "20260630",
         "unit": "ratio", "basis": "x", "source_ids": ["s"], "formula": None},
        {"id": "financials.revenue.change", "value": 54.8, "as_of": "20260630",
         "unit": "percent", "basis": "x", "source_ids": ["s"], "formula": None},
    ]
    chain = next(c for c in build_analysis_chains(facts) if c["id"] == "chain.cash-conversion")
    test = chain["verification"]["test"]
    assert needle in test and anti not in test


# ── A5：分析链 ──────────────────────────────────────────────────────────────


def test_chains_are_deterministic_and_auditable() -> None:
    from lib.insight_model import _CAUSAL_RE
    from lib.render_insight import render_insight_html, render_insight_markdown

    model = _model(_with_prior_year(collection_v2_minimal()))
    chains = model["analysis_chains"]
    assert 1 <= len(chains) <= 2
    fact_ids = {fact["id"] for fact in model["facts"]}
    for chain in chains:
        assert len(chain["fact_ids"]) >= 2
        assert set(chain["fact_ids"]).issubset(fact_ids)
        assert chain["association_status"] in {"consistent", "mechanism_unconfirmed"}
        assert len(chain["alternatives"]) >= 2
        assert chain["verification"]["event"] and chain["verification"]["test"]
        text = " ".join([chain["relation"], chain["mechanism"], *chain["alternatives"]])
        assert not _CAUSAL_RE.search(text), f"链 {chain['id']} 含因果措辞"

    markdown = render_insight_markdown(model)
    html = render_insight_html(model)
    body = _section(markdown, "分析链")
    assert "只描述事实之间的同向/不同向关系" in body
    for chain in chains:
        assert chain["id"] in body and chain["id"] in html
        assert chain["relation"] in body


def test_chains_skip_when_prerequisites_missing() -> None:
    """前置事实不足 → 整条省略并出占位声明，不硬凑。"""
    from lib.render_insight import render_insight_markdown

    collection = collection_v2_minimal()
    collection["dimensions"] = collection["dimensions"][:1]
    model = _model(collection)
    assert model["analysis_chains"] == []
    assert "不足以构成可验证的分析链" in _section(render_insight_markdown(model), "分析链")
    # A1/A5 不得改变完成度判据
    assert model["completion"] == "insufficient"


def test_chains_never_cite_price_reaction() -> None:
    """价格反应禁作链条环节证据。"""
    model = _model(_with_prior_year(collection_v2_minimal()))
    for chain in model["analysis_chains"]:
        assert "quote.change_pct.latest" not in chain["fact_ids"]


# ── 校验层：逐条只坏一个字段 ─────────────────────────────────────────────────


def _base_model():
    return _model(_with_prior_year(collection_v2_minimal()))


@pytest.mark.parametrize("mutate,needle", [
    (lambda m: m["analysis_chains"][0].update(association_status="descriptive"), "关联边界非法"),
    (lambda m: m["analysis_chains"][0].update(alternatives=["只有一条"]), "替代解释"),
    (lambda m: m["analysis_chains"][0].update(fact_ids=["valuation.pe_ttm.latest"]), "两个事实"),
    (lambda m: m["analysis_chains"][0].update(fact_ids=["nope.1", "nope.2"]), "不存在的 Fact"),
    (lambda m: m["analysis_chains"][0].update(mechanism="这导致了后续变化。"), "因果断言"),
    (lambda m: m["analysis_chains"][0].update(
        fact_ids=["quote.change_pct.latest", "valuation.pe_ttm.latest"]), "价格反应"),
    (lambda m: m["analysis_chains"][0].update(verification={"event": "x"}), "验证动作"),
    (lambda m: m.update(analysis_chains=[m["analysis_chains"][0]] * 3), "不得超过"),
])
def test_validate_rejects_broken_chain(mutate, needle) -> None:
    from lib.insight_model import validate_insight

    model = _base_model()
    mutate(model)
    errors = validate_insight(model)
    assert any(needle in err for err in errors), errors


@pytest.mark.parametrize("mutate,needle", [
    (lambda b: b.update(status="bogus"), "status 非法"),
    (lambda b: b.update(items=[]), "items 不得为空"),
    (lambda b: b.update(old_at=None), "old_at"),
    (lambda b: b.update(old_at_label=None), "时间标签"),
    (lambda b: b.update(items=[{"category": "估值"}]), "category 与 label"),
    (lambda b: b.update(items=[{"category": "估值", "label": "PE"}]), "old 与 new"),
])
def test_validate_rejects_broken_discoveries(mutate, needle) -> None:
    from lib.insight_model import validate_insight

    model = _base_model()
    block = model["discoveries"]
    block.update(status="changed", old_at="2026-06-04T12:00:00+00:00",
                 new_at="2026-06-11T12:00:00+00:00",
                 old_at_label="2026-06-04 20:00 (北京时间)",
                 new_at_label="2026-06-11 20:00 (北京时间)",
                 items=[{"category": "valuation", "category_label": "估值", "field": "roe",
                         "label": "ROE", "old": 12.0, "new": 22.0, "pct": 83.3}])
    mutate(block)
    errors = validate_insight(model)
    assert any(needle in err for err in errors), errors


def test_discoveries_tolerate_value_becoming_unavailable() -> None:
    """store 的 diff 会合法产出「值 → None」（某指标本期转为不可得，如 margin_balance）。

    这种条目必须能通过校验并以 "-" 呈现——否则一旦它落进前 3 条，整份报告会 exit 2。
    """
    from lib.insight_model import validate_insight
    from lib.render_insight import render_insight_markdown

    diff = {
        "old_at": "2026-09-14T15:26:57+00:00", "new_at": "2026-09-15T10:48:31+00:00",
        "categories": {"valuation": [
            {"field": "pe_ttm", "old": 18.35, "new": None, "pct": None},
        ]},
        "unchanged": [], "events": None,
    }
    model = _model(key_diff=diff, diff_reason="ok")
    assert validate_insight(model) == []
    body = _section(render_insight_markdown(model), "本次新增发现")
    assert "18.35" in body and "-" in body


def test_validate_accepts_models_without_new_blocks() -> None:
    """缺键视为合法——兼容旧/手写 model。"""
    from lib.insight_model import validate_insight

    model = _model()
    model.pop("discoveries", None)
    model.pop("analysis_chains", None)
    assert validate_insight(model) == []


# ── QC 契约层回归 ───────────────────────────────────────────────────────────


def test_qc_accepts_insight_with_new_blocks(tmp_path: Path) -> None:
    from lib.insight_model import write_sidecars
    from lib.render_insight import render_insight_html, render_insight_markdown
    sys.path.insert(0, str(_SHARED_LIB_DIR))
    try:
        from report_qc import qc_file
    finally:
        sys.path.remove(str(_SHARED_LIB_DIR))

    model = _model(_with_prior_year(collection_v2_minimal()))
    # report_qc 的 report_type 按**路径**判定：需 {symbol}-{name}/ 目录才归为 stock，
    # 否则 insight-contract 层不会被挂载。
    report = tmp_path / "600176-测试股份" / "sample.insight.md"
    report.parent.mkdir()
    report.write_text(render_insight_markdown(model), encoding="utf-8")
    html = report.with_suffix(".html")
    html.write_text(render_insight_html(model), encoding="utf-8")
    write_sidecars(report, model, html_path=html)

    result = qc_file(report, fail_on="error")
    assert next(layer for layer in result.layers if layer.layer == "insight-contract").status == "pass"
    assert next(layer for layer in result.layers if layer.layer == "sourcing").status == "pass"

    payload = json.loads(report.with_suffix(".insight.json").read_text(encoding="utf-8"))
    assert "discoveries" in payload and "analysis_chains" in payload
