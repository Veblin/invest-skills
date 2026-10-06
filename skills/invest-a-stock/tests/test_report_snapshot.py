"""Fixed report inputs stay immutable across repeated renders."""

from __future__ import annotations

import json

import sys

from test_default_store import _fake_result


def _sealed(symbol="600176", plan_hash=None):
    from lib.report_snapshot import seal

    data = _fake_result(symbol)
    data.update({"market_structure": {"availability": {"put_call_ratio": "unavailable"},
                                      "put_call_ratio": None},
                 "industry_peers": {"peers": [], "error": "unavailable"},
                 "pe_band": None,
                 "industry_pricing": {"status": "missing", "data": None},
                 "price_shock": {"has_shock": False, "shock_dates": []},
                 "events": [],
                 "value_result": {"availability": "unavailable", "attempted_sources": ["valuation_calc"]}})
    seal(data, plan_hash=plan_hash)
    return data


def test_fixed_collection_validates_type_symbol_hash_and_plan(isolated_store):
    from lib.report_snapshot import validate

    data = _sealed(plan_hash="plan-a")
    cid = isolated_store.save_collection(data)
    record = isolated_store.get_collection(cid)
    assert validate(record, "600176", plan_hash="plan-a") == []
    assert "须提供原 --plan" in "; ".join(validate(record, "600176"))
    assert "标的不一致" in "; ".join(validate(record, "000001"))
    assert "计划哈希不一致" in "; ".join(validate(record, "600176", plan_hash="plan-b"))
    record["raw_json"]["market_structure"]["put_call_ratio"] = 0.826
    assert "内容哈希不一致" in "; ".join(validate(record, "600176"))
    report_id = isolated_store.save_collection(data, kind="report")
    assert "类型须为 collect" in "; ".join(validate(isolated_store.get_collection(report_id), "600176"))


def test_four_fixed_reports_do_not_collect(tmp_path, isolated_store, monkeypatch):
    import invest

    cid = isolated_store.save_collection(_sealed())

    def network(*_args, **_kwargs):
        raise AssertionError("fixed render attempted network collection")

    monkeypatch.setattr(invest.collector, "collect_all", network)
    monkeypatch.setattr(invest.collector, "attach_market_structure", network)
    monkeypatch.setattr(invest.collector, "attach_phase2_extras", network)
    monkeypatch.setattr(invest, "_ensure_render_ready", network)
    for _ in range(4):
        args = invest.build_parser().parse_args([
            "report", "600176", "--collection-id", str(cid),
            "--mode", "brief", "--emit", "html", "--outdir", str(tmp_path),
        ])
        assert invest.cmd_report(args) == 0
    assert isolated_store.get_collection(cid)["raw_json"]["_meta"]["report_input_hash"] == (
        _sealed()["_meta"]["report_input_hash"])


def test_empty_plan_fails_loud(tmp_path):
    import invest
    from argparse import Namespace

    path = tmp_path / "plan.json"
    path.write_text(json.dumps({"modules": []}), encoding="utf-8")
    try:
        invest._dims_from_args(Namespace(plan=str(path), dims="quote"))
    except ValueError as exc:
        assert "modules 为空" in str(exc)
    else:
        raise AssertionError("empty plan silently fell back")


def test_report_ready_collect_seals_pcr_and_value_once(isolated_store, monkeypatch, capsys):
    import invest
    from types import SimpleNamespace

    calls = {"market": 0, "value": 0}
    monkeypatch.setattr(invest.collector, "collect_all", lambda *_a, **_k: _fake_result())
    monkeypatch.setattr(invest.collector, "attach_phase2_extras", lambda *_a: None)
    monkeypatch.setattr(invest.collector, "attach_market_structure", lambda data, _symbol: (
        calls.__setitem__("market", calls["market"] + 1),
        data.__setitem__("market_structure", {
            "put_call_ratio": {"ratio": 0.742, "sample_size": 23, "as_of": "2026-09-28"},
            "availability": {"put_call_ratio": "available"}})))
    import valuation_calc
    monkeypatch.setattr(valuation_calc, "run_valuation", lambda _symbol: (
        calls.__setitem__("value", calls["value"] + 1),
        SimpleNamespace(to_dict=lambda: {"symbol": "600176", "timestamp": "2026-09-29"}))[1])
    monkeypatch.setattr(invest, "_prepare_report_input", invest._prepare_report_input)
    from lib import events, lhb
    monkeypatch.setattr(events, "needs_events_backfill", lambda _data: False)
    monkeypatch.setattr(lhb, "attach_limit_streak_dims", lambda *_a: False)
    args = invest.build_parser().parse_args(["collect", "600176", "--report-ready", "--dims", "basic_info,quote"])
    assert invest.cmd_collect(args) == 0
    record = isolated_store.get_collection(isolated_store.list_collections(limit=1)[0]["id"])
    assert record["raw_json"]["market_structure"]["put_call_ratio"]["ratio"] == 0.742
    assert calls == {"market": 1, "value": 1}
    assert "collection_id=" in capsys.readouterr().err


def test_fact_values_match_sealed_fields():
    from lib.report_snapshot import verify_facts

    collection = _sealed()
    collection["market_structure"]["put_call_ratio"] = {"ratio": 0.742, "sample_size": 23}
    sections = [{"module": "market", "facts": [
        {"id": "F1", "value": 0.742, "source_path": "market_structure.put_call_ratio.ratio"},
        {"id": "F2", "value": 23, "source_path": "market_structure.put_call_ratio.sample_size"},
    ]}]
    assert verify_facts(sections, collection) == []
    sections[0]["facts"][0]["value"] = 0.826
    assert "不一致" in "; ".join(verify_facts(sections, collection))
    del sections[0]["facts"][0]["source_path"]
    assert "缺 source_path" in "; ".join(verify_facts(sections, collection))


