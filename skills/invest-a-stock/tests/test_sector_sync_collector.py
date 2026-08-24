"""E1 板块同步性引擎 — collector 集成测试（无网络）。

覆盖：_attach_sector_sync 的 derived 合并 / industry_hint 传递 / 无 akshare
降级 / collect_all 异常 fail loud / 模块加载。mock 打在定义模块命名空间
（D13）：`collector._orchestrate._load_sector_sync_module` / `_attach_sector_sync`。
"""

from __future__ import annotations

import types
from types import SimpleNamespace
from unittest.mock import patch

import pytest


_FAKE_FIELDS = ("sector_beta_60d", "sector_r2_60d", "idio_var_share",
                "sector_dispersion", "csad_gamma2", "downside_corr_gap")


def _fake_sector_sync_module(**overrides) -> SimpleNamespace:
    def _compute(symbol, *, industry_hint="", stock_kline=None, cache=None,
                 max_workers=8, anchor_override=None):
        return {
            "symbol": symbol,
            "available": True,
            "provider": "sw",
            "industry": industry_hint or "玻璃玻纤",
            "fields": {
                "sector_beta_60d": 1.23,
                "sector_r2_60d": 0.81,
                "idio_var_share": 0.19,
                "sector_dispersion": 2.35,
                "csad_gamma2": -1.42,
                "downside_corr_gap": 0.08,
            },
            "meta": {},
            "reasons": {},
        }

    def _probe(industry_hint="", *, cache=None):
        # F1 门控默认放行（测试聚焦合并/传递；门控行为由专门用例覆盖）
        return {"warm": True, "miss": 0, "total": 0, "valid": 0, "reason": None,
                "anchor": {"provider": "sw", "index_name": "玻璃玻纤",
                           "index_code": "801060"}}

    kw = {"SECTOR_SYNC_FIELDS": _FAKE_FIELDS, "compute_sector_sync": _compute,
          "probe_sector_cache_warmth": _probe}
    kw.update(overrides)
    return SimpleNamespace(**kw)


