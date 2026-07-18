"""v0.1.3 Phase 4 测试：跨时点 diff、watchlist、报告 UX。"""

from __future__ import annotations

import sys
from argparse import Namespace
from copy import deepcopy
from pathlib import Path

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from fixtures.collections import make_daily_basic_series
from test_v013_phase3 import _collection_phase3


def _phase4_collection(
    symbol: str = "600176",
    fetched_at: str = "2026-06-01T00:00:00Z",
    *,
    pe_offset: float = 0.0,
    latest_roe: float = 20.0,
    northbound_net: float = -600_000_000,
) -> dict:
    """Phase 4 快照 fixture：可调估值/财务/资金字段。"""
    c = deepcopy(_collection_phase3())
    c["symbol"] = symbol
    c["fetched_at"] = fetched_at

    ms = dict(c.get("market_structure") or {})
    ms["northbound"] = {
        **(ms.get("northbound") or {}),
        "net_sum_10d": northbound_net,
        "days": 10,
        "source": "tushare.hsgt_top10",
    }
    c["market_structure"] = ms

    val_rows = make_daily_basic_series(60)
    if pe_offset:
        for row in val_rows:
            row["pe_ttm"] = round(float(row["pe_ttm"]) + pe_offset, 2)

    for dim in c["dimensions"]:
        if dim["dimension"] == "valuation":
            dim["data"] = val_rows
        if dim["dimension"] == "financials":
            rows = dim["data"]
            if isinstance(rows, list) and rows:
                latest = max(rows, key=lambda r: str(r.get("end_date", "")))
                latest["roe"] = latest_roe
    return c


class TestPhase4Diff:
    def test_diff_key_fields(self, isolated_store):
        from lib.store import diff_key_snapshots

        old = _phase4_collection(
            "600176", "2026-06-01T00:00:00Z",
            pe_offset=0.0, latest_roe=18.0, northbound_net=-600_000_000,
        )
        new = _phase4_collection(
            "600176", "2026-06-08T00:00:00Z",
            pe_offset=8.0, latest_roe=22.5, northbound_net=250_000_000,
        )
        isolated_store.save_collection(old)
        isolated_store.save_collection(new)

        pair = isolated_store.get_latest_two("600176")
        assert pair is not None
        older, newer = pair
        result = diff_key_snapshots(older, newer)
        cats = result.get("categories") or {}

        assert "valuation" in cats
        assert "financials" in cats
        assert "capital_flow" in cats
        val_fields = {item["field"] for item in cats["valuation"]}
        fin_fields = {item["field"] for item in cats["financials"]}
        cap_fields = {item["field"] for item in cats["capital_flow"]}
        assert val_fields & {"pe_pct", "pe_ttm", "pb_pct", "pb"}
        assert "roe" in fin_fields
        assert "northbound_net" in cap_fields

    def test_extract_key_snapshot_db_row_parity(self, isolated_store):
        from lib.store import extract_key_snapshot

        c = _phase4_collection("600176", "2026-06-01T00:00:00Z", latest_roe=19.5)
        cid = isolated_store.save_collection(c)
        row = isolated_store.get_collection(cid)
        assert row is not None

        bare = extract_key_snapshot(c)
        wrapped = extract_key_snapshot(row)
        assert bare["symbol"] == wrapped["symbol"] == "600176"
        assert bare["valuation"].keys() == wrapped["valuation"].keys()
        assert bare["financials"].get("roe") == wrapped["financials"].get("roe") == 19.5

    def test_key_diff_threshold_filters_small_changes(self):
        from lib.store import diff_key_snapshots

        old = _phase4_collection("600176", "2026-06-01T00:00:00Z", latest_roe=20.0)
        new = _phase4_collection("600176", "2026-06-08T00:00:00Z", latest_roe=20.05)
        result = diff_key_snapshots(old, new)
        fin = result.get("categories", {}).get("financials", [])
        assert not any(item["field"] == "roe" for item in fin)

    def test_key_diff_always_fields_show_small_changes(self):
        from lib.store import _key_field_changed

        assert _key_field_changed("pe_pct", 50.0, 50.1)
        assert _key_field_changed("triggered_count", 2, 3)
        assert not _key_field_changed("roe", 20.0, 20.05)

    def test_diff_emit_md(self, isolated_store, capsys):
        import invest

        isolated_store.save_collection(
            _phase4_collection("600176", "2026-06-01T00:00:00Z", latest_roe=18.0),
        )
        isolated_store.save_collection(
            _phase4_collection("600176", "2026-06-08T00:00:00Z", latest_roe=22.0),
        )
        args = Namespace(symbol="600176", from_id=None, to_id=None, emit="md")
        assert invest.cmd_diff(args) == 0
        out = capsys.readouterr().out
        assert "## 关键字段变化" in out
        assert "### 估值" in out or "### 财务" in out