def test_fact_tolerance_follows_written_precision_including_exponent():
    """容差取「半个最末位数位」，精度须从**字面量**取，含科学计数法。

    回归：`str(1e-5)` 是 `'1e-05'`（无小数点），按「小数点后位数」数会得 0 →
    容差 0.5，于是 `value: 1e-5` 能对着封存值 `0.49` 通过固定快照的数值门。
    """
    from lib.report_snapshot import verify_facts

    collection = _sealed()

    def _check(sealed: float, written: float) -> list[str]:
        collection["market_structure"]["put_call_ratio"] = {"ratio": sealed}
        sections = [{"module": "market", "facts": [
            {"id": "F1", "value": written,
             "source_path": "market_structure.put_call_ratio.ratio"},
        ]}]
        return verify_facts(sections, collection)

    # 科学计数法按真实精度判：1e-5 与 0.49 差 0.49，远超容差 5e-6
    assert "不一致" in "; ".join(_check(0.49, 1e-5))
    assert "不一致" in "; ".join(_check(0.49, 1.5e-5))
    # 等值的科学计数法仍须通过（容差不是 0）
    assert _check(1e-5, 1e-5) == []

    # 常规量级不受影响：等值与四舍五入通过，真不一致仍被拦
    assert _check(0.742, 0.742) == []
    assert _check(0.7424, 0.742) == []
    assert "不一致" in "; ".join(_check(0.7424, 0.753))

    # 整数字面量仍是 ±0.5 容差（writer 把 4.6 写成 5 不报错）
    assert _check(4.6, 5) == []
    assert "不一致" in "; ".join(_check(4.4, 5))

    # 1e18 量级：旧算法把 '23e+18' 数成 6 位小数 → 容差 5e-7（逐位全等），
    # 等值也会因浮点尾差被误报
    big = 1.2345678901234568e18
    assert _check(big, big) == []


def test_boolean_field_cannot_verify_numeric_fact():
    from lib.report_snapshot import verify_facts

    collection = _sealed()
    for flag, value in ((True, 1), (False, 0)):
        collection["price_shock"]["has_shock"] = flag
        sections = [{"module": "market", "facts": [
            {"id": "F1", "value": value, "source_path": "price_shock.has_shock"},
        ]}]
        assert "布尔值不是数值来源" in "; ".join(verify_facts(sections, collection))


def test_legacy_resume_chooses_latest_collect_even_after_many_reports(isolated_store):
    import invest

    data = _sealed()
    isolated_store.save_collection(data)
    isolated_store.save_pipeline_step("600176", "collect", {"dims": ["quote"]})
    for _ in range(55):
        isolated_store.save_collection(data, kind="report")
    assert invest._try_resume_collection("600176")["_meta"]["report_input_hash"] == (
        data["_meta"]["report_input_hash"])


def test_fixed_input_rejects_uncollected_macro(isolated_store, capsys):
    import invest
    from lib.report_snapshot import seal

    data = _sealed()
    seal(data, options={"dims": ["quote"], "with_macro": False})
    cid = isolated_store.save_collection(data)
    args = invest.build_parser().parse_args([
        "report", "600176", "--collection-id", str(cid), "--with-macro",
    ])
    assert invest.cmd_report(args) == 2
    err = capsys.readouterr().err
    # 报错须用用户实际敲的 flag 形式（--with-macro），并给出修法
    assert "--with-macro" in err
    assert "须重新 collect --report-ready" in err


def test_macro_plan_snapshot_uses_same_dims_for_collect_and_fixed_report(
        tmp_path, isolated_store, monkeypatch):
    import invest
    from lib.planner import generate_plan
    from lib.report_snapshot import seal

    plan = generate_plan("600176", "financials_deep").to_dict()
    assert "kline" not in [module["module_id"] for module in plan["modules"]]
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    collected_dims = []

    def fake_collect(_symbol, dims, **_kwargs):
        collected_dims.extend(dims)
        return _sealed()

    def fake_prepare(data, _symbol, plan_hash, *, with_value, options):
        assert with_value
        return seal(data, plan_hash=plan_hash, options=options)

    monkeypatch.setattr(invest.collector, "collect_all", fake_collect)
    monkeypatch.setattr(invest, "_prepare_report_input", fake_prepare)
    monkeypatch.setattr(invest, "_maybe_store_macro_snapshot", lambda *_args: None)
    monkeypatch.setattr(invest.render, "render", lambda *_args: "ok")
    monkeypatch.setattr(invest.env, "print_missing_token_warnings", lambda: None)
    monkeypatch.setattr(invest, "warn_if_proxy_detected", lambda **_kwargs: None)

    collect_args = invest.build_parser().parse_args([
        "collect", "600176", "--plan", str(path), "--with-macro", "--report-ready",
    ])
    assert invest.cmd_collect(collect_args) == 0
    assert collected_dims.count("kline") == 1

    record = isolated_store.get_collection(isolated_store.list_collections(limit=1)[0]["id"])
    assert record["raw_json"]["_meta"]["report_options"]["dims"] == collected_dims
    report_args = invest.build_parser().parse_args([
        "report", "600176", "--plan", str(path), "--with-macro",
        "--collection-id", str(record["id"]),
    ])
    assert invest._load_fixed_collection(report_args) is not None


def test_hash_survives_store_json_normalization(isolated_store):
    import numpy as np
    from datetime import datetime, timezone
    from lib.report_snapshot import seal, validate

    data = _sealed()
    data["market_structure"]["put_call_ratio"] = {
        "ratio": np.float64(0.742), "as_of": datetime(2026, 9, 28, tzinfo=timezone.utc)}
    seal(data)
    cid = isolated_store.save_collection(data)
    assert validate(isolated_store.get_collection(cid), "600176") == []


def test_planned_snapshot_rejects_report_and_evidence_without_plan(tmp_path, isolated_store, capsys):
    import invest
    from lib.planner import generate_plan

    plan = generate_plan("600176").to_dict()
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    cid = isolated_store.save_collection(_sealed(plan_hash=plan["plan_hash"]))
    report_args = invest.build_parser().parse_args([
        "report", "600176", "--collection-id", str(cid), "--mode", "brief"])
    evidence_args = invest.build_parser().parse_args([
        "evidence", "600176", "--collection-id", str(cid)])
    assert invest.cmd_report(report_args) == 2
    assert invest.cmd_evidence(evidence_args) == 2
    assert "须提供原 --plan" in capsys.readouterr().err
    report_args.plan = str(path)
    evidence_args.plan = str(path)
    assert invest._load_fixed_collection(report_args) is not None
    assert invest._load_fixed_collection(evidence_args) is not None


