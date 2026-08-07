"""v0.2.4：collect/report 默认自动入库（--store 默认开启 + --no-store 逃生口）。

覆盖：parser 默认值；collect 默认入库 / --no-store / 全失败 / resume 不重复；
report render→save 顺序（改动 1/2 耦合保护）；report resume/no-store/全失败；
report diff 对上次快照；--with-macro 宏观快照入库。
"""

from __future__ import annotations

import sys
from argparse import Namespace
from pathlib import Path

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from test_v013_phase4 import _phase4_collection  # noqa: E402


def _fake_result(symbol: str = "600176", *, ok: bool = True) -> dict:
    """最小 collection（cmd_collect/cmd_report 可渲染/入库）。"""
    return {
        "symbol": symbol,
        "fetched_at": "2026-08-07T00:00:00+00:00",
        "dimensions": [
            {"dimension": "basic_info", "data": {"name": "测试股"},
             "status": "available", "_meta": {}},
            {"dimension": "quote", "data": {"close": 10.0},
             "status": "available", "_meta": {}},
        ],
        "summary": {
            "available": 2 if ok else 0, "total": 2,
            "sources_responded": 2 if ok else 0,
        },
    }


def _collect_args(**overrides) -> Namespace:
    base = dict(symbol="600176", store=True, dims="basic_info,quote",
                with_macro=False, deep=False, plan="", save_raw=False,
                resume=False, with_news_pack=False)
    base.update(overrides)
    return Namespace(**base)


def _report_args(**overrides) -> Namespace:
    base = dict(symbol="600176", store=True, dims="basic_info,quote",
                with_macro=False, deep=False, plan="", save_raw=False,
                resume=False, emit="md", mode="brief", outdir="",
                strict_rigor=False, material_gap=False, with_news_pack=False)
    base.update(overrides)
    return Namespace(**base)


class TestParserDefaults:
    def test_parser_defaults_store_true(self):
        import invest

        parser = invest.build_parser()
        assert parser.parse_args(["collect", "600176"]).store is True
        assert parser.parse_args(["collect", "600176", "--no-store"]).store is False
        assert parser.parse_args(["report", "600176"]).store is True
        assert parser.parse_args(["report", "600176", "--no-store"]).store is False
        # v0.2.4 review #4：synthesize 无 --input 时委托 report → 默认落库 + --no-store 逃生口
        assert parser.parse_args(["synthesize", "600176"]).store is True
        assert parser.parse_args(["synthesize", "600176", "--no-store"]).store is False


class TestCollectDefaultStore:
    def test_collect_default_stores(self, isolated_store, monkeypatch):
        import invest

        monkeypatch.setattr(invest, "_HAS_STORE", True)
        monkeypatch.setattr(invest, "store_mod", isolated_store)
        monkeypatch.setattr(invest.collector, "collect_all", lambda *a, **k: _fake_result())
        monkeypatch.setattr(invest.render, "render", lambda *a, **k: "ok")

        assert invest.cmd_collect(_collect_args()) == 0
        assert len(isolated_store.list_collections(symbol="600176")) == 1

    def test_collect_no_store_skips(self, isolated_store, monkeypatch):
        import invest

        monkeypatch.setattr(invest, "_HAS_STORE", True)
        monkeypatch.setattr(invest, "store_mod", isolated_store)
        monkeypatch.setattr(invest.collector, "collect_all", lambda *a, **k: _fake_result())
        monkeypatch.setattr(invest.render, "render", lambda *a, **k: "ok")

        assert invest.cmd_collect(_collect_args(store=False)) == 0
        assert len(isolated_store.list_collections(symbol="600176")) == 0

    def test_collect_all_failed_no_store(self, isolated_store, monkeypatch):
        import invest

        monkeypatch.setattr(invest, "_HAS_STORE", True)
        monkeypatch.setattr(invest, "store_mod", isolated_store)
        monkeypatch.setattr(invest.collector, "collect_all",
                            lambda *a, **k: _fake_result(ok=False))
        monkeypatch.setattr(invest.render, "render", lambda *a, **k: "ok")

        assert invest.cmd_collect(_collect_args()) == 1
        assert len(isolated_store.list_collections(symbol="600176")) == 0

    def test_collect_resume_no_duplicate(self, isolated_store, monkeypatch):
        """回归 test_v015_fixes.py:800-833：--resume 恢复路径不重复入库。"""
        import invest

        payload = _fake_result()
        isolated_store.save_collection(payload)
        isolated_store.save_pipeline_step("600176", "collect", {
            "dims": ["basic_info", "quote"], "with_macro": False, "deep": False,
        })
        monkeypatch.setattr(invest, "_HAS_STORE", True)
        monkeypatch.setattr(invest, "store_mod", isolated_store)
        monkeypatch.setattr(invest, "_try_resume_collection", lambda _s: payload)
        monkeypatch.setattr(invest.render, "render", lambda *a, **k: "ok")

        assert invest.cmd_collect(_collect_args(resume=True)) == 0
        assert len(isolated_store.list_collections(symbol="600176")) == 1