class TestPhase4Watchlist:
    def test_watchlist_multi_symbol(self, isolated_store, capsys):
        import invest

        for sym in ("000001", "600519"):
            isolated_store.save_collection(
                _phase4_collection(sym, "2026-06-08T00:00:00Z"),
            )

        args = Namespace(symbols="000001,600519", outdir=None)
        rc = invest.cmd_watchlist(args)
        assert rc == 0

        out = capsys.readouterr().out
        assert "观察列表摘要" in out
        assert "共 2 只标的" in out
        assert "## 000001" in out
        assert "## 600519" in out
        assert "最新价" in out or "PE 历史分位" in out

    def test_watchlist_live_collect_warning_without_store(self, capsys, monkeypatch):
        import invest

        monkeypatch.setattr(invest, "_HAS_STORE", False)
        monkeypatch.setattr(
            invest,
            "_watchlist_symbol_section",
            lambda sym: [f"## {sym}", ""],
        )
        args = Namespace(symbols="000001,600519", outdir=None)
        invest.cmd_watchlist(args)
        out = capsys.readouterr().out
        assert "现场采集" in out
        assert "--store" in out


class TestPhase4ReportUx:
    def test_report_has_toc(self):
        from lib.render import render_report_v3

        text = render_report_v3(_collection_phase3(), "600176")
        head = text[:2500]
        assert "## 目录" in head
        # LAW 17: TOC 现为纯文本列表（标题动态不可预测锚点）
        assert "0. 核心问题" in text
        assert "1. 当前状态" in text
        assert "PE Band（5年轨道）" in text
        assert "引用来源" in text

    def test_snapshot_module1_shows_diff_vs_stored(self, isolated_store):
        from lib.render import render_report_v3

        isolated_store.save_collection(
            _phase4_collection("600176", "2026-06-01T00:00:00Z", latest_roe=18.0),
        )
        current = _phase4_collection("600176", "2026-06-08T00:00:00Z", latest_roe=22.0)
        text = render_report_v3(current, "600176")
        mod1 = text.split("## 1.")[1].split("## 2.")[0]
        assert "相对上次调研变化" in mod1
        assert "roe" in mod1.lower() or "ROE" in mod1 or "财务" in mod1

    def test_details_collapsible(self):
        from lib.render import render_report_v3

        text = render_report_v3(_collection_phase3(), "600176")
        assert text.count("<details>") >= 2
        assert "<summary>" in text
        assert "展开：静态基本面（12题）" in text
        assert "展开：风险与不确定性" in text

    def test_pe_band_text_table(self):
        from lib.render import _index_dims, _pe_band_markdown_table, render_report_v3

        c = _collection_phase3()
        val_cache: dict = {}
        table = _pe_band_markdown_table(_index_dims(c), val_cache)
        assert table
        assert "PE Band" in table
        assert "5年轨道" in table
        assert "+1σ" in table
        assert "-1σ" in table
        assert "+2σ" in table
        assert "-2σ" in table
        assert "pe_band" in val_cache

        report = render_report_v3(c, "600176")
        mod8 = report.split("## 8.")[1].split("## 📚")[0]
        assert "PE Band（5年轨道）" in mod8
        assert "+1σ" in mod8
        refs = report.split("## 📚")[1]
        assert "PE Band（5年轨道）" not in refs