class TestAttachSectorSync:
    def test_merges_fields_into_kline_derived(self, monkeypatch):
        """6 字段并入 kline 维度 derived（ETF 侧输出形态）+ collection['sector_sync']。"""
        from lib.collector import _orchestrate as orch

        monkeypatch.setattr(orch.env, "is_akshare_available", lambda: True)
        monkeypatch.setattr(orch, "_load_sector_sync_module", _fake_sector_sync_module)
        collection: dict = {}
        dim_results = {
            "basic_info": {"data": {"行业": "玻璃玻纤", "name": "中国巨石"}},
            "kline": {"dimension": "kline", "data": [{"trade_date": "20260101", "close": 1.0}]},
        }
        orch._attach_sector_sync(collection, "600176", dim_results)

        ss_out = collection["sector_sync"]
        assert ss_out["available"] is True
        assert ss_out["industry"] == "玻璃玻纤"
        derived = dim_results["kline"]["derived"]
        for f in _FAKE_FIELDS:
            assert derived[f] == ss_out["fields"][f]

    def test_passes_industry_hint_from_basic_info(self, monkeypatch):
        """basic_info 的「行业」字段 → industry_hint（东财中文列优先）。"""
        from lib.collector import _orchestrate as orch

        monkeypatch.setattr(orch.env, "is_akshare_available", lambda: True)
        captured: dict = {}

        def _fake(symbol, *, industry_hint="", stock_kline=None, cache=None,
                  max_workers=8, anchor_override=None):
            captured["industry_hint"] = industry_hint
            captured["stock_kline"] = stock_kline
            captured["anchor_override"] = anchor_override
            return _fake_sector_sync_module().compute_sector_sync(
                symbol, industry_hint=industry_hint, stock_kline=stock_kline)

        monkeypatch.setattr(orch, "_load_sector_sync_module",
                            lambda: _fake_sector_sync_module(compute_sector_sync=_fake))
        bars = [{"trade_date": "20260101", "close": 1.0}]
        orch._attach_sector_sync({}, "600176", {
            "basic_info": {"data": {"行业": "玻璃玻纤"}},
            "kline": {"data": bars},
        })
        assert captured["industry_hint"] == "玻璃玻纤"
        assert captured["stock_kline"] == bars  # 复用已采集 kline，不二次抓取

    def test_akshare_unavailable_fails_loud(self, monkeypatch):
        """无 akshare 环境：不可得骨架（fail loud），13 键统一 schema。"""
        from lib.collector import _orchestrate as orch

        monkeypatch.setattr(orch.env, "is_akshare_available", lambda: False)
        collection: dict = {}
        orch._attach_sector_sync(collection, "600176", {"basic_info": {}, "kline": {}})
        ss_out = collection["sector_sync"]
        assert ss_out["available"] is False
        assert "akshare 数据源不可用" in ss_out["reasons"]["_all"]
        assert all(v is None for v in ss_out["fields"].values())  # 无默认值
        # 与冷缓存路径/正常路径同形（统一 schema，消费者不 KeyError）
        for key in ("symbol", "provider", "industry", "index_code",
                    "n_constituents", "n_constituents_with_kline",
                    "window_days", "window_start", "window_end", "meta"):
            assert key in ss_out, f"akshare 不可用骨架缺键 {key}"

    def test_module_load_failure_full_skeleton(self, monkeypatch):
        """sector_sync 模块加载失败：13 键骨架 + error（fail loud）。"""
        from lib.collector import _orchestrate as orch

        monkeypatch.setattr(orch.env, "is_akshare_available", lambda: True)

        def _boom(*a, **k):
            raise ImportError("no skills/lib")

        monkeypatch.setattr(orch, "_load_sector_sync_module", _boom)
        collection: dict = {}
        orch._attach_sector_sync(collection, "600176", {"basic_info": {}, "kline": {}})
        ss_out = collection["sector_sync"]
        assert ss_out["available"] is False
        assert "模块加载失败" in ss_out["reasons"]["_all"]
        assert ss_out["error"]
        for key in ("symbol", "provider", "industry", "index_code",
                    "n_constituents", "n_constituents_with_kline",
                    "window_days", "window_start", "window_end", "fields", "meta"):
            assert key in ss_out, f"模块加载失败骨架缺键 {key}"

    def test_kline_missing_no_crash(self, monkeypatch):
        """kline 维度缺失：compute 收到 stock_kline=None，sector_sync 正常挂载。"""
        from lib.collector import _orchestrate as orch

        monkeypatch.setattr(orch.env, "is_akshare_available", lambda: True)
        seen: dict = {}

        def _fake(symbol, *, industry_hint="", stock_kline=None, cache=None,
                  max_workers=8, anchor_override=None):
            seen["kline"] = stock_kline
            return _fake_sector_sync_module().compute_sector_sync(symbol)

        monkeypatch.setattr(orch, "_load_sector_sync_module",
                            lambda: _fake_sector_sync_module(compute_sector_sync=_fake))
        orch._attach_sector_sync({}, "600176", {"basic_info": {"data": {"行业": "玻璃玻纤"}}})
        assert seen["kline"] is None


class TestLoadSectorSyncModule:
    def test_loads_real_module(self):
        """显式路径加载真实 skills/lib/sector_sync.py（固定模块名 lib_sector_sync）。"""
        from lib.collector import _orchestrate as orch

        mod = orch._load_sector_sync_module()
        assert callable(mod.compute_sector_sync)
        # 内联副本与模块常量一致（字段名漂移在此 CI 可见）
        assert mod.SECTOR_SYNC_FIELDS == orch._SECTOR_SYNC_FIELD_NAMES
        # 幂等：重复加载返回同一实例（sys.modules 缓存）
        assert orch._load_sector_sync_module() is mod


class TestCollectAllSectorSyncGuard:
    def test_attach_error_fails_loud(self, monkeypatch):
        """collect_all 中 sector_sync 异常 → 不可得骨架 + 原因（fail loud）。"""
        from lib import collector

        def _fake_basic(_symbol: str) -> dict:
            return {"dimension": "basic_info", "data": {"行业": "玻璃玻纤"},
                    "status": "available"}

        def _boom(*a, **k):
            raise RuntimeError("sector_sync boom")

        with patch.object(collector._orchestrate, "COLLECTORS",
                          {"basic_info": ("基本信息", _fake_basic)}), \
                patch.object(collector._orchestrate, "_attach_sector_sync", _boom), \
                patch.object(collector._orchestrate, "attach_phase2_extras",
                             lambda r, s: None):
            import lib.events as events_mod
            monkeypatch.setattr(events_mod, "attach_events",
                                lambda r, s, days=30: None)
            monkeypatch.setattr(collector._orchestrate.env, "is_akshare_available",
                                lambda: True)
            result = collector.collect_all("600176", dims=["basic_info"])

        ss_out = result.get("sector_sync") or {}
        assert ss_out["available"] is False
        assert "boom" in ss_out["reasons"]["_all"]
        # 异常兜底同走 13 键统一骨架（含 fields/meta + error）
        for key in ("symbol", "provider", "industry", "index_code",
                    "n_constituents", "n_constituents_with_kline",
                    "window_days", "window_start", "window_end", "fields", "meta"):
            assert key in ss_out, f"异常兜底骨架缺键 {key}"
        assert ss_out["error"]