def test_successful_event_backfill_rebuilds_cards(monkeypatch):
    import invest
    from lib import events, lhb

    data = _sealed()
    data["_meta"]["analysis_cards"] = {"event_classifications": ["stale"]}
    monkeypatch.setattr(events, "needs_events_backfill", lambda _data: True)

    def attach(collection, _symbol, days):
        collection["events"] = [{"date": "2026-09-29", "title": "公告原文", "type": "notice"}]
        collection["_meta"]["events_summary"] = {"count": 1}

    monkeypatch.setattr(events, "attach_events", attach)
    monkeypatch.setattr(lhb, "attach_limit_streak_dims", lambda *_a: False)
    invest._prepare_report_input(data, "600176", None)
    cards = data["_meta"]["analysis_cards"]["event_classifications"]
    assert len(cards) == 1
    assert cards[0]["event_type"] == "notice"
    assert cards[0]["events"][0]["title"] == "公告原文"


def test_preflight_requires_fixed_input_before_collection(monkeypatch, capsys):
    import invest

    monkeypatch.setattr(invest.collector, "collect_all", lambda *_a, **_k: (
        __import__("pytest").fail("preflight collected")))
    args = invest.build_parser().parse_args(["report", "600176", "--preflight"])
    assert invest.cmd_report(args) == 2
    assert "须指定 --collection-id" in capsys.readouterr().err


def test_insight_preflight_checks_insight_artifact_and_sidecars(
        tmp_path, isolated_store, monkeypatch):
    import invest
    import report_qc
    from types import SimpleNamespace

    cid = isolated_store.save_collection(_sealed())
    monkeypatch.setattr(invest.render, "render_report_v3", lambda *_a, **_k: (
        __import__("pytest").fail("used full renderer for Insight")))
    checked = []

    def qc(candidate, **_kwargs):
        assert candidate.name.endswith(".insight.md")
        assert candidate.with_suffix(".facts.json").is_file()
        assert candidate.with_suffix(".insight.json").is_file()
        assert candidate.with_suffix(".report.json").is_file()
        assert candidate.with_suffix(".html").is_file()
        checked.append(candidate.read_text(encoding="utf-8"))
        return SimpleNamespace(overall="PASS")

    monkeypatch.setattr(report_qc, "qc_file", qc)
    monkeypatch.setattr(report_qc, "format_qc_result", lambda _result: "PASS")
    args = invest.build_parser().parse_args([
        "report", "600176", "--collection-id", str(cid), "--mode", "insight",
        "--emit", "html", "--preflight", "--outdir", str(tmp_path)])
    assert invest.cmd_report(args) == 0
    assert checked and "研究要点" in checked[0]
    assert list(tmp_path.rglob("*.md")) == []


# ── 采集合同：plan 声明的依赖必须真的被执行（主方案 §3.1）─────────────────

def test_dependency_contract_names_are_frozen():
    """声明键集与封存状态键集必须一致，且用**字面量**冻结（两处同源算出的「相等」抓不到漂移）。"""
    from lib import planner
    from lib.report_snapshot import DEPENDENCY_STATES, REPORT_DEPENDENCIES

    # 字面量：修改本清单必须同时改采集器与读取侧，属有意为之的破坏性变更
    assert list(REPORT_DEPENDENCIES) == [
        "market_structure", "industry_peers", "pe_band", "events", "limit_streak",
        "benchmark_hs300",
    ]
    assert planner.REPORT_DEPENDENCIES is REPORT_DEPENDENCIES
    assert set(DEPENDENCY_STATES) == {"available", "unavailable", "not_triggered"}
    for name, spec in REPORT_DEPENDENCIES.items():
        assert spec.get("sealed"), f"{name} 未声明封存落点"
        assert isinstance(spec.get("conditional"), bool), f"{name} 未声明是否条件项"


def test_sealed_snapshot_records_dependency_states(isolated_store):
    from lib.report_snapshot import DEPENDENCY_STATES, REPORT_DEPENDENCIES, validate

    cid = isolated_store.save_collection(_sealed())
    record = isolated_store.get_collection(cid)
    states = record["raw_json"]["_meta"]["report_dependencies"]
    assert list(states) == list(REPORT_DEPENDENCIES)
    assert all(value in DEPENDENCY_STATES for value in states.values())
    # 未触发连板 → not_triggered 属合法状态，不是缺口
    assert states["limit_streak"] == "not_triggered"
    # 显式不可得也不阻断读取（渲染层会如实标 ⚠️）
    assert states["pe_band"] == "unavailable"
    assert validate(record, "600176") == []


def test_conditional_dependency_distinguishes_trigger_and_degrade():
    """连板三态：未触发 / 触发且有数据 / 触发但源不可得——三者不得互相代称。"""
    from lib.report_snapshot import dependency_states

    def _with_lhb(status: str) -> dict:
        data = _sealed()
        data["dimensions"].append({"dimension": "lhb", "status": status, "data": {}})
        return data

    assert dependency_states(_with_lhb("available"))["limit_streak"] == "available"
    assert dependency_states(_with_lhb("degraded"))["limit_streak"] == "unavailable"
    assert dependency_states(_sealed())["limit_streak"] == "not_triggered"


def test_validate_rejects_unexecuted_contract(isolated_store, monkeypatch):
    """信封 v2 但依赖状态缺失 = 合同没跑 → 读取侧 fail-loud（不是静默放过）。"""
    from lib import report_snapshot

    # 只让状态收集返回空（哈希仍按实际 _meta 计算，故不会先撞「内容哈希不一致」）
    monkeypatch.setattr(report_snapshot, "dependency_states", lambda _c: {})
    cid = isolated_store.save_collection(_sealed())
    errors = report_snapshot.validate(isolated_store.get_collection(cid), "600176")
    assert any("依赖状态缺失" in e for e in errors), errors


