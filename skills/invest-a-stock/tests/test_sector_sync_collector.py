"""E1 板块同步性引擎 — collector 集成测试（无网络）。

覆盖：_attach_sector_sync 的 derived 合并 / industry_hint 传递 / 无 akshare
降级 / collect_all 异常 fail loud / 模块加载。mock 打在定义模块命名空间
（D13）：`collector._orchestrate._load_sector_sync_module` / `_attach_sector_sync`。
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest


_FAKE_FIELDS = ("sector_beta_60d", "sector_r2_60d", "idio_var_share",
                "sector_dispersion", "csad_gamma2", "downside_corr_gap")


def _fake_sector_sync_module(**overrides) -> SimpleNamespace:
    def _compute(symbol, *, industry_hint="", stock_kline=None, cache=None,
                 max_workers=8):
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
    return SimpleNamespace(SECTOR_SYNC_FIELDS=_FAKE_FIELDS,
                           compute_sector_sync=_compute, **overrides)


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
                  max_workers=8):
            captured["industry_hint"] = industry_hint
            captured["stock_kline"] = stock_kline
            return _fake_sector_sync_module().compute_sector_sync(
                symbol, industry_hint=industry_hint, stock_kline=stock_kline)

        monkeypatch.setattr(orch, "_load_sector_sync_module",
                            lambda: SimpleNamespace(SECTOR_SYNC_FIELDS=_FAKE_FIELDS,
                                                    compute_sector_sync=_fake))
        bars = [{"trade_date": "20260101", "close": 1.0}]
        orch._attach_sector_sync({}, "600176", {
            "basic_info": {"data": {"行业": "玻璃玻纤"}},
            "kline": {"data": bars},
        })
        assert captured["industry_hint"] == "玻璃玻纤"
        assert captured["stock_kline"] == bars  # 复用已采集 kline，不二次抓取

    def test_akshare_unavailable_fails_loud(self, monkeypatch):
        """无 akshare 环境：不可得骨架（fail loud），不加载模块、不走网络。"""
        from lib.collector import _orchestrate as orch

        monkeypatch.setattr(orch.env, "is_akshare_available", lambda: False)

        def _forbid(*a, **k):
            raise AssertionError("无 akshare 环境不应加载 sector_sync 模块")

        monkeypatch.setattr(orch, "_load_sector_sync_module", _forbid)
        collection: dict = {}
        orch._attach_sector_sync(collection, "600176", {"basic_info": {}, "kline": {}})
        ss_out = collection["sector_sync"]
        assert ss_out["available"] is False
        assert "akshare 数据源不可用" in ss_out["reasons"]["_all"]
        assert all(v is None for v in ss_out["fields"].values())  # 无默认值

    def test_kline_missing_no_crash(self, monkeypatch):
        """kline 维度缺失：compute 收到 stock_kline=None，sector_sync 正常挂载。"""
        from lib.collector import _orchestrate as orch

        monkeypatch.setattr(orch.env, "is_akshare_available", lambda: True)
        seen: dict = {}

        def _fake(symbol, *, industry_hint="", stock_kline=None, cache=None,
                  max_workers=8):
            seen["kline"] = stock_kline
            return _fake_sector_sync_module().compute_sector_sync(symbol)

        monkeypatch.setattr(orch, "_load_sector_sync_module",
                            lambda: SimpleNamespace(SECTOR_SYNC_FIELDS=_FAKE_FIELDS,
                                                    compute_sector_sync=_fake))
        orch._attach_sector_sync({}, "600176", {"basic_info": {"data": {"行业": "玻璃玻纤"}}})
        assert seen["kline"] is None


class TestLoadSectorSyncModule:
    def test_loads_real_module(self):
        """显式路径加载真实 skills/lib/sector_sync.py（固定模块名 lib_sector_sync）。"""
        from lib.collector import _orchestrate as orch

        mod = orch._load_sector_sync_module()
        assert callable(mod.compute_sector_sync)
        assert mod.SECTOR_SYNC_FIELDS == _FAKE_FIELDS
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