class TestF1ColdCacheGate:
    def test_cold_cache_skips_compute_and_force_bypasses(self, monkeypatch):
        """F1：冷缓存跳过 compute（fail loud 标注未预热）；force=True 强制计算。"""
        from lib.collector import _orchestrate as orch

        monkeypatch.setattr(orch.env, "is_akshare_available", lambda: True)
        called = {"compute": 0}
        mod = _fake_sector_sync_module()
        orig_compute = mod.compute_sector_sync
        mod.probe_sector_cache_warmth = lambda hint="", cache=None: {
            "warm": False, "miss": 185, "total": 185, "valid": 0,
            "reason": "板块成分股日线缓存未预热（0/185 只已缓存，需现场补抓 185 只 > 预算 20 只），默认采集跳过；首次请用 --force-sector-sync 强制预热（约 5-10 分钟）",
            "anchor": None,
        }

        def _compute(symbol, *, industry_hint="", stock_kline=None, cache=None,
                     max_workers=8, anchor_override=None):
            called["compute"] += 1
            return orig_compute(symbol, industry_hint=industry_hint,
                                stock_kline=stock_kline)

        mod.compute_sector_sync = _compute
        monkeypatch.setattr(orch, "_load_sector_sync_module", lambda: mod)
        collection: dict = {}
        dim_results = {
            "basic_info": {"data": {"行业": "玻璃玻纤"}},
            "kline": {"data": [{"trade_date": "20260101", "close": 1.0}]},
        }
        orch._attach_sector_sync(collection, "600176", dim_results)
        assert called["compute"] == 0
        ss_out = collection["sector_sync"]
        assert ss_out["available"] is False
        assert "未预热" in ss_out["reasons"]["_all"]
        assert all(v is None for v in ss_out["fields"].values())
        # kline derived 不得有 sector 字段（无计算即无合并）
        assert "derived" not in dim_results["kline"]

        # force 绕过门控：compute 被调用、available=True 且字段并入 derived
        orch._attach_sector_sync(collection, "600176", dim_results, force=True)
        assert called["compute"] == 1
        assert collection["sector_sync"]["available"] is True
        assert dim_results["kline"]["derived"]["sector_beta_60d"] == 1.23


class TestProbeAnchorContract:
    def test_probe_warm_anchor_none_skips_compute_fail_fast(self, monkeypatch):
        """probe 无锚定（全解析失败/行业分类缺失）→ 跳过 compute（fail-fast，
        不再给二次解析全量冷抓的机会），骨架 reason 取自 probe。"""
        from lib.collector import _orchestrate as orch

        monkeypatch.setattr(orch.env, "is_akshare_available", lambda: True)
        called = {"compute": 0}
        mod = _fake_sector_sync_module()
        mod.probe_sector_cache_warmth = lambda hint="", cache=None: {
            "warm": True, "miss": 0, "total": 0, "valid": 0,
            "reason": "板块指数不可得（行业「玻璃玻纤」未匹配到东财 BK 板块或申万 L1 行业，或板块列表源不可达）",
            "anchor": None,
        }

        def _compute(*a, **k):
            called["compute"] += 1
            return _orig_compute("600176")

        _orig_compute = mod.compute_sector_sync
        mod.compute_sector_sync = _compute
        monkeypatch.setattr(orch, "_load_sector_sync_module", lambda: mod)
        collection: dict = {}
        dim_results = {
            "basic_info": {"data": {"行业": "玻璃玻纤"}},
            "kline": {"data": [{"trade_date": "20260101", "close": 1.0}]},
        }
        orch._attach_sector_sync(collection, "600176", dim_results)
        assert called["compute"] == 0
        ss_out = collection["sector_sync"]
        assert ss_out["available"] is False
        assert "板块指数不可得" in ss_out["reasons"]["_all"]
        assert all(v is None for v in ss_out["fields"].values())

    def test_probe_anchor_passed_to_compute(self, monkeypatch):
        """probe 解析出的锚定板块经 anchor_override 传入 compute（不二次解析）。"""
        from lib.collector import _orchestrate as orch

        monkeypatch.setattr(orch.env, "is_akshare_available", lambda: True)
        captured: dict = {}
        anchor = {"provider": "sw", "index_name": "建筑材料", "index_code": "801060"}
        mod = _fake_sector_sync_module()
        mod.probe_sector_cache_warmth = lambda hint="", cache=None: {
            "warm": True, "miss": 2, "total": 185, "valid": 183, "reason": None,
            "anchor": anchor,
        }

        def _compute(symbol, *, industry_hint="", stock_kline=None, cache=None,
                     max_workers=8, anchor_override=None):
            captured["anchor_override"] = anchor_override
            return _orig_compute(symbol)

        _orig_compute = mod.compute_sector_sync
        mod.compute_sector_sync = _compute
        monkeypatch.setattr(orch, "_load_sector_sync_module", lambda: mod)
        orch._attach_sector_sync({}, "600176", {
            "basic_info": {"data": {"行业": "玻璃玻纤"}},
            "kline": {"data": [{"trade_date": "20260101", "close": 1.0}]},
        })
        assert captured["anchor_override"] == anchor