class TestReportAutoStore:
    def test_report_default_stores_after_render(self, isolated_store, monkeypatch):
        """改动 1/2 顺序保护：render 必须先于 save（否则 diff 自比恒空）。"""
        import invest

        events: list[str] = []
        orig_save = isolated_store.save_collection
        monkeypatch.setattr(invest, "_HAS_STORE", True)
        monkeypatch.setattr(invest, "store_mod", isolated_store)
        monkeypatch.setattr(invest.collector, "collect_all", lambda *a, **k: _fake_result())

        def _spy_save(*a, **k):
            events.append("save")
            return orig_save(*a, **k)

        monkeypatch.setattr(isolated_store, "save_collection", _spy_save)

        def _spy_render(*a, **k):
            events.append("render")
            return "ok"

        monkeypatch.setattr(invest.render, "render", _spy_render)

        assert invest.cmd_report(_report_args()) == 0
        assert events == ["render", "save"]
        assert len(isolated_store.list_collections(symbol="600176")) == 1

    def test_report_resume_skips_store(self, isolated_store, monkeypatch):
        import invest

        payload = _fake_result()
        monkeypatch.setattr(invest, "_HAS_STORE", True)
        monkeypatch.setattr(invest, "store_mod", isolated_store)
        monkeypatch.setattr(invest, "_try_resume_collection", lambda _s: payload)
        monkeypatch.setattr(invest, "_resume_cache_compatible", lambda *a, **k: True)
        monkeypatch.setattr(invest.render, "render", lambda *a, **k: "ok")

        assert invest.cmd_report(_report_args(resume=True)) == 0
        assert len(isolated_store.list_collections(symbol="600176")) == 0

    def test_report_resume_rejected_still_stores(self, isolated_store, monkeypatch):
        """review #3：resume 被拒（快照不兼容）→ 重新采集的结果仍入库。"""
        import invest

        monkeypatch.setattr(invest, "_HAS_STORE", True)
        monkeypatch.setattr(invest, "store_mod", isolated_store)
        monkeypatch.setattr(invest, "_try_resume_collection", lambda _s: _fake_result())
        monkeypatch.setattr(invest, "_resume_cache_compatible", lambda *a, **k: False)
        monkeypatch.setattr(invest.collector, "collect_all", lambda *a, **k: _fake_result())
        monkeypatch.setattr(invest.render, "render", lambda *a, **k: "ok")

        assert invest.cmd_report(_report_args(resume=True)) == 0
        assert len(isolated_store.list_collections(symbol="600176")) == 1

    def test_report_no_store_skips_pipeline_step(self, isolated_store, monkeypatch):
        """review #3 附：--no-store 时 report 不标记 pipeline 步骤完成。"""
        import invest

        calls: list[str] = []
        monkeypatch.setattr(invest, "_HAS_STORE", True)
        monkeypatch.setattr(invest, "store_mod", isolated_store)
        monkeypatch.setattr(invest.collector, "collect_all", lambda *a, **k: _fake_result())
        monkeypatch.setattr(invest.render, "render", lambda *a, **k: "ok")

        orig_step = isolated_store.save_pipeline_step

        def _spy_step(*a, **k):
            calls.append(a[1])
            return orig_step(*a, **k)

        monkeypatch.setattr(isolated_store, "save_pipeline_step", _spy_step)

        assert invest.cmd_report(_report_args(store=False)) == 0
        assert "report" not in calls

    def test_report_no_store_skips(self, isolated_store, monkeypatch):
        import invest

        monkeypatch.setattr(invest, "_HAS_STORE", True)
        monkeypatch.setattr(invest, "store_mod", isolated_store)
        monkeypatch.setattr(invest.collector, "collect_all", lambda *a, **k: _fake_result())
        monkeypatch.setattr(invest.render, "render", lambda *a, **k: "ok")

        assert invest.cmd_report(_report_args(store=False)) == 0
        assert len(isolated_store.list_collections(symbol="600176")) == 0

    def test_report_all_failed_no_store(self, isolated_store, monkeypatch):
        import invest

        monkeypatch.setattr(invest, "_HAS_STORE", True)
        monkeypatch.setattr(invest, "store_mod", isolated_store)
        monkeypatch.setattr(invest.collector, "collect_all",
                            lambda *a, **k: _fake_result(ok=False))
        monkeypatch.setattr(invest.render, "render", lambda *a, **k: "ok")

        assert invest.cmd_report(_report_args()) == 1
        assert len(isolated_store.list_collections(symbol="600176")) == 0

    def test_report_diff_against_previous_snapshot(self, isolated_store, monkeypatch):
        """预存 S1 → report(S2)：渲染输出含 diff 块，且入库的是 S2（渲染先于入库）。"""
        import invest

        s1 = _phase4_collection("600176", "2026-08-01T00:00:00Z")
        s2 = _phase4_collection("600176", "2026-08-07T00:00:00Z", latest_roe=22.0)
        isolated_store.save_collection(s1)
        monkeypatch.setattr(invest, "_HAS_STORE", True)
        monkeypatch.setattr(invest, "store_mod", isolated_store)
        monkeypatch.setattr(invest.collector, "collect_all", lambda *a, **k: s2)

        rendered: dict = {}
        orig_render = invest.render.render

        def _render(collection, *a, **k):
            rendered["out"] = orig_render(collection, *a, **k)
            return rendered["out"]

        monkeypatch.setattr(invest.render, "render", _render)

        assert invest.cmd_report(_report_args()) == 0
        # 真渲染输出（改动 2：有历史快照即显示 diff 块）
        assert "相对上次调研变化" in rendered["out"]
        # 入库的最新快照为 S2（渲染读的是 S1，save 的是 S2）
        rows = isolated_store.list_collections(symbol="600176")
        assert len(rows) == 2
        latest = isolated_store.get_collection(rows[0]["id"])
        assert latest["fetched_at"] == s2["fetched_at"]

    def test_report_with_macro_persists_macro(self, isolated_store, monkeypatch):
        import invest

        result = _fake_result()
        result["macro_context"] = {
            "indicators": {
                "pmi": {"value": 50.1, "source": "akshare", "signal": ""},
                "vix": 15.0,
            }
        }
        monkeypatch.setattr(invest, "_HAS_STORE", True)
        monkeypatch.setattr(invest, "store_mod", isolated_store)
        monkeypatch.setattr(invest.collector, "collect_all", lambda *a, **k: result)
        monkeypatch.setattr(invest.render, "render", lambda *a, **k: "ok")

        assert invest.cmd_report(_report_args(with_macro=True)) == 0
        hist = isolated_store.load_macro_history(7)
        assert len(hist) == 1
        assert hist[0]["pmi"] == 50.1

    def test_collect_with_macro_persists_macro(self, isolated_store, monkeypatch):
        import invest

        result = _fake_result()
        result["macro_context"] = {"indicators": {"vix": {"value": 18.5, "source": "fred"}}}
        monkeypatch.setattr(invest, "_HAS_STORE", True)
        monkeypatch.setattr(invest, "store_mod", isolated_store)
        monkeypatch.setattr(invest.collector, "collect_all", lambda *a, **k: result)
        monkeypatch.setattr(invest.render, "render", lambda *a, **k: "ok")

        assert invest.cmd_collect(_collect_args(with_macro=True)) == 0
        hist = isolated_store.load_macro_history(7)
        assert len(hist) == 1
        assert hist[0]["vix"] == 18.5


