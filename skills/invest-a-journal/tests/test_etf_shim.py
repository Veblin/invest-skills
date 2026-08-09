"""Shim smoke: journal etf_data re-exports invest-a-etf canonical module."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_LIB = Path(__file__).resolve().parent.parent / "scripts" / "lib"

from invest_path import invest_a_etf_lib_dir  # noqa: E402  # skills/lib via conftest


def _load_journal_shim():
    """Load journal shim by file path (avoid collision with canonical etf_data)."""
    for key in ("journal_etf_shim", "invest_a_etf_etf_data"):
        sys.modules.pop(key, None)
    spec = importlib.util.spec_from_file_location("journal_etf_shim", _LIB / "etf_data.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_shim_reexports_same_map_objects():
    shim = _load_journal_shim()
    canon = sys.modules["invest_a_etf_etf_data"]
    assert shim.ETF_HEDGE_MAP is canon.ETF_HEDGE_MAP
    assert shim.CSINDEX_MAP is canon.CSINDEX_MAP


def test_shim_reexports_maps_content():
    shim = _load_journal_shim()
    assert "510300" in shim.ETF_HEDGE_MAP
    assert shim.CSINDEX_MAP.get("563300") == "932000"


def test_shim_callables_are_canonical():
    shim = _load_journal_shim()
    canon = sys.modules["invest_a_etf_etf_data"]
    assert shim.query_etf_data is canon.query_etf_data
    assert shim.query_etf_quote is canon.query_etf_quote
    assert shim.query_etf_kline is canon.query_etf_kline
    assert shim.prefetch_etf_spot is canon.prefetch_etf_spot
    assert shim.rollup_etf_quality_status is canon.rollup_etf_quality_status


def test_shim_fully_forwards_exported_names():
    """shim 完整转发 canonical 导出（review 第三轮 #1 防护）。

    pytest 会话中 journal lib 可能遮蔽 canonical etf_data（conftest 顺序
    依赖）——任一 `from etf_data import X` 都必须可用，含测试辅助
    clear_etf_spot_cache 与 fetch_* 系列。此前删除 re-export 导致
    `pytest etf目录 journal目录`（etf 在前）collection 失败（实证复现）。
    """
    shim = _load_journal_shim()
    canon = sys.modules["invest_a_etf_etf_data"]
    for name in (
        "clear_etf_spot_cache",
        "fetch_etf_spot_rows",
        "fetch_etf_index_pe",
        "fetch_etf_nav",
        "fetch_etf_index_daily",
        "fetch_etf_adj_factor",
        "fetch_etf_share_history",
        "fetch_etf_industry_alloc",
        "fetch_etf_category_sina",
    ):
        assert getattr(shim, name) is getattr(canon, name), name
        assert name in shim.__all__, name




def test_invest_a_etf_lib_dir_resolves():
    lib = invest_a_etf_lib_dir()
    assert lib.name == "lib"
    assert lib.parent.name == "scripts"
    assert (lib / "etf_data.py").is_file()


def test_data_bridge_etf_attr_resolves_under_hostile_sys_path(monkeypatch, tmp_path):
    """v0.2.4 缺陷修复：_import_etf_attr 显式定位 invest-a-etf canonical
    （invest_path.load_invest_a_etf_module），不再裸 ``import etf_data``。

    模拟 sys.path 变化：invest-a-etf lib 缺席 / 后置 / 有同名 decoy
    etf_data.py 抢占首位 —— 旧实现分别 ImportError→None 或解析到 decoy，
    新实现三种布局下都解析到 canonical 同一实例。
    """
    import data_bridge

    scripts = Path(__file__).resolve().parent.parent.parent
    stock_scripts = scripts / "invest-a-stock" / "scripts"
    skills_lib = scripts / "lib"
    journal_lib = scripts / "invest-a-journal" / "scripts" / "lib"

    decoy = tmp_path / "etf_data.py"
    decoy.write_text(
        "def fetch_etf_spot_rows():\n    raise AssertionError('decoy resolved')\n",
        encoding="utf-8",
    )

    configs = [
        # 1. invest-a-etf lib 完全不在 sys.path（invest-a-stock 上下文）
        [str(stock_scripts), str(skills_lib)],
        # 2. journal 上下文但 etf lib 缺席（旧实现依赖 journal shim 被
        #    先导入才不返回 None；未导入时 ImportError → None）
        [str(journal_lib), str(skills_lib), str(stock_scripts)],
        # 3. 敌意首位：同名 decoy etf_data.py 抢先 —— 旧裸 import 解析到 decoy
        [str(decoy.parent), str(stock_scripts), str(skills_lib)],
    ]
    for cfg in configs:
        monkeypatch.setattr(sys, "path", list(cfg))
        monkeypatch.delitem(sys.modules, "invest_a_etf_etf_data", raising=False)
        fetch = data_bridge._import_etf_attr("fetch_etf_spot_rows")
        assert callable(fetch), f"resolve failed under sys.path={cfg}"
        canon = sys.modules["invest_a_etf_etf_data"]
        assert fetch is canon.fetch_etf_spot_rows, (
            f"resolved wrong module under sys.path={cfg}"
        )


def test_get_etf_spot_rows_resolves_end_to_end_under_hostile_path(monkeypatch):
    """get_etf_* getter 全链路在敌意 sys.path 下解析成功（dummy fetch，无网络）。

    旧实现：etf lib 不在 sys.path → _import_etf_attr 返回 None →
    getter 静默返回 None；新实现 resolve 后正常走缓存维度包装。
    """
    import data_bridge

    scripts = Path(__file__).resolve().parent.parent.parent
    monkeypatch.setattr(
        sys, "path",
        [str(scripts / "invest-a-stock" / "scripts"), str(scripts / "lib")],
    )
    monkeypatch.delitem(sys.modules, "invest_a_etf_etf_data", raising=False)

    fetch = data_bridge._import_etf_attr("fetch_etf_spot_rows")
    assert callable(fetch)
    # 解析到 canonical 后把 fetch 换成 dummy，避免 getter 触发真实网络
    canon = sys.modules["invest_a_etf_etf_data"]
    monkeypatch.setattr(canon, "fetch_etf_spot_rows", lambda: [])
    assert data_bridge.get_etf_spot_rows(force=True) == []