def test_plan_payload_carries_contract_and_mode():
    """plan 的 report_contract 就是采集合同：依赖表同源、模式如实记录、哈希自洽。"""
    from lib.planner import generate_plan
    from lib.report_snapshot import REPORT_DEPENDENCIES, plan_digest

    full = generate_plan("600176", "deep_analysis").to_dict()
    assert full["report_contract"]["dependencies"] == REPORT_DEPENDENCIES
    assert full["report_contract"]["mode"] == "full"
    assert plan_digest(full) == full["plan_hash"]

    brief = generate_plan("600176", "deep_analysis", mode="brief").to_dict()
    assert brief["report_contract"]["mode"] == "brief"
    # 模式是合同的一部分：不同模式不得得到同一个 plan_hash
    assert brief["plan_hash"] != full["plan_hash"]


# ── 零网络与渲染链收敛（主方案 §6.1-3、§3.2）─────────────────────────────

def _stub_all_collection_adapters(monkeypatch):
    """把**所有**采集入口换成「记录 + 抛错」探针，并封 socket。

    返回 `attempted`：调用过任何一个即非空——「零网络」用「没尝试」而不是
    「尝试了但容错」来断言（后者只证明降级路径也能出报告）。
    """
    import socket

    import invest
    import lib.industry.base as industry_base
    from lib import analysis_templates, events, lhb, style_match

    attempted: list[str] = []

    def _trip(name):
        def _probe(*_a, **_k):
            attempted.append(name)
            raise AssertionError(f"固定快照链尝试访问采集入口: {name}")
        return _probe

    # 包命名空间属性（`from lib import collector` 后按属性查找，调用期解析）
    monkeypatch.setattr(invest.collector, "collect_all", _trip("collect_all"))
    monkeypatch.setattr(invest.collector, "attach_market_structure", _trip("attach_market_structure"))
    monkeypatch.setattr(invest.collector, "attach_phase2_extras", _trip("attach_phase2_extras"))
    monkeypatch.setattr(invest, "_ensure_render_ready", _trip("_ensure_render_ready"))
    # 延迟导入的定义模块（函数体内 `from X import y` 按模块属性解析）
    monkeypatch.setattr(events, "attach_events", _trip("attach_events"))
    monkeypatch.setattr(analysis_templates, "build_analysis_cards", _trip("build_analysis_cards"))
    monkeypatch.setattr(lhb, "attach_limit_streak_dims", _trip("attach_limit_streak_dims"))
    monkeypatch.setattr(style_match, "assemble_style_match", _trip("assemble_style_match"))
    # R3 二轮：固定链在「显式 --style 或存在风格档案」时做**渲染色样式重装配**
    # （离线：封存体 + 本地档案）。本测试断言的是「无风格上下文时固定链零派生
    # 入口」，故隔离档案源——装配路径由 test_research_profile 的 R3 用例覆盖。
    monkeypatch.setattr(style_match, "load_style", lambda: None)
    monkeypatch.setattr(industry_base, "get_success_factors", _trip("get_success_factors"))
    import valuation_calc
    monkeypatch.setattr(valuation_calc, "run_valuation", _trip("run_valuation"))

    def _blocked(*_a, **_k):
        attempted.append("socket")
        raise AssertionError("固定快照链尝试建立网络连接")

    monkeypatch.setattr(socket, "socket", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)
    monkeypatch.setattr(socket, "getaddrinfo", _blocked)
    return attempted


def test_fixed_chain_renders_without_any_network(tmp_path, isolated_store, monkeypatch):
    """§6.1-3：封 socket + 全部采集适配器抛错下，四条只读路径仍成功。"""
    import invest

    attempted = _stub_all_collection_adapters(monkeypatch)
    cid = isolated_store.save_collection(_sealed())

    for mode, emit in (("brief", "html"), ("full", "md"), ("insight", "html")):
        args = invest.build_parser().parse_args([
            "report", "600176", "--collection-id", str(cid), "--mode", mode,
            "--emit", emit, "--outdir", str(tmp_path / mode),
        ])
        assert invest.cmd_report(args) == 0, mode
    evidence_args = invest.build_parser().parse_args(
        ["evidence", "600176", "--collection-id", str(cid)])
    assert invest.cmd_evidence(evidence_args) == 0

    assert attempted == [], f"固定快照链发生网络访问: {attempted}"


def test_dcf_beta_prefers_sealed_benchmark(monkeypatch):
    """渲染链唯一的真联网点（沪深300 基准）必须优先取封存序列，且数值不变。"""
    from lib import render_dcf
    from lib import collector as collector_pkg

    # 序列须有真实波动：完全线性的收益率会让市场方差 < 1e-12（calc_beta 会判为零方差）
    kline = [{"trade_date": f"2026-01-{d:02d}", "close": 10 + (1 if d % 2 else -1) * d * 0.3}
             for d in range(1, 25)]
    series = [[f"2026-01-{d:02d}", 3000 + (1 if d % 3 else -1) * d * 2.0]
              for d in range(1, 25)]

    def _must_not_fetch(*_a, **_k):
        raise AssertionError("有封存基准时不得联网抓取沪深300")

    # 模块对象形式：私有名不得用字符串目标（见 test_test_hygiene）
    monkeypatch.setattr(collector_pkg, "_akshare_hs300_dated_closes", _must_not_fetch)
    sealed = render_dcf._dcf_compute_beta(kline, benchmark={"closes": series})
    assert sealed["is_default"] is False
    assert "封存基准序列" in sealed["source"]

    # 无封存值才回落现场抓取（旧 --resume 链行为不变），数值应与封存口径一致
    # 现场路径的源返回 YYYYMMDD（封存路径按同一口径归一），故此处也归一
    monkeypatch.setattr(collector_pkg, "_akshare_hs300_dated_closes",
                        lambda **_k: [(d.replace("-", ""), c) for d, c in series])
    live = render_dcf._dcf_compute_beta(kline)
    assert live["beta"] == sealed["beta"]


