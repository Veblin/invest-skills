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


def test_northbound_stale_guard_degrades_old_data(monkeypatch):
    """P0-1：北向记录停在两年前（2024-08）→ net_sum_10d 置 None + stale 标注。

    2024-08 起北向个股披露规则变更，hsgt_top10 停更。守卫不得把两年前
    的净额当「近 10 日」参与 CV-4 印证；records 保留供追溯。
    """
    from lib.collector import _orchestrate as orch
    from datetime import datetime, timezone

    # 6 条 ≥ _MIN_NORTHBOUND_DAYS(5)：守卫必须在数量守卫之后仍拦截陈旧数据
    old_records = [
        {"trade_date": "20240811", "net_mf_amount": 100.0},
        {"trade_date": "20240812", "net_mf_amount": 200.0},
        {"trade_date": "20240813", "net_mf_amount": 300.0},
        {"trade_date": "20240814", "net_mf_amount": 400.0},
        {"trade_date": "20240815", "net_mf_amount": 500.0},
        {"trade_date": "20240816", "net_mf_amount": 600.0},
    ]

    def fake_recent(records, *, limit):
        return sorted(records, key=lambda r: str(r.get("trade_date", "")), reverse=True)[:limit]

    monkeypatch.setattr(orch, "_hsgt_top10_cached", lambda symbol: old_records)
    monkeypatch.setattr(orch, "_recent_flow_records", fake_recent)
    monkeypatch.setattr(orch, "_q_akshare_northbound", lambda symbol: None)

    out = orch._ms_fetch_northbound_stock(None, "600176")
    assert out is not None
    assert out["net_sum_10d"] is None
    assert out["stale"] is True
    assert out["latest_trade_date"] == "20240816"
    assert "停更" in out["staleness_note"]
    # 原始记录保留（供追溯），但净额不可用
    assert len(out["records"]) == 6


