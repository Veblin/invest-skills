"""C7g collect_all 抽取块的契约回归（code-review 2026-08-22 #1/#6/#11）。

- #1：events import/调用失败均 non-fatal — 附属组件链断裂时跳过 events、
  meta["deep"] 照常绑定，不因 import 失败丢弃已采集结果（report_qc 对
  collect_all 是泛化 except，不依赖此处 raise）
- #6：dims=[] 视同 None 填默认维度（修复前 ThreadPoolExecutor max_workers=0 崩溃）
- #11：_collect_industry_pricing_block 与兄弟 helper 对齐「返回 + 外层赋值」
"""
import sys


def test_attach_events_import_failure_non_fatal(monkeypatch):
    """lib.events import 失败 → 不抛异常，meta 已绑定，events 跳过。"""
    from lib.collector import _orchestrate as orch

    monkeypatch.setitem(sys.modules, "lib.events", None)
    result = {}
    orch._attach_events_block(result, "600176", False)
    assert result["_meta"]["deep"] is False
    assert "events" not in result


def test_attach_events_call_failure_non_fatal(monkeypatch):
    """attach_events 调用失败（import 成功）→ 警告继续，meta 已绑定。"""
    from lib.collector import _orchestrate as orch
    import lib.events as events_mod

    def boom(result, symbol, days=30):
        raise RuntimeError("events boom")

    monkeypatch.setattr(events_mod, "attach_events", boom)
    result = {}
    orch._attach_events_block(result, "600176", False)
    assert result["_meta"]["deep"] is False
    assert "events" not in result


def test_attach_manifest_generation_failure_non_fatal(monkeypatch):
    """generate_manifest 失败 → manifest=None 降级（旧行为保持）。"""
    from lib.collector import _orchestrate as orch
    import lib.manifest as manifest_mod

    def boom(result):
        raise RuntimeError("manifest boom")

    monkeypatch.setattr(manifest_mod, "generate_manifest", boom)
    result = {}
    orch._attach_manifest_block(result)
    assert result["_meta"]["manifest"] is None


def test_collect_all_empty_dims_uses_defaults(monkeypatch):
    """#6: collect_all(dims=[]) 视同 None 填默认维度（修复前 fanout max_workers=0 崩溃）。"""
    from lib.collector import _DEFAULT_DIMS, collect_all
    from lib.collector import _orchestrate as orch

    seen = {}

    def fake_fanout(symbol, dims, kline_kwargs):
        seen["dims"] = dims
        return {}

    # 下游块全部 stub（本测试只验证入口归一化，不走网络）
    monkeypatch.setattr(orch, "_collect_dims_fanout", fake_fanout)
    monkeypatch.setattr(orch, "_collect_industry_pricing_block", lambda *a, **k: None)
    monkeypatch.setattr(orch, "_order_dimensions", lambda dims, dr: [])
    monkeypatch.setattr(orch, "_fuse_dimensions", lambda dims, s: {})
    monkeypatch.setattr(orch, "_score_credibility", lambda dims, s: {})
    monkeypatch.setattr(orch, "_collect_macro_context_block", lambda s, m: {})
    monkeypatch.setattr(orch, "_collect_chain_context_block", lambda s, c, dr: {})
    monkeypatch.setattr(orch, "_attach_sector_sync_block", lambda *a, **k: None)
    monkeypatch.setattr(orch, "_attach_phase2_block", lambda *a, **k: None)
    monkeypatch.setattr(orch, "_attach_events_block", lambda *a, **k: None)
    monkeypatch.setattr(orch, "_attach_analysis_cards_block", lambda *a, **k: None)
    monkeypatch.setattr(orch, "_attach_manifest_block", lambda *a, **k: None)
    monkeypatch.setattr(orch, "_attach_news_pack_block", lambda *a, **k: None)

    result = collect_all("600000", dims=[])
    assert seen["dims"] == list(_DEFAULT_DIMS)
    assert result["dimensions"] == []


def test_collect_dims_fanout_empty_no_crash():
    """#2: _collect_dims_fanout 空任务直接返回 {}（修复前 max_workers=0 ValueError）。

    review #2：guard 原只在 collect_all，helper 自身的空输入契约须真实执行验证
    （不走 stub），防止未来直接调用方踩崩。
    """
    from lib.collector import _orchestrate as orch

    out = orch._collect_dims_fanout("600000", [], {})
    assert out == {}


def test_industry_pricing_block_returns_result(monkeypatch):
    """#11: helper 返回结果、不再原地变异 dim_results（与兄弟 helper 形状一致）。"""
    from lib.collector import _orchestrate as orch

    monkeypatch.setattr(orch, "_resolve_industry_for_pricing", lambda s, dr: "水泥")
    monkeypatch.setattr(orch, "collect_industry_pricing",
                        lambda s, ind: {"dimension": "industry_pricing", "data": ind})
    dim_results = {}
    out = orch._collect_industry_pricing_block(
        "600176", ["industry_pricing", "basic_info"], dim_results)
    assert out == {"dimension": "industry_pricing", "data": "水泥"}
    assert "industry_pricing" not in dim_results


def test_industry_pricing_block_failure_skeleton(monkeypatch):
    """采集异常 → 返回 status=missing 骨架（不再写入 dim_results）。"""
    from lib.collector import _orchestrate as orch

    monkeypatch.setattr(orch, "_resolve_industry_for_pricing", lambda s, dr: "水泥")

    def boom(symbol, industry):
        raise RuntimeError("pricing boom")

    monkeypatch.setattr(orch, "collect_industry_pricing", boom)
    dim_results = {}
    out = orch._collect_industry_pricing_block("600176", ["industry_pricing"], dim_results)
    assert out["status"] == "missing"
    assert "pricing boom" in out["error"]
    assert "industry_pricing" not in dim_results


def test_industry_pricing_block_not_requested_returns_none():
    """未请求 industry_pricing → 返回 None 且不动 dim_results。"""
    from lib.collector import _orchestrate as orch

    dim_results = {}
    out = orch._collect_industry_pricing_block("600176", ["basic_info"], dim_results)
    assert out is None
    assert dim_results == {}