def test_sealed_collection_carries_benchmark_series(isolated_store, monkeypatch):
    """`collect --report-ready` 必须把基准序列一起封存（否则重渲仍需联网）。"""
    import invest
    from types import SimpleNamespace
    from lib import collector as collector_pkg

    def collect_with_kline(*_args, **_kwargs):
        data = _fake_result()
        data["dimensions"].append({
            "dimension": "kline", "status": "available", "_meta": {},
            "data": [{"trade_date": f"202601{day:02d}", "close": 10 + day}
                     for day in range(1, 13)],
        })
        return data

    monkeypatch.setattr(collector_pkg, "collect_all", collect_with_kline)
    monkeypatch.setattr(collector_pkg, "attach_phase2_extras", lambda *_a, **_k: None)
    monkeypatch.setattr(collector_pkg, "attach_market_structure",
                        lambda data, _s: data.__setitem__("market_structure", {}))
    monkeypatch.setattr(collector_pkg, "_akshare_hs300_dated_closes",
                        lambda **_k: [("20260102", 3000.0), ("20260105", 3010.0)])
    from lib import analysis_templates, events, lhb
    monkeypatch.setattr(events, "needs_events_backfill", lambda _d: False)
    monkeypatch.setattr(analysis_templates, "build_analysis_cards", lambda _d: None)
    monkeypatch.setattr(lhb, "attach_limit_streak_dims", lambda *_a: False)
    import valuation_calc
    monkeypatch.setattr(valuation_calc, "run_valuation",
                        lambda _s: SimpleNamespace(to_dict=lambda: {"symbol": "600176"}))

    args = invest.build_parser().parse_args(
        ["collect", "600176", "--report-ready", "--dims", "basic_info,quote"])
    assert invest.cmd_collect(args) == 0
    raw = isolated_store.get_collection(
        isolated_store.list_collections(limit=1)[0]["id"])["raw_json"]
    bench = raw["market_structure"]["benchmark_hs300"]
    assert bench["availability"] == "available"
    assert bench["closes"] == [["20260102", 3000.0], ["20260105", 3010.0]]


def test_benchmark_fetch_skipped_without_beta_eligible_kline(monkeypatch):
    import invest
    from lib import collector as collector_pkg
    from lib.report_snapshot import dependency_states

    monkeypatch.setattr(collector_pkg, "_akshare_hs300_dated_closes",
                        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("network called")))
    for rows in (None, [{"trade_date": "20260102", "close": 10.0}] * 12):
        data = {"market_structure": {}, "dimensions": []}
        if rows is not None:
            data["dimensions"].append({"dimension": "kline", "data": rows})
        invest._seal_benchmark_series(data)
        bench = data["market_structure"]["benchmark_hs300"]
        assert bench["availability"].startswith("unavailable:")
        assert bench["attempted_sources"] == []
        assert dependency_states(data)["benchmark_hs300"] == "unavailable"


# ── 快照一致性（主方案 §6.1-4）───────────────────────────────────────────

def test_interleaved_collect_does_not_switch_snapshot(isolated_store, monkeypatch):
    """异构 run 穿插：同标的再次 collect 之后，按 ID 读取仍取原快照。"""
    import invest

    first = _sealed()
    first_id = isolated_store.save_collection(first)
    second = _sealed()
    second["market_structure"] = {"put_call_ratio": {"ratio": 0.999}}
    from lib.report_snapshot import seal
    seal(second)
    second_id = isolated_store.save_collection(second)
    assert second_id != first_id

    monkeypatch.setattr(invest, "_HAS_STORE", True)
    monkeypatch.setattr(invest, "store_mod", isolated_store)
    seen: dict = {}

    def _spy_render(collection, *_a, **k):
        seen["ratio"] = collection["market_structure"].get("put_call_ratio")
        return "ok"

    monkeypatch.setattr(invest.render, "render", _spy_render)
    args = invest.build_parser().parse_args(
        ["report", "600176", "--collection-id", str(first_id), "--mode", "brief"])
    assert invest.cmd_report(args) == 0
    assert seen["ratio"] is None  # 取的是 #first（不可得形状），不是后来的 0.999


def test_corrupt_snapshot_fails_loud_without_live_collection(isolated_store, monkeypatch):
    """坏快照必须 fail-loud，不得静默回退现场采集。"""
    import invest

    cid = isolated_store.save_collection(_sealed())
    row = isolated_store.get_collection(cid)
    row["raw_json"]["symbol"] = "000001"          # 正文标的与行标的不一致

    def _tampered(_collection_id):
        return row

    attempted: list[str] = []
    monkeypatch.setattr(invest, "_HAS_STORE", True)
    monkeypatch.setattr(invest, "store_mod", isolated_store)
    monkeypatch.setattr(invest.store_mod, "get_collection", _tampered)
    monkeypatch.setattr(invest.collector, "collect_all",
                        lambda *_a, **_k: attempted.append("collect_all"))
    args = invest.build_parser().parse_args(
        ["report", "600176", "--collection-id", str(cid), "--mode", "brief"])
    assert invest.cmd_report(args) == 2
    assert attempted == []


def test_unavailable_source_keeps_state_and_attempted_sources(isolated_store):
    """部分源不可得：状态封存 + attempted_sources 保留（不得静默丢弃）。"""
    from lib.report_snapshot import dependency_states

    data = _sealed()
    data["industry_peers"] = {"peers": [], "availability": "unavailable",
                              "attempted_sources": ["collect_all.phase2"]}
    states = dependency_states(data)
    assert states["industry_peers"] == "unavailable"
    assert data["industry_peers"]["attempted_sources"] == ["collect_all.phase2"]


def test_report_ready_collect_rejects_resume_and_no_store(capsys):
    """边界约束：--report-ready 须落库、不可与 --resume 合用。"""
    import invest

    for extra in (["--no-store"], ["--resume"]):
        args = invest.build_parser().parse_args(
            ["collect", "600176", "--report-ready", *extra])
        assert invest.cmd_collect(args) == 2
        assert "report-ready" in capsys.readouterr().err


# ── 合成前可见性与 trace（主方案 §3.3-3/4、§5 P0）────────────────────────

def test_first_render_prints_path_index_and_writing_constraints(
        tmp_path, isolated_store, capsys):
    import invest

    data = _sealed()
    data["market_structure"] = {"put_call_ratio": {"ratio": 0.742, "sample_size": 23}}
    from lib.report_snapshot import seal
    seal(data)
    cid = isolated_store.save_collection(data)
    args = invest.build_parser().parse_args([
        "report", "600176", "--collection-id", str(cid), "--mode", "brief",
        "--emit", "md", "--outdir", str(tmp_path)])
    assert invest.cmd_report(args) == 0
    err = capsys.readouterr().err
    assert "事实路径索引" in err
    assert "market_structure.put_call_ratio.ratio = 0.742" in err
    assert "写作约束" in err
    # 分位须附中位数是 warning 级——只筛 error 会漏掉它
    assert "percentile-without-median" in err