def test_northbound_stale_guard_keeps_fresh_data(monkeypatch):
    """P0-1 反例：最新记录距今 ≤90 天 → 净额照常可用。"""
    from lib.collector import _orchestrate as orch
    from datetime import datetime, timezone

    from datetime import timedelta
    fresh = datetime.now(timezone.utc).strftime("%Y%m%d")
    # 6 条：5 条近期 + 1 条基准，全部 ≥ _MIN_NORTHBOUND_DAYS
    records = [
        {"trade_date": (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y%m%d"), "net_mf_amount": 100.0},
        {"trade_date": (datetime.now(timezone.utc) - timedelta(days=20)).strftime("%Y%m%d"), "net_mf_amount": 200.0},
        {"trade_date": (datetime.now(timezone.utc) - timedelta(days=10)).strftime("%Y%m%d"), "net_mf_amount": 300.0},
        {"trade_date": (datetime.now(timezone.utc) - timedelta(days=5)).strftime("%Y%m%d"), "net_mf_amount": 400.0},
        {"trade_date": (datetime.now(timezone.utc) - timedelta(days=2)).strftime("%Y%m%d"), "net_mf_amount": 500.0},
        {"trade_date": fresh, "net_mf_amount": 600.0},
    ]

    monkeypatch.setattr(orch, "_hsgt_top10_cached", lambda symbol: records)
    monkeypatch.setattr(orch, "_recent_flow_records",
                        lambda r, *, limit: sorted(r, key=lambda x: str(x.get("trade_date", "")))[-limit:])
    monkeypatch.setattr(orch, "_q_akshare_northbound", lambda symbol: None)

    out = orch._ms_fetch_northbound_stock(None, "600176")
    assert out is not None
    assert out["net_sum_10d"] == 2100.0
    assert out.get("stale") is None


def test_northbound_stale_guard_akshare_fallback_also_degrades(monkeypatch):
    """P0-1：tushare 无数据回退 akshare 时，陈旧记录同样触发停更降级。"""
    from lib.collector import _orchestrate as orch

    old_records = [
        {"trade_date": "20240811", "net_mf_amount": 100.0},
        {"trade_date": "20240812", "net_mf_amount": 200.0},
        {"trade_date": "20240813", "net_mf_amount": 300.0},
        {"trade_date": "20240814", "net_mf_amount": 400.0},
        {"trade_date": "20240815", "net_mf_amount": 500.0},
        {"trade_date": "20240816", "net_mf_amount": 600.0},
    ]

    monkeypatch.setattr(orch, "_hsgt_top10_cached", lambda symbol: None)
    monkeypatch.setattr(orch, "_recent_flow_records",
                        lambda r, *, limit: sorted(r, key=lambda x: str(x.get("trade_date", "")))[-limit:])
    monkeypatch.setattr(orch, "_q_akshare_northbound", lambda symbol: old_records)

    out = orch._ms_fetch_northbound_stock(None, "600176")
    assert out is not None
    assert out["net_sum_10d"] is None
    assert out["stale"] is True
    assert out["source"] == "akshare.stock_hsgt_individual_em"
    assert "20240816" in out["staleness_note"]


def test_northbound_stale_guard_handles_dashed_dates(monkeypatch):
    """P0-1 修正（code-review 第四轮 DOA 回归）：akshare 持股日期为 'YYYY-MM-DD'
    横线格式（live 复现 600176 最新 '2024-08-16'），守卫必须归一化后解析——
    原实现 strptime('%Y%m%d') 恒 ValueError 静默跳过 → 陈旧净额继续以
    「近 10 日」呈现，P0-1 等于没修。"""
    from lib.collector import _orchestrate as orch

    dashed_records = [
        {"trade_date": "2024-08-11", "net_mf_amount": 100.0},
        {"trade_date": "2024-08-12", "net_mf_amount": 200.0},
        {"trade_date": "2024-08-13", "net_mf_amount": 300.0},
        {"trade_date": "2024-08-14", "net_mf_amount": 400.0},
        {"trade_date": "2024-08-15", "net_mf_amount": 500.0},
        {"trade_date": "2024-08-16", "net_mf_amount": 600.0},
    ]

    monkeypatch.setattr(orch, "_hsgt_top10_cached", lambda symbol: None)
    monkeypatch.setattr(orch, "_recent_flow_records",
                        lambda r, *, limit: sorted(r, key=lambda x: str(x.get("trade_date", "")))[-limit:])
    monkeypatch.setattr(orch, "_q_akshare_northbound", lambda symbol: dashed_records)

    out = orch._ms_fetch_northbound_stock(None, "600176")
    assert out is not None
    assert out["net_sum_10d"] is None
    assert out["stale"] is True
    assert out["latest_trade_date"] == "2024-08-16"
    assert "停更" in out["staleness_note"]


def test_northbound_stale_guard_dashed_fresh_dates_keep_value(monkeypatch):
    """P0-1 修正反例：横线格式但近期（≤90 天）→ 净额照常可用（归一化不误伤）。"""
    from lib.collector import _orchestrate as orch
    from datetime import datetime, timedelta, timezone

    d_fmt = lambda delta: (datetime.now(timezone.utc) - timedelta(days=delta)).strftime("%Y-%m-%d")
    records = [
        {"trade_date": d_fmt(30), "net_mf_amount": 100.0},
        {"trade_date": d_fmt(20), "net_mf_amount": 200.0},
        {"trade_date": d_fmt(10), "net_mf_amount": 300.0},
        {"trade_date": d_fmt(5), "net_mf_amount": 400.0},
        {"trade_date": d_fmt(2), "net_mf_amount": 500.0},
        {"trade_date": d_fmt(0), "net_mf_amount": 600.0},
    ]

    monkeypatch.setattr(orch, "_hsgt_top10_cached", lambda symbol: None)
    monkeypatch.setattr(orch, "_recent_flow_records",
                        lambda r, *, limit: sorted(r, key=lambda x: str(x.get("trade_date", "")))[-limit:])
    monkeypatch.setattr(orch, "_q_akshare_northbound", lambda symbol: records)

    out = orch._ms_fetch_northbound_stock(None, "600176")
    assert out is not None
    assert out["net_sum_10d"] == 2100.0
    assert out.get("stale") is None
