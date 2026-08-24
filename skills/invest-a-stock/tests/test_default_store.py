"""v0.2.4：collect/report 默认自动入库（--store 默认开启 + --no-store 逃生口）。

覆盖：parser 默认值；collect 默认入库 / --no-store / 全失败 / resume 不重复；
report render→save 顺序（改动 1/2 耦合保护）；report resume/no-store/全失败；
report diff 对上次快照；--with-macro 宏观快照入库。
"""

from __future__ import annotations

import sys
from argparse import Namespace
from datetime import datetime, timedelta, timezone
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

    def test_collect_flags_after_subcommand(self):
        """code-review：--plan/--resume/--save-raw 子命令后置可用（原仅主 parser
        注册，`collect 600176 --plan x` 曾 argparse exit(2)）。"""
        import invest

        parser = invest.build_parser()
        args = parser.parse_args(
            ["collect", "600176", "--plan", "/tmp/x.json", "--resume", "--save-raw"])
        assert args.plan == "/tmp/x.json"
        assert args.resume is True
        assert args.save_raw is True
        # 未给出时主 parser 默认值不被子 parser SUPPRESS 覆盖
        plain = parser.parse_args(["collect", "600176"])
        assert plain.plan == ""
        assert plain.resume is False
        assert plain.save_raw is False

    def test_collect_flags_on_other_subcommands(self):
        """evidence/analyze/synthesize 经 _add_collect_flags 获得后置旗标。"""
        import invest

        parser = invest.build_parser()
        for cmd, flag, value in (
            ("evidence", "--plan", "/tmp/e.json"),
            ("analyze", "--resume", None),
            ("synthesize", "--save-raw", None),
        ):
            argv = [cmd, "600176", flag] + ([value] if value else [])
            args = parser.parse_args(argv)
            if value:
                assert args.plan == value
            else:
                assert getattr(args, flag.lstrip("-").replace("-", "_")) is True

    @pytest.mark.parametrize("cmd,argv", [
        ("compare", ["compare", "600176", "000858", "--force-sector-sync"]),
        ("watchlist", ["watchlist", "600176,000858", "--force-sector-sync"]),
        ("rigor", ["rigor", "600176", "--force-sector-sync"]),
        ("check", ["check", "600176", "--force-sector-sync"]),
        ("risk-reward", ["risk-reward", "600176", "--force-sector-sync"]),
        ("ic", ["ic", "600176", "--force-sector-sync"]),
    ])
    def test_force_sector_sync_flag_accepted(self, cmd, argv):
        """会触发 collect_all 的子命令均可注册 --force-sector-sync（冷缓存
        预热入口；此前 argparse 直接报错，用户无法从这些命令预热）。"""
        import invest

        parser = invest.build_parser()
        args = parser.parse_args(argv)
        assert args.force_sector_sync is True


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

    def test_report_md_renders_with_attach_extras(self, isolated_store, monkeypatch):
        """#1 回归：cmd_report 默认 md 路径显式传 attach_extras=True（补挂 market_structure）。

        98813b5 把 render 默认值翻转为 False 后，默认 md 报告与落库快照静默缺失
        模块 5 市场结构；此用例锁定 cmd_report 必须显式开启。
        """
        import invest

        captured: dict = {}
        monkeypatch.setattr(invest, "_HAS_STORE", True)
        monkeypatch.setattr(invest, "store_mod", isolated_store)
        monkeypatch.setattr(invest.collector, "collect_all", lambda *a, **k: _fake_result())

        def _spy_render(*a, **k):
            captured["kwargs"] = k
            return "ok"

        monkeypatch.setattr(invest.render, "render", _spy_render)

        assert invest.cmd_report(_report_args()) == 0
        assert captured["kwargs"].get("attach_extras") is True

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
        """报告模块 1 的 diff 跳过 10 分钟窗口内的同会话行，比较上次会话。

        v0.2.7 P2-1：原测试用相同 fetched_at 模拟同会话，但微秒精度下
        同会话两次采集恒不相等——改为相对时间的 31 秒前行（真实场景）。
        """
        now = datetime.now(timezone.utc)
        s1 = _phase4_collection("600176", (now - timedelta(days=7)).isoformat())
        same_session = _phase4_collection(
            "600176", (now - timedelta(seconds=31)).isoformat())
        current = _phase4_collection(
            "600176", (now - timedelta(seconds=10)).isoformat(), latest_roe=22.0)

        isolated_store.save_collection(same_session)  # 同会话行（31 秒前）
        isolated_store.save_collection(s1)

        diff = isolated_store.load_key_diff_vs_stored("600176", current)
        assert diff is not None
        # 与上次会话（7 天前）比较，而非 31 秒前的同会话行
        assert diff.get("old_at", "").startswith(
            (now - timedelta(days=7)).strftime("%Y-%m-%d"))

    def test_load_key_diff_all_rows_in_window_returns_none(self, isolated_store):
        """窗口内无更早行 → None（不显示「相对上次调研变化」块）。"""
        now = datetime.now(timezone.utc)
        a = _phase4_collection("600176", (now - timedelta(minutes=6)).isoformat())
        b = _phase4_collection("600176", (now - timedelta(minutes=3)).isoformat())
        current = _phase4_collection("600176", now.isoformat(), latest_roe=22.0)
        isolated_store.save_collection(a)
        isolated_store.save_collection(b)
        assert isolated_store.load_key_diff_vs_stored("600176", current) is None

    def test_load_key_diff_window_boundary_excluded(self, isolated_store):
        """窗口外 1 分钟的行（11 分钟前）→ 被配对为上次调研。"""
        now = datetime.now(timezone.utc)
        old = _phase4_collection("600176", (now - timedelta(minutes=11)).isoformat())
        current = _phase4_collection("600176", now.isoformat(), latest_roe=22.0)
        isolated_store.save_collection(old)
        diff = isolated_store.load_key_diff_vs_stored("600176", current)
        assert diff is not None
        assert diff.get("old_at", "").startswith(
            (now - timedelta(minutes=11)).strftime("%Y-%m-%d"))

    def test_load_key_diff_skips_resume_self_row(self, isolated_store):
        """第五轮回归：--resume 恢复行 = 最新 stored 行（fetched_at 与 current
        全等）时,窗口守卫拦不住（陈旧到窗口外）→ 显式等值跳过须先行,
        否则恒自比较成幻影「无显著变化」。"""
        now = datetime.now(timezone.utc)
        last = _phase4_collection("600176", (now - timedelta(days=9)).isoformat())
        stored = _phase4_collection("600176", (now - timedelta(days=2)).isoformat())
        current = _phase4_collection(
            "600176", (now - timedelta(days=2)).isoformat(), latest_roe=22.0)
        isolated_store.save_collection(last)
        isolated_store.save_collection(stored)
        diff = isolated_store.load_key_diff_vs_stored("600176", current)
        assert diff is not None
        # 与上次会话（9 天前）比较,而非与 2 天前的自身行自比较
        assert diff.get("old_at", "").startswith(
            (now - timedelta(days=9)).strftime("%Y-%m-%d"))

    def test_get_latest_two_no_fallback_mix_same_session(self, isolated_store):
        """新用户同会话 collect --store + report：仅 1 条 collect 时不
        回退混入 report 行配对——否则 diff 比较几分钟内两快照恒显
        「几乎无变化」，掩盖真实跨会话变化（review #9 移除过的自我
        比较问题在回退路径复现，code-review 第三轮）。"""
        collect = _fake_result(symbol="600176")
        collect["fetched_at"] = "2026-08-07T09:00:00+00:00"
        rpt = _fake_result(symbol="600176")  # 同会话 report（几分钟后）
        rpt["fetched_at"] = "2026-08-07T09:05:00+00:00"

        isolated_store.save_collection(collect)
        isolated_store.save_collection(rpt, kind="report")

        assert isolated_store.get_latest_two("600176") is None

    def test_get_latest_two_fallback_pure_report_user(self, isolated_store):
        """纯 report 用户（无 collect 行）仍回退全部 kind 配对（兼容不破）。"""
        r1 = _fake_result(symbol="600176")
        r1["fetched_at"] = "2026-08-01T00:00:00+00:00"
        r2 = _fake_result(symbol="600176")
        r2["fetched_at"] = "2026-08-07T09:00:00+00:00"

        isolated_store.save_collection(r1, kind="report")
        isolated_store.save_collection(r2, kind="report")

        pair = isolated_store.get_latest_two("600176")
        assert pair is not None
        older, newer = pair
        assert older["fetched_at"] == "2026-08-01T00:00:00+00:00"
        assert newer["fetched_at"] == "2026-08-07T09:00:00+00:00"