def test_trace_records_stages_without_prose(tmp_path, isolated_store, monkeypatch):
    import invest

    trace = tmp_path / "trace.jsonl"
    monkeypatch.setenv("INVEST_TRACE_FILE", str(trace))
    monkeypatch.setenv("INVEST_RUN_ID", "run-test")
    cid = isolated_store.save_collection(_sealed())
    monkeypatch.setattr(sys, "argv", [
        "invest.py", "report", "600176", "--collection-id", str(cid), "--mode", "brief",
        "--emit", "md", "--outdir", str(tmp_path / "out")])
    assert invest.main() == 0

    rows = [json.loads(line) for line in trace.read_text(encoding="utf-8").splitlines()]
    assert rows, "trace 未产出任何行"
    assert {row["run_id"] for row in rows} == {"run-test"}
    stages = {row["stage"] for row in rows}
    assert {"report", "final_render"}.issubset(stages)
    assert "network" in stages, "缺少网络可观测性行"
    network = next(row for row in rows if row["stage"] == "network")
    assert "by_api" in network
    # 只记计数与时点，不得夹带正文/Token
    for row in rows:
        assert all(not isinstance(value, str) or len(value) < 200 for value in row.values())


def test_trace_failure_does_not_break_command(tmp_path, isolated_store, monkeypatch, capsys):
    """可观测性不得让业务挂掉：trace 路径不可写时命令照常执行。"""
    import invest

    monkeypatch.setenv("INVEST_TRACE_FILE", str(tmp_path / "missing-dir" / "t.jsonl"))
    cid = isolated_store.save_collection(_sealed())
    monkeypatch.setattr(sys, "argv", [
        "invest.py", "report", "600176", "--collection-id", str(cid), "--mode", "brief",
        "--emit", "md", "--outdir", str(tmp_path / "out")])
    assert invest.main() == 0
    assert "trace 写入失败" in capsys.readouterr().err


def test_evidence_dims_match_collect_contract(isolated_store, monkeypatch):
    """D2 回归：evidence 的 dims 必须与 collect 落库口径同源。

    曾漏接 `_collection_dims_from_args` → `--with-macro --from-store` 时判「维度不一致」，
    静默转现场重采，把省流优化吃掉。
    """
    import invest

    seen: dict = {}
    monkeypatch.setattr(invest, "_HAS_STORE", True)
    monkeypatch.setattr(invest, "store_mod", isolated_store)
    monkeypatch.setattr(invest, "_try_resume_collection", lambda _s: _sealed())

    def _spy_compatible(_args, dims, _cached):
        seen["dims"] = list(dims)
        return True

    monkeypatch.setattr(invest, "_resume_cache_compatible", _spy_compatible)
    monkeypatch.setattr(invest.render, "render", lambda *_a, **_k: "ok")
    evidence_args = invest.build_parser().parse_args(
        ["evidence", "600176", "--with-macro", "--dims", "quote", "--from-store"])
    assert invest.cmd_evidence(evidence_args) == 0

    collect_args = invest.build_parser().parse_args(
        ["collect", "600176", "--with-macro", "--dims", "quote"])
    assert seen["dims"] == invest._collection_dims_from_args(collect_args) == ["quote", "kline"]


# ── 审查回归（2026-09-30）：索引可被校验器读、不可得标记不得谎报 ──────────

def test_fact_path_index_paths_are_consumable_by_verifier():
    """索引打印的**每条**路径都必须能被 `verify_facts` 读出来。

    此前索引把数组下标写成 `data[0]`，而校验器按点切分并对 list 取 `int(part)`——
    照索引填写会被判「无法读取数值」。此用例把两处词汇钉在一起，防止再次漂移。
    """
    import invest
    from lib.report_snapshot import verify_facts

    data = _sealed()
    data["dimensions"].append({
        "dimension": "kline",
        "data": [{"close": 10.5, "vol": 1000}, {"close": 11.0, "vol": 1200}],
        "status": "available",
    })
    data["value_result"] = {"pe_ttm": 15.6, "nested": {"x": 1.5}}

    indexed = []
    for line in invest._fact_path_index(data):
        path, _, value = line.rpartition(" = ")
        indexed.append((path, value))
    assert indexed, "索引为空"
    assert not any("[" in path for path, _ in indexed), [p for p, _ in indexed if "[" in p]

    sections = [{"module": "m", "facts": [
        {"id": f"F{i}", "value": float(value), "source_path": path}
        for i, (path, value) in enumerate(indexed)
    ]}]
    assert verify_facts(sections, data) == []


def test_dependency_state_treats_empty_and_suffixed_markers_as_unavailable():
    """`empty` / `unavailable: <原因>` / `not_requested` 都不是「有数据」。"""
    from lib.report_snapshot import dependency_states

    data = _sealed()
    for marker in ("empty", "unavailable: 抓取失败", "not_requested"):
        data["market_structure"]["benchmark_hs300"] = {"availability": marker, "closes": []}
        assert dependency_states(data)["benchmark_hs300"] == "unavailable", marker

    data["market_structure"]["benchmark_hs300"] = {
        "availability": "available", "closes": [["20260102", 3000.0]]}
    assert dependency_states(data)["benchmark_hs300"] == "available"


# ── 审查回归（2026-09-30 第二批）─────────────────────────────────────────

def test_events_backfill_runs_at_prepare_not_render(monkeypatch):
    """events 瞬时失败须在采集/准备期重试（渲染已零网络，不再兜底）。"""
    from lib import analysis_templates, collector, events

    calls: list[int] = []

    def _attach(collection, _symbol, days=0):
        calls.append(days)
        collection["events"] = [{"date": "2026-01-02"}]

    monkeypatch.setattr(events, "needs_events_backfill", lambda _c: True)
    monkeypatch.setattr(events, "attach_events", _attach)
    monkeypatch.setattr(analysis_templates, "build_analysis_cards", lambda _c: None)

    data = _sealed()
    assert collector.attach_events_for_report(data, "600176", deep=False) is True
    assert calls == [30] and data["events"]

    assert collector.attach_events_for_report(_sealed(), "600176", deep=True) is True
    assert calls[-1] == 90

    # 回填失败：记因不抛（不阻断采集），且不再重试
    def _boom(*_a, **_k):
        raise RuntimeError("源不可得")

    monkeypatch.setattr(events, "attach_events", _boom)
    failed = _sealed()
    assert collector.attach_events_for_report(failed, "600176") is False
    assert "源不可得" in failed["_meta"]["events_error"]

    # 无需回填时是零动作
    monkeypatch.setattr(events, "needs_events_backfill", lambda _c: False)
    assert collector.attach_events_for_report(_sealed(), "600176") is False