class TestF4PartialMergeGate:
    def test_partial_failure_does_not_merge_into_derived(self, monkeypatch):
        """F4：available=False（部分字段不可得）时 kline.derived 不并入——两视图一致。"""
        from lib.collector import _orchestrate as orch

        monkeypatch.setattr(orch.env, "is_akshare_available", lambda: True)
        mod = _fake_sector_sync_module()
        partial = mod.compute_sector_sync("600176")
        partial["available"] = False
        partial["reasons"] = {"sector_dispersion": "成分股不足（< 20）"}
        partial["fields"]["sector_dispersion"] = None  # 其余 5 字段仍有效

        def _compute(symbol, *, industry_hint="", stock_kline=None, cache=None,
                     max_workers=8, anchor_override=None):
            return partial

        mod.compute_sector_sync = _compute
        monkeypatch.setattr(orch, "_load_sector_sync_module", lambda: mod)
        collection: dict = {}
        dim_results = {
            "basic_info": {"data": {"行业": "玻璃玻纤"}},
            "kline": {"dimension": "kline",
                      "data": [{"trade_date": "20260101", "close": 1.0}]},
        }
        orch._attach_sector_sync(collection, "600176", dim_results)
        assert collection["sector_sync"]["available"] is False
        # 部分字段保留在 collection['sector_sync']，但不得进 kline.derived
        assert collection["sector_sync"]["fields"]["sector_beta_60d"] == 1.23
        assert "derived" not in dim_results["kline"]


class TestF5ModuleLoadCleanup:
    def test_exec_failure_cleans_sys_modules(self, monkeypatch):
        """F5：exec 失败后 sys.modules 不残留残破模块；下次加载重新 exec。"""
        from lib.collector import _orchestrate as orch
        import importlib.util as _ilu

        orch.sys.modules.pop(orch._SECTOR_SYNC_MODULE, None)
        real_spec_from = _ilu.spec_from_file_location
        exec_count = {"n": 0}

        class _BoomLoader:
            def create_module(self, spec):
                # importlib 要求：定义 exec_module 的 loader 必须能创建模块
                return types.ModuleType(spec.name)

            def exec_module(self, mod):
                exec_count["n"] += 1
                raise RuntimeError("boom exec")

        def _spec(*a, **k):
            spec = real_spec_from(*a, **k)
            spec.loader = _BoomLoader()
            return spec

        monkeypatch.setattr(_ilu, "spec_from_file_location", _spec)
        with pytest.raises(RuntimeError, match="boom exec"):
            orch._load_sector_sync_module()
        assert orch._SECTOR_SYNC_MODULE not in orch.sys.modules
        # 第二次加载：不再短路返回残破模块，而是重新走完整 exec（计数 +1）
        with pytest.raises(RuntimeError, match="boom exec"):
            orch._load_sector_sync_module()
        assert exec_count["n"] == 2
        assert orch._SECTOR_SYNC_MODULE not in orch.sys.modules