class TestMacroRawJsonMerge:
    """code-review 第三轮：save_macro_snapshot 同日部分写入不丢溯源信封。

    raw_json 存 {value, source, signal} 完整信封；此前 upsert merge=True
    的 COALESCE 对 raw_json 恒取新值（dumps_json 对非空 dict 恒非 NULL），
    部分写入会整块覆盖旧信封（数值列因逐列 COALESCE 幸存）。
    """

    def test_partial_second_write_keeps_full_envelopes(self, isolated_store):
        import json

        ctx1 = {
            "pmi": {"value": 50.1, "source": "akshare", "signal": "扩张"},
            "vix": {"value": 15.0, "source": "fred", "signal": ""},
            "sox": {"value": 5600.0, "source": "yahoo", "signal": ""},
        }
        assert isolated_store.save_macro_snapshot(ctx1) is not None
        # 第二次仅 pmi 重取成功（macro.py 把 fetch 失败的键初始化为 None）
        assert isolated_store.save_macro_snapshot(
            {"pmi": {"value": 50.3, "source": "akshare", "signal": "扩张"},
             "vix": None, "sox": None}) is not None

        hist = isolated_store.load_macro_history(7)
        assert len(hist) == 1
        assert hist[0]["pmi"] == 50.3  # 新值覆盖
        assert hist[0]["vix"] == 15.0  # 数值列逐列 COALESCE 幸存
        assert hist[0]["sox"] == 5600.0
        raw = json.loads(hist[0]["raw_json"])  # 溯源信封同样逐键保留
        assert raw["pmi"] == {"value": 50.3, "source": "akshare", "signal": "扩张"}
        assert raw["vix"] == {"value": 15.0, "source": "fred", "signal": ""}
        assert raw["sox"] == {"value": 5600.0, "source": "yahoo", "signal": ""}