def test_render_ready_backfills_events(monkeypatch):
    """恢复路径（_ensure_render_ready）同样承担 events 回填。"""
    import invest

    seen: list[str] = []
    monkeypatch.setattr(invest.collector, "attach_market_structure",
                        lambda *_a: None)
    monkeypatch.setattr(invest.collector, "attach_phase2_extras", lambda *_a: None)
    monkeypatch.setattr(invest.collector, "attach_events_for_report",
                        lambda *_a, **_k: seen.append("events") or True)
    invest._ensure_render_ready({"market_structure": {}, "dimensions": []}, "600176")
    assert seen == ["events"]


def test_fixed_render_does_not_mutate_sealed_payload(isolated_store, monkeypatch, tmp_path):
    """封存本体在被校验之后不得被改写（否则哈希与内容不再自洽）。"""
    import invest
    from lib.report_snapshot import validate

    cid = isolated_store.save_collection(_sealed())
    seen: dict = {}

    def _spy_render(collection, *_a, **_k):
        seen["market_structure_keys"] = set(collection["market_structure"])
        return "ok"

    monkeypatch.setattr(invest.render, "render", _spy_render)
    args = invest.build_parser().parse_args([
        "report", "600176", "--collection-id", str(cid), "--mode", "brief",
        "--emit", "md", "--outdir", str(tmp_path)])
    assert invest.cmd_report(args) == 0
    assert "benchmark_hs300" not in seen["market_structure_keys"]
    # 渲染后重新校验：内容哈希仍与封存值一致
    assert validate(isolated_store.get_collection(cid), "600176") == []


def test_rate_limit_stats_survive_client_release(monkeypatch):
    """统计须与客户端生命周期解耦：worker/函数内局部客户端被回收后仍要计入。"""
    import gc

    from lib import tushare_client as tc

    before = tc.rate_limit_stats()
    client = tc.TushareClient(token="dummy-token")
    client._wait_for_rate_limit("daily")          # 获准：应记录生效预算（未等待）
    client._note_outcome("daily", None, crashed=False)
    api = tc.rate_limit_stats()["by_api"].get("daily", {})
    assert api.get("calls", 0) - before["by_api"].get("daily", {}).get("calls", 0) >= 1
    # 未发生等待也要有预算（此前只在被限流时才写，零等待运行会显示 0）
    assert api.get("budget_per_minute", 0) > 0
    del client
    gc.collect()
    after = tc.rate_limit_stats()["by_api"].get("daily", {})
    assert after["calls"] == api["calls"]
    assert after["budget_per_minute"] == api["budget_per_minute"]


def test_fixed_json_emit_stays_self_consistent(isolated_store, capsys):
    """审查场景原样复现：`--emit json` 的输出必须与自带的 report_input_hash 自洽。

    此前 `_load_fixed_collection` 在哈希校验之后给封存体注入基准占位键，于是
    `--emit json` 打出的载荷重算哈希与随附的 `report_input_hash` 不再相等。
    """
    import invest
    from lib.report_snapshot import digest

    cid = isolated_store.save_collection(_sealed())
    args = invest.build_parser().parse_args([
        "report", "600176", "--collection-id", str(cid), "--emit", "json"])
    capsys.readouterr()  # 丢弃此前的 stderr 提示
    assert invest.cmd_report(args) == 0
    stdout = capsys.readouterr().out
    payload = json.loads(stdout[stdout.index("{"):])

    meta = payload["_meta"]
    recomputed = digest({k: v for k, v in payload.items() if k != "_meta"} | {
        "_meta": {k: v for k, v in meta.items() if k != "report_input_hash"}})
    assert recomputed == meta["report_input_hash"]
    assert "benchmark_hs300" not in (payload.get("market_structure") or {})


# ── 审查回归（2026-09-30 第三批）：三态不得被「全失败但形状正常」骗过 ──────

def test_all_failed_market_structure_is_unavailable():
    """无 token 时 market_structure 是**逐因子** unavailable 字典，不是整体标记。

    形状照抄 `collect_market_structure` 的真实产出：`_ms_try_fetch` 在 `finally`
    中**无条件**写 `latency_ms`（失败路径也写），所以「全源失败」的 payload 里
    仍有非空的时序元数据——它不得被读成「有数据的因子」。
    """
    from lib.report_snapshot import dependency_states

    data = _sealed()
    data["market_structure"] = {
        "put_call_ratio": None, "erp": {}, "short_margin": None,
        "availability": {
            "put_call_ratio": "unavailable: TUSHARE_TOKEN not configured",
            "erp": "unavailable: TUSHARE_TOKEN not configured",
            "short_margin": "unavailable: TUSHARE_TOKEN not configured",
        },
        "attempted_sources": ["collect_market_structure"],
        "latency_ms": {"put_call_ratio": 3, "erp": 5, "short_margin": 2},
    }
    assert dependency_states(data)["market_structure"] == "unavailable"

    # 单独回退：只有时序元数据、无任何因子数据 → 仍是不可得（回归用例：
    # 此前 latency_ms 被当作因子数据，把全源失败封成 available）
    data["market_structure"] = {
        "availability": {}, "latency_ms": {"put_call_ratio": 3},
    }
    assert dependency_states(data)["market_structure"] == "unavailable"

    data["market_structure"]["availability"] = {}
    assert dependency_states(data)["market_structure"] == "unavailable"
    data["market_structure"]["availability"] = {"erp": "available"}
    assert dependency_states(data)["market_structure"] == "unavailable"
    data["market_structure"]["availability"] = {
        "put_call_ratio": "unavailable", "erp": "unavailable", "short_margin": "unavailable"}

    # 部分源可用 → 整体仍算可用。三种健康标记都要认：
    # "available"、"available (akshare fallback; …)"、"partial: …"（降级成功态）
    for healthy in ("available",
                    "available (akshare fallback; Tushare sw_daily 需 5000 积分)",
                    "partial: 10 aligned days (min 8)"):
        data["market_structure"]["availability"]["erp"] = healthy
        data["market_structure"]["erp"] = {"dgs10": 4.2}
        assert dependency_states(data)["market_structure"] == "available", healthy
    data["market_structure"]["availability"]["erp"] = (
        "unavailable: index_dailybasic unavailable")
    assert dependency_states(data)["market_structure"] == "unavailable"


