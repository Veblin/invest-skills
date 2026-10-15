"""A03：公共市场服务 canonical loader（invest_path.load_journal_module）契约。

A04 引入 loader（当时 canonical 在 journal）；A03 owner 迁移后 canonical 在
共享层 skills/lib，本文件验证：

- 同进程已按顶层名导入同一 canonical 文件 → **复用同一实例**（monkeypatch
  接缝/模块状态不分叉）；
- 顶层名被占用但指向别的文件（decoy）→ 不吞 decoy，按文件路径加载 canonical；
- 敌意 sys.path（decoy 目录首位）→ 仍解析到 canonical 同一实例；
- data_bridge.get_microstructure 全链路在敌意 path 下仍取 canonical（不裸 import）；
- journal shim（scripts/lib/market_microstructure.py）与 loader 解析同一公共实例。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_SKILL_ROOT = Path(__file__).resolve().parent.parent
_JOURNAL_LIB = _SKILL_ROOT / "scripts" / "lib"
_SKILLS_LIB = _SKILL_ROOT.parent / "lib"
_CANONICAL = _SKILLS_LIB / "market_microstructure.py"
_REGISTERED = "shared_market_canonical_market_microstructure"

for _p in (str(_SKILLS_LIB), str(_JOURNAL_LIB)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import market_microstructure  # noqa: E402  (conftest 已注入 journal lib)
from invest_path import journal_lib_dir, load_journal_module  # noqa: E402


def test_journal_lib_dir_resolves():
    """journal_lib_dir 仍指向 journal lib（旧路径解析/兼容面保留）。"""
    lib = journal_lib_dir()
    assert lib.name == "lib"
    assert (lib / "market_microstructure.py").is_file()  # shim


def test_shim_and_loader_resolve_public_canonical():
    """journal 上下文顶层导入 = 公共 canonical = loader 实例（同一对象）。"""
    assert Path(market_microstructure.__file__).resolve() == _CANONICAL.resolve()
    assert load_journal_module() is market_microstructure


def test_loader_adopts_top_level_instance():
    """顶层已导入 canonical → loader 复用该实例（identity）。"""
    assert load_journal_module() is market_microstructure


def test_loader_by_path_when_top_level_is_decoy(monkeypatch, tmp_path):
    """顶层名被 decoy 占用（__file__ 不匹配）→ 按路径加载 canonical，不吞 decoy。"""
    import types

    decoy = types.ModuleType("market_microstructure")
    decoy.__file__ = str(tmp_path / "decoy" / "market_microstructure.py")
    decoy.snapshot = lambda: {"decoy": True}
    monkeypatch.setitem(sys.modules, "market_microstructure", decoy)
    monkeypatch.delitem(sys.modules, _REGISTERED, raising=False)
    try:
        mod = load_journal_module()
        assert mod is not decoy
        assert Path(mod.__file__).resolve() == _CANONICAL.resolve()
        assert callable(mod.snapshot)
        # decoy 不被覆盖（setdefault 语义：不劫持既有绑定）
        assert sys.modules["market_microstructure"] is decoy
    finally:
        sys.modules.pop(_REGISTERED, None)


def test_loader_resolves_under_hostile_sys_path(monkeypatch, tmp_path):
    """decoy 文件在 path 首位 → loader 仍解析公共 canonical。"""
    decoy_dir = tmp_path / "decoy_dir"
    decoy_dir.mkdir()
    (decoy_dir / "market_microstructure.py").write_text(
        "def snapshot():\n    raise AssertionError('decoy resolved')\n", encoding="utf-8")

    stock_scripts = _SKILL_ROOT.parent / "invest-a-stock" / "scripts"
    monkeypatch.setattr(sys, "path", [str(decoy_dir), str(_SKILLS_LIB), str(stock_scripts)])
    monkeypatch.delitem(sys.modules, "market_microstructure", raising=False)
    monkeypatch.delitem(sys.modules, _REGISTERED, raising=False)
    try:
        mod = load_journal_module()
        assert Path(mod.__file__).resolve() == _CANONICAL.resolve()
        assert callable(mod.snapshot)
        # 顶层名空缺 → 注册同一实例（后续顶层 import 不再产生第二实例）
        assert sys.modules["market_microstructure"] is mod
    finally:
        sys.modules.pop(_REGISTERED, None)
        sys.modules.pop("market_microstructure", None)
        sys.modules["market_microstructure"] = market_microstructure


def test_provider_does_not_require_journal_source(monkeypatch, tmp_path):
    """公共 provider 不依赖 journal 源码：journal lib 不在 sys.path 也可解析。"""
    stock_scripts = _SKILL_ROOT.parent / "invest-a-stock" / "scripts"
    monkeypatch.setattr(sys, "path", [str(tmp_path), str(_SKILLS_LIB), str(stock_scripts)])
    monkeypatch.delitem(sys.modules, "market_microstructure", raising=False)
    monkeypatch.delitem(sys.modules, _REGISTERED, raising=False)
    try:
        mod = load_journal_module()
        assert Path(mod.__file__).resolve() == _CANONICAL.resolve()
        assert callable(mod.snapshot) and callable(mod.zt_seesaw)
    finally:
        sys.modules.pop(_REGISTERED, None)
        sys.modules.pop("market_microstructure", None)
        sys.modules["market_microstructure"] = market_microstructure


def test_bridge_get_microstructure_end_to_end(monkeypatch, tmp_path):
    """data_bridge.get_microstructure 全链路：adopt 顶层实例 + patch 接缝生效。"""
    import data_bridge
    from cache import DataCache

    monkeypatch.setattr(data_bridge, "_cache", DataCache(cache_dir=tmp_path / "cache"))
    monkeypatch.setattr(market_microstructure, "snapshot",
                        lambda: {"date": "20260803", "ad_ratio": 1.2})
    out = data_bridge.get_microstructure()
    assert out["date"] == "20260803"
    assert out["ad_ratio"] == 1.2


def test_bridge_microstructure_degrades_without_canonical(monkeypatch):
    """canonical 不可用 → None + 不抛出（旧语义）。"""
    import data_bridge

    def boom():
        raise ImportError("shared canonical missing")

    monkeypatch.setattr(data_bridge, "load_journal_module", boom)
    assert data_bridge.get_microstructure() is None