class TestWatchlistAllFailedGuard:
    """review #5（第二轮）：watchlist 现场采集全维度失败 → 不落库。"""

    def test_all_failed_not_stored(self, isolated_store, monkeypatch):
        import invest

        monkeypatch.setattr(invest, "_HAS_STORE", True)
        monkeypatch.setattr(invest, "store_mod", isolated_store)
        monkeypatch.setattr(invest.collector, "collect_all",
                            lambda *a, **k: _fake_result(ok=False))

        result = invest._watchlist_get_result("600176")
        assert result["summary"]["sources_responded"] == 0
        assert len(isolated_store.list_collections(symbol="600176")) == 0

    def test_ok_result_stored(self, isolated_store, monkeypatch):
        import invest

        monkeypatch.setattr(invest, "_HAS_STORE", True)
        monkeypatch.setattr(invest, "store_mod", isolated_store)
        monkeypatch.setattr(invest.collector, "collect_all",
                            lambda *a, **k: _fake_result())

        invest._watchlist_get_result("600176")
        assert len(isolated_store.list_collections(symbol="600176")) == 1


class TestSnapshotKindDiff:
    """review #9（第二轮）：collections.kind 区分 collect/report，
    diff 自动配对优先 collect；报告 diff 跳过同会话（同 fetched_at）行。"""

    def test_get_latest_two_prefers_collect_kind(self, isolated_store):
        s1 = _fake_result(symbol="600176")
        s1["fetched_at"] = "2026-08-01T00:00:00+00:00"
        rpt = _fake_result(symbol="600176")  # 同会话 report（夹在两次 collect 之间）
        rpt["fetched_at"] = "2026-08-07T09:05:00+00:00"
        s2 = _fake_result(symbol="600176")
        s2["fetched_at"] = "2026-08-07T09:00:00+00:00"

        isolated_store.save_collection(s1)
        isolated_store.save_collection(rpt, kind="report")
        isolated_store.save_collection(s2)

        pair = isolated_store.get_latest_two("600176")
        assert pair is not None
        older, newer = pair
        assert older["fetched_at"] == "2026-08-01T00:00:00+00:00"
        assert newer["fetched_at"] == "2026-08-07T09:00:00+00:00"

    def test_report_rows_are_kind_marked(self, isolated_store, monkeypatch):
        """report 快照以 kind='report' 落库（render→save 顺序测试的扩展）。"""
        import invest

        monkeypatch.setattr(invest, "_HAS_STORE", True)
        monkeypatch.setattr(invest, "store_mod", isolated_store)
        monkeypatch.setattr(invest.collector, "collect_all", lambda *a, **k: _fake_result())
        monkeypatch.setattr(invest.render, "render", lambda *a, **k: "ok")

        assert invest.cmd_report(_report_args()) == 0
        rows = isolated_store.list_collections(symbol="600176")
        assert len(rows) == 1
        assert rows[0]["kind"] == "report"

    def test_load_key_diff_skips_same_session_row(self, isolated_store):
        """报告模块 1 的 diff 跳过同 fetched_at 的同会话行，比较上次会话。"""
        s1 = _phase4_collection("600176", "2026-08-01T00:00:00Z")
        same_session = _phase4_collection("600176", "2026-08-07T00:00:00Z")
        current = _phase4_collection(
            "600176", "2026-08-07T00:00:00Z", latest_roe=22.0)

        isolated_store.save_collection(same_session)  # 同会话行（同 fetched_at）
        isolated_store.save_collection(s1)

        diff = isolated_store.load_key_diff_vs_stored("600176", current)
        assert diff is not None
        assert diff.get("old_at", "").startswith("2026-08-01")  # 与上次会话比较