def test_all_failed_event_legs_mark_events_unavailable():
    """events 为空数组时须看逐腿三态：公告腿未给结论是失败，empty 才是事实。

    腿名照抄 `attach_events` 的真实产出（`notice` / `dividend` / `holder_change`）——
    此前本测试用的是生产中不存在的 `announcement`，于是「公告腿 failed + 两条
    辅助腿 empty」这条真实形状从未被覆盖（判据已改为复用 `needs_events_backfill`）。
    """
    from lib.report_snapshot import dependency_states

    data = _sealed()
    data["events"] = []
    data["_meta"]["events_legs"] = {
        "notice": "failed", "dividend": "failed", "holder_change": "failed"}
    assert dependency_states(data)["events"] == "unavailable"

    # 回归用例（C4）：公告腿挂掉 + 两条辅助腿**合法空表**，不得读成「窗口内无公告」
    # ——辅助腿只覆盖很窄的公告类型，空表不能替代一手核验
    data["_meta"]["events_legs"] = {
        "notice": "failed", "dividend": "empty", "holder_change": "empty"}
    assert dependency_states(data)["events"] == "unavailable"

    # 公告腿缺结论（旧格式/未跑）同样不算事实
    data["_meta"]["events_legs"] = {"dividend": "empty"}
    assert dependency_states(data)["events"] == "unavailable"

    # 公告腿已应答 + summary 存在 → 空数组是事实（attach_events 必写 events_summary）
    data["_meta"]["events_summary"] = {"total": 0}
    data["_meta"]["events_legs"] = {"notice": "ok", "dividend": "empty"}
    assert dependency_states(data)["events"] == "available"

    data["_meta"]["events_legs"] = {"notice": "empty", "dividend": "empty"}
    assert dependency_states(data)["events"] == "available"

    # 公告腿已应答但缺 events_summary → 采集未完成，不是事实
    data["_meta"].pop("events_summary", None)
    assert dependency_states(data)["events"] == "unavailable"


def test_strict_rigor_json_emit_stays_self_consistent(isolated_store, capsys):
    """`--strict-rigor` 不得在哈希校验后改写封存体（审查场景原样复现）。"""
    import invest
    from lib.report_snapshot import digest

    cid = isolated_store.save_collection(_sealed())
    args = invest.build_parser().parse_args([
        "report", "600176", "--collection-id", str(cid), "--strict-rigor",
        "--emit", "json"])
    capsys.readouterr()
    assert invest.cmd_report(args) == 0
    stdout = capsys.readouterr().out
    payload = json.loads(stdout[stdout.index("{"):])

    meta = payload["_meta"]
    assert "strict_rigor" not in meta
    recomputed = digest({k: v for k, v in payload.items() if k != "_meta"} | {
        "_meta": {k: v for k, v in meta.items() if k != "report_input_hash"}})
    assert recomputed == meta["report_input_hash"]


def test_strict_rigor_reaches_renderer_without_touching_meta():
    """选项要真的下传到渲染器（不能因为不改集合而静默失效）。

    回退读法（`_meta.strict_rigor`）由既有契约测试覆盖（test_render_extras.py
    的 brief/full 两条），此处只锁「显式入参一路下传」。
    """
    from lib.render_markdown import _concise, _v2
    import lib.render_markdown._concise as concise_mod

    seen: list[tuple[str, bool]] = []

    def _spy_v3(collection, symbol, mode="full", *, analysis=None, profile=None,
                strict_rigor=None):
        seen.append(("v3", bool(strict_rigor)))
        return "ok"

    def _spy_val(dims, collection=None, *, strict_rigor=None):
        seen.append(("valuation", bool(strict_rigor)))
        return ""

    collection = _fake_result()   # 需要 dimensions/summary 才能走 compact 的真实链路
    orig_v3, orig_val = concise_mod.render_report_v3, _v2.render_valuation_section
    concise_mod.render_report_v3 = _spy_v3
    _v2.render_valuation_section = _spy_val
    try:
        # md 路径 → render_report_v3
        assert _v2.render(collection, "600176", "md", strict_rigor=True) == "ok"
        assert seen == [("v3", True)]
        # compact 路径 → render_report_v2 → render_valuation_section
        seen.clear()
        _v2.render(collection, "600176", "compact", strict_rigor=True)
        assert ("valuation", True) in seen
    finally:
        concise_mod.render_report_v3 = orig_v3
        _v2.render_valuation_section = orig_val


def test_cmd_report_passes_strict_rigor_to_renderer(tmp_path, isolated_store, monkeypatch):
    """端到端：`--strict-rigor` 必须到渲染器（不能因「不改集合」而静默丢选项）。"""
    import invest

    captured: dict = {}

    def _spy_render(_collection, _symbol, _fmt, **kwargs):
        captured.update(kwargs)
        return "ok"

    monkeypatch.setattr(invest.render, "render", _spy_render)
    cid = isolated_store.save_collection(_sealed())
    on = invest.build_parser().parse_args([
        "report", "600176", "--collection-id", str(cid), "--mode", "brief",
        "--strict-rigor", "--emit", "md", "--outdir", str(tmp_path / "on")])
    assert invest.cmd_report(on) == 0
    assert captured.get("strict_rigor") is True

    off = invest.build_parser().parse_args([
        "report", "600176", "--collection-id", str(cid), "--mode", "brief",
        "--emit", "md", "--outdir", str(tmp_path / "off")])
    assert invest.cmd_report(off) == 0
    assert captured.get("strict_rigor") is False
